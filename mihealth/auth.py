#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Xiaomi passport (Mi Account) headless SMS login for Mi Health / Mi Fitness.

This module reproduces the phone-ticket login flow that the Mi Health app
(com.mi.health 3.61.0) uses through the bundled Mi Account SDK
(com.xiaomi.accountsdk.*), so a plain Python process can obtain and reuse
Xiaomi cloud session cookies without a phone, emulator or browser.

Flow (all endpoints live-verified as reachable, see REPORT.md):

  1. GET  https://account.xiaomi.com/pass/serviceLogin?sid=<sid>&_json=true
     -> challenge JSON with ``_sign`` / ``qs`` / ``callback`` (code 70016 when
        not logged in, which is expected).
  2. POST https://account.xiaomi.com/pass/sendServiceLoginTicket
     form: user=<EUI-encrypted phone>, sid, type=sms, _json=true
     header: EUI: <rsa-encrypted-key>.<b64 names>.<b64 sha256(values)>
     -> sends the SMS code (success: code 0 + data.vCodeLen).
  3. POST https://account.xiaomi.com/pass/phoneInfo
     form: user=<enc phone>, ticket=<enc code>, sid, type=sms, _json=true
     header: EUI over ["user","ticket"]
     -> response header ``ticketToken`` + user info (data.id, data.phone, ...).
  4. POST https://account.xiaomi.com/pass/serviceLoginTicketAuth
     form: user=<enc phone>, ticket=<ticketToken>, sid, _json=true,
           _sign, qs, callback (from step 1 with ``_loginSign=ticket``)
     -> response headers ``userId`` + ``passToken``; JSON with
        ``ssecurity`` / ``nonce`` / ``location`` / ``securityStatus``.
  5. GET  https://account.xiaomi.com/pass/serviceLogin?sid=<sid>&_json=true
     cookies: userId, passToken, deviceId
     -> JSON with ``ssecurity``, ``nonce``, ``location``, ``cUserId``.
  6. GET  <location>&clientSign=<b64 sha1("nonce=<nonce>&<ssecurity>")>
     (no redirect following)
     -> Set-Cookie: serviceToken=..., cUserId=...

The resulting record is written to ``cookies.json`` next to this file and can
be re-validated / refreshed later with the stored ``passToken`` (no SMS
needed until the passToken itself expires).

CLI examples::

    python xiaomi_auth.py send 13800138000
    python xiaomi_auth.py verify 13800138000 123456
    python xiaomi_auth.py refresh
    python xiaomi_auth.py show

    # one-shot interactive login
    python xiaomi_auth.py login 13800138000

Importable API::

    from xiaomi_auth import login_with_sms, get_service_token, build_session
    tokens = login_with_sms("13800138000")          # prompts for SMS code
    sess = build_session("miothealth")              # requests.Session with cookies

No secrets are hard-coded: the EUI RSA public key is fetched from Xiaomi at
runtime and every device id / AES key is generated locally.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# --------------------------------------------------------------------------- #
# Constants (verified against the Mi Health 3.61.0 APK / live endpoints)
# --------------------------------------------------------------------------- #

ACCOUNT_BASE = "https://account.xiaomi.com"
PASS_BASE = ACCOUNT_BASE + "/pass"
URL_SERVICE_LOGIN = PASS_BASE + "/serviceLogin"
URL_SEND_LOGIN_TICKET = PASS_BASE + "/sendServiceLoginTicket"
URL_PHONE_INFO = PASS_BASE + "/phoneInfo"
URL_TICKET_AUTH = PASS_BASE + "/serviceLoginTicketAuth"
URL_EUI_CONFIG = ACCOUNT_BASE + "/pass2/config"
CAPTCHA_URL = PASS_BASE + "/getCode?icodeType=login"

DEFAULT_SID = "miothealth"          # com.xiaomi.fitness.account.SIDs.MIOT_HEALTH
KNOWN_SIDS = {
    "miothealth": "Mi Health / Mi Fitness cloud (sts-hlth.io.mi.com/healthapp/sts)",
    "mihealth": "Mi Health legacy STS (api.health.comm.miui.com/sts)",
    "xiaomiio": "MiIO / Mijia cloud (sts.api.io.mi.com/sts) - api.io.mi.com",
    "mijia": "Mijia (sts.api.mijia.tech/mijia/sts)",
    "i.mi.com": "Xiaomi Cloud storage (i.mi.com/sts)",
}

# Stable client identity. Must stay identical across send -> verify -> refresh,
# otherwise Xiaomi's risk control can reject the login.
DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 13; 22081212C Build/TKQ1.220829.002; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/119.0.6045.193 "
    "Mobile Safari/537.36"
)
DEFAULT_LOCALE = "zh_CN"
SAFE_PREFIX = "&&&START&&&"
REQUEST_TIMEOUT = 20
AES_IV = b"0102030405060708"

