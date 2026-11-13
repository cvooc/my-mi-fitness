# mi-fits · 小米运动健康体脂数据抓取

从 **小米运动健康（Mi Health）3.61.0** APK 逆向出的体脂秤历史数据接口，附带一个可定期运行的抓取脚本。

日常就一条命令（首次会自动走登录，之后只抓增量）：

```
python fetch_bodyfat.py
```

只依赖 `requests`（登录额外用到 `cryptography`）。

---

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

- `key="weight"` 即体重+体成分；`value` 是 JSON 字符串，含 ~56 个字段（见 §5）。
- 分页：`has_more=true` 时把 `next_key` 原样回传，直到 `has_more=false`。
- `start_time=0` 可拉到**全部历史**（实测 2021-03 起）；`sid` 是秤的 did，
  `xiaomiwear_app_manually` 表示在 App 里手动录入的体重。
- 备选（生态秤代理，虚拟成员页走的就是它）：`POST /app/v1/eco/api_proxy`，明文 body
  `{"eco_api":"eco/scale/record/pull","params":"{\"uid\":<数字id>,\"did\":\"<did>\",\"pageSize\":200,\"createTime\":<毫秒>}"}`。
  但实测该账号下 `record/pull` / `record/list` 都是空数组，所以脚本默认用 fitness 接口，
  `--api pull` 仅作兜底保留。

### 关键证据（均来自 APK 内实际代码）

| 位置 | 内容 |
| --- | --- |
| `classes10.dex` / `classes11.dex` | fitness 数据接口 `app/v1/data/get_fitness_data_by_time` 与 `get_latest_fitness_data` 路径 |
| `work/evidence/reports/cloud-REPORT.md` | 同款请求体（`key="weight"` + `next_key` 分页）与生态秤探针结果，**已用真实账号验证成功** |
| `work/evidence/js/he-1828.js` | health SkyTree bundle 模块：`getVirtualMemberRecords()` / `normalizePullRecord()`，eco 兜底路径的分页与字段映射来源 |
| 同 bundle 模块 `he-1827` | 「其他设备」入口：`getVirtualAccountList(LoginUser.userId)` → `type===2` → 跳 `VirtualMemberData` 页 |
| `assets/plugin_rn_sdk/sdk.bundle` | 生态秤插件 SDK：`requestApi(api, params)` → `MiWearHost.requestUrl("eco/api_proxy", {eco_api, params})`，证明远端 RN 页也走同一代理 |
| `classes10.dex` `com.xiaomi.fitness.account.SIDs` | `MIOT_HEALTH = "miothealth"`（登录 passport 的 sid，**不是**网上常见的 `mifitness`） |
| `work/evidence/js/common_mod_1243/1247/1248/1250.js` | 请求签名（RC4 + 1024 字节丢弃 + SHA1/SHA256）、Host 选择、12 字节 nonce |

### 签名算法（已与 APK 里的 JS 逐字节对拍）

1. `nonce` = base64( `u32BE(rand_hi)` + `u32BE(rand_lo)` + `u32BE(floor(server_sec/60))` )，12 字节。
2. `key` = base64( SHA256( b64decode(ssecurity) ‖ b64decode(nonce) ) )，32 字节，作为 RC4 密钥。
3. RC4 先丢弃前 1024 字节 keystream，之后逐串加密。
4. `rc4_hash__` = base64(SHA1(`POST&<路径>&<按 key 排序的 k=v 用 & 连接>&<key>`))
   （此时参与计算的还是**明文**值）。
5. 把所有字符串值 RC4 加密成 base64，再对**密文对象**重算一次同样的 SHA1，得到 `signature`。
6. body = `data=<密文>&rc4_hash__=...&signature=...&_nonce=...`（表单 urlencoded）。
7. 响应同样用 `key` 的 RC4 解密。

> `data` 的明文是 `JSON.stringify({data: JSON.stringify(payload)})`，
> 也就是外面再包一层 `{"data": ...}`，这是 App 拦截器统一行为。
> 对拍脚本：`work/evidence/js/test_crypto.py`（读取 `js_vectors.json`，向量由 APK 里真实 JS 生成）。

