# Mi Health 3.61.0 (`com.mi.health`) — RN / SkyTree / Plugin bundles & the 八电极体脂秤 S800 body-composition data path

**Task:** find the API(s) behind 设备 tab → 其他设备 → 米家八电极体脂秤 S800 card → RN remote page → 历史数据 → 全部记录 → paginated body-composition records.
**Author:** rn-analyst. **Scope:** static analysis of `3.61.0_20261104104057_mihealth.apk` + its extracted `assets/`.
**Conventions:** **[OBS]** = directly observed bytes/string/code (file + offset/line given). **[INF]** = inference.
All helper scripts are in `work\rn\*.py`; extracted artifacts in `work\rn\extracted\`, `work\rn\dex_strings\`.

---

## 0. TL;DR

| # | Question | Answer | Confidence |
|---|---|---|---|
| 1 | What are the bundles? | 3 different frameworks: (a) `sdk.bundle` = Mijia **miot-sdk** (plain JS/Metro, 4,740 modules); (b) `app.bundle` = Mijia plugin project ZIP (`app.js` plain JS, `app.hbc` Hermes, `app.kbc` custom bytecode) for project `com.xiaomi.smartpanel`; (c) `builtin_bundles_skytree/*.zip` = SkyTree/YRN bundles (plain JS `index.android.bundle` + localized strings + res) for modules 101/102/103/111/143/182. | high |
| 2 | API paths & hosts in JS | Only one body-fat API family: `POST /app/v1/eco/api_proxy {eco_api, params}` on `https://hlth.io.mi.com`, with `eco_api = eco/scale/record/pull` (paginated body composition) + 5 eco/scale/account/* calls, all in one module `he-1828` of the health bundle. No direct-REST body-fat path anywhere in JS. | high |
| 3 | Remote bundle loading | SkyTree biz-bundle mechanism: registry `https://atlas.nr.mi.com/skytree/atlas/getBundle`; CDN `https://skytree.gnr.mi-img.com/skytree/<32HEX>_<epochms>.zip`; bundles keyed by `moduleName/moduleId/moduleMapVersion/version/md5`, installed by native `WearableNetFrameProvider#downloadBundle` / `getNetBundleInfo`, loaded by `convertSkyTreeBundleToYRN`. A Mijia-plugin container (`PluginRNActivity`) is also present. The S800 page JS is **not in the APK**; it is fetched at runtime. | high (mechanism) / med (which container S800 uses) |
| 4 | Auth/header logic | Everything goes through a shared signed/encrypted envelope: `Cookie: serviceToken=<v>; cUserId=<v>; locale=<v>`; body `{data:<RC4(JSON)>, rc4_hash__:<sha1>, signature:<sha1>, _nonce:<b64>}` form-encoded; key = `b64(sha256(b64dec(ssecurity)||b64dec(nonce)))`; RC4 drops 1024 bytes; 12-byte nonce = 8 random + uint32BE(epochMinutes). | high |
| 5 | Record shape | Cloud: camelCase `bodyFatRate, muscleMass, visceralFat, basalMetabolism, boneMass, moistureRate, proteinRate, bmi, bodyAge, bodyScore, whr, somatotype, bodyShape, heartRate, 12 impedances lfz12..lfz34/hfz12..hfz34, 5×(limb/trunk muscle+fat mass/level/balance), weightControl/muscleControl/fatControl`. Native DB: `claim_record`, `weight_item_record`, `virtual_account` schemas. BLE: protobuf `BodyComposition`/`Bodyres_t`/`usr_bia_t`. | high |

**Bottom line for the user's flow:** the S800 detail page is a *remote* bundle (SkyTree or Mijia plugin) that is downloaded after the device is opened; the shipped APK only contains the **native eco scale SDK** (BLE, kalman, 八电极 math, claim queue) and the **proxy API** (`/app/v1/eco/api_proxy`) it uses. The body-composition record API is therefore **`eco/scale/record/pull`** (observed in shipped JS) with **`eco/scale/record/list` / `claimList` / `add` / `delete*`** existing in the native eco SDK (DEX) — and the remote page most likely calls them either directly through the same `eco_api_proxy` proxy or through a native bridge.

---

## 1. Bundle inventory and formats [OBS]

### 1.1 `assets/plugin_rn_sdk/sdk.bundle` — Mijia miot-sdk
- **10,538,603 B**, plain JavaScript Metro bundle (starts `var __BUNDLE_START_TIME__=this.n...`), modules of the form
  `__d(function (global, _$$_REQUIRE, _$$_IMPORT_DEFAULT, _$$_IMPORT_ALL, module, exports, _dependencyMap) {...}, <id>, [deps], "<path>");`
- 4,740 modules (4,712 numeric lib modules + 28 hashed project modules). Module paths: `miot-sdk/ui/...`, `miot-sdk/service/apiRepo.js`, `miot-sdk/service/smarthome.js`.
- Ends with `/**MIOT_SDK_LOADER**/__r(10120);__r(10290);`.
- Key module: `miot-sdk/service/apiRepo.js` (id **10764**, bytes 1,554,887–1,567,349) = `var API_REPO = { '/v2/...': {}, '/home/...': {}, ... }` — a ~300-path **whitelist** of Mijia-cloud APIs (`/home/device_list`, `/user/get_user_device_data`, `/miotspec/prop/get`, `/v2/device/set_props`, `/v2/user/{create,update,remove,get}_member`, …). **No scale/bodyfat/health path in it** [OBS].
- Plugin project modules (hashed ids) include `package.json` of project `miwear` and an **eco scale service** (see §5.4).

### 1.2 `assets/plugin/app.bundle` — Mijia plugin project package
- **822,496 B ZIP** (PK). Contents: `app.js` 219,156 B (plain webpack JS), `app.js.map` 311,481 B, `app.hbc` 164,276 B (Hermes bytecode, magic `c6 1f bc 03`), `app.kbc` 126,928 B (custom bytecode, head `01 fb 0b 16` + `internal://&__webpack_module`), `package.json`:
  `{"name":"com.xiaomi.smartpanel","main":"index.js","private":true,"version":"1"}`.
- `app.js` contains `;// CONCATENATED MODULE: ./miot-sdk/projects/com.xiaomi.smartpanel/index.js` and simple demo UI code ⇒ it is a **Mijia plugin project** (a demo "smart panel"), **not** the S800 page [OBS].

