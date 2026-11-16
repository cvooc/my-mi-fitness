# 使用说明

> `fetch_bodyfat.py` 的命令行用法：登录、抓取模式、查看/导出、本机定时运行、调试接口。
> 只是想把数据拉下来 → [README 快速开始](../README.md#快速开始)。

## 1. 一条命令（推荐）

```
python fetch_bodyfat.py
```

- 没有凭据时会自动进入登录（手机号 + 短信验证码，必要时再输图形验证码；
  也可先用 `python fetch_bodyfat.py login --qr` 扫码登录），凭据写入 `data/tokens.json`；
- 有凭据时直接做增量抓取，合并进 `data/bodyfat.json`，并打印最近几条体脂数据。

## 2. 手动登录（可指定手机号，便于脚本/远程使用）

```
python fetch_bodyfat.py login --phone 138xxxxxxxx
# 需要图形验证码时：图片已保存到 mihealth/captcha.png → 输入图中字符
# 请输入短信验证码: ______
```

若账号开了二次验证，按提示打开链接确认后执行 `refresh`。
也支持 `python fetch_bodyfat.py login --phone 138xxxxxxxx --code 123456` 直接提交短信码。
更省事的是扫码：`python fetch_bodyfat.py login --qr`（见上一节）。

## 3. 抓取选项

```
python fetch_bodyfat.py                    # 增量（默认：从 last_time 回溯 12 小时）
python fetch_bodyfat.py --full             # 全量重拉（全部历史，实测 2021 年起）
python fetch_bodyfat.py --rebuild          # 重建本地快照：以云端为准，去掉你在 App 里删掉的记录
python fetch_bodyfat.py --days 30          # 最近 30 天
python fetch_bodyfat.py --since 1735660800000   # 指定起始时间（毫秒）
python fetch_bodyfat.py sync --show 5      # 显式 sync，结束时显示最近 5 条
python fetch_bodyfat.py --timeout 60 --retries 5   # 弱网/接口抖动时加大超时与重试
```

默认每次请求 `timeout=30` 秒、失败重试 3 次（退避 1s→2s），仍失败会打印
`请求失败（网络）: ...` 并以退出码 3 结束；token 过期时脚本会先用 `passToken` 自动续期一次，
并打印「鉴权被拒 (HTTP 401)，正在用 passToken 续期…」「续期成功，重试本次请求」，可直接从日志判断是续期还是真失败。

## 4. 查看 / 导出

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
  "updated_at": "2026-11-13 13:40:00",
  "updated_at_ts": 1794548400,
  "count": 315,
  "latest": { "...最新一条完整记录..." },
  "records": [ { "time": 1794526019000, "time_text": "2026-11-13 07:26",
                 "weight": 65.95, "body_fat_rate": 16.0, "...": "..." } ]
}
```

> 与旧的手工 JSON（`data/bfp-data.json`）的字段/数值对比报告见 `data/compare-report.md`（含个人数据，仅本地生成，不入库）。

## 5. 定时运行（本机）

> 想完全免维护，直接用 [GitHub Actions 每日自动更新](github-actions.md)（推荐）。

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

## 6. 调试其它接口（可选）

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
