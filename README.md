# mi-fits · 小米运动健康体脂数据抓取

从 **小米运动健康（Mi Health）3.61.0** APK 逆向出的体脂秤历史数据接口，附带一个可定期运行的抓取脚本。

日常就一条命令（首次会自动走登录，之后只抓增量）：

```
python fetch_bodyfat.py
```

只依赖 `requests`（登录额外用到 `cryptography`）。

> 不想自己跑？仓库已带 **GitHub Actions**：每天北京时间 08:10 自动抓取并提交全量
> `data/bodyfat.json`，其他项目可直接用直链读取（见 §6）。

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

不想等短信/图形验证码时可以用**扫码登录**（推荐）：

```
python fetch_bodyfat.py login --qr
```

脚本会请求 `longPolling/loginUrl`，把返回的 `qr` 字段（它本身就是一张 260×260 的
二维码 PNG 图片）保存到 `data/qr.png` 并等待你扫码；请用「米家」或「小米运动健康」
App 扫一扫，微信/系统相机会把二维码当成一个图片链接打开。扫码成功后自动换
`passToken` → `ssecurity` → `serviceToken` 并写入 `data/tokens.json`。
单独调试可用：`python -m mihealth.auth qr`。

---

## 2. 目录结构

```
mihealth/
  auth.py          # 小米 passport 短信登录（含图形验证码）、token 持久化、passToken 续期
  client.py        # RC4 签名客户端 + fitness/eco 接口封装 + 记录字段归一化
fetch_bodyfat.py   # 命令行入口（默认 = 登录 + 增量抓取）
tools/
  export_token.py       # 本地把 data/tokens.json 打包成 GitHub Actions Secret
  gha_restore_token.py  # CI 内从 Secret 还原 data/tokens.json
.github/workflows/
  update-bodyfat.yml    # 每日自动抓取并提交 data/bodyfat.json
data/
  tokens.json      # 登录凭据（含 serviceToken/ssecurity/passToken，注意保密）
  device.json      # 设备标识（请求签名要用）
  bodyfat.json     # 全部体脂记录（JSON：meta + latest + records 数组；已入库，可直链读取）
  state.json       # 增量游标、上次抓取时间
  captcha.png      # 仅在登录需要图形验证码时生成
  qr.png           # 扫码登录时保存的二维码（可删）
work/
  evidence/        # 逆向证据（关键 JS 模块、签名对拍向量、各模块分析报告、枚举文案字典）
  README.md        # 证据目录说明
```

## 3. 使用

### 3.1 一条命令（推荐）

```
python fetch_bodyfat.py
```

- 没有凭据时会自动进入登录（手机号 + 短信验证码，必要时再输图形验证码；
  也可先用 `python fetch_bodyfat.py login --qr` 扫码登录），凭据写入 `data/tokens.json`；
- 有凭据时直接做增量抓取，合并进 `data/bodyfat.json`，并打印最近几条体脂数据。

### 3.2 手动登录（可指定手机号，便于脚本/远程使用）

```
python fetch_bodyfat.py login --phone 138xxxxxxxx
# 需要图形验证码时：图片已保存到 data/captcha.png → 输入图中字符
# 请输入短信验证码: ______
```

若账号开了二次验证，按提示打开链接确认后执行 `refresh`。
也支持 `python fetch_bodyfat.py login --phone 138xxxxxxxx --code 123456` 直接提交短信码。
更省事的是扫码：`python fetch_bodyfat.py login --qr`（见上一节）。

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

### 3.5 定时运行（本机）

> 想完全免维护，直接用 §6 的 GitHub Actions（推荐）。

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

## 4. JSON 结构与字段说明

字段含义依据 APK 3.61.0 的 RN bundle 文案（`res/localizedString/zh-CN.json`）与 DEX
常量（`com.xiaomi.fitness.*.annotation.*`）；取证位置见 4.3 末尾。

### 4.1 文件结构

`data/bodyfat.json` 的 schema 为 `mi-fits/bodyfat@1`：

| 顶层键 | 类型 | 说明 |
| --- | --- | --- |
| `schema` | string | 固定 `mi-fits/bodyfat@1`；结构变化时升级版本号 |
| `updated_at` | string | 快照生成时间（本地时区 ISO 8601，CI 中为 Asia/Shanghai） |
| `updated_at_ts` | number | 同上，epoch 秒 |
| `count` | number | `records` 数组长度 |
| `latest` | object | 最新一条记录的副本（即 `records` 的最后一条），方便直接读「最新体脂」 |
| `records` | array | 全部记录，按 `time` 升序；每条结构见 4.2 |

