# Live validation captures — 2026-09-23

Real request/response pairs captured via `tools/eolia_cli.py` against Kevin's real unit
("Yurt", `CS-712DX2-W`), each cross-checked against the official Eolia app's displayed
state. Purpose: validate every feature end-to-end on real hardware before/alongside
wiring it into `custom_components/eolia/`, and keep fixtures around for `tests/`.

Each file: `NN_description.json` with `request` (exact PUT body sent, when there was
one), `response` (exact PUT/GET response body), `app_confirmed` (what the official app
showed, once Kevin checked), and `notes` (anything surprising). See each file for full
detail — this README is just an index pointing at the headline finding of each.

## Log

| # | What | Headline finding |
|---|---|---|
| 01 | Power on, `Cooling` @ 24°C | New error `E-21291-01712` (temperature out of range for an active mode) when `temperature=0.0` carries over from `Stop`. `wind_volume=0`="auto" theory supported. |
| 02, 04 | Lockout hits | New error `E-21291-01718` — confirmed live: the **official app writes even on a no-op menu close**, locking out other clients for ~2 min. |
| 03, 05, 06 | `nanoex` off, all 3 `ai_control` values | All confirmed against the app. |
| 07 | `KeepMode` ("double temperature setting") | Confirmed at the wire level; the low/high range is **not** in `/status` — see 16 and `findings.md` for where it actually lives. |
| 08, 09 | `wind_volume` 0 and 1 | 0=Auto, 1="Minimal" confirmed in-app. Also: `ai_control` drifted `comfortable_econavi`→`comfortable` on its own, unexplained one-off (Kevin confirmed he didn't touch it). |
| 10–12 | `air_flow`: quiet/powerful/long | All 3 confirmed in-app; independent of `wind_volume` (`long` visibly increases airflow even at `wind_volume=0`). |
| 13–14 | `wind_shield_hit`: shield/hit | Both confirmed in-app (avoid-people / aim-at-people). |
| 15 | `wind_direction` full range | Real range is **0–6, not 0–5**: 1-5=fixed positions (1=upper, 3≈medium, 5=straight down), **6=swing** (a previously-unknown state, found via the app's "left-right arrow" toggle). |
| 16 | First-ever `PUT /customsettings` | Minimal field set works (no `operation_token` needed in the request; it *is* present in the PUT response only, mirroring `/status`). New error `E-21291-02009` (double-temp high/low need a ≥5°C gap). |
| 17 | `CoolDehumidifying` | Confirmed as the app's "Cool & Dehumidify", with a real settable target temperature — closes the project's original motivating question via a live write, not just decompiled strings. |
| 18 | `wind_direction=0` via API | Confirmed the API **can** freely enter auto (resolves the asymmetry from 15: entering auto always works, but a fixed-position write while already in auto is silently ignored — no API way to *leave* auto, only the app's own toggle does that). |
| 19 | `ComfortableDehumidification` ("Dry") | **Major finding**: this mode requires `humidity` in the PUT body — the one exception to the general "exclude humidity" rule — and `temperature=0.0` (it targets humidity, not temperature). Three earlier attempts without `humidity` all failed. |
| 20-24 | Dry mode's humidity range | Bisected live: valid values are exactly **{50, 55, 60}** (5% steps, capped at 60% — not 80%/100% as naively guessed). Same generic error for every rejected value, no distinguishing "out of range" signal. |
| 25 | `MoistCooling` ("Moist air conditioning") | Set by Kevin via the app. Confirms this mode behaves like the cooling family (real settable target temp, 16-30°C range, fixed louver/horizontal controls) rather than the humidity-target family Dry belongs to. |
| 26 | `Blast` ("Air blower") | Confirmed as the app's fan-only mode -- no temperature control shown in the app (its `temperature` field is just an inert carried-over value). Normal (non-auto-reset) fan/louver controls, unlike Dry/KeepMode. |
| 27-33 | `wind_direction_horizon` full enum | All **8 values live-confirmed**: `front` (default), `spot` (converging/focused airflow), `wide` (diverging, opposite of spot), `to_left`/`to_right` (fixed, pointing left/right), `nearby_left`/`nearby_right` (fixed, partial left/right), `auto`. Unlike the vertical axis, horizontal `auto` round-trips honestly in `/status` with no value-masking behavior observed. |
| 34 | `ClothesDryer` ("clothes drying") | Confirmed. Requires `temperature=0.0` like Dry mode, but does **not** need `humidity` — a third category, distinct from both the cooling family (real temp) and Dry (humidity target). No AI-control option shown in the app for this mode at all. |
| 35 | `Heating` | Confirmed. Real target temperature, same 16-30°C range as the cooling family. Closes out live confirmation of every "core" operation_mode this session set out to test. |

## Open questions still unresolved

- The `ai_control` drift seen in 09 (`comfortable_econavi`→`comfortable` with no write from
  either side) — one-off, not reproduced since, not understood.
- ~~`outside_temp=999.0` right at power-on~~ — **RESOLVED**: powering the unit back off
  (end of this session) made `outside_temp` immediately revert to `999.0` again, so it
  correlates with power state (outdoor sensor only reports while running), not a one-time
  startup lag as originally guessed.
- Whether `temperature=0.0` is required/rejected the same way for other rarely-used
  `operation_mode` values (SmellCare, NanoexCleaning, AutoTempControl, KeepHeating,
  Auto) — none of those tested yet (`Blast`/`Heating`/`ClothesDryer`/`ComfortableDehumidification`
  now confirmed).
- Whether horizontal `wind_direction_horizon=auto` has the same "API can enter but not
  leave" asymmetry the vertical axis has (see 15/18) — not yet tested (would need a
  fixed-position write while horizontal auto is active).
- Whether plain `Dehumidifying` (as opposed to `ComfortableDehumidification`) is a real,
  separately-selectable mode on this device at all — the app's "dehumidification" menu
  item turned out to just be `ComfortableDehumidification`; plain `Dehumidifying` was
  never confirmed as an actual app-reachable option, only as a decompiled enum string.