HERE = Path(__file__).resolve().parent
DEFAULT_TOKEN_PATH = HERE / "cookies.json"
DEVICE_PATH = HERE / "device.json"

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class AuthError(Exception):
    """Base error carrying Xiaomi's structured error response."""

    def __init__(self, message: str, *, code: Optional[int] = None,
                 desc: str = "", tips: str = "", body: Any = None,
                 http_status: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.desc = desc
        self.tips = tips
        self.body = body
        self.http_status = http_status

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        bits = [super().__str__()]
        if self.code is not None:
            bits.append(f"code={self.code}")
        if self.desc:
            bits.append(f"desc={self.desc}")
        if self.tips and self.tips != self.desc:
            bits.append(f"tips={self.tips}")
        return " | ".join(bits)


class CaptchaRequired(AuthError):
    """Xiaomi requires a captcha (code 87001); headless login is blocked."""

    def __init__(self, message: str, captcha_url: str = "", **kw):
        super().__init__(message, **kw)
        self.captcha_url = captcha_url


class NeedVerification(AuthError):
    """Xiaomi requires a second verification step (notificationUrl / 2FA)."""

    def __init__(self, message: str, notification_url: str = "", **kw):
        super().__init__(message, **kw)
        self.notification_url = notification_url


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def strip_safe_prefix(text: str) -> str:
    """Strip the ``&&&START&&&`` anti-XSSI prefix used by passport responses."""
    if text is None:
        return ""
    text = text.strip()
    if text.startswith(SAFE_PREFIX):
        return text[len(SAFE_PREFIX):]
    return text


def parse_passport_json(text: str) -> Dict[str, Any]:
    """Parse a passport JSON body, tolerating the ``&&&START&&&`` prefix."""
    body = strip_safe_prefix(text)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AuthError(
            "Xiaomi returned a non-JSON response (probably an HTML captcha or "
            "block page). First 300 chars: " + body[:300]
        ) from exc
    if not isinstance(data, dict):
        raise AuthError(f"Unexpected JSON payload type: {type(data).__name__}")
    return data


def _server_error(resp: requests.Response, data: Dict[str, Any],
                  what: str) -> AuthError:
    code = data.get("code")
    desc = str(data.get("description") or data.get("desc") or "")
    tips = str(data.get("tips") or "")
    if code == 87001:
        return CaptchaRequired(
            f"{what}: Xiaomi requires a captcha (code 87001). "
            f"Open {data.get('captchaUrl') or '(no captchaUrl)'} in a browser, "
            "or complete the login interactively.",
            captcha_url=str(data.get("captchaUrl") or ""),
            code=code, desc=desc, tips=tips, body=data,
            http_status=resp.status_code,
        )
    return AuthError(f"{what} failed: {desc or 'unknown server error'}",
                     code=code if isinstance(code, int) else None,
                     desc=desc, tips=tips, body=data,
                     http_status=resp.status_code)


def _is_success_code(code: Any) -> bool:
    """Passport treats code==0 (or a missing code) as success."""
    return code in (0, "0", None)


def normalize_phone(phone: str) -> str:
    """Normalise a mainland-China phone number to 11 digits (best effort)."""
    s = re.sub(r"[\s\-()]", "", str(phone).strip())
    if s.startswith("+86"):
        s = s[3:]
    elif s.startswith("0086"):
        s = s[4:]
    elif s.startswith("86") and len(s) == 13:
        s = s[2:]
    return s


def mask_secret(value: str, keep: int = 6) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return value[:keep] + "***"
    return value[:keep] + "..." + value[-4:]


def mask_cookie_string(cookie: str) -> str:
    """Mask every value in a 'k=v; k=v' cookie string (keeps names)."""
    parts = []
    for chunk in cookie.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            name, _, value = chunk.partition("=")
            parts.append(f"{name}={mask_secret(value)}")
        else:
            parts.append(chunk)
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# EUI encryption (Mi Account SDK EUIParamEncryptUtil / AESCoder / Coder)
# --------------------------------------------------------------------------- #


class EUICipher:
    """Reproduces com.xiaomi.accountsdk.account.utils.EUIParamEncryptUtil.

    ``user`` / ``ticket`` form fields are AES-CBC encrypted with a fresh random
    AES-128 key; the key itself is RSA(PKCS#1 v1.5) encrypted with Xiaomi's
    per-session EUI public key and shipped in the ``EUI`` header together with
    the encrypted parameter names and a SHA-256 signature.
    """

    def __init__(self, public_key_b64_der: str):
        der = base64.b64decode(public_key_b64_der)
        self._public_key = serialization.load_der_public_key(der)
        self._aes_key = os.urandom(16)
        # Android Base64 flag 10 == URL_SAFE | NO_WRAP
        encrypted_key = self._public_key.encrypt(
            base64.urlsafe_b64encode(self._aes_key),
            asym_padding.PKCS1v15(),
        )
        self._encrypted_key = base64.urlsafe_b64encode(encrypted_key).decode("ascii")

    def encrypt(self, value: str) -> str:
        """AES-CBC/PKCS5 encrypt a form value (standard base64, no wrap)."""
        padder = PKCS7(128).padder()
        padded = padder.update(value.encode("utf-8")) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self._aes_key), modes.CBC(AES_IV)).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("ascii")

    def eui(self, pairs: Sequence[Tuple[str, str]]) -> str:
        """Build the ``EUI`` header for the given (name, plaintext) pairs."""
        names: List[str] = []
        values: List[str] = []
        for name, value in pairs:
            if value is None or value == "":
                continue
            names.append(name)
            values.append(value)
        if not names:
            return ""
        names_b64 = base64.b64encode(",".join(names).encode("utf-8")).decode("ascii")
        sign_b64 = base64.b64encode(
            hashlib.sha256("".join(values).encode("utf-8")).digest()
        ).decode("ascii")
        return f"{self._encrypted_key}.{names_b64}.{sign_b64}"