### 1.3 `assets/builtin_bundles_skytree/*.zip` — SkyTree (YRN) bundles
Each zip stores one **uncompressed tar** `*.android.tar`; each tar contains `index.android.bundle` (plain JS), `com.config.json` (or `buz.config.json`), `res/localizedString/{zh-CN,zh-TW,zh-HK,en-US,bo,ug}.json`, drawables. Extract: `work\rn\extracted\skytree_<name>\`.

| zip | tar (bytes) | index.android.bundle | moduleIDPrefix | moduleId | pack_version |
|---|---|---|---|---|---|
| common | base.android.tar 6,768,640 | 6,515,717 B plain JS | `comm` | 101 | 11.302.31 |
| init | init.android.tar 112,640 | 102,127 B | `i` | 102 | 10.343.42 |
| dial | dial.android.tar 7,516,160 | 7,207,041 B | `di` | 103 | 11.301.23 |
| sport | sport.android.tar 8,867,840 | 8,240,984 B | `sp` | 111 | 11.302.35 |
| mine | mine.android.tar 5,939,200 | 5,771,457 B | `mi` | 143 | 11.300.19 |
| health | **13,230,080** | **11,335,114 B** | `he` | 182 | 11.306.10 |

Config keys: `moduleIDPrefix`, `SDKVersion:"610"`, `moduleIdMapVersion:"9"` (init "6"), `maxNativeApiLevel` 7–13, `maxCommonApiLevel` 1–5, `YRN_BUNDLE_MD5`, `pack_version`, `buildnum` (health 40407), `maketime` (health 20261102140246), `resourceDirectory:"app/assets/<name>/res"`.
All six are **plain JS** (no Hermes) — SkyTree runs on **YRN**, Xiaomi's V8-based RN engine (`libyrnbridge.so`, `libyrnv8executor.so`, `libv8android.so`, `libv8executor.so`; classes `com.xiaomi.yrn.*`, `com.xiaomi.yrn.view.ui.YRNActivity`).

### 1.4 `assets/builtin_bundles_skytree/bundleInfo.json` [OBS]
6 entries, fields: `contentHash, buildNum, urlmd5, id, residentMemory, moduleName, branch, maxNativeApiLevel, maxCommonJSApiLevel, originalClassName:"BundleInfo", bundlePath, version, updateType, noticeStrategy, moduleMapVersion, moduleId, makeTime, urlcdn`.
The `urlcdn` values are the remote-delivery template (see §3):
`https://skytree.gnr.mi-img.com/skytree/<32-HEX>_<epochms>.zip` — health `ACACBC996A2C18DF4AF2A2987934690C_1793599594932`, common `D6073112E1336DEE251FB89BE3E6A141_1793285677229`, sport `B214135508A5A619451E4B1ED670ECA4_1793287200515`, mine `FCBA32FB3BA596F8CCDCA7CEC7F76D50_1793090996258`, dial `B21D1D9AB7F6E28326A8771377D84273_1793173835483`, init `017E12325BBE80797E8AF76B91008D1F_1765281197396`.

### 1.5 Other relevant APK assets (not yet fully mined)
`assets/schemas.zip` 228,840 B (unexplored; likely JSON schemas), `assets/server_config/servers.json` (region→machineCode map, 277 KB; values `de|sg|us` ⇒ regional hosts), `assets/server_config/zh_cn.json` (18,217 B, GBK tail), `assets/config/product_zh_cn.json` (only watch products), `assets/ae/res.zip`, `assets/model/sleepnet.mdl`.

---

## 2. API surface found in JavaScript [OBS]

### 2.1 The single body-composition transport: `/app/v1/eco/api_proxy`
Found in **`skytree_health` bundle module `he-1828`**, byte range 10,129,258–10,436,665 (14,407 B), saved as `work\rn\mod_he-1828.js`, log tag `[VM-RN]`. Full request helper:

```js
var p="[VM-RN]", _="/app/v1/eco/api_proxy";
function y(t,n){ ... // t = eco_api path, n = params
  u.RRLog.info(p,"postEcoApi request",{ecoApi:t,params:n,transport:"http"});
  s.default.post(_, {eco_api:t, params:JSON.stringify(n)},
                 {hostType:c.HostType.hlth, enableCaseConversion:!1});
  h = f.result;                       // axios response
  return JSON.parse(h.resp);          // result.resp is a JSON *string*
}
```

`eco_api` values used from this module:

| eco_api | params | purpose |
|---|---|---|
| `eco/scale/account/add` | `{name, icon:"", sex, height, weightTarget, birth}` | create virtual member |
| `eco/scale/account/list` | `{uid:Number}` | list members |
| `eco/scale/account/remove` | `{accountId:String}` | delete member |
| `eco/scale/account/update` | `{accountId,name,icon,sex,height,weightTarget,birth}` | update member |
| `eco/scale/account/updateWeight` | `{uid:Number, weightTarget:String, accountId}` | target weight |
| **`eco/scale/record/pull`** | `{uid:Number, did:"0", pageSize:200, createTime:<ms Date.now()>}` | **paginated body-composition records** |

**Pagination algorithm** (he-1828, verbatim logic): request with `createTime = Date.now()`; continue while
`200===code && data.length!==0`; next cursor = `Math.min(createTime)` (ms) of the returned page; stop when
`minCreatedTime >= previousCursor` or `data.length < pageSize` (default 200).

This is the only place in the whole APK where the literal `eco_api` appears in JS (1 occurrence in all bundles) and `eco/` appears only 7× in the health bundle. `eco/scale/record/list` is **absent from all JS**.

### 2.2 Health bundle REST endpoints (93 distinct paths, all `HostType.hlth`)
`/app/v1/` (71 unique) + `/healthapp/` (22). Grouped:

- **aggregation/data**: `/app/v1/data/get_aggregated_fitness_data_by_time` (he-603, he-716), `/app/v1/data/{up_aggregated_fitness_data, get_project_data_by_time, up_project_data, delete_project_data, get_diet_records_by_time, up_diet_records, delete_diet_records, get_sleep_insight_data, get_latest_sport_record, last_achieved_goals, reoprt_achieved_goals}`, `/app/v1/user/{get_project_user_info,set_project_user_info}`, `/app/v1/geo/regeo_auth`, `/app/v1/service/gen_research_upload_url`
- **statistics** (he-1375): `/app/v1/statistics/{get_stat_data_by_time, get_stat_data_by_watermark, get_max_watermark, trigger_stat, delete_stat_data, batch_get_diet_summary, get_sleep_users_distribution, get_ai_chat_conversation_list, get_ai_chat_message_list, delete_conversation}`; body `{tag, key, nextKey, startTime, endTime, limit:50, reverse:true}`
- **health summary** (he-856, he-948): `/app/v1/health_summary/{get_all_trend, get_picked_summary, get_summary_by_model, ai_qa, get_question_list}`, SSE `/sse/health_summary/{ai_qa_stream, ai_task_stream}`
- **plan/goal**: `/app/v1/plan/goal/weight_loss/{build,dates,detail,estimate_date,estimate_weight,panel,steers,stop,summary,today,update,user/get,user/profile_set}`, `/app/v1/plan/goal/weight/{user_build_fast,user_card}`, `/app/v1/plan/{last_doing,template/user_detail,ops_recommend/position/get}`
- **diet**: `/app/v1/diet/{food_collect, food_collect_cancel, food_collect_list, food_collected, food_detail, food_list, food_search, user_consumption}`
- **misc**: `/app/v1/{auth/get_third_party, auth/third_party, behaviorLog/factors/catalog, product/get_config_info_by_category, smart_running/improve/summary, source/get_third_device_source_list, sport_recommend/get_sport_recommend_app, version/check_upgrade}`, `/app/v1/sleepInterventionPlans/{create,get,list,tasks/list,tasks/update,update}`
- **Mi Health server** `/healthapp/`: `health_plan/*` (add_plan, add_task, batch_delete_task, edit_task, get_ai_plan, get_history_plan_list, get_plan, get_plan_progress, get_plan_task_list, get_task_list, update_plan, upload_punch_data), `setting/{get_user_app_setting, get_user_app_switch, get_user_device_settings, set_user_device_settings}`, `user/{get_miot_user_profile, set_miot_user_profile}`, `third/alipay/aq/get_faq`, `track/add_event_logging`, `weather/get_weather_info_v3`.

None of these carry body-composition fields.

**Important negative result:** the health bundle's own "全部记录" page is **local-DB only** — route `miwear/health/WeightAllRecords` → module **he-2173**:
```js
ft = (0,O.getDailyRepo)(E.IDailyType.WEIGHT);
ft.getRecordList({startTime:0, endTime:<now unix>, limit:60, sort:"descend"})
   .then(({code,data}) => ...);              // data[i] = {sid, value:{time,...}}
DeviceEventEmitter.addListener(w.DATABASE_SYNC_EVENT, ...);
// row tap: t.push(HealthRoutes.WeightRecordDetail, {record:JSON.stringify(n.value), sid:n.sid, time:String(n.value.time)})
```
`getDailyRepo` sits on the `YRNDatabaseModule` native data layer ⇒ shipped weight/body records are read from the local Room DB, not from HTTP.

