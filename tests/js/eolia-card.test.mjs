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
  const images = []; // every Image() the card created to probe an icon file
  class FakeImage {
    set src(value) { this._src = value; images.push(this); }
    get src() { return this._src; }
  }
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
    Image: FakeImage,
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
  return { Card: cards.get("eolia-card"), created, timers, fireTimers, images };
}

const KEYS = {
  climate: "climate.aircon", operation_mode: "sensor.aircon_operation_mode",
  fan_speed: "select.aircon_fan", vertical_louver: "select.aircon_vlouver", horizontal_louver: "select.aircon_hlouver",
  ai_mode: "select.aircon_ai", nanoex: "switch.aircon_nanoex", silence_control: "switch.aircon_quiet",
  air_flow: "select.aircon_flow", wind_shield_hit: "select.aircon_hit",
  dry_humidity_target: "number.aircon_dry", double_temp_enabled: "switch.aircon_dt",
  double_temp_low: "number.aircon_lo", double_temp_high: "number.aircon_hi",
  indoor_temperature: "sensor.aircon_in", indoor_humidity: "sensor.aircon_h", outdoor_temperature: "sensor.aircon_out",
};
const DESCRIPTIONS = { Auto: "Picks automatically.", Blast: "Fan only.", KeepMode: "Double temp." };

