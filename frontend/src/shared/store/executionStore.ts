/**
 * executionStore — internal mutable singleton state.
 *
 * This module holds state and provides mutation methods.
 * It is NOT a React hook. Components access it via useExecutionStore().
 * ConnectionManager also calls it to apply WS updates.
 *
 * Separation of concerns:
 *   executionStore   → owns STATE and mutations
 *   connectionManager → owns WEBSOCKET lifecycle
 *   useExecutionStore → React bridge (subscribe + read)
 */

import {
  ResearchJob,
  LangGraphNode,
  LangGraphEdge,
  KnowledgeEntity,
  KnowledgeEdge,
  VectorChunk,
  RedisEventLog,
  ResearchReport,
  EvidenceGap,
} from '../types/workbench';
import type { ResearchConfig } from '../types/researchConfig';
import {
  normalizeJob,
  normalizeNodes,
  normalizeEntities,
  normalizeEdges,
  normalizeChunks,
  normalizeLogs,
  normalizeReport,
} from '../lib/normalize';

import { obj, str, num } from '../lib/safe';

import { API_BASE, apiFetch } from '../lib/api';

export { API_BASE };

const jsonHeaders = () => ({ 'Content-Type': 'application/json' });

export interface ExecutionState {
  job: ResearchJob;
  nodes: LangGraphNode[];
  edges: LangGraphEdge[];
  entities: KnowledgeEntity[];
  graphEdges: KnowledgeEdge[];
  chunks: VectorChunk[];
  logs: RedisEventLog[];
  report: ResearchReport | null;
  gaps: EvidenceGap[];
  connectionStatus: 'connected' | 'connecting' | 'disconnected';
}

const _initialJob: ResearchJob = {
  id: '',
  topic: '',
  status: 'idle',
  depth: 'standard',
  tokenBudget: 150000,
  tokenUsed: 0,
  costBudget: 5.0,
  costCurrent: 0.0,
  startTime: '',
};

let _state: ExecutionState = {
  job: { ..._initialJob },
  nodes: [],
  edges: [],
  entities: [],
  graphEdges: [],
  chunks: [],
  logs: [],
  report: null,
  gaps: [],
  connectionStatus: 'disconnected',
};

const _listeners = new Set<() => void>();

function _emit(): void {
  _listeners.forEach(fn => fn());
}

