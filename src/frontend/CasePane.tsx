import type {MouseEvent} from 'react';
import {useEffect, useRef, useState} from 'react';
import {Conversation} from './Conversation';
import {displayDate, fields} from './api';
import type {Action, Briefing, FiredRule, Judgment, Language} from './api';

export const views = ['Overview', 'Legal summaries', 'Ask this judgment'] as const;
export type View = typeof views[number];

export function Status({value}: {value: string}) {
  return <span className={'status ' + value.toLowerCase().replaceAll(' ', '-')}>{value}</span>;
}

function Evidence({label, passages}: {label: string; passages?: string[]}) {
  if (!passages?.length) return null;
  return <details><summary>{label}</summary>{passages.map((text, i) => <blockquote key={i}>{text}</blockquote>)}</details>;
}

const BANDS = {high: 'High relevance: read it', check: 'Check: skim it', low: 'Low relevance: likely skip'};

function Relevance({record}: {record: Judgment}) {
  const reasons = (sign: 1 | -1) => record.fired.filter(rule => Math.sign(rule.points) === sign);
  const list = (rules: FiredRule[]) => <ul className="reasons">{rules.map(rule =>
    <li key={rule.id}><span className="points">{rule.points > 0 ? '+' : ''}{Math.round(rule.points)}</span> {rule.explanation}</li>)}</ul>;
  if (record.score === null) return <p>Not scored yet. Run <code>python -m suva score</code>.</p>;
  return <>
    {!record.score_current && <p role="alert">This score is out of date (rules, parse or text changed). Run <code>python -m suva score</code>.</p>}
    {record.band && <p><strong>{BANDS[record.band]}</strong></p>}
    {reasons(1).length > 0 && <><h4>Why it was selected</h4>{list(reasons(1))}</>}
    {reasons(-1).length > 0 && <><h4>What counts against it</h4>{list(reasons(-1))}</>}
    {!record.fired.length && <p>No relevance rule matched this judgment.</p>}
    <p className="muted">Deterministic score from the expert rules in rules/rules.yaml.</p>
  </>;
}

function Summary({record, lang, audio, action, busy, visible}: {
  record: Judgment; lang: Language; audio: Briefing; action: Action; busy: boolean; visible: boolean;
}) {
  const result = record[lang];
  const saved = result.approved ?? result.data.summary ?? '';
  const [edit, setEdit] = useState<string>();
  const [saveStatus, setSaveStatus] = useState('');
  const [saveError, setSaveError] = useState('');
  const [submitted, setSubmitted] = useState<string>();
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    const pause = () => player.current?.pause();
    window.addEventListener('pagehide', pause);
    return () => window.removeEventListener('pagehide', pause);
  }, []);
  useEffect(() => {
    if (!visible) player.current?.pause();
  }, [visible]);
  useEffect(() => {
    if (submitted !== undefined && result.approved === submitted) {
      setEdit(current => current === submitted ? undefined : current);
      setSubmitted(undefined);
    }
  }, [result.approved, submitted]);
  const base = {case: record.key, lang};
  return <>
    <section>
      <h3>Legal summary</h3>
      {result.active ? <p role="status">Generating summary…</p> : result.error ?
        <p role="alert">{result.error}</p> : result.data.summary ?
        <form onSubmit={async event => {
          event.preventDefault();
          const summary = (edit ?? saved).trim();
          setSaveError('');
          setSaveStatus('Saving…');
          if (await action('/approve', fields({...base, summary, version: result.version ?? "",
                                               previous_approved: result.approved ?? ""}), setSaveError)) {
            setEdit(summary);
            setSubmitted(summary);
            setSaveStatus(result.approved ? 'Approved edit saved.' : 'Summary approved.');
          } else {
            setSaveStatus('');
          }
        }}>
          <label>{result.approved ? 'Approved summary' : 'AI draft · Review before use'}
            <textarea lang={lang} rows={12} required maxLength={16000} disabled={busy}
              value={edit ?? saved} onChange={event => {
                setEdit(event.target.value);
                setSaveError('');
                setSaveStatus('');
              }}/>
          </label>
          <button className="primary" disabled={busy || !(edit ?? saved).trim()}>
            {result.approved ? 'Save approved edit' : 'Approve summary'}
          </button>
          {saveStatus && <p className="save-feedback" role="status">{saveStatus}</p>}
          {saveError && <p className="save-feedback" role="alert">{saveError}</p>}
        </form> : <p>Generate independent German and French drafts from the original ruling.</p>}
      <Evidence label="Supporting passages for the draft" passages={result.data.quotes}/>
      {!record.de.active && !record.fr.active && !(record.de.data.summary && record.fr.data.summary) &&
        <button className="primary" disabled={busy}
          onClick={() => action('/run', fields({case: record.key, action: 'summarize'}))}>
          {record.de.error || record.fr.error ? 'Retry summaries' : 'Summarize judgment'}
        </button>}
    </section>
    <section className="briefing" id="case-briefing">
      <h3>Audio briefing</h3>
      {!result.approved ? <p>Approve this summary to enable audio.</p> : audio.ready ?
        <>
          <audio key={audio.key} ref={player} controls preload="none"
            aria-label={lang === 'de' ? 'German case briefing' : 'French case briefing'}
            src={'/briefing/' + audio.key + '.mp3?' + new URLSearchParams({case: record.key, lang})}/>
          <details><summary>Read transcript</summary><p className="source" lang={lang}>{audio.script}</p></details>
          <Evidence label="Supporting passages for the briefing" passages={audio.excerpts}/>
        </> : audio.active ? <p role="status">Generating audio…</p> : !audio.configured ?
        <p>Audio is unavailable until ElevenLabs is configured for this language.</p> : <>
          {audio.error && <p role="alert">{audio.error}</p>}
          <button disabled={busy} onClick={() => action('/briefing', fields(base))}>
            {audio.error ? 'Retry briefing' : 'Generate briefing'}
          </button>
        </>}
    </section>
  </>;
}

