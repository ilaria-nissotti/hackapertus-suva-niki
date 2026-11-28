import {useEffect, useState} from 'react';
import type {FormEvent, MouseEvent} from 'react';
import {displayDate} from './api';
import type {Facet, LibraryAnswer, LibraryHit, LibraryPage} from './api';

const OUTCOMES: Record<string, string> = {
  upheld: 'Upheld', partly_upheld: 'Partly upheld', dismissed: 'Dismissed', not_entered: 'Not entered',
  written_off: 'Written off', sent_back: 'Sent back',
};
const LANGUAGES: Record<string, string> = {de: 'Deutsch', fr: 'Français'};
const PERIODS: [string, string, number][] = [['', 'Any decision date', 0], ['3', 'Decided in the last 3 months', 3],
  ['6', 'Decided in the last 6 months', 6], ['12', 'Decided in the last 12 months', 12]];

/** Search snippet with the matched words (marked ⟦…⟧ by the server) highlighted. */
export function Snippet({text}: {text: string}) {
  return <p className="snippet">{text.split(/(⟦[^⟧]*⟧)/).map((part, i) => part.startsWith('⟦') ?
    <mark key={i}>{part.slice(1, -1)}</mark> : part)}</p>;
}

function monthsAgo(months: number) {
  const date = new Date();
  date.setMonth(date.getMonth() - months);
  return date.toISOString().slice(0, 10);
}

function FacetSelect({label, name, facets, value, names}: {
  label: string; name: string; facets: Facet[]; value: string; names?: Record<string, string>;
}) {
  return <div className="facet"><label htmlFor={'library-' + name}>{label}</label>
    <select id={'library-' + name} name={name} defaultValue={value}
      onChange={event => event.currentTarget.form?.requestSubmit()}>
      <option value="">All</option>
      {facets.map(facet => <option key={facet.value} value={facet.value}>
        {(facet.label ?? names?.[facet.value] ?? facet.value) + ` (${facet.count})`}</option>)}
      {value && !facets.some(facet => facet.value === value) && <option value={value}>{names?.[value] ?? value}</option>}
    </select>
  </div>;
}

export function Library({params, link, follow, navigate}: {
  params: URLSearchParams; link: (changes: Record<string, string | null>) => string;
  follow: (event: MouseEvent<HTMLAnchorElement>) => void; navigate: (href: string) => void;
}) {
  const [data, setData] = useState<LibraryPage>();
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<LibraryAnswer>();
  const [asking, setAsking] = useState(false);
  const period = PERIODS.find(item => item[0] === params.get('period')) ?? PERIODS[0];
  const query = new URLSearchParams();
  for (const name of ['q', 'statute', 'topic', 'outcome', 'language']) {
    const value = params.get(name);
    if (value) query.set(name, value);
  }
  if (period[2]) query.set('date_from', monthsAgo(period[2]));
  const search = query.toString();

  useEffect(() => {
    const abort = new AbortController();
    fetch('/api/library?' + search, {signal: abort.signal})
      .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
      .then((page: LibraryPage) => { setData(page); setError(''); })
      .catch(failure => {
        if (abort.signal.aborted) return;
        console.warn('library_load_failed', {reason: String(failure)});
        setError('Could not load the library. Try again.');
      });
    return () => abort.abort();
  }, [search]);

  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const changes: Record<string, string | null> = {};
    for (const name of ['q', 'statute', 'topic', 'outcome', 'language', 'period']) {
      changes[name] = String(values.get(name) ?? '').trim() || null;
    }
    navigate(link(changes));
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAsking(true);
    setAnswer(undefined);
    try {
      const response = await fetch('/api/library/ask', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question, lang: params.get('language') === 'fr' ? 'fr' : 'de'})});
      const body = await response.json().catch(() => ({}));
      setAnswer(response.ok ? body : {answer: null, cited: [], message: body.message || 'Could not answer. Try again.'});
    } catch (failure) {
      console.warn('library_ask_failed', {reason: String(failure)});
      setAnswer({answer: null, cited: [], message: 'Could not answer. Try again.'});
    } finally {
      setAsking(false);
    }
  }

  const caseLink = (hit: {key: string; language: string}) =>
    '/?' + new URLSearchParams({case: hit.key, view: 'Legal summaries', lang: hit.language});
  const facets = data?.facets;
  return <section className="panel library" aria-labelledby="library-title">
    <h1 id="library-title">Library</h1>
    <p>Approved German and French summaries, searchable by word and filterable by statute, topic, decision date,
      outcome and language.</p>
    <form className="library-filters" role="search" key={params.toString()} onSubmit={filter}>
      <div className="facet query"><label htmlFor="library-q">Search approved summaries</label>
        <input id="library-q" name="q" type="search" defaultValue={params.get('q') ?? ''} placeholder="e.g. Kausalzusammenhang"/>
      </div>
      <FacetSelect label="Statute" name="statute" facets={facets?.statute ?? []} value={params.get('statute') ?? ''}/>
      <FacetSelect label="Topic" name="topic" facets={facets?.topic ?? []} value={params.get('topic') ?? ''}/>
      <div className="facet"><label htmlFor="library-period">Date</label>
        <select id="library-period" name="period" defaultValue={period[0]} onChange={event => event.currentTarget.form?.requestSubmit()}>
          {PERIODS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      <FacetSelect label="Outcome" name="outcome" facets={facets?.outcome ?? []} value={params.get('outcome') ?? ''} names={OUTCOMES}/>
      <FacetSelect label="Language" name="language" facets={facets?.language ?? []} value={params.get('language') ?? ''} names={LANGUAGES}/>
      <button>Search</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">Loading the library…</p>}
    {data && <>
      <h2>{data.total} approved {data.total === 1 ? 'summary' : 'summaries'}</h2>
      {!data.hits.length && <p className="empty">{search ? 'No approved summary matches these filters.' :
        'No approved summaries yet. Approve a German or French summary in the review queue and it appears here; ' +
        'the numbers in the filters count approved summaries.'}</p>}
      <ol className="library-hits">{data.hits.map((hit: LibraryHit) => <li key={hit.key + hit.language}>
        <a href={caseLink(hit)} onClick={follow}>{hit.docket}</a>{' '}
        <span className="muted">{LANGUAGES[hit.language] ?? hit.language} · decided {displayDate(hit.decision_date)}
          {hit.outcome.length > 0 && ' · ' + hit.outcome.map(value => OUTCOMES[value] ?? value).join(', ')}
          {hit.statutes.length > 0 && ' · ' + hit.statutes.join(', ')}</span>
        {hit.title !== hit.docket && <div className="topic">{hit.title}</div>}
        <Snippet text={hit.snippet}/>
      </li>)}</ol>
    </>}
    <form className="library-ask" onSubmit={ask}>
      <h2>Ask the library</h2>
      <label>Question about the approved summaries
        <textarea rows={3} maxLength={1000} required value={question} onChange={event => setQuestion(event.target.value)}/>
      </label>
      <button className="primary" disabled={asking || !question.trim()}>{asking ? 'Asking…' : 'Ask'}</button>
      {answer && <div role="status">
        {answer.answer ? <><p>{answer.answer}</p>
          <ul>{answer.cited.map(item => <li key={item.key}>
            <a href={caseLink(item)} onClick={follow}>{item.key}</a><Snippet text={item.snippet}/></li>)}</ul></>
          : <p>{answer.message}</p>}
        <p className="muted">Apertus answer from approved summaries only. Check the cited judgments.</p>
      </div>}
    </form>
  </section>;
}
