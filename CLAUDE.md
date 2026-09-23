# CLAUDE.md

Guidance for Claude Code sessions working in this directory.

## Purpose

Reverse-engineer the Panasonic Eolia Android app's cloud API so its AI mode / ECONAVI /
"Dry vs Cool & Dehumidify" sub-mode can eventually be exposed as a Home Assistant climate
integration. Goal is a working understanding of the current API (base URL, auth flow,
control endpoints) — a full HA component is a later step, not this one.

This is a side project running on Kevin's laptop, separate from his home server ("europa").
Nothing here touches europa directly; if the eventual integration gets built, it gets deployed
to europa's Home Assistant separately.

## Background — why this project exists

Kevin's AC is on Home Assistant via ECHONET Lite (the `hems_echonet_lite` HACS integration,
entity `climate.home_air_conditioner`). A live-hardware investigation on 2026-09-22 confirmed
AI mode, ECONAVI, and the Dry/Cool&Dehumidify distinction **never touch ECHONET at all** — the
physical remote (and presumably the Eolia app) talks to the unit over IR/a separate channel,
and the network adapter only mirrors a subset of state (mode/temp/fan/louver/power-saving) onto
ECHONET. This is a confirmed hardware/firmware ceiling — no ECHONET integration change can ever
surface these modes. The only path to controlling them from HA is riding whatever API the Eolia
app itself uses.

