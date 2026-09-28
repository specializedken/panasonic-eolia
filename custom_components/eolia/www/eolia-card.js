/**
 * eolia-card -- one card for a Panasonic Eolia air conditioner.
 *
 * Shipped inside the `eolia` integration (see frontend.py) and loaded automatically.
 *
 *   type: custom:eolia-card
 *   entity: climate.<your eolia climate entity>
 *
 * Nothing else is configured:
 *   - the rest of the device's entities are found by matching the integration's fixed
 *     `translation_key`s against `hass.entities` for the same device, so it works whatever
 *     the device nickname / area / entity ids are;
 *   - what to show comes from two attributes of the device's `operation_mode` sensor:
 *     `controls` (which controls apply right now) and `mode_descriptions` (the mode tooltips).
 *     Both are computed in Python (controls.py / const.py), which is the ONLY place those rules
 *     and that copy live -- this card holds none of them, so it can't drift from the integration.
 *
 * Top to bottom:
 *   1. Setpoint: Home Assistant's own round dial (`ha-control-circular-slider`, the one the
 *      climate card uses) for the target temperature, or -- when the mode uses one instead --
 *      the Dry humidity target or the double-temperature low/high (two-thumb dial). Drag it or
 *      use -/+; changes are debounced into one write (each write to the unit takes ~3 s).
 *   2. Mode picker (like the app's mode grid) plus an Off button, with a tooltip per mode.
 *   3. Stock `entities` card: fan speed, louvers, AI mode, airflow mode, targeting, nanoeX...
 *      (all real select/switch entities, so they get HA's own labelled rows), and a stock
 *      `glance` of the room.
 * Steps 1-2 are custom; 3 reuses Home Assistant's own cards.
 *
 * If the sensor or its attributes are missing (older integration version), every control is
 * shown rather than none, and there are no tooltips.
 *
 * Optional config:
 *   icons: /eolia_static/icons   # where the app icons are loaded from (default shown: the ones the
 *                                # integration ships in www/icons/). Point it elsewhere, or use
 *                                # `icons: false` to skip them. A mode whose icon file is missing
 *                                # (or has none) falls back to a tinted mdi icon, and a settings
 *                                # row whose <icons>/rows/<key>.png is missing (checked by loading
 *                                # it) simply keeps Home Assistant's own icon.
 */

const CARD_VERSION = "0.9.1";

// Entity rows in the stock settings card, in display order. Each is a translation_key, which is
// also its id in the `controls` list. (The Dry humidity target and the double-temperature
// low/high are on the setpoint dial instead.)
const SETTINGS_ROWS = [
  "fan_speed",
  "vertical_louver",
  "horizontal_louver",
  "ai_mode",
  "air_flow",
  "wind_shield_hit",
  "nanoex",
  "silence_control",
  "air_quality_monitor",
];

// Settings rows that show an app icon (<icons>/rows/<translation_key>.png, made square and
// recoloured by tools/extract_icons.py -- HA draws a row image cropped into a circle).
const ROW_IMAGE_KEYS = ["vertical_louver", "horizontal_louver", "nanoex", "wind_shield_hit"];

const ROOM_KEYS = ["indoor_temperature", "indoor_humidity", "outdoor_temperature"];

