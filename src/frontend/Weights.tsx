import {useEffect, useState} from 'react';
import type {MouseEvent} from 'react';
import {displayDate} from './api';
import {Button, Collapsible} from './components/ui/controls';
import type {ProposalCase, ProposalData, WeightRow, WeightsStatus} from './api';

const percent = (value: number) => `${Math.round(value * 100)} %`;
const points = (value: number | null) => value === null ? '–' : String(Math.round(value));
const signed = (value: number) => (value > 0 ? '+' : '') + Math.round(value);
/** `2026-11-28 10:24:26` (UTC, from SQLite) -> `28 Nov 2026`. */
const day = (value: string | null) => value ? displayDate(value.slice(0, 10)) : '';

async function post(endpoint: string, body?: object) {
  const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body ?? {})});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || 'Could not complete the request. Try again.');
  return data.message as string;
}

/** A before → after figure with a one-line explanation. */
function Comparison({label, before, after, note, better}: {
  label: string; before: string; after: string; note: string; better: boolean | null;
}) {
  return <div className="comparison">
    <p className="comparison-label">{label}</p>
    <p className="comparison-figures"><span className="before">{before}</span>
      <span aria-label="becomes"> → </span>
      <strong>{after}</strong>
      {better !== null && <span className={'trend ' + (better ? 'up' : 'down')}>{better ? 'better' : 'worse'}</span>}</p>
    <p className="muted">{note}</p>
  </div>;
}

/** Current and new points of one rule, drawn left (against) or right (in favour) of zero. */
function WeightBars({row, scale}: {row: WeightRow; scale: number}) {
  const bar = (value: number, kind: string) => {
    const width = `${Math.min(50, Math.abs(value) / scale * 50)}%`;
    return <span className={`bar ${kind} ${value < 0 ? 'negative' : 'positive'}`}
      style={value < 0 ? {right: '50%', width} : {left: '50%', width}}/>;
  };
  return <span className="bars" aria-hidden="true" title={`Now ${signed(row.current_max)}, new ${signed(row.proposed_max)}`}>
    <span className="zero"/>{bar(row.current_max, 'current')}{bar(row.proposed_max, 'proposed')}
  </span>;
}

function amount(row: WeightRow, value: number) {
  if (!row.graded) return signed(value);
  return `${signed(value)} each` + (row.cap_count ? `, max ${signed(value * row.cap_count)}` : '');
}

function WeightTable({rows}: {rows: WeightRow[]}) {
  const scale = Math.max(1, ...rows.flatMap(row => [Math.abs(row.current_max), Math.abs(row.proposed_max)]));
  return <>
    <p className="legend" aria-hidden="true"><span className="swatch current"/> Now <span className="swatch proposed"/> New
      <span className="muted"> · left of the line counts against, right counts in favour</span></p>
    <div className="table-wrap"><table className="weight-table">
      <thead><tr><th scope="col">Rule</th><th scope="col">Change</th><th scope="col" className="num">Now</th>
        <th scope="col" className="num">New</th><th scope="col" className="chart-col"><span className="visually-hidden">Chart</span></th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id} className={row.change === 'No change' ? 'unchanged' : ''}>
        <td>{row.explanation}{row.group === 'significance' && <span className="muted"> · significance</span>}</td>
        <td><span className={'change ' + row.change.toLowerCase().replaceAll(' ', '-')}>{row.change}</span></td>
        <td className="num">{amount(row, row.current)}</td>
        <td className="num"><strong>{amount(row, row.proposed)}</strong></td>
        <td className="chart-col"><WeightBars row={row} scale={scale}/></td>
      </tr>)}</tbody>
    </table></div>
  </>;
}

function shortlisted(value: boolean | undefined) {
  return value ? 'Shortlisted' : 'Not shortlisted';
}