**Existing libraries are dead — don't waste time reinstalling them.** Checked
`avolmensky/python-panasonic-eolia` (PyPI `panasoniceolia`) and its HA wrapper
`avolmensky/panasonic_eolia` — both archived 2025-12-01, maintainer's note says Panasonic changed
their API and the component may no longer work. (This was literally the same `platform:
panasonic_eolia` block Kevin had removed from europa's `configuration.yaml` as dead orphaned
config back on 2026-08-30, before he'd made the ECHONET-vs-Eolia connection.) `aurimasniekis/
eolia-client` (Node) is too small/undated to trust either. As of 2026-09-22 there is no known
working open-source client for the current Eolia API — this needs fresh reverse engineering.

**Target hardware:** Panasonic CS-712DX2-W (Eolia X series, JP market). App package name:
`com.panasonic.SmartRAC` (JP-only, likely won't show in a non-JP Play Store account).

Full history/context lives in `services/home-automation.md` in the `os-management` git repo
(europa's docs-as-memory repo) — search for "Eolia". That repo is the system-of-record for
europa; don't try to edit it from here. If useful, clone it locally for reference, or just ask
Kevin to paste the relevant section.

## Plan

Two tracks. Do static first — it needs no rooting, no emulator config, and may turn out to be
sufficient on its own.

### Track 1 — Static: decompile the APK (start here)

1. Get the Eolia APK. It won't be pullable from a non-JP Play Store account — get it from
   APKMirror/APKPure, or extract it off Kevin's phone with an APK-extractor app if he has it
   installed there.
2. Open it in **jadx-gui** (free decompiler — `jadx` on Homebrew/apt, or download the release).
3. Grep the decompiled source for:
   - Hardcoded base URLs / hostnames (`https://`)
   - `Retrofit`/`OkHttp` client setup — usually reveals the API base + interceptors
   - Classes/methods with "Api", "Auth", "Login", "Session" in the name — auth flow and any
     custom header signing (Panasonic APIs have historically used app-specific auth headers,
     sometimes HMAC-signed)
   - Request/response model classes — gives you the JSON shape for device state and commands
4. Write findings into this directory (a `findings.md` or similar) as you go: endpoints found,
   auth mechanism, anything unclear that would need live traffic to confirm.

### Track 2 — Dynamic: capture live traffic (only if static leaves real gaps)

1. In Android Studio's Device Manager, create an AVD using a **"Google APIs" system image —
   not "Google Play"**. The Play variant is a locked "user" build; Google APIs images are
   rootable, which later steps need.
2. Sideload the APK: `adb install eolia.apk` (skips needing it in a JP Play account).
3. Run **mitmproxy** on the laptop (host machine). Point the emulator's Wi-Fi proxy at
   `10.0.2.2:<mitmproxy port>` — `10.0.2.2` is the emulator's alias for "the host machine".
4. Install the mitmproxy CA into the emulator's **system** trust store, not just the user
   store — `adb root`, remount `/system` writable, push the cert into
   `/system/etc/security/cacerts/`. Android 7+ ignores user-added CAs for app traffic by
   default, so skipping this step means you'll just see TLS failures.
5. If the app still fails to connect (certificate pinning — plausible, since it handles account
   credentials): run **Frida** with an SSL-unpinning script (e.g. a multi-unpinning gadget)
   against the rooted emulator to force the app to trust the proxy despite pinning.
6. Drive the app manually in the emulator — log in, toggle AI mode, ECONAVI, the dry sub-mode
   — while watching mitmproxy's flow list. Capture the real endpoints, headers, and JSON
   payloads for each action.

## Status

**Track 1 (static) done as of 2026-09-22, and it answered the motivating questions.** APK
(`エオリア+アプリ_7.11.7_APKPure.apk`, v7.11.7) pulled and decompiled with `jadx` CLI into
`code/sources` + `code/resources`. Full findings in [`findings.md`](findings.md) — read that
first. Summary:

- Auth: real Auth0 tenant (`auth.digital.panasonic.com`, client id
  `JpNCoLeXs4rPMhWmnOjbOxat7MWTZEgr`), Authorization Code + PKCE, scope
  `openid offline_access eolia.control`. Browser-based login, not a simple POST.
- API base: `https://app.rac.apws.panasonic.com/eolia/v6`, `Authorization: Bearer {auth0_token}`,
  no request signing.
- **The whole device state/control API is one endpoint:**
  `GET`/`PUT /eolia/v6/devices/{applianceId}/status`, plain JSON body.
- **Confirmed the Dry-vs-Cool&Dehumidify and AI/ECONAVI questions at the protocol level**: both
  are just string values of the `operation_mode` field (`ComfortableDehumidification` = Dry,
  `CoolDehumidifying` = Cool & Dehumidify) and the `ai_control` field (`off` /
  `comfortable` / `comfortable_econavi`), sent as plain JSON — not ECHONET, as expected.

**Update, same day — fully confirmed live, no Track 2 needed.** ROPC (password grant) is
disabled on the tenant, as suspected, but a manual Authorization Code + PKCE flow driven
through an ordinary desktop browser (grab the `code` out of the failed
`panasonic-eolia://...` redirect via devtools, no emulator needed) got real tokens. Confirmed
by hand with `curl` against the live API:
- `GET /eolia/v6/devices` → real device, `product_code: "CS-712DX2-W"`, nicknamed "Yurt" in the
  app.
- `GET /eolia/v6/devices/{applianceId}/status` → real live state, and it matched the app's UI:
  unit currently running `operation_mode: "ComfortableDehumidification"` (Dry), `ai_control:
  "off"`, `nanoex: true`. This is the actual proof, not just decompiled strings, that the
  operation_mode/ai_control model in findings.md is correct.
- One correction to the earlier static read: `X-Eolia-Date` is *not* inert — server enforces a
  5-minute clock-skew check against it, and it must be formatted in JST regardless of client
  timezone or you get `E-21291-00002`.

Tokens are cached locally in `.eolia_tokens.json` (gitignored, chmod 600 — never put its
contents in a doc, memory, or chat). `expires_in` is 14 days; refresh via Auth0's
`grant_type=refresh_token` before then.

**Update, same day — PUT (control write) tried and blocked.** With explicit go-ahead, tried
switching `operation_mode` to `Cooling` via `PUT /devices/{applianceId}/status`. Four different
request shapes (full realistic payload, exact no-op echo of current state, minimal payload,
explicit empty `operation_token`) all returned the identical generic backend error
`E-21291-00007`, regardless of content — meaning it's not a wrong-field-value problem, and not
guessable further from the payload alone. Stopped there rather than keep trial-and-error against
production infra. Full detail + leading theory (`operation_token` likely validated against a
server-side value this ~2-year-old device already has on file, that a fresh hand-built request
can't produce) in findings.md.

**Update, 2026-09-23 — RESOLVED. Project's core goal is done.** User-cert-on-real-phone was
tried and failed (TLS handshake rejected — Android's default network_security_config doesn't
trust user CAs, and this app doesn't opt in). Fell back to Track 2's rooted-AVD approach, but
API 34's Mainline/APEX-managed Conscrypt module turned out to reject an injected CA no matter
what was tried (system cacerts push + correct SELinux context, APEX bind-mount via `nsenter`
into the root mount namespace, even an APK-patched `network_security_config.xml` — all verified
correct at the byte level via `aapt dump xmltree`, still didn't work; never found out why).
**Fix: use API 28 (Android 9) instead** — pre-dates Conscrypt-as-Mainline-module entirely, so
the classic `/system/etc/security/cacerts/` + one reboot technique just works, no bind-mount
tricks needed.

With that, real Eolia app traffic was captured on Kevin's real account/device, confirming the
full read/write control API end-to-end — see findings.md's "2026-09-23 — RESOLVED" section for
the real captured request/response and the corrected control-request JSON shape (turns out
`applianceId` must NOT be in the PUT body — only in the URL — and `humidity` gets excluded too;
both were why the earlier hand-built `curl` attempts failed with `E-21291-00007`, not the
`operation_token` theory from the previous update).

**What's left, if this becomes an actual HA integration later:** per-device capability gating
(the `ExclusionStrategy` logic in `a9/d.java`, now understood empirically rather than from
decompiled code — `applianceId`/`humidity` get excluded for this device, likely device
capability dependent), and the Resource-Owner-Password-Grant-is-disabled auth problem for a
non-interactive HA setup (a real user needs to do the Authorization Code + PKCE dance at least
once — browser + devtools is enough, no app/emulator needed for that part — then HA just needs
to hold onto and refresh the resulting refresh_token).

## Phase 2 — Home Assistant integration (in progress)

Kevin asked to build an actual HA integration covering every feature of the app, phased.
Full design lives in the approved plan this was built from (was at
`~/.claude/plans/rosy-doodling-marble.md` in the session that wrote it — copy its content into
this repo as a design doc if that plan file isn't available in a future session). Summary:

- **Phase 1 scope** (in progress): `climate` entity (power, full `operation_mode` enum via
  `preset_mode`, coarse `hvac_mode` buckets, temperature, fan speed, both swing axes),
  `select.eolia_ai_mode` (the `ai_control` field), read-only sensors (indoor/outdoor temp,
  humidity, air quality), and switches for `nanoex`/`airquality` monitoring/`silence_control`.
  Everything else the app does (weekly timer, AI scenes, eco history, notifications, firmware
  checks, etc.) is explicitly deferred to a later phase.
- **Auth constraint discovered and confirmed live**: Eolia's Auth0 client is a native-app
  registration with a fixed `redirect_uri` — tested directly against `/authorize` with
  alternate redirect URIs (e.g. HA's own callback), got an explicit `unauthorized_client` /
  "Callback URL mismatch" error every time. HA's built-in OAuth2 config-flow helper genuinely
  cannot be used. `config_flow.py` hand-rolls the PKCE dance instead, with DevTools-based
  code extraction (foolproof step-by-step instructions baked into the form) as the
  zero-assumptions primary path — explicitly **not** relying on any one-time OS/browser
  customization, since that doesn't generalize past one specific machine.
- **Code written** (all at `custom_components/eolia/`): `const.py`, `exceptions.py`,
  `models.py`, `auth.py`, `api.py`, `coordinator.py`, `config_flow.py`, `entity.py`,
  `climate.py`, `select.py`, `sensor.py`, `switch.py`, `__init__.py`, `manifest.json`,
  `strings.json`/`translations/en.json`. Fixtures from real captured traffic at
  `tests/fixtures/` (`status_response.json`, `control_request.json`, `control_response.json`,
  `devices_response.json`). **Unit-tested and green** as of 2026-09-22 (61 tests via a local
  `.venv` + `pytest-homeassistant-custom-component` — see the "unit test suite" update in the
  Status section above) but **still not runtime-tested against a real, running Home
  Assistant** (config flow through an actual UI, real entity registration, etc.) — that part
  still resumes on europa, which already has HA installed. See "Next step" above.
- **Provisional/unconfirmed values, updated 2026-09-23 (most are now resolved — see the big
  update below)**: `wind_volume`/`wind_direction` ranges are now **fully live-confirmed**
  (`wind_volume`: 0=auto/1=min/5=max; `wind_direction`: 0=auto/1-5=fixed positions/6=swing).
  Still open: temperature step (guessed `1.0°C`, not yet disambiguated from 0.5°C), and the
  `hvac_mode` bucket table for the less common `operation_mode` values (SmellCare,
  NanoexCleaning, AutoTempControl, etc. — still bucketed by best guess, not yet confirmed
  against real device behavior; `KeepMode` specifically *is* now confirmed → `AUTO`).
- **Update, 2026-09-22 — unit test suite written and green on the laptop.** Reversed the
  earlier "don't install the harness on this laptop" call from last session: a local
  `.venv/` (gitignored) with `pytest-homeassistant-custom-component` (pulls in
  `homeassistant==2025.1.4`) is safe, local-only, and doesn't touch europa, so it made more
  sense to just do it than wait. 61 tests across `tests/test_models.py`, `test_climate.py`,
  `test_coordinator.py`, `test_auth.py`, `test_api.py`, `test_config_flow.py` — all green,
  confirmed stable across repeated runs. Covers the two "highest-value" tests
  PHASE1_PLAN.md called out (full `operation_mode` → `hvac_mode`/`preset_mode` table both
  directions; PUT-body regression asserting `applianceId`/`humidity` never appear and
  `silence_control` always does), plus auth token exchange/refresh, the `X-Eolia-Date`
  JST-regardless-of-host-tz header, `E-21291-00002`/`00007` error mapping, the 401-retry
  path, and the config flow's DevTools-pasted-URL code extraction. To reproduce:
  `source .venv/bin/activate && python -m pytest -q` (venv already set up in this repo).
  Read through every component file first (const/models/auth/api/coordinator/entity/
  climate/select/sensor/switch/config_flow/__init__) — no bugs found, matches
  PHASE1_PLAN.md's design faithfully.
  - **One real environmental gotcha worth remembering if it resurfaces**: a single test
    that raises through a real (mocked) HTTP call intermittently failed *teardown only*
    (test logic itself always passed) with a `pytest_homeassistant_custom_component`
    thread-leak false positive. Root cause fully traced: `pycares` (aiodns's backend,
    which HA's `aiohttp_client` helper hardcodes) keeps one process-global background
    thread that starts lazily the first time *any* `Channel` object is garbage-collected
    anywhere in the process — not when created — so its appearance in
    `threading.enumerate()` is GC-timing-dependent and can land on whichever test happens
    to be running. Fixed at the root in `tests/conftest.py`
    (`_prime_pycares_shutdown_thread`, session-scoped autouse): construct-and-drop a real
    `AsyncResolver` in a throwaway loop before any test's thread-leak snapshot runs.
    Confirmed stable across 5+ repeated full-suite runs after the fix. If a similar
    single-test-teardown-only thread-leak flake shows up again, check this first before
    re-deriving it from scratch.
  - Not yet automated: a full `config_flow` integration test driving the actual HA flow
    manager (menu → browser_pkce/paste_refresh_token → entry creation/reauth) end-to-end —
    PHASE1_PLAN.md's testing plan treats that as covered by the manual smoke test (§5) on
    europa instead, not as an automated unit test; only the flow's pure
    `_extract_authorization_code` parser is unit-tested here.
- **Update, 2026-09-23 — live feature walkthrough on europa via a new CLI tool, several
  real findings, and real code integrated as a result.** This session ran on europa itself
  (not the laptop), with the real physical unit available. Instead of jumping straight to
  the HA config-flow UI, built `tools/eolia_cli.py` first — a standalone script that
  imports `auth.py`/`api.py`/`models.py` directly (no reimplementation, so zero drift from
  what HA actually runs) and drives `login`/`devices`/`status`/`set`/`customsettings`/
  `set-double-temp` subcommands against the real cloud API without needing a running HA
  instance at all. This was the right call: it let every feature get walked through and
  cross-checked against the official app one at a time, far faster than iterating through
  a full HA config flow would have, and surfaced several real findings before they could
  become confusing bugs later:
  - **New CLI-only environment fix**: a plain `aiohttp.ClientSession()` (not going through
    HA's `aiohttp_client` helper) still defaults to aiodns/c-ares for DNS whenever aiodns is
    importable, and c-ares doesn't get along with europa's `systemd-resolved` stub at
    `127.0.0.53` (`DNSError: (5, 'DNS server does not implement requested operation')`,
    unrelated to the pytest-side pycares thread issue from 2026-09-22). Fixed in
    `tools/eolia_cli.py`'s `_new_session()` by forcing `aiohttp.resolver.ThreadedResolver`
    explicitly. Also hit and fixed a version mismatch (`pycares` 5.0.1 paired with `aiodns`
    3.2.0, which wants `pycares<5`) left over from the previous session's thread-leak
    debugging — pinned `pycares==4.11.0` to match; `requirements-test.txt` doesn't pin
    `pycares` directly since `aiodns==3.2.0`'s own dependency resolution handles it
    correctly from a clean install, this was only an issue because of manual reinstalls.
  - **RESOLVED — the ~2 minute write lockout (`E-21291-01718`) is `operation_token`
    continuity, not "another device".** Long investigation arc: first attributed to the
    official app writing on menu close (confirmed real — Kevin saw a "writing settings"
    modal with zero value change); then reproduced through the real HA integration's own
    writes ALONE (~18s apart, zero other clients — Kevin confirmed directly), which broke
    that theory; Kevin then revealed a real second client (a separate Eolia app on an
    AVD) genuinely *was* active during the original CLI-session occurrence, so that
    theory wasn't wrong, just incomplete. **Finally settled with a controlled A/B test
    via the CLI**: two consecutive writes ~15-20s apart **succeeded** when the second
    echoed back the operation_token from the first's response; an otherwise-identical
    write **failed** the usual way without it. So the real mechanism is write
    continuity via this token (which every successful `/status` or `/customsettings` PUT
    response includes, but which this integration's requests never sent back) — "another
    device" was just generic/misleading error text, and a real second client (no token
    tracking of its own) would trip the same mechanism, explaining why that theory kept
    testing true without being the actual cause. **Fixed in `coordinator.py`**: a new
    `_operation_token_cache` (same no-GET-readback pattern as `silence_control`/
    `humidity`) now auto-caches and echoes this token on every write to either endpoint.
    Full A/B test data in `tests/fixtures/live_captures/38`; see `02`/`04`/`37` for how
    the theory evolved. **Only tested via the CLI so far — not yet re-verified through
    the real HA integration with the fix deployed** (next step, see below).
  - **`operation_mode=KeepMode` (the app's "double temperature setting") fully resolved
    end-to-end.** The low/high range is **not** in `/status` at all (confirmed live before
    a separate Claude session, working from the decompiled APK on a different machine,
    found and documented the real location: `GET`/`PUT /devices/{id}/customsettings`,
    `double_mode_temp: {status, high, low}` — see findings.md's "KeepMode / double
    temperature setting" section, added in commit `3d76aa2`). This session then
    live-confirmed the full round-trip including the **first-ever live PUT** to that
    endpoint (never tried before): minimal field set works (`double_mode_temp` + `peak_cut`,
    no `operation_token` needed in the request, same pattern as `/status`), and discovered
    one more new error code, `E-21291-02009` ("temperature settings must be at least 5
    degrees apart" — `high`/`low` need a ≥5°C gap). **Kevin explicitly asked to integrate
    this now rather than defer it**, so it's real code, not just findings: `models.py` gained
    `EoliaCustomSettings`/`EoliaDoubleModeTemp`, `api.py` gained
    `async_get_custom_settings`/`async_set_custom_settings`, `coordinator.py` polls it
    alongside `/status` each cycle (non-fatal on failure — capability gating for this
    resource across devices isn't confirmed) and gained `async_set_custom_settings()`
    mirroring `async_set_status()`'s read-modify-write contract, and there's a new
    `number.py` platform (`number.eolia_double_temp_low`/`_high`) plus a new
    `EoliaDoubleTempEnabledSwitch` in `switch.py`. New observation worth knowing: the
    range resets to `{status: false, high: 0, low: 0}` once the unit leaves `KeepMode` —
    it isn't preserved for next time.
  - **`air_flow` (quiet/powerful/long) and `wind_shield_hit` (shield/hit) — both fully
    live-confirmed** against the app (all non-default values individually verified) and now
    exposed as real entities: `select.py` was generalized from a single hardcoded
    `EoliaAiModeSelect` class into a `SELECT_DESCRIPTIONS`-driven pattern (matching
    `sensor.py`/`switch.py`'s existing style) covering `ai_mode` (unchanged behavior/
    unique_id) plus the two new selects. Note from Kevin's own observation: `air_flow` is
    independent of `wind_volume` (fan speed) — `long` noticeably increases air volume even
    while `wind_volume` stays at `0`/auto, so they're not the same axis.
  - **`wind_direction` (vertical louver) — full range resolved, including a subtle
    two-layer state model.** Confirmed: `0`=auto, `1`-`5`=fixed positions (`1`="upper",
    `3`≈"medium", `5`="straight down" — all app-confirmed), `6`=swing/oscillate (previously
    not in the guessed `0-5` range at all — found by asking Kevin to physically test the
    app's separate "left-right arrow" toggle, which turned out to be a swing on/off button,
    not horizontal or auto as first guessed). Non-obvious behavior worth remembering: while
    the app's vertical **auto** toggle is on, `/status` always reports `wind_direction=0`
    regardless of what the API writes — the write isn't rejected or lost, it's just
    invisible/inert until auto is turned off *in the app* (no API field found to toggle auto
    itself). By contrast, a fixed-position write (`1`-`5`) sent via the API **does**
    immediately override **swing** (`6`) — confirmed by writing `2` while the unit was
    actively oscillating and watching it stop and move to position `2`. `const.py`'s
    `PROVISIONAL_WIND_VOLUME_LEVELS`/`PROVISIONAL_WIND_DIRECTION_LEVELS` were renamed to
    `WIND_VOLUME_LEVELS`/`WIND_DIRECTION_LEVELS` (dropping the "provisional" framing) with
    the corrected `0-6` range for direction; `WIND_DIRECTION_SWING = 6` added as a named
    constant. `ai_control` was also observed to drift on its own from
    `comfortable_econavi` to `comfortable` mid-session with no write from either side
    (Kevin confirmed he didn't touch it) — logged as an open question in
    `tests/fixtures/live_captures/09`'s notes, not yet understood, but reinforces that the
    coordinator's poll-and-trust-the-server design (rather than trusting "what we last set")
    is the right call.
  - **All of the above captured as real request/response pairs** in
    `tests/fixtures/live_captures/01`-`16` (with a `README.md` index), each cross-checked
    against the official app, several corrected in-place when Kevin's follow-up
    observations overturned an earlier working theory (e.g. the device-lockout cause) —
    worth reading through if picking this back up, since the notes capture *why* each
    conclusion was reached, not just the final answer.
  - **New tests added, suite still green (83 total, up from 61)**: `tests/test_models.py`
    (`EoliaCustomSettings`/`EoliaDoubleModeTemp`), `tests/test_api.py`
    (`customsettings` GET/PUT, `EoliaDeviceLockedError`/`E-21291-02009` mapping),
    `tests/test_coordinator.py` (customsettings polling is non-fatal on failure,
    `async_set_custom_settings`'s read-modify-write contract and listener notification),
    new `tests/test_select.py` and `tests/test_number.py`, plus `test_climate.py` gained
    checks for `KeepMode`'s bucket and the corrected 0-6 swing range. New fixture:
    `tests/fixtures/customsettings_response.json`.
- **Update, 2026-09-23 continued — dehumidifying modes, and a major new finding:
  `ComfortableDehumidification` requires `humidity` in the PUT body.** Continued the live
  walkthrough (captures 17-24 in `tests/fixtures/live_captures/`, index in that dir's
  `README.md`):
  - **`CoolDehumidifying` ("Cool & Dehumidify") confirmed live via a real write** — the
    other half of this project's original motivating question (Dry vs Cool & Dehumidify),
    previously only confirmed for the Dry side. Has a real settable target temperature
    (24°C tested), unlike Dry.
  - **Resolved the `wind_direction` auto asymmetry** left open from the earlier update:
    writing `wind_direction=0` via the API **does** work and turns the app's vertical-auto
    toggle on (confirmed by writing it from a fixed position and watching the app's toggle
    flip). Combined with the earlier finding, the full picture is: the API can always
    *enter* auto, but can never *leave* it (a `1`-`5` write while already at `0` stays
    silently inert) — only the app's own toggle can turn auto off.
  - **Major finding: `ComfortableDehumidification` ("Dry") requires `humidity` in the PUT
    body — the one exception to the "always exclude humidity" rule** documented back at
    project start. Also requires `temperature=0.0` (a real value like `24.0` is rejected
    with `E-21291-01712` — this mode targets humidity, not temperature). Discovered
    because Kevin knew from the app's UI that Dry mode has a humidity-target slider;
    three earlier attempts without `humidity` all failed with generic errors
    (`E-21291-01712`/`E-21291-00007`) and gave no hint that a field outside the normal
    contract was the actual problem. Bisected the valid range live: **exactly `{50, 55,
    60}`** (5% steps, capped at 60% — not 80% or 100% as naturally guessed from typical AC
    humidity-target conventions). Same generic `E-21291-00007` for every rejected value,
    no distinguishing signal. New constants `DRY_MODE_HUMIDITY_RANGE`/
    `DRY_MODE_HUMIDITY_STEP` added to `const.py`, plus a corrected comment on
    `CONTROL_REQUEST_FIELDS` documenting the exception.
  - Also ruled out along the way: plain `Dehumidifying` (as opposed to
    `ComfortableDehumidification`) was never confirmed as an actual app-reachable option on
    this device — the app's "dehumidification" menu item turned out to just be
    `ComfortableDehumidification`. Whether plain `Dehumidifying` is real on any device is
    still an open question.
- **Update, 2026-09-23 continued further — Dry mode's humidity target wired into a real
  entity, by explicit request ("Yeah it needs to be in HA").** `coordinator.py`'s
  `async_set_status()` now forces `temperature=0.0` and includes `humidity` (from a new
  local cache, `get_humidity()`/`_humidity_cache` — same no-GET-readback pattern as
  `silence_control`) whenever the *resulting* `operation_mode` is
  `ComfortableDehumidification`, and excludes `humidity` entirely otherwise — this is
  centralized in the coordinator so it works correctly no matter which entity triggers
  the mode switch, matching the project's existing "one place builds the payload"
  design. New `EoliaDryHumidityNumber` in `number.py` (translation key
  `dry_humidity_target`) exposes it, deliberately **not** using `ClimateEntity`'s native
  `target_humidity` — HA's climate humidity slider has no step-size concept, and this
  field's valid values are a hard-restricted `{50, 55, 60}`, not a continuous range, so a
  free-form 1%-granularity slider would let a user pick an invalid value and get a
  cryptic rejection. A plain `number` entity supports `native_step` directly, so it's
  used instead (same reasoning that led to `number.py` over `ClimateEntityFeature.
  TARGET_TEMPERATURE_RANGE` for the double-temp values earlier). The entity is only
  `available` while `operation_mode` is actually `ComfortableDehumidification` (unlike
  the double-temp numbers, which stay available independent of current mode, since
  `/customsettings` is a genuinely separate resource — humidity is not, it's gated purely
  by current mode with no separate backing resource at all). 90 tests now (up from 83) —
  `tests/test_coordinator.py` gained 5 new tests for this read-modify-write behavior,
  `tests/test_number.py` gained 2 (had to instantiate a real entity rather than
  introspect class attributes directly — HA's `NumberEntity` implements `_attr_*` as
  class-level properties for its `cached_property` optimization, so accessing them on the
  class itself returns the descriptor, not the assigned value — a real gotcha worth
  remembering if it comes up again testing other entity attribute bounds).
- **Update, 2026-09-23 — running live on europa via Docker, config flow done, real bugs
  found and fixed by monitoring live HA<->cloud traffic.** The integration is installed
  in the real `homeassistant` Docker container on europa (`docker cp` into
  `/config/custom_components/eolia/`, `docker restart` to reload), config flow completed
  through the actual UI, and `climate.eolia_yurt` plus the select/sensor/switch/number
  entities are live against the real unit. Per Kevin's request ("can you monitor the
  communications between HA and the aircon? There will be impossible combinations we
  have to guard against"), debug logging was temporarily enabled
  (`custom_components.eolia.api: debug` in `configuration.yaml`'s `logger:` block --
  still on, intentionally, for the next session; remove once this feature-audit pass is
  fully done) and watched live via `docker logs -f` while driving the real app/HA UI.
  Real bugs found and fixed this way, all covered by new tests, all redeployed and
  reverified live:
  - **Silent no-op setting a temperature in Dry/ClothesDryer mode.** Both modes have no
    user-settable temperature at all (server always wants `0.0`), but the climate
    entity's temperature slider was still active and `async_set_temperature` just
    forwarded whatever was asked -- `coordinator.py` was already silently overwriting it
    back to `0.0`, so the write 200'd but visibly changed nothing, with zero feedback.
    Fixed in `climate.py`: a `_NO_TARGET_TEMPERATURE_MODES` guard now raises a clear
    `HomeAssistantError` naming the actual reason (Dry targets humidity instead; use
    `number.eolia_dry_humidity_target`; ClothesDryer's temperature is just
    fixed/automatic) before the request ever reaches the coordinator.
  - **The mirror-image bug**, caught by live-testing the fix above: switching *away*
    from Dry/ClothesDryer (e.g. `climate.set_preset_mode` to `Cooling`) with no explicit
    temperature carried Dry's forced `0.0` straight into a mode that requires a real
    target, failing with `E-21291-01712` -- reproduced live via the real HA UI.
    `coordinator.py` now tracks the last real (nonzero) temperature seen from any poll or
    write (`_temperature_cache`) and substitutes it (or `FALLBACK_TEMPERATURE = 24.0` if
    none observed yet) whenever a mode switch would otherwise carry over an invalid
    `0.0` -- never overriding an explicit caller-supplied temperature.
  - **`KeepHeating` confirmed not selectable on this device.** Deliberately picked from
    the real HA preset dropdown, rejected with `E-21291-01711` -- the same generic-error
    code plain `Dehumidifying` hits -- despite an otherwise-valid payload (real
    temperature, correct field set). Excluded from `climate.py`'s settable preset list,
    same treatment as `Dehumidifying`.
  - **New operation_mode discovered: `Nanoe`.** Requesting `Blast` (fan-only) with
    `nanoex: True` gets silently substituted server-side for a previously-unknown wire
    value, `Nanoe` -- reproduced twice, independent of `ai_control`. Before this was
    understood it showed up as an "Unknown Eolia operation_mode" warning and displayed
    as the misleading OFF `hvac_mode` bucket. Now modeled properly in
    `EoliaOperationMode`/`_HVAC_MODE_BUCKETS` (buckets to `FAN_ONLY`, like `Blast`) but
    deliberately left out of the settable preset list -- it's only reachable by picking
    `Blast` and toggling the nanoeX switch, not directly.
  - **`MoistCooling` confirmed to silently downgrade to plain `Cooling`.** Unlike
    `KeepHeating`, this doesn't error at all -- `200 OK`, but the returned
    `operation_mode` doesn't match what was requested, exactly the same "server accepts
    but doesn't actually apply it" class of bug already guarded against for
    `double_mode_temp` (see the "double-temperature switch" fix from earlier this
    session). `async_set_status` now compares the requested `operation_mode` (when the
    caller explicitly asked to change it) against what actually came back, and raises a
    clear `HomeAssistantError` on any mismatch -- with one carved-out exception for the
    known-legitimate `Blast`+`nanoex`->`Nanoe` substitution above, and only checked when
    `operation_mode` was itself part of the requested change (so an *incidental*
    substitution as a side effect of some other field, e.g. toggling nanoex while
    already in `Blast`, is correctly not flagged). Left selectable rather than excluded,
    since the failure mode here is "clear error, real state still gets cached" rather
    than "hard rejection" -- same design choice as `KeepMode`'s double-temp mismatch
    check.
  - 142 tests now (up from 132 at the start of this pass).
- **Next step**: resume the live "impossible combinations" audit -- systematically drive
  every remaining `operation_mode`/field combination through the real HA UI while
  watching `docker logs -f homeassistant | grep -i eolia` (the debug logger is still on
  for exactly this). Not yet exercised this way: `SmellCare`/`SmellCareSpot`/
  `NanoexCleaning`/`Cleaning`/`Auto`/`AutoTempControl`/`KeepMode` combined with
  nanoex/ai_control/silence_control in various combinations, and the double-temperature
  (`KeepMode`) and Dry-humidity number entities haven't been exercised live through HA
  yet either (only unit-tested + confirmed via the CLI previously). Also still open: the
  temperature step (0.5 vs 1.0°C) and the remaining unconfirmed `hvac_mode` bucket
  entries for `SmellCare`/`SmellCareSpot`/`NanoexCleaning`/`Cleaning`/`AutoTempControl`.

## How to leave notes for next time

Keep this file (or a linked `findings.md` in this same directory) updated as work progresses —
what's been tried, what worked, what the actual API turned out to look like, and the concrete
next step. Treat it as living memory the same way `os-management` does for europa: a future
session (or Kevin, reading it cold) should be able to pick up exactly where the last one left
off without re-deriving context.
