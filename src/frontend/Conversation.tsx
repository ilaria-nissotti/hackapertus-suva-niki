import {useEffect, useRef} from 'react';
import {mountConversation} from './conversation-controller.mjs';
import type {Language} from './api';

export function Conversation({docket, lang}: {docket: string; lang: Language}) {
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    if (root.current) return mountConversation(root.current);
  }, [docket, lang]);
  return <section ref={root} aria-labelledby="conversation-heading" data-case={docket} data-lang={lang}
    data-transcribe="/conversation/transcribe" data-ask="/conversation/ask"
    data-speak="/conversation/speak" data-recap="/conversation/recap">
    <h3 id="conversation-heading">Ask this judgment</h3>
    <div className="segmented" aria-label="Conversation view">
      <button type="button" id="chat-tab" aria-pressed="true" aria-controls="chat-panel">Conversation</button>
      <button type="button" id="recap-tab" aria-pressed="false" aria-controls="recap-panel">Conversation summary</button>
    </div>
    <p id="chat-status" className="call-status" role="status">Ready</p>
    <div id="chat-panel">
      <div className="call-stage">
        <div className="call-portrait" aria-hidden="true">
          <span>§</span>
          <img src="/conversation-portrait" alt="" onError={event => {event.currentTarget.hidden = true;}}/>
        </div>
        <button type="button" id="chat-mic" className="call-button" aria-describedby="chat-status">Start case call</button>
        <p className="muted">Your microphone audio goes to ElevenLabs for transcription.</p>
      </div>
      <ol className="chat-turns" id="chat-turns" aria-label="Questions and answers" lang={lang}/>
      <button type="button" id="chat-clear" hidden>Clear conversation</button>
    </div>
    <div id="recap-panel" hidden>
      <button type="button" id="chat-recap">Generate summary</button>
      <div id="chat-recap-result" lang={lang}/>
    </div>
  </section>;
}
