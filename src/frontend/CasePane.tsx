import type {MouseEvent, ReactNode} from 'react';
import {useEffect, useRef, useState} from 'react';
import {Conversation} from './Conversation';
import {displayDate, fields} from './api';
import type {Action, Briefing, FiredRule, Judgment, Language} from './api';

/** Plain-language wording for the stored review statuses (the API and the filters keep the stored values). */
const STATUS_LABELS: Record<string, string> = {
  'Relevant': 'Read first', 'Review': 'Worth reviewing', 'Low relevance': 'Low relevance',
  'Stale': 'Score out of date', 'Unscored': 'Not scored',
};
export const statusLabel = (value: string) => STATUS_LABELS[value] ?? value;
const LANGUAGE_NAMES: Record<Language, string> = {de: 'Deutsch', fr: 'Français'};

export function Status({value}: {value: string}) {
  return <span className={'status ' + value.toLowerCase().replaceAll(' ', '-')}>{statusLabel(value)}</span>;
}

/** The stored ruling, paragraph by paragraph, with the court's paragraph numbers (1., 2.1 …) hung in the margin. */
function RulingText({text, lang}: {text: string; lang?: string}) {
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
  return <div className="source ruling" lang={lang}>{blocks.map((block, i) =>
    <p key={i}><span className="paragraph-number">{block.number}</span>{block.text}</p>)}</div>;
}

/** The operative part without the signature block, which starts at the place-and-date line
    ("Luzern, 6. August 2026", "Lucerne, le 6 août 2026", "Bellinzona, 3 settembre 2026"). Display only. */