### 4.2 单条记录字段

每条记录 = 云端一条体重记录 + 同一次测量返回的体成分。只有体重、没有体成分的记录
（`has_body_composition=false`，如手动录入或未赤脚测量）里，体成分字段可能为 `0` 或缺失。

**标识与时间**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `time` | number | 毫秒 | 测量时间（epoch 毫秒） |
| `time_sec` | number | 秒 | 同一时间的秒级时间戳 |
| `time_text` | string | - | 本地时间 `YYYY-MM-DD HH:mm` |
| `sid` | string | - | 来源设备 id：S800 为 `blt.3.xxx`；`xiaomiwear_app_manually` 表示 App 手动录入 |
| `key` | string | - | 数据类型键，固定 `weight`（体成分随体重记录一起返回） |
| `source` | string | - | 归一化来源，固定 `fitness/weight` |
| `has_body_composition` | boolean | - | 本次是否含完整体成分（`body_fat_rate > 0`） |

**体重与体成分**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `weight` | number | kg | 体重 |
| `bmi` | number | kg/m² | BMI |
| `body_fat_rate` | number | % | 体脂率 |
| `fat_mass` | number | kg | 脂肪量 |
| `fat_free_body` | number | kg | 去脂体重（瘦体重） |
| `muscle_mass` | number | kg | 肌肉量 |
| `muscle_rate` | number | % | 肌肉率 |
| `skeletal_muscle_mass` | number | kg | 骨骼肌量 |
| `limbs_skeletal_muscle_index` | number | kg/m² | 骨骼肌指数（四肢骨骼肌指数，常用于肌少症筛查参考） |
| `protein_mass` | number | kg | 蛋白质量 |
| `protein_rate` | number | % | 蛋白质率 |
| `body_moisture_mass` | number | kg | 体水分量 |
| `moisture_rate` | number | % | 水分率 |
| `bone_mass` | number | kg | 骨盐量（骨量） |
| `bone_rate` | number | % | 骨盐率 |
| `visceral_fat` | number | 等级 | 内脏脂肪等级（不是 kg） |
| `whr` | number | 比例 | 腰臀比 |
| `basal_metabolism` | number | kcal/天 | 基础代谢（BMR） |
| `body_age` | number | 岁 | 身体年龄 |
| `body_score` | number | 分 | 身体得分（0–100） |
| `recommended_calories_intake` | number | kcal/天 | 推荐每日热量摄入 |
| `bpm` | number | 次/分 | 测量时心率；`0` 表示本次未测到 |

**目标与控制建议**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `standard_weight` | number | kg | 标准体重（旧算法，仅部分历史记录有） |
| `standard_weight_v2` | number | kg | 标准体重（新算法，优先使用） |
| `weight_control` | number | kg | 体重目标调整量：正=建议增重，负=建议减重，`0`=维持 |
| `muscle_control` | number | kg | 肌肉目标调整量：正=建议增加，`≤0`=维持 |
| `fat_control` | number | kg | 脂肪目标调整量：负=建议减少，`≥0`=维持 |

**体型与评分口径（枚举，见 4.3）**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `somatotype` | number | 体型（10 档，-5…4） |
| `body_shape` | number | 身体形态（0…5） |
| `score_standard_type` | number | 评分所用标准（EvaluationType，0…3） |

**节段数据（八电极秤专有）**

| 字段 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `trunk_muscle_mass` / `trunk_fat_mass` | number | kg | 躯干肌肉量 / 脂肪量 |
| `left_upper_limb_muscle_mass` / `left_upper_limb_fat_mass` | number | kg | 左上肢肌肉量 / 脂肪量 |
| `right_upper_limb_muscle_mass` / `right_upper_limb_fat_mass` | number | kg | 右上肢肌肉量 / 脂肪量 |
| `left_lower_limb_muscle_mass` / `left_lower_limb_fat_mass` | number | kg | 左下肢肌肉量 / 脂肪量 |
| `right_lower_limb_muscle_mass` / `right_lower_limb_fat_mass` | number | kg | 右下肢肌肉量 / 脂肪量 |
| `trunk_muscle_rank` / `trunk_fat_rank` | number | 枚举 | 躯干肌肉 / 脂肪等级：-1 偏低、0 正常、1 偏高 |
| `left_upper_limb_muscle_rank` / `left_upper_limb_fat_rank` | number | 枚举 | 左上肢肌肉 / 脂肪等级，同上 |
| `right_upper_limb_muscle_rank` / `right_upper_limb_fat_rank` | number | 枚举 | 右上肢肌肉 / 脂肪等级，同上 |
| `left_lower_limb_muscle_rank` / `left_lower_limb_fat_rank` | number | 枚举 | 左下肢肌肉 / 脂肪等级，同上 |
| `right_lower_limb_muscle_rank` / `right_lower_limb_fat_rank` | number | 枚举 | 右下肢肌肉 / 脂肪等级，同上 |
| `upper_limb_muscle_balance` / `upper_limb_fat_balance` | number | 枚举 | 上肢（左右上肢）均衡度 |
| `lower_limb_muscle_balance` / `lower_limb_fat_balance` | number | 枚举 | 下肢（左右下肢）均衡度 |
| `limbs_muscle_balance` / `limbs_fat_balance` | number | 枚举 | 上下肢均衡度 |

