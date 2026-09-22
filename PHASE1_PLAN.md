# Eolia Home Assistant integration — Phase 1 (climate + sensors)

## Context

This repo (`panasonic-エオリア`) exists to reverse-engineer Panasonic's Eolia cloud API so AI
mode, ECONAVI, and the Dry-vs-Cool&Dehumidify distinction — none of which are visible over
ECHONET Lite, confirmed by a prior hardware investigation — can be controlled from Home
Assistant. `findings.md` now documents the complete, **live-confirmed** API contract: Auth0
Authorization Code + PKCE auth, a single `GET`/`PUT /eolia/v6/devices/{applianceId}/status`
endpoint, the full `operation_mode`/`ai_control` enums, and a real captured request/response
pair with the exact (non-obvious) JSON shape the backend actually accepts. That work is done;
this plan is the first step of turning it into an actual `custom_components/eolia/` integration.

Phase 1 scope (deliberately bounded, confirmed with Kevin): a working `climate` entity covering
power, the full `operation_mode` enum (including Dry vs Cool&Dehumidify), temperature, and
fan/swing; AI mode/ECONAVI as a `select`; plus read-only sensors for indoor/outdoor temp,
humidity, and air quality. Everything else the app does (weekly timer, AI scenes, eco history,
notifications, firmware checks, etc.) is explicitly deferred — see "Deferred" below.

Built at `custom_components/eolia/` inside this repo. Deployment to europa's real Home Assistant
is a separate, later step Kevin does himself — this repo stays self-contained per its existing
ground rules.

## The one hard external constraint

Eolia's Auth0 client (`JpNCoLeXs4rPMhWmnOjbOxat7MWTZEgr`) is registered as a **native mobile
app**, with its redirect URI hardcoded to `panasonic-eolia://auth.digital.panasonic.com/android/
com.panasonic.SmartRAC/callback` — a scheme we don't control and HA can't intercept. This rules
out HA's built-in `config_entry_oauth2_flow`/`application_credentials` machinery for the
authorization step, since that assumes HA's own callback URL is registered with the provider.
**A human has to complete the `/authorize` browser step by hand at least once per login** —
exactly the manual PKCE flow already proven working in `findings.md` and `pkce_login.sh`. The
refresh-token leg has no such constraint and is fully automatable.

**Confirmed live** (2026-09-23, direct test against `auth.digital.panasonic.com/authorize`):
the registered `panasonic-eolia://...` redirect_uri gets a real `302` to Auth0's `/login` page
(accepted); both a `http://homeassistant.local:8123/auth/external/callback` and a
`http://localhost:8123/...` redirect_uri each got an immediate
`error=unauthorized_client&error_description=Callback URL mismatch... is not in the list of
allowed callback URLs`. So this is a hard, externally-enforced constraint, not an assumption —
HA's standard OAuth2 helper genuinely cannot be used for the authorization step. Also confirmed:
the redirect is a raw HTTP 302 straight to the custom scheme (no intermediate HTML/JS page to
hook into), and Chrome's address bar reverts to showing the original `/authorize` URL rather
than the failed target — so there's no zero-setup way to read the `code` back from the browser
UI itself. On Android, the real app handles this invisibly because it's registered as that
scheme's handler; anything else needs either a technical step or a one-time local customization.

### Making the manual step as friendly as possible without assuming setup

Rejected the earlier plan revision (a required OS-level protocol-handler as the primary path) —
correctly pushed back on: it only works on whichever one machine has it registered, and a config
flow's documented instructions shouldn't assume a customized environment. Revised design:

- **Primary path (works on any device, zero setup): browser DevTools, but with foolproof
  step-by-step instructions**, shown directly in the config flow's `browser_pkce` form
  (`description_placeholders`, not just a README): *"1. Open this link. 2. Log in. 3. The page
  will fail to load (expected). 4. Open your browser's DevTools (F12, or Cmd+Option+I on Mac) →
  Network tab → reload isn't needed, just find the request whose Name starts with
  `panasonic-eolia:` → click it → copy the full 'Request URL' shown in the right-hand panel. 5.
  Paste that whole URL below."* `config_flow.py`'s submit handler parses either a bare `code` or
  a full URL defensively either way (already planned). This is guaranteed to work anywhere,
  which is the actual requirement — not the nicest possible UX, but honest about the constraint
  rather than assuming an environment.
- **Optional convenience, clearly labeled as such, not required**: `tools/eolia_login_helper/`
  in this repo — a one-time-setup OS protocol handler (desktop entry + `xdg-mime` registration)
  that catches the `panasonic-eolia://` redirect automatically and shows the code on a friendly
  local page instead of needing DevTools. Its own README states plainly this is optional,
  personal-machine tooling (useful for Kevin re-authenticating repeatedly during development),
  not something the config flow assumes or requires. The config flow's instructions never
  mention it — a user who wants it finds it in the repo, everyone else uses DevTools.

