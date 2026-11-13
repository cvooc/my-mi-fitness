# Mi Health cloud API — S800 body-composition history (evidence report)

Status 2026-11-13 10:10 CST: **transport + history endpoint confirmed**; authenticated e2e pending `work/auth/cookies.json` (not present yet). No credentials were used or attempted.
Artifacts (all `work/cloud/`): `mi_health_client.py` (v2 client, 21/21 offline self-test), `miio_client.py` (required deliverable: unified facade + classic `_sign`), `js_ref.js` (JS transcription used for byte-parity), `probe_results.json` (live probes), `refs/` (prior art + extracted bundles).
Evidence tags: **[LIVE]** own probe, **[BUNDLE]** app JS (SkyTree/RN), **[DEX]** APK dex strings, **[PRIOR]** public open source, **[LEAD]** lead's extraction.

## 1. Ranked endpoint candidates

| # | Backend / method+path | Body | Auth | Conf | Evidence |
|---|---|---|---|---|---|
| 1 | `POST https://hlth.io.mi.com/app/v1/eco/api_proxy` | `{"eco_api":"eco/scale/record/pull","params":"{\"uid\":N,\"did\":\"0\",\"pageSize\":200,\"createTime\":<ms>}"}` | serviceToken+ssecurity+cUserId (sid `miothealth`) | **high** | [LEAD] `work/lead/he-1828.js`; [BUNDLE] he-1828; [LIVE] signed request→401 `{"code":2,"message":"auth err"}` |
| 2 | `POST https://hlth.io.mi.com/app/v1/data/get_fitness_data_by_time` | `{"start_time":s,"end_time":s,"key":"weight"[,"next_key"]}` | same | **high** | [PRIOR] binglua/mi-fitness-mcp-cn verified on real accounts; [DEX] classes10.dex; [LIVE] 401 auth err |
| 3 | `POST /app/v1/eco/api_proxy` `eco_api=eco/scale/record/list` | unknown params | same | medium | [DEX] endpoint string; fallback if pull misbehaves |
| 4 | `POST /app/v1/eco/api_proxy` `eco_api=eco/common/scale/pull` | unknown params | same | medium | [DEX] endpoint string; probe last |
| 5 | `POST https://api.io.mi.com/app/user/get_user_device_data` | `{"did","key","type":"prop","time_start","time_end","limit"}` | classic cookies (userId/serviceToken) | low | [BUNDLE] plugin_rn_sdk sdk.bundle; [PRIOR] hass `async_get_user_device_data` (7-day window, limit 5, last props only) |
| — | account/region hosts | `account.xiaomi.com/pass/serviceLogin` (login), `region.hlth.io.mi.com` (200 "Welcome to mife!"), `user.huami.com/oauth2` | — | — | [LIVE] |

The S800 历史数据 page under 设备→其他设备→S800 is Mi Health's own SkyTree page (module he-1828), not the tinyapp plugin bundle (`assets/plugin/app.bundle` is only the generic tinyapp runtime `/projects/com.xiaomi.smartpanel/index.js`) ⇒ **that page reads the eco proxy, not Mijia miotspec**.

## 2. Mi Health v2 signing (byte-identical with app JS, [BUNDLE])

```
nonce_b64  = b64(8B random || BE32(floor(unix_sec/60)))            # 12 bytes
key        = SHA256(b64d(ssecurity) || b64d(nonce_b64))            # 32B RC4 key
signed_b64 = b64(key)
sign(m,path,vals) = b64(SHA1( m.upper()+"&"+path+"&" + "&".join(f"{k}={vals[k]}" for k in sorted(vals)) + "&" + signed_b64 ))
RC4        = standard KSA/crypt, first 1024 keystream bytes dropped
form       = {"data": JSON(payload)}
h1         = sign(METHOD,path,form)
b          = {**form, rc4_hash__:h1}; encrypt every string value with ONE shared RC4(key)
j          = b + {signature: sign(METHOD,path,encrypted), _nonce: nonce_b64}
POST j urlencoded, Content-Type: application/x-www-form-urlencoded; charset=UTF-8
response   = JSON.parse(RC4(key).decrypt(b64d(body)))   # body is base64 ciphertext
```
Path passed to the signer is the URL path **including** `/app/...`; no `_ssecurity` field. Verified vectors: `js_ref.js` vs Python (3 vectors, Node v26.8.1) + lead's harness (`work/lead/test_crypto.py`); `python mi_health_client.py --selftest` → 21/21 PASS. (No further crypto work needed — lead's client is independent and equal.)

## 3. Classic MiIO cloud (`api.io.mi.com`) — different variant

`_sign = b64(RC4-form)` family from `micloud`: signed path **strips `/app`**; params signed in **insertion order**; **each value** encrypted with a **fresh** RC4 instance; plaintext `ssecurity` sent as a form field; nonce = 8B random || **minimal-length** BE minutes. Same SHA256 key derivation. Implemented in `miio_client.py` (`legacy_get/legacy_post`, formula documented) and `MiioCloudClient`; used for `/home/device_list`, `/miotspec/*`, `/user/get_user_device_data` — **not** for Mi Health data. [PRIOR] `refs/micloud_dl.py`, `refs/hass_xiaomi_cloud.py`.

