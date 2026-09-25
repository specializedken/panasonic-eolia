# Integration development log

The dated log of building `custom_components/eolia/`: scope, the live feature walkthrough, every
bug found by watching real traffic, the fuzzing/A-B rounds, and the corrections made along the
way. Entries are chronological and **later entries correct earlier ones** — when two disagree
(e.g. the temperature step, the `MoistCooling` downgrade, the "one-way" vertical louver), the
later one wins. Raw evidence for most claims is in
[`../tests/fixtures/live_captures/`](../tests/fixtures/live_captures/README.md).

For the Lovelace card see [`lovelace-card.md`](lovelace-card.md); for the API contract itself see
[`findings.md`](findings.md).

_Moved verbatim from `CLAUDE.md` on 2026-09-25._ (One policy in these entries has since changed:
"Panasonic's icons are never committed" — on 2026-09-25 the 12 icons the card uses began to be committed
under `custom_components/eolia/www/icons/`; see [`lovelace-card.md`](lovelace-card.md).)

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
  (Temperature step: **resolved 2026-09-24 → 0.5°C**, see the fuzz update below.) Still open: the
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
  phase1-plan.md called out (full `operation_mode` → `hvac_mode`/`preset_mode` table both
  directions; PUT-body regression asserting `applianceId`/`humidity` never appear and
  `silence_control` always does), plus auth token exchange/refresh, the `X-Eolia-Date`
  JST-regardless-of-host-tz header, `E-21291-00002`/`00007` error mapping, the 401-retry
  path, and the config flow's DevTools-pasted-URL code extraction. To reproduce:
  `source .venv/bin/activate && python -m pytest -q` (venv already set up in this repo).
  Read through every component file first (const/models/auth/api/coordinator/entity/
  climate/select/sensor/switch/config_flow/__init__) — no bugs found, matches
  phase1-plan.md's design faithfully.
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
    phase1-plan.md's testing plan treats that as covered by the manual smoke test (§5) on
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
    `EoliaDoubleTempEnabledSwitch` in `switch.py` (removed 2026-09-25, see the card notes below). New observation worth knowing: the
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
    silently inert) — only the app's own toggle can turn auto off. **CORRECTED 2026-09-24**:
    that "inert" behaviour was `wind_shield_hit` being on, not vertical auto itself -- with
    shield/hit off, a fixed position leaves auto fine (see the fuzz update below).
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
  REMOVED at the end of the audit; re-add that two-line block to trace requests again) and watched live via `docker logs -f` while driving the real app/HA UI.
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
  - **`MoistCooling` was seen silently downgrading to plain `Cooling` (NOT reproduced
    2026-09-24: 6/6 A/B tries accepted it -- see the fuzz update below).** Unlike
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
  - **`KeepMode` is entered via `/customsettings`, not `/status`.** Setting
    `operation_mode=KeepMode` on `/status` is always rejected (`E-21291-01711`); the mode
    is entered by writing `double_mode_temp.status=true` (with a valid >=5 degree range)
    on `/customsettings`, which also powers the unit on -- and writing `status=false`
    powers it off (`Stop`). The range is only stored while the setting is on: the server
    returns `200` but silently discards high/low (returns `0/0`) if `status=true` isn't
    in the same write, which is why the range "reset to 0/0" outside `KeepMode`.
    `climate.py` now routes the `KeepMode` preset through `async_set_custom_settings`;
    `coordinator.py` fills a default 23/28 range when enabling with none, fills the
    missing bound when only one is set, and no longer flags the expected range reset when
    turning it off (a false-positive of the mismatch check, found live). New errors:
    `E-21291-02006` (invalid range, e.g. `high=0, low=16`).
  - **`AutoTempControl` not selectable**: a real `25.0` gets `E-21291-01712`, `0.0` gets
    `E-21291-00007` -- real contract unknown (decompiled APK might show it). Excluded.
  - **Unexplained**: Kevin reported the unit turning on at ~14:06 while the cloud
    reported `Stop` and only rejected/`status=false` writes had been sent. Unresolved.
  - **Per-model capability flags exist and explain most rejections.** The app fetches
    `GET /products/{productCode}/functions` (found in decompiled `a9/d.java`; same host
    and auth as `/status`) -> `{function_id: true/false}` and only offers a mode/feature
    when its flag is true. For CS-712DX2-W: `auto_temp_control`, `smell_care_spot`,
    `reheat_dehumidification` (plain `Dehumidifying`), `airquality`, `circulation`,
    `ventilation`, `good_sleep_control`, `zone` are all **false**, matching every live
    rejection; `smell_care`, `cleaning`, `nanoex_cleaning`, `blast`, `clothes_dryer`,
    `comfortable_dehumidification`, `moist_cooling` are true. `KeepHeating` has no flag in
    the list, so its rejection is still unexplained; `MoistCooling` is flagged true (and was
    accepted normally in the 2026-09-24 A/B run). Implemented: `api.async_get_functions`,
    `coordinator.functions`/`supports()` (fetched once, non-fatal, unknown = allow),
    `const.OPERATION_MODE_FUNCTION_IDS`; `climate.preset_modes` is now filtered per model,
    and the air-quality switch is only created if the model has `airquality`. The
    hardcoded exclusions (`Dehumidifying`, `AutoTempControl`, `KeepHeating`, `Nanoe`) stay,
    since they are untested on models that do support them.
  - **The clean family (`SmellCare`/`SmellCareSpot`/`NanoexCleaning`/`Cleaning`) RUNS the
    unit while `/status` says `operation_status: false`** (live for SmellCare,
    NanoexCleaning, Cleaning; the server also forces `nanoex` itself: on for SmellCare,
    off for NanoexCleaning). The app special-cases them everywhere (init exempts them from
    the "status false = stopped" logic; never saves them as the "last mode"). `climate`
    now reports them as `FAN_ONLY`. **Stopping one**: `operation_mode: Stop` is rejected
    (`E-21291-01711`); it works with the app's own normalized stop body
    (`ControlFetchCommandRHRequest.setData`, status=false branch): `operation_status:
    false`, `operation_mode: "Auto"`, `temperature: 16.0`, `wind_volume: 0`,
    `wind_direction: 0`, `wind_direction_horizon: "auto"` -- live-confirmed the unit
    stopped. The mode-mismatch check ignores the expected `Stop` when powering off. The
    "can't change X while the AC is off" guard still fires in these modes (untested).
  - **Temperature range 16-30C now enforced** (`climate` min/max, `const.TARGET_TEMPERATURE_RANGE`):
    HA's default 7-35C let a `35.0` through, rejected `E-21291-00007` in Auto. `Auto` at
    25 and 30 confirmed live. **Dry humidity number confirmed live through HA** (`55`
    sent, echoed back).
  - **Bare `climate.turn_on` was broken, now fixed** (found in the post-audit gap review):
    it carried `operation_mode: Stop` into a power-on write, rejected `E-21291-01711`. The
    thermostat-style card has no power button (on = pick a mode), so this only bites the
    service (automations/voice). `coordinator` now remembers the last real running mode
    (`_last_mode_cache`; never the clean family, `Nanoe` saved as `Blast`) and `turn_on`
    powers on into it via the preset path, falling back to `Auto` (e.g. after an HA
    restart). Confirmed live: `Auto`, `200`.
  - **Concurrent writes caused the `E-21291-01718` lockout too** (found dragging a number
    slider): two writes ~0.6s apart both echoed the same token, so the second was rejected
    as "another device" -- the token-continuity theory again. `coordinator` now serialises
    every write behind one `asyncio.Lock` so each picks up the fresh token; confirmed live
    (the next write left the instant the previous response arrived, with its new token).
    The lockout error is translated to English on both write paths (the server's message is
    Japanese-only), including that it also fires on the first write after an HA restart.
  - **Double-temp changes while `KeepMode` is on work**, with a nudge: moving one bound
    within 5 degrees of the other (`E-21291-02009`) moves the other bound to keep the gap
    when that stays in range (low 16-25, high 21-30). Confirmed live (high 27->26 moved low
    22->21). The `02009` error message is translated to English too.
  - **`Nanoe` is a readback only, so any write made while the unit reads `Nanoe` failed**
    (`E-21291-01711`): turning nanoeX off in `Nanoe` re-sent `operation_mode: Nanoe`.
    `coordinator` now sends `Blast` whenever the current mode reads `Nanoe`. Confirmed live
    both ways: Blast + nanoeX on -> `Nanoe` (`200`), nanoeX off -> `Blast` (`200`).
  - **Per-mode hidden settings audited.** Fan speed is honoured in `ClothesDryer` and Dry
    (the app hides it but the server stores it). `ai_control` is silently reverted to `off`
    (`200 OK`) in `Blast` and `ClothesDryer`, but stored in Dry; `coordinator` now raises
    "The unit ignored the AI mode change -- AI isn't available in <mode> mode" when a
    requested `ai_control` comes back different (confirmed live in HA for `Blast`).
  - **Dry humidity IS readable** (correction to the "write-only" claim above): the server
    reports `humidity` in GET and PUT responses while in Dry. `EoliaStatus.humidity` is
    parsed and the coordinator restores its cache from it, so an HA restart no longer
    resets the target to 50 (it did, live). `silence_control` is still genuinely write-only.
  - **Still-untested combinations** (ranked; the first two, the nanoeX one and the per-mode
    hidden settings are done -- louvers in `KeepMode` and `air_flow`/shield-hit in the clean
    modes weren't individually tried): double-temp low/high changes while `KeepMode`
    is on (and the >=5 gap from two separate sliders); settings the app hides per mode
    (fan speed in Dry/ClothesDryer, `ai_control` in ClothesDryer/Blast, louvers in
    KeepMode, `air_flow`/shield-hit in the clean modes); changing settings during a clean
    mode (the "AC is off" guard fires); nanoeX toggling around `Nanoe`; `CoolDehumidifying`
    through HA; first write after an HA restart (in-memory `operation_token`/
    `silence_control`/humidity caches lost); token refresh; multiple devices.
  - 185 tests now (up from 132 at the start of this pass).
- **Audit wrapped up (2026-09-23) -- next step: the custom Lovelace card.** The live
  "impossible combinations" audit through the real HA UI is done; the integration is stable
  enough to build on. Kevin's motivation for a card: many settings depend on the current
  mode and the stock climate card can't express that (e.g. Dry has a humidity target and no
  temperature, `KeepMode` a range, the clean modes run with `operation_status: false`, AI
  isn't available in Blast/ClothesDryer). Icons are extractable with
  `python tools/extract_icons.py` into a gitignored `icons/` (Panasonic's artwork: local use
  only, never commit or publish). The per-model `GET /products/{code}/functions` flags
  (coordinator `supports()`) say which controls a model has. Design questions answered
  2026-09-24: one card, served from the integration itself (see the "Lovelace card" update
  below); icons are not used. (Original note: number entities fire one write per click, so
  the card should debounce -- writes take ~3s each. Unverified: the card uses stock `entities`
  rows, whose sliders should write on release rather than per click; check when testing.)
- **Update, 2026-09-24 — random-walk fuzzing + controlled A/B tests on the real unit, several
  old findings corrected, and real guards added.** Asked to "generate lots of random actions
  and state changes to find which are impossible", built three tools (all in `tools/`):
  `eolia_fuzz.py` (random walk: GET -> random 1-3 field change -> read-modify-write PUT ->
  classify ok / modified / rejected, JSONL logs in gitignored `fuzz_runs/`, chains
  `operation_token`, restores the starting state; `--raw`/`--wild` modes exist but weren't run),
  `eolia_fuzz_report.py` (mines the logs), and `eolia_ab.py` (controlled one-change-at-a-time
  experiments from an explicit baseline) plus `eolia_keepmode_probe.py`. ~280 real writes, 1
  lockout total. Full write-up with sample counts: `tests/fixtures/live_captures/39_fuzz_findings.md`
  -- read it before touching any of the rules below. Headline: with the known rules applied,
  `/status` almost never *rejects* anything; the "impossible" combinations are the server
  answering 200 and silently overriding fields.
  - **`KeepMode` is a `/status` dead end.** Every `/status` write that carries `KeepMode` back
    is rejected `E-21291-01711` (fan, louvers, nanoeX, AI, temperature, even power-off);
    omitting `operation_mode` gives new `E-21291-01703`. Only a write that changes
    `operation_mode` to a real mode (with a real temperature) gets through, and power-off has
    to go through `/customsettings status=false`. **Implemented**: `coordinator` raises a clear
    error for any other write in `KeepMode`, routes a bare power-off through
    `/customsettings`, and `climate.turn_on` is a no-op while already in `KeepMode`.
  - **`wind_shield_hit` is an "everything auto" mode.** While `shield`/`hit` is on the server
    forces fan speed, vertical louver and horizontal louver to auto and ignores writes to them
    (they can be set again as soon as it's off, even in the same write). `air_flow` `quiet`/
    `long` conflicts with it (`air_flow` wins, in every order); `powerful` coexists. It's
    dropped in `Blast` and `ClothesDryer`; `air_flow` is dropped in Dry/`Blast`/`ClothesDryer`.
    Fan speed is only honoured while `air_flow` and shield/hit are both off. **Implemented**:
    `coordinator` compares the response with the requested fan/louver/shield-hit/air-flow
    values and raises an error that says *why* (constants in `const.py`); side effects the
    caller didn't ask for (shield/hit forcing the louvers to auto) are deliberately not flagged.
  - **Corrections**: temperature step is **0.5°C** (24.5/16.5/29.5 accepted in four modes,
    off-grid 25.3 rejected `E-21291-01712`) -- `PROVISIONAL_TEMPERATURE_STEP = 1.0` is now
    `TEMPERATURE_STEP = 0.5` and `climate.async_set_temperature` snaps to that grid;
    `MoistCooling` does **not** downgrade (see above); the vertical vane is **not** a one-way
    door (it was shield/hit, see above).
  - **Writes while the unit is off**: with a real mode carried instead of `Stop`, a write while
    off is *accepted* (17/17) but mostly not applied (louvers read back parked, `nanoex` sticks).
    The "rejected while off" behaviour was the carried `Stop` mode, not the off state itself.
    `_require_powered_on` is kept (writes go nowhere), only its docstring was corrected.
  - 227 tests now (up from 207): coordinator `KeepMode` guard, each ignored-field explanation,
    the honoured/unrequested-side-effect no-raise cases, 0.5 step + snapping, `turn_on`/
    `turn_off` in `KeepMode`. Existing tests whose mocked PUT response didn't echo the request
    were fixed to echo it (an unchanged response now correctly reads as an ignored write).
  - **Not done**: `--raw`/`--wild` fuzz passes, `peak_cut`, `timer_value`, other models.
