# 接口与协议（Mi Health 3.61.0 逆向）

> 抓取逻辑依赖的私有接口细节：请求/响应、签名与加密、登录流程、证据位置与边界。
> 只想使用数据 → [README](../README.md)；字段与枚举 → [data-schema.md](data-schema.md)。
> 对应 APK 构建 `3.61.0_20261104104057_mihealth.apk`（APK 不入库，需自备同版本）。

## 1. 接口结论

「S800 → 历史数据 → 全部记录」里的体脂数据来自 **健康数据查询接口**（fitness data），
一条记录就带完整八电极报告：

```
POST https://hlth.io.mi.com/app/v1/data/get_fitness_data_by_time
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
Cookie: serviceToken=<...>; cUserId=<...>; locale=zh_CN

data=<RC4密文>&rc4_hash__=<签名>&signature=<签名>&_nonce=<12字节base64>
```

解密后的明文（`data` 字段内容）：

```json
{"start_time": <unix 秒>, "end_time": <unix 秒>, "key": "weight", "next_key": "<游标，可选>"}
```

返回（同样 RC4 加密）：

```json
{"code":0,"message":"ok","result":{
  "data_list":[{"sid":"blt.3.1nbi7q1nk4k00","key":"weight","time":1794526019,
                "value":"{\"weight\":65.95,\"body_fat_rate\":16.0,...}"}],
  "has_more":true,"next_key":"eyJzaWQiOiJibHQuMy4xbmJpN3Exbms0azAwIiwi..."}}
```

- `key="weight"` 即体重+体成分；`value` 是 JSON 字符串，含完整八电极字段（逐字段说明见 [data-schema.md](data-schema.md)）。
- 分页：`has_more=true` 时把 `next_key` 原样回传，直到 `has_more=false`。
- `start_time=0` 可拉到**全部历史**（实测 2021-03 起）；`sid` 是秤的 did，
  `xiaomiwear_app_manually` 表示在 App 里手动录入的体重。
- 备选（生态秤代理，虚拟成员页走的就是它）：`POST /app/v1/eco/api_proxy`，明文 body
  `{"eco_api":"eco/scale/record/pull","params":"{\"uid\":<数字id>,\"did\":\"<did>\",\"pageSize\":200,\"createTime\":<毫秒>}"}`。
  但实测该账号下 `record/pull` / `record/list` 都是空数组，所以脚本默认用 fitness 接口，
  `--api pull` 仅作兜底保留。

## 2. 签名算法（已与 APK 里的 JS 逐字节对拍）

1. `nonce` = base64( `u32BE(rand_hi)` + `u32BE(rand_lo)` + `u32BE(floor(server_sec/60))` )，12 字节。
2. `key` = base64( SHA256( b64decode(ssecurity) ‖ b64decode(nonce) ) )，得到 44 字节的 base64 文本，作为 RC4 密钥。
3. RC4 先丢弃前 1024 字节 keystream，之后逐串加密。
4. `rc4_hash__` = base64(SHA1(`POST&<路径>&<按 key 排序的 k=v 用 & 连接>&<key>`))
   （此时参与计算的还是**明文**值）。
5. 把所有字符串值 RC4 加密成 base64，再对**密文对象**重算一次同样的 SHA1，得到 `signature`。
6. body = `data=<密文>&rc4_hash__=...&signature=...&_nonce=...`（表单 urlencoded）。
7. 响应同样用 `key` 的 RC4 解密。

> `data` 的明文是 `JSON.stringify({data: JSON.stringify(payload)})`，
> 也就是外面再包一层 `{"data": ...}`，这是 App 拦截器统一行为。
> 对拍脚本：`work/evidence/js/test_crypto.py`（读取 `js_vectors.json`，向量由 APK 里真实 JS 生成）。

## 3. 登录（小米 passport）