function makeHass(controls, {
  otherDevice = true, presets = ["Auto", "Cooling", "Blast", "KeepMode"], current = "Cooling",
  climateState = "cool", callService, temperature = 24, descriptions = DESCRIPTIONS, humidityState = "55", minGap = 5,
} = {}) {
  const entities = {};
  for (const [tk, id] of Object.entries(KEYS)) {
    entities[id] = { entity_id: id, device_id: "dev1", platform: "eolia", translation_key: tk === "climate" ? "eolia" : tk };
  }
  if (otherDevice) {
    // Same translation_key on another device must NOT be picked up.
    entities["select.other_ai"] = { entity_id: "select.other_ai", device_id: "dev2", platform: "eolia", translation_key: "ai_mode" };
  }
  const attrs = controls === undefined ? {} : { controls, mode_descriptions: descriptions, ...(minGap === null ? {} : { double_temp_min_gap: minGap }) };
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
      [KEYS.indoor_temperature]: { state: "23.5", attributes: {} },
      [KEYS.indoor_humidity]: { state: "48", attributes: {} },
      [KEYS.dry_humidity_target]: num(humidityState, 50, 60, 5, "%"),
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

// setpoint DOM: ha-card > root > [dial, mini?] ; dial > [slider, info, buttons?] ;
// info > [head, val > [text, unit], sub] ; buttons > [minus, plus]
const setpointRoot = (card) => view(card, "setpoint").children[0];
const dial = (card) => setpointRoot(card).children[0];
const slider = (card) => dial(card).children[0];
const info = (card) => dial(card).children[1];
const heading = (card) => info(card).children[0].textContent;
const valueText = (card) => info(card).children[1].children.map((c) => c.textContent).join("");
const subText = (card) => info(card).children[2].textContent;
const minus = (card) => dial(card).children[2].children[0];
const plus = (card) => dial(card).children[2].children[1];
// dual only: the small Low/High steppers under the dial
const minis = (card) =>
  setpointRoot(card).children[1].children.map((box) => {
    const [label, ctl] = box.children;
    const [minusBtn, val, plusBtn] = ctl.children;
    return { label: label.textContent, text: val.textContent, minus: minusBtn, plus: plusBtn };
  });

// picker DOM: ha-card > grid > buttons ; button > [ico span, label span]
const buttons = (card) => view(card, "modes").children[0].children;
const iconOf = (b) => b.children[0].children[0];
const labelOf = (b) => b.children[1].textContent;

const ALL_RUNNING = [
  "temperature", "fan_speed", "vertical_louver", "horizontal_louver", "ai_mode", "air_flow",
  "wind_shield_hit", "nanoex", "silence_control",
];

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

// --- Setpoint dial -----------------------------------------------------------------------------------
test("temperature: HA's round dial with the unit's range, value, current reading and mode colour", async () => {
  const { card } = await render(ALL_RUNNING);
  assert.equal(slider(card).tag, "ha-control-circular-slider");
  assert.deepEqual(plain([slider(card).min, slider(card).max, slider(card).step, slider(card).value, slider(card).current]), [16, 30, 0.5, 24, 23.5]);
  assert.equal(slider(card).mode, "start");
  assert.equal(heading(card), "Target temperature");
  assert.equal(valueText(card), "24.0°C");
  assert.equal(subText(card), "Currently 23.5°C");
  assert.equal(dial(card).style["--eolia-accent"], "#65accc"); // Cooling
});

test("no tile and no HVAC bar anywhere", async () => {
  const { card, created } = await render(ALL_RUNNING);
  assert.ok(!card._slots.some((x) => x.def.id === "tile"));
  assert.ok(!created.some((el) => JSON.stringify(el.config).includes("climate-hvac-modes")));
});

test("the stock thermostat card is requested (never attached) so HA defines the dial", async () => {
  const { created } = await render(ALL_RUNNING);
  assert.ok(created.some((el) => el.config.type === "thermostat" && el.config.entity === KEYS.climate));
});

test("Dry: the same dial adjusts the humidity target (5% steps) and shows room humidity", async () => {
  const { card } = await render(["dry_humidity_target", "fan_speed"]);
  assert.equal(heading(card), "Humidity target");
  assert.deepEqual(plain([slider(card).min, slider(card).max, slider(card).step, slider(card).value, slider(card).current]), [50, 60, 5, 55, 48]);
  assert.equal(valueText(card), "55%");
  assert.equal(subText(card), "Currently 48%");
});

test("KeepMode: a two-thumb dial for low/high, plus a small stepper for each bound", async () => {
  const { card } = await render(["double_temp_low", "double_temp_high"]);
  assert.equal(heading(card), "Keep the room between");
  assert.equal(slider(card).dual, true);
  assert.deepEqual(plain([slider(card).low, slider(card).high, slider(card).min, slider(card).max]), [22, 27, 16, 30]);
  assert.equal(valueText(card), "22 – 27°C");
  assert.deepEqual(plain(minis(card).map((m) => [m.label, m.text])), [["Low", "22°C"], ["High", "27°C"]]);
});

test("a mode with no target keeps the dial on screen, greyed out", async () => {
  const { card } = await render([]); // e.g. odor care: nothing adjustable
  assert.equal(slot(card, "setpoint").wrapper.hidden, false);
  assert.equal(slider(card).disabled, true);
  assert.equal(heading(card), "No target in this mode");
  assert.equal(valueText(card), "–");
  assert.equal(subText(card), "Currently 23.5°C"); // the room reading is still useful
  assert.equal(minus(card).disabled, true);
  assert.equal(plus(card).disabled, true);
});

test("an unavailable unit reads 'Unavailable' in place of the temperature, like HA's own card", async () => {
  const { card } = await render(undefined, { climateState: "unavailable" });
  assert.equal(slider(card).disabled, true);
  assert.equal(heading(card), "");
  assert.equal(valueText(card), "Unavailable");
  assert.equal(subText(card), "");
  assert.equal(minus(card).disabled, true);
  assert.equal(plus(card).disabled, true);
  assert.ok(buttons(card).every((b) => b.disabled)); // the mode picker can't act either
});

test("'Unavailable' uses HA's localized string when there is one", async () => {
  const env = loadCard();
  const card = new env.Card();
  card.setConfig({ entity: "climate.aircon" });
  const hass = makeHass(undefined, { climateState: "unavailable" });
  hass.localize = (key) => (key === "state.default.unavailable" ? "Indisponible" : "");
  card.hass = hass;
  await card._building;
  await card._render();
  assert.equal(valueText(card), "Indisponible");
});

test("the dial comes back to normal when the unit does", async () => {
  const { card } = await render(undefined, { climateState: "unavailable" });
  card.hass = makeHass(undefined, { climateState: "cool" });
  await card._render();
  assert.equal(heading(card), "Target temperature");
  assert.equal(valueText(card), "24.0°C");
  assert.equal(slider(card).disabled, false);
  assert.ok(buttons(card).every((b) => !b.disabled));
});

test("off reads 'Off' on the greyed dial", async () => {
  const { card } = await render([], { climateState: "off" });
  assert.equal(heading(card), "Off");
  assert.equal(slider(card).disabled, true);
});

test("switching between 'has a target' and 'has none' reuses the same dial (no layout change)", async () => {
  const { card } = await render(ALL_RUNNING);
  const first = slider(card);
  assert.equal(first.disabled, false);
  card.hass = makeHass([]);
  await card._render();
  assert.equal(slider(card), first);
  assert.equal(first.disabled, true);
  card.hass = makeHass(ALL_RUNNING);
  await card._render();
  assert.equal(slider(card), first);
  assert.equal(first.disabled, false);
  assert.equal(valueText(card), "24.0°C");
});

test("the setpoint block has the same structure in every state, so nothing below it moves", async () => {
  const shapes = [];
  for (const controls of [ALL_RUNNING, ["dry_humidity_target"], [], ["double_temp_low", "double_temp_high"]]) {
    const { card } = await render(controls);
    const root = setpointRoot(card);
    shapes.push(root.children.map((c) => c.className));
    assert.equal(slot(card, "setpoint").wrapper.hidden, false);
  }
  // dial + a block reserving the Low/High row's height (the spacer, or the row itself in KeepMode)
  assert.deepEqual(plain(shapes.map((x) => x.length)), [2, 2, 2, 2]);
  assert.deepEqual(plain(shapes.map((x) => x[0])), Array(4).fill("eolia-dial"));
  assert.deepEqual(plain(shapes.map((x) => x[1])), ["eolia-mini-spacer", "eolia-mini-spacer", "eolia-mini-spacer", "eolia-mini"]);
});

test("Dry with an unavailable humidity number falls back to the greyed dial, not a hole", async () => {
  const { card } = await render(["dry_humidity_target"], { humidityState: "unavailable" });
  assert.equal(slider(card).disabled, true);
  assert.equal(heading(card), "No target in this mode");
});

test("dragging shows the value live but writes nothing until released", async () => {
  const calls = [];
  const { card, fireTimers } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  slider(card).listeners["value-changing"][0]({ detail: { value: 26 } });
  assert.equal(valueText(card), "26.0°C");
  assert.equal(calls.length, 0);
  slider(card).listeners["value-changed"][0]({ detail: { value: 26 } });
  await fireTimers();
  assert.deepEqual(plain(calls), [["climate", "set_temperature", { entity_id: KEYS.climate, temperature: 26 }]]);
});

test("dial events with no usable value are ignored (no crash, no bogus write)", async () => {
  // Seen live: value-changing / value-changed arriving with an undefined value threw
  // "Cannot read properties of undefined (reading 'toFixed')" in the browser.
  const calls = [];
  const { card, fireTimers, timers } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  for (const detail of [{ value: undefined }, {}, { value: NaN }, { value: null }]) {
    slider(card).listeners["value-changing"][0]({ detail });
    slider(card).listeners["value-changed"][0]({ detail });
  }
  assert.equal(card._dragging, false);
  assert.equal(valueText(card), "24.0°C"); // unchanged
  assert.equal(timers.size, 0);
  await fireTimers();
  assert.equal(calls.length, 0);
});

test("dual dial: events with no usable value are ignored too", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  slider(card).listeners["low-changing"][0]({ detail: { value: undefined } });
  slider(card).listeners["high-changed"][0]({ detail: {} });
  await fireTimers();
  assert.equal(valueText(card), "22 – 27°C");
  assert.equal(calls.length, 0);
});