### 2.3 Mijia plugin / miot endpoints present in DEX [OBS]
`/v2/plugin/{fetch_plugin, fetch_sdk, get_config_info_new, get_support_models}`, `/plugin/{get_config_info_new, update_plugin}`, `/app/v2/plugin/get_protocol`, `/api/v4/detail/config` (classes3.dex). Plugin runtime: `com.miot.rn.plugin.activity.PluginRNActivity` (declared in AndroidManifest), `com.xiaomi.smarthome.framework.plugin.rn.*`, plugin fs `plugin/install/rn/std-plugin/data`, `cloud_rn_plugin_cache_v1`.
Mijia whitelist (`sdk.bundle` apiRepo) has **no** health/scale endpoint; Mijia plugin pages reach Mi Health proxy via native bridge (§5.4).

### 2.4 Hosts [OBS]
`skytree_common` module 1248 `HostMap`: `HLTH` = `hlth.io.mi.com` / `pv.hlth.io.mi.com` / `staging-hlth.io.mi.com` / `onebox-hlth.io.mi.com`; `Watch` = `watch.iot.mi.com` (+pv/st/onebox); `WatchAppstore` = `watch-appstore.iot.mi.com`.
Other hosts observed: `region.hlth.io.mi.com` (applinks/HTML), `atlas.nr.mi.com` (skytree), `skytree.gnr.mi-img.com` (bundle CDN), `cdn.web-global.fds.api.mi-img.com`, `cdn.cnbj1.fds.api.mi-img.com`, `cdn.eco.mi.com` (eco scale assets), `res.eco.mi.com`, `api.io.mi.com` (+`pv.api.io.mi.com`, `shopapi.io.mi.com`), `region-cdn-fds.xiaomiwear.com`, `wear.mi.com.internal.yrn.net`, `account.xiaomi.com`, `m.mibi.mi.com`, `neptune.g.mi.com`, Sentry `*.sentry.d.xiaomi.net`.

---

## 3. How the remote S800 page is loaded (Q3) [OBS/INF]

### 3.1 SkyTree bundle pipeline (primary)
- **Registry URL** (full in `classes2.dex` string table): `https://atlas.nr.mi.com/skytree/atlas/getBundle`; path `/skytree/atlas/getBundle` also in classes3.dex.
- **Native classes** (classes3.dex): `com.xiaomi.wearable.yrn.provider.WearableNetBeanProvider$MiSkyTreeResponseInfoBean`, `…$MiSkyTreeResponseBundleInfoBean`, `WearableNetFrameProvider$downloadBundle$1`, `WearableNetFrameProvider$getNetBundleInfo$1`, `com.xiaomi.yrn.net.impl.YRNNetDefaultBeanImpl$MiSkyTreeResponseBean/BundleInfoBean/InfoBean`, `com.xiaomi.yrn.env.entity.bundleload.*` (`YRNBundleRequestBean`, `YRNBundleResponseBean`, `YRNBundleResponseInfoBean`, `load.YRNAssetsLoadRequest`, `load.YRNLoadRequest`, `load.YRNNetworkLoadRequest`).
- **Strings**: `convertSkyTreeBundleToYRN`, `YRNSkyTree`, `YRNSkyTreeFail`, `createBizInstance error: bizBundleInfo ==  null`, `createBizInstance error: module is `, `assetsBuiltinBundleJsonPath`, `builtin_bundles_skytree/bundleInfo.json`, `bundle.sign`, `bundle.cert`, `bundleFileName 不存在`, `bundle cool down`, `bundleIsMinify`, `urlcdn`, `urlmd5`.
- **CDN template**: `https://skytree.gnr.mi-img.com/skytree/<32HEX>_<epochms>.zip` (`bundleInfo.json` `urlcdn`; the hex is the bundle `id`/md5, epoch ms is the build time).
- **Registry semantics** (inferred from `BundleInfo` bean fields + JS side): each bundle is identified by `moduleName` (comm/i/he/di/sp/mi), numeric `moduleId` (101/102/103/111/143/182), `version` (semver + `maketime`), `moduleMapVersion`, `contentHash`, and `urlmd5`; native `getNetBundleInfo` asks the atlas for updated entries, compares md5, downloads `urlcdn` into the app's bundle dir, verifies `bundle.sign`/`bundle.cert`, then loads via `convertSkyTreeBundleToYRN`.
- **JS side sees only paths, not the registry**: `CountryManager` (health) calls `NativeModules.LocaleAndLanguageModule` → `{areaId, globalAreaId, phoneSegment, areaName, defaultLanguage, fileUploadUrl, skytreeUrl, businessUrl}`, defaults `""`, tag `[CountryManager]`; `getRnBundleInfo()`/`getAllRnVersion()` map `getAllRnVersion()` rows by `module`; asset path `file://<splitBundlePath>/<module>/<md5>/res/<name>` (Android) with CDN fallback `https://region-cdn-fds.xiaomiwear.com/hlth-client-assets/<module>/<name>`.

**[INF]** The S800 page is delivered as one of these SkyTree "biz bundles" (a module beyond the 6 builtin ones, or a newer version of `health`), keyed by module id/name; the 6 builtin bundles are baked into the APK only to bootstrap the framework.

### 3.2 Mijia plugin path (alternative / complementary)
The APK also carries the **complete Mijia RN plugin runtime** (`PluginRNActivity` in Manifest; plugin `fetch_plugin` endpoints; plugin fs layout) and ships one demo plugin (`app.bundle`, project `com.xiaomi.smartpanel`). The S800 card could equally be a Mijia plugin page for the scale's device model; plugin pages get their device data via `miot-sdk` and their eco data via the Mi Health native bridge (§5.4). Which container launches is decided by native device-model config (not visible in these bundles).

### 3.3 Consequence
Neither the S800 page's JS nor its page-specific API list ships in the APK. What ships is: (a) the proxy transport (`/app/v1/eco/api_proxy`) + one consumer (`he-1828`), (b) the native eco scale SDK (BLE + claim queue + cloud client), (c) the SkyTree/plugin loaders. To capture the page itself one must fetch the bundle at runtime (per §3.1) or observe a device.

---

## 4. Auth / header / signing logic in JS (Q4) [OBS]

All HTTP in the SkyTree bundles goes through **one axios instance** = `skytree_common` module **1243** (16,168 B, `common_mod_1243.js`), with helpers:

| module | file | role |
|---|---|---|
| 1243 | `common_mod_1243.js` | axios request/response interceptor |
| 1244 | `common_mod_1244.js` | `convertToSnakeCase`/`convertToCamelCase` (+`toSnakeCase`/`toCamelCase`) |
| 1245 | `common_mod_1245.js` | `normalizePrecisionSafeFields`, `protectPrecisionSafeFieldsBeforeParse`, `stringifyRequestData` |
| 1246 | `common_mod_1246.js` | misc: `updateToken` (via `NativeModules.CommonBridgeModule.updateToken`), `getCurEnvDomainSid`, `uploadErrorToSomePlatform` |
| 1247 | `common_mod_1247.js` | `encryptParameters` / `decryptResponse` (RC4 + SHA1/SHA256) |
| 1248 | `common_mod_1248.js` | `HostType`, `HostMap`, `getHttpHost`, `getRegionCode`, `getBaseUrl` |
| 1249 | `common_mod_1249.js` | `UserServerInfo`, `CookieManager`, `getCurrentLanguageCode`, `getServerTime[Diff]` |
| 1250 | `common_mod_1250.js` | `Nonce` |
| 586 | `common_mod_586.js` | `qs` (`{formats, parse, stringify}`) |

