# AGENTS.md · 开发与 AI 代理指南

本文件面向 **在本仓库内改代码的 AI 编码代理和贡献者**。项目介绍、特性与数据直链见 [README.md](README.md)；
接口协议、字段字典、CLI 全量用法分别在 [docs/protocol.md](docs/protocol.md)、[docs/data-schema.md](docs/data-schema.md)、
[docs/usage.md](docs/usage.md)。本文件只写**改动代码时必须知道的事**：地图、命令、不变量、验证清单、常见坑。

## 1. 一句话定位

从「小米运动健康（Mi Health）」APK 逆向出的私有体脂接口（`get_fitness_data_by_time`）抓取历史体脂数据，
维护 `data/bodyfat.json` 快照；有两个消费者：**命令行用户**（`fetch_bodyfat.py`）与 **GitHub Actions 每日任务**。

## 2. 代码地图

| 路径 | 职责 | 改动时注意 |
| --- | --- | --- |
| `fetch_bodyfat.py` | 唯一 CLI 入口：argparse 子命令（sync/fetch/login/refresh/accounts/raw/latest/export）+ 抓取编排 + 快照写入 | 改子命令/参数要同步 docs/usage.md |
| `mihealth/auth.py` | 小米 passport 登录：短信/图形验证码/扫码、serviceToken 获取、passToken 续期、凭据落盘；`python -m mihealth.auth` 也可单独用 | 加密部分与 APK EUI 实现对齐，改动需回归登录 |
| `mihealth/client.py` | 签名加密层（nonce / `rc4_hash__` / signature / RC4 解密）、fitness 分页、eco `api_proxy`、`normalize_pull_record`、异常类 | **核心不变量所在**，改完必须跑对拍脚本 |
| `mihealth/__init__.py` | `__version__`（当前 `1.2.0`） | 接口/快照格式有变时 bump |
| `tools/export_token.py` | 本地 `data/tokens.json` → `data/token.b64`（仅保留必要字段，默认在线校验） | `--no-check` 离线导出、`--full` 保留全字段 |
| `tools/gha_restore_token.py` | CI 中从 Secret 还原 `data/tokens.json` | 与 workflow 环境变量约定一致 |
| `.github/workflows/update-bodyfat.yml` | 每日 08:10（北京时间）抓取 → 有变化才提交 | 见 §7 |
| `data/bodyfat.json` | 入库的公开快照（schema `mi-fits/bodyfat@1`） | 不手改；由脚本原子写入 |
| `docs/` | 用户文档（protocol / usage / data-schema / github-actions） | 行为变更必须同步 |
| `work/` | 逆向证据：APK 内 JS 模块切片、分析报告、枚举文案、签名对拍 | 改签名逻辑时这里是唯一 ground truth |

## 3. 常用命令

```bash
python -m pip install -r requirements.txt

python fetch_bodyfat.py --help                 # 查看 CLI
python fetch_bodyfat.py                        # 增量抓取（首次自动登录）
python fetch_bodyfat.py login --qr             # 扫码登录
python fetch_bodyfat.py --days 1 --show 3      # 小范围试跑
python fetch_bodyfat.py export --out /tmp/x.json
python -m mihealth.auth --help                 # 单独调试登录/续期

# 验证（提交前必跑，见 §6）
python -m compileall -q fetch_bodyfat.py mihealth tools
python work/evidence/js/test_crypto.py         # 期望输出 ALL PASS
```

## 4. 关键不变量（改动前务必确认）

1. **签名/加密不得漂移**：`mihealth/client.py` 的实现与 APK 内 `common_mod_1243/1245/1247/1248/1249.js`
   逐字节对齐，`work/evidence/js/js_vectors.json` 是真实 JS 生成的向量。
   - nonce：12 字节 = `u32be(rand)‖u32be(rand)‖u32be(server_sec//60)` 再 base64；
   - key：`base64(SHA256(b64(ssecurity)‖b64(nonce)))`；
   - RC4 加密后**丢弃前 1024 字节**（`RC4_DROP_LENGTH`）；
   - `rc4_hash__`：对**明文** params 排序拼 `k=v&` 后算 SHA1(`POST&<path>&<query>&<key_s>`)；
   - signature：对所有字符串值 RC4 加密后的**密文**再算一遍；
   - 请求 form：`data=&rc4_hash__=&signature=&_nonce=`，明文外层是 `{"data": "<inner JSON string>"}`；
   - 响应体用同一 key RC4 解密。Host 为 `hlth.io.mi.com`，Cookie 带 serviceToken/cUserId/locale。
   改完必须 `python work/evidence/js/test_crypto.py` 为 ALL PASS。
2. **快照格式受控**：顶层 `schema/updated_at/updated_at_ts/count/latest/records`；改字段或语义要升级 schema 版本号，
   并同步 [docs/data-schema.md](docs/data-schema.md) 与 `_record_key` 去重逻辑。
