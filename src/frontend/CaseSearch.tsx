import {useState} from 'react';
import type {FormEvent, MouseEvent} from 'react';
import {Status} from './CasePane';
import {Snippet} from './Library';
import {displayDate} from './api';
import type {CaseSearchHit} from './api';

const KINDS: Record<string, string> = {statute: 'Same provision', article: 'Same article', bge: 'Same BGE', term: 'Term'};

/** The last search, kept by the app (in memory only) so it survives a switch to another screen and back. */
export type PrecedentSearch = {description: string; hits?: CaseSearchHit[]};

/** Precedents for a lawyer's case: the judgements of the window ranked against a short case description. */
export function CaseSearch({follow, search, onChange}: {
  follow: (event: MouseEvent<HTMLAnchorElement>) => void;
  search: PrecedentSearch; onChange: (search: PrecedentSearch) => void;
}) {
  const {description, hits} = search;
  const setDescription = (value: string) => onChange({description: value, hits});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      const response = await fetch('/api/case-search', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({description})});
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.message || 'Could not search. Try again.');
      onChange({description, hits: body.hits});
    } catch (failure) {
      console.warn('case_search_failed', {reason: String(failure)});
      setMessage(failure instanceof Error ? failure.message : 'Could not search. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel library" aria-labelledby="precedents-title">
    <h1 id="precedents-title">Find precedents</h1>
    <p>Describe your case: facts, legal question, provisions (Art. 6 UVG) and leading decisions (BGE 129 V 177).
      Judgments of the window that cite the same provisions or decisions rank first; every hit shows why it matched.</p>
    <form className="library-ask" onSubmit={submit}>
      <label>Case description
        <textarea rows={5} maxLength={4000} required value={description} onChange={event => setDescription(event.target.value)}/>
      </label>
      <button className="primary" disabled={busy || !description.trim()}>{busy ? 'Searching…' : 'Find precedents'}</button>
    </form>
    {message && <p role="alert">{message}</p>}
    {hits && <div role="status">
      <h2>{hits.length ? `${hits.length} matching judgments` : 'No matching judgment in the window'}</h2>
      <ol className="library-hits">{hits.map(hit => <li key={hit.key}>
        <a href={'/?' + new URLSearchParams({case: hit.key})} onClick={follow}>{hit.docket}</a>{' '}
        <Status value={hit.status}/> <span className="muted">decided {displayDate(hit.decision_date)}</span>
        {hit.title !== hit.docket && <div className="topic">{hit.title}</div>}
        <ul className="matches">{hit.matches.map(match => <li key={match.kind + match.value}>
          <span className="muted">{KINDS[match.kind] ?? match.kind}:</span> {match.value}</li>)}</ul>
        {hit.highlights.map((text, i) => <Snippet key={i} text={text}/>)}
      </li>)}</ol>
    </div>}
  </section>;
}
