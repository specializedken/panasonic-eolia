# Live validation captures — 2026-09-23

Real request/response pairs captured via `tools/eolia_cli.py set` against Kevin's real
unit ("Yurt", `CS-712DX2-W`), each confirmed against the official Eolia app's displayed
state before moving to the next one. Purpose: validate every feature end-to-end on real
hardware, and record fixtures to fold into `tests/` later (either as new parametrized
cases or to replace the provisional/guessed values flagged in `PHASE1_PLAN.md`/
`const.py`).

Each file: `NN_description.json` with `request` (exact PUT body sent), `response` (exact
PUT response body), `app_confirmed` (what the official app showed after, once Kevin
checked — `null` until confirmed), and `notes` (anything surprising).

## Log

1. **`01_power_on_cooling.json`** — power on + `operation_mode=Cooling` + `temperature=24.0`.
   - First attempt carried over `temperature=0.0` from the prior `Stop` state unmodified
     -> rejected with a **new error code, `E-21291-01712`** (generic "an application
     error occurred" message, same shape as the already-known `E-21291-00007`). Not
     previously documented; add to `findings.md`'s known-error-codes list. Leading
     theory: server validates `temperature` against `operation_mode` whenever
     `operation_status=true`, and `0.0` is out of Cooling's valid range.
   - Retried with `temperature=24.0`, everything else identical (including
     `wind_volume=0`) -> succeeded.
   - `wind_volume=0` was accepted -> supports the provisional "0 = auto" guess.
   - `outside_temp` stayed `999.0` even with the unit on, contradicting the "999 only
     while off" theory floated right after the first live `status` call today. Needs
     more observation across the rest of this session.
   - App confirmation: pending.

## Open questions to fold back into `findings.md` once this session wraps

- Full list of `E-21291-*` codes seen (now 3: `00002` clock-skew, `00007` generic,
  `01712` generic/possibly temperature-range). Do `00007` and `01712` actually mean
  different things, or is `01712` also just a generic catch-all the same way `00007`
  turned out to be non-specific? Not fully confirmed -- `01712` was only ever seen with
  an invalid temperature so far, so the correlation could be coincidental. Worth
  deliberately re-triggering to check if it's specific to temperature or generic.
- Whether `temperature=0.0` is rejected for *every* active `operation_mode`, or only
  some (e.g. maybe `Heating` has a different valid range check than `Cooling`).
- What's actually driving `outside_temp=999.0` -- sensor availability, update lag, or
  something else. Keep recording it on every capture in this session.