### 4.1 Request interceptor (verbatim, key lines)
```js
T = new w.Nonce(getServerTimeDiff());
C = _.data ?? _.params;
S = !1 !== _.options?.enableCaseConversion;
N = S ? l.convertToSnakeCase(C) : C;
D = f.normalizePrecisionSafeFields(_.options?.precisionSafeFields?.request, S);
F = await y.UserServerInfo.getInstance().serviceInfo();        // {serviceToken, ssecurity, cUserId}
I = F.cUserId, U = F.serviceToken, B = F.ssecurity;
G = _.options?.encryptExcludePathPrefix;  q = _.url;  G && (q = _.url.replace(G,""));
z = !1 === _.options?.encrypt ? N
    : h.encryptParameters({data: f.stringifyRequestData(N,D)}, q, _.method ?? "", T.base64Nonce, B);
_._nonce = T.base64Nonce; _._ssecurity = B;
_.data ? (_.data = u.default.stringify(z),
          _.headers = {..._.headers, "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8", ..._.options?.headers})
        : (_.params = z);
Q = ""; U && (Q += "serviceToken="+U+"; "); I && (Q += "cUserId="+I+"; ");
(X = y.getCurrentLanguageCode()) && (Q += "locale="+X);
Q && (_.headers = Object.assign({}, _.headers ?? {}, {Cookie: Q}));
_.baseURL = await v.getBaseUrl(_.options?.hostType ?? v.HostType.hlth);
```

### 4.2 Signing / encryption (module 1247, verbatim core)
```js
p.RC4_DROP_LENGTH = 1024;                       // RC4 keystream: first 1024 bytes discarded
class p { constructor(n){ this.key = Buffer.from(n,"base64");
  if (this.key.length !== 32) throw new Error("Key length must be 32 bytes");
  this.rc4Instance = new RC4(this.key); this.rc4Instance.encrypt(Buffer.alloc(1024)); }
  encrypt(t){ return this.rc4Instance.encrypt(Buffer.from(t,"utf8")).toString("base64"); }
  decrypt(t){ return this.rc4Instance.encrypt(Buffer.from(t,"base64")).toString("utf8"); } }

function l(t,n){ // t = ssecurity(b64), n = nonce(b64)
  var s = Base64.parse(t), c = Base64.parse(n),
      o = WordArray.create(s.words.concat(c.words), s.sigBytes+c.sigBytes);
  return Base64.stringify(SHA256(o)); }        // key = b64(sha256(b64dec(ssecurity) || b64dec(nonce)))

function v(t,n,s,c){ // t=method, n=url, s=params, c=key
  if (!c) throw new Error("Security key is required");
  var o = Object.keys(s).sort().map(t => t+"="+s[t]).join("&"),
      f = [t.toUpperCase(), n, o, c].join("&");
  return Base64.stringify(SHA1(f)); }

encryptParameters = function(t,n,s,c,o){         // (params, url, method, nonce, ssecurity)
  var u = l(o,c), f = new p(u);
  var y = v(s,n,t,u);                            // sign plaintext params
  var b = {...t, rc4_hash__: y};                 // add rc4_hash__
  var j = {}; Object.keys(b).sort().forEach(k => { j[k] = typeof b[k]==="string" ? f.encrypt(b[k]) : b[k]; });
  var O = v(s,n,j,u);                            // sign encrypted map
  j.signature = O; j._nonce = c; return j;
};
decryptResponse = function(t,n,s){ return new p(l(s,n)).decrypt(t); };
```

⇒ wire body for `POST /app/v1/eco/api_proxy`:
`data=<b64 RC4 of '{"eco_api":"eco/scale/record/pull","params":"{...}"}'>  &  rc4_hash__=<b64 sha1>  &  signature=<b64 sha1>  &  _nonce=<b64(12B)>`
with `Cookie: serviceToken=...; cUserId=...; locale=...` (`enableCaseConversion:false` only affects a body-case transform, not encryption).

### 4.3 Nonce (module 1250, verbatim)
```js
new Nonce(timeDiff) { var c=(Date.now()+timeDiff)/1e3; this._base64Nonce = this.generateRawNonceWithTI(c); }
generateRawNonceWithTI(t){ var n = 4294967296*Math.floor(4294967296*Math.random())+Math.floor(4294967296*Math.random()),
  o = Math.floor(t/60), u = Buffer.alloc(12);
  u.writeUInt32BE(n/4294967296>>>0,0); u.writeUInt32BE(n>>>0,4); u.writeUInt32BE(o,8);
  return u.toString("base64"); }
```
⇒ 12-byte nonce = 8 random bytes + uint32BE(epoch minutes); time offset comes from server `Date` header (`setServerTimeDiffWithResponse`).

### 4.4 User/server identity (module 1249)
`UserServerInfo` holds `{serviceToken, ssecurity, cUserId}`; filled by (in order):
1. `DeviceEventEmitter.addListener("USER_ACCOUNT_SERVER_INFO_UPDATE", …)` (native pushes),
2. native `getServiceTokenInfo()`,
3. GET `https://wear.mi.com.internal.yrn.net` → `{ssecurity:{value}, serviceToken:{value}, cUserId:{value}}`.
Token refresh on 401-ish responses: logs `登录已过期:[url]，尝试刷新token并重试请求`, `serviceTokenResolver: 开始刷新token` / `更新token成功` / `刷新token失败`, `服务端token更新时间小于10秒，不刷新token`; refresh calls `updateToken(getServerTime())` then `LoginUser.getUserInfo()`.
Also present: `CheckServiceToken`→`getCookie('serviceToken',domain)`, `LoginFromJS/LoginFromNative/LoginByNativePassToken`, `prepareServiceToken` (reads cookie from `.shopapi.io.mi.com`), `preFixUA` (appends `;`+MakeTime+`;` to UA), `SaveUserId`, `isUserLogined`.

### 4.5 Response path
`decryptResponse(...)` → `JSON.parse` → `convertToCamelCase` (unconditional) ⇒ JS always sees camelCase fields; `precisionSafeFields.response` (not used by eco) would only affect listed keys.

### 4.6 Mijia plugin bridge
`sdk.bundle` project module `b4f412a06f01a4220c8788c32f7c567d`:
```js
requestData(url, params) { return new Promise((res,rej) => MiWearHost.requestUrl(url, params, (ok,r) => ok?res(r):rej(r))); }
requestApi(api, params)  { return this.requestData("eco/api_proxy", {eco_api: api, params: params}); }
```
⇒ plugin pages reach the same `/app/v1/eco/api_proxy` through the host, with the same native-side signing.

---

## 5. Body-composition record shape (Q5) [OBS]

