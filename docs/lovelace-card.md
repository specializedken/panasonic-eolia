# Lovelace card (`custom:eolia-card`)

The card that ships inside the integration. **Start with "Current design"**; the "Design log" below
it is the original chronological record, in which earlier layouts (0.3.0 stepper, 0.4.0 custom
dropdowns, ...) were later superseded.

## Current design (card 0.8.x)

**Where the code is**

| What | Path |
|---|---|
| The card (plain JS, no build step) | `custom_components/eolia/www/eolia-card.js` |
| Serves + registers it | `custom_components/eolia/frontend.py` (static path `/eolia_static/eolia-card.js`, loaded with `add_extra_js_url` and a `?v=<manifest version>` cache-buster; `manifest.json` depends on `frontend` + `http`) |
| Card tests (Node) | `tests/js/eolia-card.test.mjs` — `node --test tests/js/` (runs the real card file against a fake DOM and `hass`) |
| Backend rules the card renders from | `custom_components/eolia/controls.py`, `const.py` (`PRESET_MODE_ORDER`, `OPERATION_MODE_TOOLTIPS`, `DOUBLE_MODE_TEMP_MIN_GAP`), and the `operation_mode` sensor in `sensor.py` |
| Icon extractor | `tools/extract_icons.py` |
| Panasonic icons (12 PNGs) | `custom_components/eolia/www/icons/{modes,rows}/`, served at `/eolia_static/icons/` (`frontend.py` registers the folder) |

**Config:** `type: custom:eolia-card`, `entity: climate.<your eolia climate>`, optional
`icons: /eolia_static/icons` (default: the icons shipped in the integration) or `icons: false`.

**No hardcoded entity ids.** The card finds the device's other entities from `hass.entities` (same
`device_id`, matched by `translation_key`), because ids depend on the device nickname *and* the
area. Entities whose state is HA's `restored` orphan marker are ignored.

**The card holds no rules and no copy.** What applies right now comes from attributes of the
`operation_mode` sensor: `controls` (list from `controls.py`), `mode_descriptions` (tooltip text),
`double_temp_min_gap`. If a rule changes, change `controls.py` and its tests, not the JS. Without
the attributes (older integration) the card shows everything.

**Layout, top to bottom**

1. **Setpoint dial** — HA's own `ha-control-circular-slider`. Target temperature normally; the Dry
   humidity target (50–60, 5% steps) in Dry; a two-thumb low/high in Double temperature (with a
   small Low/High stepper each and the ≥5° gap enforced instantly). In modes with no target (odor
   care, off, clothes drying) it stays on screen **greyed out**, so the layout never shifts. When the
   climate entity is `unavailable`/`unknown` (unit offline) the dial reads **Unavailable** (HA's
   localized string) where the temperature would be, like HA's thermostat card, and the mode grid
   is disabled. Drags
   show live; taps and releases are debounced (700 ms) into one write.
2. **Mode grid** — Off first, then the modes in `const.PRESET_MODE_ORDER`, with the app's own accent
   colours, Panasonic icons where the app has one (mdi otherwise) and a tooltip per mode.
3. **Stock `entities` card** — fan speed, vertical/horizontal louver, AI mode, airflow mode, airflow
   targeting, nanoeX, quiet mode (plus air-quality monitoring on models that have it), then a stock
   `glance` of indoor temp/humidity and outdoor temp.

