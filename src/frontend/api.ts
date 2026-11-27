export type Language = 'de' | 'fr';
type Result<T> = {version: string | null; data: Partial<T>; error: string | null; approved: string | null; active: boolean};
export type QueueRow = {
  key: string; docket: string; title: string; publication_date: string | null; decision_date: string | null;
  score: number | null; status: string; issue?: string | null;
};
export type FiredRule = {id: string; points: number; explanation: string};
export type Judgment = Omit<QueueRow, 'issue'> & {
  date_source: string | null; source_url: string | null; full_text: string;
  band: 'high' | 'check' | 'low' | null; fired: FiredRule[]; score_current: boolean;
  de: Result<{summary: string; quotes: string[]}>;
  fr: Result<{summary: string; quotes: string[]}>;
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

export function displayDate(value: string | null) {
  return value ? new Intl.DateTimeFormat('en-GB', {dateStyle: 'medium', timeZone: 'UTC'})
    .format(new Date(value + 'T00:00:00Z')) : 'Unknown';
}