### 5.1 Cloud response, raw server fields (from `normalizePullRecord` in `he-1828`)
`accountId, weight, createTime, bmi, bodyFatRate, lfz12, lfz13, lfz14, lfz23, lfz24, lfz34, hfz12, hfz13, hfz14, hfz23, hfz24, hfz34, moistureRate, boneMass, basalMetabolism, muscleMass, proteinMass, visceralFat, bodyMoistureMass, fatMass, limbsSkeletalMuscleIndex, leftUpperLimbMuscleMass, leftLowerLimbMuscleMass, rightUpperLimbMuscleMass, rightLowerLimbMuscleMass, trunkMuscleMass, leftUpperLimbFatMass, leftLowerLimbFatMass, rightUpperLimbFatMass, rightLowerLimbFatMass, trunkFatMass, leftUpperLimbMuscleMassLevel, leftLowerLimbMuscleMassLevel, rightUpperLimbMuscleMassLevel, rightLowerLimbMuscleMassLevel, trunkMuscleMassLevel, leftUpperLimbFatMassLevel, leftLowerLimbFatMassLevel, rightUpperLimbFatMassLevel, rightLowerLimbFatMassLevel, trunkFatMassLevel, upperLimbMuscleBalance, lowerLimbMuscleBalance, limbsMuscleBalance, upperLimbFatBalance, lowerLimbFatBalance, limbsFatBalance, somatotype, bodyShape, whr, heartRate, bodyScore, bodyAge, standardWeight, recommendedCaloriesIntake, weightControl, muscleControl, fatControl`.

Helper conversions observed: percentage = `Math.round(1000*mass/weight)/10`; `createTime` < 1e10 treated as seconds (×1000) or parsed as date string with `-`→`/`; `heartRate` → `bpm`.

### 5.2 Normalized JS record (`normalizePullRecord` output, camelCase after interceptor)
`{accountId, time, weight, bmi, body_fat_rate→bodyFatRate, measuring_impedance_1..12 (lfz12..lfz34,hfz12..hfz34), moistureRate, boneMass, boneRate, basalMetabolism, muscleRate, proteinRate, visceralFat, bodyMoistureMass, proteinMass, fatMass, muscleMass, limbsSkeletalMuscleIndex, leftUpperLimbMuscleMass…, trunkMuscleMass, leftUpperLimbFatMass…, trunkFatMass, *_muscle_level, *_fat_level, upperLimbMuscleBalance, lowerLimbMuscleBalance, limbsMuscleBalance, upperLimbFatBalance, lowerLimbFatBalance, limbsFatBalance, somatotype, bodyShape, whr, bpm, bodyScore, bodyAge, standardWeight, recommendedCaloriesIntake, weightControl, muscleControl, fatControl}`.

### 5.3 Native local schemas (DEX classes10/11, full strings)
**`claim_record`** (classes11) — offline claim queue of scale measurements:
```sql
CREATE TABLE IF NOT EXISTS `claim_record` (`did` TEXT NOT NULL, `type` TEXT NOT NULL, `weight` REAL NOT NULL,
 `unit` TEXT NOT NULL, `createTime` INTEGER NOT NULL, `heartRate` INTEGER NOT NULL,
 `lfz12` INTEGER NOT NULL, `lfz13` INTEGER NOT NULL, `lfz14` INTEGER NOT NULL,
 `lfz23` INTEGER NOT NULL, `lfz24` INTEGER NOT NULL, `lfz34` INTEGER NOT NULL,
 `hfz12` INTEGER NOT NULL, `hfz13` INTEGER NOT NULL, `hfz14` INTEGER NOT NULL,
 `hfz23` INTEGER NOT NULL, `hfz24` INTEGER NOT NULL, `hfz34` INTEGER NOT NULL,
 `id` INTEGER NOT NULL, `isCloud` INTEGER NOT NULL, PRIMARY KEY(`createTime`))
```
**`weight_item_record`** (classes10) — daily/aggregated weight+fat record (used by 全部记录):
```sql
CREATE TABLE IF NOT EXISTS `weight_item_record` (`key` TEXT NOT NULL, `sid` TEXT NOT NULL, `time` INTEGER NOT NULL,
 `zeroTimeIn0Tz` INTEGER NOT NULL, `weight` REAL NOT NULL, `bmi` REAL, `bodyFatRate` REAL, `moistureRate` REAL,
 `boneMass` REAL, `basalMetabolism` INTEGER, `muscleMass` REAL, `proteinRate` REAL, `visceralFat` REAL,
 `value` TEXT, `endTime` INTEGER NOT NULL, `zoneOffsetInSec` INTEGER NOT NULL, `zoneName` TEXT,
 `timeIn0Tz` INTEGER NOT NULL, `endTimeIn0Tz` INTEGER NOT NULL, `isUpload` INTEGER NOT NULL,
 `isDeleted` INTEGER NOT NULL, PRIMARY KEY(`key`, `sid`, `time`))
```
(`value TEXT` holds the full serialized record, cf. `WeightAllRecords` passing `record:JSON.stringify(value)`.)
**`virtual_account`** (classes10) — virtual/family member + its latest `bodyComposition` JSON:
```sql
CREATE TABLE IF NOT EXISTS `virtual_account` (`did` TEXT NOT NULL, `model` TEXT NOT NULL, `uid` INTEGER NOT NULL,
 `accountId` INTEGER NOT NULL, `name` TEXT NOT NULL, `icon` TEXT NOT NULL, `isDel` INTEGER NOT NULL,
 `isSyn` INTEGER NOT NULL, `type` INTEGER NOT NULL, `bodyComposition` TEXT NOT NULL, `createDate` TEXT NOT NULL,
 `createTime` INTEGER NOT NULL, `sn` TEXT NOT NULL DEFAULT '', `dataVersion` INTEGER NOT NULL DEFAULT 0,
 `sex` INTEGER NOT NULL DEFAULT -1, `birth` INTEGER NOT NULL DEFAULT 0, `height` INTEGER NOT NULL DEFAULT 0,
 `weightTarget` REAL NOT NULL DEFAULT 0, PRIMARY KEY(`createTime`))
```

### 5.4 BLE / native measurement payload
**Protobuf** embedded in `skytree_common` (protobufjs bundle, offsets 3,662,000–3,695,000) — scale message envelope with `oneOf payload`: `userInfo, delUserInfo, unbindUser, setupUnit, acquireUnit, switchMode, updateWeight, updateCctTime, updateInfantData, updateHistoryData, visitorData, bodyComposition, weighProcedure, bindUserInfo, userNum, childUserData, UserList` (`bodyComposition` = field 12).

```js
BodyComposition { 1: miid(miid_t), 2: uid(userid_t), 3: conflictFlag, 4: weight, 5: time,
                  6: lfRes(Bodyres_t), 7: hfRes(Bodyres_t), 8: usrBia(usr_bia_t),
                  9: heartRate, 10: unit }
Bodyres_t { bodyResZ12, bodyResZ13, bodyResZ14, bodyResZ23, bodyResZ24, bodyResZ34 }   // 6 low-freq impedances
usr_bia_t { bfp, slm, bwp, bmc, pm, bmr, bwm, bfm, ma, vfr, limbmi, sbc, bmi, bt, sbw, bs, whr, cin,
            lam, ram, llm, rlm, tm, lamP, ramP, llmP, rlmP, tmP, lamS, ramS, llmS, rlmS, tmS,
            lafm, rafm, llfm, rlfm, tfm, lafmP, rafmP, llfmP, rlfmP, tfmP, lafmS, rafmS, llfmS, rlfmS, tfmS,
            eoma, eoml, eomb, eofa, eofl, eofb, wc, fc, mc }
```
**Gson model strings** (classes9) — native measurement bean fields: `"LF_100K","LF_20K","LH_100K","LH_20K","RF_100K","RF_20K","RH_100K","T_100K","T_20K"` (4 limbs × 2 freqs + trunk), `"fatLF/fatLH/fatRF/fatRH/fatT"`, `"muscleLF/muscleLH/muscleRF/muscleRH/muscleT"`, `"last_res100_{left_arm,left_leg,right_arm,right_leg,trunk}"`, `"last_res20_*"`, `"last_origin_resistances"`, plus `"bodyFatRate","bmi","bmr","bodyAge","bodyShape","muscleMass","bone","lbm","edemaIndex","intracellularFluid","extracellularFluid","whr","somatotype","heartRate","fatGrade","isEightScaleSupportQuadMethod","isSupportWSPEight","hasGenerateEightByCategorySingleBle","eight_reason_mask","EightResistanceData"`, and `{"EightResistanceData": {"userIndex": ...}}` JSON.

