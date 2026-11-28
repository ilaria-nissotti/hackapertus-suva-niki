import type {MouseEvent} from 'react';
import {useEffect, useId, useRef, useState} from 'react';
import {Conversation} from './Conversation';
import {displayDate, fields, judgmentSubject, operativeText} from './api';
import {Badge, Button, Collapsible, Textarea} from './components/ui/controls';
import type {Action, Briefing, FiredRule, Judgment, Language, TriageFeedback} from './api';

export const views = ['Overview', 'Legal summaries', 'Ask this judgment'] as const;
export type View = typeof views[number];
const viewLabels: Record<View, string> = {
  Overview: 'Overview', 'Legal summaries': 'Summaries', 'Ask this judgment': 'Ask a question',
};

export function Status({value}: {value: string}) {
  return <Badge className={'status ' + value.toLowerCase().replaceAll(' ', '-')}>{value}</Badge>;
}

function Evidence({label, passages}: {label: string; passages?: string[]}) {
  if (!passages?.length) return null;
  return <Collapsible label={label}>{passages.map((text, i) => <blockquote key={i}>{text}</blockquote>)}</Collapsible>;
}

/** The stored ruling, paragraph by paragraph, with the court's paragraph numbers (1., 2.1 …) hung in the margin. */
function RulingText({text}: {text: string}) {
  const blocks: {number: string; text: string}[] = [];
  let number = '';
  for (const part of text.split(/\n{2,}/)) {
    if (!part.trim()) continue;
    // A number on its own ("2.2."), or on the first line of the paragraph it numbers ("2.2.2.\nNach dem …").
    const numbered = part.match(/^\s*(\d+(?:\.\d+)*\.?)\s*(?:\n([\s\S]*))?$/);
    if (numbered && !numbered[2]) {
      if (number) blocks.push({number, text: ''});
      number = numbered[1];
    } else if (numbered) {
      if (number) blocks.push({number, text: ''});
      blocks.push({number: numbered[1], text: numbered[2]});
      number = '';
    } else {
      blocks.push({number, text: part});
      number = '';
    }
  }
  if (number) blocks.push({number, text: ''});
  return <div className="source ruling">{blocks.map((block, i) =>
    <p key={i}><span className="paragraph-number">{block.number}</span>{block.text}</p>)}</div>;
}

function DecisionPreview({text, language}: {text: string; language: string | null}) {
  const [expanded, setExpanded] = useState(false);
  const continuationId = useId();
  const excerptLimit = 480;
  const firstLines = text.split('\n', 12).join('\n').trimEnd();
  const excerpt = firstLines.length <= excerptLimit ? firstLines :
    firstLines.slice(0, excerptLimit + 1).replace(/\s+\S*$/, '').slice(0, excerptLimit).trimEnd();
  return <div lang={language ?? undefined} className="decision-preview">
    {excerpt === text ? <RulingText text={text}/> : <>
      <p className="muted">Decision excerpt</p>
      <RulingText text={excerpt + ' …'}/>
      <Button className="decision-toggle" variant="outline" aria-expanded={expanded}
        aria-controls={continuationId} onClick={() => setExpanded(value => !value)}>
        {expanded ? 'Show less' : 'Read full decision'}
      </Button>
      <div id={continuationId} className="decision-continuation"
        data-expanded={expanded} aria-hidden={!expanded}>
        <div><RulingText text={text.slice(excerpt.length).trimStart()}/></div>
      </div>
    </>}
  </div>;
}

function Relevance({record}: {record: Judgment}) {
  const leading = record.score_current ? record.fired.filter(rule => rule.points > 0).slice(0, 2) : [];
  const list = (rules: FiredRule[]) => <ul className="reasons">{rules.map(rule =>
    <li key={rule.id}><span className="points">{rule.points > 0 ? '+' : ''}{Math.round(rule.points)}</span>{rule.explanation}</li>)}</ul>;
  if (record.score === null) return <p className="muted">This judgment hasn’t been scored.</p>;
  return <>
    {leading.length > 0 && <ul className="leading-reasons">{leading.map(rule =>
      <li key={rule.id}>{rule.explanation}</li>)}</ul>}
    {!record.score_current && <p role="status">This score is out of date.</p>}
    {record.score_current && !leading.length && <p className="muted">No rules increased this score.</p>}
    <Collapsible label="Score details">
      <p><strong>Score {Math.round(record.score)}</strong>{!record.score_current && ' · Out of date'}</p>
      {record.fired.length ? list(record.fired) : <p>No relevance rule matched.</p>}
      <p className="muted">Rules: <code>rules/rules.yaml</code></p>
    </Collapsible>
  </>;
}

