# Mi Health 3.61.0 — DEX 静态分析报告：体脂/身体成分数据 API

目标 APK：`D:\workspace\github\mi-fits\3.61.0_20261104104057_mihealth.apk`
sha256 `0A741C885939D264AEAA2F0FD3F412F381211EB08E43F8B585E35280777BC68A`，13 个 dex（classes.dex .. classes13.dex），共 742,981 个字符串。
分析方式：纯静态读 dex 字符串池 + 注解 + 方法字节码（androguard 4.1.4 低层 API）；未运行、未登录、未联网。
所有原始证据文件在 `work/dex/`（见第 11 节）。

---

## 0. 结论摘要（TL;DR）

1. **体脂记录列表不由 Mijia 云 RPC 提供，而是 Mi Health 自有后端 `hlth.io.mi.com`，通过自研“动态代理 + 注解”HTTP 框架（不是 Retrofit）调用，最终全部落到同一个代理入口**：
   `POST https://hlth.io.mi.com/app/v1/eco/api_proxy`，body 为 `{"eco_api":"<API 名>","params":"<JSON 字符串>"}`。
   - 注解证据：`Lcom/xiaomi/fitness/electronic/scale/request/ScaleService;` [classes11] 类注解 `@BaseUrl(host='https://hlth.io.mi.com/', path='app/v1/')`，两个方法注解 `@Lamj(value='eco/api_proxy')`。
   - body 键证据：`ScaleRequest.getEcoApiProxy` 的 const-string 顺序 `'eco_api' | 'params' | 'getEcoApiProxy() data = '`，以及 `map.put("eco_api", name)` 语义（方法体构造 Map 后 toJson）。与 Lead 团队 JS 侧逐字节验证一致。
2. **“全部记录”列表的本机（dex）调用链**：`MainViewModel.getWeightRecords(accountId, page, pageSize)` → `ScaleRequest.getWeightRecords` → `eco_api = 'eco/scale/record/list'`，params `{accountId, page, pageSize}`；`MainViewModel.pullRecord(uid:J, did:String, pageSize:I, createTime:J)` → `ScaleRequest.pullRecord` → `eco_api = 'eco/scale/record/pull'`，params `{uid, did, pageSize, createTime}`。RN 侧 `HostModule.requestUrl` 同样用 `'eco_api'` 走同一入口。
3. **数据库本地也存一份**：Room 表 `virtual_account`（字段含 `bodyComposition TEXT`）与 `WeightRepository.getBodyCompositionList` 在 [classes13] 走本地 Room；远端拉取由 `ScaleRequest` 完成。两者是“远端拉取 + 本地缓存/同步”关系。
4. **认证**：`AccountServiceCookieImpl` [classes12] 会给 `.hlth.io.mi.com`（及 `.staging-hlth.io.mi.com`、`.st.iot.home.mi.com`、`.watch.iot.mi.com`、`.st-watch.iot.mi.com`、`.dev.fe.home.mi.com`、`.account.xiaomi.com`）种 cookie：`serviceToken=<sid=miothealth 的 serviceToken>`、`cUserId=<cUserId>`、`locale=<locale>`（其中还有格式化串 `%s_serviceToken=%s`，用于 sid 前缀 cookie 名）。sid=`miothealth` 由 `AccountModule$Companion.provideCookie()` 的 const-string `'miothealth'` 证实。
5. **“其他设备”列表请求**：`ThirdPartyDeviceApiService.getDevices` → `@Lamj('source/get_third_device_source_list')`，`@BaseUrl(host='https://hlth.io.mi.com/', path='app/v1/')`，完整 URL = `https://hlth.io.mi.com/app/v1/source/get_third_device_source_list`（POST），请求体字段 `page_size / page_start / status`；响应 `ThirdPartyDeviceListData`（list + next 游标），设备项字段 `did, model, name, identifier, alias`（+mac/nodeId）。这是“其他设备”卡片的数据来源；卡片时隐时现由 `EcoDeviceManagerComponent`/`OtherDeviceLoaderManagerImpl` 的 **onResume + diffTime 节流、登录成功后、地区变更后** 刷新决定。
6. **S800 → 插件 id 映射不在 dex**：全部 dex 中不存在 `S800/s800/xmtzc/eight-electrode/eightelectrode/body_composition` 字面量（只有 `八电极` 中文日志，属 BLE 计算）；`assets/config/product_zh_cn.json` 只有一条小米手表（xiaomi.watch.mars, pd_id 1711），`assets/server_config/*.json` 只有国家→machineCode。体脂秤的 product/plugin 配置由服务端 `product/get_config_info_by_category` 动态下发，或位于 RN 插件 bundle（不在 dex 结论范围）。
7. **uid 来源**：dex 中无 `LoginUser` 类（那是 RN JS 侧对象）。原生对应 `MiAccessToken`（[classes10]）字段：`userId, cUserId, security(=ssecurity), serviceToken, timeDiff, expireTime, updated`；由 `TokenManager.getServiceToken(sid, force, ...)` 返回。`AccountManager.getUserId()` / `AccountCoreInfo.getUserId()` 也返回 String userId。RN `CommonBridgeModule.getServiceTokenInfo()` 返回 `ssecurity/serviceToken/timeDiff`（用于前端 RC4 签名），其 `userId` 来自 `getUserInfo`/账号模块的 `MiAccessToken.userId`（**与 cUserId 不同**），登录后即可拿到。

