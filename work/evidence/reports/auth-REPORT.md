# Xiaomi Mi Health — phone/SMS login (auth layer)

Status: **ready for end-to-end test with the user's phone number + SMS code.**
Everything that can be verified without credentials has been verified live; the
remaining steps (`send` with a real number, `verify` with the received code) need
the Lead to supply the phone number and code.

Deliverables in this directory:

| file | purpose |
|---|---|
| `xiaomi_auth.py` | CLI + importable module (send / verify / refresh / show / login) |
| `test_xiaomi_auth.py` | 12 offline unit tests (`python test_xiaomi_auth.py`) — all pass |
| `cookies.json` | created by `verify` (not yet present — needs real SMS) |
| `device.json` | stable device identity (`deviceId`, `ua`), created on first run |
| `_decomp/`, `_dexdump/`, `ref/`, `_probe*.py`, `_dec_any.py` | reverse-engineering evidence (APK decompiled classes, probes, prior art) |

---

## 1. What was verified live (no credentials, 2026-11-13)

All requests via python `requests` 2.32.5 from this machine. `account.xiaomi.com`
is reachable; responses use the Xiaomi convention `&&&START&&&` + JSON.

### 1.1 `serviceLogin` challenge — sid = `miothealth`

```
GET https://account.xiaomi.com/pass/serviceLogin?sid=miothealth&_json=true
```
HTTP 200, `application/json;charset=UTF-8`, body:
`&&&START&&&{...}` with keys
`[_sign, callback, captchaUrl, child, code, desc, description, location, miDemo,
pwd, qs, result, securityStatus, serviceParam, sid]`, `code=70016`
("登录验证失败" — the normal unauthenticated challenge, **not** an error),
`captchaUrl=null`, `securityStatus=0`,
`callback=https://sts-hlth.io.mi.com/healthapp/sts`,
`qs=%3Fsid%3Dmiothealth%26_json%3Dtrue`,
`location=https://account.xiaomi.com/fe/service/login?_json=true&sid=miothealth&qs=...`.
Response also sets cookies `deviceId=wb_<uuid>`, `pass_ua=web`, `uLocale=zh_CN`.

With `&_loginSign=ticket` (the phone-ticket meta request used by the app):

```
GET /pass/serviceLogin?sid=miothealth&_json=true&_loginSign=ticket
```
same `code=70016`, `_sign=5TY92n7e+PXCTFfxDGWNrB34zPE=`,
`qs=%3Fsid%3Dmiothealth%26_json%3Dtrue%26_loginSign%3Dticket`,
`callback=https://sts-hlth.io.mi.com/healthapp/sts`. This meta (`_sign/qs/callback`)
is what the app reuses in the ticket-auth POST — **the client never computes `_sign`
itself**; it only computes `clientSign` for the STS call (see 3.5).

### 1.2 sid → callback map (all HTTP 200, `code=70016`, `captchaUrl=null`)

| sid | callback | verdict |
|---|---|---|
| `miothealth` | `https://sts-hlth.io.mi.com/healthapp/sts` | **Mi Health backend (chosen)** |
| `mihealth` | `https://api.health.comm.miui.com/sts` | valid, older/regional health endpoint |
| `xiaomiio` | `https://sts.api.io.mi.com/sts` | Mijia/MiIO cloud (`api.io.mi.com`) |
| `mijia` | `https://sts.api.mijia.tech/mijia/sts` | Mijia app |
| `i.mi.com` | `https://i.mi.com/sts` | Mi Cloud |
| `miio`, `mina`, `health` | — | `code=21315` 无效的sid |

`serviceParam` is always `{"checkSafePhone":false,"checkSafeAddress":false,"lsrp_score":0.0}`.

### 1.3 EUI public key + encrypted-phone acceptance

```
GET https://account.xiaomi.com/pass2/config?key=eui
→ HTTP 200 {"euiPublicKey":"MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A..."}  (RSA-2048, X.509 DER, base64)
```