def fetch_eui_public_key(session: requests.Session) -> str:
    """Fetch Xiaomi's current EUI RSA public key (base64 DER, X.509)."""
    resp = session.get(URL_EUI_CONFIG, params={"key": "eui"}, timeout=REQUEST_TIMEOUT)
    data = parse_passport_json(resp.text)
    key = data.get("euiPublicKey")
    if not key:
        raise AuthError("Xiaomi did not return euiPublicKey", body=data,
                        http_status=resp.status_code)
    return key


def client_sign(nonce: Any, ssecurity: str) -> str:
    """Compute the STS ``clientSign`` (Coder.generateSignature for nonce)."""
    if nonce is None or not ssecurity:
        raise AuthError("Cannot compute clientSign without nonce/ssecurity")
    raw = f"nonce={nonce}&{ssecurity}".encode("utf-8")
    return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")


# --------------------------------------------------------------------------- #
# Client identity persistence (stable device id)
# --------------------------------------------------------------------------- #


def load_or_create_device(device_path: Path = DEVICE_PATH) -> Dict[str, str]:
    """Load the persisted device identity or create a new one."""
    try:
        if device_path.exists():
            data = json.loads(device_path.read_text(encoding="utf-8"))
            if data.get("deviceId") and data.get("ua"):
                return {"deviceId": data["deviceId"], "ua": data["ua"]}
    except (OSError, ValueError):
        pass
    identity = {"deviceId": "wb_" + uuid.uuid4().hex, "ua": DEFAULT_UA}
    try:
        device_path.parent.mkdir(parents=True, exist_ok=True)
        device_path.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    except OSError:
        pass
    return identity


