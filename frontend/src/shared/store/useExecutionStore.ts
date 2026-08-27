/**
 * useExecutionStore — React hook bridge to executionStore.
 *
 * This hook does exactly two things:
 *   1. Subscribes to executionStore state changes and re-renders on update
 *   2. Exposes startResearch and loadJob actions
 *
 * It does NOT:
 *   - Own WebSocket connections (that is ConnectionManager's job)
 *   - Read localStorage (that is App.tsx's job, on single mount)
 *   - Create side effects on mount (no useEffect with loadJob here)
 *
 * This eliminates the duplicate-connection mount race (audit finding F-06).
 */
import { useSyncExternalStore } from 'react';
import { executionStore, ExecutionState } from './executionStore';
import { connect, disconnect } from '../lib/connectionManager';
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

export type { ExecutionState };

export interface ExecutionStoreHook extends ExecutionState {
  startResearch: (query: string, depth: ResearchJob['depth']) => Promise<string>;
  loadJob: (jobId: string) => Promise<void>;
}

export function useExecutionStore(): ExecutionStoreHook {
  const state = useSyncExternalStore(
    executionStore.subscribe,
    executionStore.getSnapshot,
  );

  return {
    ...state,

    startResearch: async (query: string, depth: ResearchJob['depth']): Promise<string> => {
      executionStore.setConnectionStatus('connecting');
      try {
        const jobId = await executionStore.startResearch(query, depth);
        executionStore.reset(jobId, query, depth);
        connect(jobId);
        return jobId;
      } catch (err) {
        executionStore.setConnectionStatus('disconnected');
        throw err;
      }
    },

    loadJob: async (jobId: string): Promise<void> => {
      executionStore.setConnectionStatus('connecting');
      await executionStore.loadJob(jobId);
      const job = executionStore.getJob();
      if (job.status === 'running' || job.status === 'pending') {
        connect(jobId);
      } else {
        executionStore.setConnectionStatus('disconnected');
      }
    },
  };
}
