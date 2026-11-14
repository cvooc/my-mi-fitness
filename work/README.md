# work/ · 逆向证据（精简版）

这里只保留定位/验证体脂接口时真正用得上的少量证据。APK 解包、SkyTree bundle
解压、dex 字符串转储等大体积中间产物已清理，需要时可用自备的同版本 APK 重新生成（`*.apk` 不入库）。

## evidence/js/

| 文件 | 说明 |
| --- | --- |
| `he-1828.js` | health SkyTree bundle 模块 `he-1828`：`getVirtualAccountList` / `getVirtualMemberRecords`（`eco/scale/record/pull` 分页）/ `normalizePullRecord`（field map），以及 `/app/v1/eco/api_proxy` 调用封装 |
| `common_mod_1243.js` | common bundle 模块 1243：axios 实例 + 拦截器（nonce/签名/Cookie/baseURL/解密/时间差） |
| `common_mod_1245.js` | 模块 1245：`stringifyRequestData` / precision-safe 字段处理 |
| `common_mod_1247.js` | 模块 1247：RC4（丢弃 1024 字节）+ SHA1/SHA256 签名 `encryptParameters` / `decryptResponse` |
| `common_mod_1248.js` | 模块 1248：HostType/HostMap，`getBaseUrl()`（cn → `https://hlth.io.mi.com`，其他地区加区号前缀） |
| `common_mod_1249.js` | 模块 1249：`UserServerInfo`（cookie `ssecurity/serviceToken/cUserId`、`serviceInfo()`）、语言码、服务器时间差 |
| `harness_crypto.js` | 加载 APK 内真实 JS 模块并与 Python 实现对拍的 Node 脚本 |
| `test_crypto.py` | Python 侧对拍脚本，读取 `js_vectors.json`，逐字节校验签名/加解密 |
| `js_vectors.json` | 由真实 JS 生成的签名向量（nonce/ssecurity/明文/密文/signature） |
| `probe_fitness3.py` | 首次成功拉取 `get_fitness_data_by_time` 的探针脚本（保留参数与解析方式） |
| `ms116_spec.json` | 米家八电极秤 S800 的 miot-spec（`xiaomi.scales.ms116`）公开实例 |

## evidence/reports/

| 文件 | 说明 |
| --- | --- |
| `dex-REPORT.md` | dex 静态分析：eco/fitness 端点表、`ThirdPartyDeviceApiService`（「其他设备」列表）、Cookie 注入、S800 相关类 |
| `rn-REPORT.md` | RN/SkyTree/插件 SDK 分析：模块 id 表、eco proxy 传输层、历史数据页实现、atlas 远端 bundle 机制 |
| `rn-ENDPOINTS.md` | health/common bundle 内全部 HTTP 端点清单 |
| `auth-REPORT.md` | 小米 passport 登录逆向（EUI 加密、短信/图形验证码、passToken、serviceToken 获取） |
| `cloud-REPORT.md` | Mi Health / Mijia 云接口探针结果（`get_fitness_data_by_time` 同款请求体、miio 签名、S800 did、eco 空数据结论） |

## evidence/labels/

| 文件 | 说明 |
| --- | --- |
| `health-weight-zh-CN.json` | 从 APK `health` SkyTree bundle 的 `res/localizedString/zh-CN.json` 抽出的体重/体成分文案（`health.weight.somatotype.*`、`bodyShape.*`、`balance.*`、`index.*`、`segmental.*`、`bodyType.desc.*` 等），是 [docs/data-schema.md](../docs/data-schema.md) 枚举字典中文名的来源 |

## 复现签名对拍

```bash
python work/evidence/js/test_crypto.py      # 应为 ALL PASS
```

`test_crypto.py` 读取同目录 `js_vectors.json`；向量本身由 `harness_crypto.js` 从 APK 内的
`common` bundle 提取真实模块算出（需要先把 APK 里的 `common*.zip` → `*.android.tar` →
`index.android.bundle` 解出来，再传路径给 harness）。

## 如何从 APK 重新生成这些材料

1. `python -m zipfile -e 3.61.0_*_mihealth.apk work/tmp/`（或任意解压工具）。
2. `assets/builtin_bundles_skytree/*.zip` 内是 `*.android.tar`，tar 里是 `index.android.bundle`（Metro JS bundle）。
3. 模块边界：`__d(function(g,r,i,a,m,e,d){...},<id>,[deps]);`，用 `rfind("__d(", 0, end_marker)` 截取。
4. dex 里的字符串/方法用 `androguard` 读取；`assets/plugin_rn_sdk/sdk.bundle` 是 miwear 插件 SDK。
