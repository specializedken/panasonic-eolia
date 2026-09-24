// Run with: node --test tests/js/
// Exercises eolia-card.js against a minimal fake DOM + fake `hass`: entity discovery by
// device/translation_key, rendering from the sensor's `controls`/`mode_descriptions`, the setpoint
// steppers (incl. debounce), the labelled airflow rows, the mode picker and the fallbacks.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

class FakeEl {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.hidden = false; this.disabled = false;
    this.className = ""; this.textContent = ""; this.title = ""; this.value = "";
    this.attrs = {}; this.listeners = {};
    this.style = { setProperty: (k, v) => { this.style[k] = v; } };
  }
  appendChild(c) { this.children.push(c); c.parent = this; }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  replaceChildren(...cs) { this.children = []; cs.forEach((c) => this.appendChild(c)); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  replaceWith(n) { const p = this.parent; p.children = p.children.map((x) => (x === this ? n : x)); n.parent = p; }
  dispatchEvent(e) { (this.dispatched ||= []).push(e); }
  click() { return this.listeners.click[0](); }
}

function loadCard() {
  const cards = new Map();
  const created = [];
  const timers = new Map();
  let nextTimer = 0;
  const helpers = {
    createCardElement(cfg) {
      const el = new FakeEl("card");
      el.config = cfg;
      el.setConfig = (c) => { el.config = c; el.reconfigured = (el.reconfigured || 0) + 1; };
      created.push(el);
      return el;
    },
  };
  const ctx = {
    HTMLElement: FakeEl,
    document: { createElement: (t) => new FakeEl(t) },
    customElements: { get: (n) => cards.get(n), define: (n, c) => cards.set(n, c) },
    window: { loadCardHelpers: async () => helpers },
    console: { info() {} },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    setTimeout: (f) => { timers.set(++nextTimer, f); return nextTimer; },
    clearTimeout: (id) => { timers.delete(id); },
  };
  ctx.window.customCards = [];
  vm.runInNewContext(readFileSync("custom_components/eolia/www/eolia-card.js", "utf8"), ctx);
  const fireTimers = async () => {
    const fns = [...timers.values()];
    timers.clear();
    for (const f of fns) await f();
  };
  return { Card: cards.get("eolia-card"), created, timers, fireTimers };
}

const KEYS = {
  climate: "climate.aircon", operation_mode: "sensor.aircon_operation_mode",
  ai_mode: "select.aircon_ai", nanoex: "switch.aircon_nanoex", silence_control: "switch.aircon_quiet",
  air_flow: "select.aircon_flow", wind_shield_hit: "select.aircon_hit",
  dry_humidity_target: "number.aircon_dry", double_temp_enabled: "switch.aircon_dt",
  double_temp_low: "number.aircon_lo", double_temp_high: "number.aircon_hi",
  indoor_temperature: "sensor.aircon_in", indoor_humidity: "sensor.aircon_h", outdoor_temperature: "sensor.aircon_out",
};
const DESCRIPTIONS = { Auto: "Picks automatically.", Blast: "Fan only.", KeepMode: "Double temp." };

function makeHass(controls, {
  otherDevice = true, presets = ["Auto", "Cooling", "Blast", "KeepMode"], current = "Cooling",
  climateState = "cool", callService, temperature = 24, descriptions = DESCRIPTIONS,
} = {}) {
  const entities = {};
  for (const [tk, id] of Object.entries(KEYS)) {
    entities[id] = { entity_id: id, device_id: "dev1", platform: "eolia", translation_key: tk === "climate" ? "eolia" : tk };
  }
  if (otherDevice) {
    // Same translation_key on another device must NOT be picked up.
    entities["select.other_ai"] = { entity_id: "select.other_ai", device_id: "dev2", platform: "eolia", translation_key: "ai_mode" };
  }
  const attrs = controls === undefined ? {} : { controls, mode_descriptions: descriptions };
  const num = (state, min, max, step, unit) => ({ state, attributes: { min, max, step, unit_of_measurement: unit } });
  return {
    entities,
    config: { unit_system: { temperature: "°C" } },
    states: {
      [KEYS.operation_mode]: { state: "Cooling", attributes: attrs },
      [KEYS.climate]: {
        state: climateState,
        attributes: {
          preset_modes: presets, preset_mode: current,
          temperature, min_temp: 16, max_temp: 30, target_temp_step: 0.5,
          fan_modes: ["0", "1", "2"], fan_mode: "0",
          swing_modes: ["0", "1", "6"], swing_mode: "1",
          swing_horizontal_modes: ["auto", "wide"], swing_horizontal_mode: "auto",
        },
      },
      [KEYS.dry_humidity_target]: num("55", 50, 60, 5, "%"),
      [KEYS.double_temp_low]: num("22", 16, 25, 1, "°C"),
      [KEYS.double_temp_high]: num("27", 21, 30, 1, "°C"),
    },
    formatEntityAttributeValue: (_st, attr, v) => `${attr}:${v}`,
    callService: callService || (async () => {}),
  };
}