---

## 1. 方法与产物（可复现）

环境：Windows + Python 3.13 + androguard 4.1.4；没有 jadx/apktool/java。全部脚本在 `work/dex/`：

| 文件 | 作用 |
|---|---|
| `dexlib.py` | 无依赖 DEX 解析（strings/classes/methods），用于生成字符串表 |
| `extract_strings.py` | 生成 `strings/classesN.txt`（idx<TAB>字符串）与 `classes/classesN.cls.txt`（TYPE<TAB>SUPER<TAB>方法数） |
| `q.py` | 对已提取字符串做正则检索（`python q.py "<regex>" [-f classesN] [-m N] [-c]`） |
| `scan_urls_dex.py` → `scan_urls.json` | 全 dex URL/域名扫描（266 域名 / 780 URL） |
| `scan_paths.py` → `scan_paths.json/txt` | 全 dex 路径候选（238） |
| `scan_services.py` → `services.json/txt/summary` | 扫描全 dex 类/方法注解，得 4,934 个带注解类；注解频次：Secret 128 / BaseUrl 117 / Ly3c 318 / Lamj 231 / Lb8b 212 / Lryc 18 |
| `svc_table.py` → `svc_all.txt` | 服务接口速查表：类、@BaseUrl(host,path)、@Secret、方法→路径/Header |
| `dump_class2.py` | 富 dump：类/方法/参数注解、const-string 字面量、invoke 目标（输出 `dump_*.txt`） |
| `find_callers.py` | 全 dex 交叉引用：给定类/方法正则，输出调用方（`dex callerClass callerMethod -> target`） |
| `resolve_idx.py` | 解析某 dex 的 method_id 索引 → 签名（用于人工核对 invoke） |

关键 API 事实（复现时避坑）：`dex.get_cm_method(idx)` 返回 `[class, name, [descChunks]]`（desc 是 list，需 join）；`dex.get_method()` 不存在；指令用 `ins.get_name()`（助记符）、`ins.get_string()`、`ins.get_ref_kind()`；注解用 `cm.get_annotation_item(off).get_annotation()` → `EncodedAnnotation.get_type_idx()/get_elements()`，`AnnotationElement.get_name_idx()/get_value()`。

---

## 2. 网络框架：自研注解 + 动态代理（非 Retrofit，非 OkHttp 原生调用）

全部位于 [classes12]，包 `com/xiaomi/fitness/net/`：
`ApiHolder, BaseRequest, HttpConfig, HttpFlow, HttpRepository, di/HttpClientModule, interceptor/HttpBeforeHandler, interceptor/HttpHandlerInterceptor, url/{BaseUrl,BaseUrlData,DefaultUrlData,DynamicHost,Secret,Secret$Companion,SecretData}, response/{BaseResponse,BaseData,BaseListData,BaseResult,HttpResultHandler,ApiException}, scope/{AndroidScope,NetCoroutineScope}`。

注解含义（由 `services.json` 全量统计 + 方法体行为推断，均有 const-string 证据）：

| 注解 | 目标 | 元素 | 作用 |
|---|---|---|---|
| `Lcom/xiaomi/fitness/net/url/BaseUrl;` | 类 | `host()`、`path()` | 服务接口的 host 与基础 path（如 `https://hlth.io.mi.com/` + `app/v1/`） |
| `Lcom/xiaomi/fitness/net/url/Secret;` | 类/方法 | `pathPrefix()`、`sid()`、`loginPolicy()`、`filterSignatureKeys()`、`encryptResponse()` | 该接口的密钥/签名/SID/登录策略；`SecretData` 为运行期值对象 |
| `@Ly3c`（混淆名，classes5 定义） | 方法 | `value()` = path | GET 请求 |
| `@Lamj`（混淆名，classes5 定义） | 方法 | `value()` = path | POST 请求 |
| `@Lb8b`（混淆名） | 方法 | 无 | POST 方法的附加标记（所有 @Lamj 方法都带；推断为 body/form 编码标记） |
| `@Lryc`（混淆名） | 方法 | `value()` = header 列表 | 额外 Header，如 `['Cookie: auth_key=rwelJuWBFJxmbMKD']` |

`ApiHolder`（[classes12]）是核心：`getPath(Method)`、`getBaseUrl(Class)`、`getAnnotationUrl(Class)`、`createHolderApi(Class, Function0)` = `java.lang.reflect.Proxy`、`invokeApi(Object, Method, Object[])`。
错误串（逐字）：`方法上必须添加注解（GET/POST）和path, 或方法中包含@Url 参数`、`需要添加BaseUrl注解`、`'s url is null`。
`BaseRequest.getService()` 通过泛型父类 `ParameterizedType` 取服务接口 Class，再 `Repository.getService()`；`BaseRequest.execute(Object)` → `HttpConfig.getHttpResultHandler().handleResponse(...)`。
`HttpHandlerInterceptor` 调 `HttpBeforeHandler.onHttpRequestBefore` → `chain.proceed` → `onHttpResultResponse`；签名/公共参数注入应实现于某个 `HttpBeforeHandler`（**本次未把具体签名实现类定位到，见 Open gaps**；但前端 JS 侧 RC4+base64 已由 Lead 团队验证）。

