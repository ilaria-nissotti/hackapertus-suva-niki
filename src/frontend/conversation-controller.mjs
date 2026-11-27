const tabHistory = new Map();

export function mountConversation(root) {
  const byId = id => root.querySelector(`#${id}`);
  const mic = byId("chat-mic");
  const status = byId("chat-status");
  const summaryButton = byId("chat-recap");
  const scope = {case: root.dataset.case, lang: root.dataset.lang};
  const storageKey = `suva-conversation:${scope.case}:${scope.lang}`;
  const limit = 3 * 1024 * 1024;
  let turns = [...(tabHistory.get(storageKey) ?? [])], recap = null, busy = false, pendingQuestion = "";
  let nextTurnId = Date.now();
  let recorder, stream, timer, controller, audioPlayer, epoch = 0;
  const recordings = new Map();
  const speechAvailable = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  function save() {
    if (turns.length) tabHistory.set(storageKey, turns.slice(-6));
    else tabHistory.delete(storageKey);
  }

  // Never log questions, transcripts, answers or audio: only the step, the case and the error reason.
  function report(step, error) {
    console.warn("conversation_failed", {step, case: scope.case, lang: scope.lang, reason: error?.message ?? String(error)});
  }

  function element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function evidence(parent, entries) {
    if (!entries.length) return;
    const details = element("details");
    details.append(element("summary", "View supporting passages"));
    for (const item of entries) details.append(element("blockquote", `Line ${item.id} · ${item.text}`));
    parent.append(details);
  }

  function stopAudio() {
    if (audioPlayer) audioPlayer.pause();
    audioPlayer = undefined;
    root.removeAttribute("data-chat-playing");
  }

  function render() {
    const list = byId("chat-turns");
    list.replaceChildren();
    for (const turn of turns) {
      const row = element("li");
      row.append(element("strong", turn.question), element("p", turn.answer));
      const speak = element("button", "Play answer");
      speak.type = "button";
      speak.disabled = busy;
      speak.addEventListener("click", () => play(turn));
      row.append(speak);
      evidence(row, turn.citations);
      list.append(row);
    }
    if (pendingQuestion) {
      const item = element("li");
      item.append(element("strong", pendingQuestion), element("p", "Waiting for answer…"));
      list.append(item);
    }
    byId("chat-clear").hidden = !turns.length && !busy;
    mic.disabled = !speechAvailable || (busy && recorder?.state !== "recording");
    mic.textContent = recorder?.state === "recording" ? "Send question" :
      turns.length ? "Ask follow-up" : "Start case call";
    root.toggleAttribute("data-chat-recording", recorder?.state === "recording");
    byId("recap-tab").disabled = recorder?.state === "recording";
    summaryButton.disabled = busy || !turns.length;
    summaryButton.textContent = recap ? "Refresh summary" : "Generate summary";
    const panel = byId("chat-recap-result");
    panel.replaceChildren();
    if (recap) {
      if (recap.stale) panel.append(element("p", "New turns were added. Refresh this summary."));
      for (const [key, label] of [["points", "Points discussed"], ["takeaways", "Source-backed takeaways"], ["open_questions", "Open questions"]]) {
        if (!recap[key].length) continue;
        const ul = element("ul");
        for (const item of recap[key]) ul.append(element("li", item));
        panel.append(element("h4", label), ul);
      }
      evidence(panel, recap.citations);
    }
  }

  function setBusy(value, message) {
    busy = value;
    root.toggleAttribute("data-chat-busy", value);
    status.textContent = message || (root.hasAttribute("data-chat-playing") ? "Speaking…" :
      speechAvailable ? "Ready" : "Microphone unavailable in this browser.");
    render();
  }

  async function call(action, payload, signal) {
    const multipart = payload instanceof FormData;
    const response = await fetch(root.dataset[action], {
      method: "POST", signal,
      body: multipart ? payload : JSON.stringify({...scope, ...payload}),
      headers: multipart ? {} : {"Content-Type": "application/json"},
    });
    if (!response.ok) {
      const details = await response.json().catch(() => ({}));
      throw new Error(details.error || "Could not complete the request. Try again.");
    }
    return action === "speak" ? response.blob() : response.json();
  }

  const history = () => turns.slice(-6).map(turn => ({
    question: turn.question, answer: turn.answer, evidence_ids: turn.citations.map(c => c.id),
  }));

  async function speak(turn, token, signal) {
    let url = recordings.get(turn.id);
    if (!url) {
      status.textContent = "Preparing audio…";
      const clip = await call("speak", {answer: turn.answer}, signal);
      if (token !== epoch) return;
      url = URL.createObjectURL(clip);
      recordings.set(turn.id, url);
    }
    stopAudio();
    audioPlayer = new Audio(url);
    const player = audioPlayer;
    player.addEventListener("ended", () => {
      if (audioPlayer === player) {
        root.removeAttribute("data-chat-playing");
        if (!busy) status.textContent = "Ready";
      }
    });
    try {
      await player.play();
      if (token === epoch) {
        root.setAttribute("data-chat-playing", "");
        status.textContent = "Speaking…";
      }
    } catch (error) {
      console.info("conversation_autoplay_blocked", {case: scope.case, reason: error?.message});
      if (token === epoch) status.textContent = "Select Play answer to listen.";
    }
  }

  async function play(turn) {
    if (busy) return;
    const token = epoch;
    controller = new AbortController();
    setBusy(true);
    try { await speak(turn, token, controller.signal); }
    catch (error) { report("speak", error); if (token === epoch) status.textContent = error.message; }
    finally { if (token === epoch) setBusy(false, status.textContent); }
  }

  async function ask(text, token, signal) {
    pendingQuestion = text;
    setBusy(true, "Answering…");
    const response = await call("ask", {question: text, history: history()}, signal);
    if (token !== epoch) return;
    const turn = {...response, id: ++nextTurnId, question: text};
    turns.push(turn);
    if (turns.length > 6) {
      const removed = turns.shift();
      if (recordings.has(removed.id)) URL.revokeObjectURL(recordings.get(removed.id));
      recordings.delete(removed.id);
    }
    if (recap) recap.stale = true;
    pendingQuestion = "";
    save();
    render();
    await speak(turn, token, signal);
  }

  async function submit(recording) {
    const token = epoch;
    controller = new AbortController();
    const signal = controller.signal;
    try {
      if (!recording.size || recording.size > limit) throw new Error("Record a shorter question, under 3 MB.");
      const data = new FormData();
      for (const [key, value] of Object.entries(scope)) data.append(key, value);
      data.append("audio", recording, "question");
      setBusy(true, "Transcribing…");
      const {transcript} = await call("transcribe", data, signal);
      if (token !== epoch) return;
      await ask(transcript, token, signal);
    } catch (error) {
      report("question", error);
      if (token === epoch) status.textContent = error.message;
    } finally {
      if (token === epoch) { pendingQuestion = ""; setBusy(false, status.textContent); }
    }
  }

  function releaseMic() {
    window.clearTimeout(timer);
    stream?.getTracks().forEach(track => track.stop());
    stream = undefined;
  }

  mic.addEventListener("click", async () => {
    if (recorder?.state === "recording") { recorder.stop(); return; }
    if (busy) return;
    stopAudio();
    const token = epoch;
    setBusy(true, "Opening microphone…");
    try {
      const opened = await navigator.mediaDevices.getUserMedia({audio: true});
      if (token !== epoch) { opened.getTracks().forEach(track => track.stop()); return; }
      stream = opened;
      const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"]
        .find(type => MediaRecorder.isTypeSupported(type));
      const current = new MediaRecorder(stream, mimeType ? {mimeType} : {});
      recorder = current;
      const chunks = [];
      current.ondataavailable = event => chunks.push(event.data);
      current.onstop = () => {
        if (token !== epoch || recorder !== current) return;
        releaseMic();
        recorder = undefined;
        submit(new Blob(chunks, {type: current.mimeType || chunks[0]?.type}));
      };
      current.onerror = () => {
        if (token !== epoch) return;
        recorder = undefined;
        releaseMic();
        setBusy(false, "Recording failed. Try starting another call.");
      };
      current.start();
      timer = window.setTimeout(() => { if (current.state === "recording") current.stop(); }, 30000);
      setBusy(true, "Listening… Select Send question when you’re done.");
    } catch (error) {
      report("microphone", error);
      if (token !== epoch) return;
      releaseMic();
      recorder = undefined;
      setBusy(false, "Microphone unavailable. Check browser permissions and try again.");
    }
  });

  function cancel() {
    epoch += 1;
    controller?.abort();
    if (recorder?.state === "recording") recorder.stop();
    recorder = undefined;
    releaseMic();
    stopAudio();
    for (const url of recordings.values()) URL.revokeObjectURL(url);
    recordings.clear();
  }

  byId("chat-clear").addEventListener("click", () => {
    cancel();
    turns = []; recap = null; pendingQuestion = "";
    save();
    setBusy(false);
  });
  summaryButton.addEventListener("click", async () => {
    if (busy || !turns.length) return;
    const token = epoch;
    controller = new AbortController();
    setBusy(true, "Summarizing conversation…");
    try {
      const result = await call("recap", {history: history()}, controller.signal);
      if (token === epoch) { recap = result; status.textContent = ""; }
    } catch (error) { report("recap", error); if (token === epoch) status.textContent = error.message; }
    finally { if (token === epoch) setBusy(false, status.textContent); }
  });
  for (const tab of ["chat", "recap"]) {
    byId(`${tab}-tab`).addEventListener("click", () => {
      for (const other of ["chat", "recap"]) {
        byId(`${other}-tab`).setAttribute("aria-pressed", String(other === tab));
        byId(`${other}-panel`).hidden = other !== tab;
      }
    });
  }
  window.addEventListener("pagehide", cancel);
  const restore = event => {
    if (event.persisted) { pendingQuestion = ""; setBusy(false); }
  };
  window.addEventListener("pageshow", restore);
  setBusy(false);
  return () => {
    window.removeEventListener("pagehide", cancel);
    window.removeEventListener("pageshow", restore);
    cancel();
  };
}