async function render(controls, opts, config = {}) {
  const env = loadCard();
  const card = new env.Card();
  card.setConfig({ entity: "climate.aircon", ...config });
  card.hass = makeHass(controls, opts);
  await card._building;
  await card._render();
  return { card, ...env };
}

// The card runs in a separate vm realm, so its arrays have a different prototype; compare plain copies.
const plain = (x) => JSON.parse(JSON.stringify(x));
const slot = (card, id) => card._slots.find((s) => s.def.id === id);
const settings = (card) => plain(slot(card, "settings").element.config.entities);
const view = (card, id) => card._views[id].card;

// setpoint DOM: ha-card > root > [head, steppers row] ; stepper box > [label?, ctl] ; ctl > [minus, val, plus]
const setpointRoot = (card) => view(card, "setpoint").children[0];
const heading = (card) => setpointRoot(card).children[0].textContent;
const steppers = (card) =>
  setpointRoot(card).children[1].children.map((box) => {
    const ctl = box.children[box.children.length - 1];
    const [minus, val, plus] = ctl.children;
    return { label: box.children.length > 1 ? box.children[0].textContent : "", minus, plus, valEl: val, text: val.textContent + val.children.map((c) => c.textContent).join("") };
  });

// picker DOM: ha-card > grid > buttons ; button > [ico span, label span]
const buttons = (card) => view(card, "modes").children[0].children;
const iconOf = (b) => b.children[0].children[0];
const labelOf = (b) => b.children[1].textContent;

// airflow DOM: ha-card > root > rows ; row > [label, select]
const airflowRows = (card) => view(card, "airflow").children[0].children;

const ALL_RUNNING = ["temperature", "fan", "louvers", "ai_mode", "air_flow", "wind_shield_hit", "nanoex", "silence_control", "double_temp_enabled"];

test("requires an entity", () => {
  const { Card } = loadCard();
  assert.throws(() => new Card().setConfig({}), /entity/);
});

test("unknown climate entity shows an error and no cards", async () => {
  const { Card } = loadCard();
  const card = new Card();
  card.setConfig({ entity: "climate.nope" });
  card.hass = makeHass([]);
  await card._render();
  assert.match(card._container.textContent, /not found/);
});

// --- Setpoint ------------------------------------------------------------------------------------
test("temperature stepper on top: heading, value, unit, no tile / hvac bar anywhere", async () => {
  const { card } = await render(ALL_RUNNING);
  assert.equal(heading(card), "Target temperature");
  const [s] = steppers(card);
  assert.equal(s.text, "24.0°C");
  assert.ok(!card._slots.some((x) => x.def.id === "tile"));
});

test("Dry shows a humidity stepper (5% steps) instead of temperature", async () => {
  const { card } = await render(["dry_humidity_target", "fan", "louvers"]);
  assert.equal(heading(card), "Humidity target");
  const [s] = steppers(card);
  assert.equal(s.text, "55%");
});

test("KeepMode shows low and high steppers", async () => {
  const { card } = await render(["double_temp_enabled", "double_temp_low", "double_temp_high"]);
  assert.equal(heading(card), "Keep the room between");
  const ss = steppers(card);
  assert.deepEqual(plain(ss.map((s) => [s.label, s.text])), [["Low", "22°C"], ["High", "27°C"]]);
});

test("no setpoint when nothing adjustable applies (off / clean modes)", async () => {
  const { card } = await render(["double_temp_enabled"]);
  assert.equal(slot(card, "setpoint").wrapper.hidden, true);
});

test("taps are debounced into ONE write and the shown value moves immediately", async () => {
  const calls = [];
  const { card, fireTimers, timers } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  const tap = () => steppers(card)[0].plus.click(); // rebuilt after each tap, so re-query every time
  tap(); tap(); tap();
  assert.equal(steppers(card)[0].text, "25.5°C"); // 24 + 3 x 0.5, before any write
  assert.equal(calls.length, 0);
  assert.equal(timers.size, 1); // earlier timers were cancelled
  await fireTimers();
  assert.deepEqual(plain(calls), [["climate", "set_temperature", { entity_id: KEYS.climate, temperature: 25.5 }]]);
});