---

## 3. 主机清单（dex 字符串统计，Observation）

| Host | 次数 | 出现 dex |
|---|---|---|
| `cdn.web-global.fds.api.mi-img.com` | 200 | 多 dex（资源 CDN） |
| `cdn.cnbj1.fds.api.mi-img.com` | 68 | 多 dex |
| `account.xiaomi.com` | 36 | oauth/服务 token |
| `watch.iot.mi.com` | 36 | Mi Wear / watch 后端 |
| `hlth.io.mi.com` | 14 | classes2,3,6,8,9,10,11,12,13（**Mi Health 主后端**） |
| `m.xiaomi.com` | 13 | |
| `region.hlth.io.mi.com` | 11 | 区域/海外下载 |
| `st-watch.iot.mi.com` | 8 | |
| `api.io.mi.com` | 5 | **仅 classes3/classes13**：Mijia 云 RPC（`/home/...`、`/v2/home/...`、`/miotspec/...`、`/app/v2/...`），用于米家设备/插件管理，**不是体脂列表通道** |
| `pv.hlthopen.io.mi.com` | 1 | classes2 |
| `home.mi.com` | 1 | classes11（Mijia 登录 cookie 域） |
| `shopapi.io.mi.com` | 1 | |
| `staging-hlth.io.mi.com` | 1 | classes11 |
| `hlth.research.xiaomiwear.com` | 2 | classes12,13 |
| `watch.xiaomiwear.com` | 2 | |

精确 URL 样例：`https://hlth.io.mi.com/`（classes10 idx45022 / classes11 idx44196）、`https://hlth.io.mi.com/app/v1/resource/download`（classes11 idx44197）、`https://region.hlth.io.mi.com`、`https://region.hlth.io.mi.com/third-oauth/v3/callback`、`https://cdn.cnbj1.fds.api.mi-img.com/hlth-common-resource/oobe/device/`。

Mijia SDK 路径（[classes3] 内为 Mijia 云 RPC，供“米家插件/设备列表”而非体脂数据）：
`/home/device_list_page`、`/v2/home/device_list_page`、`/v2/home/home_device_list`、`/v2/home/local_device_list`、`/v2/home/get_device_cert`、`/homeroom/gethome`、`/v2/plugin/fetch_plugin`、`/v2/plugin/fetch_sdk`、`/miotspec/prop/get`、`/skytree/atlas/getBundle` 等；运行日志串佐证：`core /v2/home/device_list_page request next page with did:`、`core /v2/home/local_device_list onSuccess size:`、`request /v2/home/home_device_list homeId=`。

---

## 4. 体脂/体成分相关 API（核心表）

### 4.1 传输入口（所有 eco 接口复用）

```
POST https://hlth.io.mi.com/app/v1/eco/api_proxy
Content-Type: (由 @Lb8b 标记的编码策略决定)
Body(JSON): {"eco_api": "<name>", "params": "<JSON string>"}
```
- 服务接口：`Lcom/xiaomi/fitness/electronic/scale/request/ScaleService;` [classes11]
  - 类注解 `@BaseUrl(host='https://hlth.io.mi.com/', path='app/v1/')` + `@Secret()`（空元素，即全默认）
  - 方法1 `getEcoApiProxy(String body, String ?, Continuation)`：`@Lamj(value='eco/api_proxy')` + `@Lb8b()`
  - 方法2 `getEcoApiProxy(String body, Continuation)` 同上
- 同一入口的还有 `EcoProxyService`（[classes11]）、`FtpService`、`EnvironmentalService`、`RNRequest$RNApiService`/`CommonRequest$CommonApiService`（`path=None` 动态 @Url）等。
- 请求构造：`Lcom/xiaomi/fitness/electronic/scale/request/ScaleRequest;` [classes11]（extends `com.xiaomi.fitness.net.BaseRequest`）中的 `getEcoApiProxy(scope, ecoApi:String, params:String, success, error)`；const-string 顺序证据：`'eco_api' | 'params' | 'getEcoApiProxy() data = '`；`getEcoApiProxySync` 同理（日志 `'getEcoApiProxySync() data = '`），返回 `ProxyResponse`（`ProxyResponse(code, message, result)`）。

### 4.2 ScaleRequest 全部 eco_api 名（逐方法，含参数键，Observation）

