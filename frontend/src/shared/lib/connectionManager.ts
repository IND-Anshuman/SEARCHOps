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

const WS_BASE = import.meta.env.VITE_API_BASE_URL?.replace(/^http/, 'ws') ?? 'ws://localhost:8000';
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
  executionStore.setConnectionStatus('disconnected');
}

function _openSocket(jobId: string): void {
  const url = `${WS_BASE}/ws/research/${jobId}`;
  const ws = new WebSocket(url);
  conn.ws = ws;

  executionStore.setConnectionStatus('connecting');

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
