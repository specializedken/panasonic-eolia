# 39 — Fuzz + controlled A/B findings (2026-09-24)

Sources, all against the real unit "Yurt" (CS-712DX2-W):
- `tools/eolia_fuzz.py` random walk: pilot (30 steps) + 250-step run, ~257 accepted `/status`
  writes; mined with `tools/eolia_fuzz_report.py`.
- `tools/eolia_keepmode_probe.py`: targeted `KeepMode` payload variants.
- `tools/eolia_ab.py`: controlled A/B experiments (explicit baseline, ONE change per write) that
  settled every hypothesis the fuzzer raised. Rules marked **[A/B]** are confirmed that way;
  **[fuzz]** = random-walk evidence only (small n, treat as a hypothesis).

Raw logs: `fuzz_runs/` (gitignored; tokens/appliance id are never logged).

Every write chains the previous response's `operation_token` at ~5s spacing: 0 lockouts in the
pilot and all A/B runs, 1 in the 250-step run (waited out by the fuzzer).

## 1. Hard rejections — almost none for /status

With the known rules applied, 0 of ~260 fuzz `/status` writes were rejected outside `KeepMode`/
`Stop`. Everything "impossible" is the server answering 200 and silently **ignoring or
overriding** fields, not erroring. HA needs a post-write comparison for those (the coordinator
already does it for `operation_mode`/`ai_control`).

## 2. KeepMode — a `/status` dead end (HIGH impact for HA) [fuzz 8/8 + direct probe]

While in `KeepMode`, every `/status` write that carries `KeepMode` back is rejected
`E-21291-01711` (nanoex, air_flow, ai_control, silence_control, temperature, bare power-on...).
Probe from inside `KeepMode`, fan 3:

| variant | result |
|---|---|
| carry `KeepMode` | `E-21291-01711` |
| carry `KeepMode`, `operation_status=False` (power off) | `E-21291-01711` — **power off only works via `/customsettings status=false`** |
| `operation_mode=Other`, or `Stop` + status false | `E-21291-01711` |
| omit `operation_mode` entirely | `E-21291-01703` (new code; missing required field) |
| `operation_mode=Auto` with `temperature=0.0` carried | `E-21291-01712` (KeepMode's temp is 0.0; needs a real one) |
| `operation_mode=Cooling` with a real temperature (fuzz step 30) | **OK** — the only way out via `/status` |

So in `KeepMode` fan/louvers/nanoeX/AI cannot be changed without leaving `KeepMode`.

## 3. wind_shield_hit ("shield"/"hit") is an all-auto mode [A/B]

Turning `shield` or `hit` on makes the server force, in the same response:
- `wind_direction` (vertical) -> `0` (auto)
- `wind_direction_horizon` -> `auto`
- `wind_volume` -> `0` (auto)

While it is on, writes to any of those three are silently ignored (read back as auto / 0).
Turning it off does NOT restore the old louver/fan values; they stay auto, but can be set
again right away (including in the same write that turns shield/hit off).
`shield` and `hit` behave identically for all of the above.

**Per mode:** supported in Auto, Cooling, CoolDehumidifying, MoistCooling,
ComfortableDehumidification (Dry) and Heating; **always dropped (-> `not_set`) in `Blast` and
`ClothesDryer`**. (One fuzz sample, Heating with the mode change in the same write, showed
`shield` ignored — unexplained, A/B shows it works when set after entering Heating.)

**vs `air_flow`:** `quiet` and `long` conflict with shield/hit and **`air_flow` wins**, in every
order (hit then `quiet`/`long` -> hit dropped; `quiet`/`long` then hit -> hit dropped; both in one
write -> hit dropped). `powerful` **coexists** with shield/hit in every order. In Dry the server
drops `air_flow` altogether (see 5), so shield/hit always wins there.

## 4. wind_volume is forced to 0 (auto) [A/B]

Fan speed is only honored when `air_flow == not_set` AND `wind_shield_hit == not_set`. Otherwise
the server resets it to `0` (also resets a value already set: `air_flow=quiet` alone took fan
3 -> 0, and it stays 0 after `air_flow` is cleared). `powerful` counts as an air_flow value too
(fuzz: 3 -> 0). Consistent with the known "`air_flow=long` boosts air at `wind_volume=0`".

## 5. Vertical vane is NOT a one-way door — correction to 15/18 [A/B]

In Cooling with shield/hit off: `0->3`, `3->0`, `0->4`, `2->6` (swing), `6->1` (fixed overrides
swing) were all honored. The old "writing 1-5 while in auto is silently inert" was shield/hit being
on (fuzz: 5/5 fixed-from-auto writes honored without shield/hit, 5/5 ignored with it). The app's
own vertical-auto toggle may still be a separate thing, but the API needs no special handling
beyond "shield/hit must be off".

## 6. Horizontal louver [A/B]

Same as the vertical one: `to_left`/`wide`/`to_right`/`auto` are honored freely with shield/hit
off; with it on, the server forces `auto` (a `to_left` set before `hit` becomes `auto`).

## 7. Per-mode fields the server resets [fuzz, n in parentheses]

| mode | reset by server |
|---|---|
| `ClothesDryer` | `air_flow` -> not_set (0/4 honored), `wind_shield_hit` (see 3), `ai_control` -> off (known); fan, both louvers, nanoex, airquality all honored |
| `Blast` | `air_flow` -> not_set (n=2), `wind_shield_hit` (see 3), `ai_control` -> off (known) |
| `ComfortableDehumidification` | `air_flow` -> not_set — **A/B: always** (quiet and powerful both dropped, alone or with hit); `ai_control` and vertical louver honored |
| `Nanoe` (Blast + nanoeX) | `ai_control` -> off (0/3), same as Blast |
| clean family (`Cleaning`/`NanoexCleaning`/`SmellCare`) | overrides nearly everything on entry: `operation_status`->False, temp->0.0 (16.0 for SmellCare), fan/vane->0, horizon->auto, `ai_control`->comfortable, `nanoex` forced (known) |

Honored in every mode sampled (n>=2): `nanoex`, `temperature`, `air_flow` in Cooling (5/5) and
CoolDehumidifying (3/3) [A/B: also Auto], `ai_control` in Auto/Cooling/MoistCooling/Dry.

## 8. Temperature step is 0.5, not 1.0 [A/B]

`24.5`, `16.5` and `29.5` were accepted in Cooling, Heating, CoolDehumidifying and Auto; an
off-grid `25.3` was rejected `E-21291-01712` in all four. So the server grid is 0.5C over
16-30 — `const.PROVISIONAL_TEMPERATURE_STEP = 1.0` (and climate's whole-degree step) is wrong.
(The fuzz's `29.5 -> 30.0` was a power-off write, not rounding.)

## 9. MoistCooling does NOT downgrade [A/B, 6/6]

Accepted as `MoistCooling` when entered from Cooling, Heating, Dry, Auto, Blast, and from power
off; its temperature (26.0) is honored too. This contradicts the earlier "silently downgrades to
`Cooling`" note in CLAUDE.md and capture 25; the cause of that earlier observation is unknown (it may
depend on device/room conditions). The coordinator's requested-vs-returned mode check is still
harmless — it just should not be described as a known MoistCooling behaviour.

## 10. Smaller items

- `/customsettings` with `status=False, low=23, high=0` -> `E-21291-02006`: the range is
  validated even when turning the setting off. `E-21291-02009` (gap < 5) reproduced with 19/23.
- A write from `Stop` that carries the `Stop` mode back is rejected `E-21291-01711` even for an
  unrelated change (temperature) — the fuzzer substitutes the last real mode.
- Powering off reports `operation_mode: Stop`, as known.
- 0 drift between a PUT response and the next GET across all runs.

## Not covered yet

`--raw` and `--wild` fuzz passes; `peak_cut`; `timer_value`; `SmellCareSpot`, `Dehumidifying`,
`AutoTempControl`, `KeepHeating` (known-unsupported on this model); shield/hit and louvers in the
clean family (all overridden on entry, so probably moot); behaviour on other models.