| 方法 | params 键 | eco_api |
|---|---|---|
| `getWeightRecords(scope, accountId:String, page:I, pageSize:I, …)` | `accountId, page, pageSize` | **`eco/scale/record/list`** |
| `pullRecord(uid:J, did:String, pageSize:I, createTime:J, …)` （suspend） | `uid, did, pageSize, createTime` | **`eco/scale/record/pull`** |
| `pullRecordCommon(uid:J, did:String, model:String, pageSize:I, createTime:J, …)` | `uid, did, model, pageSize, createTime` | `eco/common/scale/pull` |
| `getVirtualAccount(scope, did:J, uid:String, …)` | `did, uid` | `eco/scale/account/list` |
| `getClaimedRecords(scope, did:String, …)` | `did, page, pageSize` | `eco/scale/record/claimList` |
| `getWeightAccount(scope, uid:String, weight:I, …)` | `uid, weight` | `scale/account/getAccountByWeight` |
| `addRecord` | `params` | `eco/scale/record/add` |
| `addRecordCommon` | `params` | `eco/common/scale/batchAdd` |
| `addVirtualAccount(scope, name, icon, sex, height, weightTarget, birth, …)` | `name, icon, sex, height, weightTarget, birth` | `eco/scale/account/add` |
| `modifyVirtualAccount(scope, accountId, name, icon, sex, height, weightTarget, birth, …)` | 同上+`accountId` | `eco/scale/account/update` |
| `deleteVirtualAccount(scope, accountId, …)` | `accountId` | `eco/scale/account/remove` |
| `deleteRecord(scope, did, …)` | `did, accountId` | `eco/scale/record/delete` |
| `deleteByDidAndCreateTime(scope, did:J, createTime:J, …)` | `did, createTime` | `eco/scale/record/deleteByDidAndCreateTime` |
| `deleteByDidAndCreateTimeParamsList(scope, delList, …)` | `did, createTime`（列表逐项，日志 `deleteByDidAndCreateTime json========4 is `） | 同上 |
| `getAccountUserInfo(scope, uid, …)` | `uid` | `account/user/info` |
| `getBindKey(scope, …)` | — | `/bs/bind/miot/bindKey` |
| `uploadScaleIcon(scope, uid:J, …)` | `uid` | `upload/scale/icon` |

补充：`EcoProxyRequest` [classes11]（同框架）`getEcoApiProxy(scope, String, String)` 同样 `'eco_api'/'params'`；`addDeviceSport(...)` → `eco/general-equipment/common/sport/add`；`recordToEcoServer(uid, mac, did, sn, deviceType, model, firmwareVersion, activationTime, otherData, createTime, updateTime)` → `eco/general-equipment/common/device/add`。

### 4.3 本机原生调用方（find_callers.py 结果，Observation）

`eco/scale/record/list`、`record/pull`、`account/list` 的**直接**调用方（全部经 `ScaleRequest`，[classes11]）：
- `Lcom/xiaomi/fitness/electronic/scale/MainViewModel;`
  - `pullRecord` → `ScaleRequest.pullRecord(J,String,I,J,Continuation)`
  - `getWeightRecords` → `ScaleRequest.getWeightRecords(scope, String accountId, I page, I pageSize, success, error)`
  - `addRecord` → `ScaleRequest.addRecord(...)`
- `Lcom/xiaomi/fitness/electronic/scale/WeightViewModel;`：`getAllVirtualAccount` / `getVirtualAccountByVirtualId` / `initVirtualAccount` → `getVirtualAccount(scope, J did, String uid, …)`
- `Lcom/xiaomi/fitness/electronic/scale/BabyWeightViewModel;`：同上（婴儿/虚拟成员）
- `Lcom/xiaomi/fitness/electronic/scale/ClaimRecordsViewModel;` / `ClaimRecordsHelper`：`getClaimedRecords`、`deleteByDidAndCreateTimeParamsList`
- `Lcom/xiaomi/fitness/electronic/scale/config/sync/EightDeviceSyncImpl;`：`syncVirtualUser` → `getVirtualAccount`（八电极同步路径）
- `Lcom/xiaomi/fitness/electronic/scale/model/UserManageViewModel;`：`addVirtualAccount` / `deleteVirtualAccount`
- `Lcom/xiaomi/fitness/electronic/scale/model/UserDataViewModel;`：`getVirtualAccount`
- `Lcom/xiaomi/fitness/electronic/scale/model/ElectronicScaleBaseViewModel;`：构造 `ScaleRequest`
- `Lcom/xiaomi/fitness/electronic/scale/AboutDeviceViewModel;`：`recallReminder` → `deleteRecord`
- RN：`Lcom/xiaomi/fitness/rn/modules/HostModule;`（[classes13]）`requestUrl` 的 const-string 含 `'eco_api'`（即 RN 页面通过 host module 走同一代理）。
- **`com.xiaomi.fitness.electronic.scale.export.*` 不直接发 HTTP**：该包是“对外导出 API”层（`ElectronicScaleRequest` 接口 61 方法、`ElectronicScaleManager` 等，classes11），由上面这些 ViewModel/Manager 层实现后转 ScaleRequest；未发现 export 包内直接构造 eco_api 字符串。

结论：体脂列表是 **Mi Health hlth 后端**（不是 Mijia 云 RPC，也不是 RN 页面自己独立域名）；RN 页面同样调用 `eco/api_proxy`。

---

## 5. “其他设备”卡片来源（Q1/Q3）

### 5.1 请求（Observation）

`Lcom/xiaomi/fitness/device/manager/api/ThirdPartyDeviceApiService;` [classes11]
- 类注解：`@BaseUrl(host='https://hlth.io.mi.com/', path='app/v1/')`、`@Secret(pathPrefix='')`
- `getDevices(String, Continuation)`：`@Lamj(value='source/get_third_device_source_list')` + `@Lb8b()`
  → **完整 URL：`https://hlth.io.mi.com/app/v1/source/get_third_device_source_list`（POST）**
