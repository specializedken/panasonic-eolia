# Documentation

Start with the top-level [`README.md`](../README.md) for what this is and how to use it. These
documents are for people (and Claude sessions) working on the code.

| Document | What it is |
|---|---|
| [`findings.md`](findings.md) | **The API reference.** Auth (Auth0 + PKCE), the endpoints, request/response JSON shapes, the `operation_mode` / `ai_control` enums, error codes. Started as the static analysis of the decompiled app, then corrected against live traffic. |
| [`integration-log.md`](integration-log.md) | Dated log of building the integration: every live finding, every bug found by watching real traffic, the fuzzing and A/B rounds. Chronological; **later entries correct earlier ones**. |
| [`lovelace-card.md`](lovelace-card.md) | The Lovelace card: the current design (where the code is, layout, icons, deploying, debugging), then its design log. |
| [`research-history.md`](research-history.md) | How the project started: the reverse-engineering plan (static decompile, then live capture), what each track found, and how the blockers were resolved. |
| [`phase1-plan.md`](phase1-plan.md) | The original design plan for the first phase of the integration (scope, module layout, testing plan). |

Evidence lives next to the tests rather than here:
[`../tests/fixtures/live_captures/`](../tests/fixtures/live_captures/README.md) holds the real
request/response pairs captured against the unit (with an index), and its
`39_fuzz_findings.md` is the write-up of the random-walk fuzzing and A/B experiments — the most
recent hardware evidence.

`../CLAUDE.md` is the short working guide for Claude Code sessions (repo map, deploy workflow,
rules that must not be broken, open gaps).