export function CasePane({record, audio, lang, view, selectView, selectLanguage, close, onClose, action, busy}: {
  record: Judgment; audio: Briefing; lang: Language; view: View;
  selectView: (view: View) => void; selectLanguage: (lang: Language) => void;
  close: string; onClose: (event: MouseEvent<HTMLAnchorElement>) => void; action: Action; busy: boolean;
}) {
  return <article className="panel case" aria-labelledby="case-title">
    <header className="case-header">
      <a className="back" href={close} onClick={onClose}>Back to queue</a>
      <div className="case-identity">
        <div><p className="context">Federal Supreme Court</p><h2 id="case-title">{record.docket}</h2></div>
        <div className="case-score">{record.score !== null && <strong>{Math.round(record.score)}<small> / 100</small></strong>}
          <Status value={record.status}/></div>
      </div>
      {record.title !== record.docket && <p className="case-name">{record.title}</p>}
      <dl className="metadata">
        <div><dt>Published</dt><dd>{record.publication_date ? displayDate(record.publication_date) +
          (record.date_source ? ' · ' + record.date_source : '') : 'Unknown'}</dd></div>
        <div><dt>Decided</dt><dd>{displayDate(record.decision_date)}</dd></div>
      </dl>
    </header>
    <nav className="case-nav" aria-label="Judgment sections">
      {views.map(item => <button key={item} type="button" aria-pressed={view === item}
        onClick={() => selectView(item)}>{item}</button>)}
    </nav>
    <div className="case-body">
      {view !== 'Overview' && <div className="segmented languages" aria-label="Review language">
        <button type="button" lang="de" aria-pressed={lang === 'de'} onClick={() => selectLanguage('de')}>Deutsch</button>
        <button type="button" lang="fr" aria-pressed={lang === 'fr'} onClick={() => selectLanguage('fr')}>Français</button>
      </div>}
      <div hidden={view !== 'Overview'}>
        <section>
          <h3>Relevance to Suva</h3>
          <Relevance record={record}/>
        </section>
        <section>
          <h3>Original judgment</h3>
          {record.source_url && <a href={record.source_url} target="_blank" rel="noopener noreferrer">Open source judgment</a>}
          <details><summary>Read judgment text</summary><div className="source">{record.full_text}</div></details>
        </section>
      </div>
      <div hidden={view !== 'Legal summaries'}>
        <Summary key={JSON.stringify([lang, record[lang].version])} record={record} lang={lang} audio={audio} action={action} busy={busy} visible={view === 'Legal summaries'}/>
      </div>
      {view === 'Ask this judgment' && <Conversation key={lang} caseKey={record.key} lang={lang}/>}
    </div>
  </article>;
}