function VerdictCases({cases, follow}: {cases: ProposalCase[]; follow: (event: MouseEvent<HTMLAnchorElement>) => void}) {
  return <div className="table-wrap"><table className="verdict-table">
    <thead><tr><th scope="col">Judgment</th><th scope="col">Your verdict</th><th scope="col">Now</th><th scope="col">New weights</th></tr></thead>
    <tbody>{cases.map(item => {
      const nowRight = item.current_shown === item.verdict, newRight = item.proposed_shown === item.verdict;
      return <tr key={item.key}>
        <td><a href={'/?case=' + encodeURIComponent(item.key)} onClick={follow}>{item.docket}</a>
          <div className="topic">{item.title}</div></td>
        <td>{item.verdict ? 'Relevant' : 'Not relevant'}</td>
        <td className={nowRight ? 'agrees' : 'disagrees'}>{shortlisted(item.current_shown)}
          <span className="muted"> ({points(item.current_points)} pts)</span><span className="visually-hidden">{nowRight ? ', agrees' : ', disagrees'}</span></td>
        <td className={newRight ? 'agrees' : 'disagrees'}>{shortlisted(item.proposed_shown)}
          <span className="muted"> ({points(item.proposed_points)} pts)</span><span className="visually-hidden">{newRight ? ', agrees' : ', disagrees'}</span></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function CaseList({cases, total, follow}: {
  cases: ProposalCase[]; total: number; follow: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  if (!total) return <p className="muted">None.</p>;
  return <ul className="case-list">{cases.map(item => <li key={item.key}>
    <a href={'/?case=' + encodeURIComponent(item.key)} onClick={follow}>{item.docket}</a> {item.title}
    <span className="muted"> · {points(item.current_points)} → {points(item.proposed_points)} pts</span></li>)}
    {total > cases.length && <li className="muted">and {total - cases.length} more</li>}
  </ul>;
}

function ProposalView({data, follow}: {data: ProposalData; follow: (event: MouseEvent<HTMLAnchorElement>) => void}) {
  const {agreement, picks_recall: recall, shortlist, floor} = data;
  const changed = data.weights.filter(row => row.change !== 'No change').length;
  return <>
    {data.warnings.length > 0 && <div className="notice warning" role="note">
      <strong>Check before accepting</strong>
      <ul>{data.warnings.map(text => <li key={text}>{text}</li>)}</ul>
    </div>}
    <div className="comparisons">
      <Comparison label="Agrees with your verdicts" before={`${agreement.current} of ${agreement.verdicts}`}
        after={`${agreement.proposed} of ${agreement.verdicts}`}
        better={agreement.proposed === agreement.current ? null : agreement.proposed > agreement.current}
        note="Shortlisted when you said relevant, left out when you said not relevant. The new weights are tested on verdicts they were not trained on."/>
      <Comparison label="Suva’s past picks in the reading list" before={percent(recall.current)}
        after={percent(recall.proposed)}
        better={Math.round(recall.proposed * 100) === Math.round(recall.current * 100) ? null : recall.proposed > recall.current}
        note="Top 30 per month in the training months (Oct 2025 – Feb 2026)."/>
      <Comparison label="Shortlist in the current window" before={`${shortlist.before}`} after={`${shortlist.after}`}
        better={null} note={`${shortlist.joined} judgments would join, ${shortlist.left} would leave (of ${shortlist.judgements}).`}/>
    </div>

    <h3>What changes per rule</h3>
    <p className="muted">{changed ? `${changed} of ${data.weights.length} rules change.` : 'No rule changes.'} Points a
      rule adds to a judgment when it matches; the queue is ranked by the total.</p>
    <WeightTable rows={data.weights}/>
    <p className="floor">Minimum points to be shortlisted: <span className="before">{points(floor.current)}</span> → <strong>{points(floor.proposed)}</strong>
      <span className="muted"> (still finds 90 % of the relevant training judgments)</span></p>

    {data.verdict_cases.length > 0 && <Collapsible label={`Your verdicts compared (${data.verdict_cases.length})`}>
      <p className="muted">Disagreements with the current rules come first.</p>
      <VerdictCases cases={data.verdict_cases} follow={follow}/>
    </Collapsible>}
    <Collapsible label={`Shortlist changes (${shortlist.joined} join, ${shortlist.left} leave)`}>
      <h4>Would join the shortlist</h4><CaseList cases={shortlist.joined_cases} total={shortlist.joined} follow={follow}/>
      <h4>Would leave the shortlist</h4><CaseList cases={shortlist.left_cases} total={shortlist.left} follow={follow}/>
    </Collapsible>
    <Collapsible label="How these weights were calculated">
      <p>Trained on {data.training.judgements.toLocaleString('en')} judgments: Suva’s {data.training.spreadsheet_picks} past
        picks from the spreadsheet (decided Oct 2025 – Feb 2026) and {data.verdicts.used} of your verdicts
        ({data.verdicts.used_relevant} relevant). Where your verdict and the spreadsheet disagree, your verdict counts
        ({data.verdicts.overrides} {data.verdicts.overrides === 1 ? 'case' : 'cases'}).</p>
      {data.verdicts.test_split > 0 && <p>{data.verdicts.test_split} verdicts are on judgments kept aside to test the
        scoring (March 2026) and were not used.</p>}
      {data.verdicts.missing > 0 && <p>{data.verdicts.missing} verdicts are on judgments no longer in the knowledge base.</p>}
      <p>Display offset (base): {points(data.base.current)} → {points(data.base.proposed)}. It shifts every score shown
        to lawyers by the same amount and does not change the order.</p>
      <p>Accepting writes the new numbers into <code>rules/rules.yaml</code> (comments are kept) and rescores the queue.</p>
    </Collapsible>
  </>;
}

export function Weights({follow}: {follow: (event: MouseEvent<HTMLAnchorElement>) => void}) {
  const [status, setStatus] = useState<WeightsStatus>();
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const working = Boolean(status?.computing || status?.rescoring);

  useEffect(() => {
    const abort = new AbortController();
    let timer: number;
    fetch('/api/weights', {signal: abort.signal})
      .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then((data: WeightsStatus) => {
        setStatus(data);
        setError('');
        if (data.computing || data.rescoring) timer = window.setTimeout(() => setRefresh(value => value + 1), 2000);
      })
      .catch(failure => {
        if (abort.signal.aborted) return;
        console.warn('weights_load_failed', {reason: String(failure)});
        setError('Could not load the rule weights. Try again.');
      });
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [refresh]);

  async function act(endpoint: string, body?: object) {
    setBusy(true);
    setMessage('');
    try {
      setMessage(await post(endpoint, body));
    } catch (failure) {
      setMessage(failure instanceof Error ? failure.message : 'Could not complete the request.');
    } finally {
      setBusy(false);
      setRefresh(value => value + 1);
    }
  }

  if (error) return <div className="notice" role="alert">{error} <button onClick={() => setRefresh(value => value + 1)}>Retry</button></div>;
  if (!status) return <p role="status">Loading rule weights…</p>;
  const {verdicts, proposal, history} = status;
  const ready = verdicts.new >= verdicts.threshold;
  const pending = proposal?.status === 'pending' && proposal.data;
  return <div className="weights">
    <div className="headline"><h1>Rule weights</h1></div>
    <p className="lead">The queue is ranked by points: each rule adds or subtracts points when it matches a judgment.
      Your relevance verdicts can retrain those points. Nothing changes until you accept.</p>
    {message && <p className="notice" role="status">{message}</p>}

    <section className="panel weights-verdicts" aria-labelledby="verdicts-title">
      <h2 id="verdicts-title">Your verdicts</h2>
      <p><strong>{verdicts.total}</strong> saved · {verdicts.relevant} relevant · {verdicts.not_relevant} not relevant
        {verdicts.last_update && <span className="muted"> · last weight update {day(verdicts.last_update)}</span>}</p>
      <label className="progress">{ready
        ? `${verdicts.new} new verdicts since the last update: ready to recalculate.`
        : `${verdicts.new} of ${verdicts.threshold} new verdicts before the next suggested update.`}
        <meter min={0} max={verdicts.threshold} value={Math.min(verdicts.new, verdicts.threshold)}/>
      </label>
      <div className="actions">
        <Button variant={ready ? 'default' : 'outline'} disabled={busy || working || !verdicts.total}
          onClick={() => act('/api/weights/propose')}>
          {status.computing ? 'Calculating…' : 'Calculate new weights'}</Button>
        {!verdicts.total && <span className="muted">Save a verdict on a judgment (Overview → Your assessment) first.</span>}
        {status.computing && <span className="muted" role="status">This takes up to a minute.</span>}
      </div>
    </section>

    {status.rescoring && <p className="notice" role="status">Rescoring the queue with the new weights…</p>}
    {!status.rescoring && status.rescore.error && <p className="notice warning" role="alert">{status.rescore.error}</p>}
    {!status.rescoring && !status.rescore.error && status.rescore.scored !== null &&
      <p className="notice" role="status">Queue rescored with the new weights ({status.rescore.scored.toLocaleString('en')} judgments).</p>}

    {proposal?.status === 'failed' && <div className="notice warning" role="alert">{proposal.error}</div>}
    {pending && <section className="panel case proposal" aria-labelledby="proposal-title">
      <div className="case-body">
        <h2 id="proposal-title">Proposed weights</h2>
        <p className="muted">Calculated {day(proposal.created_at)} from Suva’s {proposal.data!.training.spreadsheet_picks} past
          picks and {proposal.data!.verdicts.used} of your verdicts.</p>
        {proposal.rules_changed && <p className="notice warning" role="alert"><code>rules/rules.yaml</code> was edited after
          this calculation. Calculate again before accepting.</p>}
        <ProposalView data={proposal.data!} follow={follow}/>
        <div className="actions decision">
          <Button disabled={busy || working || proposal.rules_changed}
            onClick={() => act('/api/weights/accept', {id: proposal.id})}>Accept new weights</Button>
          <Button variant="outline" disabled={busy || working}
            onClick={() => act('/api/weights/discard', {id: proposal.id})}>Discard</Button>
          <span className="muted">Accepting rescores the queue. The previous numbers stay in the git history of rules.yaml.</span>
        </div>
      </div>
    </section>}

    {history.length > 0 && <section className="weights-history" aria-labelledby="history-title">
      <h2 id="history-title">Previous updates</h2>
      <ul>{history.map(item => <li key={item.id}>{day(item.decided_at)}: {item.verdicts} verdicts used · agreed
        with {item.agreement.current} → {item.agreement.proposed} of {item.agreement.verdicts} · minimum
        points {points(item.floor.current)} → {points(item.floor.proposed)}</li>)}</ul>
    </section>}
  </div>;
}
