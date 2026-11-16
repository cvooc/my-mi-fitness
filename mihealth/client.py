#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mi Health (小米运动健康) HLTH cloud client.

Reverse-engineered from the APK 3.61.0:
  * health SkyTree bundle module `he-1828` -> postEcoApi
      POST https://hlth.io.mi.com/app/v1/eco/api_proxy
      body = {eco_api: <api>, params: JSON.stringify(<params>)}
  * common SkyTree bundle module 1243 (axios wrapper) / 1247 (crypto) / 1250 (nonce)
      Xiaomi cloud v2 signature: RC4(SHA256(ssecurity+nonce)) + SHA1 signature
      cookie: serviceToken=..; cUserId=..; locale=zh_CN
  * sid used for the service token: "miothealth"
      (classes10.dex Lcom/xiaomi/fitness/account/SIDs;->MIOT_HEALTH)

This module only depends on `requests`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional

import requests

RC4_DROP_LENGTH = 1024

DEFAULT_UA = (
    "Android-15-3.61.0-Xiaomi-Mi 15"
)  # app builds: systemName-systemVersion-appVersion-brand-model


# --------------------------------------------------------------------------- #
# crypto primitives (port of work/lead/common_mod_1247.js)
# --------------------------------------------------------------------------- #
def _rc4_keystream_init(key: bytes) -> List[int]:
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 0xFF
        S[i], S[j] = S[j], S[i]
    return S


