const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {runInNewContext} = require("node:vm");

function setup({denyMic = false, blockAutoplay = false, request} = {}) {
  class Element {
    constructor() { this.events = {}; this.children = []; this.attrs = {}; this.value = ""; }
    addEventListener(name, handler) { this.events[name] = handler; }
    append(...elements) { this.children.push(...elements); }
    replaceChildren() { this.children = []; }
    setAttribute(key, value) { this.attrs[key] = value; }
    removeAttribute(key) { delete this.attrs[key]; }
    toggleAttribute(key, on) { if (on) this.setAttribute(key, ""); else this.removeAttribute(key); }
    click() { return this.events.click?.({preventDefault() {}}); }
    hasAttribute(key) { return Object.hasOwn(this.attrs, key); }
  }
  const elements = new Map();
  const byId = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const root = byId("root");
  root.querySelector = selector => byId(selector.slice(1));
  root.dataset = {case: "8C_354/2026", lang: "de", ask: "ask",
    speak: "speak", transcribe: "transcribe", recap: "recap"};
  const calls = [], events = {};
  let tracksStopped = 0, pauses = 0, revoked = 0, stopTimer;
  class Recorder {
    static isTypeSupported() { return true; }
    constructor() { this.state = "inactive"; this.mimeType = "audio/webm"; }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      this.ondataavailable({data: new Blob(["recording"])});
      this.onstop();
    }
  }
  class Player extends Element {
    async play() { if (blockAutoplay) throw new Error("Autoplay blocked"); }
    pause() { pauses++; }
  }
  const window = {MediaRecorder: Recorder, addEventListener: (event, fn) => events[event] = fn,
    removeEventListener: (event, fn) => { if (events[event] === fn) delete events[event]; },
    clearTimeout() {}, setTimeout: fn => { stopTimer = fn; }};
  const dispose = runInNewContext(readFileSync("src/frontend/conversation-controller.mjs", "utf8").replace("export function", "function") + "\nmountConversation(root);", {
    root,
    window, MediaRecorder: Recorder, Audio: Player, AbortController, FormData, Blob,
    URL: {createObjectURL: () => "blob:demo", revokeObjectURL: () => revoked++},
    document: {querySelector: () => root, getElementById: byId, createElement: () => new Element()},
    navigator: {mediaDevices: {async getUserMedia() {
      if (denyMic) throw new Error("Denied");
      return {getTracks: () => [{stop() { tracksStopped++; }}]};
    }}},
    async fetch(action, options) {
      const payload = action === "transcribe" ? options.body : JSON.parse(options.body);
      calls.push({action, payload, signal: options.signal});
      const data = request ? await request(action) : undefined;
      if (data?.error) return {ok: false, async json() { return data; }};
      const defaults = {transcribe: {transcript: "Was entschied das Gericht?"},
        ask: {kind: "court", answer: "Die Beschwerde war unzulässig.", citations: [{id:"2", text:"Quelle"}]},
        recap: {points:["Ausgang"], takeaways:[], open_questions:[], citations:[]}};
      return {ok: true, async json() { return data ?? defaults[action]; }, async blob() { return new Blob(["audio"]); }};
    },
  });
  return {byId, calls, events, dispose, tracks: () => tracksStopped,
    pauses: () => pauses, revoked: () => revoked, timeout: () => stopTimer(),
    async record() { await byId("chat-mic").click(); await byId("chat-mic").click(); }};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const actions = ui => ui.calls.map(c => c.action);

test("voice submits automatically and blocked playback reuses one audio response", async () => {
  const ui = setup({blockAutoplay: true});
  await ui.byId("chat-mic").click();
  assert.equal(ui.byId("chat-mic").textContent, "Send question");
  await ui.byId("chat-mic").click();
  await flush();
  assert.deepEqual(actions(ui), ["transcribe", "ask", "speak"]);
  assert.equal(ui.calls[0].payload.get("lang"), "de");
  assert.equal(ui.calls[1].payload.question, "Was entschied das Gericht?");
  assert.match(ui.byId("chat-status").textContent, /Play answer/);
  const row = ui.byId("chat-turns").children[0];
  await row.children[2].click();
  assert.deepEqual(actions(ui), ["transcribe", "ask", "speak"]);
  assert.equal(ui.tracks(), 1);
});

test("permission errors, follow-up history, and recap freshness", async () => {
  const denied = setup({denyMic: true});
  await denied.byId("chat-mic").click();
  assert.match(denied.byId("chat-status").textContent, /Microphone unavailable/);
  assert.deepEqual(actions(denied), []);
  const ui = setup();
  assert.equal(ui.byId("chat-mic").textContent, "Start case call");
  await ui.record();
  await flush();
  assert.equal(ui.byId("chat-status").textContent, "Speaking…");
  await ui.byId("recap-tab").click();
  assert.equal(ui.byId("recap-panel").hidden, false);
  await ui.byId("chat-recap").click();
  await ui.byId("chat-tab").click();
  await ui.record();
  await flush();
  const answers = ui.calls.filter(call => call.action === "ask");
  assert.equal(answers[1].payload.history[0].question, "Was entschied das Gericht?");
  assert.match(ui.byId("chat-recap-result").children[0].textContent, /Refresh/);
  await ui.byId("chat-clear").click();
  assert.equal(ui.byId("chat-turns").children.length, 0);
  assert.equal(ui.byId("chat-recap").disabled, true);
});

test("navigation stops mic and playback, and clearing discards late requests", async () => {
  const recording = setup();
  await recording.byId("chat-mic").click();
  recording.events.pagehide();
  assert.equal(recording.tracks(), 1);
  assert.deepEqual(actions(recording), []);
  const active = setup();
  await active.byId("chat-mic").click();
  active.timeout();
  await flush();
  active.dispose();
  assert.equal(active.events.pagehide, undefined);
  assert.equal(active.pauses(), 1);
  assert.equal(active.revoked(), 1);
  let finish;
  const ui = setup({request: () => new Promise(resolve => { finish = resolve; })});
  await ui.record();
  await ui.byId("chat-clear").click();
  assert.equal(ui.calls[0].signal.aborted, true);
  finish();
  await flush();
  assert.equal(ui.byId("chat-turns").children.length, 0);
});

test("provider failures allow another recording or playback retry", async () => {
  let fail = "transcribe";
  const ui = setup({request: action => action === fail ? {error: "Provider unavailable"} : undefined});
  await ui.byId("chat-mic").click();
  await ui.byId("chat-mic").click();
  await flush();
  assert.deepEqual(actions(ui), ["transcribe"]);
  assert.match(ui.byId("chat-status").textContent, /Provider unavailable/);
  fail = "ask";
  await ui.byId("chat-mic").click();
  await ui.byId("chat-mic").click();
  await flush();
  assert.match(ui.byId("chat-status").textContent, /Provider unavailable/);
  fail = "speak";
  await ui.record();
  await flush();
  let row = ui.byId("chat-turns").children[0];
  await row.children[2].click();
  assert.match(ui.byId("chat-status").textContent, /Provider unavailable/);
  fail = null;
  row = ui.byId("chat-turns").children[0];
  await row.children[2].click();
  assert.equal(ui.byId("chat-status").textContent, "Speaking…");
  fail = "recap";
  await ui.byId("chat-recap").click();
  assert.match(ui.byId("chat-status").textContent, /Provider unavailable/);
  assert.equal(ui.byId("chat-recap").disabled, false);
});