小米 passport（`sid=miothealth`）短信登录：`sendServiceLoginTicket`（EUI 加密手机号）
→ `phoneInfo`（验证码换 `ticketToken`）→ `serviceLoginTicketAuth`（拿 `userId`/`passToken`）
→ `serviceLogin`（带 passToken，拿 `ssecurity`/`location`）
→ `GET <location>&clientSign=...` 不跟随跳转，从 `Set-Cookie` 取 `serviceToken` 与 `cUserId`。

登录风控会要求图形验证码（`87001`）：脚本会自动下载验证码图片到 `mihealth/captcha.png`
并提示输入；大小写敏感。

`passToken` 有效期较长，之后可用 `python fetch_bodyfat.py refresh` 免短信续期。

不想等短信/图形验证码时可以用**扫码登录**（推荐）：

```
python fetch_bodyfat.py login --qr
```

脚本会请求 `longPolling/loginUrl`，把返回的 `qr` 字段（它本身就是一张 260×260 的
二维码 PNG 图片）保存到 `data/qr.png` 并等待你扫码；请用「米家」或「小米运动健康」
App 扫一扫，微信/系统相机会把二维码当成一个图片链接打开。扫码成功后自动换
`passToken` → `ssecurity` → `serviceToken` 并写入 `data/tokens.json`。
单独调试可用：`python -m mihealth.auth qr`（该入口默认存到 `mihealth/qr.png`）。

## 4. 逆向证据索引

| 位置 | 内容 |
| --- | --- |
| `classes10.dex` / `classes11.dex` | fitness 数据接口 `app/v1/data/get_fitness_data_by_time` 与 `get_latest_fitness_data` 路径 |
| `work/evidence/reports/cloud-REPORT.md` | 同款请求体（`key="weight"` + `next_key` 分页）与生态秤探针结果，**已用真实账号验证成功** |
| `work/evidence/js/he-1828.js` | health SkyTree bundle 模块：`getVirtualMemberRecords()` / `normalizePullRecord()`，eco 兜底路径的分页与字段映射来源 |
| 同 bundle 模块 `he-1827` | 「其他设备」入口：`getVirtualAccountList(LoginUser.userId)` → `type===2` → 跳 `VirtualMemberData` 页 |
| `assets/plugin_rn_sdk/sdk.bundle` | 生态秤插件 SDK：`requestApi(api, params)` → `MiWearHost.requestUrl("eco/api_proxy", {eco_api, params})`，证明远端 RN 页也走同一代理 |
| `classes10.dex` `com.xiaomi.fitness.account.SIDs` | `MIOT_HEALTH = "miothealth"`（登录 passport 的 sid，**不是**网上常见的 `mifitness`） |
| `work/evidence/js/common_mod_12{43,45,47,48,49}.js` | 请求签名（RC4 + 1024 字节丢弃 + SHA1/SHA256）、querystring 序列化、Host 选择、Cookie/ssecurity；nonce 结构由 `js_vectors.json` 自测覆盖 |

## 5. 边界与已知限制

- 该接口是 **App 内部私有接口**，小米可能随时改签名/路径；本文档对应 3.61.0（2026-11-04 构建）。
- 已实测：`get_fitness_data_by_time` 能拉到 2021-03 至今全部记录（含八电极完整体成分）；
  `eco/scale/record/pull`、`eco/scale/record/list` 在当前账号下返回空数组（可能只有虚拟成员/家人数据才走它）。
- 脚本只读数据，不修改云端记录；请求间隔受分页控制，正常使用不会触发风控。
- `data/tokens.json` 等价于登录态，请勿提交到公开仓库（已在 `.gitignore` 中排除）。
- **`data/bodyfat.json` 会随 workflow 提交到公开仓库**（这正是直链能用的原因）。
  如果你不想公开体重/体脂数据，请把仓库设为私有，或删掉 `.github/workflows/update-bodyfat.yml`。
- `work/evidence/` 保留少量关键逆向证据，便于日后升级 APK 时快速定位差异；
  完整解包结果不入库，需要时用同版本 APK 重新解包（`*.apk` 已在 `.gitignore` 中排除，详情见 [`work/README.md`](../work/README.md)）。