### 5.5 The 八电极 (8-electrode) native SDK [OBS]
- Device domain = `eco` (`com.xiaomi.fitness.eco.*`, `EcoDevice`, `EcoLocalDevice`, DB `eco_device_db`, flavor `electronic-scale_chinaProductRelease`).
- Native log strings (classes5/7/9): `VA八电极秤 人体成分数据接收异常`, `VA八电极秤 存储数据 最终得到`, `VA八电极 没有存储数据`, `VA新方案 HMAC flags, isAppMetricSync=`, `kalman VA八电极秤 存储数据 最终得到`, `八电极数据计算 calcEightData`, `八电极秤端测量数据没有体脂率但有阻抗！`, `单蓝牙八电极阻抗调整`, `当前设备不是八电极设备`, `开始云验签绑定失败`, `同步体脂秤历史记录结果:`, `开始同步同步体脂秤数据`.
- Native pages/routes: `/electronic/activity/eight/weight`, `/electronic/activity/{user,user_add,userinfo,DataReport,VirtualMemberData}`, `/ecoDevice/activity/bind_eco`; assets `https://cdn.eco.mi.com/eco-static/scale/eight-electrode-operation/*` (pairing/weight guides); service class `EcoScaleService` in `sdk.bundle` (BLE packets `EcoBlePacketScaleDataID.{setUsrInfo,updateBaseWeight,changeMode}`, `EcoUserType={MAIN_USER:0,VIRTUAL_USER:1,SHARE_USER:2,VISITOR_USER:3}`, `EcoMode={MODE_BABY:1,MODE_BALANCED:2,MODE_NORMAL:3}`, `zk1..zk12` measurement values passed to native confirm pages).
- Eco API list in `classes11.dex`: `eco/api_proxy`, `eco/scale/account/{add,list,remove,update,updateWeight}`, `eco/scale/record/{add,claimList,delete,deleteByDidAndCreateTime,list,pull}`, `eco/common/scale/{add,batchAdd,pull}`, `eco/general-equipment/common/{device/add,sport/add}`.

---

## 6. User-flow mapping (设备 tab → … → 全部记录)

| Step | Where it lives | Evidence |
|---|---|---|
| 设备 tab | native | `navToDevicePage` → `com.xiaomi.fitness.main.MainActivity {key_tab:2}`; 设备管理 = `bg.device.title`; no 八电极 strings in bundle localizations |
| 其他设备 list | native + Mijia device list | `/v2/home/home_device_list`, `/home/device_list`; `com.xiaomi.fitness.mijia.sdk.*`, `miot-sdk` device list modules |
| 米家八电极体脂秤 S800 card | native device config; card opens a **remote page** | SkyTree biz-bundle loader (§3.1) / Mijia plugin container `PluginRNActivity` (§3.2) |
| RN remote page (detail/history) | downloaded at runtime; **not in APK** | §3 |
| 历史数据 / 全部记录 list | page-side; if it uses shipped code path alternatives: (a) `eco/scale/record/pull` via proxy, (b) native `eco/scale/record/list`, (c) local DB `weight_item_record` via `getDailyRepo` | `he-1828`, `classes11.dex`, `he-2173` |
| Record detail/report | local DB / page-side; `miwear/health/WeightRecordDetail` payload `{record,sid,time}` | he-2173, he-485 |

---

## 7. Confidence & gaps

**High confidence**
- Bundle formats/identities (§1) — verified by extraction.
- `POST https://hlth.io.mi.com/app/v1/eco/api_proxy` + `eco/scale/record/pull` + pagination algorithm + full record field list (§2.1, §5.1) — verbatim JS.
- Signing/encryption envelope, Cookie header, baseURL construction (§4) — verbatim JS.
- Local DB schemas and the 12-impedance/8-electrode native model (§5.3–5.5) — DEX string tables.
- SkyTree delivery: registry `atlas.nr.mi.com/skytree/atlas/getBundle`, CDN `skytree.gnr.mi-img.com/skytree/<hash>_<ts>.zip` (§3) — DEX + bundleInfo.json.

**Medium / open**
- Which container hosts the S800 page (SkyTree `YRNActivity` vs Mijia `PluginRNActivity`) and its `moduleId`/plugin id — requires runtime capture or the `getBundle` response.
- The exact request body/headers of `atlas.nr.mi.com/skytree/atlas/getBundle` (only the URL survives; the request bean structure is obfuscated without decompilation).
- Whether the remote page uses `eco/scale/record/list` directly (proxy or bridge) — `record/list` is native-only in the APK.
- Main-user (non-virtual) `record/pull` semantics: `he-1828` is the virtual-member module and passes `uid`; `did:"0"` suggests a cloud-side did, not BLE MAC.
- `assets/schemas.zip` not yet inspected (candidate for JSON Schema of body-composition payload).

**Suggested next probes**
1. Runtime capture (mitm/charles) of the app opening the S800 card → capture the `atlas.nr.mi.com/skytree/atlas/getBundle` request and the returned bundle list; download and unzip the S800 bundle.
2. In the downloaded bundle, grep `eco/scale/record/list`, `eco_api`, `hlth.io.mi.com`, `/app/v1/`.
3. If no device: reimplement `eco/scale/record/pull` with a signed request (needs `serviceToken`+`ssecurity` from a logged-in account; can be extracted from `/data/data/com.mi.health/...` on a rooted/emulated device or from native logs) to confirm the response shape.
4. Inspect `assets/schemas.zip` for `body_composition` JSON schema.

---

## 8. Reproduction index (`work\rn\`)

Scripts (all Python 3.13, stdlib only): `probe1..5.py` (bundle listing/extract), `scan_hosts.py`, `scan1.py`, `kw_count.py`, `bc1.py`, `paths1.py`, `modmap.py`, `modmap_common.py`, `dump1/2.py`, `dumpmods2/3/4.py`, `getmods.py`, `show_int*.py`, `auth1-3.py`, `eco1/2.py`, `ecoall.py`, `ecoall2.py`, `ecoctx.py`, `sdk1..6.py`, `sdkmap2/3.py`, `sdkhash*.py`, `sdkapi1.py`, `sdkloc.py`, `sdktail.py`, `sky1/2.py`, `loc1/2.py`, `heco1/2.py`, `hedump.py`, `hedump2.py`, `henative.py`, `heroutes.py`, `hewin.py`, `hecalls.py`, `hepaths3.py`, `dexstr.py`, `dexgrep1..7.py`, `dexwin1.py`, `dexpaths.py`, `dexpaths2.py`, `dexcn.py`, `man1/2.py`, `assets1.py`, `apk1/2.py`, `pb1..3.py`, `s800.py`, `s800b.py`, `wtrec.py`, `resjson.py`, `lfz.py`, `clus.py`, `impp.py`.
Key outputs: `corpus.pkl` (name→text for all JS bundles), `common_bundle.js` + `common_modules3.json`, `common_mod_1243..1250.js` + `common_mod_586.js`, `mod_he-1828.js`, `he_he-*.js` (dumped health modules), `health_modules.json`, `sdk_modules2.json`, `sdkproj/*.js`, `dex_strings/classes*.txt`, `dexpaths.out`, `dexpaths2.out`, `dexcn.out`, `manifest_strings.json`, `ENDPOINTS.md`, `hosts.json`.


