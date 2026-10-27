/**
 * Defensive field access.
 *
 * The frontend reads a job payload that arrives over the WebSocket and over
 * REST, and whose shape changes with job age: an older job has no
 * `report_document`, a partial update has no `token_cost`, a node delta can be
 * `None`. Several views called straight into those fields — `x.toFixed(3)`,
 * `x.toLowerCase()`, `x.startsWith()` — so one absent field threw during
 * render, unmounted the whole tree (there is no error boundary), and the user
 * saw a blank screen.
 *
 * These helpers make the absent case a boring default instead of a crash.
 */

/** Number that never throws, for `.toFixed()` / arithmetic. */
export function num(value: unknown, fallback = 0): number {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** String that never throws, for `.toLowerCase()` / `.includes()`. */
export function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value == null ? fallback : String(value);
}

/** Array that never throws, for `.map()` / `.filter()` / `.length`. */
export function arr<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/** Record that never throws, for `JSON.stringify()` / key reads. */
export function obj(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Boolean-ish: true only for real true or 'true'. */
export function bool(value: unknown): boolean {
  return value === true || value === 'true' || value === 1 || value === '1';
}

/** Money display: always a finite 3-dp string. */
export function money(value: unknown, digits = 3): string {
  return num(value, 0).toFixed(digits);
}

/** Percent display from a 0..1 score. Always finite. */
export function pct(value: unknown, digits = 1): string {
  return (num(value, 0) * 100).toFixed(digits);
}

/**
 * A date a reader can read.
 *
 * The backend sends an ISO timestamp with microseconds, which is machine
 * format. On a report's colophon — the place a reader looks to know what
 * they are holding — that is the wrong register. A report is dated the way a
 * document is dated: "21 October 2026 at 17:56".
 *
 * Falls back to the raw string when it is not a parseable date, because
 * showing a slightly odd string beats showing "Invalid Date".
 */
export function dateLabel(value: unknown): string {
  const raw = str(value);
  if (!raw) return '';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  const day = parsed.getDate();
  const month = parsed.toLocaleString('en-GB', { month: 'long' });
  const year = parsed.getFullYear();
  const time = parsed.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  });
  return `${day} ${month} ${year} at ${time}`;
}

/** Read a field by either camelCase or snake_case key — the API has used both. */
export function pick(source: unknown, camel: string, snake: string, fallback = ''): string {
  const rec = obj(source);
  if (rec[camel] != null && rec[camel] !== '') return str(rec[camel], fallback);
  if (rec[snake] != null && rec[snake] !== '') return str(rec[snake], fallback);
  return fallback;
}

/** pick() for numbers, camelCase or snake_case. */
export function pickNum(source: unknown, camel: string, snake: string, fallback = 0): number {
  const rec = obj(source);
  if (rec[camel] != null && rec[camel] !== '') return num(rec[camel], fallback);
  if (rec[snake] != null && rec[snake] !== '') return num(rec[snake], fallback);
  return fallback;
}