// How each preset (= operation_mode) looks in the picker. `icon` is a file name (no extension)
// under <icons>/modes/ from tools/extract_icons.py -- only modes the app actually has an icon
// for; `mdi` is the fallback; `color` is the app's own accent for that mode family.
const MODE_STYLE = {
  Auto: { icon: "v6_drive_mode_automatic", mdi: "mdi:autorenew", color: "#9ccc65" },
  Cooling: { mdi: "mdi:snowflake", color: "#65accc" },
  Heating: { mdi: "mdi:fire", color: "#c19270" },
  KeepHeating: { mdi: "mdi:fire", color: "#c19270" },
  Blast: { icon: "v6_drive_mode_blower", mdi: "mdi:fan", color: "#70c1a3" },
  Dehumidifying: { icon: "v6_drive_mode_dehumidity", mdi: "mdi:water-percent", color: "#70c1a2" },
  CoolDehumidifying: { mdi: "mdi:snowflake-melt", color: "#65accc" },
  ComfortableDehumidification: { icon: "v6_drive_mode_dehumidity", mdi: "mdi:water-percent", color: "#70c1a2" },
  ClothesDryer: { icon: "v6_drive_mode_clothes_drying", mdi: "mdi:tshirt-crew", color: "#70c1a3" },
  MoistCooling: { icon: "v6_drive_mode_moist_cooling", mdi: "mdi:snowflake", color: "#65accc" },
  AutoTempControl: { mdi: "mdi:thermostat-auto", color: "#9ccc65" },
  KeepMode: { mdi: "mdi:thermometer-lines", color: "#c19270" },
  SmellCare: { icon: "v6_drive_mode_smell_care", mdi: "mdi:scent", color: "#aa93d0" },
  SmellCareSpot: { icon: "v6_drive_mode_smell_care", mdi: "mdi:scent", color: "#aa93d0" },
  NanoexCleaning: { icon: "v6_drive_mode_nanoex", mdi: "mdi:shimmer", color: "#aa93d0" },
  Cleaning: { icon: "v6_drive_mode_cleaning", mdi: "mdi:broom", color: "#aa93d0" },
};
const DEFAULT_MODE_STYLE = { mdi: "mdi:air-conditioner", color: "var(--primary-color)" };
const OFF_STYLE = { mdi: "mdi:power", color: "var(--secondary-text-color)" };
const DEFAULT_ICON_BASE = "/eolia_static/icons"; // served by the integration (frontend.py)
const DEBOUNCE_MS = 700;

const CSS = `
.eolia-modes{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:8px;padding:12px}
.eolia-mode{display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 4px;
  border-radius:12px;border:2px solid transparent;background:var(--secondary-background-color);
  color:var(--primary-text-color);font:inherit;font-size:12px;line-height:1.2;text-align:center;cursor:pointer}
.eolia-mode[aria-pressed="true"]{border-color:var(--accent);
  background:color-mix(in srgb,var(--accent) 16%,var(--card-background-color))}
.eolia-mode[disabled]{opacity:.55;cursor:progress}
.eolia-mode .ico{width:36px;height:36px;display:flex;align-items:center;justify-content:center;color:var(--accent)}
.eolia-mode img{width:36px;height:36px;object-fit:contain}
.eolia-mode ha-icon{--mdc-icon-size:32px}
.eolia-setpoint{padding:12px 12px 16px;display:flex;flex-direction:column;align-items:center}
.eolia-dial{position:relative;width:min(320px,100%);
  --control-circular-slider-color:var(--eolia-accent,var(--primary-color))}
.eolia-dial ha-control-circular-slider{display:block;width:100%}
.eolia-info{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:4px;pointer-events:none;color:var(--primary-text-color)}
.eolia-head{font-size:14px;font-weight:500;text-align:center;max-width:60%}
.eolia-val{font-size:44px;font-weight:300;line-height:1.1;white-space:nowrap}
.eolia-val-text{font-size:24px}
.eolia-val small{font-size:16px;margin-left:2px;color:var(--secondary-text-color)}
.eolia-sub{font-size:13px;color:var(--secondary-text-color);min-height:1.2em}
.eolia-buttons{position:absolute;bottom:10px;left:0;right:0;display:flex;justify-content:center;
  gap:24px;pointer-events:none}
.eolia-buttons>*{pointer-events:auto}
.eolia-step{width:48px;height:48px;border-radius:50%;border:1px solid var(--divider-color);
  background:transparent;color:var(--primary-text-color);cursor:pointer;display:flex;
  align-items:center;justify-content:center;padding:0}
.eolia-step[disabled]{opacity:.35;cursor:default}
.eolia-mini,.eolia-mini-spacer{min-height:64px;margin-top:8px}
.eolia-mini{display:flex;justify-content:center;gap:24px;flex-wrap:wrap}
.eolia-mini .eolia-slabel{font-size:12px;color:var(--secondary-text-color);text-align:center}
.eolia-mini .eolia-sctl{display:flex;align-items:center;gap:8px}
.eolia-mini .eolia-step{width:36px;height:36px}
.eolia-mini .eolia-mval{min-width:3.2em;text-align:center;font-size:18px}
`;

const decimals = (step) => (String(step).split(".")[1] || "").length;
const snap = (value, step, min, max) => {
  const snapped = Number((Math.round(value / step) * step).toFixed(decimals(step)));
  return Math.min(max, Math.max(min, snapped));
};

class EoliaCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("eolia-card: set `entity` to your Eolia climate entity.");
    }
    this._config = config;
    this._resetView();
    if (this._container) this._container.replaceChildren();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  static getStubConfig(hass) {
    const climate = Object.values(hass.entities || {}).find(
      (e) => e.platform === "eolia" && e.entity_id.startsWith("climate.")
    );
    return { entity: climate ? climate.entity_id : "" };
  }

  /** Forget everything built for a previous config / set of entities. */
  _resetView() {
    for (const edit of Object.values(this._edits || {})) clearTimeout(edit.timer);
    this._resolvedJson = null;
    this._slots = null;
    this._views = {};
    this._edits = {};
    this._pending = null;
    this._rowImages = null;
  }

  /** Map translation_key -> entity_id for every Eolia entity on the same device. */
  _resolveEntities() {
    const entities = this._hass.entities || {};
    const climate = entities[this._config.entity];
    if (!climate) return null;
    const resolved = { climate: climate.entity_id };
    for (const e of Object.values(entities)) {
      if (
        e.device_id &&
        e.device_id === climate.device_id &&
        e.platform === climate.platform &&
        e.translation_key &&
        !this._isOrphan(e.entity_id)
      ) {
        resolved[e.translation_key] = e.entity_id;
      }
    }
    return resolved;
  }

  /**
   * True for an entity nothing provides any more: its registry entry survives, and HA shows it
   * as an "unavailable" state flagged `restored`. Such a thing must not become a dead row (this
   * is what a switch left over from an older version, or for a feature this model lacks, is).
   */
  _isOrphan(entityId) {
    const st = this._hass.states[entityId];
    return !!(st && st.attributes && st.attributes.restored);
  }

  /** True while the climate entity has no usable state (the unit is offline / not yet read). */
  _isUnreachable(climate) {
    return !!climate && (climate.state === "unavailable" || climate.state === "unknown");
  }

  /** "Unavailable" / "Unknown" in the user's language, as HA's own cards show it. */
  _stateLabel(state) {
    const localize = this._hass.localize;
    const text = localize && localize(`state.default.${state}`);
    return text || (state === "unavailable" ? "Unavailable" : "Unknown");
  }

  _operationModeAttrs(ent) {
    const sensor = ent.operation_mode && this._hass.states[ent.operation_mode];
    return (sensor && sensor.attributes) || {};
  }

  /** The applicable-controls set from the integration, or null if unavailable. */
  _controls(ent) {
    const list = this._operationModeAttrs(ent).controls;
    return Array.isArray(list) ? new Set(list) : null;
  }

  _notify(err) {
    // The integration's errors are written for humans (why the unit refused); surface them.
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message: (err && err.message) || String(err) },
        bubbles: true,
        composed: true,
      })
    );
  }

  /** Rebuild `wrapper` only when `key` changes, so icons/inputs don't flicker on every poll. */
  _rebuild(id, wrapper, key, build) {
    const view = (this._views[id] ||= {});
    if (view.key === key) return;
    view.key = key;
    if (!view.card) {
      view.card = document.createElement("ha-card");
      wrapper.appendChild(view.card);
    }
    view.card.replaceChildren(...build());
  }

  /** Where the Panasonic icons live, or null when the card was told not to use them. */
  _iconBase() {
    return this._config.icons === false ? null : this._config.icons || DEFAULT_ICON_BASE;
  }

  /**
   * Find out which row icons exist (once per config) by loading them; a row only gets an
   * `image` after its file has actually loaded, so nothing is ever a broken picture.
   */
  _probeRowImages() {
    if (this._rowImages) return;
    this._rowImages = {};
    const base = this._iconBase();
    if (!base) return;
    for (const key of ROW_IMAGE_KEYS) {
      const url = `${base}/rows/${key}.png`;
      const img = new Image();
      img.onload = () => {
        this._rowImages[key] = url;
        this._render();
      };
      img.onerror = () => {}; // not installed: the entity keeps its own icon
      img.src = url;
    }
  }

  _icon(style, folder) {
    const mdi = () => {
      const icon = document.createElement("ha-icon");
      icon.setAttribute("icon", style.mdi);
      return icon;
    };
    const base = this._iconBase();
    if (!style.icon || !base) return mdi();
    const img = document.createElement("img");
    img.alt = "";
    img.src = `${base}/${folder}/${style.icon}.png`;
    // The Panasonic artwork isn't shipped; if it wasn't copied, quietly use the mdi icon.
    img.onerror = () => img.replaceWith(mdi());
    return img;
  }

  _label(climate, attr, value) {
    const fmt = this._hass.formatEntityAttributeValue;
    return (fmt && fmt(climate, attr, value)) || String(value);
  }

  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      this._notify(err);
    }
  }

  // --- 1. Setpoint -----------------------------------------------------------------------

  _stateNumber(entityId) {
    const st = entityId && this._hass.states[entityId];
    const value = st ? parseFloat(st.state) : NaN;
    return Number.isNaN(value) ? undefined : value;
  }

  /**
   * What the dial shows right now, or null only if the climate entity is missing:
   * {heading, dual, disabled, unit, accent, current, steppers[{key,label,value,min,max,step,send}]}.
   * When the mode has no target (odor care, off, ...) it is a `disabled` spec: the dial stays
   * on screen greyed out, so the card's layout never changes with the mode.
   */
  _setpoint(ent, controls) {
    const states = this._hass.states;
    const has = (id) => controls !== null && controls.has(id);
    const climate = states[ent.climate];
    const mode = climate && climate.attributes.preset_mode;
    const accent = (MODE_STYLE[mode] || DEFAULT_MODE_STYLE).color;
    const numberStepper = (key, label) => {
      const st = ent[key] && states[ent[key]];
      const value = this._stateNumber(ent[key]);
      if (value === undefined) return null;
      const a = st.attributes;
      return {
        key,
        label,
        value,
        unit: a.unit_of_measurement || "",
        min: a.min ?? 0,
        max: a.max ?? 100,
        step: a.step ?? 1,
        send: (v) => this._call("number", "set_value", { entity_id: ent[key], value: v }),
      };
    };
    const tempUnit =
      (this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.temperature) || "°C";
    if (!climate) return null;
    const ca = climate.attributes;
    const current = (key) => this._stateNumber(ent[key]);
    // Same shape as the temperature dial, so switching between "has a target" and "has none"
    // reuses the dial instead of rebuilding it.
    const disabledSpec = {
      heading: climate.state === "off" ? "Off" : "No target in this mode",
      dual: false,
      disabled: true,
      unit: tempUnit,
      accent: "var(--disabled-color)",
      current: current("indoor_temperature"),
      steppers: [
        {
          key: "temperature",
          label: "",
          value: ca.min_temp ?? 16,
          min: ca.min_temp ?? 16,
          max: ca.max_temp ?? 30,
          step: ca.target_temp_step ?? 0.5,
          send: () => {},
        },
      ],
    };

    // Like HA's thermostat card: an unreachable unit shows its state ("Unavailable") where the
    // temperature would be, on the greyed dial.
    if (this._isUnreachable(climate)) {
      return {
        ...disabledSpec,
        heading: "",
        valueText: this._stateLabel(climate.state),
        current: undefined,
      };
    }

    if (has("double_temp_low")) {
      const steppers = [numberStepper("double_temp_low", "Low"), numberStepper("double_temp_high", "High")];
      if (steppers.every(Boolean)) {
        return {
          heading: "Keep the room between",
          dual: true,
          // low/high must be this far apart; the integration says how far (const.py) and also
          // nudges the other bound server-side -- we mirror it here so the dial moves at once.
          minGap: this._operationModeAttrs(ent).double_temp_min_gap,
          disabled: false,
          unit: tempUnit,
          accent,
          current: current("indoor_temperature"),
          steppers,
        };
      }
    } else if (has("dry_humidity_target")) {
      const stepper = numberStepper("dry_humidity_target", "");
      if (stepper) {
        return {
          heading: "Humidity target",
          dual: false,
          disabled: false,
          unit: stepper.unit || "%",
          accent,
          current: current("indoor_humidity"),
          steppers: [stepper],
        };
      }
    } else if ((controls === null || controls.has("temperature")) && typeof ca.temperature === "number") {
      return {
        heading: "Target temperature",
        dual: false,
        disabled: false,
        unit: tempUnit,
        accent,
        current: current("indoor_temperature"),
        steppers: [
          {
            ...disabledSpec.steppers[0],
            value: ca.temperature,
            send: (v) => this._call("climate", "set_temperature", { entity_id: ent.climate, temperature: v }),
          },
        ],
      };
    }
    return disabledSpec;
  }

  /**
   * Move the shown value locally now; send ONE write once changes stop for DEBOUNCE_MS.
   * `linked` are other values shown moved by this change (the other double-temp bound, pushed
   * to keep the gap): shown locally, never sent themselves -- the coordinator makes the same
   * nudge server-side -- and dropped together with this edit.
   */
  _commit(stepper, raw, linked = []) {
    if (!Number.isFinite(raw)) return;
    const next = snap(raw, stepper.step, stepper.min, stepper.max);
    const edit = (this._edits[stepper.key] ||= {});
    clearTimeout(edit.timer);
    edit.value = next;
    edit.linked = linked.map((l) => l.key);
    for (const l of linked) this._edits[l.key] = { value: l.value };
    edit.timer = setTimeout(async () => {
      await stepper.send(next);
      delete this._edits[stepper.key];
      // a linked value that has since been moved by its own change keeps that (it has a timer)
      for (const key of edit.linked) if (this._edits[key] && !this._edits[key].timer) delete this._edits[key];
      this._render();
    }, DEBOUNCE_MS);
    this._render();
  }

  /**
   * Both bounds after moving bound `index` to `raw`, keeping them `sp.minGap` apart: the other
   * bound is pushed (as the coordinator does server-side); if it hits its own limit the moved
   * one is held back instead. No minGap known -> no enforcement (the server still nudges).
   */
  _withGap(sp, index, raw) {
    const [lo, hi] = sp.steppers;
    let low = index === 0 ? snap(raw, lo.step, lo.min, lo.max) : this._shown(lo);
    let high = index === 1 ? snap(raw, hi.step, hi.min, hi.max) : this._shown(hi);
    const gap = sp.minGap;
    if (Number.isFinite(gap) && high - low < gap) {
      if (index === 0) {
        high = snap(low + gap, hi.step, hi.min, hi.max);
        if (high - low < gap) low = snap(high - gap, lo.step, lo.min, lo.max);
      } else {
        low = snap(high - gap, lo.step, lo.min, lo.max);
        if (high - low < gap) high = snap(low + gap, hi.step, hi.min, hi.max);
      }
    }
    return [low, high];
  }

  /** A double-temperature change: show the gap-corrected pair now, send only the moved bound. */
  _commitDual(sp, index, raw) {
    if (!Number.isFinite(raw)) return;
    const pair = this._withGap(sp, index, raw);
    const other = 1 - index;
    this._commit(sp.steppers[index], pair[index], [{ key: sp.steppers[other].key, value: pair[other] }]);
  }

  /** A -/+ tap, relative to what's currently shown (so rapid taps accumulate). */
  _bump(stepper, direction) {
    const base = this._shown(stepper);
    const next = snap(base + direction * stepper.step, stepper.step, stepper.min, stepper.max);
    if (next === base) return;
    const sp = this._views.setpoint && this._views.setpoint.sp;
    const index = sp && sp.dual ? sp.steppers.findIndex((s) => s.key === stepper.key) : -1;
    if (index >= 0) this._commitDual(sp, index, next);
    else this._commit(stepper, next);
  }

  _renderSetpoint(ent, wrapper, controls) {
    const sp = this._setpoint(ent, controls);
    if (!sp) {
      wrapper.hidden = true;
      return;
    }
    wrapper.hidden = false;
    const view = (this._views.setpoint ||= {});
    view.sp = sp; // handlers created below always read the latest spec from here
    // Build once per shape; afterwards update in place, so a poll can't interrupt a drag.
    const shape = JSON.stringify([sp.dual, sp.steppers.map((s) => [s.key, s.min, s.max, s.step])]);
    if (view.shape !== shape) {
      view.shape = shape;
      if (!view.card) {
        view.card = document.createElement("ha-card");
        wrapper.appendChild(view.card);
      }
      view.card.replaceChildren(this._buildSetpoint(view));
    }
    this._updateSetpoint(view);
  }

  _stepButton(direction, onClick) {
    const button = document.createElement("button");
    button.className = "eolia-step";
    button.setAttribute("aria-label", direction < 0 ? "Decrease" : "Increase");
    const glyph = document.createElement("ha-icon");
    glyph.setAttribute("icon", direction < 0 ? "mdi:minus" : "mdi:plus");
    button.appendChild(glyph);
    button.addEventListener("click", onClick);
    return button;
  }

  /** Static structure; every value is filled in by _updateSetpoint. Handlers read view.sp. */
  _buildSetpoint(view) {
    const dual = view.sp.dual;
    const refs = (view.refs = { minis: [] });
    const root = document.createElement("div");
    root.className = "eolia-setpoint";
    const dial = document.createElement("div");
    dial.className = "eolia-dial";
    refs.dial = dial;

    const slider = document.createElement("ha-control-circular-slider");
    slider.preventInteractionOnScroll = true;
    refs.slider = slider;
    // The dial's events can arrive without a usable value (e.g. at the start or end of a
    // drag); ignore those instead of formatting/writing garbage.
    const usable = (values) => values.every((v) => Number.isFinite(v));
    const live = (values) => {
      if (!usable(values)) return;
      this._dragging = true;
      refs.showValues(values);
    };
    if (dual) {
      slider.setAttribute("dual", "");
      slider.dual = true;
      // While a thumb is dragged, the other one is pushed live so the 5-degree rule is visible
      // immediately instead of after the status round-trip.
      const liveDual = (index, value) => {
        if (!Number.isFinite(value)) return;
        const pair = this._withGap(view.sp, index, value);
        live(pair);
        if (index === 0) slider.high = pair[1];
        else slider.low = pair[0];
      };
      slider.addEventListener("low-changing", (e) => liveDual(0, e.detail.value));
      slider.addEventListener("high-changing", (e) => liveDual(1, e.detail.value));
      slider.addEventListener("low-changed", (e) => { this._dragging = false; this._commitDual(view.sp, 0, e.detail.value); });
      slider.addEventListener("high-changed", (e) => { this._dragging = false; this._commitDual(view.sp, 1, e.detail.value); });
    } else {
      slider.mode = "start";
      slider.addEventListener("value-changing", (e) => live([e.detail.value]));
      slider.addEventListener("value-changed", (e) => { this._dragging = false; this._commit(view.sp.steppers[0], e.detail.value); });
    }

    const info = document.createElement("div");
    info.className = "eolia-info";
    refs.head = document.createElement("div");
    refs.head.className = "eolia-head";
    refs.val = document.createElement("div");
    refs.val.className = "eolia-val";
    refs.valText = document.createElement("span");
    refs.valUnit = document.createElement("small");
    refs.val.append(refs.valText, refs.valUnit);
    refs.sub = document.createElement("div");
    refs.sub.className = "eolia-sub";
    info.append(refs.head, refs.val, refs.sub);
    dial.append(slider, info);

    if (!dual) {
      const buttons = document.createElement("div");
      buttons.className = "eolia-buttons";
      refs.minus = this._stepButton(-1, () => this._bump(view.sp.steppers[0], -1));
      refs.plus = this._stepButton(1, () => this._bump(view.sp.steppers[0], 1));
      buttons.append(refs.minus, refs.plus);
      dial.appendChild(buttons);
    }
    root.appendChild(dial);

    if (!dual) {
      // The two-thumb layout has a row of Low/High steppers under the dial; keep the same
      // height here so switching modes never moves what's below.
      const spacer = document.createElement("div");
      spacer.className = "eolia-mini-spacer";
      root.appendChild(spacer);
    }
    if (dual) {
      // The dial has no room for two sets of -/+ buttons, so each bound gets its own small one.
      const mini = document.createElement("div");
      mini.className = "eolia-mini";
      view.sp.steppers.forEach((s, i) => {
        const box = document.createElement("div");
        const label = document.createElement("div");
        label.className = "eolia-slabel";
        label.textContent = s.label;
        const ctl = document.createElement("div");
        ctl.className = "eolia-sctl";
        const m = { minus: this._stepButton(-1, () => this._bump(view.sp.steppers[i], -1)), plus: this._stepButton(1, () => this._bump(view.sp.steppers[i], 1)) };
        m.val = document.createElement("span");
        m.val.className = "eolia-mval";
        ctl.append(m.minus, m.val, m.plus);
        box.append(label, ctl);
        mini.appendChild(box);
        refs.minis.push(m);
      });
      root.appendChild(mini);
    }
    return root;
  }

  /** The value to show for a stepper: the pending edit if there is one, else the state. */
  _shown(stepper) {
    return this._edits[stepper.key] ? this._edits[stepper.key].value : stepper.value;
  }

  _updateSetpoint(view) {
    const { refs, sp } = view;
    const fmt = (s, v) => v.toFixed(decimals(s.step));
    const shown = sp.steppers.map((s) => this._shown(s));
    refs.showValues = (values) => {
      refs.valText.textContent = values.map((v, i) => fmt(sp.steppers[i], v)).join(" – ");
    };
    refs.head.textContent = sp.heading;
    refs.valUnit.textContent = sp.disabled ? "" : sp.unit;
    refs.slider.disabled = !!sp.disabled;
    refs.sub.textContent =
      sp.current !== undefined ? `Currently ${sp.current}${sp.unit}` : "";
    refs.slider.min = Math.min(...sp.steppers.map((s) => s.min));
    refs.slider.max = Math.max(...sp.steppers.map((s) => s.max));
    refs.slider.step = sp.steppers[0].step;
    refs.slider.current = sp.current;
    refs.dial.style.setProperty("--eolia-accent", sp.accent);
    // A word ("Unavailable") doesn't fit the big-number size.
    refs.val.className = sp.valueText ? "eolia-val eolia-val-text" : "eolia-val";
    if (sp.disabled) {
      refs.valText.textContent = sp.valueText || "–";
    } else if (!this._dragging) {
      // Never touch the dial mid-drag: a poll landing then would yank the thumb.
      if (sp.dual) {
        refs.slider.low = shown[0];
        refs.slider.high = shown[1];
      } else {
        refs.slider.value = shown[0];
      }
      refs.showValues(shown);
    }
    if (refs.minus) {
      refs.minus.disabled = sp.disabled || shown[0] <= sp.steppers[0].min;
      refs.plus.disabled = sp.disabled || shown[0] >= sp.steppers[0].max;
    }
    refs.minis.forEach((m, i) => {
      const s = sp.steppers[i];
      m.val.textContent = `${fmt(s, shown[i])}${sp.unit}`;
      m.minus.disabled = shown[i] <= s.min;
      m.plus.disabled = shown[i] >= s.max;
    });
  }

  // --- 2. Mode picker ----------------------------------------------------------------------

  _renderPicker(ent, wrapper, controls) {
    const climate = this._hass.states[ent.climate];
    const presets = (climate && climate.attributes.preset_modes) || [];
    const current = climate && climate.attributes.preset_mode;
    const isOff = climate && climate.state === "off";
    const unreachable = this._isUnreachable(climate);
    if (!presets.length) {
      wrapper.hidden = true;
      return;
    }
    wrapper.hidden = false;
    const descriptions = this._operationModeAttrs(ent).mode_descriptions || {};
    const key = JSON.stringify([presets, current, isOff, unreachable, this._pending, this._config.icons, descriptions]);
    this._rebuild("modes", wrapper, key, () => {
      const grid = document.createElement("div");
      grid.className = "eolia-modes";
      const add = (look, text, tip, active, onClick) => {
        const button = document.createElement("button");
        button.className = "eolia-mode";
        button.setAttribute("aria-pressed", String(active));
        button.style.setProperty("--accent", look.color);
        button.disabled = this._pending != null || unreachable;
        if (tip) button.title = tip;
        const ico = document.createElement("span");
        ico.className = "ico";
        ico.appendChild(this._icon(look, "modes"));
        const label = document.createElement("span");
        label.textContent = text;
        button.append(ico, label);
        button.addEventListener("click", onClick);
        grid.appendChild(button);
      };
      // Off first, then the modes in the order the integration lists them (const.PRESET_MODE_ORDER;
      // the card keeps no ordering of its own).
      add(OFF_STYLE, "Off", "Turn the unit off.", isOff, () => this._turnOff(ent.climate, isOff));
      for (const mode of presets) {
        add(
          MODE_STYLE[mode] || DEFAULT_MODE_STYLE,
          this._label(climate, "preset_mode", mode),
          descriptions[mode],
          !isOff && mode === current,
          () => this._selectMode(ent.climate, mode, current)
        );
      }
      return [grid];
    });
  }

  /** Run a service call with every picker button disabled until it finishes. */
  async _withPending(name, run) {
    this._pending = name;
    this._render();
    try {
      await run();
    } finally {
      this._pending = null;
      this._render();
    }
  }

  async _selectMode(entityId, mode, current) {
    if (mode === current || this._pending != null) return;
    await this._withPending(mode, () =>
      this._call("climate", "set_preset_mode", { entity_id: entityId, preset_mode: mode })
    );
  }

  async _turnOff(entityId, isOff) {
    if (isOff || this._pending != null) return;
    await this._withPending("off", () => this._call("climate", "turn_off", { entity_id: entityId }));
  }

  // --- Layout ------------------------------------------------------------------------------

  _showError(text) {
    this._container.textContent = text;
    this._slots = null;
    this._resolvedJson = null;
  }

  /** Slots, top to bottom. `render` = custom section; `config` = stock card config or null. */
  _slotDefs() {
    return [
      { id: "setpoint", render: (ent, wrapper, controls) => this._renderSetpoint(ent, wrapper, controls) },
      { id: "modes", render: (ent, wrapper, controls) => this._renderPicker(ent, wrapper, controls) },
      {
        id: "settings",
        config: ({ ent, allows }) => {
          const rows = SETTINGS_ROWS.filter((k) => ent[k] && allows(k)).map((k) =>
            this._rowImages && this._rowImages[k] ? { entity: ent[k], image: this._rowImages[k] } : ent[k]
          );
          if (ent.operation_mode) rows.unshift(ent.operation_mode);
          return rows.length ? { type: "entities", entities: rows } : null;
        },
      },
      {
        id: "room",
        config: ({ ent }) => {
          const rows = ROOM_KEYS.filter((k) => ent[k]).map((k) => ent[k]);
          return rows.length ? { type: "glance", entities: rows } : null;
        },
      },
    ];
  }

  async _buildSlots(ent) {
    const helpers = await window.loadCardHelpers();
    // The circular slider is only defined once HA has loaded its thermostat card, so ask for
    // one (never attached to the page). If it isn't defined the -/+ buttons still work.
    try {
      helpers.createCardElement({ type: "thermostat", entity: ent.climate });
    } catch (err) {
      /* the dial just stays a plain box */
    }
    return this._slotDefs().map((def) => {
      const wrapper = document.createElement("div");
      wrapper.hidden = true;
      return { def, wrapper, element: null, appliedJson: null, helpers };
    });
  }

  async _render() {
    if (!this._hass || !this._config) return;
    if (!this._container) {
      this._container = document.createElement("div");
      this._container.style.cssText = "display:flex;flex-direction:column;gap:8px;";
      this.appendChild(this._container);
    }

    const ent = this._resolveEntities();
    if (!ent) {
      this._showError(`eolia-card: entity ${this._config.entity} not found.`);
      return;
    }

    // (Re)build only when the set of resolved entities changes, not on every state update.
    const json = JSON.stringify(ent);
    if (json !== this._resolvedJson) {
      this._resolvedJson = json;
      this._slots = null;
      this._views = {};
      const building = this._buildSlots(ent);
      this._building = building;
      const slots = await building;
      if (this._building !== building) return; // superseded by a newer resolve
      this._slots = slots;
      const style = document.createElement("style");
      style.textContent = CSS;
      this._container.replaceChildren(style, ...slots.map((s) => s.wrapper));
    }
    if (!this._slots) return;
    this._probeRowImages();

    const controls = this._controls(ent);
    // Without the attribute, show everything rather than nothing.
    const allows = (id) => id === null || controls === null || controls.has(id);
    for (const slot of this._slots) {
      if (slot.def.render) {
        slot.def.render(ent, slot.wrapper, controls);
        continue;
      }
      const cfg = slot.def.config({ ent, allows });
      if (!cfg) {
        slot.wrapper.hidden = true;
        continue;
      }
      const cfgJson = JSON.stringify(cfg);
      if (!slot.element) {
        slot.element = slot.helpers.createCardElement(cfg);
        slot.wrapper.appendChild(slot.element);
        slot.appliedJson = cfgJson;
      } else if (slot.appliedJson !== cfgJson) {
        slot.element.setConfig(cfg);
        slot.appliedJson = cfgJson;
      }
      slot.wrapper.hidden = false;
      slot.element.hass = this._hass;
    }
  }
}

if (!customElements.get("eolia-card")) {
  customElements.define("eolia-card", EoliaCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "eolia-card")) {
  window.customCards.push({
    type: "eolia-card",
    name: "Panasonic Eolia",
    description:
      "Controls for an Eolia air conditioner, showing only what applies to its current state.",
  });
}

console.info(`%c EOLIA-CARD %c ${CARD_VERSION} `, "background:#03a9f4;color:#fff", "");