3. **写入语义**：tmp + `os.replace` 原子写；`--rebuild` 遇云端 0 条**拒绝覆盖**；`--if-changed` 内容未变则不重写
   （CI 依赖此语义避免空提交）；去重键 = `accountId|sid + time + weight`；增量回溯 `OVERLAP_MS = 12h`。
4. **错误与退出码**：`0` 成功、`2` 用法/交互/凭据错误（argparse、EOF、`AuthError`）、`3` 接口请求失败（`HlthHttpError`）、`130` Ctrl-C。实现见 `fetch_bodyfat.py` 的 `main()`；CI 依赖非 0 判定失败，勿吞异常。
5. **凭据绝不入库**：`data/tokens.json`、`data/device.json`、`data/state.json`、`data/token.b64`、`data/qr.png`、
   `mihealth/qr.png`、`mihealth/captcha.png` 均在 `.gitignore`；日志里不要打印 token/ssecurity。
6. **个人数据文件**：`data/bfp-data.json`、`data/compare-report.md`、`data/state.json` 属私有中间产物，不入库。
7. **只操作自己账号**：任何改动不得引入批量/他人账号抓取；保持请求频率低（分页 + 重试退避）。

## 5. 修改指引（改哪、同步哪）

- 接口/签名/登录逻辑 → 同时更新 [docs/protocol.md](docs/protocol.md)，必要时补 `work/evidence/` 证据与向量。
- 字段/枚举/数值语义 → [docs/data-schema.md](docs/data-schema.md)（枚举中文名来源：`work/evidence/labels/health-weight-zh-CN.json`）。
- CLI 参数/输出/退出码 → [docs/usage.md](docs/usage.md)。
- 快照生成/CI 行为 → [docs/github-actions.md](docs/github-actions.md) + workflow 注释。
- `README.md` 定位是**项目介绍**（是什么、怎么快速用、数据在哪、文档索引）；不要往里塞实现细节与代理指令。
- `work/README.md` 是证据索引；增删证据文件时同步表格。

## 6. 提交前验证清单

```bash
python -m compileall -q fetch_bodyfat.py mihealth tools   # 语法
python work/evidence/js/test_crypto.py                    # 签名对拍 → ALL PASS
python fetch_bodyfat.py --help                             # CLI 未损坏
git diff --stat                                            # 确认无凭据/大文件入库
```

- 有凭据时可加跑 `python fetch_bodyfat.py --days 1 --if-changed --show 3` 做真实接口回归（不应产生多余快照改动）。
- 若改了 workflow：本地无法直接跑 Actions，至少用 `python tools/gha_restore_token.py` 的路径做一次干跑验证。
- Windows 控制台是 cp936，中文帮助可能显示乱码（`chcp 65001` 或 `python -X utf8` 可正常显示）——这是显示问题，不是编码 bug。

## 7. CI / 提交约定

- **Conventional Commits**：`feat:` / `fix:` / `docs:` / `ci:` / `chore(data):` / `refactor:`。
- workflow `Update body-fat data`：cron `10 0 * * *`（UTC）= 北京时间 08:10，`TZ=Asia/Shanghai`；
  步骤 = 还原 token → `python fetch_bodyfat.py --rebuild --if-changed --show 3` → 有变化才以
  `chore(data): update body-fat snapshot (<时间>)` 提交；`permissions: contents: write`；`concurrency: update-bodyfat`。
- Secret：`MIHEALTH_TOKEN_B64`（兼容旧的 `MIHEALTH_TOKEN`）；token 失效时按 docs/github-actions.md 重新导出。
- **不要手改** `data/bodyfat.json` 的 `updated_at/count`；**不要让 CI 生成 `--if-changed` 之外的差异**。

## 8. 已知坑 / 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 401/403 | 脚本会用 `passToken` 自动续期**一次**；仍失败则重新登录（`login --qr`） |
| `STS did not return a serviceToken` | 登录态失效，重新登录并重新导出 Secret |
| `云端返回 0 条记录，拒绝用空快照覆盖` | 凭据失效或接口变更；不要删掉该保护 |
| `--api pull/list/common` 返回空数组 | 生态（eco）兜底接口对当前账号无数据，属预期，不是 bug |
| Actions 不再运行 | 仓库 60 天无提交会被暂停 schedule，去 Actions 页 Enable workflow |
| 直链数据滞后 | raw 约 1 分钟；jsDelivr 缓存约 12 小时，实时性优先用 raw |
| 签名对拍失败 | 先确认 `js_vectors.json` 未被改动，再检查 `RC4_DROP_LENGTH`/明文与密文的 hash 顺序 |

## 9. 文档分工（三层，别写混）

| 层 | 文件 | 面向 | 放什么 |
| --- | --- | --- | --- |
| 介绍 | `README.md` | 新访客 | 是什么、特性、快速开始、数据直链、文档索引 |
| 深水区 | `docs/*.md` | 使用者/维护者 | 协议、字段字典、CLI 全量用法、CI 细节 |
| 开发 | `AGENTS.md`（本文件） | AI 代理/贡献者 | 地图、不变量、验证清单、提交约定、坑 |