test("humidity stepper writes the number entity and can't pass its limits", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["dry_humidity_target"], { callService: async (...a) => { calls.push(a); } });
  steppers(card)[0].plus.click(); // 55 -> 60
  assert.equal(steppers(card)[0].text, "60%");
  assert.equal(steppers(card)[0].plus.disabled, true); // at max
  await fireTimers();
  assert.deepEqual(plain(calls), [["number", "set_value", { entity_id: KEYS.dry_humidity_target, value: 60 }]]);
});

test("temperature is clamped to the unit's range", async () => {
  const { card } = await render(ALL_RUNNING, { temperature: 30 });
  assert.equal(steppers(card)[0].plus.disabled, true);
  assert.equal(steppers(card)[0].minus.disabled, false);
});

test("a refused setpoint write is shown to the user", async () => {
  const { card, fireTimers } = await render(ALL_RUNNING, { callService: async () => { throw new Error("nope"); } });
  steppers(card)[0].plus.click();
  await fireTimers();
  assert.equal(card.dispatched[0].detail.message, "nope");
});

// --- Airflow rows ----------------------------------------------------------------------------------
test("fan and louvers are labelled rows with readable options", async () => {
  const { card } = await render(ALL_RUNNING);
  const rows = airflowRows(card);
  assert.deepEqual(plain(rows.map((r) => r.children[0].textContent)), ["Fan speed", "Vertical louver", "Horizontal louver"]);
  const fan = rows[0].children[1];
  assert.deepEqual(plain(fan.children.map((o) => o.textContent)), ["fan_mode:0", "fan_mode:1", "fan_mode:2"]);
  assert.equal(fan.value, "0");
});

test("airflow rows follow the controls list", async () => {
  const onlyFan = await render(["fan"]);
  assert.deepEqual(plain(airflowRows(onlyFan.card).map((r) => r.children[0].textContent)), ["Fan speed"]);
  const onlyLouvers = await render(["louvers"]);
  assert.equal(airflowRows(onlyLouvers.card).length, 2);
  const none = await render(["temperature"]);
  assert.equal(slot(none.card, "airflow").wrapper.hidden, true);
});

test("changing a row calls the matching climate service", async () => {
  const calls = [];
  const { card } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  const [fan, vertical, horizontal] = airflowRows(card).map((r) => r.children[1]);
  fan.value = "2"; await fan.listeners.change[0]();
  vertical.value = "6"; await vertical.listeners.change[0]();
  horizontal.value = "wide"; await horizontal.listeners.change[0]();
  assert.deepEqual(plain(calls), [
    ["climate", "set_fan_mode", { entity_id: KEYS.climate, fan_mode: "2" }],
    ["climate", "set_swing_mode", { entity_id: KEYS.climate, swing_mode: "6" }],
    ["climate", "set_swing_horizontal_mode", { entity_id: KEYS.climate, swing_horizontal_mode: "wide" }],
  ]);
});

// --- Mode picker -------------------------------------------------------------------------------------
test("picker: one button per preset plus Off; active one pressed; labels from HA", async () => {
  const { card } = await render(ALL_RUNNING);
  const b = buttons(card);
  assert.deepEqual(plain(b.map(labelOf)), ["preset_mode:Auto", "preset_mode:Cooling", "preset_mode:Blast", "preset_mode:KeepMode", "Off"]);
  assert.deepEqual(plain(b.map((x) => x.attrs["aria-pressed"])), ["false", "true", "false", "false", "false"]);
});

test("picker buttons carry the integration's tooltips", async () => {
  const { card } = await render(ALL_RUNNING);
  const b = buttons(card);
  assert.equal(b[0].title, "Picks automatically.");
  assert.equal(b[2].title, "Fan only.");
  assert.equal(b[1].title, ""); // no description supplied for Cooling in this fixture
  assert.equal(b[4].title, "Turn the unit off.");
});

test("no tooltips (and no crash) when the sensor lacks mode_descriptions", async () => {
  const { card } = await render(undefined);
  assert.ok(buttons(card).slice(0, 4).every((b) => b.title === ""));
});

test("Off is pressed (and no mode is) while the unit is off; clicking it calls turn_off", async () => {
  const off = await render(ALL_RUNNING, { climateState: "off" });
  assert.deepEqual(plain(buttons(off.card).map((x) => x.attrs["aria-pressed"])), ["false", "false", "false", "false", "true"]);
  const calls = [];
  const on = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  await buttons(on.card)[4].click();
  assert.deepEqual(plain(calls), [["climate", "turn_off", { entity_id: KEYS.climate }]]);
});

test("clicking Off while already off is a no-op", async () => {
  const calls = [];
  const { card } = await render(ALL_RUNNING, { climateState: "off", callService: async (...a) => { calls.push(a); } });
  await buttons(card)[4].click();
  assert.equal(calls.length, 0);
});

