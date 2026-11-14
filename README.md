# mi-fits · 小米运动健康体脂数据抓取

从 **小米运动健康（Mi Health）3.61.0** APK 逆向出的体脂秤历史数据接口，附带一个可定期运行的抓取脚本。

[![Update body-fat data](https://github.com/cvooc/my-mi-fitness/actions/workflows/update-bodyfat.yml/badge.svg)](https://github.com/cvooc/my-mi-fitness/actions/workflows/update-bodyfat.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Schema](https://img.shields.io/badge/schema-mi--fits%2Fbodyfat%401-informational)

一条体脂记录就带完整八电极报告（体重、体脂率、肌肉量、水分、基础代谢、内脏脂肪、节段肌肉/脂肪……共 60 余个字段）。
日常就一条命令（首次会自动走登录，之后只抓增量）：

```
python fetch_bodyfat.py
```

> 不想自己跑？仓库已带 **GitHub Actions**：每天北京时间 08:10 自动抓取并提交全量
> `data/bodyfat.json`，其他项目可直接用直链读取（见 [docs/github-actions.md](docs/github-actions.md)）。

## 特性

- **一条命令**：无凭据时自动进入登录（短信验证码，必要时补图形验证码；也支持扫码），之后增量抓取。
- **完整八电极数据**：直接命中健康数据查询接口 `get_fitness_data_by_time`，无需逐项补接口。
- **快照可靠**：写入原子安全；`--rebuild` 以云端为准重建、云端 0 条时拒绝覆盖；无变化不重写，适合 CI 定时提交。
- **长期在线**：GitHub Actions 每天更新并提交快照，raw / jsDelivr 双直链；token 过期可用 `passToken` 自动续期。
- **可验证**：签名/加解密实现与 APK 内真实 JS 逐字节对拍（证据与对拍脚本见 [work/](work/README.md)）。

## 快速开始

```bash
python -m pip install -r requirements.txt   # requests；登录额外需要 cryptography

python fetch_bodyfat.py                     # 首次：手机号 + 短信验证码 → 抓取 → 写入 data/bodyfat.json
python fetch_bodyfat.py login --qr          # 或者扫码登录
```

常用参数：

| 命令 | 作用 |
| --- | --- |
| `python fetch_bodyfat.py` | 增量抓取（默认从上次时间回溯 12 小时） |
| `python fetch_bodyfat.py --full` | 全量重拉（实测 2021 年起） |
| `python fetch_bodyfat.py --rebuild` | 以云端为准重建快照（删掉 App 里已删的记录） |
| `python fetch_bodyfat.py --days 30` | 只取最近 N 天 |
| `python fetch_bodyfat.py latest -n 10` | 查看最近记录 |
| `python fetch_bodyfat.py export --out bf.json` | 导出 JSON |

完整用法（手动登录、本机定时、调试 eco 接口）见 [docs/usage.md](docs/usage.md)。

## 数据快照与直链

仓库中的 [`data/bodyfat.json`](data/bodyfat.json) 是每日自动更新的全量快照（当前 schema `mi-fits/bodyfat@1`）：

```json
{
  "schema": "mi-fits/bodyfat@1",
  "updated_at": "2026-11-13 15:54:31",
  "count": 315,
  "latest": { "weight": 65.95, "body_fat_rate": 16.0, "...": "..." },
  "records": [ { "time": 1794526019000, "weight": 65.95, "body_fat_rate": 16.0, "...": "..." } ]
}
```

直接读取（内容相同）：

- `https://raw.githubusercontent.com/cvooc/my-mi-fitness/main/data/bodyfat.json`（更新最及时）
- `https://cdn.jsdelivr.net/gh/cvooc/my-mi-fitness@main/data/bodyfat.json`（有约 12 小时缓存）

字段含义、枚举字典（体型/身体形态/节段等级等）见 [docs/data-schema.md](docs/data-schema.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/usage.md](docs/usage.md) | 命令行用法、手动/扫码登录、本机定时运行、调试其它接口 |
| [docs/data-schema.md](docs/data-schema.md) | `data/bodyfat.json` 字段说明与枚举字典 |
| [docs/protocol.md](docs/protocol.md) | 接口、签名与 RC4 加密、登录流程、逆向证据与边界 |
| [docs/github-actions.md](docs/github-actions.md) | Actions 自动更新的原理、Secret 设置、直链与维护 |
| [work/README.md](work/README.md) | 逆向证据索引（JS 模块、报告、枚举文案来源）与对拍复现 |
| [AGENTS.md](AGENTS.md) | 给 AI 编码代理 / 贡献者的开发指南（架构、不变量、验证清单） |

## 项目结构

```
mi-fits/
├─ fetch_bodyfat.py          # CLI 入口：登录 + 抓取 + 导出
├─ mihealth/
│  ├─ auth.py                # 小米 passport 登录（短信 / 扫码 / passToken 续期）
│  └─ client.py              # 请求签名加密 + fitness / eco 接口封装
├─ tools/
│  ├─ export_token.py        # 本地登录态 → base64，写入 GitHub Secret
│  └─ gha_restore_token.py   # CI 中还原登录态
├─ data/
│  ├─ bodyfat.json           # 每日更新的全量快照（入库公开）
│  └─ ...                    # 登录凭据 / 设备标识 / 增量游标（本地生成，不入库）
├─ .github/workflows/update-bodyfat.yml
├─ docs/                     # 详细文档（协议 / 用法 / 数据格式 / CI）
├─ work/                     # 逆向证据与签名对拍脚本
├─ README.md                 # 项目介绍（本文件）
└─ AGENTS.md                 # 给 AI / 贡献者的开发指南
```

运行时的本地文件：

| 文件 | 说明 |
| --- | --- |
| `data/bodyfat.json` | 全部体脂记录快照（meta + latest + records 数组；已入库，可直链读取） |
| `data/tokens.json` | 登录凭据（含 serviceToken / ssecurity / passToken，注意保密，不入库） |
| `data/device.json` | 设备标识（请求签名要用，不入库） |
| `data/state.json` | 增量游标、上次抓取时间（不入库） |
| `data/token.b64` | 导出给 GitHub Actions 的 Secret 内容（不入库） |
| `mihealth/captcha.png` / `data/qr.png` | 登录时的图形验证码 / 二维码，可删（不入库） |

## 注意事项

- 本项目为非官方工具，与小米公司无关；数据接口来自 APK 逆向，App 更新后可能失效（当前适配 3.61.0）。
- 请只抓取自己账号的数据，不要用于他人账号或高频滥用；仓库中默认的 `data/bodyfat.json` 是维护者的个人真实数据，fork 后请用自己的账号覆盖。
- APK 体积大且不入库（`.apk` 已忽略）；复现逆向材料需自备同版本 APK，步骤见 [work/README.md](work/README.md)。
- 仓库目前未附带 LICENSE 文件。
