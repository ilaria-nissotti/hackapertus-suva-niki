import {useEffect, useRef, useState} from 'react';
import type {FormEvent, MouseEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {CasePane, Status, views} from './CasePane';
import {Library} from './Library';
import {CaseSearch} from './CaseSearch';
import {Weights} from './Weights';
import {Start} from './Start';
import type {PrecedentSearch} from './CaseSearch';
import type {View} from './CasePane';
import {displayDate, judgmentSubject} from './api';
import {Button, Collapsible, Input} from './components/ui/controls';
import type {Dashboard, Language, Reviewer} from './api';
import '@fontsource-variable/atkinson-hyperlegible-next';
import '@fontsource-variable/literata';
import './style.css';

const count = new Intl.NumberFormat('en');

// Without a reviewer in the session (new browser, session expired), every screen goes back to the start page.
const nativeFetch = window.fetch.bind(window);
window.fetch = async (...args: Parameters<typeof fetch>) => {
  const response = await nativeFetch(...args);
  if (response.status === 401 && location.pathname !== '/start') location.assign('/start');
  return response;
};

async function changeReviewer() {
  await nativeFetch('/api/session/end', {method: 'POST'}).catch(() => undefined);
  location.assign('/start');
}

/** The start page (who is reviewing), or the app once the session names the reviewer. */
function Root() {
  const [reviewer, setReviewer] = useState<Reviewer | null>();
  const [failed, setFailed] = useState(false);
  const starting = location.pathname === '/start';
  useEffect(() => {
    if (starting) return;
    fetch('/api/session').then(response => response.json()).then(({reviewer}: {reviewer: Reviewer | null}) => {
      if (reviewer) setReviewer(reviewer);
      else location.assign('/start');
    }).catch(failure => {
      console.warn('session_load_failed', {reason: String(failure)});
      setFailed(true);
    });
  }, [starting]);
  if (starting) return <Start/>;
  if (reviewer) return <App reviewer={reviewer}/>;
  return <main><p role={failed ? 'alert' : 'status'}>{failed ? 'Could not reach the review app. Reload the page.' : 'Loading…'}</p></main>;
}
const statuses = ['Relevant', 'Review', 'Low relevance', 'Stale', 'Unscored'];

function App({reviewer}: {reviewer: Reviewer}) {
  const [url, setUrl] = useState(location.search);
  const [dashboard, setDashboard] = useState<Dashboard>();
  const [loadedQuery, setLoadedQuery] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [rangeError, setRangeError] = useState('');
  const [notice, setNotice] = useState(() => new URLSearchParams(location.search).get('message') ?? '');
  const [busy, setBusy] = useState(false);
  const [addError, setAddError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const context = useRef(location.search);
  const actionLock = useRef(false);
  const params = new URLSearchParams(url);
  const customRange = params.has('published_from') || params.has('published_to');
  const caseKey = params.get('case');
  const screen = ['library', 'precedents', 'weights'].find(value => value === params.get('screen')) ?? 'queue';
  const lang: Language = params.get('lang') === 'fr' ? 'fr' : 'de';
  const view = views.find(value => value === params.get('view')) ??
    (location.hash === '#case-briefing' ? 'Legal summaries' : 'Overview');
  const selected = dashboard?.selected?.key === caseKey ? dashboard.selected : null;
  const dataParams = new URLSearchParams(url);
  dataParams.delete('view');
  const dataQuery = dataParams.toString();
  // The queue is only fetched while it is on screen; the other screens keep the last queue (search, filters, open
  // case) so "Review queue" returns to it as the reviewer left it.
  const queueQuery = screen === 'queue' ? dataQuery : null;
  const lastQueue = useRef('/');
  if (screen === 'queue') lastQueue.current = '/' + url;
  const [precedents, setPrecedents] = useState<PrecedentSearch>({description: ''});
  // Queue responses already seen (or prefetched on hover), so going back to a case, a filter or the queue from
  // another screen shows at once while a fresh copy loads. In memory only; a reload starts empty.
  const cache = useRef(new Map<string, Dashboard>());
  const lastQueueQuery = useRef<string | null>(null);
  function remember(query: string, data: Dashboard) {
    cache.current.delete(query);
    cache.current.set(query, data);
    if (cache.current.size > 40) cache.current.delete(cache.current.keys().next().value!);
  }
  function prefetch(href: string) {
    const query = new URLSearchParams(new URL(href, location.href).search);
    query.delete('view');
    const key = query.toString();
    if (cache.current.has(key)) return;
    fetch('/api/dashboard?' + key).then(response => response.ok ? response.json() : null)
      .then((data: Dashboard | null) => { if (data && !cache.current.has(key)) remember(key, data); })
      .catch(() => {});
  }
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
    const screenOf = (search: string) => new URLSearchParams(search).get('screen');
    const switching = screenOf(new URL(href, location.href).search) !== screenOf(location.search);
    history.pushState(null, '', href);
    if (switching) window.scrollTo(0, 0);  // a new screen starts at its top, under the masthead
    context.current = location.search;
    setUrl(location.search);
    setError('');
    setRangeError('');
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
      setRangeError('');
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    if (queueQuery === null) return;
    // A new query (not a refresh after an action, which must not show the state from before it): show the
    // remembered copy right away.
    const cached = queueQuery !== lastQueueQuery.current ? cache.current.get(queueQuery) : undefined;
    lastQueueQuery.current = queueQuery;
    if (cached) {
      setDashboard(cached);
      setLoadedQuery(queueQuery);
    }
    const abort = new AbortController();
    let timer: number;
    let pending = false;
    async function load() {
      try {
        const response = await fetch('/api/dashboard?' + queueQuery, {signal: abort.signal});
        if (response.status === 400) {
          const {message} = await response.json();
          if (!abort.signal.aborted) setError(message || 'Could not apply the publication dates.');
          return;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data: Dashboard = await response.json();
        if (abort.signal.aborted) return;
        remember(queueQuery!, data);
        setDashboard(data);
        setLoadedQuery(queueQuery!);
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
  }, [queueQuery, refresh]);

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

  function applyPublicationRange(first: string | null, last: string | null) {
    navigate(link({published_from: first, published_to: last, case: null, view: null, page: null}));
  }

  function filterPublication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const dates = new FormData(event.currentTarget);
    const first = String(dates.get('published_from'));
    const last = String(dates.get('published_to'));
    if (first > last) {
      setRangeError('The first publication date must be on or before the last.');
      return;
    }
    applyPublicationRange(first, last);
  }

  const close = link({case: null, view: null});
  return <>
    <header className="masthead">
      <div className="topbar">
        <a className="brand" href="/" onClick={follow}><span aria-hidden="true">§</span> Judgment Review</a>
        <nav className="sections" aria-label="Sections">
          <a href={lastQueue.current} onClick={follow} aria-current={screen === 'queue' ? 'page' : undefined}>Review queue</a>
          <a href="/?screen=precedents" onClick={follow} aria-current={screen === 'precedents' ? 'page' : undefined}>Find precedents</a>
          <a href="/?screen=library" onClick={follow} aria-current={screen === 'library' ? 'page' : undefined}>Library</a>
          <a href="/?screen=weights" onClick={follow} aria-current={screen === 'weights' ? 'page' : undefined}>Rule weights</a>
        </nav>
        <div className="challenge account">
          <span title={reviewer.function || undefined}>Reviewing as {reviewer.name} ({reviewer.initials})</span>
          <Button variant="ghost" onClick={() => void changeReviewer()}>Change</Button>
        </div>
      </div>
    </header>
    {screen === 'library' ? <main><Library params={params} link={link} follow={follow} navigate={navigate}/></main> :
     screen === 'precedents' ? <main><CaseSearch follow={follow} search={precedents} onChange={setPrecedents}/></main> :
     screen === 'weights' ? <main><Weights follow={follow}/></main> :
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
        <p aria-live="polite"><strong>{count.format(dashboard.count)} judgments</strong> · {count.format(dashboard.shortlist)} shortlisted
          {dashboard.count > dashboard.scored && ` · ${count.format(dashboard.count - dashboard.scored)} unscored`}
        </p>
        <Collapsible label="Coverage">
          <p id="publication-date-format">Enter dates as YYYY-MM-DD.</p>
          <form className="publication-filter" key={dashboard.window.join('/')} onSubmit={filterPublication}>
            <label>Published from
              <Input name="published_from" type="text" defaultValue={dashboard.window[0]} size={12}
                pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}" aria-describedby="publication-date-format" required/>
            </label>
            <label>Published through
              <Input name="published_to" type="text" defaultValue={dashboard.window[1]} size={12}
                pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}" aria-describedby="publication-date-format" required/>
            </label>
            <Button type="submit" variant="outline">Apply dates</Button>
            {customRange && <Button variant="ghost" onClick={() => applyPublicationRange(null, null)}>Reset dates</Button>}
          </form>
          {rangeError && <p role="alert">{rangeError}</p>}
          {dashboard.dates && <p>Dates found: {displayDate(dashboard.dates[0])}–{displayDate(dashboard.dates[1])}</p>}
          {dashboard.unknown_publication > 0 && <p>{count.format(dashboard.unknown_publication)} cases have no publication date.</p>}
          {customRange && <p>Custom ranges show imported judgments with known publication dates.</p>}
          <p>Source coverage may be incomplete.</p>
        </Collapsible>
      </div>}
      {dashboard && dashboard.stale > 0 && <Collapsible className="notice warning"
        label={`${count.format(dashboard.stale)} scores are out of date`}>
        <p>Stale cases are excluded from the shortlist. Run <code>python -m suva parse</code> and{' '}
          <code>python -m suva score{customRange && ` --published-from ${dashboard.window[0]} --published-to ${dashboard.window[1]}`}</code>,
          then reload.</p>
      </Collapsible>}
      {dashboard?.rescoring && <p className="notice" role="status">Rescoring the queue with the new rule weights…</p>}
      {dashboard && !dashboard.rescoring && dashboard.feedback.new >= dashboard.feedback.threshold &&
        <p className="notice feedback-ready" role="status">
          <strong>{count.format(dashboard.feedback.new)} new relevance verdicts</strong> since the last weight update.{' '}
          <a href="/?screen=weights" onClick={follow}>Review updated rule weights</a>
        </p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <div className="notice" role="alert">{error}{' '}
        <Button onClick={() => setRefresh(value => value + 1)}>Retry</Button>{' '}
        {customRange && <Button variant="ghost" onClick={() => applyPublicationRange(null, null)}>Reset dates</Button>}
      </div>}
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
                onPointerEnter={event => prefetch(event.currentTarget.href)} onFocus={event => prefetch(event.currentTarget.href)}
                title={row.issue ?? undefined} aria-current={row.key === caseKey ? 'true' : undefined}>{row.docket}</a>
                {row.title !== row.docket ? <div className="topic">{judgmentSubject(row.title)}</div>
                  : row.issue && <div className="topic">{row.issue}</div>}</td>
              <td>{row.publication_date ? displayDate(row.publication_date) : 'Unknown'}</td>
              <td><Status value={row.status}/></td>
            </tr>)}</tbody>
          </table></div>
          {!dashboard.rows.length && <p className="empty">{dashboard.count
            ? 'No judgments match these filters.'
            : customRange ? 'No imported judgments match these publication dates.' : 'No judgments in this window.'}</p>}
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

createRoot(document.getElementById('root')!).render(<Root/>);