test("modes with an app icon use it from the icons dir; others fall back to mdi", async () => {
  const { card } = await render(ALL_RUNNING);
  const [auto, cooling, , , off] = buttons(card).map(iconOf);
  assert.equal(auto.tag, "img");
  assert.equal(auto.src, "/local/eolia-icons/modes/v6_drive_mode_automatic.png");
  assert.equal(cooling.attrs.icon, "mdi:snowflake"); // the app has no Cooling icon
  assert.equal(off.attrs.icon, "mdi:power");
});

test("a missing icon file falls back to the mdi icon", async () => {
  const { card } = await render(ALL_RUNNING);
  const button = buttons(card)[0];
  iconOf(button).onerror();
  assert.equal(iconOf(button).attrs.icon, "mdi:autorenew");
});

test("icons: false and a custom icons path are honoured", async () => {
  const off = await render(ALL_RUNNING, undefined, { icons: false });
  assert.ok(buttons(off.card).every((b) => iconOf(b).tag === "ha-icon"));
  const custom = await render(ALL_RUNNING, undefined, { icons: "/local/x" });
  assert.equal(iconOf(buttons(custom.card)[0]).src, "/local/x/modes/v6_drive_mode_automatic.png");
});

test("clicking a mode calls climate.set_preset_mode; the current mode is a no-op", async () => {
  const calls = [];
  const { card } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  await buttons(card)[1].click(); // current: Cooling
  assert.equal(calls.length, 0);
  await buttons(card)[2].click(); // Blast
  assert.deepEqual(plain(calls), [["climate", "set_preset_mode", { entity_id: KEYS.climate, preset_mode: "Blast" }]]);
});

test("buttons are disabled while a write is pending, and re-enabled after", async () => {
  let release;
  const { card } = await render(ALL_RUNNING, { callService: () => new Promise((r) => { release = r; }) });
  const click = buttons(card)[2].click();
  assert.ok(buttons(card).every((b) => b.disabled));
  release();
  await click;
  assert.ok(buttons(card).every((b) => !b.disabled));
});

test("a refused mode change is shown to the user instead of swallowed", async () => {
  const { card } = await render(ALL_RUNNING, { callService: async () => { throw new Error("KeepMode can't do that"); } });
  await buttons(card)[3].click();
  const [event] = card.dispatched;
  assert.equal(event.type, "hass-notification");
  assert.equal(event.detail.message, "KeepMode can't do that");
});

test("the picker isn't rebuilt on unrelated state updates", async () => {
  const { card } = await render(ALL_RUNNING);
  const first = view(card, "modes").children[0];
  card.hass = makeHass(ALL_RUNNING);
  await card._render();
  assert.equal(view(card, "modes").children[0], first);
});

test("no presets means no picker", async () => {
  const { card } = await render(ALL_RUNNING, { presets: [] });
  assert.equal(slot(card, "modes").wrapper.hidden, true);
});

// --- Stock cards: settings + room ------------------------------------------------------------------------
test("settings card lists operation_mode first, then only applicable rows from THIS device", async () => {
  const { card } = await render(["ai_mode", "nanoex", "double_temp_enabled"]);
  assert.deepEqual(settings(card), [KEYS.operation_mode, KEYS.ai_mode, KEYS.nanoex, KEYS.double_temp_enabled]);
});

test("the humidity and double-temp sliders are steppers, not settings rows", async () => {
  const { card } = await render(["dry_humidity_target", "double_temp_low", "double_temp_high", "double_temp_enabled"]);
  assert.deepEqual(settings(card), [KEYS.operation_mode, KEYS.double_temp_enabled]);
});

test("without the controls attribute everything is shown, not nothing", async () => {
  const { card } = await render(undefined);
  assert.equal(heading(card), "Target temperature");
  assert.equal(airflowRows(card).length, 3);
  // operation_mode + every settings row that exists on the device (the fake has no
  // air_quality_monitor, like a model without air-quality support, so it is skipped)
  assert.equal(settings(card).length, 1 + 6);
});

test("a change in controls reconfigures the settings card instead of recreating it", async () => {
  const { card, created } = await render(["ai_mode", "nanoex"]);
  const before = created.length;
  card.hass = makeHass(["ai_mode"]);
  await card._render();
  assert.equal(created.length, before);
  assert.equal(slot(card, "settings").element.reconfigured, 1);
  assert.deepEqual(settings(card), [KEYS.operation_mode, KEYS.ai_mode]);
});

test("room glance lists only sensors that exist", async () => {
  const { card } = await render([]);
  assert.deepEqual(plain(slot(card, "room").element.config.entities), [
    KEYS.indoor_temperature, KEYS.indoor_humidity, KEYS.outdoor_temperature,
  ]);
});
