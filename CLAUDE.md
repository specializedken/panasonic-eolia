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

## How to leave notes for next time

Keep this file (or a linked `findings.md` in this same directory) updated as work progresses —
what's been tried, what worked, what the actual API turned out to look like, and the concrete
next step. Treat it as living memory the same way `os-management` does for europa: a future
session (or Kevin, reading it cold) should be able to pick up exactly where the last one left
off without re-deriving context.
