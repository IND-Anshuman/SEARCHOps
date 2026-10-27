/**
 * ConnectionManager — singleton WebSocket connection lifecycle manager.
 *
 * Responsibilities:
 *   - Maintain exactly ONE active WebSocket per job
 *   - Prevent duplicate connections when multiple components mount
 *   - Exponential backoff reconnect (max 5 attempts, 30s cap)
 *   - Heartbeat detection (ping/pong, 30s interval)
 *   - Clean disconnection and timer cleanup
 *
 * This is NOT a React hook. It is a plain module singleton.
 * Components access it through useExecutionStore, which calls connect/disconnect.
 */

import { executionStore } from '../store/executionStore';
import { getSessionToken, wsBase } from './api';

const HEARTBEAT_INTERVAL = 30_000; // 30s
const HEARTBEAT_TIMEOUT = 10_000;  // 10s to receive pong
const MAX_RETRIES = 5;
const TERMINAL_STATUSES = new Set(['completed', 'failed']);

interface ConnectionState {
  ws: WebSocket | null;
  jobId: string | null;
  retryCount: number;
  retryTimer: ReturnType<typeof setTimeout> | null;
  pingInterval: ReturnType<typeof setInterval> | null;
  pongTimer: ReturnType<typeof setTimeout> | null;
}

const conn: ConnectionState = {
  ws: null,
  jobId: null,
  retryCount: 0,
  retryTimer: null,
  pingInterval: null,
  pongTimer: null,
};

// Bumped on every open/disconnect, so a socket whose token fetch finishes
// after the user moved on is never opened.
let openSeq = 0;

function clearTimers(): void {
  if (conn.retryTimer) { clearTimeout(conn.retryTimer); conn.retryTimer = null; }
  if (conn.pingInterval) { clearInterval(conn.pingInterval); conn.pingInterval = null; }
  if (conn.pongTimer) { clearTimeout(conn.pongTimer); conn.pongTimer = null; }
}

export function connect(jobId: string): void {
  // Guard: already connected to this job
  if (conn.jobId === jobId && conn.ws?.readyState === WebSocket.OPEN) return;
  // Guard: connecting to this job
  if (conn.jobId === jobId && conn.ws?.readyState === WebSocket.CONNECTING) return;

  disconnect();
  conn.jobId = jobId;
  conn.retryCount = 0;
  _openSocket(jobId);
}

export function disconnect(): void {
  clearTimers();
  if (conn.ws) {
    conn.ws.onclose = null; // prevent reconnect on explicit disconnect
    conn.ws.close();
    conn.ws = null;
  }
  conn.jobId = null;
  conn.retryCount = 0;
  openSeq += 1;
  executionStore.setConnectionStatus('disconnected');
}

function _openSocket(jobId: string): void {
  const seq = ++openSeq;
  executionStore.setConnectionStatus('connecting');
  // Browsers cannot set headers on a WebSocket, so the Clerk session token
  // travels as a subprotocol. The server answers with "searchops" and never
  // echoes the token.
  void getSessionToken().then((token) => {
    if (seq !== openSeq || conn.jobId !== jobId) return;
    _attachSocket(jobId, token);
  });
}

function _attachSocket(jobId: string, token: string | null): void {
  const url = `${wsBase()}/ws/research/${jobId}`;
  const ws = new WebSocket(url, token ? ['searchops', `bearer.${token}`] : undefined);
  conn.ws = ws;

  ws.onopen = () => {
    conn.retryCount = 0;
    executionStore.setConnectionStatus('connected');
    _startHeartbeat(ws, jobId);
  };

  ws.onmessage = (event: MessageEvent) => {
    try {
      const data = JSON.parse(event.data as string);
      if (data.type === 'ping') {
        // Backend sent ping — acknowledge
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'pong' }));
        return;
      }
      if (data.type === 'pong') {
        // Clear pong timeout on receipt
        if (conn.pongTimer) { clearTimeout(conn.pongTimer); conn.pongTimer = null; }
        return;
      }
      // Normal state update
      executionStore.applyUpdate(data);
      if (TERMINAL_STATUSES.has(data.status)) {
        clearTimers();
        executionStore.setConnectionStatus('disconnected');
      }
    } catch (e) {
      console.error('[ConnectionManager] Failed to parse WS message:', e);
    }
  };

  ws.onerror = () => {
    // onclose will fire after onerror — reconnect logic is there
    console.warn('[ConnectionManager] WebSocket error', jobId);
  };

  ws.onclose = () => {
    clearTimers();
    executionStore.setConnectionStatus('disconnected');
    const { status } = executionStore.getJob();
    if ((status === 'running' || status === 'pending') && conn.retryCount < MAX_RETRIES) {
      const backoffMs = Math.min(Math.pow(2, conn.retryCount) * 1000 + Math.random() * 500, 30_000);
      console.warn(`[ConnectionManager] Reconnecting in ${(backoffMs / 1000).toFixed(1)}s (attempt ${conn.retryCount + 1}/${MAX_RETRIES})`);
      conn.retryCount++;
      conn.retryTimer = setTimeout(() => _openSocket(jobId), backoffMs);
    }
  };
}

function _startHeartbeat(ws: WebSocket, jobId: string): void {
  conn.pingInterval = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'ping', job_id: jobId }));
    // Expect pong within 10s or consider connection dead
    conn.pongTimer = setTimeout(() => {
      console.warn('[ConnectionManager] Pong timeout — closing dead socket');
      ws.close();
    }, HEARTBEAT_TIMEOUT);
  }, HEARTBEAT_INTERVAL);
}