- `getProductConfig(String, String, Continuation)`：`@Ly3c(value='product/get_config_info_by_category')`
- `unbindThirdDevice(String, Continuation)`：`@Lamj(value='bind/third_device_unbind')` + `@Lb8b()`

请求体（`ThirdPartyDeviceRequest.getDevices`，[classes11]，`toJson` 后传）：字段 **`page_size`、`page_start`、`status`**；另一重载（`getDevices(int, boolean)`）日志 `'getDevices status:'`、`'  bindDevice:'`；`getProductByCategory(long)` body：`{category:'health_equipment,sport_equipment', last_modify_time, app_platform, app_version}`；`unbindThirdDevice(sid)` 日志 `'unbindThirdDevice sid='` 并广播 `com.mi.health.action.THIRD_PARTY_DEVICE_CHANGED`。

### 5.2 响应 DTO（Observation）

- `Lcom/xiaomi/fitness/device/manager/api/model/ThirdPartyDeviceListData;` [classes11] extends `com.xiaomi.fitness.net.response.BaseListData`（[classes12]：BaseResponse 的 code/message + BaseData 的 data + BaseListData 的 list/next），自带 `getNext()/setNext(String)` → **游标分页**。
- 列表项 `Lcom/xiaomi/fitness/device/manager/export/bean/ThirdPartyDevice;` [classes11] extends `com.xiaomi.fitness.device.manager.bean.Device`：Room `@Entity(tableName='thirdparty_device')`、`@TypeConverters(DeviceConverter)`；继承字段 `did, model, name, identifier, alias`（+ mac/nodeId）。
- `ThirdPartyDeviceInfo`（继承 `bean/DeviceInfo`）toString 字段：`device, firmwareVersion, product, bleName, batteryInfo, firmwareUpgradeTipTimestamp, sn, battery, mac, did, model, name, nodeID, huamiAuthKey, productId, privateUUID`。
- **没有 `pluginId` 字段**；pluginId/packageId 只出现在 [classes3] `com.xiaomi.wearable.yrn`（米家 RN 插件管理器，通用插件框架），与 ThirdPartyDevice 响应无关。

同层还有（米家生态设备/产品）：
- `EcoDeviceApiService` [classes11]：`@BaseUrl(hlth.io.mi.com/, app/v1/)`、`@Secret(pathPrefix='')`；`getDevices @Lamj('source/get_source_list')`、`recordDevices @Lamj('source/record_source')`、`getProductConfig @Ly3c('product/get_config_info')`、`getProductConfigByCategory @Secret(loginPolicy=3) + @Lryc(['Cookie: auth_key=rwelJuWBFJxmbMKD']) + @Ly3c('product/get_config_info_by_category')`。
- `EcoDeviceRequest` [classes11]：`getDevices` 组 `{page_size, page_start, status}`，`getProductByCategory` 组 `{category:'health_equipment,sport_equipment', last_modify_time, app_platform, app_version}`，`recordDevices()`（把手机自己注册为 eco 设备，串 `mihlth.xiaomi.fitness.phone`、`persist.sys.device_name`、`MODEL`），`updateDeviceInfo(did, phone_id, detail)`，`unbind(did, model)` 等。

### 5.3 为什么“其他设备”时隐时现（触发链，Observation + Inference）

`Lcom/xiaomi/fitness/eco/device/manager/EcoDeviceManagerComponent;` [classes11]
- 类注解 `@AppComponent(namespace='EcoDeviceManager')`
- 方法带 `@ComponentTask(...)`：
  - `initConnect(multiProcess=True, processName=':device')`
  - `loadDeviceCache(notLaterThan=4, processName=':device')`
  - `requestDeviceWhenMainCreated(notLaterThan=10, processName=':device')`
  - `requestDeviceWhenMainResume(notLaterThan=4, processName=':device')`
  - `updateDevices(notEarlierThan=2, notLaterThan=4, processName=':device')`
  - `updateDevicesIfLogin`、`startConnectProcess(name='EcoConnectProcess', notLaterThan=11, onMainThread=False)`
- 相关字符串：`'request device when main resume, diffTime='`、`'login success, and requestDeviceList'`、`'logout success, and clear device'`、`'The region has changed from ' ... ', and reset device list'`、`'current region is '`、`'activity'/'lifecycle'/'onResume'`。
→ 刷新时机：**App 回到前台(onResume) 且距上次刷新超过阈值(diffTime)**、**登录成功**、**地区变更**；`updateDevices` 有 2~4 的调度窗口（ComponentTask 的 notEarlierThan/notLaterThan，单位疑似秒/任务数，未静态证实）。
- `OtherDeviceLoaderManagerImpl` [classes11]：`refreshEcoDevices`、`refreshRNDevices`、`refreshThirdPartyDevices`、`updateDevices` —— “其他设备”页的 loader。
- `ThirdPartyDeviceManagerImpl.requestDeviceList`（[classes11]）→ `ThirdPartyDeviceRequest.getDevices` → `source/get_third_device_source_list`；由 `DeviceManagerComponent.requestThirdPartyDevices`、`tryToRequestThirdPartyDevice$networkListener$1`、`registerThirdPartyDeviceRequestOnResume$receiver$1` 触发。
- `FirstDeviceManagerComponent`/`ScanManager`/`DeviceTabParentFragmentV4$onEcoDeviceStatusListener$1` 等 UI 层监听状态后决定卡片展示。
→ **卡片“时隐时现”= 该请求成功/缓存非空时出现；onResume 刷新被 diffTime 节流、或网络/登录状态变化时可能暂时为空。**

