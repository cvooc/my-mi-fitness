# ENDPOINTS.md — Mi Health 3.61.0: hlth / eco / plugin / skytree endpoint inventory

All items marked **[OBS]** are directly observed strings/code in the cited artifact; **[INF]** is inference.
Evidence paths are relative to `D:\workspace\github\mi-fits\work\assets\` unless noted.
Extracted files/offsets: `work\rn\` (`mod_he-1828.js`, `common_mod_1243..1250.js`, `he_he-*.js`,
`dex_strings\classes*.txt`, `dexpaths.out`, `dexpaths2.out`).

---

## 1. Transport, host and signing (all `/app/v1/*` and `/healthapp/*` calls in SkyTree bundles)

**[OBS]** `skytree_common` bundle module 1248 (`common_mod_1248.js`) — host map and base URL:

```js
HostType = { hlth:"HLTH", watch:"Watch", watchAppstore:"WatchAppstore" }
HostMap[HLTH] = { release:"hlth.io.mi.com", pre:"pv.hlth.io.mi.com",
                  test:"staging-hlth.io.mi.com", at:"onebox-hlth.io.mi.com" }
HostMap[Watch] = { release:"watch.iot.mi.com", pre:"pv.watch.iot.mi.com", ... }
getBaseUrl = async hostType => {
  region = await getRegionCode();          // native.getCurrentServer(), lowercased, default "cn"
  host   = getHttpHost(hostType);          // HostMap[hostType][getHostMode() ?? "release"]
  return region !== "cn" ? "https://"+region+"."+host : "https://"+host;
}
```

⇒ CN release: **`https://hlth.io.mi.com`**; other regions `https://<region>.hlth.io.mi.com`
(e.g. `https://sg.hlth.io.mi.com`). `getRegionCode` comes from
`NativeModules.CommonBridge..getCurrentServer()` via a native callback.

**[OBS]** module 1243 (`common_mod_1243.js`) — axios request interceptor. For every request it:

1. builds `Cookie: serviceToken=<U>; cUserId=<I>; locale=<lang>` (values from
   `UserServerInfo.getInstance().serviceInfo()` → `{serviceToken, ssecurity, cUserId}`);
2. optionally snake_cases the body (`enableCaseConversion`, default **true**);
3. wraps the body in `{data: <JSON string>}`, RC4-encrypts it and signs it with
   module 1247 (`encryptParameters`): envelope
   `{ data:<RC4+base64>, rc4_hash__:<b64 sha1>, signature:<b64 sha1>, _nonce:<b64> }`;
4. `qs.stringify()`s the envelope and sends `Content-Type: application/x-www-form-urlencoded`;
5. `baseURL = getBaseUrl(hostType ?? "HLTH")`.

Nonce (module 1250) = base64 of 12 bytes = 8 random bytes + uint32BE( (Date.now()+timeDiff)/1000/60 ).

**[OBS]** Response interceptor always `convertToCamelCase(JSON.parse(decryptResponse(...)))`, so **all JS
sees camelCase keys**, even for `/healthapp/*` paths. `encryptExcludePathPrefix` (e.g. `"/healthapp"`,
see he-603) strips a prefix from the *signed* URL only.

---

## 2. eco device API — body-composition scale records

The only eco HTTP endpoint present in JS **[OBS]** (`skytree_health` `he-1828`, `work\rn\mod_he-1828.js`):

```
POST /app/v1/eco/api_proxy            (hostType HLTH, enableCaseConversion:false)
body (logical): { eco_api: "<eco path>", params: "<JSON string>" }
response:       result.resp = "<JSON string>"  -> JSON.parse
```

`eco_api` values called from JS (`he-1828`, module tag `[VM-RN]`, virtual/family member records):

| eco_api | params | purpose |
|---|---|---|
| `eco/scale/account/add` | `{name, icon:"", sex, height, weightTarget, birth}` | create virtual account |
| `eco/scale/account/list` | `{uid:Number}` | list accounts |
| `eco/scale/account/remove` | `{accountId:String}` | delete account |
| `eco/scale/account/update` | `{accountId,name,icon,sex,height,weightTarget,birth}` | update profile |
| `eco/scale/account/updateWeight` | `{uid:Number, weightTarget:String, accountId}` | target weight |
| **`eco/scale/record/pull`** | `{uid:Number, did:"0", pageSize:200, createTime:<ms>}` | **paginated body-composition records** |

Pagination loop in he-1828: call with `createTime = Date.now()`; continue while
`200===code && data.length!==0`, next cursor = `Math.min(createTime)` of the page; stop when
`minCreated >= previousCursor` or `data.length < pageSize`.

**[OBS]** DEX (`classes11.dex` string table, `dex_strings\classes11.txt`) contains the **complete eco API
set of the native eco SDK** — the superset that the remote page / native screens can reach:

```
eco/api_proxy
eco/scale/account/{add,list,remove,update,updateWeight}
eco/scale/record/{add, claimList, delete, deleteByDidAndCreateTime, list, pull}
eco/common/scale/{add, batchAdd, pull}
eco/general-equipment/common/{device/add, sport/add}
```

⇒ **`eco/scale/record/list` exists only as a native/DEX string**, never in any JS bundle
(`record/list` regex over `corpus.pkl` = 0 hits). **[INF]** the S800 remote plugin page (if it needs the
full list) either calls `record/pull` (observed pattern) or reaches `record/list` through a **native**
bridge (MiWearHost/EcoHost), not through JS HTTP.

Also in DEX (`classes11.dex`) around the same region: `ecoDeviceManager`, `ecoFetchDeviceList`,
`ecoBindDevices`, `ecoWeightActivityPath`, `com.xiaomi.fitness.eco.device.manager.export.bean.EcoDevice`,
`EcoLocalDevice`, DB names `eco_device_db` / `eco_local_device_db`, flavors
`eco-device-manager-export_release`, `electronic-scale-export_release` — i.e. the scale device domain
is the pre-existing "eco" SDK, and the S800 (八电极) is one of its devices.

---

## 3. hlth REST API used by the health SkyTree bundle (93 distinct `/app|/healthapp` paths) [OBS]

`skytree_health` bundle; modules in parentheses. Nothing here is body-fat specific.

**data / aggregation**
- `/app/v1/data/get_aggregated_fitness_data_by_time`, `/app/v1/data/up_aggregated_fitness_data` (he-603, he-716)
- `/app/v1/data/{get_project_data_by_time, up_project_data, delete_project_data, get_diet_records_by_time, up_diet_records, delete_diet_records, get_sleep_insight_data, get_latest_sport_record, last_achieved_goals, reoprt_achieved_goals}`
- `/app/v1/user/{get_project_user_info,set_project_user_info}`
- `/app/v1/geo/regeo_auth`, `/app/v1/service/gen_research_upload_url`

**statistics** (he-1375): `get_stat_data_by_time`, `get_stat_data_by_watermark`, `get_max_watermark`,
`trigger_stat`, `delete_stat_data`, `batch_get_diet_summary`, `get_sleep_users_distribution`,
`get_ai_chat_conversation_list`, `get_ai_chat_message_list`, `delete_conversation`
— `get_stat_data_by_time` body `{tag, key, nextKey, startTime, endTime, limit:50, reverse:true}`;
this is the generic **trend/stat** endpoint family (stat data is packed by tag/key).

**health summary** (he-856, he-948): `/app/v1/health_summary/{get_all_trend,get_picked_summary,get_summary_by_model,ai_qa,get_question_list}` plus SSE
`/sse/health_summary/{ai_qa_stream,ai_task_stream}` — `get_all_trend` is the "all trends" screen
(candidate for 历史数据 trend cards if the S800 page used SkyTree, but the S800 page is not in these bundles).

**plan / goal (weight loss, weight)**
`/app/v1/plan/goal/weight_loss/{build,dates,detail,estimate_date,estimate_weight,panel,steers,stop,summary,today,update,user/get,user/profile_set}`,
`/app/v1/plan/goal/weight/{user_build_fast,user_card}`, `/app/v1/plan/{last_doing,template/user_detail,ops_recommend/position/get}`

**diet, sleep intervention, misc**
`/app/v1/diet/{food_collect,food_collect_cancel,food_collect_list,food_collected,food_detail,food_list,food_search,user_consumption}`,
`/app/v1/sleepInterventionPlans/{create,get,list,tasks/list,tasks/update,update}`,
`/app/v1/{auth/get_third_party,auth/third_party,behaviorLog/factors/catalog,product/get_config_info_by_category,smart_running/improve/summary,source/get_third_device_source_list,sport_recommend/get_sport_recommend_app,version/check_upgrade}`,
`/app/healthapp/health_plan/get_history_plan_list`

**healthapp (Mi Health server, not watch)**
`/healthapp/health_plan/{add_plan,add_task,batch_delete_task,edit_task,get_ai_plan,get_history_plan_list,get_plan,get_plan_progress,get_plan_task_list,get_task_list,update_plan,upload_punch_data}`,
`/healthapp/setting/{get_user_app_setting,get_user_app_switch,get_user_device_settings,set_user_device_settings}`,
`/healthapp/user/{get_miot_user_profile,set_miot_user_profile}`, `/healthapp/{third/alipay/aq/get_faq,track/add_event_logging,weather/get_weather_info_v3}`

### 3.1 "全部记录" page in the health bundle is NOT an HTTP screen [OBS]
Route `miwear/health/WeightAllRecords` → module **he-2173** (`work\rn\he_he-2173.js`):

```js
ft = (0,O.getDailyRepo)(E.IDailyType.WEIGHT);            // local data layer
ft.getRecordList({startTime:0, endTime:<now unix>, limit:60, sort:"descend"})
    .then(({code,data}) => { ... })                       // {value:{time,...}, sid}
DeviceEventEmitter.addListener(w.DATABASE_SYNC_EVENT, ...) // re-load on sync
```
Row tap: `t.push(HealthRoutes.WeightRecordDetail, {record:JSON.stringify(n.value), sid:n.sid, time:String(n.value.time)})`.
`YRNDatabaseModule`, `YRNHealthModule` etc. are the native bridges; **weight/body records are persisted
locally (Room) and read from the local DB**, cloud sync happens natively/elsewhere.

Body-composition UI modules in health bundle: he-1836 / he-1837 / he-1853 / he-1866 / he-2175
(`WeightDataReport`), he-484 (FDS image map only: `body_composition.webp`, `segmentalFat`,
`segmentalMuscle`, `somatoType*`, `VirtualMember*`). No URL constants in these.

---

## 4. SkyTree dynamic bundle delivery [OBS]

- Registry/update endpoint (DEX `classes2.dex` full URL + `classes3.dex` path):
  **`https://atlas.nr.mi.com/skytree/atlas/getBundle`**; related class names
  `com.xiaomi.wearable.yrn.provider.WearableNetBeanProvider$MiSkyTreeResponseInfoBean`,
  `…BundleInfoBean`, `WearableNetFrameProvider$downloadBundle$1`, `getNetBundleInfo$1`,
  `com.xiaomi.yrn.net.impl.YRNNetDefaultBeanImpl$MiSkyTreeResponse*`,
  `com.xiaomi.yrn.env.entity.bundleload.YRNBundleRequestBean` / `YRNBundleResponse*` /
  `YRNAssetsLoadRequest` / `YRNLoadRequest` / `YRNNetworkLoadRequest`,
  plus strings `assetsBuiltinBundleJsonPath`, `builtin_bundles_skytree/bundleInfo.json`,
  `createBizInstance error: bizBundleInfo ==  null`, `convertSkyTreeBundleToYRN`,
  `bundle.sign`, `bundle.cert`, `bundleFileName 不存在`, `bundle cool down`, `bundleIsMinify`.
- Bundle CDN template (`builtin_bundles_skytree\bundleInfo.json`, field `urlcdn`):
  `https://skytree.gnr.mi-img.com/skytree/<32-HEX>_<epochms>.zip`
  (6 shipped bundle entries: moduleId 101 common / 102 init / 103 dial / 111 sport / 143 mine / 182 health).
- JS side only stores/reads what native gives it: `CountryManager` gets
  `{skytreeUrl, businessUrl, fileUploadUrl}` from `NativeModules.LocaleAndLanguageModule`;
  `getRnBundleInfo()` → `getAllRnVersion()`; asset path
  `file://<splitBundlePath>/<module>/<md5>/res/<name>` (android) and CDN fallback
  `https://region-cdn-fds.xiaomiwear.com/hlth-client-assets/<module>/<name>`.

→ The **S800 remote page is a SkyTree "biz bundle" fetched at runtime**; its id/version registry is
served by `atlas.nr.mi.com/skytree/atlas/getBundle`, the code by `skytree.gnr.mi-img.com`. Neither the
page JS nor its API list ships in the APK.

---

## 5. Mijia plugin (RN) endpoints present in the APK [OBS, DEX `classes3.dex`]

```
/v2/plugin/fetch_plugin, /v2/plugin/fetch_sdk, /v2/plugin/get_config_info_new,
/v2/plugin/get_support_models, /plugin/get_config_info_new, /plugin/update_plugin,
/app/v2/plugin/get_protocol, /api/v4/detail/config
```
Plugin runtime classes: `com.miot.rn.plugin.activity.PluginRNActivity` (also declared in
AndroidManifest.xml), `com.xiaomi.smarthome.framework.plugin.rn.*` (`MIOTHostModuleCore`,
`MIOTDeviceModuleCore`, `MIOTBluetoothModuleCore`, `MIOTServiceModuleCore`, …),
`com.xiaomi.smarthome.core.entity.plugin.{PluginPackageInfo,PluginDownloadTask}`.
Plugin fs layout: `plugin/install/rn/std-plugin/data`, `cloud_rn_plugin_cache_v1`,
`/mnt/sdcard/SmartHome/logs/plugin_timestamp.log`.

Mijia cloud / miot (from `sdk.bundle` `miot-sdk/service/apiRepo.js`, module 10764 — whitelist of
allowed paths, all values `{}`): `/home/device_list`, `/user/get_user_device_data`,
`/user/set_user_device_data`, `/user/del_user_device_data`, `/v2/user/get_device_data_raw`,
`/v2/user/getuserdevicedatatab`, `/v2/user/setuserdevicedatatab`, `/miotspec/prop/{get,set}`,
`/miotspec/action`, `/v2/device/{set_props,batch_set_props}`, `/v2/home/{gethome,homeroom/home_device_list,device_list_page,get_device_cert}`,
`/v2/user/{create,update,remove,get}_member`, `/scene/*`, `/share/*`, `/v2/aftersale/*`,
`/smarthome/product/panel/plugin/*` … **no scale/bodyfat/health path in the whitelist**.

Mijia plugin pages reach Mi Health's own proxy through the native bridge **[OBS]** (`sdk.bundle`
project module `b4f412a06f01a4220c8788c32f7c567d`):
```js
requestData(url, params) { MiWearHost.requestUrl(url, params, cb) }
requestApi(api, params)  { requestData("eco/api_proxy", {eco_api: api, params}) }
```
⇒ even the Mijia-plugin side lands on **`/app/v1/eco/api_proxy`** for eco device data.

---

## 6. Non-HTTP native routes / activities (for completeness, not APIs) [OBS, DEX]

`/electronic/activity/eight/weight` (八电极 weight page), `/electronic/activity/user`,
`/electronic/activity/user_add`, `/electronic/activity/userinfo`, `/electronic/activity/DataReport`,
`/electronic/activity/VirtualMemberData`; `/ecoDevice/activity/{bind_bt,bind_eco,bind_eco_huami_scale,local_bind_eco,bind_rope_skipping_list}`;
`/device/activity/{add,bind_bt,bind_eco,…}`; `/sport_eco/activity/*`.
Deep links: `https://region.hlth.io.mi.com/applinks/peripheral/connect?`, `mihealth://intent?...`.

---

## 7. Answers to Lead's C question (short)

1. **No direct-REST body-fat endpoint exists in any JS bundle.** The only JS body-fat transport is
   `POST /app/v1/eco/api_proxy` with `eco_api=eco/scale/record/pull` (virtual-member list, paginated).
2. `eco/scale/record/list` appears **only in native DEX strings** (`classes11.dex`), together with
   `record/{add,claimList,delete,deleteByDidAndCreateTime}` — reachable natively, not via SkyTree JS.
3. "全部记录" in the shipped health bundle (`miwear/health/WeightAllRecords`) reads the **local DB**
   (`getDailyRepo(IDailyType.WEIGHT).getRecordList({limit:60,sort:"descend"})`), no HTTP.
4. Trend/summary families that do exist: `/app/v1/statistics/get_stat_data_by_time` (he-1375),
   `/app/v1/data/get_aggregated_fitness_data_by_time` (he-603), `/app/v1/health_summary/get_all_trend`
   (he-848/856) — generic health data, no body-composition fields.
5. All of the above use the same signed/encrypted envelope (b1 §1) with `Cookie: serviceToken/cUserId/locale`.
