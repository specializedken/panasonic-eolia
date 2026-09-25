# CLAUDE.md

Guidance for Claude Code sessions working in this directory. This is the short version: the
detailed history and design notes live in [`docs/`](docs/README.md) — read the relevant document
before changing an area (the dated logs record *why* things are the way they are, including
hardware behaviour that isn't obvious from the code).

## Purpose

An unofficial Home Assistant custom integration (`custom_components/eolia/`) and Lovelace card
for Panasonic Eolia air conditioners, built by reverse-engineering the Eolia Android app's cloud
API. It exposes what ECHONET Lite cannot: the full `operation_mode` list (notably **Dry vs Cool &
Dehumidify**), AI mode / ECONAVI, nanoeX, airflow targeting, and per-mode settings.

It is built and **running on Kevin's Home Assistant** (a Docker container named `homeassistant` on
his home server "europa"), against the real unit: Panasonic CS-712DX2-W (Eolia X, JP market),
nicknamed "Yurt". Capability gating is only proven on that model. The original goal (a working
understanding of the API) was met on 2026-09-23; everything since is integration and card work.

## Where things are

| What | Path |
|---|---|
| Integration | `custom_components/eolia/` — `api.py`/`auth.py`/`models.py` (framework-free core), `coordinator.py` (the only place a write body is built), platforms (`climate`, `select`, `number`, `sensor`, `switch`), `controls.py` (what applies per state), `const.py` (rules and enums), `config_flow.py` |
| **Lovelace card (JS)** | `custom_components/eolia/www/eolia-card.js`, served by `custom_components/eolia/frontend.py` |
| Card tests (JS) | `tests/js/eolia-card.test.mjs` |
| Python tests | `tests/test_*.py`; fixtures from real traffic in `tests/fixtures/` |
| Raw live captures | `tests/fixtures/live_captures/` (index in its `README.md`; `39_fuzz_findings.md` is the fuzzing write-up) |
| Tools (talk to the real cloud/unit) | `tools/` — `eolia_cli.py`, `eolia_fuzz*.py`, `eolia_ab.py`, `eolia_keepmode_probe.py`, `extract_icons.py` |
| **Panasonic icons** | The 12 the card uses are **committed** in `custom_components/eolia/www/icons/{modes,rows}/` and served by the integration (`frontend.py`). `/icons/` at the repo root is the extractor's full local output (~100 files) and stays gitignored |
| Decompiled app, APK, fuzz logs, tokens | `code/`, `*.apk`, `fuzz_runs/`, `.eolia_tokens.json` — all gitignored |
| Docs | `docs/` (index: `docs/README.md`) and the top-level `README.md` |
| HACS | `hacs.json` (repo root) and the manifest's `issue_tracker`. Not done: `brand/icon.png`, which only HACS's *default* list requires; `codeowners` is empty |

## Documentation

- [`README.md`](README.md) — what this is, install, setup, the card, development.
- [`docs/findings.md`](docs/findings.md) — the API reference: auth, endpoints, JSON shapes, error codes, enums.
- [`docs/integration-log.md`](docs/integration-log.md) — dated log of the integration work and every live finding (later entries correct earlier ones).
- [`docs/lovelace-card.md`](docs/lovelace-card.md) — the card: current design first, then its design log.
- [`docs/research-history.md`](docs/research-history.md) — the original reverse-engineering plan and how it was resolved.
- [`docs/phase1-plan.md`](docs/phase1-plan.md) — the original Phase 1 design plan.

## Working here

**Tests** (a local venv, gitignored; recreate with `python3 -m venv .venv && pip install -r requirements-test.txt`):

```
source .venv/bin/activate && python -m pytest -q     # Python (integration, coordinator, controls, ...)
node --test tests/js/                                # the card (Node 18+)
```

**Deploying to europa.** HA runs in the `homeassistant` container; its config is bind-mounted from
`/home/kevin/homeassistant/config` to `/config`.

- `docker cp custom_components/eolia/. homeassistant:/config/custom_components/eolia/`
- Restart (`docker restart homeassistant`) only when Python, the manifest or translations changed;
  the card's JS is served straight from disk. A restart interrupts Kevin's whole HA, so don't do it
  needlessly, and say so when you do.
- After a restart the Eolia entry can take **1–4 minutes** to load (other integrations are slow), so
  wait and re-check the entity registry or logs before concluding something failed.
- Bump `manifest.json`'s `version` whenever the card's JS changes (it is the cache-buster).
- Useful checks: `docker exec -w /config homeassistant python -c "import custom_components.eolia..."`
  (the deployed code), reading `/config/.storage/core.entity_registry` with
  `docker exec -i homeassistant python - <<'EOF' ... EOF` (**`-i` is required** for stdin), and
  `docker logs homeassistant`. Info-level Eolia logs are hidden by default.