---

## 6. 认证 / Cookie / Token（Q2，Observation）

### 6.1 Cookie 种植（classes12 `Lcom/xiaomi/fitness/login/token/AccountServiceCookieImpl;`）

- 构造器 const-string：`'mTokenManager' / 'mAccessManager' / 'miothealth' / 'miothealth-staging' / 'miothealth-onebox' / 'xiaomihome'`（sid 集合）。
- `plantHealthCookie(String, MiAccessToken)`：
  - 域名：`.st.iot.home.mi.com`、`.account.xiaomi.com`、`.watch.iot.mi.com`、`.st-watch.iot.mi.com`、`.dev.fe.home.mi.com`、`.hlth.io.mi.com`、`.staging-hlth.io.mi.com`
  - cookie 格式串：`'locale=%s'`、`'cUserId=%s'`、`'%s_serviceToken=%s'`、`'serviceToken=%s'`（日志 `plant cookie success`）
- `plantMiHomeLoginCookie`：域 `.home.mi.com`，cookie `serviceToken=%s`、`cUserId=%s`。
- `plantPrivateCookie`：域 `.wear.mi.com.internal.yrn.net`，cookie `serviceToken=%s`、`cUserId=%s`、`ssecurity=%s`、`updateTime=`；并向 RN 发 `USER_ACCOUNT_SERVER_INFO_UPDATE`（日志 `plantPrivateCookie send token message to rn`）。
- `notifyAccountCookie()`：检查 `'miothealth'` 的 `|Cookie|`，分别调上述三个 plant 方法。
→ **hlth.io.mi.com 请求的凭据 = Cookie `serviceToken`（sid=miothealth 的 service token）+ `cUserId`；这也是 `eco/api_proxy` 通过 `@Secret()` 默认策略所需的 auth。**

### 6.2 SID 与 CookieFetcher（classes10 `AccountModule$Companion`）

`provideCookie()` 的 const-string 是 `'miothealth'`；`provideCookie$lambda$0` 把 `'cUserId'`、`'serviceToken'` 放进 Map（来源 `MiAccessToken`）。→ 原生端也是 **sid=miothealth**，与 Lead 结论一致。

### 6.3 RN `getServiceTokenInfo`（classes3 `com.xiaomi.wearable.yrn.modules.CommonBridgeModule`）

`getServiceTokenInfo(Promise)` → 协程 `CommonBridgeModule$getServiceTokenInfo$1.invokeSuspend`：
1. `AccountManagerExtKt.getInstance(AccountManager.Companion)` → `AccountManager.isLogin()`
2. `AccountManagerExtKt.getInstance(TokenManager.Companion)` → **`TokenManager.getServiceToken(sid:String, forceLogin:Z, …): MiAccessToken`**（sid 实参在 lambda 中未直接出现字面量，外层方法/常量注入，见 Open gaps）
3. `MiAccessToken.getSecurity()` → `map.putString("ssecurity", …)`
4. `MiAccessToken.getServiceToken()` → `map.putString("serviceToken", …)`
5. `MiAccessToken.getTimeDiff()` → `map.putInt("timeDiff", …)`
- 失败路径 `$1$2` → `promise.reject(Exception('GET_SERVICE_TOKEN_INFO_ERROR'))`；未登录分支日志 `getServiceTokenInfo: account not login`。
`MiServiceTokenInfo`（[classes3] `com.xiaomi.youpin.login.entity.account.MiServiceTokenInfo`）字段：`sid, cUserId, serviceToken, ssecurity, timeDiff, domain`（构造器 5×String + long；toString 同）。
→ **RN 拿到的 ssecurity/serviceToken 即 sid=miothealth 的 TokenManager 结果；`serviceLogin(sid=miothealth)` 的 serviceToken+ssecurity 可直接用于 eco/api_proxy 的前端签名**（前端 JS 已由 Lead 团队逐字节验证）。

### 6.4 uid（Q1）

- `MiAccessToken`（[classes10]，`com/xiaomi/fitness/account/token/MiAccessToken`）构造器 `(String userId, String cUserId, String security, String serviceToken, long timeDiff, boolean updated)`；getter：`getUserId()/getCUserId()/getSecurity()/getServiceToken()/getTimeDiff()/getUpdated()/isExpires()`。
- `AccountManager`（[classes10]）：`getUserId(): String`、`getMiAccountCoreInfoSync(Z): AccountCoreInfo`；`AccountCoreInfo` 有 `getUserId()/getUserName()/getNickName()/getRegion()/…`（无 cUserId 字段）。
- `LoginMiAccount`（[classes3] youpin login SDK）：字段 `userId, passToken, isSystemAccount, serviceTokens`，`getUserId()`。
→ RN`LoginUser.userId` 的原生对应是 **`MiAccessToken.userId`（Xiaomi userId，数字串）**，不是 `cUserId`（cUserId 单独用于 cookie）。两者都来自登录结果（AccountManager/TokenManager），登录后即可获得；`LoginUser` 是 JS 侧对象，dex 无同类。