def new_session(device_id: str, ua: str = DEFAULT_UA) -> requests.Session:
    """Create a requests session with the stable UA and deviceId cookie."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": ua,
        "Accept": "application/json;charset=UTF-8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    session.cookies.set("deviceId", device_id, domain="account.xiaomi.com")
    return session


# --------------------------------------------------------------------------- #
# Passport protocol steps
# --------------------------------------------------------------------------- #


def fetch_challenge(session: requests.Session, sid: str = DEFAULT_SID,
                    phone_ticket_meta: bool = False) -> Dict[str, Any]:
    """GET serviceLogin and return the challenge JSON.

    ``phone_ticket_meta=True`` adds ``_loginSign=ticket`` which makes the server
    return the ``_sign`` / ``qs`` / ``callback`` triple required by the
    ``serviceLoginTicketAuth`` call (this mirrors
    XMPassport.getPhoneLoginMetaLoginData).
    """
    params: Dict[str, str] = {"sid": sid, "_json": "true"}
    if phone_ticket_meta:
        params["_loginSign"] = "ticket"
    resp = session.get(URL_SERVICE_LOGIN, params=params, timeout=REQUEST_TIMEOUT)
    data = parse_passport_json(resp.text)
    if data.get("code") == 21315:
        raise AuthError(f"Invalid sid '{sid}' (code 21315)", code=21315,
                        desc=str(data.get("description") or ""), body=data,
                        http_status=resp.status_code)
    return data


def send_sms_code(phone: str, sid: str = DEFAULT_SID, *,
                  session: Optional[requests.Session] = None,
                  captcha_code: str = "", captcha_ick: str = "",
                  verify_token: str = "", action: str = "",
                  ticket_type: str = "sms",
                  device_path: Path = DEVICE_PATH) -> Dict[str, Any]:
    """Request an SMS login code for ``phone`` (does NOT log in yet).

    Returns a dict with ``ok``, ``code_length`` and the raw server response.
    """
    phone = normalize_phone(phone)
    if not phone:
        raise AuthError("Empty phone number")
    identity = load_or_create_device(device_path)
    session = session or new_session(identity["deviceId"], identity["ua"])
    eui = EUICipher(fetch_eui_public_key(session))

    form: Dict[str, str] = {
        "user": eui.encrypt(phone),
        "sid": sid,
        "type": ticket_type or "sms",
        "_json": "true",
        "_locale": DEFAULT_LOCALE,
    }
    if captcha_code:
        form["captCode"] = captcha_code
    if captcha_ick:
        form["captIck"] = captcha_ick
    if verify_token:
        form["vToken"] = verify_token
    if action:
        form["vAction"] = action

    headers = {"EUI": eui.eui([("user", phone)])}
    resp = session.post(URL_SEND_LOGIN_TICKET, data=form, headers=headers,
                        timeout=REQUEST_TIMEOUT)
    data = parse_passport_json(resp.text)
    code = data.get("code")
    if not _is_success_code(code):
        raise _server_error(resp, data, "sendServiceLoginTicket")
    data_block = data.get("data") or {}
    code_len = data_block.get("vCodeLen")
    return {
        "ok": True,
        "sid": sid,
        "phone": phone,
        "code_length": int(code_len) if isinstance(code_len, int) else code_len,
        "deviceId": identity["deviceId"],
        "raw": data,
    }


def _extract_ticket_token(resp: requests.Response, data: Dict[str, Any]) -> str:
    token = resp.headers.get("ticketToken")
    if not token:
        # header lookup is case-insensitive in requests, but be defensive
        for key, value in resp.headers.items():
            if key.lower() == "tickettoken":
                token = value
                break
    if not token:
        data_block = data.get("data") or {}
        token = data_block.get("ticketToken")
    if not token:
        raise AuthError("phoneInfo succeeded but no ticketToken was returned",
                        body=data, http_status=resp.status_code)
    return token


def verify_sms_code(phone: str, code: str, sid: str = DEFAULT_SID, *,
                    session: Optional[requests.Session] = None,
                    token_path: Path = DEFAULT_TOKEN_PATH,
                    device_path: Path = DEVICE_PATH,
                    save: bool = True) -> Dict[str, Any]:
    """Complete the SMS login and return the persisted credential record."""
    phone = normalize_phone(phone)
    code = str(code).strip()
    if not phone or not code:
        raise AuthError("Both phone and SMS code are required")
    identity = load_or_create_device(device_path)
    session = session or new_session(identity["deviceId"], identity["ua"])
    eui = EUICipher(fetch_eui_public_key(session))

    # 1) challenge meta (_sign / qs / callback) for serviceLoginTicketAuth
    meta = fetch_challenge(session, sid, phone_ticket_meta=True)
    for key in ("_sign", "qs", "callback"):
        if not meta.get(key):
            raise AuthError(f"serviceLogin challenge is missing '{key}'",
                            body=meta)

    # 2) POST /pass/phoneInfo  -> ticketToken header
    phone_info_form = {
        "user": eui.encrypt(phone),
        "ticket": eui.encrypt(code),
        "sid": sid,
        "type": "sms",
        "_json": "true",
        "_locale": DEFAULT_LOCALE,
    }
    resp = session.post(
        URL_PHONE_INFO, data=phone_info_form,
        headers={"EUI": eui.eui([("user", phone), ("ticket", code)])},
        timeout=REQUEST_TIMEOUT,
    )
    data = parse_passport_json(resp.text)
    if not _is_success_code(data.get("code")):
        if data.get("code") == 10031:
            raise AuthError("Wrong SMS verification code (code 10031)",
                            code=10031, desc=str(data.get("description") or ""),
                            body=data, http_status=resp.status_code)
        raise _server_error(resp, data, "phoneInfo (SMS code verification)")
    ticket_token = _extract_ticket_token(resp, data)
    user_info = data.get("data") or {}

    # 3) POST /pass/serviceLoginTicketAuth -> userId + passToken headers
    auth_form = {
        "user": eui.encrypt(phone),
        "ticket": ticket_token,
        "sid": sid,
        "_json": "true",
        "_sign": meta["_sign"],
        "qs": meta["qs"],
        "callback": meta["callback"],
        "_locale": DEFAULT_LOCALE,
    }
    session.cookies.set("ticketToken", ticket_token, domain="account.xiaomi.com")
    resp = session.post(
        URL_TICKET_AUTH, data=auth_form,
        headers={"EUI": eui.eui([("user", phone)])},
        timeout=REQUEST_TIMEOUT,
    )
    data = parse_passport_json(resp.text)
    if not _is_success_code(data.get("code")):
        raise _server_error(resp, data, "serviceLoginTicketAuth")
    security_status = data.get("securityStatus") or 0
    notification_url = str(data.get("notificationUrl") or "")
    user_id = resp.headers.get("userId") or data.get("userId")
    pass_token = resp.headers.get("passToken") or data.get("passToken")
    if security_status not in (0, "0") or notification_url:
        raise NeedVerification(
            "Xiaomi requires an extra verification step for this account "
            f"(securityStatus={security_status}). Open {notification_url} in a "
            "browser to finish it, then retry 'refresh'.",
            notification_url=notification_url,
            code=data.get("code"), desc=str(data.get("description") or ""),
            body=data, http_status=resp.status_code,
        )
    if not user_id or not pass_token:
        raise AuthError(
            "serviceLoginTicketAuth returned success but userId/passToken "
            "headers are missing", body=data, http_status=resp.status_code)

    # 4) passToken re-login (same call the app makes on every cold start)
    record = _login_with_passtoken_impl(
        session, str(user_id), str(pass_token), sid,
        fallback=data, phone=phone, device_id=identity["deviceId"],
        ua=identity["ua"], user_info=user_info,
    )
    if save:
        save_tokens(record, token_path)
    return record


def _extract_service_token(resp: requests.Response) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Collect serviceToken / cUserId from a STS response.

    Returns (cookies, raw_set_cookie_map).
    """
    cookies: Dict[str, str] = {}
    raw: Dict[str, str] = {}
    for cookie in resp.cookies:
        cookies[cookie.name] = cookie.value
        raw[cookie.name] = cookie.value
    for header in resp.headers.get("Set-Cookie", "").split(","):
        if "=" not in header:
            continue
        name, _, rest = header.partition("=")
        name = name.strip()
        value = rest.split(";", 1)[0].strip()
        if name and name not in raw:
            raw[name] = value
            cookies.setdefault(name, value)
    return cookies, raw


