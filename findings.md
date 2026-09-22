# Eolia API — static reverse-engineering findings

Source: APK `エオリア+アプリ_7.11.7_APKPure.apk` (package `com.panasonic.SmartRAC`), decompiled with
`jadx` CLI into `code/sources` (Java) and `code/resources` (manifest, resources, strings).
Code is R8/ProGuard-obfuscated (single/double-letter package and class names) but *not*
string-encrypted, so string resources and literals are all readable in plain text.

All of this came from **static analysis only** (Track 1). No live traffic has been captured
yet (Track 2 not started) — nothing below has been confirmed against a real request/response,
but it's all first-party strings/constants pulled directly from the shipping app, not guesses.

## Auth — Auth0, Authorization Code + PKCE (browser-based)

The app uses the real `com.auth0.android` SDK (not a custom auth scheme). Confirmed via
`com.auth0.android.provider.{WebAuthActivity,AuthenticationActivity,RedirectActivity}` in
`AndroidManifest.xml` and via `c9/c.java` (the app's Auth0 wrapper class).

- **Auth0 domain:** `auth.digital.panasonic.com`
- **Client ID:** `JpNCoLeXs4rPMhWmnOjbOxat7MWTZEgr` (public native-app client ID — not a secret,
  Auth0 native/PKCE clients ship this in the APK by design)
- **Audience:** `https://club.panasonic.jp/JpNCoLeXs4rPMhWmnOjbOxat7MWTZEgr/api/v1/`
  (string resource `audienceValue`)
- **Scope requested:** `openid offline_access eolia.control`
  (`c9/c.java:602` — `.f("openid offline_access eolia.control")`)
- **Redirect/callback:** custom scheme `panasonic-eolia://auth.digital.panasonic.com/android/com.panasonic.SmartRAC/callback`
  (manifest intent-filter on `RedirectActivity`, `android:pathPrefix="/android/com.panasonic.SmartRAC/callback"`)
- Login is a full browser/Custom-Tabs redirect flow (Auth0 `WebAuthProvider`-style), not
  resource-owner-password. User logs in with their club.panasonic.jp (Panasonic ID) account in
  the browser; app gets an authorization code back via the custom-scheme redirect, exchanges it
  for tokens. `offline_access` scope confirms a refresh token is issued and stored
  (`c9/c.java` — credential manager wraps Auth0's SecureCredentialsManager, checks `sso`
  expiry state, calls the Auth0 `/oauth/token` refresh endpoint on expiry — see
  `EoliaSplashActivity$a` inner class, "ssoExpire" logic).
- User profile is fetched from Auth0's `/userinfo`; the app reads
  `https://club.panasonic.jp/userinfo/app_metadata.member_user_id` out of the profile claims
  to identify the account (`EoliaSplashActivity.java` class `b`).
- Every API call attaches `Authorization: Bearer {access_token}` (the Auth0-issued JWT access
  token, not a separate app-issued session token). Token is refreshed transparently by the
  Auth0 SDK when `Auth0AccessToken.isValid()` is false (see `y8/e.java`).

**Practical implication for Track 2 / a from-scratch client:** this means login cannot be
reduced to a single POST with a JSON body — it's a real OAuth2 Authorization Code + PKCE
exchange against a standard Auth0 tenant. A non-Android client (e.g. a Home Assistant
integration) will need to either (a) drive an actual browser-based OAuth flow once and persist
the resulting refresh token, or (b) find out whether the `auth.digital.panasonic.com` tenant
also allows the Resource Owner Password Grant (untested — worth checking with a raw
`POST https://auth.digital.panasonic.com/oauth/token` using `grant_type=password` against the
above client_id/audience/scope; many consumer Auth0 tenants have this disabled, but it's cheap
to try before building a full webview-based login flow).

## API base URLs

Defined in `u8/a.java` (`Config.java`):

| Method | Returns |
|---|---|
| `u8.a.a()` | `https://app.rac.apws.panasonic.com/eolia` |
| `u8.a.b()` | `https://app.rac.apws.panasonic.com/eolia/v6` |
| `u8.a.c()` | `https://app.rac.apws.panasonic.com/eolia/json` (static JSON blobs — banners, transition URLs, aq_reference.json, etc. — not device control) |
| `u8.a.e()` | `https://static.rac.apws.panasonic.com/launch/` |

Base-URL selection logic (`y8/e.java` method `b()`): if the request path contains
`specifications/serial_no` or `notice/p-notice`, the base is `u8.a.a()` (no `/v6`); otherwise
almost everything (including all device status/control calls) goes to `u8.a.b()`
(`.../eolia/v6`).

## Request headers (every API call)

Set in `y8/e.java` (`PanaHttpClientProxy`), methods `f()` (GET) and `g()` (POST/PUT/DELETE):

```
Accept: application/json
Content-Type: application/json;charset=UTF-8
X-Eolia-Date: yyyy-MM-ddTHH:mm:ss   (local date/time, plain — not an HMAC signature, just a timestamp header)
Authorization: Bearer {auth0_access_token}
```

No request signing / HMAC was found — no signing key or digest computation anywhere in
`y8/e.java` or its callers. **`X-Eolia-Date` is not inert, though (confirmed live 2026-09-22):**
the server rejects requests with `E-21291-00002` ("server time and device time differ by 5+
minutes") if it's missing or skewed. It must be formatted as `yyyy-MM-ddTHH:mm:ss` in **JST**
(`Asia/Tokyo`), with no timezone offset in the string — sending UTC time fails the skew check
even though the wall-clock UTC time was correct, because the server assumes the naive
timestamp is JST (matches `Locale.getDefault()` on a JP-market phone).

## Device control — the actual endpoint

Both device status read and device control write hit **the same URL**:

```
GET  /eolia/v6/devices/{applianceId}/status   → StateDataRHResponse
PUT  /eolia/v6/devices/{applianceId}/status   ← ControlFetchCommandRHRequest (JSON body)
                                                → ControlFetchCommandResponse
```

Confirmed in `a9/d.java` (`AppTopApi.java`), methods `d()` (GET) and `f()` (PUT):
```java
str = getString(R.string.api_common_devices_file)      // "/devices"
    + "/" + URLEncoder.encode(applianceId, "utf-8")
    + getString(R.string.api_common_status);            // "/status"
```
`applianceId` is per-device, obtained from `GET /eolia/v6/devices` (device list —
`api_devices` / `api_common_devices_file`, both `"/devices"`).

### Control request JSON shape (`ControlFetchCommandRHRequest`)

```json
{
  "applianceId": "string",
  "operation_status": true,           // power on/off ("state" field, JSON key "operation_status")
  "operation_mode": "Cooling",        // see mode table below
  "operation_token": "string",        // from AirconMainSetting/local storage, see b9.a.X()
  "temperature": 26.0,
  "wind_volume": 0,                   // int, fan speed level
  "wind_direction": 0,                // int, vertical louver
  "wind_direction_horizon": "auto",   // horizontal louver: front/spot/wide/to_left/nearby_left/nearby_right/to_right/auto
  "timer_value": 0,
  "humidity": 50,
  "air_flow": "not_set",              // "not_set" | "quiet" | "powerful" | "long" (quiet/powerful/nanoeX-long mode)
  "wind_shield_hit": "not_set",       // "not_set" | "shield" | "hit"
  "airquality": false,
  "nanoex": false,
  "ai_control": "off",                // *** AI mode / ECONAVI — see below ***
  "circulation": "off",               // only sent if device supports it (checked via z().R(id,"circulation"))
  "temp_correction": 0.0,             // only sent in "AutoTempControl"(おまかせ温度制御) mode
  "local_outside_temp": 0,            // only sent in "AutoTempControl" mode
  "addhumidifying_value": "string",   // only sent for heating/blast/auto/AutoTempControl modes on devices supporting it
  "ventilation": { "type": "supply_only", "supply_only": { "mode": "string" } },  // only if device has ventilation/ventilation_two_mode capability
  "two_way_built_in_ceiling": { "zone_mode": "2way|zone1|zone2", "zone1_wind_direction": 0, "zone2_wind_direction": 0 }
}
```

Which optional fields get included is capability-gated per device (checked against
`FunctionsResponse.ac_function_list`, function ids `nanoex` / `airquality` / `ai_control`, and
against local per-device flags via `c9.z().R(applianceId, featureName)` — this looks like it
reads a locally cached capabilities list, presumably itself populated from
`GET /eolia/v6/products/{productCode}/functions` or similar, fetched at device-registration
time. Not fully traced yet.)

### `operation_mode` — the "Dry vs Cool & Dehumidify" answer

**This is the field the whole project was chasing.** Mapping table lives in `s8/k.java`
(`ConversionModel`), methods `e()` (API string → Japanese UI label) / `f()` (reverse):

| `operation_mode` (wire value) | Japanese UI label | English meaning |
|---|---|---|
| `Auto` (default/fallback) | 自動 | Auto |
| `Cooling` | 冷房 | Cool |
| `Heating` | 暖房 | Heat |
| `KeepHeating` | キープ暖房 | Keep-warm heating |
| `Blast` | 送風 | Fan only |
| `Dehumidifying` | その他除湿 | "Other" dehumidify (plain/generic dry, no cooling emphasis) |
| `CoolDehumidifying` | 冷房除湿 | **Cool & Dehumidify** — the mode that was thought to be ECHONET-invisible |
| `ComfortableDehumidification` | 除湿 | **Dry** — plain comfort-dehumidify mode |
| `ClothesDryer` | 衣類乾燥 | Clothes-drying mode |
| `MoistCooling` | しっとり冷房 | "Moist cooling" (humidity-aware cool) |
| `AutoTempControl` | おまかせ温度制御 | "Leave it to us" auto temp control (uses `temp_correction`/`local_outside_temp`) |
| `KeepMode` | ダブル温度設定 | Dual-temperature keep mode |
| `SmellCare` / `SmellCareSpot` | においケア / においケア ねらって脱臭 | Odor-care / targeted odor-care |
| `NanoexCleaning` | おでかけクリーン | "Away clean" nanoeX mode |
| `Cleaning` | おそうじ | Self-clean |
| `Stop` | 停止 | Stop |
| `Other` | その他 | Other |

So **"Dry" vs "Cool & Dehumidify" are two distinct `operation_mode` string values**
(`ComfortableDehumidification` vs `CoolDehumidifying`), sent as plain strings in the same JSON
body as everything else — not a separate flag, not ECHONET, confirming the original hardware
investigation.

### `ai_control` — AI mode / ECONAVI

Enum lives in `OperationMode.java` (`ExclusiveBean`) and is mapped to Japanese in `s8/k.java`
method `a()`:

| `ai_control` (wire value) | Japanese UI label | Meaning |
|---|---|---|
| `off` | AI オフ | AI mode off |
| `comfortable` | AI 快適 | AI comfort mode on |
| `comfortable_econavi` | AI エコナビ | AI comfort mode **+ ECONAVI** combined |

So ECONAVI isn't independently toggleable in this API — it's expressed as a third state of the
same `ai_control` field alongside AI-comfort-mode. (Matches the physical remote: AI and ECONAVI
share one button/indicator on this unit family.)

### Status response shape (`StateDataRHResponse`, GET)

Same field set as the control request but with response-side JSON keys (slightly different
from the request body in a few spots — Gson `@SerializedName` per field, both classes list
their own key constants at the top if you need the exact diff):

Key fields: `operation_status` (bool, power), `operation_mode` (string, see table above),
`temperature`, `target_temperature`, `inside_temp`, `inside_humidity`, `outside_temp`,
`wind_volume`, `wind_direction`, `wind_direction_horizon`, `humidity`, `air_flow`,
`wind_shield_hit`, `airquality` (bool), `nanoex` (bool), `ai_control` (string, table above),
`circulation`, `device_errstatus` (bool), `operation_priority` (bool), `feeling_temperature`,
`temp_correction`, `timer_value`, `sleep_control_status`, `addhumidifying_value`, `ventilation`,
`two_way_built_in_ceiling`.

## Other endpoints found (from `strings.xml`, keys prefixed `api_`)

Not individually traced to call sites, but paths are plain strings so worth listing for later.
All relative to `/eolia/v6` unless noted. `%1$s` etc. are `String.format` placeholders
(typically `applianceId`).

```
/agreements                                          api_agreements
/auth/agreements  /auth/login  /auth/logout          api_auth_*
/devices                                              list devices
/devices/{id}/status                                  ← the one that matters (see above)
/devices/{id}/settings[?term_id={id}]                 temp-monitor notification settings
/devices/{id}/powermonitor/settings
/devices/{id}/firmware/agreement /firmware/updateinfo
/devices/{id}/aiscene/settings /aiscene/learning /aiscene/approachhome
/multipledevices/status                               bulk "turn all off"
/poc/devices/{id}/area /contribution/econavi /eco/history /eco/latest /eco/predictions
/airrich/settings /airrich/status                      "Air Rich" (humidifier?) companion device
/cleanfilter/status
/airquality/current /airquality/history
/eco/current /eco/history /eco/prediction
/weather/forecast
/weeklytimer
/notifications/settings
/products  /products/{productCode}/functions /defaultnames
/gw  /gw/{id}/devices/entry                            gateway (network adapter) management
/locations /rooms/category /shops/category /shops/area
/versions                                              app version check (only endpoint that's called with an invalid/absent token — see y8/e.java `.contains("/versions")` special case)
/specifications/serial_no                              uses the non-/v6 base (u8.a.a())
/notice/p-notice                                       uses the non-/v6 base (u8.a.a())
```

Beach-prefixed endpoints (`/beach/...`) are dashboard "card" widgets — coming-home scene,
going-out scene, sleep scene, eco monitor, air-quality monitor, human-detection monitor, etc.
Not control endpoints, informational only.

## Target device

Panasonic CS-712DX2-W (Eolia X series, JP), nicknamed "Yurt" in the app.
`applianceId` (opaque per-account device token, not a secret by itself — useless without a
valid bearer token, but treat it as loosely sensitive anyway): `EXAMPLEAPPLIANCEID0000000000000000000000000=`

## Live confirmation (2026-09-22)

Full auth + API round-trip done by hand with `curl`, confirming everything above against the
real account/device rather than just static analysis:

1. Authorization Code + PKCE flow driven manually through a desktop browser (`/authorize` →
   login → captured `code` from the failed `panasonic-eolia://...` redirect in browser
   devtools) → `POST /oauth/token` with `grant_type=authorization_code` → got back
   `access_token` / `refresh_token` / `id_token`, `expires_in: 1209600` (14 days),
   `scope: "openid eolia.control offline_access"`.
   - Tokens are cached locally in `.eolia_tokens.json` (chmod 600, gitignored — **never commit
     this file or paste its contents into chat/docs**). Refresh via the standard Auth0
     `grant_type=refresh_token` call against the same `/oauth/token` endpoint before the
     access_token's `exp` claim passes, using the cached `refresh_token`.
   - Decoded (not printed here) `id_token`/`access_token` claims are worth knowing about:
     audience list includes `https://pdpauth-a1.panasonic.auth0.com/userinfo` — reveals the
     underlying Auth0 tenant's actual custom-domain backend host
     (`pdpauth-a1.panasonic.auth0.com`) behind the `auth.digital.panasonic.com` custom domain.
2. `GET /eolia/v6/devices` with `Authorization: Bearer {access_token}` + the required
   `Accept` / `Content-Type` / `X-Eolia-Date` (JST) headers → **200**, returned `ac_list` with
   one device: `product_code: "CS-712DX2-W"`, `nickname: "Yurt"`, plus `appliance_id`,
   `hashed_guid`, `purchase_date`, `point_code`, etc.
3. `GET /eolia/v6/devices/{applianceId}/status` (URL-encoded appliance_id) → **200**, real
   live state:
   ```json
   {
     "operation_status": true,
     "operation_mode": "ComfortableDehumidification",
     "inside_temp": 22.5,
     "inside_humidity": 60,
     "outside_temp": 23.0,
     "nanoex": true,
     "ai_control": "off",
     "airquality": false,
     "aq_value": -1,
     "aq_name": "off",
     ...
   }
   ```
   This is the AC actually running in **Dry mode** (`ComfortableDehumidification`) with AI off
   and nanoeX on right now — matches the app's UI 1:1 and confirms the `operation_mode` /
   `ai_control` enum mapping from static analysis is correct in practice, not just in the
   decompiled strings.

**`PUT` (control write) tried 2026-09-22, blocked — needs live traffic capture to resolve.**
With explicit go-ahead, tried switching `operation_mode` to `"Cooling"`. Four request shapes
were tried against the real `/devices/{applianceId}/status` endpoint, all returning the
identical generic backend error:
```json
{"code":"E-21291-00007","message":"アプリケーションエラーが発生しました。ご迷惑をおかけし申し訳ございません。"}
```
1. A full realistic payload (all non-conditional `ControlFetchCommandRHRequest` fields, mode
   changed to `Cooling`, temperature 26.0) — 400, `E-21291-00007`.
2. An exact no-op echo of the current GET `/status` response translated field-for-field into
   the request shape (mode *unchanged*, same everything) — same 400/`E-21291-00007`. This rules
   out anything mode- or value-specific: even a byte-for-byte round-trip of the server's own
   last-known state fails.
3. A minimal payload (only `applianceId`/`operation_status`/`operation_mode`/`temperature`) —
   same error.
4. Attempt 1 again with an explicit `"operation_token": ""` — same error.

`-v` output confirmed headers/method/URL-encoding/Content-Length were all correct and the
request reached the backend (the JSON error shape is the app's own custom format, produced by
application logic, not a generic AWS API Gateway validation rejection — so auth, routing, and
JSON parsing all succeeded; something in the *processing* fails every time regardless of body
content).

**Leading theory:** `operation_token` genuinely is required/validated, but not in a way that
accepts empty/absent — it's likely checked against a value the *server* already has on file for
this device (this AC has ~2 years of real usage history via the actual app, unlike a
freshly-registered device where the first-ever call might legitimately have no prior token).
`ControlFetchCommandRHRequest`'s Gson `ExclusionStrategy` in `a9/d.java` also has a `switch`
block jadx couldn't decompile (`shouldSkipField`, referencing `nanoex`/`airquality`/
`humidity`/`applianceId` hash cases) — there's per-device capability-gating logic here that's
invisible to static analysis, and it's plausible the real app is doing something with fields or
sequencing that a hand-built request can't replicate blind.

**2026-09-22 update — user-cert approach tried on Kevin's real phone, failed as expected:**
mitmproxy + firewall setup on the laptop worked fine (confirmed traffic reaching the proxy), and
a CA cert was installed as a user cert on the phone. The Eolia app still failed
(`E-21291-T0004` — a *client-side* code the app generates locally for any request that throws
before getting an HTTP response, not a server-sent error) and mitmweb's console showed:
```
Client TLS handshake failed. The client does not trust the proxy's certificate for
app.rac.apws.panasonic.com (OpenSSL Error([('SSL routines', '', 'ssl/tls alert certificate unknown')]))
```
So it's confirmed: this isn't necessarily hardcoded certificate pinning (no pinning library was
found statically, and none needed to be — this is consistent with plain Android 7+ default
behavior, where apps only trust the **system** CA store unless they explicitly opt in to user
certs via `network_security_config.xml`, which Eolia apparently doesn't). The distinction
doesn't matter practically — either way, a **rooted** environment with the CA pushed into
`/system/etc/security/cacerts/` is required, which is what Track 2 in this file's plan (system-
level cert install on a rooted Google-APIs AVD) was for all along. No shortcut found; falling
back to the originally-planned Track 2 setup.

**2026-09-23 — RESOLVED. Real control traffic captured, root cause confirmed.** After
downgrading to a Google APIs AVD at **API 28 (Android 9)** — pre-Mainline, so the classic
`/system/etc/security/cacerts` + one reboot technique works with no APEX/bind-mount
shenanigans — mitmproxy successfully intercepted real Eolia app traffic on Kevin's actual
account/device. (API 34's Conscrypt-as-Mainline-module setup never accepted the injected CA
despite every static-level check passing: correct SELinux context, correct subject-hash
filename, bind-mounted into `/apex/com.android.conscrypt/cacerts` from the root mount
namespace via `nsenter`, and even an APK-level `network_security_config.xml` patch adding
explicit `<certificates src="system"/><certificates src="user"/>` trust-anchors. All confirmed
correct via `aapt dump xmltree`/`aapt dump resources` on the actually-installed APK. Whatever
Android 14's real trust-anchor resolution path is, it isn't a live directory scan of those
locations — never got to the bottom of it, and API 28 sidesteps the question entirely.)

Real captured `PUT /eolia/v6/devices/{applianceId}/status` (switching to Cooling, 20°C, fan 3,
front airflow) — request body:
```json
{"ai_control":"off","air_flow":"not_set","airquality":false,"nanoex":true,"operation_mode":"Cooling","silence_control":false,"operation_status":true,"temperature":20.0,"timer_value":0,"wind_direction":3,"wind_volume":3,"wind_direction_horizon":"front","wind_shield_hit":"not_set"}
```
→ **200 OK**, response:
```json
{"operation_token":"RdGdV8zfXxFdTvL0","appliance_id":"EXAMPLEAPPLIANCEID0000000000000000000000000=","operation_status":true,"operation_mode":"Cooling","temperature":20.0,"wind_volume":3,"wind_direction":3,"inside_humidity":65,"inside_temp":21.5,"outside_temp":23.0,"operation_priority":false,"timer_value":0,"device_errstatus":false,"airquality":false,"nanoex":true,"aq_value":-1,"aq_name":"off","ai_control":"off","air_flow":"not_set","wind_shield_hit":"not_set","wind_direction_horizon":"front"}
```

**Root cause of the earlier hand-built `curl` PUT failures (`E-21291-00007`), confirmed by
diffing against this real request:**
- **`applianceId` must NOT be in the body at all** — it's only in the URL path. All four of our
  earlier hand-built attempts included it. This is almost certainly what the undecompiled
  `ExclusionStrategy.shouldSkipField` switch in `a9/d.java` was doing for the `applianceId`
  hash case — unconditionally stripping it before serialization.
- **`humidity` is also omitted** for this device/mode, despite the Java model
  (`ControlFetchCommandRHRequest.setData()`) appearing to set it unconditionally — same
  exclusion-strategy logic evidently strips it too, at least for this device's capability set.
- **`silence_control: false` must be included** — a field none of our hand-built attempts sent.
- **`operation_token` genuinely isn't required** — the earlier "stale server-side token" theory
  was wrong. This request succeeded with no `operation_token` in the body at all (this was a
  fresh app install with nothing cached locally, same as our raw `curl` attempts), and the
  response is where a fresh token (`RdGdV8zfXxFdTvL0`) gets minted for future requests to
  build on.

**Corrected minimal working control-request shape** (fields confirmed present in a real
successful request for this device — `CS-712DX2-W`): `ai_control`, `air_flow`, `airquality`,
`nanoex`, `operation_mode`, `silence_control`, `operation_status`, `temperature`,
`timer_value`, `wind_direction`, `wind_volume`, `wind_direction_horizon`, `wind_shield_hit`.
No `applianceId`, no `humidity`, no `operation_token` needed for a first-ever control call on
this device. (Optional fields seen in the Java model — `circulation`, `ventilation`,
`two_way_built_in_ceiling`, `temp_correction`, `local_outside_temp`, `addhumidifying_value` —
still weren't present here either, consistent with this specific unit not supporting those
capabilities, matching what its `GET /status` responses have shown all along.)

This closes out the project's original goal: the full read/write device-control API is now
confirmed working end-to-end against the real account and real hardware, including the exact
request shape needed for `operation_mode`/`ai_control` (the Dry-vs-Cool&Dehumidify and
AI/ECONAVI questions this project started from).

**Recommended next step (superseded by the above, kept for context):** capture one real control action from the actual Eolia app, which is
a much lighter lift than full Track 2 (rooted AVD + Frida) turned out to need for `GET`s. No
certificate-pinning bypass code (TrustKit, custom `CertificatePinner`, etc.) was found anywhere
in the decompiled sources — worth trusting that and trying the cheap route first: install
mitmproxy's CA as a **user** cert on Kevin's real phone (Settings → Security → install
certificate — this *is* the "user trust store" Android 7+ normally ignores for apps, but it's
worth testing whether this app happens to still trust it, before assuming otherwise) and point
the phone's Wi-Fi proxy at the laptop's mitmproxy instance. If a real mode-change from the real
app on the real phone shows up in mitmproxy with TLS successfully intercepted, that's the exact
request shape (including whatever's really going on with `operation_token`) with zero rooting/
emulator/Frida work. Only escalate to the full rooted-AVD Track 2 setup if the user-cert
approach hits a TLS handshake failure (i.e., the app *does* pin certs after all, just not via
one of the common libraries jadx would have flagged).

## What's confirmed vs. still open

**Confirmed by static analysis (high confidence — these are literal strings/constants in the
shipping APK, not inferred):**
- Auth0 tenant, client ID, audience, scope, redirect URI
- API base URL and the no-signing-just-a-Bearer-token auth model
- The single GET/PUT `/devices/{id}/status` endpoint shape
- The full `operation_mode` and `ai_control` enums and their Japanese-label mapping — this
  answers the original "Dry vs Cool & Dehumidify" and "AI mode / ECONAVI" questions definitely
  at the *protocol* level (assuming the firmware honors what the app sends, which live traffic
  would confirm)

**Still open / needs Track 2 (live traffic) or further static digging:**
- ~~Whether the Auth0 tenant permits Resource Owner Password Grant~~ — **resolved: disabled**
  (`unauthorized_client`, see below). Authorization Code + PKCE is required and works (see
  "Live confirmation" below); a manual desktop-browser + devtools flow is enough, no emulator
  needed.
- ~~The real `applianceId` for the CS-712DX2-W unit~~ — **resolved**, see "Live confirmation".
- A `PUT` to `/devices/{id}/status` hasn't been tried live yet — everything above is GET-only so
  far. Needed to fully confirm the control direction (not just read) actually works as modeled.
- Exact `operation_token` semantics (looks like an idempotency/session token per device, stored
  locally via `b9.a.X(context, applianceId)` — not yet traced to where it's first obtained)
- Per-device capability gating (`c9.z().R(applianceId, featureName)`) — likely backed by
  `GET /products/{productCode}/functions`, not yet traced
- Confirming against a real response that `CoolDehumidifying` vs `ComfortableDehumidification`
  actually behave differently on this specific hardware (static analysis only proves the app
  *sends* two distinct values — it doesn't prove the unit's firmware does something different
  with them, though that's the whole premise of the app exposing them as separate menu items)

## Next step

Track 1 (static) has answered the two motivating questions (mode enums for AI/ECONAVI and
Dry-vs-Cool&Dehumidify) well enough to maybe skip straight to a minimal live test: try the
Auth0 `password` grant once by hand (curl), and if that works, a `GET /eolia/v6/devices` +
`GET /eolia/v6/devices/{id}/status` round-trip would confirm the whole model end-to-end without
needing the full mitmproxy/Frida/rooted-AVD Track 2 setup. Fall back to Track 2 only if the
password grant is disabled on this tenant.

**2026-09-22 update — ROPC tried, disabled on this tenant:**
```
POST https://auth.digital.panasonic.com/oauth/token  grant_type=password
→ {"error":"unauthorized_client","error_description":"Grant type 'password' not allowed for the client."}
```
So the Resource Owner Password Grant is off for this client — confirms a real Authorization
Code + PKCE exchange is required, as the app does. Before committing to full Track 2
(mitmproxy + rooted AVD + Frida), try the lighter option first: drive the login in an ordinary
desktop browser and pull the authorization `code` out of the failed `panasonic-eolia://...`
redirect via browser devtools (Network tab), since that custom scheme has no handler on a
laptop and the failed-navigation request is still visible/inspectable. Then do the
code-for-token exchange by hand with `curl`. This gets a real access+refresh token pair without
touching an emulator at all. Only fall back to full Track 2 if this doesn't work (e.g. if the
tenant validates `redirect_uri` strictly against a registered native scheme in a way that
blocks inspecting it, or if there's bot/device-fingerprint detection on the `/authorize` page).