---

## 7. 记录 / 分页 DTO（Q4/记录形状，Observation）

- `BodyComposition` 枚举（[classes10] `com.xiaomi.fit.fitness.export.data.aggregation.record.BodyComposition`）：`WEIGHT, BODY_FAT_RATE, MOISTURE_RATE, BONE_MASS, BASAL_METABOLISM, MUSCLE_RATE, PROTEIN_RATE, VISCERAL_FAT`（字段序即 enum 常量表）。
- `VirtualAccount`（[classes11] `electronic/scale/export/bean/VirtualAccount`，67 方法，`eco/scale/account/list` 的 result 元素）：字段/getter：`did(String), model(String), uid(long), accountId(long), name(String), icon(String), isDel(bool), isSyn(bool), type(int), bodyComposition(String), createDate(String), createTime(long), creationTime(long), sn(String), dataVersion(int), sex(int), birth(long), height(int), weightTarget(float)`。
- `WeightRecord`（[classes11] `electronic/scale/export/bean/WeightRecord`）：`id(int), createTime(String), weight(float)`（轻量记录）。
- `WeightAccount`：`uid(String), icon(String), isVirtual(int)`；`WeightAccountList`/`WeightInfoAccount`/`Detail`/`ShareAccount` 等共享成员 DTO 同包。
- 本地 Room 表 `virtual_account`（[classes11] idx8437 CREATE TABLE 逐字）：
  `did TEXT NOT NULL, model TEXT NOT NULL, uid INTEGER NOT NULL, accountId INTEGER NOT NULL, name TEXT NOT NULL, icon TEXT NOT NULL, isDel INTEGER NOT NULL, isSyn INTEGER NOT NULL, type INTEGER NOT NULL, bodyComposition TEXT NOT NULL, createDate TEXT NOT NULL, createTime INTEGER NOT NULL, sn TEXT NOT NULL DEFAULT '', dataVersion INTEGER NOT NULL DEFAULT 0, sex INTEGER NOT NULL DEFAULT -1, birth INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0, weightTarget REAL NOT NULL DEFAULT 0, PRIMARY KEY(createTime)`。
- `WeightRepository`（[classes13]）`getBodyCompositionList(BodyComposition, J, I, I, I)` 等 → 本地 Room（const-string `'record'/'composition'/'compositions'`，返回 `Map<Long, List<WeightItem>>`）；远端同步在 `ScaleRequest` 层。
- `WeightManager`（[classes11]）`addBodyCompositionToDb*`/`handleUploadBodyComposition`/`handleBackgroundUploadBodyComposition` 负责写库/上报（日志 `handleWeight addBodyCompositionToDb success`、`handleUploadBodyComposition data success`）。

### 7.1 `record/list` 与 `record/pull` 的差异（Q4，Observation）

| | `eco/scale/record/list` | `eco/scale/record/pull` | `eco/common/scale/pull` |
|---|---|---|---|
| 入口 | 同一 POST eco/api_proxy | 同一 | 同一 |
| params | `{accountId, page, pageSize}` | `{uid, did, pageSize, createTime}` | `{uid, did, model, pageSize, createTime}` |
| 原生调用 | `MainViewModel.getWeightRecords(accountId, page, pageSize)` | `MainViewModel.pullRecord(uid:J, did:String, pageSize:I, createTime:J)` | `ScaleRequest.pullRecordCommon` |
| 语义 | 按 accountId 的页码分页（全部记录页） | 按 createTime 游标倒序拉取（增量/增量同步） | 同 pull + 设备 model（跨型号通用） |
| 备注 | pagesize 无上限字面量；JS 侧实际用 pageSize=200 | 同左 | 同左 |

其他体脂读取路径：`FitnessApiService`（[classes10]，base `app/v1/`）有 `data/get_fitness_data_by_time`、`data/get_fitness_data_by_watermark`、`data/get_latest_fitness_data`、`data/get_aggregated_fitness_data_by_time`、`statistics/get_stat_data_by_time` 等通用健康数据接口；但**体成分是 scale/eco 域数据**，dex 中体成分相关调用全部走 `ScaleRequest`→eco_api（未发现 `data/get_*` 直接取 bodyFatRate 的调用）。`/app/v2/plugin/get_protocol`、`/app/service/getappconfig` 等 `app/` 字面量仅少量存在，属旧/米家插件协议，不用于列表。

---

## 8. S800 / 插件 id 映射（Q2 结论）

