/**
 * Normalisation of raw job payloads into the shapes the views render.
 *
 * One reason the UI broke so often: the same data arrived in two different
 * shapes. The WebSocket/REST payload uses snake_case (`latency_ms`,
 * `token_cost`, `document_title`), while the backend's graph endpoint uses
 * camelCase (`canonicalId`, `documentTitle`). Views then called straight into
 * whichever shape they assumed, and one mismatch threw during render.
 *
 * Every field is coerced here, once, so a view can never be handed an absent
 * field. Nothing downstream needs a guard — but the guards in `safe.ts` remain
 * as a net for anything that reaches a view without passing through here.
 */

import { num, str, arr, obj } from './safe';
import type {
  ResearchJob,
  LangGraphNode,
  KnowledgeEntity,
  KnowledgeEdge,
  VectorChunk,
  RedisEventLog,
  ResearchReport,
} from '../types/workbench';

export function normalizeJob(raw: unknown): ResearchJob {
  const d = obj(raw);
  return {
    id: str(d.job_id ?? d.id),
    topic: str(d.query ?? d.topic),
    status: str(d.status, 'idle') as ResearchJob['status'],
    depth: str(d.depth, 'standard') as ResearchJob['depth'],
    model: d.model ? str(d.model) : undefined,
    agentProfile: d.agent_profile ? str(d.agent_profile) : undefined,
    tokenBudget: num(d.token_budget, 150000),
    tokenUsed: num(d.token_used),
    costBudget: num(d.cost_budget, 5),
    costCurrent: num(d.cost_current),
    startTime: str(d.start_time ?? d.created_at),
    completedAt: d.completed_at ? str(d.completed_at) : undefined,
    progress: num(d.progress),
  };
}

/** A node delta may be `null` (LangGraph surfaces an empty delta that way). */
export function normalizeNode(raw: unknown): LangGraphNode {
  const d = obj(raw);
  return {
    id: str(d.id),
    label: str(d.label, str(d.id)),
    type: str(d.type, 'planner') as LangGraphNode['type'],
    status: str(d.status, 'pending') as LangGraphNode['status'],
    latencyMs: num(d.latency_ms ?? d.latencyMs),
    tokenCost: num(d.token_cost ?? d.tokenCost),
    retries: num(d.retries),
    prompt: str(d.prompt),
    inputPayload: obj(d.input_payload ?? d.inputPayload),
    outputPayload: obj(d.output_payload ?? d.outputPayload),
    timestamp: str(d.timestamp),
  };
}

export function normalizeNodes(raw: unknown): LangGraphNode[] {
  return arr(raw).map(normalizeNode);
}

export function normalizeEntity(raw: unknown): KnowledgeEntity {
  const d = obj(raw);
  // The graph endpoint serialises `type`; the job payload uses `entity_type`.
  const type = str(d.type) || str(d.entity_type, 'technology');
  const summary = str(d.summary) || str(d.description);
  return {
    id: str(d.id),
    canonicalId: str(d.canonicalId) || str(d.canonical_id),
    name: str(d.name, str(d.id)),
    type,
    summary,
  };
}

export function normalizeEntities(raw: unknown): KnowledgeEntity[] {
  return arr(raw).map(normalizeEntity);
}

export function normalizeEdge(raw: unknown, idx = 0): KnowledgeEdge {
  const d = obj(raw);
  return {
    id: str(d.id) || `edge_${idx}`,
    source: str(d.source) || str(d.source_canonical_id) || str(d.source_id),
    target: str(d.target) || str(d.target_canonical_id) || str(d.target_id),
    relation_type: str(d.relation_type ?? d.relationType, 'RELATED_TO'),
    description: str(d.description),
  };
}

export function normalizeEdges(raw: unknown): KnowledgeEdge[] {
  return arr(raw).map(normalizeEdge);
}

export function normalizeChunk(raw: unknown): VectorChunk {
  const d = obj(raw);
  return {
    id: str(d.id),
    documentTitle: str(d.documentTitle) || str(d.document_title),
    sourceUrl: str(d.sourceUrl) || str(d.source_url),
    similarityScore: num(d.similarityScore) || num(d.similarity_score),
    tokenCount: num(d.tokenCount) || num(d.token_count),
    chunkPreview: str(d.chunkPreview) || str(d.chunk_preview),
    cited: Boolean(d.cited),
  };
}

export function normalizeChunks(raw: unknown): VectorChunk[] {
  return arr(raw).map(normalizeChunk);
}

export function normalizeLog(raw: unknown): RedisEventLog {
  const d = obj(raw);
  const payload = d.payload;
  return {
    id: str(d.id),
    stream: str(d.stream),
    eventType: str(d.eventType) || str(d.event_type),
    correlationId: str(d.correlationId) || str(d.correlation_id),
    timestamp: str(d.timestamp),
    payload: payload !== null && typeof payload === 'object' && !Array.isArray(payload)
      ? (payload as Record<string, unknown>)
      : {},
    level: str(d.level, 'info') as RedisEventLog['level'],
  };
}

export function normalizeLogs(raw: unknown): RedisEventLog[] {
  return arr(raw).map(normalizeLog);
}

export function normalizeReport(
  jobId: string,
  topic: string,
  raw: unknown,
  completedAt?: string,
): ResearchReport | null {
  const d = obj(raw);
  const markdown = str(d.final_report ?? d.markdownContent);
  if (!markdown) return null;
  const quality = str(d.report_quality);
  // The writer gives the report its own title as the first H1; prefer it
  // over the raw query, which reads as a question rather than a title.
  const h1 = /^#\s+(.+)$/m.exec(markdown.split('\n').slice(0, 3).join('\n'));
  const generic = !h1 || /^research report\b/i.test(h1[1].trim());
  return {
    id: jobId,
    title: str(d.title) || (generic ? topic : h1[1].trim()),
    generatedAt: str(d.generated_at ?? d.completed_at) || completedAt || new Date().toISOString(),
    summary: `Synthesized report on: ${topic}`,
    markdownContent: markdown,
    sourcesCount: num(d.source_count ?? d.sourcesCount),
    entitiesCount: num(d.entity_count ?? d.entitiesCount),
    citations: arr(d.citations).map(c => str(c)),
    // 'ok' means the model wrote it. Anything else is a degraded state and
    // the UI must say so rather than presenting it as a normal report.
    quality: quality || 'ok',
    userMessage: str(d.report_user_message) || undefined,
  };
}