### 4.3 枚举字典

所有枚举的整数常量都来自 APK 的 DEX；中文名来自 APK 内 RN bundle 的文案字典。

#### `somatotype` 体型

| 值 | 常量 | 中文名（App 文案） |
| --- | --- | --- |
| -5 | `THIN` | 消瘦型 |
| -4 | `SKINNY` | 偏瘦型 |
| -3 | `MASKED_OBESITY` | 隐形肥胖 |
| -2 | `LEAN` | 苗条肌肉型 |
| -1 | `SLENDER` | 苗条型 |
| 0 | `FIT` | 健康型 |
| 1 | `MUSCULAR` | 肌肉型 |
| 2 | `PLUMP` | 偏胖 |
| 3 | `ATHLETIC` | 运动员体型 |
| 4 | `OVERWEIGHT` | 肥胖 |

> DEX 里这些常量以 byte 存储（`THIN=251 … SLENDER=255`，即 -5…-1 的无符号写法），
> JSON 中是带符号整数。App 另有两套 `bodyType.desc.*` 建议文案（普通秤 / S400·电子秤各一套），
> 属于长段文字，本仓库未收录。

#### `body_shape` 身体形态

| 值 | 常量 | 中文名 |
| --- | --- | --- |
| 0 | `UNKNOWN` | 未识别 |
| 1 | `APPLE_SHAPE` | 苹果型 |
| 2 | `PEAR_SHAPE` | 梨型 |
| 3 | `HOUR_GLASS_SHAPE` | 沙漏型 |
| 4 | `V_SHAPE` | 倒三角型 |
| 5 | `NEAT_FIGURE` | 匀称型 |

#### `score_standard_type` 评分标准

| 值 | 常量 | 含义 |
| --- | --- | --- |
| 0 | `DEFAULT` | 默认 |
| 1 | `TYPE_S400` | S400 体脂秤口径 |
| 2 | `TYPE_WEIGHT_SCALE_STANDARDS` | 普通体重秤口径 |
| 3 | `TYPE_ELECTRONIC_SCALE_STANDARDS` | 电子体脂秤口径（S800 属此类；本仓库现有记录全为 3） |

#### `*_balance` 均衡度

| 值 | 常量 | 中文名 |
| --- | --- | --- |
| 0 | （默认） | 均衡 |
| 1 | `SLIGHTLY_UNBALANCED` | 轻度不均 |
| 2 | `SEVERE_UNBALANCED` | 严重不均 |

#### `*_rank` 节段等级

| 值 | 含义 |
| --- | --- |
| -1 | 偏低（App 用 low 配色） |
| 0 | 正常 |
| 1 | 偏高（App 用 high 配色） |

#### 其它取值说明

- `sid`：`blt.3.xxx`（八电极秤设备 id）或 `xiaomiwear_app_manually`（App 手动录入）。
- `key` 固定 `weight`；`source` 固定 `fitness/weight`。
- `time_text` 依赖运行环境时区；CI 已固定 `TZ=Asia/Shanghai`。
- BMI、体脂率等**数值型**字段本身不带分级，App 会按 `score_standard_type` 用不同阈值
  判定「偏低/正常/偏高」，阈值属于展示逻辑，不在 JSON 中。

> 取证：`Lcom/xiaomi/fitness/electronic/scale/annotation/{Somatotype,BodyShape,WeightParamBalanceLevel}`、
> `Lcom/xiaomi/fitness/health/weight/annotation/EvaluationType`（classes11/12.dex）；
> 中文名来自 `health` bundle 的 `health.weight.somatotype.* / bodyShape.* / balance.*`；
> `*_rank` 配色来自 health bundle `he-1866` 的 `-1===l?low:1===l?high:normal`。