---

## 9. Addendum — round 2 (SkyTree bundle request shape, scale cloud API surface, plugin SDK identity)

All findings below are from the APK only (DEX bytecode/string tables, assets) plus read-only HTTP probes of the
public SkyTree registry (no credentials, no downloads of unknown bundles).

### 9.1 `atlas.nr.mi.com/skytree/atlas/getBundle` is live and does NOT require login
Probe script: `atlasprobe1.py`..`atlasprobe5.py`.

- `GET https://atlas.nr.mi.com/skytree/atlas/getBundle`
  -> `405 {"timestamp":...,"status":405,"error":"Method Not Allowed","message":"Request method 'GET' not supported","path":"/skytree/atlas/getBundle"}`
  (headers include `Server: MiFE/3.5.12`, `Content-Type: application/json`).
- `POST https://atlas.nr.mi.com/skytree/atlas/getBundle` with a JSON body -> **200**, no cookie/token/signature:
  `{"code":0,"message":"ok","data":{"appId":null,"appKey":"<echo of request appKey>","platform":"android","maxNativeApiLevel":null,"rnEngineVersion":"1","modules":[]},"traceId":"...","attachments":null}`
- `modules` stays `[]` for every appKey/ appId tried (list in `atlasprobe2/3/4/5.py`): `YRN_YRNV3_SDK`, `mihealth`,
  `mi_health`, `com.mi.health`, `mi_health_appstore`, `mi_health_device`, `xiaomi_health`, `health`, `MI_HEALTH`,
  `mi-health`, `miwear`, `mi_health_app`, `health_app`, numeric ids `2882303761517406062`, `2882303761520200290`,
  `2882303761517368855`, `2882303761517925182`, `631123200000`, `com.xiaomi.smarthome`.
  The response only echoes `appKey`/`appId` back (the body field is `appKey`; numeric values are echoed as `appId`).
- Conclusion: the registry is keyed by the app's **registered appKey/appId in SkyTree's server-side config**; it is
  not an auth problem (401-free) and the identifier is not present as a string anywhere in the APK (dex string
  tables, AndroidManifest, assets, native libs `libyrnbridge.so`/`libyrnv8executor.so`/`libv8executor.so`).

### 9.2 Exact request assembly (DEX bytecode, `classes3.dex`; scripts `dis1.py`, `dis2.py`, `callers1.py`)
Call chain: `WearableNetFrameProvider.getNetBundleInfo(request)`
-> `WearableNetInfoProvider.getBundleNetUrl(request)` (`RegionManager.getCurrentRegion()` -> `Constants.getBundleRequestUrl(region)`)
-> `WearableNetBeanProvider.getRequestStrFromBean(request)` (JSON body)
-> `new WearableNetFrameProvider$getNetBundleInfo$1(...)` -> `Lflv.d(...)`;
on failure logs `WearableNetFrameProvider.getNetBundleInfo:IYRNNetDefaultFrameInfoProvider is null,pls check`,
`WearableNetFrameProvider.getNetBundleInfo:url is Empty `, `WearableNetFrameProvider.downloadBundle: url is `.

`Constants.getBundleRequestUrl(regionCode)` builds:
`<scheme>://<[regionPrefix-]host>/skytree/atlas/getBundle` where
- scheme = `http` only for the `preview` env, else `https`;
- host = `atlas.test.nr.mi.com` for `staging`, else `atlas.nr.mi.com`;
- `regionPrefix` comes from `Constants.REGION_PREFIX_MAP` (`cn,sg,de,eu,us,i2,in,ru`) and a `'-'` separator.
Observed const-strings in `Constants.<init>/<clinit>`: `cn,sg,de,eu,us,i2,in,ru`, `preview`, `staging`,
`atlas.nr.mi.com`, `atlas.test.nr.mi.com`, `https`, `http`, `://`, `/skytree/atlas/getBundle`.

`YRNBundleRequestBean` fields (classes3): `appName, branch, maxCommonJSApiLevel, maxNativeApiLevel, module,
moduleIdMapVersion, platform, rnEngineVersion`.
`WearableNetBeanProvider.getRequestStrFromBean(bean)` adds, in this bytecode order:
`appKey`=`bean.getAppName()`, `platform`, `rnEngineVersion`, `maxNativeApiLevel` (as *String*),
`moduleNames` (JSON array from `bean.getModule()`), `uid`=`UserInfoManager.getInstance().getUserId()`,
`areaId`=`RegionManager.getInstance().getCurrentRegion().toUpperCase(Locale.ROOT)`,
conditionally `maxCommonJSApiLevel` (only if modules non-empty and first module == `Lmjv.g()`),
`moduleIdMapVersion`; then serialized with `toJsonString`.
`YRNNetDefaultBeanImpl.getRequestStrFromBean` (newer variant) uses
`appKey, platform, branch, moduleNames, maxNativeApiLevel, maxCommonJSApiLevel, rnEngineVersion, moduleIdMapVersion`.
Response beans: `YRNBundleResponseBean{code,data,traceId}`; `...ResponseInfoBean{appName|appKey,maxNativeApiLevel,
modules,platform,rnEngineVersion}`; per-module `MiSkyTreeResponseBundleInfoBean{branch,bundlePath,description,
makeTime,maxCommonJSApiLevel,maxNativeApiLevel,moduleId,moduleMapVersion,moduleName,residentMemory,updateType,
urlcdn,urlmd5,version}` — the same schema as `assets/builtin_bundles_skytree/bundleInfo.json`, i.e. the registry
response is a list of downloadable module bundles with CDN urls.

### 9.3 Where the appKey comes from
`classes2.dex` has the app-side YRN wiring:
- `Lcom/xiaomi/fitness/yrn_config/YRNConfigManager;` calls `WearableRNManager.init(context, appConfigs)` in
  `yrnInit()`; it declares static `APP_NAME`, `MAX_API_LEVEL`, `RN_ENGINE_VERSION`, `TAG`.
- `Lcom/xiaomi/fitness/yrn_config/YRNConstants;` declares static `APP_NAME`, `PLATFORM`, `MAX_API_LEVEL`,
  `RN_ENGINE_VERSION`, `RN_WHITE_ACCOUNT`, `SKY_TREE_URL`.
- Neither value is materialised as a `const-string` in `<clinit>/<init>` (verified by raw 16-bit unit decode), and
  no `sput-object` to `YRNConstants.APP_NAME` exists in any of the 13 dex files -> the value is produced at runtime
  (BuildConfig/DI merge or JNI), so it is not recoverable from the APK statically.
- `WearableRNManager` (classes3) methods: `init(appConfigs,context)`, `appName()` -> `getAppName(...)`,
  `appConfig()`, `initReactNative`, `checkBundleUpdate` (`'sport','mine'`), `preLoadBundle`, `reloadBundle`,
  `sendJSEvent(Map)`; static fields `_yrnProvider`, `mContext`, `isBundlePreLoaded`, `isMainActivityReady`.

### 9.4 Scale cloud API wrappers in DEX (`classes11.dex`, scripts `sc1.py` -> `dex_scale_request.txt`)
`Lcom/xiaomi/fitness/electronic/scale/request/ScaleRequest;` — all via `getEcoApiProxy(eco_api, params)` (which POSTs
`/app/v1/eco/api_proxy`; strings `eco_api`,`params`,`getEcoApiProxy() data = `):