- 全 dex 检索 `S800|s800|xmtzc|eight-electrode|eightelectrode|EightElectrode|body_composition|bodycomposition`：**无命中**（`q.py` regex 覆盖 742,981 条字符串）。`八电极` 仅出现在 BLE 计算/日志类：classes7 `VA八电极秤` 系列、classes9 `八电极数据计算 calcEightData`/`calcEightDataNotChangeRes`、classes5 `当前设备不是八电极设备`。
- `assets/config/product_zh_cn.json`（1247B）仅含 `model:"xiaomi.watch.mars", pd_id:1711, pd_name:"小米手表", …`；`assets/server_config/zh_cn.json` 仅国家→machineCode（sg/cn/de/us…）。
- 因此 S800 的 `model → pluginId/packageId` 映射**不在 dex**：体脂秤产品配置由 `product/get_config_info_by_category`（`category='health_equipment,sport_equipment'`，body 含 `last_modify_time/app_platform/app_version`）服务端动态下发；S800 的 RN 页面/插件 id 位于 RN 插件 bundle（assets/内置 bundle，属其他同学范围）或服务端配置。

---

## 9. 观测 vs 推断

**观测（Observed，可直接引用）**：上面所有类/方法/注解/const-string/CRUD 名称、URL 片段、cookie 域名/名字、DTO 字段、Room 表定义、调用方列表。
**推断（Inferred，高置信）**：
1. `@Lb8b` 标记 POST 的 body/form 编码（所有 @Lamj 均带，@Ly3c 不带）。
2. `eco/api_proxy` 最终请求体 = `{"eco_api": <name>, "params": <JSON 字符串>}`（const-string 键 + Map 构造 + JS 已验证）。
3. `seed/get_third_device_source_list` 响应 list 的条目经 Gson 解析到 `ThirdPartyDevice`/`Device`（`ThirdPartyDeviceManagerImpl` 的 `requestDeviceList`/`toDeviceInfo`/`toDeviceModel` 方法体引用）。
4. `EcoDeviceManagerComponent.requestDeviceWhenMainResume` 的 diffTime 阈值为固定毫秒常量（具体值未静态读取）。

**Open gaps**：
- 具体签名实现类未定位（应在 classes12 的 `interceptor/*` 或某 `HttpBeforeHandler` 实现；`ApiHolder/HttpConfig/HttpHandlerInterceptor` 只定义了插入点）。前端 JS 的 RC4+base64(_sign) 已由 Lead 团队验证，可直接复用前端算法。
- `TokenManager.getServiceToken` 调用点的 sid 字面量未在 lambda 中出现（推测外层传入或全局常量；`AccountModule.provideCookie` 已证 miothealth）。
- “其他设备”卡片展示逻辑在 `DeviceTabParentFragmentV4`/RN 页面，dex 只到 loader 触发；`其他设备` 文案不在 dex。
- S800→pluginId 映射不在 dex（见第 8 节）。
- pageSize 上限无静态常量；JS 实际用 200。

---

## 10. 给“定时抓取脚本”的可用结论

最小请求（与 Lead 已验证一致）：
```
POST https://hlth.io.mi.com/app/v1/eco/api_proxy
Cookie: serviceToken=<sid=miothealth 的 token>; cUserId=<cUserId>   # 以及前端签名头/参数
Body: {"eco_api":"eco/scale/record/pull","params":"{\"uid\":<MiAccessToken.userId>,\"did\":\"0\",\"pageSize\":200,\"createTime\":<ms>}"}
```
- 翻页：`record/pull` 用返回的 createTime 游标倒序；`record/list` 用 `accountId/page/pageSize`。
- `account/list` 先取虚拟成员（`{did,uid}`），再按 accountId 过滤 pull 结果（HE-1828 结论）。
- 保存原始响应便于对字段（`result` 为 JSON 字符串，需二次解析）。

---

## 11. 证据文件清单（全部在 `work/dex/`）

```
strings/classesN.txt            742,981 条字符串（idx<TAB>value）
classes/classesN.cls.txt        类名/父类/方法数
scan_urls.json / scan_paths.json
strings_index.json
services.json / services.txt / services_summary.txt
svc_all.txt                     116 个带 @BaseUrl 的服务接口速查表
dump_net.txt                    网络框架类 dump（354 行）
dump_q.txt / dump_wr.txt / dump_scale.txt
dump_tp.txt                     第三方设备 DTO/service dump
dump_auth.txt                   cookie/token/BaseData dump（353 行）
dump_b.txt                      ThirdPartyDevice/DeviceInfo/CommonBridgeModule dump（520 行）
dump_bean.txt                   Device/DeviceInfo 基类字段
dump_bc.txt                     BodyComposition/WeightItem/WeightRepository/ScaleRequest
dump_uid.txt / dump_uid2.txt    AccountModule/MiAccessToken/AccountCoreInfo/TokenManager
dump_va.txt                     VirtualAccount DTO
hits/*.txt                      关键词命中（bodyComposition/八电极/体脂…）
dexlib.py extract_strings.py q.py scan_urls_dex.py scan_paths.py scan_services.py
svc_table.py dump_class2.py find_callers.py resolve_idx.py
```

复现入口：
```
$env:PYTHONIOENCODING="utf-8"
python q.py "eco/scale/record" -c
python dump_class2.py 'Lcom/xiaomi/fitness/electronic/scale/request/ScaleRequest;'
python find_callers.py "electronic/scale/request/ScaleRequest" "pullRecord|getWeightRecords"
python svc_table.py   # 生成 svc_all.txt
```