### 登录

小米 passport（`sid=miothealth`）短信登录：`sendServiceLoginTicket`（EUI 加密手机号）
→ `phoneInfo`（验证码换 `ticketToken`）→ `serviceLoginTicketAuth`（拿 `userId`/`passToken`）
→ `serviceLogin`（带 passToken，拿 `ssecurity`/`location`）
→ `GET <location>&clientSign=...` 不跟随跳转，从 `Set-Cookie` 取 `serviceToken` 与 `cUserId`。

登录风控会要求图形验证码（`87001`）：脚本会自动下载验证码图片到 `data/captcha.png`
并提示输入；大小写敏感。

`passToken` 有效期较长，之后可用 `python fetch_bodyfat.py refresh` 免短信续期。

---

## 2. 目录结构

```
mihealth/
  auth.py          # 小米 passport 短信登录（含图形验证码）、token 持久化、passToken 续期
  client.py        # RC4 签名客户端 + fitness/eco 接口封装 + 记录字段归一化
fetch_bodyfat.py   # 命令行入口（默认 = 登录 + 增量抓取）
data/
  tokens.json      # 登录凭据（含 serviceToken/ssecurity/passToken，注意保密）
  device.json      # 设备标识（请求签名要用）
  bodyfat.json     # 全部体脂记录（JSON：meta + latest + records 数组）
  state.json       # 增量游标、上次抓取时间
  captcha.png      # 仅在登录需要图形验证码时生成
work/
  evidence/        # 逆向证据（关键 JS 模块、签名对拍向量、各模块分析报告）
  README.md        # 证据目录说明
```

## 3. 使用

### 3.1 一条命令（推荐）

```
python fetch_bodyfat.py
```

- 没有凭据时会自动进入登录（手机号 + 短信验证码，必要时再输图形验证码），
  凭据写入 `data/tokens.json`；
- 有凭据时直接做增量抓取，合并进 `data/bodyfat.json`，并打印最近几条体脂数据。

### 3.2 手动登录（可指定手机号，便于脚本/远程使用）

```
python fetch_bodyfat.py login --phone 138xxxxxxxx
# 需要图形验证码时：图片已保存到 data/captcha.png → 输入图中字符
# 请输入短信验证码: ______
```

若账号开了二次验证，按提示打开链接确认后执行 `refresh`。
也支持 `python fetch_bodyfat.py login --phone 138xxxxxxxx --code 123456` 直接提交短信码。

### 3.3 抓取选项

```
python fetch_bodyfat.py                    # 增量（默认：从 last_time 回溯 12 小时）
python fetch_bodyfat.py --full             # 全量重拉（全部历史，实测 2021 年起）
python fetch_bodyfat.py --rebuild          # 重建本地快照：以云端为准，去掉你在 App 里删掉的记录
python fetch_bodyfat.py --days 30          # 最近 30 天
python fetch_bodyfat.py --since 1735660800000   # 指定起始时间（毫秒）
python fetch_bodyfat.py sync --show 5      # 显式 sync，结束时显示最近 5 条
```

### 3.4 查看 / 导出

```
python fetch_bodyfat.py latest -n 10
python fetch_bodyfat.py export                         # 重写 data/bodyfat.json
python fetch_bodyfat.py export --out D:\bak\bf.json    # 另存一份
python fetch_bodyfat.py export --array                 # 只输出 records 数组
```

`data/bodyfat.json` 结构：

```json
{
  "schema": "mi-fits/bodyfat@1",
  "updated_at": "2026-11-13 12:40:00",
  "updated_at_ts": 1794548400,
  "count": 315,
  "latest": { "...最新一条完整记录..." },
  "records": [ { "time": 1794526019000, "time_text": "2026-11-13 07:26",
                 "weight": 65.95, "body_fat_rate": 16.0, "...": "..." } ]
}
```

> 与旧的手工 JSON（`data/bfp-data.json`）的字段/数值对比报告见 `data/compare-report.md`（含个人数据，仅本地生成，不入库）。

### 3.5 定时运行

Windows（计划任务，每 6 小时）：