| method | eco_api | params |
|---|---|---|
| `getWeightRecords` | `eco/scale/record/list` | `accountId, page, pageSize` |
| `getClaimedRecords` | `eco/scale/record/claimList` | `did, page, pageSize` |
| `pullRecord` | `eco/scale/record/pull` | `uid, did, pageSize, createTime` |
| `pullRecordCommon` | `eco/common/scale/pull` | `uid, did, model, pageSize, createTime` |
| `addRecord` | `eco/scale/record/add` | `scope, params` |
| `addRecordCommon` | `eco/common/scale/batchAdd` | `scope, params` |
| `deleteRecord` | `eco/scale/record/delete` | `did, accountId` |
| `deleteByDidAndCreateTime` | `eco/scale/record/deleteByDidAndCreateTime` | `did, createTime` |
| `addVirtualAccount` | `eco/scale/account/add` | `name, icon, sex, height, weightTarget, birth` |
| `deleteVirtualAccount` | `eco/scale/account/remove` | `accountId` |
| `modifyVirtualAccount` | `eco/scale/account/update` | `accountId, name, icon, sex, height, weightTarget, birth` |
| `getVirtualAccount` | `eco/scale/account/list` | `did, uid` |
| `getWeightAccount` | `scale/account/getAccountByWeight` | `uid, weight` |
| `getAccountUserInfo` | `account/user/info` | `uid` |
| `uploadScaleIcon` | `upload/scale/icon` | `uid` |

`Lcom/xiaomi/fitness/electronic/scale/request/BindScaleRequest;` (same proxy): `/bs/bind/bind` (bindIndex,bindSign,
bindDid,thirdNodeId,peripheralId,sn,name,extraValues), `/bs/bind/ecdh` (bindIndex,bindDid,pubD,signD),
`/bs/bind/hello` (did,model,sn,mac,randomD,mode,name), `/bs/bind/relieve` (bindDid,mac,tokenKey),
`/bs/bind/devices` (page,pageSize,status), `/bs/bind/shareBind`, `/bs/bind/shareEcdh` (shareId),
`/bs/bind/miot/bindKey`, `/bs/share/{shareConfirm,userExist,shareDevice,shareCancel,sharedDevicesList}`,
`/eco/common/scale/add` (model,did,data,sn,uid,accountId,createTime,dataVersion,fromSource),
`eco/scale/account/updateWeight` (weightTarget,uid,accountId).

=> 「历史数据 -> 全部记录」 pagination for the scale feature uses **`eco/scale/record/list` with `accountId, page,
pageSize`** (page-based). `record/pull` is the sync cursor API (`createTime`). The JS `he-1828` sends `did:"0"`
with `record/pull` (virtual-member flow, 12 impedances `lfz12..lfz34/hfz12..hfz34`).

### 9.5 `sdk.bundle` identity + plugin cloud entry (definitive)
- `assets/plugin_rn_sdk/project.json` = `{"name":"miwear","version":"2.0.3","platform":"android","is_sdk_bundle":true,
  "sdk_api_level":20003,"min_native_api_level":20001,"build_time":1703507965000,"signature":1048576}`;
  inside it `miot-sdk/package.json` = `{"name":"miot","version":"1.9.1","api_level":10091,...}` and
  `raw/{miotsdk,miwearsdk,wearsdk}_package.json` describe miot 1.8.7 / miwear 2.0.3 / wear 2.0.3.
  So sdk.bundle = **miwear SDK bundle embedding miot-plugin-sdk 1.9.1** (and it is a Mijia/Host plugin runtime).
- Plugin cloud entry: `Service.callSmartHomeAPI(api, params)`:
  `omitApi.some(p => api.startsWith(p))` -> `MIOTRPC.standardCall(api, params, cb)`; else if `apiRepo[api]` ->
  `MIOTRPC.standardCall`; else reject (`失败，原因如下:...`).
- `miot-sdk/service/omitApi.js` (module 10845): `var OMIT_API = ['/appgateway/third/miwifi/app','/eco',
  '/business/outplatform'];` -> plugin pages may call `/eco/*` (including `eco/scale/record/list`) through the
  native bridge, bypassing `apiRepo`.
- `eco/api_proxy` appears exactly once in sdk.bundle, in the miwear Host project module (~offset 10,390,390):
  `requestData(url,params){... MiWearHost.requestUrl(url,params,cb) ...}`,
  `requestApi(api,params){ return this.requestData("eco/api_proxy",{eco_api:api,params}) }`; used by
  `EcoScaleService.updateVirtualUserInfoToServer` (offset 10,452,481).
  => A Mijia-plugin S800 page would call `/eco/scale/...` via `MIOTRPC`, not `/app/v1/eco/api_proxy`; the proxy
  form is used by the built-in miwear project JS.

### 9.6 `assets/schemas.zip` (inspected; `sch1.py` -> `schema_weight_fat_report.json`, `schema_index.json`)
73 entries; `index.json` maps `category/activityType/fileType`: health activityType **10 = `weight_fat`**,
`fileType` health {0: record, 1: report}. `weight_fat/report/weight_fat_report_v1.json` (12,754 B,
`$id:http://wear.xiami.com/binaryschemas/fitness/weightbody/report/v1.json`) is the BLE binary report protocol
「体重体脂测量记录报告协议」: header `timestamp/timezone/version/fileType/encryptionCompressionMode/validityFlags`
(weightValid, bmiValid, bodyfatValid, fatmassValid, musclemassValid, basalmetabolicrateValid, bodywaterValid,
bonesaltValid, proteinValid, proteinmassValid, bonemineralcontentValid...) + body items
`timestamp uint32, weight uint16 kg, bmi uint16, bodywater, bonesalt, protein, bodyfat, fatmass, musclemass,
bonemineralcontent, proteinmass, basalmetabolicrate`. This is the **device-side** report schema, not the cloud JSON
record (cloud record shape remains §5.1/5.2).
`assets/miao_health_product_catalog.json` = third-party devices only (sinocare/kefu/...), no scale/S800 mapping.

### 9.7 Updated conclusions for the S800 user flow
1. The S800 card page JS is **not in the APK**. It is downloaded at runtime either
   (a) as a Mijia plugin bundle (`PluginRNActivity` + `com.xiaomi.smarthome.framework.plugin.rn.*`,
   `/v2/plugin/fetch_plugin` + `pluginId/packageId` + `urlcdn`-style download, plugin cloud sends `pluginId`), or
   (b) as a SkyTree biz bundle via `atlas.nr.mi.com/skytree/atlas/getBundle` (POST JSON: `appKey, platform,
   rnEngineVersion, maxNativeApiLevel, moduleNames[], uid, areaId, [maxCommonJSApiLevel, moduleIdMapVersion]`,
   response `modules[].urlcdn/urlmd5`).
2. The registry call needs the app's registered SkyTree appKey (not in APK) and possibly a valid `uid`; **it is not
   gated by login** (anonymous POST returns code 0, empty module list). With the appKey (or a runtime capture) the
   module list — and thus the S800 page bundle URL — is recoverable.
3. Body-composition/历史数据 API for scales is the eco family behind `/app/v1/eco/api_proxy`
   (`hlth.io.mi.com`), notably `eco/scale/record/list {accountId,page,pageSize}` for paging and
   `eco/scale/record/pull {uid,did,pageSize,createTime}` for sync; the same names exist in DEX and in the built-in
   he-1828 JS (pull only). A Mijia-plugin page would reach them as `/eco/scale/...` via MIOTRPC.
4. Remaining gaps: SkyTree appKey value; S800 plugin `pluginId`/`packageId`; whether the card opens
   `PluginRNActivity` or `YRNActivity` (needs runtime capture/devices). Suggested probe list in §7 stands, with the
   new atlas request template above.
