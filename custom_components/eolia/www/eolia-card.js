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
 *   1. Setpoint: target temperature, or -- when the mode uses one instead -- the Dry humidity
 *      target or the double-temperature low/high. Big +/- steppers; taps are debounced into one
 *      write (each write to the unit takes ~3 s).
 *   2. Mode picker (like the app's mode grid) plus an Off button, with a tooltip per mode.
 *   3. Airflow: fan speed and both louvers, as labelled rows.
 *   4. Stock `entities` card for the remaining switches/selects, and a `glance` of the room.
 * Steps 1-3 are custom; 4 reuses Home Assistant's own cards.
 *
 * If the sensor or its attributes are missing (older integration version), every control is
 * shown rather than none, and there are no tooltips.
 *
 * Optional config:
 *   icons: /local/eolia-icons   # where Panasonic's app icons were copied (default shown);
 *                               # `icons: false` skips them. Those icons are Panasonic's
 *                               # artwork and are NOT shipped with the integration -- copy them
 *                               # yourself with tools/extract_icons.py. A mode whose icon file
 *                               # is missing (or has none) falls back to a tinted mdi icon.
 */

const CARD_VERSION = "0.4.0";

// Entity rows in the stock settings card, in display order. Each is a translation_key, which is
// also its id in the `controls` list. (The Dry humidity target and the double-temperature
// low/high are steppers in the setpoint section instead.)
const SETTINGS_ROWS = [
  "ai_mode",
  "nanoex",
  "silence_control",
  "air_flow",
  "wind_shield_hit",
  "air_quality_monitor",
  "double_temp_enabled",
];

const ROOM_KEYS = ["indoor_temperature", "indoor_humidity", "outdoor_temperature"];

// The climate entity's own airflow attributes, as labelled rows. `gate` is the control id.
const AIRFLOW_ROWS = [
  { gate: "fan", label: "Fan speed", attr: "fan_mode", list: "fan_modes", service: "set_fan_mode" },
  { gate: "louvers", label: "Vertical louver", attr: "swing_mode", list: "swing_modes", service: "set_swing_mode" },
  {
    gate: "louvers",
    label: "Horizontal louver",
    attr: "swing_horizontal_mode",
    list: "swing_horizontal_modes",
    service: "set_swing_horizontal_mode",
  },
];

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
const DEFAULT_ICON_BASE = "/local/eolia-icons";
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
.eolia-setpoint{padding:12px 12px 16px;text-align:center}
.eolia-head{font-size:13px;color:var(--secondary-text-color);margin-bottom:4px}
.eolia-steppers{display:flex;justify-content:center;gap:24px;flex-wrap:wrap}
.eolia-slabel{font-size:12px;color:var(--secondary-text-color)}
.eolia-sctl{display:flex;align-items:center;justify-content:center;gap:12px}
.eolia-val{font-size:40px;font-weight:300;line-height:1.1;min-width:3.5ch;color:var(--primary-text-color)}
.eolia-val small{font-size:16px;margin-left:2px;color:var(--secondary-text-color)}
.eolia-step{width:44px;height:44px;border-radius:50%;border:1px solid var(--divider-color);
  background:var(--secondary-background-color);color:var(--primary-text-color);cursor:pointer;
  display:flex;align-items:center;justify-content:center;padding:0}
.eolia-step[disabled]{opacity:.35;cursor:default}
.eolia-airflow{padding:4px 16px}
.eolia-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0}
.eolia-row label{color:var(--primary-text-color)}
.eolia-row select{background:var(--secondary-background-color);color:var(--primary-text-color);
  border:1px solid var(--divider-color);border-radius:8px;padding:6px 8px;font:inherit;min-width:9em}
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
        e.translation_key
      ) {
        resolved[e.translation_key] = e.entity_id;
      }
    }
    return resolved;
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

  _icon(style, folder) {
    const mdi = () => {
      const icon = document.createElement("ha-icon");
      icon.setAttribute("icon", style.mdi);
      return icon;
    };
    const base = this._config.icons === false ? null : this._config.icons || DEFAULT_ICON_BASE;
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

  /** What the top control adjusts right now: {heading, steppers[]}, or null for nothing. */
  _setpoint(ent, controls) {
    const states = this._hass.states;
    const has = (id) => controls !== null && controls.has(id);
    const numberStepper = (key, label) => {
      const st = ent[key] && states[ent[key]];
      const value = st ? parseFloat(st.state) : NaN;
      if (Number.isNaN(value)) return null;
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

    if (has("double_temp_low")) {
      const steppers = [numberStepper("double_temp_low", "Low"), numberStepper("double_temp_high", "High")];
      return { heading: "Keep the room between", steppers: steppers.filter(Boolean) };
    }
    if (has("dry_humidity_target")) {
      const s = numberStepper("dry_humidity_target", "");
      return { heading: "Humidity target", steppers: s ? [s] : [] };
    }
    if (controls === null || controls.has("temperature")) {
      const climate = states[ent.climate];
      const a = climate && climate.attributes;
      if (a && typeof a.temperature === "number") {
        const unit = (this._hass.config && this._hass.config.unit_system && this._hass.config.unit_system.temperature) || "°C";
        return {
          heading: "Target temperature",
          steppers: [
            {
              key: "temperature",
              label: "",
              value: a.temperature,
              unit,
              min: a.min_temp ?? 16,
              max: a.max_temp ?? 30,
              step: a.target_temp_step ?? 0.5,
              send: (v) => this._call("climate", "set_temperature", { entity_id: ent.climate, temperature: v }),
            },
          ],
        };
      }
    }
    return null;
  }

  /** One tap: move the shown value locally now, send a single write once taps stop. */
  _bump(stepper, direction) {
    const edit = (this._edits[stepper.key] ||= {});
    const base = edit.value ?? stepper.value;
    const next = snap(base + direction * stepper.step, stepper.step, stepper.min, stepper.max);
    if (next === base) return;
    edit.value = next;
    clearTimeout(edit.timer);
    edit.timer = setTimeout(async () => {
      await stepper.send(next);
      delete this._edits[stepper.key];
      this._render();
    }, DEBOUNCE_MS);
    this._render();
  }

  _renderSetpoint(ent, wrapper, controls) {
    const setpoint = this._setpoint(ent, controls);
    if (!setpoint || !setpoint.steppers.length) {
      wrapper.hidden = true;
      return;
    }
    wrapper.hidden = false;
    const shown = (s) => (this._edits[s.key] ? this._edits[s.key].value : s.value);
    const key = JSON.stringify([setpoint.heading, setpoint.steppers.map((s) => [s.key, shown(s), s.min, s.max, s.step, s.unit])]);
    this._rebuild("setpoint", wrapper, key, () => {
      const root = document.createElement("div");
      root.className = "eolia-setpoint";
      const head = document.createElement("div");
      head.className = "eolia-head";
      head.textContent = setpoint.heading;
      const row = document.createElement("div");
      row.className = "eolia-steppers";
      for (const s of setpoint.steppers) {
        const value = shown(s);
        const box = document.createElement("div");
        if (s.label) {
          const label = document.createElement("div");
          label.className = "eolia-slabel";
          label.textContent = s.label;
          box.appendChild(label);
        }
        const ctl = document.createElement("div");
        ctl.className = "eolia-sctl";
        const step = (direction, icon, disabled) => {
          const button = document.createElement("button");
          button.className = "eolia-step";
          button.setAttribute("aria-label", direction < 0 ? "Decrease" : "Increase");
          button.disabled = disabled;
          const glyph = document.createElement("ha-icon");
          glyph.setAttribute("icon", icon);
          button.appendChild(glyph);
          button.addEventListener("click", () => this._bump(s, direction));
          return button;
        };
        const val = document.createElement("span");
        val.className = "eolia-val";
        val.textContent = value.toFixed(decimals(s.step));
        if (s.unit) {
          const unit = document.createElement("small");
          unit.textContent = s.unit;
          val.appendChild(unit);
        }
        ctl.append(step(-1, "mdi:minus", value <= s.min), val, step(1, "mdi:plus", value >= s.max));
        box.appendChild(ctl);
        row.appendChild(box);
      }
      root.append(head, row);
      return [root];
    });
  }

  // --- 2. Mode picker ----------------------------------------------------------------------

  _renderPicker(ent, wrapper, controls) {
    const climate = this._hass.states[ent.climate];
    const presets = (climate && climate.attributes.preset_modes) || [];
    const current = climate && climate.attributes.preset_mode;
    const isOff = climate && climate.state === "off";
    if (!presets.length) {
      wrapper.hidden = true;
      return;
    }
    wrapper.hidden = false;
    const descriptions = this._operationModeAttrs(ent).mode_descriptions || {};
    const key = JSON.stringify([presets, current, isOff, this._pending, this._config.icons, descriptions]);
    this._rebuild("modes", wrapper, key, () => {
      const grid = document.createElement("div");
      grid.className = "eolia-modes";
      const add = (look, text, tip, active, onClick) => {
        const button = document.createElement("button");
        button.className = "eolia-mode";
        button.setAttribute("aria-pressed", String(active));
        button.style.setProperty("--accent", look.color);
        button.disabled = this._pending != null;
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
      for (const mode of presets) {
        add(
          MODE_STYLE[mode] || DEFAULT_MODE_STYLE,
          this._label(climate, "preset_mode", mode),
          descriptions[mode],
          !isOff && mode === current,
          () => this._selectMode(ent.climate, mode, current)
        );
      }
      add(OFF_STYLE, "Off", "Turn the unit off.", isOff, () => this._turnOff(ent.climate, isOff));
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

  // --- 3. Airflow ----------------------------------------------------------------------------

  _renderAirflow(ent, wrapper, controls) {
    const climate = this._hass.states[ent.climate];
    const allows = (id) => controls === null || controls.has(id);
    const rows = AIRFLOW_ROWS.filter(
      (r) => allows(r.gate) && climate && Array.isArray(climate.attributes[r.list]) && climate.attributes[r.list].length
    );
    if (!rows.length) {
      wrapper.hidden = true;
      return;
    }
    wrapper.hidden = false;
    const key = JSON.stringify(rows.map((r) => [r.attr, climate.attributes[r.attr], climate.attributes[r.list]]));
    this._rebuild("airflow", wrapper, key, () => {
      const root = document.createElement("div");
      root.className = "eolia-airflow";
      for (const r of rows) {
        const row = document.createElement("div");
        row.className = "eolia-row";
        const label = document.createElement("label");
        label.textContent = r.label;
        const select = document.createElement("select");
        select.setAttribute("aria-label", r.label);
        for (const value of climate.attributes[r.list]) {
          const option = document.createElement("option");
          option.value = String(value);
          option.textContent = this._label(climate, r.attr, value);
          select.appendChild(option);
        }
        select.value = String(climate.attributes[r.attr]);
        select.addEventListener("change", () =>
          this._call("climate", r.service, { entity_id: ent.climate, [r.attr]: select.value })
        );
        row.append(label, select);
        root.appendChild(row);
      }
      return [root];
    });
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
      { id: "airflow", render: (ent, wrapper, controls) => this._renderAirflow(ent, wrapper, controls) },
      {
        id: "settings",
        config: ({ ent, allows }) => {
          const rows = SETTINGS_ROWS.filter((k) => ent[k] && allows(k)).map((k) => ent[k]);
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

  async _buildSlots() {
    const helpers = await window.loadCardHelpers();
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
      const building = this._buildSlots();
      this._building = building;
      const slots = await building;
      if (this._building !== building) return; // superseded by a newer resolve
      this._slots = slots;
      const style = document.createElement("style");
      style.textContent = CSS;
      this._container.replaceChildren(style, ...slots.map((s) => s.wrapper));
    }
    if (!this._slots) return;

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
