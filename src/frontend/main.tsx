import {useEffect, useRef, useState} from 'react';
import type {FormEvent, MouseEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {CasePane, Status, views} from './CasePane';
import {Library} from './Library';
import type {View} from './CasePane';
import {displayDate, judgmentSubject} from './api';
import {Button, Collapsible, Input} from './components/ui/controls';
import type {Dashboard, Language} from './api';
import '@fontsource-variable/atkinson-hyperlegible-next';
import '@fontsource-variable/literata';
import './style.css';

const count = new Intl.NumberFormat('en');
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
  const screen = params.get('screen') === 'library' ? 'library' : 'queue';
  const lang: Language = params.get('lang') === 'fr' ? 'fr' : 'de';
  const view = views.find(value => value === params.get('view')) ??
    (location.hash === '#case-briefing' ? 'Legal summaries' : 'Overview');
  const selected = dashboard?.selected?.key === caseKey ? dashboard.selected : null;
  const dataParams = new URLSearchParams(url);
  dataParams.delete('view');
  const dataQuery = dataParams.toString();
  const caseLoading = Boolean(caseKey && !selected && !error && loadedQuery !== dataQuery);
  const displayed = selected ?? (caseLoading ? dashboard?.selected : null);
  const lastCaseView = useRef({view, lang});
  useEffect(() => {
    if (selected) lastCaseView.current = {view, lang};
  }, [selected, view, lang]);
  const displayedView = selected ? {view, lang} : lastCaseView.current;

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
    setError('');
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
      setError('');
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
        <nav className="sections" aria-label="Sections">
          <a href="/" onClick={follow} aria-current={screen === 'queue' ? 'page' : undefined}>Review queue</a>
          <a href="/?screen=library" onClick={follow} aria-current={screen === 'library' ? 'page' : undefined}>Library</a>
        </nav>
        <span className="challenge">Suva</span>
      </div>
    </header>
    {screen === 'library' ? <main><Library params={params} link={link} follow={follow} navigate={navigate}/></main> :
    <main>
      <div className="headline">
        <h1>Federal Court judgments</h1>
      </div>
      <dialog ref={dialog} aria-labelledby="add-title" onCancel={event => {if (busy) event.preventDefault();}}>
        <form ref={form} onSubmit={event => {event.preventDefault(); void action('/add', new FormData(event.currentTarget));}}>
          <h2 id="add-title">Add judgment</h2>
          <label>Case ID<Input name="docket" placeholder="8C_123/2026" required/></label>
          <label>Decision date (required for uploads or multiple decisions)<Input name="decision_date" type="date"/></label>
          <label>Original text file (optional)<Input name="text" type="file" accept=".txt,text/plain"/></label>
          <label>Original URL (optional)<Input name="source_url" type="url"/></label>
          <label>Publication date (optional)<Input name="publication_date" type="date"/></label>
          {addError && <p role="alert">{addError}</p>}
          <div className="actions">
            <Button type="submit" disabled={busy}>{busy ? 'Adding…' : 'Add judgment'}</Button>
            <Button variant="outline" disabled={busy} onClick={() => dialog.current?.close()}>Cancel</Button>
          </div>
        </form>
      </dialog>
      {dashboard && <div className="coverage">
        <p><strong>{count.format(dashboard.count)} judgments</strong> · {count.format(dashboard.shortlist)} shortlisted
          {dashboard.count > dashboard.scored && ` · ${count.format(dashboard.count - dashboard.scored)} unscored`}
        </p>
        <Collapsible label="Coverage">
          <p>Publication window: {displayDate(dashboard.window[0])}–{displayDate(dashboard.window[1])}</p>
          {dashboard.dates && <p>Dates found: {displayDate(dashboard.dates[0])}–{displayDate(dashboard.dates[1])}</p>}
          {dashboard.unknown_publication > 0 && <p>{count.format(dashboard.unknown_publication)} cases have no publication date.</p>}
          <p>Source coverage may be incomplete.</p>
        </Collapsible>
      </div>}
      {dashboard && dashboard.stale > 0 && <Collapsible className="notice warning"
        label={`${count.format(dashboard.stale)} scores are out of date`}>
        <p>Stale cases are excluded from the shortlist. Run <code>python -m suva parse</code> and <code>python -m suva score</code>, then reload.</p>
      </Collapsible>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <div className="notice" role="alert">{error} <button onClick={() => setRefresh(value => value + 1)}>Retry</button></div>}
      {!dashboard && !error && <p role="status">Loading judgments…</p>}
      {dashboard && <div className={'workspace' + (displayed ? ' has-case' : '')}>
        <section className="panel queue" aria-labelledby="queue-title">
          <div className="queue-header">
            <div className="queue-heading">
              <h2 id="queue-title">Review queue {dashboard.total !== dashboard.count && <small>{count.format(dashboard.total)} matches</small>}</h2>
              <Button variant="outline" onClick={() => {setAddError(''); dialog.current?.showModal();}}>Add judgment</Button>
            </div>
            <form className="filters" key={JSON.stringify([params.get('q'), params.get('status'), params.get('sort')])}
              onSubmit={filter}>
              <Input name="q" type="search" aria-label="Search cases" placeholder="Search case or legal issue" defaultValue={params.get('q') ?? ''}/>
              <select name="status" aria-label="Review status" defaultValue={params.get('status') ?? ''}
                onChange={event => event.currentTarget.form?.requestSubmit()}>
                <option value="">All statuses</option>
                {statuses.map(status => <option key={status}>{status}</option>)}
              </select>
              <select name="sort" aria-label="Sort by" defaultValue={params.get('sort') === 'date' ? 'date' : 'score'}
                onChange={event => event.currentTarget.form?.requestSubmit()}>
                <option value="score">Highest score</option><option value="date">Newest publication</option>
              </select>
              <Button type="submit" variant="outline">Search</Button>
            </form>
          </div>
          <div className="table-wrap"><table>
            <thead><tr><th scope="col">Judgment</th><th scope="col">Published</th><th scope="col">Status</th></tr></thead>
            <tbody>{dashboard.rows.map(row => <tr key={row.key} className={row.key === caseKey ? 'selected' : ''}>
              <td><a className="queue-link" href={link({case: row.key, view: null})} onClick={follow}
                title={row.issue ?? undefined} aria-current={row.key === caseKey ? 'true' : undefined}>{row.docket}</a>
                {row.title !== row.docket ? <div className="topic">{judgmentSubject(row.title)}</div>
                  : row.issue && <div className="topic">{row.issue}</div>}</td>
              <td>{row.publication_date ? displayDate(row.publication_date) : 'Unknown'}</td>
              <td><Status value={row.status}/></td>
            </tr>)}</tbody>
          </table></div>
          {!dashboard.rows.length && <p className="empty">{dashboard.count
            ? 'No judgments match these filters.' : 'No judgments in this window.'}</p>}
          {dashboard.total > 30 && <nav className="pagination" aria-label="Queue pages">
            {dashboard.page > 2 && <a href={link({page: null})} onClick={follow}>First</a>}
            {dashboard.page > 1 && <a href={link({page: String(dashboard.page - 1)})} onClick={follow}>Previous</a>}
            <span>Page {dashboard.page} of {Math.ceil(dashboard.total / 30)}</span>
            {dashboard.page * 30 < dashboard.total && <a href={link({page: String(dashboard.page + 1)})} onClick={follow}>Next</a>}
            {(dashboard.page + 1) * 30 < dashboard.total &&
              <a href={link({page: String(Math.ceil(dashboard.total / 30))})} onClick={follow}>Last</a>}
          </nav>}
        </section>
        {displayed && <CasePane key={displayed.key} record={displayed} lang={displayedView.lang}
          view={displayedView.view} audio={dashboard.lang === displayedView.lang ? dashboard.audio : {}}
          selectView={value => navigate(link({view: value}))} selectLanguage={value => navigate(link({lang: value}))}
          close={close} onClose={follow} action={action} busy={busy} switching={caseLoading}/>}
        {!displayed && (!caseKey || caseLoading) && <section className="panel case-placeholder" aria-busy={caseLoading}>
          <h2>{caseLoading ? 'Loading judgment…' : 'Select a judgment'}</h2>
        </section>}
        {caseKey && !displayed && !caseLoading && <section className="panel empty" role="status">
          {error ? 'Judgment could not be loaded.' : 'Judgment unavailable.'}{' '}
          <a href={close} onClick={follow}>Back to queue</a>
        </section>}
      </div>}
    </main>}
  </>;
}

createRoot(document.getElementById('root')!).render(<App/>);
