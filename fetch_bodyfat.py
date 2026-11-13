#!/usr/bin/env python3
"""Fetch body-composition records (体脂数据) from Xiaomi Mi Health.

Reverse engineered from Mi Health 3.61.0 (2026-11-04).

The data the app shows under 「其他设备 → 米家八电极体脂秤 S800 → 历史数据 →
全部记录」 is served by:

    POST https://hlth.io.mi.com/app/v1/data/get_fitness_data_by_time
    {"start_time": <unix s>, "end_time": <unix s>, "key": "weight",
     "next_key": <cursor, optional>}

Every row is ``{sid: <scale did>, key: "weight", time: <unix s>,
value: "<JSON string>"}`` and ``value`` holds the complete body composition
(body_fat_rate, muscle/fat masses, segmental values, BMR, visceral fat,
body age/score, whr, ...).

The request is signed with the Mi Health RC4/SHA1 scheme (same as the app);
login is the Xiaomi passport SMS flow. Credentials are stored in
``data/tokens.json`` and can be renewed without an SMS via the saved passToken.

Quick start (one command — logs in on first run, then only fetches):

    python fetch_bodyfat.py

Usage:
    python fetch_bodyfat.py                 # 登录(如需要) + 增量抓取 + 显示最新
    python fetch_bodyfat.py --full          # 全量抓取（首次建议用一次）
    python fetch_bodyfat.py login --phone 13800000000
    python fetch_bodyfat.py login --qr    # 扫码登录（推荐，无需短信）
    python fetch_bodyfat.py latest -n 5
    python fetch_bodyfat.py export          # 导出 data/bodyfat.json
    python fetch_bodyfat.py refresh         # 用 passToken 免短信续期

Data files (all under ./data):
    tokens.json    登录凭据（含 passToken，注意保密）
    device.json    设备标识（请求签名要用）
    bodyfat.json   全部体脂记录（JSON：meta + records + latest）
    state.json     增量抓取游标
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mihealth import auth as xa  # noqa: E402
from mihealth.client import HlthHttpError, MiHealthClient  # noqa: E402

DATA_DIR = ROOT / "data"
TOKENS_PATH = DATA_DIR / "tokens.json"
DEVICE_PATH = DATA_DIR / "device.json"
RECORDS_PATH = DATA_DIR / "bodyfat.json"
LEGACY_JSONL = DATA_DIR / "bodyfat.jsonl"
STATE_PATH = DATA_DIR / "state.json"

# how far back to re-scan on an incremental run (records can be edited/added late)
OVERLAP_MS = 12 * 3600 * 1000


# --------------------------------------------------------------------------- #
# small io helpers
# --------------------------------------------------------------------------- #
def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _record_key(rec: Dict[str, Any]) -> str:
    ident = rec.get("accountId")
    if ident in (None, "", 0):
        ident = rec.get("sid")
    return "%s|%s|%s" % (ident, rec.get("time"), rec.get("weight"))


def _canonical_records(records: Iterable[Dict[str, Any]]) -> str:
    """Stable fingerprint of the record set, ignoring snapshot metadata."""
    rows = sorted(records, key=lambda r: (r.get("time") or 0, _record_key(r)))
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fmt_time_text(ms: Any) -> Optional[str]:
    try:
        ts = int(ms)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts / 1000.0))


def _load_records() -> Dict[str, Dict[str, Any]]:
    """Load all records from data/bodyfat.json (migrating the old .jsonl once)."""
    out: Dict[str, Dict[str, Any]] = {}
    if RECORDS_PATH.exists():
        try:
            data = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        rows = data.get("records") if isinstance(data, dict) else data
        for rec in rows or []:
            if isinstance(rec, dict):
                out[_record_key(rec)] = rec
        return out
    if LEGACY_JSONL.exists():
        with LEGACY_JSONL.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out[_record_key(rec)] = rec
        if out:
            _write_records(out)
            LEGACY_JSONL.unlink(missing_ok=True)
            print("已把旧的 data/bodyfat.jsonl 迁移到 data/bodyfat.json（%d 条）" % len(out))
    return out


def _write_records(records: Dict[str, Dict[str, Any]]) -> None:
    _ensure_dirs()
    ordered = sorted(records.values(), key=lambda r: r.get("time") or 0)
    payload: Dict[str, Any] = {
        "schema": "mi-fits/bodyfat@1",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at_ts": int(time.time()),
        "count": len(ordered),
        "latest": ordered[-1] if ordered else None,
        "records": ordered,
    }
    tmp = RECORDS_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(RECORDS_PATH)


# --------------------------------------------------------------------------- #
# auth / client
# --------------------------------------------------------------------------- #
def _token_record(*, refresh: bool = False) -> Dict[str, Any]:
    rec = xa.load_tokens(TOKENS_PATH, sid=xa.DEFAULT_SID)
    if rec and refresh and rec.get("passToken") and rec.get("userId"):
        rec = xa.login_with_passtoken(
            rec["userId"], rec["passToken"], xa.DEFAULT_SID,
            token_path=TOKENS_PATH, device_path=DEVICE_PATH,
        )
    return rec or {}


def _ensure_token(args: argparse.Namespace) -> Dict[str, Any]:
    """Return valid credentials, running the interactive SMS login if needed."""
    rec = _token_record()
    if rec.get("serviceToken") and rec.get("ssecurity"):
        return rec
    phone = getattr(args, "phone", None)
    if not phone:
        if not sys.stdin.isatty():
            raise SystemExit(
                "还没有登录凭据。请运行:\n"
                "  python fetch_bodyfat.py login --qr          (扫码，推荐)\n"
                "  python fetch_bodyfat.py login --phone <你的小米账号手机号>"
            )
        print("还没有登录凭据，先登录小米账号（首次需要短信验证码）。")
        phone = input("小米账号手机号: ").strip()
    print("登录 %s ..." % phone)
    rec = xa.login_with_sms(
        phone, None, xa.DEFAULT_SID,
        token_path=TOKENS_PATH, device_path=DEVICE_PATH,
    )
    print("登录成功，凭据已保存到 %s" % TOKENS_PATH)
    return rec


def _make_client(rec: Dict[str, Any], verbose: bool = False) -> MiHealthClient:
    return MiHealthClient(
        service_token=rec["serviceToken"],
        c_user_id=rec.get("cUserId") or rec.get("userId") or "",
        ssecurity=rec["ssecurity"],
        locale=rec.get("locale") or "zh_CN",
        ua=rec.get("ua") or xa.DEFAULT_UA,
        verbose=verbose,
    )


def _numeric_uid(rec: Dict[str, Any]) -> int:
    for key in ("userId", "cUserId"):
        raw = str(rec.get(key) or "")
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 5:
            return int(digits)
    raise SystemExit("无法从 token 中解析出数字 uid（userId=%r）" % rec.get("userId"))


def _fetch_with_retry(rec: Dict[str, Any], fn, verbose: bool = False):
    """Run fn(client); on 401/403 renew the token once and retry."""
    client = _make_client(rec, verbose=verbose)
    try:
        return fn(client)
    except HlthHttpError as exc:
        if exc.status not in (401, 403):
            raise
        if verbose:
            print("[fetch] token rejected (%s), refreshing via passToken..." % exc.status, file=sys.stderr)
        renewed = _token_record(refresh=True)
        if not (renewed.get("serviceToken") and renewed.get("ssecurity")):
            raise SystemExit("凭据已失效且无法自动续期，请重新登录: python fetch_bodyfat.py login")
        client = _make_client(renewed, verbose=verbose)
        return fn(client)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_login(args: argparse.Namespace) -> int:
    _ensure_dirs()
    if getattr(args, "qr", False):
        rec = xa.login_with_qr(
            xa.DEFAULT_SID,
            token_path=TOKENS_PATH,
            device_path=DEVICE_PATH,
            qr_path=Path(args.qr_out) if getattr(args, "qr_out", None) else (DATA_DIR / "qr.png"),
        )
    else:
        phone = args.phone or input("小米账号手机号: ").strip()
        if args.code:
            rec = xa.verify_sms_code(
                phone, args.code, xa.DEFAULT_SID,
                token_path=TOKENS_PATH, device_path=DEVICE_PATH,
            )
        else:
            rec = xa.login_with_sms(
                phone, None, xa.DEFAULT_SID,
                token_path=TOKENS_PATH, device_path=DEVICE_PATH,
            )
    print("登录成功，凭据已保存到 %s" % TOKENS_PATH)
    print("  userId  : %s" % rec.get("userId"))
    print("  cUserId : %s" % rec.get("cUserId"))
    print("  sid     : %s" % rec.get("sid"))
    return 0


def cmd_refresh(_args: argparse.Namespace) -> int:
    rec = _token_record(refresh=True)
    if not (rec.get("serviceToken") and rec.get("ssecurity")):
        raise SystemExit("没有可续期的凭据，请先登录: python fetch_bodyfat.py login")
    print("已用 passToken 续期，userId=%s" % rec.get("userId"))
    return 0


def _fetch_records(rec: Dict[str, Any], since_ms: Optional[int],
                   args: argparse.Namespace) -> List[Dict[str, Any]]:
    uid = _numeric_uid(rec)
    api = getattr(args, "api", "fitness")
    if api == "fitness":
        end_s = int(time.time())
        start_s = max(0, int(int(since_ms) / 1000)) if since_ms else 0
        rows = _fetch_with_retry(
            rec,
            lambda c: c.get_weight_records(start_time=start_s, end_time=end_s),
            getattr(args, "verbose", False),
        )
        for row in rows:
            if not row.get("time_text"):
                row["time_text"] = _fmt_time_text(row.get("time"))
        return rows

    accounts = _fetch_with_retry(
        rec, lambda c: c.get_virtual_account_list(uid), getattr(args, "verbose", False)
    )
    labels: Dict[int, Any] = {
        int(acc["accountId"]): acc.get("name")
        for acc in accounts
        if acc.get("accountId") is not None
    }
    if api == "list":
        ids: List[int] = []
        if args.account_id is not None:
            ids = [int(args.account_id)]
        else:
            ids = [int(a["accountId"]) for a in accounts if a.get("accountId") is not None]
            if not ids:
                ids = [0]
        rows = []
        for account_id in ids:
            rows.extend(_fetch_with_retry(
                rec,
                lambda c, aid=account_id: c.list_records(aid, page_size=args.page_size),
                getattr(args, "verbose", False),
            ))
    else:
        eco_api = "eco/common/scale/pull" if api == "common" else "eco/scale/record/pull"
        rows = _fetch_with_retry(
            rec,
            lambda c: c.get_records(
                uid,
                account_id=args.account_id,
                page_size=args.page_size,
                since_ms=since_ms,
                eco_api=eco_api,
                model=None if api != "common" else (args.model or ""),
            ),
            getattr(args, "verbose", False),
        )
    for row in rows:
        aid = row.get("accountId")
        if aid is not None and labels.get(int(aid)):
            row["account_name"] = labels[int(aid)]
    return rows


def _since_from_args(args: argparse.Namespace) -> Optional[int]:
    if getattr(args, "rebuild", False):
        return None
    if getattr(args, "full", False):
        return None
    if getattr(args, "days", None):
        return int(time.time() * 1000) - int(args.days) * 86400 * 1000
    if getattr(args, "since", None):
        return int(args.since)
    last = _load_state().get("last_time")
    if isinstance(last, (int, float)) and last > 0:
        return int(last) - OVERLAP_MS
    return None


def cmd_sync(args: argparse.Namespace) -> int:
    """Login if needed, fetch new records, merge and print the latest."""
    _ensure_dirs()
    rec = _ensure_token(args)
    uid = _numeric_uid(rec)
    fetched = _fetch_records(rec, _since_from_args(args), args)
    if getattr(args, "rebuild", False) and not fetched:
        raise SystemExit(
            "云端返回 0 条记录，拒绝用空快照覆盖 %s" % RECORDS_PATH
        )

    if args.raw_out:
        Path(args.raw_out).write_text(
            json.dumps(fetched, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("原始记录 -> %s (%d 条)" % (args.raw_out, len(fetched)))

    previous = _load_records()
    stored = dict(previous)
    if getattr(args, "rebuild", False):
        print("重建本地快照：以本次全量 %d 条为准（会去掉云端已删除的记录）" % len(fetched))
        stored = {}
    added = 0
    for item in fetched:
        key = _record_key(item)
        old = stored.get(key)
        if old is None:
            stored[key] = item
            added += 1
        elif old != item:
            stored[key] = item
    changed = _canonical_records(stored.values()) != _canonical_records(previous.values())
    wrote = False
    if getattr(args, "if_changed", False) and not changed:
        print("无变化（不重写快照）")
    else:
        _write_records(stored)
        wrote = True

    times = [r.get("time") for r in stored.values() if isinstance(r.get("time"), int)]
    state = _load_state()
    state.update({
        "uid": uid,
        "last_time": max(times) if times else None,
        "last_fetch_at": int(time.time()),
        "last_fetch_count": len(fetched),
        "total_records": len(stored),
    })
    _save_state(state)

    print("uid=%d 本次拉取=%d 条，%s本地共 %d 条%s"
          % (uid, len(fetched),
             "" if getattr(args, "rebuild", False) else "新增=%d 条，" % added,
             len(stored),
             "（快照已更新）" if wrote else "（无变化）"))
    _print_latest(sorted(stored.values(), key=lambda r: r.get("time") or 0), n=args.show)
    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    """List eco scale accounts (main user + virtual members)."""
    rec = _ensure_token(args)
    uid = _numeric_uid(rec)
    accounts = _fetch_with_retry(
        rec, lambda c: c.get_virtual_account_list(uid), getattr(args, "verbose", False)
    )
    print("uid=%d, eco 秤账户数=%d" % (uid, len(accounts)))
    for acc in accounts:
        print("  " + json.dumps(acc, ensure_ascii=False))
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    """Debug: call an arbitrary eco_api through the proxy and dump the reply."""
    rec = _ensure_token(args)
    uid = _numeric_uid(rec)
    if args.params_gen == "uid":
        params: Dict[str, Any] = {"uid": uid}
    else:
        params = json.loads(args.params or "{}")
    result = _fetch_with_retry(rec, lambda c: c.eco_api(args.eco_api, params),
                               getattr(args, "verbose", False))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    stored = _load_records()
    if not stored:
        print("本地还没有记录，先运行: python fetch_bodyfat.py")
        return 2
    _print_latest(sorted(stored.values(), key=lambda r: r.get("time") or 0), n=args.n)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    stored = _load_records()
    if not stored:
        print("本地还没有记录，先运行: python fetch_bodyfat.py")
        return 2
    ordered = sorted(stored.values(), key=lambda r: r.get("time") or 0)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.array:
        payload: Any = ordered
    else:
        payload = {
            "schema": "mi-fits/bodyfat@1",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at_ts": int(time.time()),
            "count": len(ordered),
            "latest": ordered[-1] if ordered else None,
            "records": ordered,
        }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("导出 %d 条 JSON -> %s" % (len(ordered), out))
    return 0


# --------------------------------------------------------------------------- #
# pretty printing
# --------------------------------------------------------------------------- #
_CORE_FIELDS = [
    ("time_text", "时间"),
    ("weight", "体重kg"),
    ("bmi", "BMI"),
    ("body_fat_rate", "体脂率%"),
    ("body_score", "身体得分"),
    ("body_age", "身体年龄"),
    ("visceral_fat", "内脏脂肪"),
    ("muscle_mass", "肌肉量kg"),
    ("muscle_rate", "肌肉率%"),
    ("basal_metabolism", "基础代谢kcal"),
    ("moisture_rate", "水分率%"),
    ("bone_mass", "骨量kg"),
    ("protein_mass", "蛋白质量kg"),
    ("fat_mass", "脂肪量kg"),
    ("bpm", "心率"),
]


def _print_latest(rows: List[Dict[str, Any]], n: int = 3) -> None:
    if not rows:
        return
    latest = rows[-n:]
    print("\n最近 %d 条记录：" % len(latest))
    for rec in reversed(latest):
        if not rec.get("time_text"):
            rec = dict(rec)
            rec["time_text"] = _fmt_time_text(rec.get("time"))
        parts = []
        for key, label in _CORE_FIELDS:
            value = rec.get(key)
            if value is None:
                continue
            if isinstance(value, float):
                value = round(value, 2)
            parts.append("%s=%s" % (label, value))
        name = rec.get("account_name")
        prefix = ("[%s] " % name) if name else ""
        print("  " + prefix + "  ".join(parts))


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
FETCH_OPTIONS = [
    (("--full",), {"action": "store_true", "help": "全量拉取（默认增量）"}),
    (("--rebuild",), {"action": "store_true",
                     "help": "重建本地快照（隐含 --full，删掉云端已删除的记录）"}),
    (("--days",), {"type": int, "default": None, "help": "只拉最近 N 天"}),
    (("--since",), {"type": int, "default": None, "help": "起始时间戳（毫秒）"}),
    (("--account-id",), {"type": int, "default": None, "help": "只保留某个 accountId"}),
    (("--page-size",), {"type": int, "default": 200}),
    (("--api",), {"default": "fitness",
                  "choices": ["fitness", "pull", "list", "common"],
                  "help": "fitness=get_fitness_data_by_time（默认，含完整体脂报告）"}),
    (("--model",), {"default": None, "help": "--api common 时的 model 参数"}),
    (("--if-changed",), {"action": "store_true",
                         "help": "内容无变化时不重写 JSON（供 GitHub Actions 使用）"}),
    (("--raw-out",), {"default": None, "help": "把本次原始记录另存为 JSON"}),
    (("--show",), {"type": int, "default": 3, "help": "结束时显示最近几条"}),
]


def _add_fetch_options(parser: argparse.ArgumentParser) -> None:
    for flags, kwargs in FETCH_OPTIONS:
        parser.add_argument(*flags, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="抓取小米运动健康 App 的体脂秤历史数据"
                    "（get_fitness_data_by_time）。不带子命令 = 登录 + 增量抓取。",
        epilog="示例: python fetch_bodyfat.py   |   python fetch_bodyfat.py --full",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="打印调试信息")
    # allow `python fetch_bodyfat.py --full` without naming the sync subcommand
    _add_fetch_options(ap)
    sub = ap.add_subparsers(dest="cmd")

    p_sync = sub.add_parser("sync", help="登录(如需要) + 增量抓取 + 显示最新")
    _add_fetch_options(p_sync)
    p_sync.set_defaults(func=cmd_sync)

    p_fetch = sub.add_parser("fetch", help="同 sync（兼容旧命令）")
    _add_fetch_options(p_fetch)
    p_fetch.set_defaults(func=cmd_sync)

    p_login = sub.add_parser("login", help="小米账号登录（短信或扫码），保存凭据")
    p_login.add_argument("--phone", default=None)
    p_login.add_argument("--code", default=None, help="短信验证码（省略则交互输入）")
    p_login.add_argument("--qr", action="store_true",
                         help="扫码登录（下载官方二维码图片，用米家/小米运动健康 App 扫）")
    p_login.add_argument("--qr-out", default=None,
                         help="二维码图片保存路径（默认 data/qr.png）")
    p_login.set_defaults(func=cmd_login)

    p_refresh = sub.add_parser("refresh", help="用保存的 passToken 免短信续期")
    p_refresh.set_defaults(func=cmd_refresh)

    p_acc = sub.add_parser("accounts", help="列出 eco 秤账户（含虚拟成员）")
    p_acc.add_argument("--phone", default=None, help="缺凭据时可顺带登录")
    p_acc.set_defaults(func=cmd_accounts)

    p_raw = sub.add_parser("raw", help="调试：调用任意 eco_api 并打印原始响应")
    p_raw.add_argument("eco_api")
    p_raw.add_argument("--params", default="{}")
    p_raw.add_argument("--params-gen", default=None, choices=[None, "uid"])
    p_raw.add_argument("--phone", default=None)
    p_raw.set_defaults(func=cmd_raw)

    p_latest = sub.add_parser("latest", help="显示本地最近记录")
    p_latest.add_argument("-n", type=int, default=5)
    p_latest.set_defaults(func=cmd_latest)

    p_export = sub.add_parser("export", help="导出 JSON（默认 data/bodyfat.json）")
    p_export.add_argument("--out", default=str(RECORDS_PATH))
    p_export.add_argument("--array", action="store_true",
                          help="只输出记录数组（不含 meta/latest）")
    p_export.set_defaults(func=cmd_export)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        args.func = cmd_sync
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except EOFError:
        print(
            "需要交互输入手机号/验证码，但当前不是交互式终端。\n"
            "请改用: python fetch_bodyfat.py login --phone <手机号>",
            file=sys.stderr,
        )
        return 2
    except xa.AuthError as exc:
        print("登录/鉴权失败: %s" % exc, file=sys.stderr)
        return 2
    except HlthHttpError as exc:
        print("请求失败: %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
