export type Language = 'de' | 'fr';
type Result<T> = {version: string | null; data: Partial<T>; error: string | null; approved: string | null; active: boolean};
export type QueueRow = {
  key: string; docket: string; title: string; publication_date: string | null; decision_date: string | null;
  score: number | null; status: string; issue?: string | null;
};
export type GlossaryWarning = {found: string; expected: string};
type Draft = {summary: string; quotes: string[]; glossary_warnings: GlossaryWarning[]};
export type TriageFeedback = {status: string; reason: string; rule_points: number | null; saved_at: string};
export type FiredRule = {id: string; points: number; explanation: string};
export type Judgment = Omit<QueueRow, 'issue'> & {
  date_source: string | null; source_url: string | null; decision: string | null; full_text: string; language: string | null;
  band: 'high' | 'check' | 'low' | null; fired: FiredRule[]; score_current: boolean;
  triage_feedback?: TriageFeedback | null;
  de: Result<Draft>;
  fr: Result<Draft>;
};
export type Briefing = {key?: string; active?: boolean; configured?: boolean; ready?: boolean;
  error?: string; script?: string; excerpts?: string[]};
export type Dashboard = {
  rows: QueueRow[]; selected: Judgment | null; count: number; total: number;
  scored: number; shortlist: number; stale: number; unknown_publication: number;
  window: [string, string]; dates: [string, string] | null; model: string;
  page: number; lang: Language; audio: Briefing; active: boolean;
};
export type Action = (endpoint: string, body: FormData, onError?: (message: string) => void) => Promise<boolean>;

export function fields(values: Record<string, string>) {
  const form = new FormData();
  for (const [key, value] of Object.entries(values)) form.set(key, value);
  return form;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `2026-09-25` -> `25 Sep 2026`: fixed three-letter months (the en-GB locale writes "Sept"). */
export function displayDate(value: string | null) {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return value || 'Unknown';
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

/** Keep the official title when its subject cannot be separated confidently. */
export function judgmentSubject(title: string) {
  return title.split(/,\s+(?=(?:Beschwerde|recours|ricorso|reclamo)\b)/i, 1)[0].trim() || title;
}

/** Omit a separate signature block from the preview; the original text stays intact. */
export function operativeText(decision: string) {
  const parts = decision.trim().split(/\n{2,}/);
  const signature = parts.findIndex((part, index) => index > 0 &&
    /^\p{Lu}[\p{L} .'-]*, (?:den |le |il )?\d{1,2}\.? \p{L}+ \d{4}\.?$/u.test(part.trim()));
  return (signature < 0 ? parts : parts.slice(0, signature)).join('\n\n');
}

export type Facet = {value: string; count: number; label?: string};
export type LibraryHit = {
  key: string; docket: string; title: string; language: string; decision_date: string | null;
  approved_at: string; snippet: string; statutes: string[]; outcome: string[];
  topics: {id: string; explanation: string}[];
};
export type LibraryPage = {hits: LibraryHit[]; total: number;
  facets: {statute: Facet[]; topic: Facet[]; outcome: Facet[]; language: Facet[]}};
export type LibraryAnswer = {answer: string | null; message?: string;
  cited: {key: string; language: string; snippet: string}[]};
export type CaseSearchHit = {
  key: string; docket: string; title: string; decision_date: string | null; status: string; relevance: number;
  matches: {kind: string; value: string}[]; highlights: string[];
};