- Card problems: HA logs uncaught frontend errors, so
  `docker logs homeassistant | grep -A8 "Uncaught error"` shows the message and an
  `eolia-card.js:<line>` stack.
- Restart noise unrelated to Eolia: TP-Link/kasa tracebacks and `RuntimeError: Session is closed`
  while HA shuts down.

**The tools write to the real unit.** `eolia_cli.py set`, `eolia_fuzz.py`, `eolia_ab.py` change
Kevin's actual air conditioner. Get an explicit go-ahead first, and keep writes to one at a time
(see the lockout rule below). Reads (`status`, `devices`, `customsettings`) are safe.

## Rules that must not be broken

- **Secrets.** `.eolia_tokens.json` holds live tokens: never put its contents in a doc, memory,
  commit or chat.
- **Panasonic's artwork: only the 12 icons the card uses are committed** (`www/icons/`, Kevin's
  decision 2026-09-25); they are copyrighted, so **never add more**, and never publish or push
  without Kevin deciding to (if the repo is ever republished, `www/icons/` should be removed). The
  extractor's full output (`/icons/`), the decompiled app (`code/`) and `*.apk` stay gitignored. The
  card must keep working without the icons.
- **One place builds a write.** `coordinator.py` does read-modify-write with a fixed field set:
  `applianceId` is never in the body, `humidity` only in Dry, `silence_control` comes from a local
  cache (it is write-only), and the last `operation_token` is echoed. **All writes are serialised
  behind one lock** — two writes with the same token trigger the `E-21291-01718` lockout. Details
  and error codes: `docs/findings.md`, `docs/integration-log.md`.
- **The server often answers 200 and silently overrides fields.** After a write the coordinator
  compares the response with what was asked and explains a mismatch; keep that when adding controls.
- **"What applies in this state" lives in Python** (`controls.py`, `const.py`), not in the card. The
  card only renders the `controls` / `mode_descriptions` / `double_temp_min_gap` attributes of the
  `operation_mode` sensor. Change a rule in Python plus its tests.
- **Add a tooltip and an icon mapping when adding a mode** (`OPERATION_MODE_TOOLTIPS` is enforced by
  a test; `MODE_STYLE` in the card is not).
- **Retiring or gating an entity:** add its unique_id suffix to `_RETIRED_UNIQUE_ID_SUFFIXES` or
  `_MODEL_GATED_UNIQUE_ID_SUFFIXES` in `__init__.py`, or the old registry entry lingers and the card
  shows it as a dead row.
- **Later log entries win.** `docs/integration-log.md` is chronological and corrects itself; when
  entries disagree, the later one is right (and `tests/fixtures/live_captures/39_fuzz_findings.md`
  is the most recent hardware evidence).
- **Kevin asks for commits explicitly.** Don't commit unprompted.

## Environment gotchas

- The test harness pins `homeassistant==2025.1.4` (via `pytest-homeassistant-custom-component`);
  production HA on europa is 2026.9.x. The card depends on internals of the 2026.9 frontend.
- Test-teardown thread-leak false positive from `pycares`: already fixed in `tests/conftest.py`
  (`_prime_pycares_shutdown_thread`); see `docs/integration-log.md` before re-deriving it.
- The CLI forces `aiohttp`'s `ThreadedResolver` because c-ares doesn't get along with europa's
  `systemd-resolved` stub.
- No headless browser here: card appearance is only ever checked by eye; the JS tests cover logic.

## Status and known gaps

The integration (climate, selects, numbers, sensors, switches, config flow with re-auth) and the
Lovelace card are complete and deployed. Open items:

- **Known gaps, deliberately left**: the card's appearance is only ever checked by eye (no headless
  browser here; the JS tests cover logic), tooltips are hover-only (nothing on touch), changing settings during a clean-family mode (the "AC is
  off" guard still fires -- the card now hides those controls instead), `air_flow`/shield-hit in the clean modes,
  `CoolDehumidifying` through HA (CLI-confirmed only), `KeepHeating` (rejected, no flag
  explains it), Kevin's
  unexplained 14:06 power-on, token refresh after 14 days,
  and multiple devices/models (capability gating is only proven on CS-712DX2-W).

## How to leave notes for next time

Keep this file **short**: purpose, map, workflow, rules, open gaps. Put detail where it belongs —
dated findings and decisions in `docs/integration-log.md` (or `docs/lovelace-card.md` for the card),
API facts in `docs/findings.md`, hardware evidence in `tests/fixtures/live_captures/`. A future
session (or Kevin, reading it cold) should be able to pick up exactly where the last one left off
without re-deriving context.
