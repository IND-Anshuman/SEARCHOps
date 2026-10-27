/**
 * Every backend call goes through `apiFetch`.
 *
 * People sign in with Clerk. Clerk's session token is short-lived (about a
 * minute) and the SDK refreshes it; `apiFetch` asks for a fresh one per
 * request and sends it as `Authorization: Bearer`. The API verifies it against
 * the Clerk instance's public keys. No credential is compiled into this bundle:
 * the publishable key is public by design.
 *
 * The token getter is registered by `<ClerkTokenBridge>` inside
 * `<ClerkProvider>`, so non-React code (stores, the socket manager) can call
 * the API too.
 */

// Unset: local development against a backend on :8000. Set to an empty string
// ("same origin"): the API serves this app itself, as in the Docker image.
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

/** Origin for WebSocket connections, derived from API_BASE (or the page, when same-origin). */
export function wsBase(): string {
  if (API_BASE) return API_BASE.replace(/^http/, 'ws');
  return `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;
}

export const UNAUTHORIZED_EVENT = 'searchops:unauthorized';

type TokenGetter = (options?: { skipCache?: boolean }) => Promise<string | null>;

let tokenGetter: TokenGetter | null = null;

export function setTokenGetter(getter: TokenGetter | null): void {
  tokenGetter = getter;
}

/** A current Clerk session token, or null when signed out. */
export async function getSessionToken(skipCache = false): Promise<string | null> {
  if (!tokenGetter) return null;
  try {
    return await tokenGetter(skipCache ? { skipCache: true } : undefined);
  } catch {
    return null;
  }
}

async function send(url: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  let res = await send(url, init, await getSessionToken());
  if (res.status === 401 && tokenGetter) {
    // The cached token may have expired in flight; retry once with a new one.
    res = await send(url, init, await getSessionToken(true));
  }
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  return res;
}