```
schtasks /Create /TN "MiHealthBodyFat" /SC HOURLY /MO 6 ^
  /TR "C:\Path\to\python.exe D:\workspace\github\mi-fits\fetch_bodyfat.py" /F
```

Linux/macOS（crontab）：

```
0 */6 * * * cd /path/to/mi-fits && /usr/bin/python3 fetch_bodyfat.py >> data/fetch.log 2>&1
```

token 过期时脚本会用 `passToken` 自动续期；`passToken` 也失效时重新跑一次登录即可。

### 3.6 调试其它接口（可选）

```
# 查看 eco 秤账户（主账号 + 虚拟成员）
python fetch_bodyfat.py accounts

# 兜底数据接口（fitness 接口万一不可用）
python fetch_bodyfat.py --api list   --account-id 123456
python fetch_bodyfat.py --api common --model xiaomi.scales.ms116

# 任意 eco_api 原始调用
python fetch_bodyfat.py raw eco/scale/account/list --params-gen uid
python fetch_bodyfat.py raw eco/scale/record/pull --params '{"uid":123,"did":"<did>","pageSize":5,"createTime":0}'
```

设备身份可直接从 fitness 记录的 `sid` 得到：S800 = `blt.3...` / `xiaomi.scales.ms116`。
原生「其他设备」列表接口是 `POST /app/v1/source/get_third_device_source_list`
（`ThirdPartyDeviceApiService`，body `page_size/page_start/status`），由
`EcoDeviceManagerComponent` 在 onResume（有 diffTime 节流）、登录成功、地区变更时刷新
——也就是「其他设备」选项时有时无的根因。

## 4. 数据字段（归一化后的常用键）

| 键 | 含义 | 键 | 含义 |
| --- | --- | --- | --- |
| `time` / `time_text` | 测量时间（毫秒/本地时间串） | `body_fat_rate` | 体脂率 % |
| `weight` | 体重 kg | `bmi` | BMI |
| `body_score` | 身体得分 | `body_age` | 身体年龄 |
| `muscle_mass` / `muscle_rate` | 肌肉量 / 肌肉率 | `fat_mass` | 脂肪量 |
| `basal_metabolism` | 基础代谢 kcal | `visceral_fat` | 内脏脂肪等级 |
| `moisture_rate` / `body_moisture_mass` | 水分率 / 水分量 | `bone_mass` / `bone_rate` | 骨量 |
| `protein_mass` / `protein_rate` | 蛋白质量 / 率 | `bpm` | 心率 |
| `measuring_impedance_1..12` | 12 路生物电阻抗（eco 记录里有，fitness 记录通常省略） | `whr` | 腰臀比 |
| `left_upper_limb_muscle_mass` 等 | 各部位肌肉/脂肪量 | `somatotype` / `body_shape` | 体态/体型 |
| `sid` / `time_sec` / `source` | 设备 did / 秒级时间戳 / 数据来源 | `has_body_composition` | 是否含完整体成分（体脂率 > 0） |

`accountId` 为秤上的用户账户（虚拟成员在此区分），只在 `--api pull/list` 的记录里出现；
fitness 记录用 `sid`（设备）区分，`account_name` 来自 `eco/scale/account/list`。

## 5. 说明与边界

- 该接口是 **App 内部私有接口**，小米可能随时改签名/路径；本文档对应 3.61.0（2026-11-04 构建）。
- 已实测：`get_fitness_data_by_time` 能拉到 2021-03 至今全部记录（含八电极完整体成分）；
  `eco/scale/record/pull`、`eco/scale/record/list` 在当前账号下返回空数组（可能只有虚拟成员/家人数据才走它）。
- 脚本只读数据，不修改云端记录；请求间隔受分页控制，正常使用不会触发风控。
- `data/tokens.json` 等价于登录态，请勿提交到公开仓库（已在 `.gitignore` 中排除）。
- `work/evidence/` 保留少量关键逆向证据，便于日后升级 APK 时快速定位差异；
  完整解包结果可由仓库根目录的 APK 重新生成，不再保留在仓库里（详情见 `work/README.md`）。