test("a poll landing mid-drag doesn't move the thumb", async () => {
  const { card } = await render(ALL_RUNNING);
  slider(card).listeners["value-changing"][0]({ detail: { value: 27 } });
  slider(card).value = 27; // where the user's finger is
  card.hass = makeHass(ALL_RUNNING, { temperature: 20 }); // a state update arrives
  await card._render();
  assert.equal(slider(card).value, 27);
  assert.equal(valueText(card), "27.0°C");
});

test("the dial isn't rebuilt on unrelated state updates", async () => {
  const { card } = await render(ALL_RUNNING);
  const first = slider(card);
  card.hass = makeHass(ALL_RUNNING);
  await card._render();
  assert.equal(slider(card), first);
});

test("a state change updates the same dial in place", async () => {
  const { card } = await render(ALL_RUNNING);
  const first = slider(card);
  card.hass = makeHass(ALL_RUNNING, { temperature: 21.5 });
  await card._render();
  assert.equal(slider(card), first);
  assert.equal(first.value, 21.5);
  assert.equal(valueText(card), "21.5°C");
});

test("-/+ taps are debounced into ONE write and the shown value moves immediately", async () => {
  const calls = [];
  const { card, fireTimers, timers } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  plus(card).click(); plus(card).click(); plus(card).click();
  assert.equal(valueText(card), "25.5°C"); // 24 + 3 x 0.5, before any write
  assert.equal(slider(card).value, 25.5);
  assert.equal(calls.length, 0);
  assert.equal(timers.size, 1); // earlier timers were cancelled
  await fireTimers();
  assert.deepEqual(plain(calls), [["climate", "set_temperature", { entity_id: KEYS.climate, temperature: 25.5 }]]);
});