**Icons.** They are Panasonic's copyrighted artwork. **Since 2026-09-25 (Kevin's call) the 12 the
card uses are committed and shipped inside the integration** — 8 mode icons (`modes/`) and 4
settings-row icons (`rows/`, square-padded and recoloured) in `custom_components/eolia/www/icons/` —
so the card has them out of the box and HACS installs deliver them. Before that they were local-only
and copied to `<config>/www/eolia-icons/`; older log entries below describe that. Only these 12 are
tracked: `/icons/` at the repo root (the extractor's full ~100-file output) and `code/` stay
gitignored. `tests/test_frontend.py` fails if the card references an icon that isn't shipped.

To refresh or extend them: decompile the Eolia APK with `jadx` into `code/`, run
`python tools/extract_icons.py` (row icons need Pillow), and copy the wanted files from `icons/` into
`custom_components/eolia/www/icons/`.

Missing files degrade gracefully: mode buttons fall back to tinted mdi icons and rows keep HA's own
icon (each row image is probed by loading it first). `icons: false`, or a path to another folder
with the same `modes/` + `rows/` layout, overrides the default. **If the repository is ever
republished, remove `www/icons/`** — the rights in that artwork are Panasonic's.

**Deploying a card change:** bump `manifest.json`'s `version` (it is the cache-buster), copy
`custom_components/eolia/` into the HA config, restart HA if any Python changed (JS alone is served
straight from disk), then hard-refresh the browser (the companion app needs its cache cleared).

**Debugging without a browser:** HA logs uncaught frontend errors —
`docker logs homeassistant | grep -A8 "Uncaught error"` shows the message and an
`eolia-card.js:<line>` stack.

**Risks:** the dial relies on HA-internal element names/properties read from the 2026.9 frontend
bundle (`ha-control-circular-slider`: `value`/`low`/`high`/`min`/`max`/`step`/`current`/`mode`/`dual`,
`value-changed`/`-changing`, `low-`/`high-` events). Re-check them after an HA upgrade. Appearance
can only be checked by eye (no headless browser in this environment); the JS tests cover logic.

## Design log

Chronological notes, moved verbatim from `CLAUDE.md` on 2026-09-25 (the card started as
`custom:eolia-card` 0.1.0 on 2026-09-24). **Statements below that the icons are never committed or must
be copied to `www/eolia-icons/` are out of date** — see "Icons" in the current design above.

- **Update, 2026-09-24 — the Lovelace card (`custom:eolia-card`) built and deployed to europa.**
  Kevin's requirement: hide controls that don't apply to the current mode, and be
  **distributable** (no hardcoded entity ids -- they depend on the device nickname *and* the
  area, e.g. europa has `sensor.yurt_*` but the new sensor registered as
  `sensor.living_room_yurt_operation_mode`). Researched first: stock conditional cards can only
  test an entity's *state* (attribute conditions are still an open frontend feature request), a
  2026.5 dashboard-strategy API exists but is thinly documented and generates whole dashboards,
  so: **one custom card shipped inside the integration.**
  - **Config is just** `type: custom:eolia-card` + `entity: climate.<yours>`. The card finds the
    device's other entities from `hass.entities` (same `device_id`, matched by
    `translation_key` -- both fields confirmed present in europa's frontend bundle), so no
    entity id is ever written down. Custom setpoint dial and mode picker plus stock
    `entities` / `glance` cards (see the 0.5.0 layout below). `www/eolia-card.js`, plain
    JS, no build step.
  - **Served by the integration** (`frontend.py`): `async_register_static_paths` for
    `/eolia_static/eolia-card.js` plus `add_extra_js_url` with `?v=<manifest version>` for cache
    busting -- **bump `manifest.json`'s `version` whenever the JS changes** or browsers keep the
    old file. Chosen over a Lovelace-resource entry because it works in both storage and YAML
    dashboard modes and can't create duplicate resources. `manifest.json` now depends on
    `frontend` and `http`; registration happens once in `async_setup`.
  - **The "what applies now" rules live in Python only** (`controls.py`,
    `applicable_controls(status)`, unit-tested in `tests/test_controls.py`) and reach the card as
    the `controls` attribute of the new `sensor.<device>_operation_mode` (an ENUM sensor
    exposing the raw `operation_mode`; unknown server values fold into `Other`). The card holds
    **no rules**, so it can't drift from `const.py`. If you learn a new rule, change
    `controls.py` + its tests, not the JS. Ids are entity `translation_key`s (`fan_speed`,
    `vertical_louver`, `horizontal_louver`, `ai_mode`, ...) plus `temperature` for the
    dial's target-temperature case. Current rules: `KeepMode` ->
    only low/high (it's a `/status` dead end); off or clean family -> nothing; no `temperature` in Dry/ClothesDryer; `ai_mode` dropped in
    Blast/Nanoe/ClothesDryer; `air_flow` dropped in Dry/Blast/Nanoe/ClothesDryer;
    `wind_shield_hit` dropped in Blast/Nanoe/ClothesDryer; shield/hit on hides fan + louvers;
    any non-`not_set` `air_flow` hides fan; `dry_humidity_target` Dry only. (Nanoe follows Blast's
    rules -- assumed from "Nanoe = Blast + nanoeX", only AI is live-confirmed for it.) If the
    attribute is missing (older integration) the card shows everything rather than nothing.
    New const: `AI_UNSUPPORTED_MODES`.
  - **Tested**: `tests/test_controls.py`, `tests/test_frontend.py`, sensor tests (Python), and
    `tests/js/eolia-card.test.mjs` (`node --test tests/js/`) which runs the real card file
    against a fake DOM/`hass` (entity discovery across devices, rendering from `controls`,
    reconfigure-not-recreate, missing-attribute fallback). 289 Python tests + 64 JS tests.
    Kevin has looked at it rendered (feedback drove the 0.4.0 layout below); there is no
    headless browser on europa, so layout/CSS is only ever checked by eye -- the JS tests cover
    logic, not appearance. Hard-refresh after a deploy (companion app needs a cache clear).
    **Debugging the card without a browser:** uncaught JS errors from the frontend are logged by
    HA itself -- `docker logs homeassistant | grep -A8 "Uncaught error"` shows the message and a
    stack with `eolia-card.js:<line>` (found this way 2026-09-24: the dial's `value-changing`/
    `-changed` events can arrive with no usable value, and formatting it threw
    `TypeError ... 'toFixed'`; fixed in 0.6.1 by ignoring non-finite values, with regression tests).
    Check it after any card change Kevin has been using.
  - **Deployment gotcha found on the way**: europa's running integration had been left on an
    older `coordinator.py`/`climate.py`/`const.py`/`entity.py` than HEAD (a commit landed after
    the last `docker cp`). It was redeployed from the working tree this session. After any
    commit that changes integration code, remember `docker cp custom_components/eolia/.
    homeassistant:/config/custom_components/eolia/` + `docker restart homeassistant`.
  - **Panasonic icons (2026-09-24, card 0.3.0) -- a mode picker like the app's.** Stock HA rows
    can only show mdi icons, so the card has one custom piece: a grid of mode buttons (replaced
    the tile's preset dropdown) calling `climate.set_preset_mode`, with the app's own accent
    colours. Icons come from `<icons>/modes/<name>.png`, default `icons: /local/eolia-icons` (=
    HA's `/config/www/eolia-icons/`); `icons: false` disables them. **The icons are Panasonic's
    artwork, so they are NOT in the repo or the integration** -- each user copies their own with
    `python tools/extract_icons.py` (needs the local decompiled `code/`), then copies the
    `modes/` folder into HA's `www/eolia-icons/`. On europa that was done with `docker cp` of
    the 8 files the card references (`v6_drive_mode_{automatic,blower,cleaning,clothes_drying,
    dehumidity,moist_cooling,nanoex,smell_care}.png`). A mode with no app icon, or whose file is
    missing (`<img onerror>`), falls back to a tinted mdi icon, so the card is fully usable
    without them. The app has **no icon for Cooling, Heating, Cool & Dehumidify or KeepMode**
    (those use mdi with the app's accent colours: cooling `#65accc`, heating `#c19270`), so the
    grid mixes the two styles. Mapping caveats: `MoistCooling` uses `moist_cooling` and
    `Dehumidifying`/Dry share `dehumidity` by name only; it isn't verified which app mode each
    artwork belongs to. A refused write (the integration's human-readable errors) is shown as an
    HA notification instead of being swallowed.
  - **Card layout, current (0.6.0; earlier passes revised from Kevin's feedback).**
    Top to bottom: (1) the **setpoint dial** -- HA's own `ha-control-circular-slider` (the round
    control the climate card uses; internals copied from the stock humidity control's template)
    with a big number, the room's current reading as a marker + "Currently ..." line, -/+
    buttons, and the mode's accent colour. It swaps by mode via the `controls` list: target
    temperature normally; the **Dry humidity target** (50-60, 5% steps) in Dry; a **two-thumb dial
    (`dual`) with a small Low/High stepper each** in KeepMode (the dial has no room for two sets
    of -/+); and in modes with no target (odor care, off, clothes drying...) the **same dial stays on
    screen greyed out** ("No target in this mode" / "Off", value "–", -/+ disabled, room reading
    kept) -- it was hidden in 0.5.0, but that shifted the mode icons below it, so the setpoint block
    now has a constant height in every state (a spacer stands in for KeepMode's Low/High row) and
    the dial element is reused across the has-target/no-target switch. Dragging shows the value live and writes on release;
    -/+ taps and releases are **debounced (700 ms, one write per burst)** because each write
    takes ~3 s; a poll never moves the thumb mid-drag, and the dial updates in place rather than
    being rebuilt. The dial element is defined lazily by HA, so the card asks the helpers for a
    (never attached) stock `thermostat` card to make HA load it; if it ever isn't defined the -/+
    buttons still work. **Relies on HA-internal element names/props** (`value`/`low`/`high`/
    `min`/`max`/`step`/`current`/`mode`/`dual`, `value-changed`/`-changing`, `low-`/`high-`) read
    from the 2026.9 bundle -- re-check them if a future HA upgrade breaks the dial. (2) the mode
    picker with an **Off button** (`climate.turn_off`) -- the stock tile and its HVAC-mode bar were
    removed at Kevin's request and Off would otherwise have no home (my call); (3) the stock
    `entities` card (fan speed, vertical/horizontal louver, AI mode, airflow mode, targeting,
    nanoeX, quiet, air-quality) and a stock `glance`. The humidity and low/high
    number entities are the dial, not rows.
  - **Why the dial isn't HA's stock `thermostat` card (asked 2026-09-24).** The stock card IS an
    independent card, but it only takes a `climate`/`water_heater` entity and is just a title + the
    `ha-state-control-climate-temperature` control (+ optional `features`; the HVAC bar only
    appears if configured). That control reads the climate entity's own temperature attributes and
    calls `climate.set_temperature`, so it can express the target temperature but NOT Dry's
    humidity target or KeepMode's low/high, which live on number entities. Embedding it for
    temperature only would make the setpoint block swap between two different components (own
    title, padding, aspect ratio) and re-introduce the layout jumps the greyed dial fixed. So the
    card uses the primitive that control is built from, `ha-control-circular-slider` (exactly HA's
    dial), for all three cases, and only the number/buttons/layout around it are ours. Possible
    later polish: use HA's own `ha-big-number` and `ha-outlined-icon-button` elements for
    pixel-exact typography (both are defined by the same lazily loaded chunk as the thermostat
    card; would need a wait-until-defined with a fallback).
  - **Mode order (Kevin, 2026-09-24):** Off, Auto, Dry, Cooling, Cool & Dehumidify, Moist Cooling,
    Heating, Double temperature, Clothes drying, Odor care, "always clean", Self-clean. The order
    lives in Python (`const.PRESET_MODE_ORDER`, applied to `climate._SETTABLE_PRESET_MODES`) so HA's
    own preset dropdown matches the card; the card shows `preset_modes` as given, with **Off
    first**, and keeps no ordering of its own. Interpretation calls to confirm with Kevin: "always
    clean" = `NanoexCleaning` (the app's おでかけクリーン, currently labelled "Away clean (nanoeX)"
    here -- not renamed) and "self-clean" = `Cleaning`; **Fan only (`Blast`) wasn't in his list**,
    so any settable mode not listed follows the listed ones (Fan only lands last) instead of being
    dropped.
  - **Lag after entering/leaving Double temperature (Kevin, 2026-09-24) -- fixed in the
    coordinator.** KeepMode is entered/left through `/customsettings`, but the mode, the card's
    dial variant and the `controls` list all come from `/status`, which was only re-read by the
    60 s poll, so the old mode stayed on screen. Now a `/customsettings` write that toggles
    `double_mode_temp.status` re-reads `/status` right away (`_async_resync_status`: up to 3 GETs
    2 s apart while the server still reports the old mode; a single GET if the server ignored the
    write; a failed read is non-fatal). The mirror case is covered too: leaving KeepMode via
    `/status` re-reads `/customsettings` so the double-temp switch/sliders don't go stale. Moving
    the range alone does not re-read `/status`. **The fix is verified by unit tests only** (the
    exact server-side propagation delay between the two resources is unmeasured); if the lag is
    still there, time entry live and look at whether `/status` lags `/customsettings`. Range
    writes themselves are just one PUT (~3 s) plus the card's 700 ms debounce.
  - **Double temperature on the card (Kevin, 2026-09-25).** (1) **No on/off switch**: picking the
    Double temperature mode enters it and any other mode/Off leaves it, so the card's settings
    list no longer has the switch and `controls.py` never offers `double_temp_enabled` (off/clean
    modes now give an empty list). **The entity itself was then deleted too** (Kevin asked):
    `EoliaDoubleTempEnabledSwitch` and its translations are gone, and `__init__.py`'s
    `async_remove_retired_entities` deletes the leftover registry entry on setup (unique_id suffix
    `_double_temp_enabled`; list in `_RETIRED_UNIQUE_ID_SUFFIXES`, add to it when retiring another
    entity) so it doesn't sit on the device page as a permanent "no longer provided" ghost. To
    turn Double temperature on/off use the mode picker, `climate.set_preset_mode` (KeepMode) or
    `climate.turn_off`. (2) **The >=5 degree low/high gap is enforced instantly in the
    card**: moving one bound into the gap pushes the other at once -- while dragging, on release
    and from the small -/+ steppers -- and if the other bound hits its own limit the moved one is
    held back (e.g. high dragged to 18 becomes low 16 / high 21). Only the moved bound is written;
    the coordinator makes the same nudge server-side, and the locally pushed value is dropped when
    the write finishes so the real state wins. The number comes from Python
    (`const.DOUBLE_MODE_TEMP_MIN_GAP`, now used by the coordinator instead of four literal 5s, and
    exposed as the operation_mode sensor's `double_temp_min_gap` attribute); the card hardcodes
    nothing, and enforces nothing if the attribute is absent.
  - **Fan speed and both louvers are now real select entities** (`select.py`:
    `fan_speed`/`vertical_louver`/`horizontal_louver`, options `"0".."5"`, `"0".."6"` and the
    `EoliaWindDirectionHorizon` values; a new `to_wire` field converts the string option back to
    `int` for the numeric fields). They **duplicate the climate entity's `fan_mode`/`swing_mode`/
    `swing_horizontal_mode`** (same wire fields, same guards) -- deliberate: Kevin wanted them to
    look like the other labelled rows (AI mode, airflow targeting), and HA has no stock labelled
    row for climate attributes. My first attempt was custom `<select>` rows; replaced. Control ids
    in `controls.py` were split accordingly (`fan_speed`, `vertical_louver`, `horizontal_louver`).
  - **Mode tooltips** (native `title` on the picker buttons; hover only, so not on touch):
    user-facing text in `const.OPERATION_MODE_TOOLTIPS` (plain English, no error codes/Japanese/API
    jargon -- tested), delivered as the operation_mode sensor's `mode_descriptions` attribute and
    listed in `_unrecorded_attributes` so the recorder doesn't store static text on every change.
    The card holds no copy of its own. Different from `OPERATION_MODE_DESCRIPTIONS`, which is the
    developer-facing note set. Add a tooltip when adding a mode (`test_sensor` enforces coverage).
  - **Row icons (card 0.8.0, 2026-09-25):** four settings rows show the app's own icons --
    vertical louver (`icon_updown_swing`), horizontal louver (`ic_wind_hor_swing`), nanoeX
    (`v6_operation_nanoe`) and airflow targeting (`wind_hit`). Stock entity rows accept an `image`
    (mdi only otherwise), but HA draws it with `background-size: cover` in a 40 px **circle**, and
    the originals are wide and/or black (invisible on a dark theme), so `tools/extract_icons.py`
    now also writes `<out>/rows/<translation_key>.png`: cropped to the glyph, **square-padded with
    the glyph kept inside the circle**, recoloured to the app's slate `(105,124,146)` (readable on
    light and dark; semi-transparent originals are stretched solid). Needs Pillow (in the venv);
    without it the step is skipped. Install like the mode icons: copy `<out>/rows/` next to
    `modes/` in HA's `www/eolia-icons/` (done on europa with `docker cp`). The card probes each
    file by loading it and only sets `image` after it loads, so a missing file leaves HA's own
    icon rather than a broken picture; keyed by translation_key via `ROW_IMAGE_KEYS`. Deliberately
    not done: switching the targeting icon between `wind_shield` and `wind_hit` by state.
  - **Air quality is not available on CS-712DX2-W, and its switch is gone (2026-09-25).** Checked
    live against `GET /products/CS-712DX2-W/functions`: `airquality: false` and
    `ai_airquality: false` (also `circulation`/`ventilation` false). The switch was already only
    created when the flag is true, but an entry registered before that gating stayed in the entity
    registry -- and the card, which finds entities from `hass.entities`, showed it as a dead row.
    Fixes: `__init__.async_remove_unsupported_entities` deletes registry entries for features the
    model reports false (`_MODEL_GATED_UNIQUE_ID_SUFFIXES`: `_airquality` -> `airquality`; only
    acts on a flag the cloud actually returned false -- `supports()` is True for unknown, so a
    failed fetch deletes nothing), and the card now skips entities whose state is HA's `restored`
    orphan marker (`_isOrphan`), so no leftover ever renders as a row. **The two air-quality
    sensors (`air_quality`, disabled-by-default raw value) got the same treatment at Kevin's
    request** -- they can only read "off"/-1 on this model: `sensor.py` creates them only when
    `supports(..., "airquality")` (a `function_id` on the description, like the switches) and
    their unique_id suffixes `_aq_name`/`_aq_value` are in `_MODEL_GATED_UNIQUE_ID_SUFFIXES`, so the
    existing registry entries are deleted on setup. Models that do have the feature keep all three.
    The fan speed select has the stock `mdi:fan` icon (`icon` on its description).
  - **The old YAML prototype (`dashboard/eolia.yaml`) was deleted** -- it hardcoded entity ids
    and encoded rules that had since gone stale.
  - **Not done / ideas**: the card doesn't use the per-model `supports()` flags directly (a
    control whose entity doesn't exist -- e.g. air quality on this model -- is simply skipped);
    no visual editor (`getConfigElement`); no separate treatment of a unit that's "off" beyond
    hiding settings; the louver/airflow/shield-hit icons from the app are unused (dark grey or
    white line art that disappears on one theme -- would need per-theme recolouring).
- **Update, 2026-09-28 — card 0.9.1: an unreachable unit reads "Unavailable".** The AC was off, so
  the cloud answered `E-21291-01602` (see `integration-log.md`) and the climate entity went
  `unavailable`; the card just showed a greyed dial with "No target in this mode" and a dash.
  Now, as in HA's thermostat card, the dial's value reads "Unavailable" (`hass.localize`
  `state.default.unavailable`, falling back to English; smaller font so the word fits), with no
  heading or "Currently" line, the -/+ buttons disabled, and the mode grid disabled. `unknown`
  is treated the same. Tests: 3 new (67 JS tests).
  - **0.9.2**: the disabled mode buttons showed the busy (spinner) cursor because
    `.eolia-mode[disabled]` had `cursor:progress`, meant for a write in flight. It is now
    `default`, and `progress` only applies under `.eolia-pending` (set while `_pending`).
  - **0.10.2 (2026-09-30)**: "Custom element doesn't exist: eolia-card" in the Android app although
    the script loaded (200) and ran. Diagnosed over CDP on the real phone (WebView 153): the card's
    `customElements.define` ran ~58 ms *before* HA's frontend installed its scoped-registry polyfill
    (inline `import()` from `add_extra_js_url` beats the core bundle), so the polyfill's own map never
    had it (`customElements.get` false, but `window.customCards` set). Now the card defines itself after
    `customElements.whenDefined("home-assistant")`, falling back to an immediate define when `whenDefined`
    is missing or rejects. Also fixed the stale `CARD_VERSION` banner. Not yet verified on the phone.