const ASSESSMENTS = ['Relevant', 'Review', 'Low relevance'];

/** The reviewer's own triage, saved beside the rules score (which it never changes). */
function Assessment({record}: {record: Judgment}) {
  const [saved, setSaved] = useState<TriageFeedback | null>(record.triage_feedback ?? null);
  const [status, setStatus] = useState(saved?.status ?? '');
  const [reason, setReason] = useState(saved?.reason ?? '');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  return <form className="assessment" onSubmit={async event => {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      const response = await fetch('/api/triage-feedback', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({case: record.key, status, reason})});
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.message || 'Could not save the assessment.');
      setSaved(body.triage_feedback);
      setMessage(body.message);
    } catch (failure) {
      console.warn('triage_feedback_failed', {reason: String(failure)});
      setMessage(failure instanceof Error ? failure.message : 'Could not save the assessment.');
    } finally {
      setBusy(false);
    }
  }}>
    <h4>Your assessment</h4>
    <p className="muted">Agree or disagree with the rules. Saved for the rules experts; the score stays as it is.</p>
    <fieldset>
      <legend>Relevance for Suva</legend>
      {ASSESSMENTS.map(value => <label key={value}>
        <input type="radio" name="assessment" value={value} checked={status === value} required
          onChange={() => setStatus(value)}/> {value}</label>)}
    </fieldset>
    <label>Reason (optional)
      <textarea rows={3} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)}/>
    </label>
    <button disabled={busy || !status}>{saved ? 'Update assessment' : 'Save assessment'}</button>
    {message && <p role="status">{message}</p>}
    {saved && !message && <p className="muted">Saved: {saved.status}{saved.status !== record.status &&
      ` (rules: ${record.status})`}.</p>}
  </form>;
}