test("humidity writes the number entity and can't pass its limits", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["dry_humidity_target"], { callService: async (...a) => { calls.push(a); } });
  plus(card).click(); // 55 -> 60
  assert.equal(valueText(card), "60%");
  assert.equal(plus(card).disabled, true); // at max
  await fireTimers();
  assert.deepEqual(plain(calls), [["number", "set_value", { entity_id: KEYS.dry_humidity_target, value: 60 }]]);
});

test("a value dragged past a bound is clamped to the entity's own range", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  slider(card).listeners["low-changed"][0]({ detail: { value: 29 } }); // low's own max is 25
  await fireTimers();
  assert.deepEqual(plain(calls), [["number", "set_value", { entity_id: KEYS.double_temp_low, value: 25 }]]);
});

test("KeepMode: each thumb writes its own number entity", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  slider(card).listeners["low-changed"][0]({ detail: { value: 20 } });
  slider(card).listeners["high-changed"][0]({ detail: { value: 28 } });
  await fireTimers();
  assert.deepEqual(plain(calls), [
    ["number", "set_value", { entity_id: KEYS.double_temp_low, value: 20 }],
    ["number", "set_value", { entity_id: KEYS.double_temp_high, value: 28 }],
  ]);
});

test("KeepMode: the small steppers nudge one bound", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  minis(card)[1].minus.click(); // high 27 -> 26 (step 1)
  assert.equal(minis(card)[1].text, "26°C");
  await fireTimers();
  assert.deepEqual(plain(calls), [["number", "set_value", { entity_id: KEYS.double_temp_high, value: 26 }]]);
});

test("temperature -/+ are disabled at the ends of the range", async () => {
  const { card } = await render(ALL_RUNNING, { temperature: 30 });
  assert.equal(plus(card).disabled, true);
  assert.equal(minus(card).disabled, false);
});

test("a refused setpoint write is shown to the user", async () => {
  const { card, fireTimers } = await render(ALL_RUNNING, { callService: async () => { throw new Error("nope"); } });
  plus(card).click();
  await fireTimers();
  assert.equal(card.dispatched[0].detail.message, "nope");
});

