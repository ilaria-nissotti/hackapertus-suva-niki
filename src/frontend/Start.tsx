import {useState} from 'react';
import {Button, Input} from './components/ui/controls';

const REMEMBERED = 'suva-reviewer';  // this browser's last reviewer, to fill the form in next time

type Reviewer = {name: string; initials: string; function: string};

function remembered(): Reviewer {
  try {
    return {name: '', initials: '', function: '', ...JSON.parse(localStorage.getItem(REMEMBERED) ?? '{}')};
  } catch {
    return {name: '', initials: '', function: ''};
  }
}

/** Who is reviewing: entered once at the start (no account, no password); approvals are signed with it. */
export function Start() {
  const [reviewer, setReviewer] = useState(remembered);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const field = (key: keyof Reviewer) => ({
    value: reviewer[key], onChange: (event: {target: {value: string}}) => setReviewer({...reviewer, [key]: event.target.value}),
  });
  return <main className="start">
    <form className="start-form" onSubmit={async event => {
      event.preventDefault();
      setBusy(true);
      setError('');
      try {
        const response = await fetch('/api/session', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(reviewer)});
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.message || 'Could not start. Try again.');
        localStorage.setItem(REMEMBERED, JSON.stringify(body.reviewer));
        location.assign('/');
      } catch (failure) {
        console.warn('start_failed', {reason: String(failure)});
        setError(failure instanceof Error ? failure.message : 'Could not start. Try again.');
        setBusy(false);
      }
    }}>
      <h1>Who is reviewing?</h1>
      <p className="muted">Summaries you approve are signed with this name, as in Suva's exports:
        “Frei Thomas (FRT), Rechtsanwalt”.</p>
      <label>Name<Input autoComplete="name" required minLength={2} maxLength={80} placeholder="Frei Thomas"
        autoFocus {...field('name')}/></label>
      <label>Initials<Input required maxLength={6} placeholder="FRT" pattern="[A-Za-z0-9]{1,6}"
        title="1 to 6 letters or digits" {...field('initials')}/></label>
      <label>Function (optional)<Input maxLength={80} placeholder="Rechtsanwalt" {...field('function')}/></label>
      {error && <p role="alert">{error}</p>}
      <Button type="submit" disabled={busy}>{busy ? 'Starting…' : 'Start reviewing'}</Button>
    </form>
  </main>;
}
