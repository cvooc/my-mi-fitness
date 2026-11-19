# GitHub Actions 每日自动更新

> 仓库自带的 workflow 每天从云端抓取体脂数据并提交 `data/bodyfat.json`，外部项目可直接用 raw 直链读取。
> 相关文件：`.github/workflows/update-bodyfat.yml`、`tools/export_token.py`、`tools/gha_restore_token.py`。

## 1. 原理

1. 本地把登录态打包成一个 base64 字符串（`data/token.b64`），存到仓库 Secret
   `MIHEALTH_TOKEN_B64`（**不是**提交到仓库）。
2. workflow 每天按计划运行：还原 `data/tokens.json` → `--rebuild` 全量拉取 → 写入
   `data/bodyfat.json` → 内容有变化才提交并推送。
3. 其他项目直接 GET 一个 JSON 直链即可。

## 2. 一次性设置

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

## 3. 直链

```
https://raw.githubusercontent.com/cvooc/my-mi-fitness/main/data/bodyfat.json
https://cdn.jsdelivr.net/gh/cvooc/my-mi-fitness@main/data/bodyfat.json
```

`raw.githubusercontent.com` 更新最及时（通常 1 分钟内）；jsDelivr 有缓存（约 12 小时），
适合对实时性要求不高的展示。两者内容相同：`schema/updated_at/count/latest/records`。

## 4. 日常维护

- **token 过期**（日志报 401/403，或 "STS did not return a serviceToken"）：本机重新登录
  （`python fetch_bodyfat.py login --qr`）→ 重跑 `python tools/export_token.py` →
  更新 Secret（`gh secret set ... < data/token.b64`）。
- **日志出现「鉴权被拒…正在用 passToken 续期」**：属自动续期，说明 Secret 里的 `passToken` 仍有效；
  紧跟的「续期成功，重试本次请求」若之后仍失败，才需要重新导出 Secret。
- **日志出现 `请求失败（网络）: ... Read timed out`**：接口抖动/弱网。脚本已按 `--retries`（默认 3 次）
  做指数退避重试，连续失败才以退出码 3 结束；重跑一次 workflow 通常即可恢复，无需重导 Secret。
- **数据没更新**：看 Actions 日志里 "无变化（不重写快照）" 是正常的（云端没新数据）；
  若看到 "云端返回 0 条记录，拒绝用空快照覆盖" 则是凭据失效或接口变更。
- **Actions 被自动暂停**：仓库 60 天无提交时 schedule 会被暂停，去 Actions 页点
  "Enable workflow" 即可。
- **Secret 泄露**：直接删除 Secret 并重新登录导出；`data/token.b64` 已被 `.gitignore` 忽略，
  注意别手动提交。

## 5. 触发时间与延迟

cron 写的是 UTC：`10 0 * * *` = 北京时间 08:10。GitHub 官方说明 schedule 是**尽力而为**的
（"The schedule event can be delayed during periods of high loads … some queued jobs may be dropped"）。

实测（2026-11-14 ~ 11-18，仓库自己的 5 次 schedule 运行）：

| cron 时刻 (UTC) | 实际创建时间 (UTC) | 延迟 | 北京时间 |
| --- | --- | --- | --- |
| 00:10 | 06:10 / 06:16 / 06:17 | 6.0h | 14:10 ~ 14:17 |
| 00:10 | 07:47 | 7.6h | 15:47 |
| 00:10 | 07:15 | 7.1h | 15:15 |

- 延迟发生在**调度器投递阶段**（`created_at` 就已经晚了，`started_at - created_at ≈ 0s`），
  不是 runner 排队；同一时间 `workflow_dispatch` 手动触发是秒级的。
- 因此 workflow 里额外加了一条 `20 18 * * *`（北京 02:20）的补偿触发：按 6~7.6h 延迟，
  它会落在北京 08:20~09:56，赶在早间称重之后抓当日数据；主触发保留，GitHub 恢复后会自己回到 08:10。
- 如果长期不需要这种补偿，删掉 workflow 里第二条 cron 即可；想精确到分钟则改用外部定时器
  调 `workflow_dispatch`（需要一个带 `workflow`/Actions write 权限的 token）。