// --- Double temperature: the low/high gap is enforced instantly -----------------------------------------
const lowHigh = (card) => plain([slider(card).low, slider(card).high]);

test("there is no double-temperature on/off switch on the card", async () => {
  const { card } = await render(["double_temp_low", "double_temp_high"]);
  assert.ok(!settings(card).includes(KEYS.double_temp_enabled));
  const all = await render(undefined); // even with everything shown
  assert.ok(!settings(all.card).includes(KEYS.double_temp_enabled));
});

test("moving low into the gap pushes high at once, and only the moved bound is written", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  slider(card).listeners["low-changed"][0]({ detail: { value: 24 } }); // high is 27; 27-24 < 5
  assert.deepEqual(lowHigh(card), [24, 29]); // shown immediately, before any status update
  assert.equal(valueText(card), "24 – 29°C");
  assert.equal(calls.length, 0);
  await fireTimers();
  assert.deepEqual(plain(calls), [["number", "set_value", { entity_id: KEYS.double_temp_low, value: 24 }]]);
});

test("moving high into the gap pushes low", async () => {
  const { card } = await render(["double_temp_low", "double_temp_high"]);
  slider(card).listeners["high-changed"][0]({ detail: { value: 24 } }); // low is 22
  assert.deepEqual(lowHigh(card), [19, 24]);
});

test("a bound that can't be pushed any further is held back instead", async () => {
  const { card } = await render(["double_temp_low", "double_temp_high"]);
  // high can't go below 21 (its own limit) so low is pushed to 16 (its limit), gap 5
  slider(card).listeners["high-changed"][0]({ detail: { value: 18 } });
  assert.deepEqual(lowHigh(card), [16, 21]);
});

test("the pushed bound is shown live while dragging, with no write", async () => {
  const calls = [];
  const { card } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  slider(card).listeners["low-changing"][0]({ detail: { value: 25 } });
  assert.equal(slider(card).high, 30);
  assert.equal(valueText(card), "25 – 30°C");
  assert.equal(calls.length, 0);
});

test("the small -/+ steppers respect the gap too", async () => {
  const calls = [];
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"], { callService: async (...a) => { calls.push(a); } });
  minis(card)[0].plus.click(); // low 22 -> 23, so high 27 -> 28
  assert.equal(minis(card)[0].text, "23°C");
  assert.equal(minis(card)[1].text, "28°C");
  await fireTimers();
  assert.deepEqual(plain(calls), [["number", "set_value", { entity_id: KEYS.double_temp_low, value: 23 }]]);
});

test("a stepper tap that stays outside the gap doesn't touch the other bound", async () => {
  const { card } = await render(["double_temp_low", "double_temp_high"]);
  minis(card)[0].minus.click(); // low 22 -> 21, gap grows
  assert.deepEqual(lowHigh(card), [21, 27]);
});

test("the locally pushed value is dropped once the write finishes (the state is the truth)", async () => {
  const { card, fireTimers } = await render(["double_temp_low", "double_temp_high"]);
  slider(card).listeners["low-changed"][0]({ detail: { value: 24 } });
  await fireTimers();
  assert.deepEqual(plain(card._edits), {});
});

test("with no gap supplied by the integration the card enforces nothing (the server still nudges)", async () => {
  const { card } = await render(["double_temp_low", "double_temp_high"], { minGap: null });
  slider(card).listeners["low-changed"][0]({ detail: { value: 24 } });
  assert.deepEqual(lowHigh(card), [24, 27]);
});

// --- Mode picker -------------------------------------------------------------------------------------
// Look buttons up by label, so these tests don't depend on where a mode sits in the grid.
const btn = (card, label) => buttons(card).find((b) => labelOf(b) === label);
const mode = (name) => `preset_mode:${name}`;