```
POST https://account.xiaomi.com/pass/sendServiceLoginTicket
     user=<EUI-encrypted phone>&sid=miothealth&type=sms&_json=true&_locale=zh_CN
     header EUI: <enc_key>.<b64("user")>.<b64(sha256(phone))>
→ HTTP 200 {"code":70008,"desc":"电话号码格式错误","tips":"手机号格式不正确",...}
```

`70008` is the **phone-format** rejection (we used `123456`), i.e. the server
decrypted the EUI-encrypted `user` and reached phone validation. A rejected EUI
key would return `81004`; we never observed `81004`. So the EUI implementation in
`xiaomi_auth.py` is byte-compatible with the app.

### 1.4 `/pass/phoneInfo` reachability

```
POST /pass/phoneInfo  {user:<enc phone>, ticket:<enc code>, sid, type=sms, _json, _locale}
→ HTTP 200 {"code":10001,"desc":"系统错误",...}
```
Same with plaintext params. The endpoint is reachable; the fake phone/ticket is
rejected with a generic system error (no SMS is sent by this endpoint).

### 1.5 What was *not* verified (needs real phone/SMS)

* the success shape of `sendServiceLoginTicket` (`code=0`, `data.vCodeLen`);
* `phoneInfo` returning `ticketToken` for a real code;
* `serviceLoginTicketAuth` returning `userId`/`passToken` headers;
* `serviceLogin` + STS returning `serviceToken`/`cUserId` for a real passToken.

The offline end-to-end test (`EndToEndMockTests`) exercises all of these steps
against a mocked HTTP session, so the code path is covered; only the real server
responses are pending.

---

## 2. Documented flow (verified from APK decompiled classes)

Source: `classes9.dex` / `classes3.dex` Mi Account SDK
(`com.xiaomi.accountsdk.account.XMPassport`, `…ui.page.VerifyCodeLoginFragment`,
`…ui.utils.LoginAndRegisterController`, `…utils.EUIParamEncryptUtil`,
`…utils.Coder`, `…utils.AESCoder`, `…URLs`). Decompiled sources are in `_decomp/`.

### 2.1 Step 0 — device identity

`deviceId = "wb_" + 32 hex` (UUID4 hex) persisted in `device.json` and reused for
every request (send → verify → serviceLogin). UA is a stable mobile Chrome UA.
The app also has a hashed deviceId path (`DeviceIdHasher`), not needed here.

### 2.2 Step 1 — request the SMS code (`send`)

```
POST https://account.xiaomi.com/pass/sendServiceLoginTicket
form: user=<AES(phone)>, sid=<sid>, type=sms, _json=true, _locale=zh_CN,
      [captCode=<captcha>, captIck=<ick>, vToken=<verifyToken>, vAction=<action>]
header: EUI = <rsa_enc_key>.<b64("user")>.<b64(sha256(phone))>
cookies: deviceId
```
* `user` encryption (`EUIParamEncryptUtil.encryptParam`): fresh AES-128 key,
  AES/CBC/PKCS5Padding, IV `0102030405060708`, base64 ciphertext. The AES key is
  RSA/PKCS1v15-encrypted with `euiPublicKey`, base64url-encoded (NO_WRAP).
* EUI header = `enc_key . b64(names joined ",") . b64(sha256(concat(values)))`.
* Response `{"code":int,"description":...}`; success is `code` absent or 0, and
  `data.vCodeLen` gives the expected code length.
* Error codes: `70008` invalid phone, `70022` reach limit / rate limited,
  `21317` token expired, `87001` captcha required (response has `captchaUrl`;
  retry with `captCode`/`captIck`/`vToken`/`vAction`), `81004` bad EUI key
  (clear cached key and retry once).

### 2.3 Step 2 — verify the SMS code (`verify`, part 1)

The app does **two** requests: verify the code to obtain a `ticketToken`, then
exchange the ticket for the account tokens.

```
POST https://account.xiaomi.com/pass/phoneInfo
form: user=<AES(phone)>, ticket=<AES(code)>, sid, type=sms, _json=true, _locale=zh_CN
header: EUI = ... over ["user", phone, "ticket", code]
cookies: deviceId
→ code=0, header "ticketToken", data {id, phone, nickname, ...}
```
`10031` = wrong verification code, `70008` invalid phone, `81004` bad EUI key.

