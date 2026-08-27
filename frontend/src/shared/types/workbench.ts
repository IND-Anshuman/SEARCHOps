export type WorkbenchView = 
  | 'chat'
  | 'execution'
  | 'graph'
  | 'vector'
  | 'reports';

export type ViewportMode = 'operations' | 'developer' | 'graph_focus';

export interface ResearchJob {
  id: string;
  topic: string;
  status: 'idle' | 'pending' | 'planning' | 'running' | 'paused' | 'verifying' | 'completed' | 'failed';
  depth: 'quick' | 'standard' | 'deep' | 'exhaustive';
  model?: string;
  agentProfile?: string;
  tokenBudget: number;
  tokenUsed: number;
  costBudget: number;
  costCurrent: number;
  startTime: string;
  completedAt?: string;
  progress?: number;
}

export interface LangGraphNode {
  id: string;
  label: string;
  type: 'planner' | 'search' | 'scrape' | 'extract' | 'graph_rag' | 'verify' | 'report';
  status: 'pending' | 'running' | 'completed' | 'failed' | 'retrying';
  latencyMs: number;
  tokenCost: number;
  retries: number;
  prompt: string;
  inputPayload: Record<string, any>;
  outputPayload: Record<string, any>;
  timestamp: string;
}

export interface LangGraphEdge {
  id: string;
  source: string;
  target: string;
  animated?: boolean;
  label?: string;
}

export interface KnowledgeEntity {
  id: string;
  canonicalId: string;
  name: string;
  type: string;
  summary: string;
}

export interface KnowledgeEdge {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  description: string;
}

export interface VectorChunk {
  id: string;
  documentTitle: string;
  sourceUrl: string;
  similarityScore: number;
  tokenCount: number;
  chunkPreview: string;
}

export interface RedisEventLog {
  id: string;
  stream: string;
  eventType: string;
  correlationId: string;
  timestamp: string;
  payload: Record<string, any>;
  level: 'info' | 'warn' | 'error' | 'success';
}

export interface ResearchReport {
  id: string;
  title: string;
  generatedAt: string;
  summary: string;
  markdownContent: string;
  sourcesCount: number;
  entitiesCount: number;
  citations: string[];
}

export interface AISuggestion {
  id: string;
  type: 'optimization' | 'warning' | 'quality';
  message: string;
  actionText: string;
  actionPayload: any;
  dismissed?: boolean;
}
