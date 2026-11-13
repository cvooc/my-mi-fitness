#!/usr/bin/env python3
"""Export the local Mi Health login as one base64 blob for GitHub Actions.

The blob is meant to be stored as the repository secret ``MIHEALTH_TOKEN_B64``
and is decoded back into ``data/tokens.json`` inside the workflow by
``tools/gha_restore_token.py``.  Nothing secret is ever committed: the blob is
written to ``data/token.b64`` which is gitignored.

Usage:
    python tools/export_token.py             # validate online + write data/token.b64
    python tools/export_token.py --no-check  # offline, just encode
    python tools/export_token.py --full      # keep every token field (bigger blob)

Exit codes: 0 ok, 2 no/invalid local credentials, 3 online validation failed.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mihealth import auth as xa  # noqa: E402
from mihealth.client import HlthHttpError, MiHealthClient  # noqa: E402

DEFAULT_TOKENS = ROOT / "data" / "tokens.json"
DEFAULT_OUT = ROOT / "data" / "token.b64"
SECRET_NAME = "MIHEALTH_TOKEN_B64"
REPO = "cvooc/my-mi-fitness"

# Minimal fields the daily fetch / passToken refresh actually needs.
# Keeping the blob small also keeps the eventual leak surface small.
MINIMAL_KEYS = (
    "sid",
    "userId",
    "cUserId",
    "serviceToken",
    "ssecurity",
    "passToken",
    "deviceId",
    "ua",
    "locale",
    "createdAt",
    "timestamp",
    "epoch",
    "phone",
)


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


def _minimal_record(record: Dict[str, Any], *, full: bool) -> Dict[str, Any]:
    if full:
        return dict(record)
    return {key: record[key] for key in MINIMAL_KEYS if record.get(key) not in (None, "")}


def _online_check(record: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only probe: fetch the last week and return the page result."""
    now = int(time.time())
    client = MiHealthClient(
        service_token=record["serviceToken"],
        c_user_id=record.get("cUserId") or record.get("userId") or "",
        ssecurity=record["ssecurity"],
        locale=record.get("locale") or "zh_CN",
        ua=record.get("ua") or xa.DEFAULT_UA,
    )
    result = client.get_fitness_data_page("weight", now - 7 * 86400, now)
    rows = result.get("data_list") or []
    print("在线校验: 最近 7 天返回 %d 条记录" % len(rows))
    if rows:
        newest = max(rows, key=lambda row: int(row.get("time") or 0))
        stamp = int(newest.get("time") or 0)
        text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
        print("          最新记录时间 %s (sid=%s)" % (text, newest.get("sid")))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把本地 data/tokens.json 打包成 GitHub Actions Secret（base64）。",
    )
    parser.add_argument("--tokens", default=str(DEFAULT_TOKENS),
                        help="token JSON 路径（默认 data/tokens.json）")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="输出的 base64 文件（默认 data/token.b64）")
    parser.add_argument("--no-check", action="store_true",
                        help="跳过在线校验（离线环境使用）")
    parser.add_argument("--full", action="store_true",
                        help="保留 token 的全部字段（默认只保留必要字段）")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    token_path = Path(args.tokens)
    record = xa.load_tokens(token_path, sid=xa.DEFAULT_SID)
    if not record:
        print(
            "没有找到可用的登录凭据: %s\n"
            "请先运行:\n"
            "  python fetch_bodyfat.py login --qr\n"
            "  # 或者短信登录: python fetch_bodyfat.py login --phone <手机号>" % token_path,
            file=sys.stderr,
        )
        return 2

    if not (record.get("serviceToken") and record.get("ssecurity")):
        print("凭据缺少 serviceToken/ssecurity，请重新登录。", file=sys.stderr)
        return 2

    if not args.no_check:
        try:
            _online_check(record)
        except HlthHttpError as exc:
            print("在线校验失败（HTTP %s）: %s" % (exc.status, exc), file=sys.stderr)
            print("凭据可能已过期：请重新运行 python fetch_bodyfat.py login --qr", file=sys.stderr)
            return 3
        except Exception as exc:  # noqa: BLE001 - surface any network/auth failure
            print("在线校验失败: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            print("如需离线导出可加 --no-check", file=sys.stderr)
            return 3

    payload = _minimal_record(record, full=args.full)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = base64.b64encode(raw).decode("ascii")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(blob + "\n", encoding="ascii")

    print()
    print("sid          : %s" % payload.get("sid"))
    print("userId       : %s" % payload.get("userId"))
    print("cUserId      : %s" % _mask(payload.get("cUserId")))
    print("passToken    : %s (age %s days)" % (
        "present" if payload.get("passToken") else "MISSING",
        _age_days(record) if payload.get("passToken") else "-",
    ))
    print("字段数       : %d%s" % (len(payload), "" if not args.full else " (full)"))
    print("base64 长度  : %d" % len(blob))
    print("已写入       : %s" % out_path)
    print()
    print("下一步：把它加到 GitHub 仓库 Secret（名称 %s）：" % SECRET_NAME)
    print("  gh secret set %s --repo %s < \"%s\"" % (SECRET_NAME, REPO, out_path))
    print("  或：仓库 Settings → Secrets and variables → Actions → New repository secret")
    print()
    print("注意：data/token.b64 已被 .gitignore 忽略，请勿提交；更新后请删除该文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
