# Panasonic Eolia for Home Assistant

An **unofficial** Home Assistant custom integration, plus a Lovelace card, for Panasonic **Eolia**
air conditioners (the Japanese "Smart App" models). It talks to Panasonic's cloud the same way the
official Eolia app does.

## Why

Eolia units can be reached over ECHONET Lite, but ECHONET only mirrors a small part of the state.
It never carries **AI mode / ECONAVI**, **nanoeX**, airflow targeting, or the difference between
**Dry** (Comfortable Dehumidify) and **Cool & Dehumidify**. Those exist only in the app's cloud
API, so that is what this integration uses. The API was reverse-engineered from the Android app
(see [`docs/findings.md`](docs/findings.md)); the two older open-source clients for it are
archived and no longer work.

> **Status:** working and in daily use on one real unit (**CS-712DX2-W**, Eolia X). Nothing else
> has been tested — see [Limitations](#limitations).

## What you get

Per air conditioner:

| Platform | Entities |
|---|---|
| `climate` | On/off, target temperature (16–30 °C, 0.5 steps), fan speed, both louver axes, and the **full mode list** as presets: Auto, Dry, Cooling, Cool & Dehumidify, Moist Cooling, Heating, Double temperature, Clothes drying, Odor care, Away clean, Self-clean, Fan only (only the modes your model supports are offered) |
| `select` | AI mode (off / AI comfort / AI comfort + ECONAVI), Airflow mode (normal / quiet / powerful / long reach), Airflow targeting (normal / avoid people / aim at people), Fan speed, Vertical louver, Horizontal louver |
| `number` | Dry humidity target (50–60 %, 5 % steps), Double temperature low and high |
| `sensor` | Operation mode (the raw mode the unit reports), indoor temperature, indoor humidity, outdoor temperature |
| `switch` | nanoeX, Quiet mode |

Air-quality entities (a monitoring switch and two sensors) are created **only on models that report
the feature**; the CS-712DX2-W does not, so none appear there.

Not covered: the weekly timer, AI scenes, energy history, notifications and firmware checks.

## Requirements

- Home Assistant. Developed and run on **2026.9**; the unit tests use the 2025.1 test harness.
  Older versions are untested (the card in particular relies on recent frontend internals).
- A Panasonic ID with at least one Eolia unit registered in the official app.

## Install

Manual install (there is no HACS metadata yet):

1. Copy the `custom_components/eolia/` folder from this repo into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.

## Set up

**Settings → Devices & services → Add integration → Panasonic Eolia.**

Panasonic's login only accepts the Eolia app's own redirect address, so Home Assistant's built-in
OAuth helper can't be used. Instead you log in once in a browser and hand the result to the
integration (the form walks you through it):

1. Open the login link shown in the form and sign in with your Panasonic ID.
2. The page will **fail to load after you log in — that is expected.**
3. Open your browser's Developer Tools → **Network** tab and find the request whose name starts
   with `panasonic-eolia:`.
4. Copy its full **Request URL** (it contains `code=…`) and paste it into the form.

The integration then keeps the token refreshed on its own. If Panasonic ever rejects it, Home
Assistant asks you to log in again.

## The Lovelace card

The integration ships its own card and registers it automatically; there is nothing to install.
Add it to a dashboard:

```yaml
type: custom:eolia-card
entity: climate.your_eolia_climate
```

Only the climate entity is configured; the card finds everything else on the same device, so it
works whatever the device is called. It shows:

1. **A round setpoint dial** (Home Assistant's own dial): target temperature, the humidity target in
   Dry, or a low/high pair in Double temperature (kept at least 5° apart). In modes with no target it
   stays on screen greyed out so the layout doesn't move.
2. **A mode grid**: Off plus every mode, each with a tooltip explaining what it does.
3. **The remaining controls**, and only the ones that apply to the current mode: fan speed, louvers,
   AI mode, airflow mode, targeting, nanoeX, quiet mode, plus the room's temperature and humidity.

### Icons

The mode grid and four of the settings rows use icons taken from the official Eolia app: 8 mode icons
and 4 row icons (the row ones are square-padded and recoloured versions of the app's). They live in
`custom_components/eolia/www/icons/`, and the integration serves them, so the card has them with no
setup. **They are Panasonic's artwork**, included for convenience; see the [disclaimer](#disclaimer).

The card works without them (a mode with no icon, or a missing file, falls back to a standard Home
Assistant icon), and you can change this in the card config:

```yaml
type: custom:eolia-card
entity: climate.your_eolia_climate
icons: false                  # don't use them
# icons: /local/my-icons      # or load the same layout (modes/, rows/) from your own folder
```

To regenerate them from your own copy of the app: decompile it with
[`jadx`](https://github.com/skylot/jadx) into `code/`, run `python tools/extract_icons.py` (the row
icons need `pip install Pillow`), and copy the files the card uses from the git-ignored `icons/`
output into `custom_components/eolia/www/icons/`.

## Limitations

- **One model tested.** Everything was validated on a single CS-712DX2-W. Other models expose
  different features (the integration reads each model's capability list and hides what it lacks),
  but that has only been proven on this one. Multiple devices on one account are untested.
- **It's a cloud API.** State is polled every 60 s, and each write takes about 3 seconds. Writes are
  sent one at a time, because overlapping writes make Panasonic lock the account out for a couple of
  minutes.
- **Some things the server simply refuses**, and the integration says so in plain language instead of
  failing silently: changing a setting while the unit is off, or one the current mode ignores
  (for example AI mode in Fan only).
- **It can break.** Panasonic can change this private API at any time — that is what killed the
  earlier open-source clients.
- Panasonic's access token lasts 14 days and the integration refreshes it itself. That refresh is
  covered by unit tests, but a real expiry has not been waited out yet.

## Development

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
python -m pytest -q          # Python tests
node --test tests/js/        # Lovelace card tests (Node 18+)
```

Where things are:

| Path | What |
|---|---|
| `custom_components/eolia/` | The integration |
| `custom_components/eolia/www/eolia-card.js` | **The Lovelace card** (plain JavaScript, no build step); `frontend.py` serves and registers it |
| `custom_components/eolia/www/icons/` | **The icons the card uses** (12 PNGs, Panasonic's artwork), served by the integration |
| `tests/` | Python tests; `tests/js/` holds the card's tests; `tests/fixtures/` holds real captured API traffic |
| `tools/` | Scripts that talk to the real cloud and unit (CLI, fuzzer, icon extractor) — see the warning below |
| `docs/` | The API reference, the development log and the card's design notes ([index](docs/README.md)) |
| `icons/` | Git-ignored local output of `tools/extract_icons.py` (~100 files); only the 12 the card uses are committed, under `www/icons/` above |

**Warning:** the scripts in `tools/` (`eolia_cli.py set`, the fuzzer, the A/B runner) send real
commands to your air conditioner. Read what they do before running them.

## Disclaimer

This project is not affiliated with, endorsed by or supported by Panasonic. *Panasonic*, *Eolia* and
*nanoeX* are trademarks of Panasonic Holdings Corporation. The integration signs in to Panasonic's
cloud with your own account and controls your own appliance; use it at your own risk.

The icons in `custom_components/eolia/www/icons/` are copyrighted Panasonic artwork taken from the
Eolia app. They are included for convenience and are **not** covered by any license this project may
grant for its code; Panasonic keeps all rights in them. If you republish or redistribute this
repository, remove that folder (the card still works without it).

## License

No license has been chosen yet, so the default applies: all rights reserved.