export const executionStore = {
  // ── Subscriptions (used by useExecutionStore hook) ─────────────────────
  subscribe(listener: () => void): () => void {
    _listeners.add(listener);
    return () => _listeners.delete(listener);
  },

  getSnapshot(): ExecutionState {
    return _state;
  },

  // ── Read helpers (used by ConnectionManager) ───────────────────────────
  getJob(): ResearchJob {
    return _state.job;
  },

  // ── Mutations ──────────────────────────────────────────────────────────
  setConnectionStatus(status: ExecutionState['connectionStatus']): void {
    _state = { ..._state, connectionStatus: status };
    _emit();
  },

  reset(jobId: string, query: string, depth: ResearchJob['depth']): void {
    _state = {
      job: { ..._initialJob, id: jobId, topic: query, status: 'running', depth, startTime: new Date().toISOString() },
      nodes: [],
      edges: [],
      entities: [],
      graphEdges: [],
      chunks: [],
      logs: [],
      report: null,
      gaps: [],
      connectionStatus: 'connecting',
    };
    _emit();
  },

  applyUpdate(data: Record<string, unknown>): void {
    // A malformed frame must not be able to blank the UI. If it is not an
    // object we cannot read, drop it rather than throwing inside the store.
    if (data === null || typeof data !== 'object' || Array.isArray(data)) return;

    // Guard: only apply updates for the active job
    if (data.job_id && data.job_id !== _state.job.id) return;

    // Every field is coerced, so no view is ever handed an absent one. This
    // is what makes a partial WS frame survivable.
    const job = normalizeJob({
      ..._state.job,
      status: (data.status as ResearchJob['status']) ?? _state.job.status,
      token_used: data.token_used,
      cost_current: data.cost_current,
      progress: data.progress,
      completed_at: data.completed_at,
      query: data.query ?? _state.job.topic,
      depth: data.depth ?? _state.job.depth,
    });

    const rawNodes = (data.nodes as unknown[] | undefined) ?? _state.nodes;
    const nodes: LangGraphNode[] = normalizeNodes(rawNodes);

    const edges: LangGraphEdge[] = [];
    for (let i = 0; i < nodes.length - 1; i++) {
      edges.push({
        id: `e_${nodes[i].id}_${nodes[i + 1].id}`,
        source: nodes[i].id,
        target: nodes[i + 1].id,
        animated: nodes[i].status === 'completed' && nodes[i + 1].status === 'running',
      });
    }

    const entities: KnowledgeEntity[] = data.entities
      ? normalizeEntities(data.entities)
      : _state.entities;

    const graphEdges: KnowledgeEdge[] = data.relations
      ? normalizeEdges(data.relations)
      : _state.graphEdges;

    const chunks: VectorChunk[] = data.chunks
      ? normalizeChunks(data.chunks)
      : _state.chunks;

    const logs: RedisEventLog[] = data.logs ? normalizeLogs(data.logs) : _state.logs;

    // Build report from final_report if present
    let report = _state.report;
    const built = normalizeReport(_state.job.id, _state.job.topic, data, _state.job.completedAt);
    if (built) report = built;

    const rawGaps = (data.evidence_gaps ?? data.gaps) as unknown[];
    const gaps: EvidenceGap[] = Array.isArray(rawGaps)
      ? rawGaps.map(g => {
          const go = obj(g);
          return {
            description: str(go.description),
            targetQuery: str(go.targetQuery || go.target_query),
            priority: num(go.priority, 1),
          };
        })
      : _state.gaps;

    _state = { ..._state, job, nodes, edges, entities, graphEdges, chunks, logs, report, gaps };
    _emit();
  },

  hydrateFromRest(jobId: string, jobData: Record<string, unknown>): void {
    const d = obj(jobData);
    // Read the whole payload through the normalisers so the hydrated state is
    // identical in shape to the live-updated state.
    const job = normalizeJob({
      ...d,
      job_id: jobId,
      id: jobId,
      query: d.query,
      created_at: d.created_at,
      start_time: d.start_time,
      status: d.status,
    });

    const rawGaps = (d.evidence_gaps ?? d.gaps) as unknown[];
    const gaps: EvidenceGap[] = Array.isArray(rawGaps)
      ? rawGaps.map(g => {
          const go = obj(g);
          return {
            description: str(go.description),
            targetQuery: str(go.targetQuery || go.target_query),
            priority: num(go.priority, 1),
          };
        })
      : [];

    _state = {
      ..._state,
      job,
      nodes: normalizeNodes(d.nodes),
      edges: [],
      entities: normalizeEntities(d.entities),
      graphEdges: normalizeEdges(d.relations),
      chunks: normalizeChunks(d.chunks),
      logs: normalizeLogs(d.logs),
      gaps,
    };

    const report = normalizeReport(jobId, job.topic, d, job.completedAt);
    _state = { ..._state, report };

    // Derive the connecting edges the same way applyUpdate does.
    const edges: LangGraphEdge[] = [];
    for (let i = 0; i < _state.nodes.length - 1; i++) {
      edges.push({
        id: `e_${_state.nodes[i].id}_${_state.nodes[i + 1].id}`,
        source: _state.nodes[i].id,
        target: _state.nodes[i + 1].id,
        animated: false,
      });
    }
    _state = { ..._state, edges };

    _emit();
  },

  // ── API actions ────────────────────────────────────────────────────────
  async startResearch(
    query: string,
    depth: ResearchJob['depth'],
    config?: ResearchConfig,
  ): Promise<string> {
    let mappedDepth: 'shallow' | 'standard' | 'deep' = 'standard';
    if (depth === 'quick') mappedDepth = 'shallow';
    else if (depth === 'exhaustive' || depth === 'deep') mappedDepth = 'deep';

    // "Exhaustive" is deep reflection with a wider net: more sources per
    // query. It used to send exactly what "deep" sent.
    const maxSources = depth === 'exhaustive' ? 25 : depth === 'deep' ? 15 : 10;

    // The config travels as snake_case — the API schema is the contract.
    const body: Record<string, unknown> = { query, depth: mappedDepth, max_sources: maxSources };
    if (config) {
      body.config = {
        max_pages: config.maxPages,
        max_depth: config.maxDepth,
        max_content_chars: config.maxContentChars,
        verify_sampling_rate: config.verifySamplingRate,
        max_report_sections: config.maxReportSections,
        scrape_budget: config.scrapeBudget,
        target_pages: config.targetPages,
        report_theme: config.reportTheme,
        report_type: config.reportType,
        report_structure_prompt: config.reportStructurePrompt,
      };
    }
    body.target_pages = config?.targetPages ?? 12;
    body.report_theme = config?.reportTheme ?? 'default';
    if (config?.reportType) body.report_type = config.reportType;
    if (config?.reportStructurePrompt) body.report_structure_prompt = config.reportStructurePrompt;

    const resp = await apiFetch(`${API_BASE}/api/v1/research/`, {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      // The server explains WHY a config was refused (usually pages > budget).
      // Surface that rather than a bare status code, or the user cannot fix it.
      const detail = await resp.json().catch(() => null) as { detail?: unknown } | null;
      const msg = typeof detail?.detail === 'string' ? detail.detail : `HTTP ${resp.status} starting research`;
      throw new Error(msg);
    }

    const data = await resp.json() as { job_id: string };
    const jobId = data.job_id;
    localStorage.setItem('searchops:active_job_id', jobId);
    return jobId;
  },

  async loadJob(jobId: string): Promise<void> {
    try {
      const resp = await apiFetch(`${API_BASE}/api/v1/research/${jobId}`, {
        headers: jsonHeaders(),
      });
      if (!resp.ok) {
        // A job that no longer exists is not an error the user needs a stack
        // trace for — drop the stale id and leave the workspace empty.
        localStorage.removeItem('searchops:active_job_id');
        return;
      }
      const jobData = await resp.json() as Record<string, unknown>;
      executionStore.hydrateFromRest(jobId, jobData);
    } catch (err) {
      // The backend being unreachable must not blank the workspace: keep
      // whatever is on screen and report it in the console.
      console.error('[executionStore] loadJob failed:', err);
    }
  },
};