## File layout

```
custom_components/eolia/
├── manifest.json          # domain "eolia", iot_class: cloud_polling, config_flow: true
├── const.py                # DOMAIN, AUTH0_*, API_BASE, operation_mode/ai_control enums,
│                            #   hvac_mode bucket table, poll interval, error-code constants
├── auth.py                  # EoliaAuth: PKCE helpers, authorization_code + refresh_token
│                            #   exchange, expiry tracking, asyncio.Lock-guarded refresh
├── api.py                    # EoliaApiClient: get_devices/get_status/set_status,
│                            #   X-Eolia-Date generation, typed error parsing
├── models.py                  # EoliaDevice / EoliaStatus dataclasses, defensive .from_dict()
├── coordinator.py             # EoliaDataUpdateCoordinator(DataUpdateCoordinator[...]),
│                            #   async_set_status() does read-modify-write against cached state
├── config_flow.py             # menu → browser_pkce | paste_refresh_token; + reauth
├── climate.py                  # EoliaClimateEntity
├── select.py                   # ai_control select + operation_mode select (see below)
├── sensor.py                    # temps, humidity, air quality
├── switch.py                     # silence_control, nanoex, airquality-monitoring
├── strings.json / translations/en.json
└── __init__.py                    # async_setup_entry/async_unload_entry
```

Mirrors current HA integration conventions: fully `async`, `ConfigEntry.runtime_data` (typed
dataclass holding the api client + coordinator) rather than `hass.data[DOMAIN]`,
`DataUpdateCoordinator` driving all platforms, `CoordinatorEntity` as the entity base,
`EntityDescription`-driven sensor/switch definitions.

## Auth / config_flow

Hand-rolled `EoliaAuth` class, not a retrofit of HA's OAuth2 helper (that helper's refresh
bookkeeping assumes `application_credentials`, which itself assumes a normal callback — not
worth fighting for Phase 1).

`config_flow.py`:
1. **`async_step_user`** → menu: `browser_pkce` (primary) or `paste_refresh_token` (fallback,
   also useful for headless re-setup).
2. **`browser_pkce`**: generate PKCE verifier/challenge + `state` (same recipe as
   `pkce_login.sh`), build and show the `/authorize` URL, one text field for the user to paste
   back either a bare `code` or the full failed-redirect URL (parsed defensively either way). On
   submit, exchange for tokens, call `/userinfo` for `member_user_id` →
   `async_set_unique_id()` + `_abort_if_unique_id_configured()` (prevents duplicate entries for
   the same account), call `GET /devices` once to validate + get a nickname for the entry title.
3. **`paste_refresh_token`**: one field, validated via an immediate refresh-grant call, same
   unique_id/device-discovery tail as above.
4. **`async_step_reauth`**: triggered by `ConfigEntryAuthFailed` (raised when a refresh returns
   `invalid_grant`), reuses the same menu, ends in `async_update_reload_and_abort` against the
   existing entry.

Token storage: `access_token`/`refresh_token`/computed `expires_at` in `entry.data`.
`EoliaAuth.async_get_access_token()` refreshes when <1 day remains (generous given the 14-day
lifetime), behind an `asyncio.Lock`. Always persist whatever the refresh response contains;
Auth0 rotation behavior for this tenant isn't confirmed, so don't assume the refresh_token stays
constant.

## API client (`api.py`)

- Use HA's shared `aiohttp_client.async_get_clientsession(hass)`.
- `X-Eolia-Date` computed fresh per request via stdlib `zoneinfo` (`Asia/Tokyo`), independent of
  the HA host's configured timezone — this is a hard requirement per `findings.md`'s confirmed
  clock-skew check.
- Central `_request()`: non-2xx → parse `{"code","message"}` → typed `EoliaApiError`. Special
  case `E-21291-00002` → `EoliaClockSkewError` with an actionable log message (check host clock,
  nothing the integration can fix itself). Log unknown `E-21291-*` codes verbatim (only `00002`
  and `00007` are confirmed so far) so they can be folded back into `findings.md` later.
- 401/403 → one token refresh + one retry; still failing → `ConfigEntryAuthFailed`.
- Retries: transient network errors get one retry with backoff. **Never** auto-retry an
  `E-21291-*` application error (resending an identical bad request won't help, and repeated
  failed writes against production infra should stay a conscious, manual action — matches how
  this project already treated live control-endpoint testing).
- Writes are single-attempt, no auto-retry (avoids duplicate/flapping commands to physical
  hardware); failures surface as `HomeAssistantError` for the user to retry deliberately.