test("picker: Off first, then one button per preset in the order given; active one pressed", async () => {
  const { card } = await render(ALL_RUNNING);
  const b = buttons(card);
  assert.deepEqual(plain(b.map(labelOf)), ["Off", mode("Auto"), mode("Cooling"), mode("Blast"), mode("KeepMode")]);
  assert.deepEqual(plain(b.map((x) => x.attrs["aria-pressed"])), ["false", "false", "true", "false", "false"]);
});

test("the card keeps no ordering of its own: it shows the integration's order (Off aside)", async () => {
  const requested = ["Auto", "ComfortableDehumidification", "Cooling", "CoolDehumidifying", "MoistCooling",
    "Heating", "KeepMode", "ClothesDryer", "SmellCare", "NanoexCleaning", "Cleaning"];
  const { card } = await render(ALL_RUNNING, { presets: requested });
  assert.deepEqual(plain(buttons(card).map(labelOf)), ["Off", ...requested.map(mode)]);
  const reversed = [...requested].reverse();
  const flipped = await render(ALL_RUNNING, { presets: reversed });
  assert.deepEqual(plain(buttons(flipped.card).map(labelOf)), ["Off", ...reversed.map(mode)]);
});

test("picker buttons carry the integration's tooltips", async () => {
  const { card } = await render(ALL_RUNNING);
  assert.equal(btn(card, mode("Auto")).title, "Picks automatically.");
  assert.equal(btn(card, mode("Blast")).title, "Fan only.");
  assert.equal(btn(card, mode("Cooling")).title, ""); // no description supplied for Cooling in this fixture
  assert.equal(btn(card, "Off").title, "Turn the unit off.");
});

test("no tooltips (and no crash) when the sensor lacks mode_descriptions", async () => {
  const { card } = await render(undefined);
  assert.ok(buttons(card).filter((b) => labelOf(b) !== "Off").every((b) => b.title === ""));
});

test("Off is pressed (and no mode is) while the unit is off; clicking it calls turn_off", async () => {
  const off = await render(ALL_RUNNING, { climateState: "off" });
  assert.deepEqual(plain(buttons(off.card).map((x) => x.attrs["aria-pressed"])), ["true", "false", "false", "false", "false"]);
  const calls = [];
  const on = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  await btn(on.card, "Off").click();
  assert.deepEqual(plain(calls), [["climate", "turn_off", { entity_id: KEYS.climate }]]);
});

test("clicking Off while already off is a no-op", async () => {
  const calls = [];
  const { card } = await render(ALL_RUNNING, { climateState: "off", callService: async (...a) => { calls.push(a); } });
  await btn(card, "Off").click();
  assert.equal(calls.length, 0);
});

test("modes with an app icon use it from the icons dir; others fall back to mdi", async () => {
  const { card } = await render(ALL_RUNNING);
  const auto = iconOf(btn(card, mode("Auto")));
  assert.equal(auto.tag, "img");
  assert.equal(auto.src, "/eolia_static/icons/modes/v6_drive_mode_automatic.png");
  assert.equal(iconOf(btn(card, mode("Cooling"))).attrs.icon, "mdi:snowflake"); // the app has no Cooling icon
  assert.equal(iconOf(btn(card, "Off")).attrs.icon, "mdi:power");
});

test("a missing icon file falls back to the mdi icon", async () => {
  const { card } = await render(ALL_RUNNING);
  const button = btn(card, mode("Auto"));
  iconOf(button).onerror();
  assert.equal(iconOf(button).attrs.icon, "mdi:autorenew");
});

test("icons: false and a custom icons path are honoured", async () => {
  const off = await render(ALL_RUNNING, undefined, { icons: false });
  assert.ok(buttons(off.card).every((b) => iconOf(b).tag === "ha-icon"));
  const custom = await render(ALL_RUNNING, undefined, { icons: "/local/x" });
  assert.equal(iconOf(btn(custom.card, mode("Auto"))).src, "/local/x/modes/v6_drive_mode_automatic.png");
});