### 4.4 与原始 Cloud API 的差异

- 云端 fitness 记录的 `value` 是 JSON 字符串，内含 **57 个键**；本仓库额外补了
  `time_sec / time_text / source / has_body_composition`，并把 `time` 统一成毫秒。
- 云端记录没有 `*_level` 字段（App 本地库里另有），所以节段等级只有 `*_rank`。
- `measuring_impedance_1..12`、`accountId`、`account_name` 只在 eco 兜底接口
  （`--api pull/list`）的记录里出现；fitness 记录不含原始阻抗，只返回云端算好的结果字段。

## 5. 说明与边界

- 该接口是 **App 内部私有接口**，小米可能随时改签名/路径；本文档对应 3.61.0（2026-11-04 构建）。
- 已实测：`get_fitness_data_by_time` 能拉到 2021-03 至今全部记录（含八电极完整体成分）；
  `eco/scale/record/pull`、`eco/scale/record/list` 在当前账号下返回空数组（可能只有虚拟成员/家人数据才走它）。
- 脚本只读数据，不修改云端记录；请求间隔受分页控制，正常使用不会触发风控。
- `data/tokens.json` 等价于登录态，请勿提交到公开仓库（已在 `.gitignore` 中排除）。
- **`data/bodyfat.json` 会随 workflow 提交到公开仓库**（这正是直链能用的原因）。
  如果你不想公开体重/体脂数据，请把仓库设为私有，或删掉 `.github/workflows/update-bodyfat.yml`。
- `work/evidence/` 保留少量关键逆向证据，便于日后升级 APK 时快速定位差异；
  完整解包结果可由仓库根目录的 APK 重新生成，不再保留在仓库里（详情见 `work/README.md`）。

## 6. GitHub Actions 每日自动更新

### 6.1 原理

1. 本地把登录态打包成一个 base64 字符串（`data/token.b64`），存到仓库 Secret
   `MIHEALTH_TOKEN_B64`（**不是**提交到仓库）。
2. workflow 每天按计划运行：还原 `data/tokens.json` → `--rebuild` 全量拉取 → 写入
   `data/bodyfat.json` → 内容有变化才提交并推送。
3. 其他项目直接 GET 一个 JSON 直链即可。

### 6.2 一次性设置

```bash
# 1) 本地已有 data/tokens.json（扫码或短信登录过）后，导出并做在线校验：
python tools/export_token.py

# 2) 把它写成仓库 Secret（二选一）
gh secret set MIHEALTH_TOKEN_B64 --repo cvooc/my-mi-fitness < data/token.b64
# 或：仓库 Settings → Secrets and variables → Actions → New repository secret
#     名称 MIHEALTH_TOKEN_B64，值粘贴 data/token.b64 的整行内容

# 3) workflow 与首份 data/bodyfat.json 已在仓库里；只有你本地改过代码时才需要
git add -A && git commit -m "ci: daily bodyfat update" && git push

# 4) 到仓库 Actions 页手动 Run 一次 "Update body-fat data" 验证
```

导出脚本只保留必要字段（`serviceToken`/`ssecurity`/`passToken` 等），并会先真实请求
最近 7 天数据做只读校验；`--no-check` 可离线导出，`--full` 保留全部字段。

### 6.3 直链

```
https://raw.githubusercontent.com/cvooc/my-mi-fitness/main/data/bodyfat.json
https://cdn.jsdelivr.net/gh/cvooc/my-mi-fitness@main/data/bodyfat.json
```

`raw.githubusercontent.com` 更新最及时（通常 1 分钟内）；jsDelivr 有缓存（约 12 小时），
适合对实时性要求不高的展示。两者内容相同：`schema/updated_at/count/latest/records`。

### 6.4 日常维护

- **token 过期**（日志报 401/403，或 "STS did not return a serviceToken"）：本机重新登录
  （`python fetch_bodyfat.py login --qr`）→ 重跑 `python tools/export_token.py` →
  更新 Secret（`gh secret set ... < data/token.b64`）。
- **数据没更新**：看 Actions 日志里 "无变化（不重写快照）" 是正常的（云端没新数据）；
  若看到 "云端返回 0 条记录，拒绝用空快照覆盖" 则是凭据失效或接口变更。
- **Actions 被自动暂停**：仓库 60 天无提交时 schedule 会被暂停，去 Actions 页点
  "Enable workflow" 即可。
- **Secret 泄露**：直接删除 Secret 并重新登录导出；`data/token.b64` 已被 `.gitignore` 忽略，
  注意别手动提交。