- Poll interval: 60s constant (unvalidated guess — `findings.md` documents no rate-limit
  behavior at all; revisit if real-world testing shows it's wrong).

## `coordinator.py` — the read-modify-write contract

`async_set_status(appliance_id, **changes)` is the **only** path that builds a PUT body. It
starts from the coordinator's last-known `EoliaStatus`, produces the exact 13-field payload
confirmed live — `ai_control, air_flow, airquality, nanoex, operation_mode, silence_control,
operation_status, temperature, timer_value, wind_direction, wind_volume,
wind_direction_horizon, wind_shield_hit` — applies only the caller's requested field(s), and
**never includes `applianceId` or `humidity`**, per the hard-won finding that blocked live
testing until real traffic was captured. After a successful PUT, update cached state from the
PUT response body directly (it echoes full state) rather than waiting for the next poll, so
entities reflect the change instantly.

`silence_control` has no readback (confirmed absent from every GET `/status` response) — cache
the last-sent value locally in the coordinator and always resend it; document this as a known
state-drift limitation (can go stale if changed via the physical remote or the real app).

## `climate.py` — mode mapping

`hvac_mode` carries a coarse bucket for standard thermostat-card/voice-assistant compatibility;
`preset_mode` carries the exact `operation_mode` wire value as the real source of truth
(confirmed approach — HA's fixed `HVACMode` enum can't represent all ~16 values, and this keeps
full fidelity on one entity rather than splitting it into a second select). Both
`async_set_hvac_mode`/`async_set_preset_mode` funnel through one internal
`_async_set_operation_mode()`.

Bucket table (subjective, flagged for correction against real device behavior once testable):

| `operation_mode` | HVACMode bucket |
|---|---|
| `Stop` | `OFF` |
| `Auto`, `AutoTempControl`, `KeepMode` | `AUTO` |
| `Cooling`, `CoolDehumidifying`, `MoistCooling` | `COOL` |
| `Heating`, `KeepHeating` | `HEAT` |
| `Blast`, `SmellCare`, `SmellCareSpot`, `NanoexCleaning`, `Cleaning` | `FAN_ONLY` |
| `Dehumidifying`, `ComfortableDehumidification`, `ClothesDryer` | `DRY` |
| `Other` | not offered as a settable preset; on read, log + display as `OFF` bucket rather than crash |

Power (`operation_status`) maps to `HVACMode.OFF` independent of `operation_mode`'s last value —
matches how the real GET/PUT pairs treat them as separate fields.

`ai_control` is its own `select.eolia_ai_mode` (`off`/`comfortable`/`comfortable_econavi`), not
part of the climate entity.

Fan/swing: `wind_direction_horizon` maps to `swing_horizontal_mode`
(`front/spot/wide/to_left/nearby_left/nearby_right/to_right/auto`, fully enumerated in
`findings.md`). `wind_volume` (fan speed) / `wind_direction` (vertical louver) ranges are **not**
confirmed — ship a provisional guessed level set (e.g. `0=auto, 1–5`), clearly commented as
provisional, validate during the manual smoke test, correct `findings.md` + the constant
afterward if wrong.

Temperature step: default `1.0°C` (unconfirmed from the single live capture — `20.0` doesn't
disambiguate; validate on first live control test).

## `select.py`, `sensor.py`, `switch.py`

- **`select.eolia_ai_mode`**: `off`/`comfortable`/`comfortable_econavi`, human labels via
  `strings.json`.
- **`sensor.py`**, all sourced from the same `GET /status` the coordinator already fetches (zero
  extra calls): indoor temp (`inside_temp`), outdoor temp (`outside_temp`, guard `None`/absent
  rather than show a bogus 0), indoor humidity (`inside_humidity`), air quality — state from
  `aq_name`, with `aq_value`'s numeric scale undocumented so expose it as a separate
  `entity_category: diagnostic`, disabled-by-default sensor rather than guessing its meaning.
  Note: this device currently has `airquality: false` (monitoring off), so the air-quality
  sensor may just read "off" in practice — document this so it isn't mistaken for broken.
- **`switch.py`**: `silence_control` (quiet mode, write-only/cached per above),
  `nanoex` (nanoeX), `airquality` (air-quality monitoring on/off) — all round-trip through the
  same `async_set_status()` contract.
- Capability gating: decide each entity's availability from whichever keys were actually present
  in the coordinator's first successful fetch for that device, not assumed present for every
  device (matches `findings.md`'s explicit note that capability gating isn't fully understood
  yet). A key disappearing on a later poll → that entity goes `unavailable`, not a coordinator
  failure.

## Deferred (not designed, not built this phase)

Weekly timer, AI scenes/learning/approach-home, eco monitoring/history/predictions, notification
settings, firmware update checks, power monitor, "Air Rich" companion device, clean-filter
status, multi-zone ceiling unit control (`two_way_built_in_ceiling`) and `ventilation`/
`circulation` (passed through untouched in PUT bodies if ever present, never actively
controlled), options flow (scan interval, etc.), multi-account beyond "run config flow again",
`/multipledevices/status` bulk-off.

## Testing plan

_Status: done, see CLAUDE.md's "unit test suite written and green" update (2026-09-22) for
what actually got built and one environmental gotcha worth knowing about before touching this
again. This section is kept as the original design record._

1. **`pytest-homeassistant-custom-component`** harness, `tests/` mirroring the component layout,
   `MockConfigEntry` + `aioresponses`/`aioclient_mock` intercepting both `auth.digital.
   panasonic.com` and `app.rac.apws.panasonic.com` — never touching the real API in automated
   tests.
2. **Fixtures from real captured traffic**: `tests/fixtures/status_response.json` (the live GET
   body), `control_request.json`/`control_response.json` (the real PUT captured 2026-09-23),
   and `devices_response.json` (the raw `GET /devices` body captured just now while writing this
   plan — see below). Token responses in tests use fabricated dummy values, never anything from
   the real `.eolia_tokens.json`.
3. **Highest-value test**: parametrized over the full `operation_mode` table asserting correct
   `hvac_mode`/`preset_mode` mapping both directions, plus a regression test asserting outgoing
   PUT bodies never contain `applianceId`/`humidity` and always contain `silence_control` — this
   directly encodes the finding that blocked live testing for a day.
4. Auth/token tests (authorization_code + refresh_token exchange mocks, expiry-buffer refresh
   triggering, `invalid_grant` → reauth), a header test (frozen time + non-JST host timezone →
   assert `X-Eolia-Date` still computed in JST), error-path tests for `E-21291-00002`/`00007`.
5. **Manual smoke test** once units pass: throwaway local `hass -c ./config` with
   `custom_components/eolia` symlinked in, real config flow through the UI (same manual
   copy-paste PKCE method already proven), confirm climate entity state matches the real app 1:1,
   one deliberate real control action (e.g. fan speed), confirm the physical AC responds and the
   next poll picks it up. No need to redo the rooted-AVD/mitmproxy setup — only needed again if a
   *future* phase has to reverse-engineer a new endpoint.
6. Keep live PUTs manual and deliberate in all cases — no automated test ever fires a real
   control write against the live device.

### Real `GET /devices` response (captured live while writing this plan, for the fixture)

```json
{
  "ac_list": [
    {
      "appliance_id": "EXAMPLEAPPLIANCEID0000000000000000000000000=",
      "nickname": "Yurt",
      "purchase_date": "2024-09-16",
      "shop_category_id": "0",
      "shop_area_id": "",
      "shop_name": "",
      "inst_place_id": "001",
      "memo": "",
      "appliance_type": 1,
      "product_code": "CS-712DX2-W",
      "product_name": "ルームエアコン　ホワイト",
      "hashed_guid": "f036d8c6bf9cc8caa99d7c3f45d7503da59cfe9c6c96f3c9ed3e1dadddeac57c",
      "device_register_num": 1,
      "initialize_flg": false,
      "repair_status": "initial",
      "point_code": "1223800",
      "vpa_enable": false
    }
  ]
}
```

## Repo hygiene + GitHub push

Kevin asked to push this repo to GitHub as a private repo. Before doing that, one thing worth
flagging rather than deciding silently: the repo currently also contains the full `jadx`
decompile of Panasonic's proprietary Eolia APK (`code/`, ~11,766 files) and the raw APK itself
(`エオリア+アプリ_7.11.7_APKPure.apk`, ~94MB). Pushing decompiled third-party proprietary source
to GitHub — even to a private repo — is legally murkier than keeping it local-only (copyright
applies regardless of repo visibility, and GitHub's own terms discourage redistributing
decompiled binaries). Recommend `.gitignore`-ing both `code/` and the `.apk` file, keeping the
push to the actual original work: `CLAUDE.md`, `findings.md`, `.gitignore`, and (once built)
`custom_components/eolia/` and `tools/eolia_login_helper/`. `.eolia_tokens.json`,
`.tools/`, and `.scratch_*` are already gitignored from earlier in this session.

Sequence once the plan is approved: add `code/` and `*.apk` to `.gitignore` → `git init` →
`git add` the intended files → first commit → `gh repo create --private` → `git push`. This
happens as one of the first steps after approval (repo backup), independent of whether Phase 1
code is finished yet — "when done" is read as "once this planning/setup round is done," not
"once all of Phase 1 is built."

## Verification

1. `pytest` passes for all the tests in §Testing plan, run locally in this repo.
2. Manual smoke test (§5 above) against Kevin's real account/device, confirming the config flow,
   climate entity, select, sensors, and switches all work end-to-end and match the real app's
   displayed state.