### 2.4 Step 3 — ticket auth (`verify`, part 2)

```
POST https://account.xiaomi.com/pass/serviceLoginTicketAuth
form: user=<AES(phone)>, ticket=<ticketToken>, sid, _json=true,
      _sign=<from serviceLogin meta>, qs=<...>, callback=<...>, _locale=zh_CN
header: EUI over ["user", phone]
cookies: deviceId, ticketToken
→ code=0; headers userId=<uid>, passToken=<passToken>
```
`securityStatus != 0` (or a `notificationUrl` in JSON) means Xiaomi wants extra
verification (2FA/trusted-device); the module raises `NeedVerification`.
Errors: `70008`, `70014` invalid ticket, `70069` invalid tzSign, `81004`.

The `_sign`/`qs`/`callback` come from the earlier
`serviceLogin?...&_loginSign=ticket` meta call (step 1 of `verify`).

### 2.5 Step 4 — mint `serviceToken` for the sid

```
GET https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=miothealth
cookies: userId=<uid>, passToken=<passToken>, deviceId=<deviceId>
→ code=0, JSON {ssecurity, nonce, location, cUserId}
```
Then the STS call, **without following redirects**:

```
GET <location>&clientSign=<b64(sha1("nonce=<nonce>&<ssecurity>"))>&_userIdNeedEncrypt=true
→ 302; Set-Cookie: serviceToken=<...>; cUserId=<...>
```
`clientSign` is the only signature the client computes here:
`base64(sha1("nonce=" + nonce + "&" + ssecurity))`.

### 2.6 Step 5 — use the cookies

For Mi Health backend calls (`hlth.io.mi.com`), send cookies
`userId`, `cUserId`, `serviceToken` (+ `deviceId`) and the same UA.
`build_session(sid)` returns a `requests.Session` preconfigured with those
cookies for `account.xiaomi.com`, `.xiaomi.com`, `.mi.com`,
`api.io.mi.com`, `sts-hlth.io.mi.com`.

---

## 3. sid recommendation

**Use `miothealth` for Mi Health.** Confirmed independently by the Lead from
`classes10.dex` (`com.xiaomi.fitness.account.SIDs`: `MIOT_HEALTH="miothealth"`,
plus staging/onebox variants) and by the live callback
`https://sts-hlth.io.mi.com/healthapp/sts`, which matches the
`hlth.io.mi.com/app/v1/eco/api_proxy` endpoint the Lead's crypto probe targets.

* One login (`passToken`) is account-level; the `serviceToken` is minted
  **per sid**. So `miothealth` login gives the serviceToken for the health cloud.
* If Mijia cloud (`api.io.mi.com`) body-scale data is also needed, run a second
  `serviceLogin` with `sid=xiaomiio` reusing the same `userId`+`passToken` and
  mint a separate `serviceToken` (the module's `login_with_passtoken(...,
  sid="xiaomiio")` does this). Whether the same `serviceToken` is accepted by
  both clouds is unverified; per-sid tokens are the safe assumption.
* `mihealth` (`api.health.comm.miui.com/sts`) is a valid but older health
  service; not used by this app version.

---

## 4. Module API

```python
import xiaomi_auth as xa

# 1) request SMS code (never call this with a real number unless the user asked)
res = xa.send_sms_code("13800138000", sid="miothealth")
#    -> {"ok": True, "sid": ..., "phone": ..., "code_length": 6, "deviceId": ...}

# 2) verify the code; persists work/auth/cookies.json
rec = xa.verify_sms_code("13800138000", "123456", sid="miothealth")
#    -> {"sid","userId","cUserId","serviceToken","ssecurity","passToken",
#        "deviceId","locale","ua","phone","createdAt","timestamp","epoch",
#        "nonce","location","raw_cookie"}

# 3) reuse
sess = xa.build_session("miothealth")          # requests.Session with cookies+UA
tok  = xa.get_service_token("miothealth")      # load or refresh via passToken

