const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {runInNewContext} = require("node:vm");

function setup({denyMic = false, blockAutoplay = false, request, recording} = {}) {
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
      this.ondataavailable({data: recording ?? new Blob(["recording"])});
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
      if (data instanceof Error) throw data;  // a dropped connection rejects fetch
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

test("limit: an empty or over-3 MB recording is refused before any upload", async () => {
  for (const recording of [new Blob([]), new Blob([new Uint8Array(3 * 1024 * 1024 + 1)])]) {
    const ui = setup({recording});
    await ui.record();
    await flush();
    assert.deepEqual(actions(ui), []);
    assert.match(ui.byId("chat-status").textContent, /under 3 MB/);
    assert.equal(ui.byId("chat-mic").disabled, false);  // the user can record again
  }
});

test("limit: a recording of exactly 3 MB is uploaded", async () => {
  const ui = setup({recording: new Blob([new Uint8Array(3 * 1024 * 1024)])});
  await ui.record();
  await flush();
  assert.deepEqual(actions(ui), ["transcribe", "ask", "speak"]);
});

test("limit: only the last six turns are kept and sent as history", async () => {
  const ui = setup();
  for (let i = 0; i < 8; i++) { await ui.record(); await flush(); }
  assert.equal(ui.byId("chat-turns").children.length, 6);
  const asks = ui.calls.filter(call => call.action === "ask");
  assert.equal(asks.length, 8);
  assert.equal(asks.at(-1).payload.history.length, 6);
});

test("duplicate: clicking Generate summary twice while it runs sends one recap request", async () => {
  let finish;
  const ui = setup({request: action => action === "recap" ? new Promise(resolve => { finish = resolve; }) : undefined});
  await ui.record();
  await flush();
  const first = ui.byId("chat-recap").click();
  ui.byId("chat-recap").click();
  ui.byId("chat-recap").click();
  assert.equal(actions(ui).filter(a => a === "recap").length, 1);
  finish({points: ["Ausgang"], takeaways: [], open_questions: [], citations: []});
  await first;
  assert.equal(ui.byId("chat-recap").disabled, false);
});

test("duplicate: the mic ignores a new call while an answer is pending", async () => {
  let finish;
  const ui = setup({request: action => action === "ask" ? new Promise(resolve => { finish = resolve; }) : undefined});
  await ui.record();
  await flush();
  await ui.byId("chat-mic").click();  // busy: must not open a second recording
  assert.deepEqual(actions(ui), ["transcribe", "ask"]);
  assert.equal(ui.tracks(), 1);
  finish();
  await flush();
  assert.deepEqual(actions(ui), ["transcribe", "ask", "speak"]);
});

test("out of order: a recap that returns after Clear is discarded", async () => {
  let finish;
  const ui = setup({request: action => action === "recap" ? new Promise(resolve => { finish = resolve; }) : undefined});
  await ui.record();
  await flush();
  const pending = ui.byId("chat-recap").click();
  await ui.byId("chat-clear").click();
  finish({points: ["Late"], takeaways: [], open_questions: [], citations: []});
  await pending;
  await flush();
  assert.equal(ui.byId("chat-recap-result").children.length, 0);
  assert.equal(ui.byId("chat-turns").children.length, 0);
  assert.equal(ui.byId("chat-recap").disabled, true);
});

test("out of order: a transcript that returns after Clear never becomes a question", async () => {
  let finish;
  const ui = setup({request: action => action === "transcribe" ? new Promise(resolve => { finish = resolve; }) : undefined});
  await ui.record();
  await ui.byId("chat-clear").click();
  finish({transcript: "Late question"});
  await flush();
  assert.deepEqual(actions(ui), ["transcribe"]);
  assert.equal(ui.byId("chat-turns").children.length, 0);
});

test("network: a dropped connection shows the error and the next attempt works", async () => {
  let drop = true;
  const ui = setup({request: action => action === "ask" && drop ? new TypeError("Failed to fetch") : undefined});
  await ui.record();
  await flush();
  assert.equal(ui.byId("chat-status").textContent, "Failed to fetch");
  assert.equal(ui.byId("chat-turns").children.length, 0);
  assert.equal(ui.byId("chat-mic").disabled, false);
  drop = false;
  await ui.record();
  await flush();
  assert.equal(ui.byId("chat-turns").children.length, 1);
});