class RC4:
    def __init__(self, key: bytes):
        self.S = _rc4_keystream_init(key)
        self.i = 0
        self.j = 0
        self.crypt(bytes(RC4_DROP_LENGTH))  # JS drops 1024 bytes

    def _output(self) -> int:
        self.i = (self.i + 1) & 0xFF
        self.j = (self.j + self.S[self.i]) & 0xFF
        S = self.S
        S[self.i], S[self.j] = S[self.j], S[self.i]
        return S[(S[self.i] + S[self.j]) & 0xFF]

    def crypt(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        for idx, b in enumerate(data):
            out[idx] = b ^ self._output()
        return bytes(out)

    def encrypt_str(self, text: str) -> str:
        return base64.b64encode(self.crypt(text.encode("utf-8"))).decode("ascii")

    def decrypt_str(self, b64text: str) -> str:
        return self.crypt(base64.b64decode(b64text)).decode("utf-8")


def derive_key(ssecurity_b64: str, nonce_b64: str) -> bytes:
    """base64(SHA256(b64decode(ssecurity) || b64decode(nonce)))"""
    raw = base64.b64decode(ssecurity_b64) + base64.b64decode(nonce_b64)
    return base64.b64encode(hashlib.sha256(raw).digest())


def sign_params(method: str, url: str, params: Dict[str, Any], key_b64: str) -> str:
    kv = "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
    text = "&".join([method.upper(), url, kv, key_b64])
    return base64.b64encode(hashlib.sha1(text.encode("utf-8")).digest()).decode("ascii")


def encrypt_parameters(
    params: Dict[str, Any],
    url: str,
    method: str,
    nonce_b64: str,
    ssecurity_b64: str,
) -> Dict[str, str]:
    key = derive_key(ssecurity_b64, nonce_b64)
    key_s = key.decode("ascii")
    rc4 = RC4(base64.b64decode(key))

    h1 = sign_params(method, url, params, key_s)
    body = dict(params)
    body["rc4_hash__"] = h1

    enc: Dict[str, Any] = {}
    for k in sorted(body):
        v = body[k]
        enc[k] = rc4.encrypt_str(v) if isinstance(v, str) else v

    enc["signature"] = sign_params(method, url, enc, key_s)
    enc["_nonce"] = nonce_b64
    return enc  # type: ignore[return-value]


def decrypt_response(text: str, nonce_b64: str, ssecurity_b64: str) -> str:
    key = derive_key(ssecurity_b64, nonce_b64)
    return RC4(base64.b64decode(key)).decrypt_str(text)


def make_nonce(server_time: Optional[float] = None) -> str:
    """12-byte nonce: u32(rand) u32(rand) u32(floor(sec/60)), base64."""
    if server_time is None:
        server_time = time.time()
    r1 = secrets.randbits(32)
    r2 = secrets.randbits(32)
    minutes = int(server_time // 60) & 0xFFFFFFFF
    raw = (
        r1.to_bytes(4, "big")
        + r2.to_bytes(4, "big")
        + minutes.to_bytes(4, "big")
    )
    return base64.b64encode(raw).decode("ascii")


def qs_stringify(obj: Dict[str, Any]) -> str:
    """axios/qs default urlencoded serialization used by the RN bundle."""
    return urllib.parse.urlencode(obj, quote_via=urllib.parse.quote, safe="")


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
class MiHealthClient:
    def __init__(
        self,
        service_token: str,
        c_user_id: str,
        ssecurity: str,
        locale: str = "zh_CN",
        ua: str = DEFAULT_UA,
        base_url: str = "https://hlth.io.mi.com",
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 2.0,
        verbose: bool = False,
    ):
        self.service_token = str(service_token)
        self.c_user_id = str(c_user_id)
        self.ssecurity = ssecurity
        self.locale = locale
        self.ua = ua
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.backoff = max(1.0, float(backoff))
        self.verbose = verbose
        self.session = requests.Session()
        self.time_diff = 0.0  # server_time - local_time (ms), learned from responses

    # -- helpers ----------------------------------------------------------- #
    def _log(self, *args: Any) -> None:
        if self.verbose:
            print("[mihealth]", *args, file=sys.stderr)

    def server_time(self) -> float:
        return time.time() + self.time_diff / 1000.0

    def _headers(self) -> Dict[str, str]:
        cookie = "serviceToken=%s; cUserId=%s; locale=%s" % (
            self.service_token,
            self.c_user_id,
            self.locale,
        )
        return {
            "User-Agent": self.ua,
            "Cookie": cookie,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    def _update_time_diff(self, resp: requests.Response) -> None:
        date = resp.headers.get("Date")
        if not date:
            return
        try:
            from email.utils import parsedate_to_datetime

            server_ms = parsedate_to_datetime(date).timestamp() * 1000.0
            self.time_diff = server_ms - time.time() * 1000.0
        except Exception:
            pass

    # -- low level --------------------------------------------------------- #
    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Send one request, retrying transient network failures.

        A single 15s timeout used to be enough to kill a whole GitHub Actions run
        when the TLS handshake to hlth.io.mi.com stalled, so timeouts / resets /
        TLS errors are retried with exponential backoff before giving up.
        """
        attempts = self.retries
        delay = 1.0
        last: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                last = exc
                if attempt >= attempts:
                    break
                self._log(
                    "%s %s failed (%d/%d): %s: %s; retry in %.1fs"
                    % (method, url, attempt, attempts, type(exc).__name__, exc, delay)
                )
                time.sleep(delay)
                delay *= self.backoff
        raise HlthNetworkError(method, url, attempts, last)

    def post(
        self,
        path: str,
        data: Any,
        *,
        encrypt: bool = True,
        snake_case: bool = False,
        host_header: Optional[str] = None,
    ) -> Any:
        """POST an app-style request.

        `data` is a python object.  Unless `encrypt=False`, it is wrapped the
        same way the RN axios interceptor does: payload -> {data: JSON(...)}
        -> RC4 signed form body.
        """
        url = self.base_url + path
        headers = self._headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if host_header:
            headers["Host"] = host_header

        payload = data
        if snake_case and isinstance(payload, dict):
            payload = _convert_to_snake_case(payload)

        if encrypt:
            nonce = make_nonce(self.server_time())
            outer = {"data": json.dumps(payload, separators=(",", ":"), ensure_ascii=False)}
            enc = encrypt_parameters(outer, path, "post", nonce, self.ssecurity)
            body = qs_stringify(enc)
        else:
            nonce = ""
            body = qs_stringify(
                {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in payload.items()}
            )

        self._log("POST", url, "encrypted=", encrypt)
        resp = self._request(
            "POST", url, data=body.encode("utf-8"), headers=headers
        )
        self._update_time_diff(resp)
        text = resp.text
        if resp.status_code != 200:
            raise HlthHttpError(resp.status_code, text[:800], url)

        if encrypt:
            try:
                plain = decrypt_response(text, nonce, self.ssecurity)
            except Exception as exc:  # pragma: no cover - diagnostics
                raise HlthDecryptError(
                    "failed to decrypt response from %s: %s (body head=%r)" % (url, exc, text[:200])
                ) from exc
        else:
            plain = text

        try:
            return json.loads(plain)
        except json.JSONDecodeError as exc:
            raise HlthDecryptError(
                "response is not JSON from %s: %r" % (url, plain[:500])
            ) from exc

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        encrypt: bool = True,
    ) -> Any:
        url = self.base_url + path
        headers = self._headers()
        if encrypt:
            nonce = make_nonce(self.server_time())
            enc = encrypt_parameters(
                {k: str(v) for k, v in (params or {}).items()}, path, "get", nonce, self.ssecurity
            )
        else:
            enc = params or {}
        resp = self._request("GET", url, params=enc, headers=headers)
        self._update_time_diff(resp)
        if resp.status_code != 200:
            raise HlthHttpError(resp.status_code, resp.text[:800], url)
        if encrypt:
            return json.loads(decrypt_response(resp.text, nonce, self.ssecurity))
        return resp.json()

    # -- eco proxy --------------------------------------------------------- #
    def eco_api(self, eco_api: str, params: Dict[str, Any]) -> Any:
        """Call /app/v1/eco/api_proxy and return the parsed `resp` JSON.

        The RN wrapper (he-1828 postEcoApi) does:
            post("/app/v1/eco/api_proxy",
                 {eco_api: eco_api, params: JSON.stringify(params)},
                 {hostType: HLTH, enableCaseConversion: false})
            -> JSON.parse(result.resp)
        """
        params_json = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        raw = self.post(
            "/app/v1/eco/api_proxy",
            {"eco_api": eco_api, "params": params_json},
            encrypt=True,
            snake_case=False,
        )
        self._log("eco_api", eco_api, "response keys:", list(raw) if isinstance(raw, dict) else type(raw))
        result = raw.get("result") if isinstance(raw, dict) else None
        resp_text = (result or {}).get("resp") if isinstance(result, dict) else None
        if resp_text is None:
            # Some deployments answer directly with the object.
            if isinstance(raw, dict) and isinstance(raw.get("resp"), str):
                resp_text = raw["resp"]
            else:
                raise HlthProtocolError("unexpected eco response: %r" % (raw,))
        return json.loads(resp_text)

    # -- domain helpers (port of he-1828) ---------------------------------- #
    def get_virtual_account_list(self, uid: int) -> List[Dict[str, Any]]:
        s = self.eco_api("eco/scale/account/list", {"uid": int(uid)})
        data = s.get("data")
        if data is None:
            data = s.get("result")
        data = data or []
        out = []
        for item in data:
            item = dict(item)
            for k in ("accountId", "birth", "height", "sex", "weightTarget"):
                if k in item:
                    item[k] = _num(item[k])
            if item.get("sex") == 2:
                item["sex"] = 0
            out.append(item)
        return out

    def get_records(
        self,
        uid: int,
        account_id: Optional[int] = None,
        page_size: int = 200,
        since_ms: Optional[int] = None,
        max_pages: int = 500,
        eco_api: str = "eco/scale/record/pull",
        did: str = "0",
        model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Port of getVirtualMemberRecords(accountId, uid, page, pageSize).

        ``eco_api`` may be ``eco/scale/record/pull`` (default, cursor by
        createTime) or ``eco/common/scale/pull`` (same params plus ``model``).
        """
        cursor = int(since_ms) if since_ms else int(time.time() * 1000)
        records: List[Dict[str, Any]] = []
        seen = set()
        pages = 0
        while pages < max_pages:
            params = {
                "uid": int(uid),
                "did": did,
                "pageSize": int(page_size),
                "createTime": int(cursor),
            }
            if model is not None:
                params["model"] = model
            page = self.eco_api(eco_api, params)
            data = page.get("data")
            if not isinstance(data, list) or page.get("code") != 200 or not data:
                break
            pages += 1
            for rec in data:
                norm = normalize_pull_record(rec)
                if not norm:
                    continue
                if account_id is not None and norm.get("accountId") != account_id:
                    continue
                key = (norm.get("accountId"), norm.get("time"), norm.get("weight"))
                if key in seen:
                    continue
                seen.add(key)
                records.append(norm)
            times = [
                t
                for t in (_create_time_ms(r.get("createTime")) for r in data)
                if t is not None
            ]
            if not times:
                break
            minimum = min(times)
            if minimum >= cursor or len(data) < page_size:
                break
            cursor = minimum
        records.sort(key=lambda r: r.get("time") or 0)
        return records

    def list_records(
        self,
        account_id: int,
        page_size: int = 200,
        max_pages: int = 500,
    ) -> List[Dict[str, Any]]:
        """Port of MainViewModel.getWeightRecords -> eco/scale/record/list.

        Native params are ``{accountId, page, pageSize}``; pages start at 1.
        """
        records: List[Dict[str, Any]] = []
        seen = set()
        for page_no in range(1, max_pages + 1):
            page = self.eco_api(
                "eco/scale/record/list",
                {"accountId": int(account_id), "page": page_no, "pageSize": int(page_size)},
            )
            data = page.get("data")
            if not isinstance(data, list) or page.get("code") != 200 or not data:
                break
            for rec in data:
                norm = normalize_pull_record(rec)
                if not norm:
                    continue
                key = (norm.get("accountId"), norm.get("time"), norm.get("weight"))
                if key in seen:
                    continue
                seen.add(key)
                records.append(norm)
            if len(data) < page_size:
                break
        records.sort(key=lambda r: r.get("time") or 0)
        return records

    def get_third_device_source_list(
        self,
        page_size: int = 50,
        page_start: int = 0,
        status: int = 0,
    ) -> Any:
        """The「其他设备」list under the 设备 tab (native ThirdPartyDeviceApiService)."""
        return self.post(
            "/app/v1/source/get_third_device_source_list",
            {"page_size": int(page_size), "page_start": int(page_start), "status": int(status)},
            encrypt=True,
            snake_case=True,
        )

    # -- fitness data (the real body-composition source) -------------------- #
    def get_fitness_data_page(
        self,
        key: str = "weight",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        next_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One page of ``/app/v1/data/get_fitness_data_by_time``.

        ``key="weight"`` carries the full eight-electrode report as a JSON
        string in ``value``::

            {"code":0,"message":"ok",
             "result":{"data_list":[{"sid":"blt.3...","key":"weight",
                                     "time":1794526019,
                                     "value":"{\\"body_fat_rate\\":16.0,...}"}],
                       "has_more":true,"next_key":"eyJzaWQiOi..."}}

        ``time`` is unix **seconds**; ``sid`` is the scale's Mijia did (or
        ``xiaomiwear_app_manually`` for weights typed into the app).
        """
        if end_time is None:
            end_time = int(time.time())
        if start_time is None:
            start_time = end_time - 365 * 86400
        payload: Dict[str, Any] = {
            "start_time": int(start_time),
            "end_time": int(end_time),
            "key": key,
        }
        if next_key:
            payload["next_key"] = str(next_key)
        raw = self.post("/app/v1/data/get_fitness_data_by_time", payload)
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            raise HlthProtocolError("unexpected fitness response: %r" % (raw,))
        return result

    def iter_fitness_data(
        self,
        key: str = "weight",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        max_pages: int = 500,
    ) -> List[Dict[str, Any]]:
        """Every ``data_list`` row for ``key`` between the two unix seconds."""
        items: List[Dict[str, Any]] = []
        next_key: Optional[str] = None
        seen: set = set()
        for _ in range(max_pages):
            page = self.get_fitness_data_page(key, start_time, end_time, next_key)
            data = page.get("data_list") or []
            items.extend(data)
            nk = page.get("next_key")
            if not page.get("has_more") or not nk or not data:
                break
            nk = str(nk)
            if nk in seen:  # defensive: pagination cursor loop
                break
            seen.add(nk)
            next_key = nk
        return items

    def get_weight_records(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        max_pages: int = 500,
    ) -> List[Dict[str, Any]]:
        """Flattened weight/body-composition records, oldest first.

        Each row is the parsed ``value`` JSON plus::

            time      unix milliseconds
            time_sec  unix seconds
            sid       scale did (e.g. blt.3.1nbi7q1nk4k00)
            key       fitness data key ("weight")
            source    "fitness/weight"
            has_body_composition  bool (body_fat_rate > 0)
        """
        rows: List[Dict[str, Any]] = []
        for item in self.iter_fitness_data("weight", start_time, end_time, max_pages):
            value = item.get("value")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = {}
            if not isinstance(value, dict):
                value = {}
            row = dict(value)
            try:
                t = int(item.get("time") or value.get("time") or 0)
            except (TypeError, ValueError):
                t = 0
            row["time_sec"] = t
            row["time"] = t * 1000 if t and t < 10 ** 11 else t
            row["sid"] = item.get("sid")
            row["key"] = item.get("key") or "weight"
            row["source"] = "fitness/weight"
            bfr = row.get("body_fat_rate")
            row["has_body_composition"] = bool(isinstance(bfr, (int, float)) and bfr > 0)
            rows.append(row)
        rows.sort(key=lambda r: r.get("time") or 0)
        return rows


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class HlthError(Exception):
    pass


class HlthHttpError(HlthError):
    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__("HTTP %s from %s: %s" % (status, url, body))


class HlthNetworkError(HlthError):
    """Network-level failure (timeout / TLS / reset) after every retry."""

    def __init__(self, method: str, url: str, attempts: int, last: Optional[BaseException]):
        self.method = method
        self.url = url
        self.attempts = attempts
        self.last = last
        detail = "%s: %s" % (type(last).__name__, last) if last else "unknown error"
        super().__init__(
            "%s %s failed after %d attempt(s): %s" % (method, url, attempts, detail)
        )


class HlthDecryptError(HlthError):
    pass


class HlthProtocolError(HlthError):
    pass


# --------------------------------------------------------------------------- #
# record normalization (port of normalizePullRecord)
# --------------------------------------------------------------------------- #
def _num(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else f


def _create_time_ms(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        if n <= 0:
            return None
        return int(n * 1000) if n < 1e10 else int(n)
    if isinstance(v, str) and v:
        try:
            if v.isdigit():
                n = float(v)
                return int(n * 1000) if n < 1e10 else int(n)
            import datetime as _dt

            s = v.replace("-", "/")
            return int(_dt.datetime.strptime(s, "%Y/%m/%d %H:%M:%S").timestamp() * 1000)
        except Exception:
            try:
                return int(float(v) * (1000 if float(v) < 1e10 else 1))
            except Exception:
                return None
    return None


def _rate(spec: Any, weight: Any) -> Optional[float]:
    try:
        a = float(spec)
        b = float(weight)
        if b <= 0:
            return None
        return round(1000 * a / b) / 10
    except (TypeError, ValueError):
        return None


_IMPEDANCE_PAIRS = [
    ("lfz12", "measuring_impedance_1"),
    ("lfz13", "measuring_impedance_2"),
    ("lfz14", "measuring_impedance_3"),
    ("lfz23", "measuring_impedance_4"),
    ("lfz24", "measuring_impedance_5"),
    ("lfz34", "measuring_impedance_6"),
    ("hfz12", "measuring_impedance_7"),
    ("hfz13", "measuring_impedance_8"),
    ("hfz14", "measuring_impedance_9"),
    ("hfz23", "measuring_impedance_10"),
    ("hfz24", "measuring_impedance_11"),
    ("hfz34", "measuring_impedance_12"),
]

_SIMPLE_FIELDS = [
    ("weight", "weight"),
    ("bmi", "bmi"),
    ("bodyFatRate", "body_fat_rate"),
    ("moistureRate", "moisture_rate"),
    ("boneMass", "bone_mass"),
    ("basalMetabolism", "basal_metabolism"),
    ("muscleMass", "muscle_mass"),
    ("proteinMass", "protein_mass"),
    ("visceralFat", "visceral_fat"),
    ("bodyMoistureMass", "body_moisture_mass"),
    ("fatMass", "fat_mass"),
    ("limbsSkeletalMuscleIndex", "limbs_skeletal_muscle_index"),
    ("leftUpperLimbMuscleMass", "left_upper_limb_muscle_mass"),
    ("leftLowerLimbMuscleMass", "left_lower_limb_muscle_mass"),
    ("rightUpperLimbMuscleMass", "right_upper_limb_muscle_mass"),
    ("rightLowerLimbMuscleMass", "right_lower_limb_muscle_mass"),
    ("trunkMuscleMass", "trunk_muscle_mass"),
    ("leftUpperLimbFatMass", "left_upper_limb_fat_mass"),
    ("leftLowerLimbFatMass", "left_lower_limb_fat_mass"),
    ("rightUpperLimbFatMass", "right_upper_limb_fat_mass"),
    ("rightLowerLimbFatMass", "right_lower_limb_fat_mass"),
    ("trunkFatMass", "trunk_fat_mass"),
    ("leftUpperLimbMuscleMassLevel", "left_upper_limb_muscle_mass_level"),
    ("leftLowerLimbMuscleMassLevel", "left_lower_limb_muscle_mass_level"),
    ("rightUpperLimbMuscleMassLevel", "right_upper_limb_muscle_mass_level"),
    ("rightLowerLimbMuscleMassLevel", "right_lower_limb_muscle_mass_level"),
    ("trunkMuscleMassLevel", "trunk_muscle_mass_level"),
    ("leftUpperLimbFatMassLevel", "left_upper_limb_fat_mass_level"),
    ("leftLowerLimbFatMassLevel", "left_lower_limb_fat_mass_level"),
    ("rightUpperLimbFatMassLevel", "right_upper_limb_fat_mass_level"),
    ("rightLowerLimbFatMassLevel", "right_lower_limb_fat_mass_level"),
    ("trunkFatMassLevel", "trunk_fat_mass_level"),
    ("upperLimbMuscleBalance", "upper_limb_muscle_balance"),
    ("lowerLimbMuscleBalance", "lower_limb_muscle_balance"),
    ("limbsMuscleBalance", "limbs_muscle_balance"),
    ("upperLimbFatBalance", "upper_limb_fat_balance"),
    ("lowerLimbFatBalance", "lower_limb_fat_balance"),
    ("limbsFatBalance", "limbs_fat_balance"),
    ("somatotype", "somatotype"),
    ("bodyShape", "body_shape"),
    ("whr", "whr"),
    ("bodyScore", "body_score"),
    ("bodyAge", "body_age"),
    ("standardWeight", "standard_weight"),
    ("recommendedCaloriesIntake", "recommended_calories_intake"),
    ("weightControl", "weight_control"),
    ("muscleControl", "muscle_control"),
    ("fatControl", "fat_control"),
]


def normalize_pull_record(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(rec, dict):
        return None
    out: Dict[str, Any] = {}
    for src, dst in _SIMPLE_FIELDS:
        if src in rec:
            out[dst] = _num(rec[src])
    account_id = _num(rec.get("accountId"))
    if account_id is not None:
        out["accountId"] = account_id
    t = _create_time_ms(rec.get("createTime"))
    if t is not None:
        out["time"] = t
        out["time_text"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t / 1000))
    for src, dst in _IMPEDANCE_PAIRS:
        if src in rec:
            out[dst] = _num(rec[src])
    if "heartRate" in rec:
        out["bpm"] = _num(rec["heartRate"])
    for src, dst in (
        ("muscleMass", "muscle_rate"),
        ("proteinMass", "protein_rate"),
        ("boneMass", "bone_rate"),
    ):
        if src in rec and "weight" in rec:
            r = _rate(rec[src], rec["weight"])
            if r is not None:
                out[dst] = r
    # keep anything else the server sends so nothing is lost
    for k, v in rec.items():
        if k in out or k in ("createTime",):
            continue
        out.setdefault(k, v)
    return out if out.get("time") is not None else None


def _convert_to_snake_case(obj: Any) -> Any:
    if isinstance(obj, list):
        return [_convert_to_snake_case(x) for x in obj]
    if isinstance(obj, dict):
        import re

        conv = lambda k: re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), k)
        return {conv(k): _convert_to_snake_case(v) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- #
# cookie json loading
# --------------------------------------------------------------------------- #
def load_client_from_file(path: str, verbose: bool = False) -> MiHealthClient:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # accept a few shapes
    if "cookies" in data and isinstance(data["cookies"], dict):
        data = {**data, **data["cookies"]}
    svc = data.get("serviceToken") or data.get("service_token")
    cid = data.get("cUserId") or data.get("c_user_id") or data.get("userId")
    ss = data.get("ssecurity")
    if not (svc and cid and ss):
        raise SystemExit("cookies file %s lacks serviceToken/cUserId/ssecurity" % path)
    return MiHealthClient(
        service_token=svc,
        c_user_id=cid,
        ssecurity=ss,
        locale=data.get("locale") or "zh_CN",
        ua=data.get("ua") or DEFAULT_UA,
        verbose=verbose,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mi Health HLTH client (eco proxy)")
    ap.add_argument("--cookies", default=os.path.join(os.path.dirname(__file__), "..", "auth", "cookies.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    p_acc = sub.add_parser("accounts", help="list eco scale accounts for a uid")
    p_acc.add_argument("--uid", type=int, required=True)

    p_rec = sub.add_parser("records", help="pull body-composition records")
    p_rec.add_argument("--uid", type=int, required=True)
    p_rec.add_argument("--account-id", type=int, default=None)
    p_rec.add_argument("--page-size", type=int, default=200)
    p_rec.add_argument("--since", type=int, default=None, help="createTime cursor (ms)")
    p_rec.add_argument("--out", default=None)

    p_raw = sub.add_parser("raw", help="raw eco call")
    p_raw.add_argument("eco_api")
    p_raw.add_argument("--params", default="{}")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2

    client = load_client_from_file(os.path.abspath(args.cookies), verbose=args.verbose)

    if args.cmd == "accounts":
        print(json.dumps(client.get_virtual_account_list(args.uid), ensure_ascii=False, indent=2))
    elif args.cmd == "records":
        recs = client.get_records(
            args.uid,
            account_id=args.account_id,
            page_size=args.page_size,
            since_ms=args.since,
        )
        text = json.dumps(recs, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print("wrote %d records -> %s" % (len(recs), args.out))
        else:
            print(text)
    elif args.cmd == "raw":
        params = json.loads(args.params)
        print(json.dumps(client.eco_api(args.eco_api, params), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