function decisionWithoutSignatures(text: string) {
  const parts = text.split(/\n{2,}/);
  const end = parts.findIndex((part, i) => i > 0 && /^\p{Lu}[\p{L} .'-]*, (le |il )?\d{1,2}\.? \p{L}+ \d{4}\.?\s*$/u.test(part.trim()));
  return end > 0 ? parts.slice(0, end).join('\n\n') : text;
}

function WhyInQueue({record}: {record: Judgment}) {
  const reasons = (sign: 1 | -1) => record.fired.filter(rule => Math.sign(rule.points) === sign);
  const list = (rules: FiredRule[]) => <ul className="reasons">{rules.map(rule =>
    <li key={rule.id}><span className="points">{rule.points > 0 ? '+' : ''}{Math.round(rule.points)}</span> {rule.explanation}</li>)}</ul>;
  if (record.score === null) return <p>Not scored yet. Run <code>python -m suva score</code>.</p>;
  return <>
    {!record.score_current && <p role="alert">This score is out of date (rules, parse or text changed). Run{' '}
      <code>python -m suva parse</code>, then <code>python -m suva score</code>.</p>}
    <p><strong>{statusLabel(record.status)}</strong>, score {Math.round(record.score)} of 100</p>
    {list(reasons(1))}
    {reasons(-1).length > 0 && <><h4>What counts against it</h4>{list(reasons(-1))}</>}
    {!record.fired.length && <p>No relevance rule matched this judgment.</p>}
    <p className="muted">Scored by the expert rules in rules/rules.yaml.</p>
  </>;
}

/** Everything that depends on the review language. Remounted when the language or the draft changes. */
function LanguageWorkspace({record, lang, audio, action, busy, selectLanguage}: {
  record: Judgment; lang: Language; audio: Briefing; action: Action; busy: boolean;
  selectLanguage: (lang: Language) => void;
}) {
  const result = record[lang];
  const saved = result.approved ?? result.data.summary ?? '';
  const [edit, setEdit] = useState<string>();
  const [saveStatus, setSaveStatus] = useState('');
  const [saveError, setSaveError] = useState('');
  const [submitted, setSubmitted] = useState<string>();
  const [asking, setAsking] = useState(false);
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    const pause = () => player.current?.pause();
    window.addEventListener('pagehide', pause);
    return () => window.removeEventListener('pagehide', pause);
  }, []);
  useEffect(() => {
    if (submitted !== undefined && result.approved === submitted) {
      setEdit(current => current === submitted ? undefined : current);
      setSubmitted(undefined);
    }
  }, [result.approved, submitted]);

  const base = {case: record.key, lang};
  const text = edit ?? saved;
  const formId = `summary-${lang}`;
  const edited = result.approved !== null && text.trim() !== result.approved;
  const generating = record.de.active || record.fr.active;

  // One main action at a time, always in the toolbar: generate, then approve, then create the audio briefing.
  let primary: ReactNode = null;
  if (generating) primary = <button className="primary" disabled>Generating summaries…</button>;
  else if (!result.data.summary) primary = <button className="primary" disabled={busy}
    onClick={() => action('/run', fields({case: record.key, action: 'summarize'}))}>
    {record.de.error || record.fr.error ? 'Retry summaries' : 'Generate summaries'}</button>;
  else if (!result.approved || edited) primary = <button className="primary" form={formId}
    disabled={busy || !text.trim()}>{result.approved ? 'Save approved edit' : 'Approve summary'}</button>;
  else if (audio.active) primary = <button className="primary" disabled>Creating audio briefing…</button>;
  else if (!audio.ready && audio.configured) primary = <button className="primary" disabled={busy}
    onClick={() => action('/briefing', fields(base))}>{audio.error ? 'Retry audio briefing' : 'Create audio briefing'}</button>;
  else if (!audio.ready) primary = <p className="muted">Audio briefings need ElevenLabs for {LANGUAGE_NAMES[lang]}.</p>;

  return <>
    <div className="case-toolbar">
      <div className="segmented languages" role="group" aria-label="Review language">
        {(['de', 'fr'] as const).map(value => <button key={value} type="button" lang={value}
          aria-pressed={lang === value} onClick={() => selectLanguage(value)}>{LANGUAGE_NAMES[value]}</button>)}
      </div>
      <div className="primary-action">{primary}</div>
    </div>

    <div className="case-grid">
      <section aria-labelledby="why-title">
        <h3 id="why-title">Why it’s in the queue</h3>
        <WhyInQueue record={record}/>
      </section>
      <section aria-labelledby="decision-title">
        <h3 id="decision-title">What the court decided</h3>
        {record.decision ? <RulingText text={decisionWithoutSignatures(record.decision)} lang={record.language ?? undefined}/>
          : <p className="muted">The operative part wasn’t found in this judgment. Read the full judgment below.</p>}
      </section>
    </div>

    <div className="case-grid summary-grid">
      <section aria-labelledby="summary-title">
        <h3 id="summary-title">Summary in {LANGUAGE_NAMES[lang]}</h3>
        {result.active ? <p role="status">Generating the summary…</p> : result.error ?
          <p role="alert">{result.error}</p> : result.data.summary ?
          <form id={formId} onSubmit={async event => {
            event.preventDefault();
            const summary = text.trim();
            setSaveError('');
            setSaveStatus('Saving…');
            if (await action('/approve', fields({...base, summary, version: result.version ?? '',
                                                 previous_approved: result.approved ?? ''}), setSaveError)) {
              setEdit(summary);
              setSubmitted(summary);
              setSaveStatus(result.approved ? 'Approved edit saved.' : 'Summary approved.');
            } else {
              setSaveStatus('');
            }
          }}>
            <label>{result.approved ? 'Approved summary' : 'AI draft: review it, then approve it in the toolbar'}
              <textarea lang={lang} rows={12} required maxLength={16000} disabled={busy} value={text}
                onChange={event => { setEdit(event.target.value); setSaveError(''); setSaveStatus(''); }}/>
            </label>
          </form> : <p>No summary yet. Generate summaries drafts the German and French summaries from the
            original ruling.</p>}
        {saveStatus && <p className="save-feedback" role="status">{saveStatus}</p>}
        {saveError && <p className="save-feedback" role="alert">{saveError}</p>}
      </section>
      <aside aria-labelledby="passages-title">
        <h3 id="passages-title">Supporting passages</h3>
        {result.data.quotes?.length ? result.data.quotes.map((quote, i) => <blockquote key={i} lang={record.language ?? undefined}>{quote}</blockquote>)
          : <p className="muted">The passages the draft relies on appear here.</p>}
      </aside>
    </div>

    <section className="briefing" id="case-briefing" aria-labelledby="briefing-title">
      <h3 id="briefing-title">Audio briefing</h3>
      {!result.approved ? <p className="muted">Available once the {LANGUAGE_NAMES[lang]} summary is approved.</p>
        : audio.ready ? <>
          <audio key={audio.key} ref={player} controls preload="none"
            aria-label={lang === 'de' ? 'German case briefing' : 'French case briefing'}
            src={'/briefing/' + audio.key + '.mp3?' + new URLSearchParams({case: record.key, lang})}/>
          <details><summary>Read the transcript</summary><p className="source" lang={lang}>{audio.script}</p></details>
          {!!audio.excerpts?.length && <details><summary>Passages the briefing cites</summary>
            {audio.excerpts.map((text, i) => <blockquote key={i}>{text}</blockquote>)}</details>}
        </> : audio.active ? <p role="status">Creating the audio briefing…</p>
        : audio.error ? <p role="alert">{audio.error}</p>
        : <p className="muted">{audio.configured ? 'Not created yet.' : `Needs ElevenLabs for ${LANGUAGE_NAMES[lang]}.`}</p>}
    </section>

    <details className="case-more" onToggle={event => setAsking(event.currentTarget.open)}>
      <summary>Ask questions about this judgment</summary>
      {asking && <Conversation key={lang} caseKey={record.key} lang={lang}/>}
    </details>
    <details className="case-more">
      <summary>Read the full judgment</summary>
      {record.source_url && <p><a href={record.source_url} target="_blank" rel="noopener noreferrer">Open the original on the court’s site</a></p>}
      <RulingText text={record.full_text} lang={record.language ?? undefined}/>
    </details>
  </>;
}

export function CasePane({record, audio, lang, selectLanguage, close, onClose, action, busy}: {
  record: Judgment; audio: Briefing; lang: Language; selectLanguage: (lang: Language) => void;
  close: string; onClose: (event: MouseEvent<HTMLAnchorElement>) => void; action: Action; busy: boolean;
}) {
  const title = record.title !== record.docket ? record.title : record.docket;
  return <article className="case" aria-labelledby="case-title">
    <a className="back" href={close} onClick={onClose}>Back to list</a>
    <header className="case-header">
      <div className="case-heading">
        <h2 id="case-title" className="case-name" lang={record.language ?? undefined}>{title}</h2>
        <Status value={record.status}/>
      </div>
      <dl className="metadata">
        <div><dt>Case</dt><dd>{record.docket}</dd></div>
        <div><dt>Decided</dt><dd>{displayDate(record.decision_date)}</dd></div>
        <div><dt>Published</dt><dd>{record.publication_date ? displayDate(record.publication_date) +
          (record.date_source ? ` (${record.date_source})` : '') : 'Unknown'}</dd></div>
      </dl>
    </header>
    <LanguageWorkspace key={JSON.stringify([lang, record[lang].version])} record={record} lang={lang} audio={audio}
      action={action} busy={busy} selectLanguage={selectLanguage}/>
  </article>;
}