test("clicking a mode calls climate.set_preset_mode; the current mode is a no-op", async () => {
  const calls = [];
  const { card } = await render(ALL_RUNNING, { callService: async (...a) => { calls.push(a); } });
  await btn(card, mode("Cooling")).click(); // current
  assert.equal(calls.length, 0);
  await btn(card, mode("Blast")).click();
  assert.deepEqual(plain(calls), [["climate", "set_preset_mode", { entity_id: KEYS.climate, preset_mode: "Blast" }]]);
});

test("buttons are disabled while a write is pending, and re-enabled after", async () => {
  let release;
  const { card } = await render(ALL_RUNNING, { callService: () => new Promise((r) => { release = r; }) });
  const click = btn(card, mode("Blast")).click();
  assert.ok(buttons(card).every((b) => b.disabled));
  release();
  await click;
  assert.ok(buttons(card).every((b) => !b.disabled));
});

test("a refused mode change is shown to the user instead of swallowed", async () => {
  const { card } = await render(ALL_RUNNING, { callService: async () => { throw new Error("KeepMode can't do that"); } });
  await btn(card, mode("KeepMode")).click();
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
test("fan speed and both louvers are ordinary rows in the stock settings card, labelled by HA", async () => {
  const { card } = await render(ALL_RUNNING);
  assert.deepEqual(settings(card), [
    KEYS.operation_mode, KEYS.fan_speed, KEYS.vertical_louver, KEYS.horizontal_louver,
    KEYS.ai_mode, KEYS.air_flow, KEYS.wind_shield_hit, KEYS.nanoex, KEYS.silence_control,
  ]);
  assert.ok(!card._slots.some((x) => x.def.id === "airflow")); // no custom dropdowns any more
});

test("settings rows follow the controls list and come from THIS device only", async () => {
  const { card } = await render(["ai_mode", "nanoex"]);
  assert.deepEqual(settings(card), [KEYS.operation_mode, KEYS.ai_mode, KEYS.nanoex]);
});

test("shield/hit-style state hides fan and louver rows", async () => {
  const { card } = await render(["ai_mode", "air_flow", "wind_shield_hit"]);
  assert.deepEqual(settings(card), [KEYS.operation_mode, KEYS.ai_mode, KEYS.air_flow, KEYS.wind_shield_hit]);
});

test("the humidity and double-temp values are on the dial, not settings rows", async () => {
  const { card } = await render(["dry_humidity_target", "double_temp_low", "double_temp_high"]);
  assert.deepEqual(settings(card), [KEYS.operation_mode]);
});

test("without the controls attribute everything is shown, not nothing", async () => {
  const { card } = await render(undefined);
  assert.equal(heading(card), "Target temperature");
  // operation_mode + every settings row that exists on the device (the fake has no
  // air_quality_monitor, like a model without air-quality support, so it is skipped)
  assert.equal(settings(card).length, 1 + 8);
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

// --- Row icons (Panasonic artwork, optional) ----------------------------------------------------------
const settingsRows = (card) => plain(slot(card, "settings").element.config.entities);
const rowFor = (card, entity) => settingsRows(card).find((r) => (typeof r === "string" ? r : r.entity) === entity);

test("row icons are probed by loading them, one per icon-bearing row", async () => {
  const { images } = await render(ALL_RUNNING);
  assert.deepEqual(plain(images.map((i) => i.src).sort()), [
    "/eolia_static/icons/rows/horizontal_louver.png",
    "/eolia_static/icons/rows/nanoex.png",
    "/eolia_static/icons/rows/vertical_louver.png",
    "/eolia_static/icons/rows/wind_shield_hit.png",
  ]);
});

test("a row gets its app icon only once the file has actually loaded", async () => {
  const { card, images } = await render(ALL_RUNNING);
  assert.equal(rowFor(card, KEYS.nanoex), KEYS.nanoex); // plain entity id until it loads
  images.find((i) => i.src.endsWith("/nanoex.png")).onload();
  await card._render();
  assert.deepEqual(rowFor(card, KEYS.nanoex), { entity: KEYS.nanoex, image: "/eolia_static/icons/rows/nanoex.png" });
  assert.equal(rowFor(card, KEYS.vertical_louver), KEYS.vertical_louver); // its own file hasn't loaded
});

test("all four icon rows can show their icon; row order is unchanged", async () => {
  const { card, images } = await render(ALL_RUNNING);
  const before = settingsRows(card).map((r) => (typeof r === "string" ? r : r.entity));
  images.forEach((i) => i.onload());
  await card._render();
  const after = settingsRows(card);
  assert.deepEqual(after.map((r) => (typeof r === "string" ? r : r.entity)), before);
  const withImage = after.filter((r) => typeof r !== "string").map((r) => r.entity).sort();
  assert.deepEqual(withImage, [KEYS.horizontal_louver, KEYS.nanoex, KEYS.vertical_louver, KEYS.wind_shield_hit].sort());
});

test("a missing icon file leaves the row with Home Assistant's own icon", async () => {
  const { card, images } = await render(ALL_RUNNING);
  images.forEach((i) => i.onerror());
  await card._render();
  assert.ok(settingsRows(card).every((r) => typeof r === "string"));
});

test("rows without an app icon never get an image, even if everything loaded", async () => {
  const { card, images } = await render(ALL_RUNNING);
  images.forEach((i) => i.onload());
  await card._render();
  assert.equal(rowFor(card, KEYS.ai_mode), KEYS.ai_mode);
  assert.equal(rowFor(card, KEYS.air_flow), KEYS.air_flow);
});

test("a row the current mode hides stays hidden even with its icon loaded", async () => {
  const { card, images } = await render(["ai_mode", "nanoex"]); // no louver rows
  images.forEach((i) => i.onload());
  await card._render();
  assert.equal(rowFor(card, KEYS.vertical_louver), undefined);
  assert.deepEqual(rowFor(card, KEYS.nanoex), { entity: KEYS.nanoex, image: "/eolia_static/icons/rows/nanoex.png" });
});

test("icons: false probes nothing; a custom icons path is used", async () => {
  const off = await render(ALL_RUNNING, undefined, { icons: false });
  assert.equal(off.images.length, 0);
  const custom = await render(ALL_RUNNING, undefined, { icons: "/local/x" });
  assert.ok(custom.images.every((i) => i.src.startsWith("/local/x/rows/")));
});

test("the probe runs once, not on every state update", async () => {
  const { card, images } = await render(ALL_RUNNING);
  const n = images.length;
  card.hass = makeHass(ALL_RUNNING);
  await card._render();
  assert.equal(images.length, n);
});

test("a leftover 'restored' entity never becomes a dead row", async () => {
  // e.g. the air-quality switch registered by an older version, on a model that lacks the feature
  const env = loadCard();
  const card = new env.Card();
  card.setConfig({ entity: "climate.aircon" });
  const hass = makeHass(undefined);
  hass.entities["switch.aircon_airq"] = {
    entity_id: "switch.aircon_airq", device_id: "dev1", platform: "eolia", translation_key: "air_quality_monitor",
  };
  hass.states["switch.aircon_airq"] = { state: "unavailable", attributes: { restored: true } };
  card.hass = hass;
  await card._building;
  await card._render();
  assert.ok(!settings(card).includes("switch.aircon_airq"));
  // ...whereas the same entity provided normally (no `restored` flag) is shown
  hass.states["switch.aircon_airq"] = { state: "off", attributes: {} };
  card.hass = { ...hass };
  await card._building; // a newly resolved entity rebuilds the cards
  await card._render();
  assert.ok(settings(card).includes("switch.aircon_airq"));
});

test("room glance lists only sensors that exist", async () => {
  const { card } = await render([]);
  assert.deepEqual(plain(slot(card, "room").element.config.entities), [
    KEYS.indoor_temperature, KEYS.indoor_humidity, KEYS.outdoor_temperature,
  ]);
});