# 4) refresh without SMS (uses stored passToken)
xa.login_with_passtoken(rec["userId"], rec["passToken"], sid="miothealth")
```

CLI:

```powershell
python work/auth/xiaomi_auth.py send <phone>              # trigger SMS; prints server JSON
python work/auth/xiaomi_auth.py verify <phone> <code>     # complete login; writes cookies.json
python work/auth/xiaomi_auth.py show                      # masked view of cookies.json
python work/auth/xiaomi_auth.py refresh                   # re-login with stored passToken
python work/auth/xiaomi_auth.py login <phone> --code <code>  # non-interactive send+verify
```

Global flags: `--sid` (default `miothealth`), `--out` (default
`work/auth/cookies.json`), `--device-file`, `--json`.
Exit codes: `0` ok, `2` auth/server error (full body printed to stdout),
`3` captcha required, `4` extra verification required (2FA/notificationUrl).

### Observed CLI behaviour (harmless probes, no SMS)

```
$ python work/auth/xiaomi_auth.py send 123456
{ "code": 70008, "result": "error", "desc": "电话号码格式错误", ... }
send failed: sendServiceLoginTicket failed: 电话号码格式错误 | code=70008 ...
[exit 2]

$ python work/auth/xiaomi_auth.py verify 123456 000000
{ "code": 10001, "result": "error", "desc": "系统错误", ... }
verify failed: phoneInfo (SMS code verification) failed: 系统错误 | code=10001 ...
[exit 2]
```

`show` with a sample record prints `sid/userId/cUserId/serviceToken/ssecurity/
passToken/deviceId/locale/phone/createdAt/raw_cookie` with values masked.

---

## 5. What is needed from the user

1. **Phone number** (CN, +86) — for `send`.
2. **SMS code** received on that phone — for `verify`.
3. Optional: if the account has 2FA / "new device verification", Xiaomi may
   return `notificationUrl` after ticket auth; the code raises
   `NeedVerification` and prints the URL. That flow (identity/list →
   sendPhoneTicket → verifyPhone) is documented from prior art but **not**
   implemented yet — it would need another SMS/email code round.

Alternative (no SMS): the Lead/user can paste a cookie export from a logged-in
Mi Health session (`userId`, `cUserId`, `serviceToken`, `ssecurity`); save it as
`cookies.json` with `"sid":"miothealth"` and `build_session()` will use it.
A browser cookie import is more fragile than the SMS flow.

---

## 6. Risks / caveats

* **Captcha (`87001`)**: the app may demand an image captcha before sending the
  SMS. The CLI accepts `--captcha-code/--captcha-ick/--verify-token/--action`
  and exits `3` with the `captchaUrl` so the Lead can fetch the image and retry.
  No bypass is implemented (per task rules).
* **2FA / trusted-device (`notificationUrl`, `securityStatus != 0`)**: may
  interrupt the ticket-auth step; handled as `NeedVerification`, not implemented.
  Prior art (xiao-cli) reports headless re-login often hits captcha + email 2FA.
* **Rate limits (`70022`)**: don't retry `send` rapidly; SMS quotas apply.
* **Token lifetime**: `serviceToken` is short-lived (hours); `passToken` is
  long-lived but can be revoked. `refresh` re-mints without SMS.
* **Device identity**: keep `device.json` stable; changing `deviceId` may
  trigger extra verification.
* **CN only**: endpoints here are `account.xiaomi.com` (CN). Global
  (`account.xiaomi.com` vs regional) variants are out of scope.
* **Prior art vs verified**: the flow in §2 is from the APK's own classes
  (authoritative); the request/response *shapes* marked "verified" in §1 were
  observed live. Prior-art packages (`miservice`, `migate`, `micloud`,
  `mijiaAPI`) use the same endpoints but the *password* path
  (`serviceLoginAuth2` + `hash=md5(pwd).upper()`), which is not used here.

---

## 7. Next step (for the Lead)

1. Provide the user's phone number → run `send <phone>`.
2. Provide the SMS code → run `verify <phone> <code>`.
3. If `verify` fails at a specific step, the CLI prints the full response body
   (status + JSON) to stdout; send that output back.
4. On success, `cookies.json` contains everything needed for
   `hlth.io.mi.com/app/v1/eco/api_proxy` (`userId`, `cUserId`, `serviceToken`,
   `ssecurity`, `deviceId`).