function Summary({record, lang, audio, action, busy, visible}: {
  record: Judgment; lang: Language; audio: Briefing; action: Action; busy: boolean; visible: boolean;
}) {
  const result = record[lang];
  const formId = useId();
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
  const canGenerate = !record.de.active && !record.fr.active && !(record.de.data.summary && record.fr.data.summary);
  const retry = Boolean(record.de.error || record.fr.error);
  const dirty = edit !== undefined && edit.trim() !== saved.trim();
  const canSave = Boolean(result.data.summary && !result.active && !result.error && (!result.approved || dirty));
  const canCreateAudio = Boolean(result.approved && audio.configured && !audio.active && !audio.ready);
  const generate = () => action('/run', fields({case: record.key, action: 'summarize'}));
  return <>
    <section>
      {result.active ? <p role="status">Generating summary…</p> : result.error ?
        <p role="alert">{result.error}</p> : result.data.summary ?
        <form id={formId} onSubmit={async event => {
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
          <label>{result.approved ? 'Approved summary' : 'Draft summary'}
            <Textarea lang={lang} rows={12} required maxLength={16000} disabled={busy}
              value={edit ?? saved} onChange={event => {
                setEdit(event.target.value);
                setSaveError('');
                setSaveStatus('');
              }}/>
          </label>
          {saveStatus && <p className="save-feedback" role="status">{saveStatus}</p>}
          {saveError && <p className="save-feedback" role="alert">{saveError}</p>}
        </form> : (record.de.active || record.fr.active) && <p role="status">Generating summaries…</p>}
      {!!result.data.glossary_warnings?.length && <div className="notice warning">
        <strong>Terminology check</strong> (rules/glossary.yaml): <ul>{result.data.glossary_warnings.map(item =>
          <li key={item.found}>“{item.found}” is the other language’s term; Suva uses “{item.expected}”.</li>)}</ul>
      </div>}
      <Evidence label="Supporting passages for the draft" passages={result.data.quotes}/>
    </section>
    {result.approved && <section className="briefing" id="case-briefing">
      <h3>Audio briefing</h3>
      {audio.ready ?
        <>
          <audio key={audio.key} ref={player} controls preload="none"
            aria-label={lang === 'de' ? 'German case briefing' : 'French case briefing'}
            src={'/briefing/' + audio.key + '.mp3?' + new URLSearchParams({case: record.key, lang})}/>
          <Collapsible label="Read transcript"><p className="source" lang={lang}>{audio.script}</p></Collapsible>
          <Evidence label="Supporting passages for the briefing" passages={audio.excerpts}/>
        </> : audio.active ? <p role="status">Generating audio…</p> : !audio.configured ?
        <p className="muted">Audio is unavailable for this language.</p> :
        audio.error && <p role="alert">{audio.error}</p>}
    </section>}
    {(canSave || canGenerate || canCreateAudio) && <div className="case-actions">
      {canSave ? <Button type="submit" form={formId} disabled={busy || !(edit ?? saved).trim()}>
        {result.approved ? 'Save approved edit' : 'Approve summary'}
      </Button> : canCreateAudio ? <Button disabled={busy} onClick={() => action('/briefing', fields(base))}>
        {audio.error ? 'Retry briefing' : 'Create audio briefing'}
      </Button> : canGenerate && <Button disabled={busy} onClick={generate}>
        {retry ? 'Retry summaries' : 'Generate summaries'}
      </Button>}
      {canGenerate && (canSave || canCreateAudio) && <Button variant="outline" disabled={busy} onClick={generate}>
        {retry ? 'Retry summaries' : 'Generate both drafts'}
      </Button>}
    </div>}
  </>;
}

export function CasePane({record, audio, lang, view, selectView, selectLanguage, close, onClose, action, busy, switching}: {
  record: Judgment; audio: Briefing; lang: Language; view: View;
  selectView: (view: View) => void; selectLanguage: (lang: Language) => void;
  close: string; onClose: (event: MouseEvent<HTMLAnchorElement>) => void; action: Action; busy: boolean; switching: boolean;
}) {
  const result = record[lang];
  const drafting = record.de.active || record.fr.active;
  const retry = Boolean(record.de.error || record.fr.error);
  const decision = record.decision ? operativeText(record.decision) : '';
  const createAudio = Boolean(result.approved && audio.configured && !audio.active && !audio.ready);
  const summaryAction = createAudio ? (audio.error ? 'Retry briefing' : 'Create audio briefing') :
    result.approved ? 'Open summary' : result.data.summary ? 'Review draft' :
    drafting ? 'View summaries' : retry ? 'Retry summaries' : 'Generate summaries';
  function openSummaries() {
    selectView('Legal summaries');
    if (createAudio) {
      void action('/briefing', fields({case: record.key, lang}));
    } else if (!result.data.summary && !drafting) {
      void action('/run', fields({case: record.key, action: 'summarize'}));
    }
  }
  return <article className="panel case" aria-labelledby="case-title" inert={switching} aria-busy={switching}>
    <header className="case-header">
      <a className="back" href={close} onClick={onClose}>Back to queue</a>
      <div className="case-identity">
        <span className="case-docket">{record.docket}</span>
        <Status value={record.status}/>
      </div>
      <h2 id="case-title" className="case-name" lang={record.language ?? undefined}>{judgmentSubject(record.title)}</h2>
      <dl className="metadata">
        <div><dt>Decided</dt><dd>{displayDate(record.decision_date)}</dd></div>
        {record.publication_date
          ? <div><dt>Published</dt><dd>{displayDate(record.publication_date)}</dd></div>
          : <div><dt>Publication date</dt><dd>unknown</dd></div>}
      </dl>
    </header>
    <nav className="case-nav" aria-label="Judgment sections">
      {views.map(item => <Button key={item} variant="ghost" aria-pressed={view === item}
        onClick={() => selectView(item)}>{viewLabels[item]}</Button>)}
    </nav>
    <div className="case-body">
      {view !== 'Overview' && <div className="segmented languages" aria-label="Review language">
        <button type="button" lang="de" aria-pressed={lang === 'de'} onClick={() => selectLanguage('de')}>Deutsch</button>
        <button type="button" lang="fr" aria-pressed={lang === 'fr'} onClick={() => selectLanguage('fr')}>Français</button>
      </div>}
      <div hidden={view !== 'Overview'}>
        <section>
          <h3>Court’s decision</h3>
          {decision ? <DecisionPreview text={decision} language={record.language}/>
            : <p className="muted">No decision excerpt available.</p>}
        </section>
        <section>
          <h3>Why this was flagged</h3>
          <Relevance record={record}/>
          <Assessment record={record}/>
        </section>
        <section>
          <h3>Original judgment</h3>
          {record.title !== judgmentSubject(record.title) &&
            <p className="official-title" lang={record.language ?? undefined}>{record.title}</p>}
          {record.full_text && <Collapsible label="Read full judgment"><RulingText text={record.full_text}/></Collapsible>}
          {record.source_url && <a href={record.source_url} target="_blank" rel="noopener noreferrer">Open source</a>}
        </section>
        <div className="case-actions">
          <Button disabled={busy} onClick={openSummaries}>{summaryAction}</Button>
        </div>
      </div>
      <div hidden={view !== 'Legal summaries'}>
        <Summary key={JSON.stringify([lang, record[lang].version])} record={record} lang={lang} audio={audio} action={action} busy={busy} visible={!switching && view === 'Legal summaries'}/>
      </div>
      {view === 'Ask this judgment' && <Conversation key={lang} caseKey={record.key} lang={lang}/>}
    </div>
  </article>;
}
