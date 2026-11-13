import json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from mihealth.client import MiHealthClient

rec = json.loads(Path("data/tokens.json").read_text(encoding="utf-8"))
c = MiHealthClient(rec["serviceToken"], rec["cUserId"], rec["ssecurity"], locale=rec.get("locale") or "zh_CN", ua=rec.get("ua"))
now = int(time.time()); start = now - 365*86400
items = []; nk = None; pages = 0
while True:
    payload = {"start_time": start, "end_time": now, "key": "weight"}
    if nk:
        payload["next_key"] = nk
    r = c.post("/app/v1/data/get_fitness_data_by_time", payload)
    res = r.get("result") or {}
    dl = res.get("data_list") or []
    items.extend(dl); pages += 1
    print("page", pages, "n=", len(dl), "has_more=", res.get("has_more"), "next_key=", str(res.get("next_key"))[:40])
    if not res.get("has_more") or not res.get("next_key") or pages > 20:
        break
    nk = res.get("next_key")

tz = timezone(timedelta(hours=8))
print("TOTAL", len(items))
items.sort(key=lambda x: x.get("time") or 0)
for it in items:
    v = it.get("value")
    if isinstance(v, str):
        try: v = json.loads(v)
        except Exception: v = {}
    t = it.get("time") or 0
    print(datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d %H:%M"),
          "sid=", it.get("sid"), "weight=", v.get("weight"), "bfr=", v.get("body_fat_rate"),
          "imp1=", v.get("measuring_impedance_1"), "nkeys=", len(v))
if items:
    print("LAST_VALUE_FULL:", json.dumps(json.loads(items[-1]["value"]) if isinstance(items[-1]["value"], str) else items[-1]["value"], ensure_ascii=False)[:3000])