def _sts_get(session: requests.Session, location: str, nonce: Any,
             ssecurity: str, sid: str) -> Dict[str, str]:
    """GET the STS autoLoginUrl with clientSign and capture serviceToken."""
    if not location:
        raise AuthError("Passport response did not contain a STS location")
    sign = client_sign(nonce, ssecurity)
    resp = session.get(
        location,
        params={"clientSign": sign, "_userIdNeedEncrypt": "true"},
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    cookies, raw = _extract_service_token(resp)
    service_token = (
        cookies.get("serviceToken")
        or raw.get("serviceToken")
        or cookies.get(f"{sid}_serviceToken")
        or raw.get(f"{sid}_serviceToken")
    )
    c_user_id = cookies.get("cUserId") or raw.get("cUserId") or ""
    if not service_token:
        # Some variants return a JSON body instead of Set-Cookie headers.
        try:
            data = parse_passport_json(resp.text)
        except AuthError:
            data = {}
        service_token = str(data.get("serviceToken") or "")
        c_user_id = c_user_id or str(data.get("cUserId") or "")
    if not service_token:
        safe_headers = {
            k: (mask_cookie_string(v) if k.lower() == "set-cookie" else v)
            for k, v in resp.headers.items()
        }
        raise AuthError(
            "STS did not return a serviceToken cookie (login may be blocked or "
            "the passToken is invalid).",
            body={"status": resp.status_code,
                  "headers": safe_headers,
                  "text": strip_safe_prefix(resp.text)[:1000]},
            http_status=resp.status_code,
        )
    return {
        "serviceToken": service_token,
        "cUserId": c_user_id,
        "rawSetCookie": raw,
        "status": str(resp.status_code),
        "location": resp.headers.get("Location", ""),
    }


def _login_with_passtoken_impl(session: requests.Session, user_id: str,
                               pass_token: str, sid: str, *,
                               fallback: Optional[Dict[str, Any]] = None,
                               phone: str = "", device_id: str = "",
                               ua: str = DEFAULT_UA,
                               user_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Step 5+6: serviceLogin with userId/passToken cookies, then STS."""
    session.cookies.set("userId", user_id, domain="account.xiaomi.com")
    session.cookies.set("passToken", pass_token, domain="account.xiaomi.com")
    resp = session.get(
        URL_SERVICE_LOGIN,
        params={"sid": sid, "_json": "true", "_locale": DEFAULT_LOCALE},
        timeout=REQUEST_TIMEOUT,
    )
    data = parse_passport_json(resp.text)
    if not _is_success_code(data.get("code")):
        # The ticket-auth response already contains ssecurity/location on some
        # accounts; use it as a fallback so a flaky serviceLogin does not lose
        # an otherwise successful SMS login.
        if fallback and fallback.get("location") and fallback.get("ssecurity"):
            data = dict(fallback)
        else:
            raise _server_error(resp, data,
                                "serviceLogin with stored passToken (passToken may "
                                "be expired or revoked)")
    ssecurity = str(data.get("ssecurity") or "")
    nonce = data.get("nonce")
    location = str(data.get("location") or "")
    if not ssecurity or nonce is None:
        raise AuthError("Passport login response is missing ssecurity/nonce",
                        body=data, http_status=resp.status_code)
    sts = _sts_get(session, location, nonce, ssecurity, sid)
    c_user_id = sts.get("cUserId") or str(data.get("cUserId") or "")
    uid = str(user_info.get("id")) if user_info and user_info.get("id") else c_user_id
    # Prefer the numeric account id when the server gives us one.
    numeric_uid = re.sub(r"\D", "", str(uid))
    if len(numeric_uid) < 5:
        numeric_uid = re.sub(r"\D", "", str(data.get("userId") or ""))
    if len(numeric_uid) < 5:
        numeric_uid = re.sub(r"\D", "", c_user_id)
    record = {
        "sid": sid,
        "userId": numeric_uid or str(uid),
        "cUserId": c_user_id,
        "serviceToken": sts["serviceToken"],
        "ssecurity": ssecurity,
        "passToken": pass_token,
        "deviceId": device_id,
        "locale": DEFAULT_LOCALE,
        "ua": ua,
        "phone": phone,
        "createdAt": _now_iso(),
        "timestamp": _now_iso(),
        "epoch": int(time.time()),
        "nonce": nonce,
        "location": location,
        "service_token_header": sts["serviceToken"],
    }
    record["raw_cookie"] = build_cookie_string(record)
    return record


def login_with_passtoken(user_id: str, pass_token: str, sid: str = DEFAULT_SID, *,
                         session: Optional[requests.Session] = None,
                         token_path: Path = DEFAULT_TOKEN_PATH,
                         device_path: Path = DEVICE_PATH,
                         save: bool = True) -> Dict[str, Any]:
    """Re-login (refresh) using a stored passToken; no SMS is needed."""
    identity = load_or_create_device(device_path)
    session = session or new_session(identity["deviceId"], identity["ua"])
    record = _login_with_passtoken_impl(
        session, str(user_id), str(pass_token), sid,
        device_id=identity["deviceId"], ua=identity["ua"],
    )
    if save:
        save_tokens(record, token_path)
    return record


# --------------------------------------------------------------------------- #
# Token persistence / importable API
# --------------------------------------------------------------------------- #


def build_cookie_string(record: Dict[str, Any]) -> str:
    """Build a raw Cookie header value usable directly with requests/curl."""
    pairs = []
    for key in ("userId", "cUserId", "serviceToken", "deviceId"):
        value = record.get(key)
        if value:
            pairs.append(f"{key}={value}")
    return "; ".join(pairs)


def save_tokens(record: Dict[str, Any], path: Path = DEFAULT_TOKEN_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("createdAt", _now_iso())
    record.setdefault("timestamp", _now_iso())
    record.setdefault("epoch", int(time.time()))
    record["raw_cookie"] = build_cookie_string(record)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_tokens(path: Path = DEFAULT_TOKEN_PATH, *, sid: Optional[str] = None,
                max_age_days: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Load a saved credential record; returns None when absent/invalid/stale."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if sid and record.get("sid") and record["sid"] != sid:
        return None
    if max_age_days is not None:
        epoch = record.get("epoch")
        if not isinstance(epoch, (int, float)):
            return None
        if (time.time() - epoch) > max_age_days * 86400:
            return None
    return record


def get_service_token(sid: str = DEFAULT_SID,
                      token_path: Path = DEFAULT_TOKEN_PATH,
                      device_path: Path = DEVICE_PATH,
                      auto_refresh: bool = True) -> Dict[str, Any]:
    """Return a valid credential record for ``sid``, refreshing via passToken."""
    record = load_tokens(token_path, sid=sid)
    if record and record.get("serviceToken"):
        return record
    if record and record.get("passToken") and record.get("userId"):
        if not auto_refresh:
            return record
        return login_with_passtoken(record["userId"], record["passToken"], sid,
                                    token_path=token_path, device_path=device_path)
    raise AuthError(
        f"No stored login for sid={sid}. Run 'python xiaomi_auth.py send <phone>' "
        "and then 'python xiaomi_auth.py verify <phone> <code>' first."
    )


def build_session(sid: str = DEFAULT_SID,
                  token_path: Path = DEFAULT_TOKEN_PATH,
                  device_path: Path = DEVICE_PATH,
                  auto_refresh: bool = True) -> requests.Session:
    """Build a requests.Session carrying the Xiaomi cloud cookies for ``sid``."""
    record = get_service_token(sid, token_path, device_path, auto_refresh)
    session = new_session(record.get("deviceId") or
                          load_or_create_device(device_path)["deviceId"],
                          record.get("ua") or DEFAULT_UA)
    cookie_values = {
        "userId": record.get("userId"),
        "cUserId": record.get("cUserId"),
        "serviceToken": record.get("serviceToken"),
        "deviceId": record.get("deviceId"),
    }
    for domain in ("account.xiaomi.com", ".xiaomi.com", ".mi.com",
                   "api.io.mi.com", "sts-hlth.io.mi.com", "hlth.io.mi.com"):
        for name, value in cookie_values.items():
            if value:
                try:
                    session.cookies.set(name, str(value), domain=domain)
                except Exception:  # pragma: no cover - requests cookie edge cases
                    pass
    return session


def login_with_sms(phone: str, code: Optional[str] = None,
                   sid: str = DEFAULT_SID,
                   token_path: Path = DEFAULT_TOKEN_PATH,
                   device_path: Path = DEVICE_PATH,
                   prompt: bool = True,
                   captcha_path: Optional[Path] = None) -> Dict[str, Any]:
    """Full SMS login helper: send the code (if needed), then verify it.

    Handles the login captcha (87001) interactively: when the server demands a
    captcha, the image is downloaded (``captcha_path`` or ``mihealth/captcha.png``)
    and the user is asked to type the characters shown in it.

    When ``code`` is None and ``prompt`` is True the code is read from stdin.
    Returns the credential record and writes it to ``token_path``.
    """
    if code is None:
        session = None
        captcha_code = ""
        captcha_ick = ""
        info: Optional[Dict[str, Any]] = None
        for _attempt in range(4):
            try:
                info = send_sms_code(
                    phone, sid,
                    session=session,
                    captcha_code=captcha_code,
                    captcha_ick=captcha_ick,
                    device_path=device_path,
                )
                break
            except CaptchaRequired as exc:
                if not prompt:
                    raise
                cap_path = Path(captcha_path) if captcha_path else (HERE / "captcha.png")
                cap = fetch_captcha(session=session, device_path=device_path,
                                    save_to=cap_path)
                session = cap["session"]
                print(f"需要图形验证码，图片已保存到: {cap['path']}")
                print("请打开图片，输入图中的字符（区分大小写）。")
                captcha_code = input("图形验证码: ").strip()
                captcha_ick = cap["ick"]
                if not captcha_code:
                    raise AuthError("Captcha code is required to continue")
        if info is None:
            raise AuthError("Could not send the SMS code (captcha not solved?)")
        print(f"SMS sent to {info['phone']} (sid={sid}); "
              f"code length: {info.get('code_length')}")
        if not prompt:
            raise AuthError("SMS code is required (pass code=...)")
        code = input("请输入短信验证码: ").strip()
    return verify_sms_code(phone, code, sid, token_path=token_path,
                           device_path=device_path)


def fetch_captcha(session: Optional[requests.Session] = None,
                  device_path: Path = DEVICE_PATH,
                  save_to: Optional[Path] = None) -> Dict[str, Any]:
    """Download a login captcha image (JPEG) and its ``ick`` cookie.

    Pass the returned ``session`` to :func:`send_sms_code` together with
    ``captcha_code``/``captcha_ick`` so the answer is bound to the same session.
    """
    identity = load_or_create_device(device_path)
    session = session or new_session(identity["deviceId"], identity["ua"])
    resp = session.get(CAPTCHA_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    ick = ""
    for cookie in session.cookies:
        if cookie.name == "ick":
            ick = cookie.value
            break
    if not ick:
        ick = resp.cookies.get("ick") or ""
    saved = None
    if save_to is not None:
        save_to = Path(save_to)
        save_to.parent.mkdir(parents=True, exist_ok=True)
        save_to.write_bytes(resp.content)
        saved = save_to
    return {
        "session": session,
        "ick": ick,
        "image": resp.content,
        "path": saved,
        "content_type": resp.headers.get("Content-Type", ""),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _print_record(record: Dict[str, Any]) -> None:
    raw_cookie = record.get("raw_cookie") or build_cookie_string(record)
    print(f"sid          : {record.get('sid')}")
    print(f"userId       : {record.get('userId')}")
    print(f"cUserId      : {record.get('cUserId')}")
    print(f"serviceToken : {mask_secret(str(record.get('serviceToken') or ''))}")
    print(f"ssecurity    : {mask_secret(str(record.get('ssecurity') or ''))}")
    print(f"passToken    : {mask_secret(str(record.get('passToken') or ''))}")
    print(f"deviceId     : {record.get('deviceId')}")
    print(f"locale       : {record.get('locale')}")
    print(f"phone        : {record.get('phone')}")
    print(f"createdAt    : {record.get('createdAt') or record.get('timestamp')}")
    print(f"raw_cookie   : {mask_cookie_string(raw_cookie)}")


def _cmd_send(args: argparse.Namespace) -> int:
    try:
        result = send_sms_code(
            args.phone, args.sid,
            captcha_code=args.captcha_code or "",
            captcha_ick=args.captcha_ick or "",
            verify_token=args.verify_token or "",
            action=args.action or "",
            device_path=Path(args.device_file) if args.device_file else DEVICE_PATH,
        )
    except CaptchaRequired as exc:
        print(f"CAPTCHA REQUIRED: {exc}", file=sys.stderr)
        if exc.captcha_url:
            print(f"captchaUrl: {exc.captcha_url}", file=sys.stderr)
        return 3
    except AuthError as exc:
        print(f"send failed: {exc}", file=sys.stderr)
        if exc.body is not None:
            _print_json(exc.body)
        return 2
    print(f"SMS code sent to {result['phone']} (sid={result['sid']}).")
    print(f"code length: {result.get('code_length')}")
    print(f"deviceId   : {result['deviceId']}")
    if args.json:
        _print_json(result["raw"])
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    token_path = Path(args.out) if args.out else DEFAULT_TOKEN_PATH
    try:
        record = verify_sms_code(
            args.phone, args.code, args.sid,
            token_path=token_path,
            device_path=Path(args.device_file) if args.device_file else DEVICE_PATH,
        )
    except CaptchaRequired as exc:
        print(f"CAPTCHA REQUIRED: {exc}", file=sys.stderr)
        if exc.captcha_url:
            print(f"captchaUrl: {exc.captcha_url}", file=sys.stderr)
        return 3
    except NeedVerification as exc:
        print(f"EXTRA VERIFICATION REQUIRED: {exc}", file=sys.stderr)
        if exc.notification_url:
            print(f"notificationUrl: {exc.notification_url}", file=sys.stderr)
        return 4
    except AuthError as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        if exc.http_status:
            print(f"http_status: {exc.http_status}", file=sys.stderr)
        if exc.body is not None:
            _print_json(exc.body)
        return 2
    print("Login succeeded.")
    _print_record(record)
    print(f"\nSaved credentials to: {token_path}")
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    token_path = Path(args.out) if args.out else DEFAULT_TOKEN_PATH
    record = load_tokens(token_path)
    if not record:
        print(f"No token file at {token_path}; run send/verify first.",
              file=sys.stderr)
        return 2
    if not record.get("passToken") or not record.get("userId"):
        print("Stored record has no passToken/userId; re-run the SMS login.",
              file=sys.stderr)
        return 2
    try:
        refreshed = login_with_passtoken(
            record["userId"], record["passToken"], args.sid or record.get("sid") or DEFAULT_SID,
            token_path=token_path,
            device_path=Path(args.device_file) if args.device_file else DEVICE_PATH,
        )
    except AuthError as exc:
        print(f"refresh failed: {exc}", file=sys.stderr)
        if exc.body is not None:
            _print_json(exc.body)
        return 2
    print("Refreshed.")
    _print_record(refreshed)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    token_path = Path(args.out) if args.out else DEFAULT_TOKEN_PATH
    record = load_tokens(token_path)
    if not record:
        print(f"No token file at {token_path}.", file=sys.stderr)
        return 2
    _print_record(record)
    if args.json:
        _print_json(record)
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    token_path = Path(args.out) if args.out else DEFAULT_TOKEN_PATH
    try:
        record = login_with_sms(
            args.phone, code=args.code, sid=args.sid,
            token_path=token_path,
            device_path=Path(args.device_file) if args.device_file else DEVICE_PATH,
        )
    except (AuthError, NeedVerification) as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 2
    print("Login succeeded.")
    _print_record(record)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Xiaomi passport SMS login for Mi Health / Mi Fitness.",
    )
    parser.add_argument("--sid", default=DEFAULT_SID,
                        help=f"passport service id (default: {DEFAULT_SID})")
    parser.add_argument("--out", default=str(DEFAULT_TOKEN_PATH),
                        help="token JSON path (default: ./cookies.json)")
    parser.add_argument("--device-file", default=str(DEVICE_PATH),
                        help="persisted device identity path")
    parser.add_argument("--json", action="store_true",
                        help="print raw server JSON responses")
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send", help="request an SMS login code")
    p_send.add_argument("phone")
    p_send.add_argument("--captcha-code", default="")
    p_send.add_argument("--captcha-ick", default="")
    p_send.add_argument("--verify-token", default="")
    p_send.add_argument("--action", default="")
    p_send.set_defaults(func=_cmd_send)

    p_verify = sub.add_parser("verify", help="verify the SMS code and save tokens")
    p_verify.add_argument("phone")
    p_verify.add_argument("code")
    p_verify.set_defaults(func=_cmd_verify)

    p_refresh = sub.add_parser("refresh",
                               help="re-login with the stored passToken (no SMS)")
    p_refresh.set_defaults(func=_cmd_refresh)

    p_show = sub.add_parser("show", help="show the stored credential record")
    p_show.set_defaults(func=_cmd_show)

    p_login = sub.add_parser("login", help="send + verify in one interactive run")
    p_login.add_argument("phone")
    p_login.add_argument("--code", default=None,
                         help="SMS code (skip the interactive prompt)")
    p_login.set_defaults(func=_cmd_login)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
