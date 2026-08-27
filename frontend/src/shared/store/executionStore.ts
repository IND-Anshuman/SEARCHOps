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
} from '../types/workbench';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const API_KEY = import.meta.env.VITE_API_KEY ?? '';

if (!API_KEY && import.meta.env.DEV) {
  console.warn('[executionStore] VITE_API_KEY is not set. API requests will fail auth. Add it to frontend/.env.local');
}

const authHeaders = () => ({
  'Content-Type': 'application/json',
  ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
});

export interface ExecutionState {
  job: ResearchJob;
  nodes: LangGraphNode[];
  edges: LangGraphEdge[];
  entities: KnowledgeEntity[];
  graphEdges: KnowledgeEdge[];
  chunks: VectorChunk[];
  logs: RedisEventLog[];
  report: ResearchReport | null;
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
      connectionStatus: 'connecting',
    };
    _emit();
  },

  applyUpdate(data: Record<string, unknown>): void {
    // Guard: only apply updates for the active job
    if (data.job_id && data.job_id !== _state.job.id) return;

    const job: ResearchJob = {
      ..._state.job,
      status: (data.status as ResearchJob['status']) ?? _state.job.status,
      tokenUsed: (data.token_used as number) ?? _state.job.tokenUsed,
      costCurrent: (data.cost_current as number) ?? _state.job.costCurrent,
      progress: (data.progress as number) ?? _state.job.progress,
      completedAt: (data.completed_at as string) ?? _state.job.completedAt,
    };

    // Derive animated edges from node topology
    const rawNodes = (data.nodes as LangGraphNode[] | undefined) ?? _state.nodes;
    const edges: LangGraphEdge[] = [];
    for (let i = 0; i < rawNodes.length - 1; i++) {
      edges.push({
        id: `e_${rawNodes[i].id}_${rawNodes[i + 1].id}`,
        source: rawNodes[i].id,
        target: rawNodes[i + 1].id,
        animated: rawNodes[i].status === 'completed' && rawNodes[i + 1].status === 'running',
      });
    }

    // Map knowledge graph entities
    const entities: KnowledgeEntity[] = data.entities
      ? (data.entities as Array<Record<string, unknown>>).map(e => ({
          id: e.id as string,
          canonicalId: (e.canonical_id as string) ?? '',
          name: e.name as string,
          type: (e.entity_type as string) ?? 'technology',
          summary: (e.description as string) ?? '',
        }))
      : _state.entities;

    // Map knowledge graph edges
    const graphEdges: KnowledgeEdge[] = data.relations
      ? (data.relations as Array<Record<string, unknown>>).map((r, idx) => ({
          id: (r.id as string) ?? `e_${idx}`,
          source: (r.source_canonical_id as string) ?? (r.source_id as string) ?? '',
          target: (r.target_canonical_id as string) ?? (r.target_id as string) ?? '',
          relation_type: (r.relation_type as string) ?? 'RELATED_TO',
          description: (r.description as string) ?? '',
        }))
      : _state.graphEdges;

    // Map vector chunks (backend uses snake_case field names)
    const chunks: VectorChunk[] = data.chunks
      ? (data.chunks as Array<Record<string, unknown>>).map(c => ({
          id: c.id as string,
          documentTitle: (c.document_title as string) ?? (c.documentTitle as string) ?? '',
          sourceUrl: (c.source_url as string) ?? (c.sourceUrl as string) ?? '',
          similarityScore: (c.similarity_score as number) ?? (c.similarityScore as number) ?? 0,
          tokenCount: (c.token_count as number) ?? (c.tokenCount as number) ?? 0,
          chunkPreview: (c.chunk_preview as string) ?? (c.chunkPreview as string) ?? '',
        }))
      : _state.chunks;

    // Map logs
    const logs: RedisEventLog[] = data.logs
      ? (data.logs as Array<Record<string, unknown>>).map(l => ({
          id: l.id as string,
          stream: (l.stream as string) ?? '',
          eventType: (l.event_type as string) ?? (l.eventType as string) ?? '',
          correlationId: (l.correlation_id as string) ?? (l.correlationId as string) ?? '',
          timestamp: l.timestamp as string,
          payload: (l.payload as Record<string, unknown>) ?? {},
          level: (l.level as RedisEventLog['level']) ?? 'info',
        }))
      : _state.logs;

    // Build report from final_report if present
    let report = _state.report;
    if (data.final_report && typeof data.final_report === 'string' && data.final_report.length > 0) {
      report = {
        id: _state.job.id,
        title: _state.job.topic,
        generatedAt: (data.completed_at as string) ?? new Date().toISOString(),
        summary: `Synthesized report on: ${_state.job.topic}`,
        markdownContent: data.final_report,
        sourcesCount: (data.source_count as number) ?? 0,
        entitiesCount: (data.entity_count as number) ?? 0,
        citations: (data.citations as string[]) ?? [],
      };
    }

    _state = { ..._state, job, nodes: rawNodes, edges, entities, graphEdges, chunks, logs, report };
    _emit();
  },

  hydrateFromRest(jobId: string, jobData: Record<string, unknown>): void {
    const job: ResearchJob = {
      id: jobId,
      topic: (jobData.query as string) ?? '',
      status: (jobData.status as ResearchJob['status']) ?? 'idle',
      depth: (jobData.depth as ResearchJob['depth']) ?? 'standard',
      tokenBudget: (jobData.token_budget as number) ?? 150000,
      tokenUsed: (jobData.token_used as number) ?? 0,
      costBudget: (jobData.cost_budget as number) ?? 5.0,
      costCurrent: (jobData.cost_current as number) ?? 0.0,
      startTime: (jobData.created_at as string) ?? '',
      completedAt: jobData.completed_at as string | undefined,
      progress: (jobData.progress as number) ?? 0,
    };
    _state = { ..._state, job };
    _emit();
    // Apply the full data as an update to populate nodes/entities/chunks/logs/report
    executionStore.applyUpdate({ ...jobData, job_id: jobId });
  },

  // ── API actions ────────────────────────────────────────────────────────
  async startResearch(query: string, depth: ResearchJob['depth']): Promise<string> {
    let mappedDepth: 'shallow' | 'standard' | 'deep' = 'standard';
    if (depth === 'quick') mappedDepth = 'shallow';
    else if (depth === 'exhaustive' || depth === 'deep') mappedDepth = 'deep';

    const resp = await fetch(`${API_BASE}/api/v1/research/`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ query, depth: mappedDepth, max_sources: 10 }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} starting research`);

    const data = await resp.json() as { job_id: string };
    const jobId = data.job_id;
    localStorage.setItem('searchops:active_job_id', jobId);
    return jobId;
  },

  async loadJob(jobId: string): Promise<void> {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/research/${jobId}`, {
        headers: authHeaders(),
      });
      if (!resp.ok) {
        localStorage.removeItem('searchops:active_job_id');
        return;
      }
      const jobData = await resp.json() as Record<string, unknown>;
      executionStore.hydrateFromRest(jobId, jobData);
    } catch (err) {
      console.error('[executionStore] loadJob failed:', err);
    }
  },
};
