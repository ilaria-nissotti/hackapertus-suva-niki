import {useEffect, useRef, useState} from 'react';
import type {FormEvent, MouseEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {CasePane, Status, views} from './CasePane';
import type {View} from './CasePane';
import {displayDate} from './api';
import type {Dashboard, Language} from './api';
import '@fontsource-variable/atkinson-hyperlegible-next';
import '@fontsource-variable/literata';
import './style.css';

const statuses = ['Relevant', 'Review', 'Low relevance', 'Stale', 'Unscored'];

function App() {
  const [url, setUrl] = useState(location.search);
  const [dashboard, setDashboard] = useState<Dashboard>();
  const [loadedQuery, setLoadedQuery] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(() => new URLSearchParams(location.search).get('message') ?? '');
  const [busy, setBusy] = useState(false);
  const [addError, setAddError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const context = useRef(location.search);
  const actionLock = useRef(false);
  const params = new URLSearchParams(url);
  const caseKey = params.get('case');
  const lang: Language = params.get('lang') === 'fr' ? 'fr' : 'de';
  const view = views.find(value => value === params.get('view')) ??
    (location.hash === '#case-briefing' ? 'Legal summaries' : 'Overview');
  const selected = dashboard?.selected?.key === caseKey ? dashboard.selected : null;
  const dataParams = new URLSearchParams(url);
  dataParams.delete('view');
  const dataQuery = dataParams.toString();

  function link(changes: Record<string, string | null>) {
    const next = new URLSearchParams(location.search);
    next.delete('message');
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return next.size ? '/?' + next : '/';
  }

  function navigate(href: string) {
    history.pushState(null, '', href);
    context.current = location.search;
    setUrl(location.search);
    setNotice('');
  }

  function follow(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(event.currentTarget.href);
  }

  useEffect(() => {
    const onPop = () => {
      context.current = location.search;
      setUrl(location.search);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let timer: number;
    let pending = false;
    async function load() {
      try {
        const response = await fetch('/api/dashboard?' + dataQuery, {signal: abort.signal});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data: Dashboard = await response.json();
        if (abort.signal.aborted) return;
        setDashboard(data);
        setLoadedQuery(dataQuery);
        setError('');
        pending = data.active;
      } catch (failure) {
        if (!abort.signal.aborted) {
          console.warn('dashboard_load_failed', {reason: String(failure)});
          setError('Could not load the review queue. Try again.');
        }
      } finally {
        if (!abort.signal.aborted && pending) timer = window.setTimeout(load, 8000);
      }
    }
    void load();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [dataQuery, refresh]);

  async function action(endpoint: string, body: FormData, onError?: (message: string) => void) {
    if (actionLock.current) return false;
    const start = context.current;
    actionLock.current = true;
    setBusy(true);
    setNotice('');
    setAddError('');
    try {
      const response = await fetch(endpoint, {method: 'POST', headers: {Accept: 'application/json'}, body});
      const data = await response.json().catch(() => ({}));
      if (response.status === 409) setRefresh(value => value + 1);
      if (!response.ok) throw new Error(data.message || (response.status === 413 ?
        'The upload exceeds the size limit.' : 'Could not complete the request. Try again.'));
      if (endpoint === '/add' && data.case) {
        dialog.current?.close();
        form.current?.reset();
        navigate(link({case: data.case, view: null}));
      }
      if (!onError && (start === context.current || endpoint === '/add')) setNotice(data.message || '');
      setRefresh(value => value + 1);
      return true;
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : 'Could not complete the request.';
      console.warn('action_failed', {endpoint, reason: message});
      if (endpoint === '/add') setAddError(message);
      else if (onError) onError(message);
      else if (start === context.current) setNotice(message);
      return false;
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  }

  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    navigate(link({q: String(values.get('q')).trim(), status: String(values.get('status')),
      sort: String(values.get('sort')), page: null}));
  }

  const close = link({case: null, view: null});
  return <>
    <header className="masthead">
      <div className="topbar">
        <a className="brand" href="/" onClick={follow}><span aria-hidden="true">§</span> Judgment Review</a>
        <span className="challenge">Suva challenge <span aria-hidden="true">/</span> Apertus</span>
      </div>
    </header>
    <main>
      <div className="headline">
        <h1>Federal Court judgments</h1>
        <button className="primary" onClick={() => {setAddError(''); dialog.current?.showModal();}}>Add judgment</button>
      </div>
      <dialog ref={dialog} aria-labelledby="add-title" onCancel={event => {if (busy) event.preventDefault();}}>
        <form ref={form} onSubmit={event => {event.preventDefault(); void action('/add', new FormData(event.currentTarget));}}>
          <h2 id="add-title">Add judgment</h2>
          <p>Add a judgment from the knowledge base, or upload its original text.</p>
          <label>Case ID<input name="docket" placeholder="8C_123/2026" required/></label>
          <label>Decision date (required for uploads or multiple decisions)<input name="decision_date" type="date"/></label>
          <label>Original text file (optional)<input name="text" type="file" accept=".txt,text/plain"/></label>
          <label>Original URL (optional)<input name="source_url" type="url"/></label>
          <label>Publication date (optional)<input name="publication_date" type="date"/></label>
          {addError && <p role="alert">{addError}</p>}
          <div className="actions">
            <button className="primary" disabled={busy}>{busy ? 'Adding…' : 'Add judgment'}</button>
            <button type="button" disabled={busy} onClick={() => dialog.current?.close()}>Cancel</button>
          </div>
        </form>
      </dialog>
      {dashboard && <div className="coverage">
        <dl className="metrics">
          <div><dt>Judgments</dt><dd>{dashboard.count}</dd></div>
          <div><dt>Scored</dt><dd>{dashboard.scored}</dd></div>
          <div><dt>Shortlisted</dt><dd>{dashboard.shortlist}</dd></div>
        </dl>
        <div className="coverage-dates">
          <strong>{dashboard.dates ? 'Observed publications' : 'No dated publications in the window yet'}</strong>
          {dashboard.dates && <span>{displayDate(dashboard.dates[0])} – {displayDate(dashboard.dates[1])}</span>}
          <small>Window: published {displayDate(dashboard.window[0])}–{displayDate(dashboard.window[1])}.
            {dashboard.unknown_publication > 0 && ` ${dashboard.unknown_publication} with unknown publication date included.`}
            {' '}Source coverage may be incomplete.</small>
        </div>
      </div>}
      {dashboard && dashboard.stale > 0 && <p className="notice warning" role="status">
        <strong>{dashboard.stale} of {dashboard.count} scores are out of date</strong>, so they show as Stale and
        don’t count toward the shortlist. Run <code>python -m suva parse</code>, then <code>python -m suva score</code>,
        and reload.</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <div className="notice" role="alert">{error} <button onClick={() => setRefresh(value => value + 1)}>Retry</button></div>}
      {!dashboard && !error && <p role="status">Loading judgments…</p>}
      {dashboard && <div className={'workspace' + (selected ? ' has-case' : '')}>
        <section className="panel queue" aria-labelledby="queue-title">
          <div className="queue-header">
            <h2 id="queue-title">Review queue <small>{dashboard.total}</small></h2>
            <form className="filters" key={JSON.stringify([params.get('q'), params.get('status'), params.get('sort')])}
              onSubmit={filter}>
              <input name="q" type="search" aria-label="Search cases" placeholder="Search case or legal issue" defaultValue={params.get('q') ?? ''}/>
              <select name="status" aria-label="Review status" defaultValue={params.get('status') ?? ''}
                onChange={event => event.currentTarget.form?.requestSubmit()}>
                <option value="">All statuses</option>
                {statuses.map(status => <option key={status}>{status}</option>)}
              </select>
              <select name="sort" aria-label="Sort by" defaultValue={params.get('sort') === 'date' ? 'date' : 'score'}
                onChange={event => event.currentTarget.form?.requestSubmit()}>
                <option value="score">Highest score</option><option value="date">Newest publication</option>
              </select>
              <button>Search</button>
            </form>
          </div>
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Judgment</th><th scope="col">Published</th><th scope="col">Score</th><th scope="col">Status</th></tr></thead>
            <tbody>{dashboard.rows.map(row => <tr key={row.key} className={row.key === caseKey ? 'selected' : ''}>
              <td><a href={link({case: row.key, view: null})} onClick={follow}
                aria-current={row.key === caseKey ? 'true' : undefined}>{row.docket}</a>
                {row.title !== row.docket ? <div className="topic" title={row.issue ?? undefined}>{row.title}</div>
                  : row.issue && <div className="topic">{row.issue}</div>}</td>
              <td>{row.publication_date ? displayDate(row.publication_date) : 'Unknown'}</td><td className="score">{row.score === null ? '—' : Math.round(row.score)}</td>
              <td><Status value={row.status}/></td>
            </tr>)}</tbody>
          </table></div>
          {!dashboard.rows.length && <p className="empty">{!dashboard.count ?
            'No judgments in the window. Run python -m suva fetch, parse and score.'
            : dashboard.stale > 0 && params.get('status') && params.get('status') !== 'Stale' ?
            `No judgments match these filters. ${dashboard.stale} judgments have out-of-date scores and are listed as Stale until they are re-scored.`
            : 'No judgments match these filters.'}</p>}
          {dashboard.total > 30 && <nav className="pagination" aria-label="Queue pages">
            {dashboard.page > 2 && <a href={link({page: null})} onClick={follow}>First</a>}
            {dashboard.page > 1 && <a href={link({page: String(dashboard.page - 1)})} onClick={follow}>Previous</a>}
            <span>Page {dashboard.page} of {Math.ceil(dashboard.total / 30)}</span>
            {dashboard.page * 30 < dashboard.total && <a href={link({page: String(dashboard.page + 1)})} onClick={follow}>Next</a>}
            {(dashboard.page + 1) * 30 < dashboard.total &&
              <a href={link({page: String(Math.ceil(dashboard.total / 30))})} onClick={follow}>Last</a>}
          </nav>}
        </section>
        {selected && <CasePane key={selected.key} record={selected} lang={lang} view={view} audio={dashboard.lang === lang ? dashboard.audio : {}}
          selectView={value => navigate(link({view: value}))} selectLanguage={value => navigate(link({lang: value}))}
          close={close} onClose={follow} action={action} busy={busy}/>}
        {!caseKey && <section className="panel case-placeholder">
          <h2>Select a judgment</h2>
          <p>Choose a case in the queue to see why it was selected, draft the German and French summaries, and ask
            questions about the ruling.</p>
        </section>}
        {caseKey && !selected && <section className="panel empty" role="status">{
          loadedQuery === dataQuery ? 'Judgment unavailable.' : error ? 'Judgment could not be loaded.' : 'Loading judgment…'
        } <a href={close} onClick={follow}>Back to queue</a></section>}
      </div>}
      {dashboard && <footer>Model {dashboard.model}</footer>}
    </main>
  </>;
}

createRoot(document.getElementById('root')!).render(<App/>);