## 4. Auth interface (owned by auth-analyst)

sid = `miothealth`. Cookies for the data host: `serviceToken=<t>; cUserId=<c>; locale=zh_CN` (app also sends `userId=`). Login (passToken, no password): `GET https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=miothealth` with `Cookie: userId=..; passToken=..` → strip `&&&START&&&` → JSON {passToken,userId,ssecurity,location}; `GET location` → collect `Set-Cookie` → cookie header. Deliverable file contract: `{"serviceToken","ssecurity","cUserId","userId","locale","region"}`. `userId` (numeric) is the eco `uid`; `region` `cn` → bare host, else `<region>.hlth.io.mi.com` (`de,i2,ru,sg,us`).

## 5. eco proxy payload / pagination / normalization ([BUNDLE] he-1828)

`params` is a **JSON string**, not an object. Outer envelope `{code:0,result:{resp:"<json string>"}}`; `JSON.parse(result.resp)` → inner `{code:200,data:[...]}`. Pull loop: cursor `createTime=Date.now()`; repeat `{uid,did:"0",pageSize:200,createTime:cursor}`; stop on `code!==200`, empty `data`, page shorter than pageSize, or `min(createTime)>=cursor`; else cursor=min(createTime); dedupe `accountId:time:weight`; sort time asc. Account list: `eco_api:"eco/scale/account/list"`, `params:{"uid":N}`; rows in `data` (`result` fallback); mapper: accountId/birth/height/sex/type/weightTarget. `createTime` may be ms (<1e10 ⇒ ×1000) or "YYYY-MM-DD HH:MM:SS".

## 6. Field map (body composition)

eco pull rows: `accountId`, `weight`, `createTime` (+ app maps weight→weight/bmi later); account row: birth/height/sex/type/weightTarget. Fitness `key="weight"` value JSON ([DEX] classes10.dex `weight_item_record` mirrors it): `weight`, `bmi`, `body_fat_rate`, `moisture_rate`, `muscle_rate`/`muscle_mass`, `protein_rate`/`protein_mass`, `bone_mass`, `visceral_fat`, `basal_metabolism`, `body_age`; DB also has `impedance`; 8-electrode data = `measuring_impedance_1..12` (lfz12/13/14/23/24/34 + hfz12/13/14/23/24/34) and segmental `left/right_{upper,lower}_limb_muscle_mass`, `trunk_muscle_mass`, `skeletal_muscle_mass`. `hasEightElectrodeReportData` = body_fat_rate≠0 && all 12 impedances≠0. [BUNDLE] health bundle + [DEX] classes10/8.dex.

## 7. Error codes

Outer: `0` ok; `2`+HTTP401 `auth err` (missing/expired serviceToken) — [LIVE]; code 200 is the **inner eco** convention. Classic: `0`+`{"message":"auth error"}` quirk on HTTP401 [LIVE]. Login: `70016` 登录验证失败 without cookies [LIVE]. `-1/auth fail` on resource/download [LIVE].

## 8. Unauthenticated live observations (`probe_results.json`)

`api.io.mi.com/app/v2/public/get_timestamp` → 200 `{code:0,result:{timestamp}}` (server clock for nonce; also `de.` host); `hlth.io.mi.com/` 204; `/app/v1/*` 401 auth err (routes exist); `/app/v2/public/get_timestamp` 204 (no body); `region.hlth.io.mi.com` 200 "Welcome to mife!"; `cn.hlth.io.mi.com` TLS EOF (use bare host); `pv.hlthopen.io.mi.com` 403; `account.xiaomi.com` login reachable; `user.huami.com/oauth2` 200. No data endpoint is readable without auth.

## 9. Mijia-side independent history? (lead Q1)

No evidence of a dedicated Mijia cloud body-composition **history** endpoint in APK dex/JS: only generic RPCs `/miotspec/prop/get|set`, `/v2/miotspec/action` (classes3.dex + plugin SDK), `user/get_user_device_data` (plugin SDK + prior art, last-N props in a time window), `home/device_list`, `get_timestamp`. Scale body-composition fields exist only as local DB/UI names (`body_fat_rate`, `bodyFatRate`, `body_composition`, `virtual_account.bodyComposition` in classes11.dex); no `/app/scale|body|health` history path. Mijia device props (siid/piid) are device-spec driven and not hardcoded in the APK — resolve from the device spec once a device token exists. `https://api.io.mi.com/healthapp` appears in classes13.dex (legacy prefix hint, unverified).

## 10. Gaps / next tests (needs cookies)

1. `python miio_client.py --cookie-file ..\auth\cookies.json --accounts` → confirm eco account list + `uid` shape.
2. `... --pull --uid N` → verify outer/inner envelope, field names, `createTime` units, dedupe/paging termination.
3. If pull fails: `--pull --prefer fitness` (key=weight, body-comp fields) and fallback `eco/scale/record/list` / `eco/common/scale/pull` (params unknown; probe conservatively).
4. Check region host if account is non-CN (`--region`), and whether `serviceToken` for sid `miothealth` differs from `xiaomiio`.
5. Only then wire periodic fetch; no login/password logic needed if passToken can be reused or re-minted.
