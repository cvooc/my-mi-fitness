#!/usr/bin/env python3
"""Restore the Mi Health login inside GitHub Actions from a repository secret.

The workflow sets ``MIHEALTH_TOKEN_B64`` (compatible fallback: ``MIHEALTH_TOKEN``)
to a base64 of the token JSON produced by ``tools/export_token.py``.  This script
decodes it and writes the local files the fetcher expects:

  data/tokens.json   -- login credentials (serviceToken + ssecurity + passToken)
  data/device.json   -- {deviceId, ua}, so the device fingerprint stays stable

Only masked details are printed; the secret itself never reaches the log.

Exit codes: 0 ok, 2 missing or invalid secret.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TOKENS_PATH = DATA_DIR / "tokens.json"
DEVICE_PATH = DATA_DIR / "device.json"

REQUIRED_KEYS = ("sid", "userId", "cUserId", "serviceToken", "ssecurity")
RECOMMENDED_KEYS = ("passToken", "deviceId", "ua", "locale")
SECRET_VARS = ("MIHEALTH_TOKEN_B64", "MIHEALTH_TOKEN")


def _mask(value: Any, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return "<empty>"
    if len(text) <= keep * 2:
        return "*" * len(text)
    return "%s...%s" % (text[:2], text[-keep:])


def _age_days(record: Dict[str, Any]) -> Optional[int]:
    for key in ("timestamp", "epoch"):
        value = record.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return max(0, int((time.time() - float(value)) / 86400))
    created = record.get("createdAt")
    if isinstance(created, str) and created:
        try:
            when = datetime.fromisoformat(created)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0, int((datetime.now(timezone.utc) - when).total_seconds() / 86400))
        except ValueError:
            return None
    return None


def _decode(blob: str) -> Dict[str, Any]:
    text = "".join(blob.split())
    if not text:
        raise ValueError("secret is empty")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("decoded blob is not valid UTF-8 JSON: %s" % exc) from exc
    if not isinstance(payload, dict):
        raise ValueError("decoded blob is not a JSON object")
    return payload


def _write_json(path: Path, data: Dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def _fail(message: str) -> int:
    print("恢复登录凭据失败: %s" % message, file=sys.stderr)
    print("检查清单:", file=sys.stderr)
    print("  1. 仓库 Settings → Secrets and variables → Actions 中是否配置了 MIHEALTH_TOKEN_B64", file=sys.stderr)
    print("  2. 本地是否执行过: python tools/export_token.py", file=sys.stderr)
    print("  3. Secret 内容是否为 data/token.b64 的完整单行文本（base64）", file=sys.stderr)
    print("  4. 若凭据已过期: 本地重新扫码登录后重新导出并更新 Secret", file=sys.stderr)
    return 2


def main() -> int:
    blob = ""
    used_var = ""
    for name in SECRET_VARS:
        value = os.environ.get(name) or ""
        if value.strip():
            blob = value
            used_var = name
            break
    if not blob:
        return _fail("环境变量 %s 为空或不存在" % " / ".join(SECRET_VARS))

    try:
        record = _decode(blob)
    except ValueError as exc:
        return _fail(str(exc))

    missing = [key for key in REQUIRED_KEYS if not record.get(key)]
    if missing:
        return _fail("缺少必需字段: %s" % ", ".join(missing))

    record.setdefault("locale", "zh_CN")
    _write_json(TOKENS_PATH, record)
    device: Dict[str, Any] = {}
    if record.get("deviceId"):
        device["deviceId"] = record["deviceId"]
    if record.get("ua"):
        device["ua"] = record["ua"]
    if device:
        _write_json(DEVICE_PATH, device)

    print("已从 %s 恢复登录凭据" % used_var)
    print("  sid        : %s" % record.get("sid"))
    print("  userId     : %s" % record.get("userId"))
    print("  cUserId    : %s" % _mask(record.get("cUserId")))
    print("  serviceToken: %s" % _mask(record.get("serviceToken")))
    print("  ssecurity  : %s" % _mask(record.get("ssecurity")))
    pass_token = record.get("passToken")
    print("  passToken  : %s%s" % (
        "present" if pass_token else "MISSING (will only work while serviceToken is valid)",
        "" if not pass_token else " (age %s days)" % _age_days(record),
    ))
    print("  deviceId   : %s" % _mask(record.get("deviceId")))
    print("写入文件:")
    print("  %s" % TOKENS_PATH.relative_to(ROOT))
    if device:
        print("  %s" % DEVICE_PATH.relative_to(ROOT))
    if not pass_token:
        print("警告: 没有 passToken，serviceToken 过期后无法自动续期。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
