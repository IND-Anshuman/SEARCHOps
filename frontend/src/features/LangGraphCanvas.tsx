import React, { useState } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { money, num, str } from '../shared/lib/safe';
import {
  GitFork,
  ArrowRight,
  CircleAlert,
  Loader2,
  Check,
} from 'lucide-react';
import { StatusMarkGlyph } from '../shared/components/StatusMark';

/** A node has no real latency until it has actually run. */
function hasRun(status: string): boolean {
  return status === 'completed' || status === 'failed' || status === 'retrying';
}

/**
 * Pipeline view.
 *
 * Previously every node was a wide bordered card with a monospace all-caps
 * label, a coloured status pill and a "Template: x.jinja2" line — a dense
 * debug dump rather than a pipeline you can read. This reads as a sequence:
 * state on the left, name, then real measurements only where they exist.
 */
export const LangGraphCanvas: React.FC = () => {
  const { nodes, job } = useExecutionStore();
  const { inspectItem } = useWorkbenchStore();

  const completed = nodes.filter(n => n.status === 'completed').length;
  const failed = nodes.filter(n => n.status === 'failed').length;

  return (
    <div className="flex-1 flex flex-col h-full bg-paper text-ink overflow-hidden">
      <div className="bg-paper-raised border-b border-rule px-4 py-3 shrink-0 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-label font-medium text-ink">Pipeline</h2>
          {str(job.id) ? (
            <p className="text-micro text-ink-faint font-mono truncate mt-0.5">{str(job.id)}</p>
          ) : null}
        </div>
        {nodes.length > 0 && (
          <p className="text-micro text-ink-faint font-mono shrink-0">
            {completed}/{nodes.length} complete{failed > 0 ? ` · ${failed} failed` : ''}
          </p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {nodes.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto py-12">
            <p className="text-label font-medium text-ink">No pipeline running</p>
            <p className="text-micro text-ink-muted mt-1.5 leading-relaxed">
              Start a research run and each stage will appear here as it completes.
            </p>
          </div>
        ) : (
          <ol className="max-w-2xl space-y-1">
            {nodes.map((node, index) => {
              const ran = hasRun(node.status);
              const isCompleted = node.status === 'completed';
              const isFailed = node.status === 'failed';
              const isRunning = node.status === 'running';

              return (
                <li key={node.id} className="relative">
                  {/* Connector between stages */}
                  {index < nodes.length - 1 && (
                    <span className="absolute left-[11px] top-8 bottom-0 w-px bg-rule" aria-hidden="true" />
                  )}

                  <button
                    type="button"
                    onClick={() => inspectItem('node', node)}
                    className={`w-full flex items-center gap-3 rounded-md px-2 py-2.5 text-left transition-colors border ${
                      isRunning
                        ? 'bg-paper-inset border-ink/40'
                        : isFailed
                        ? 'bg-paper-inset border-rule-strong hover:border-ink'
                        : 'bg-paper-raised border-transparent hover:border-rule'
                    }`}
                  >
                    {/* The node's state, as a printed-form mark: a filled
                        square for a stage that ran, a half for one that is
                        running, an empty one for a failure, and a bare rule
                        for a stage that has not started. */}
                    <span
                      className={`relative z-10 w-[22px] h-[22px] flex items-center justify-center shrink-0 border ${
                        isRunning
                          ? 'bg-paper-inset border-ink/40 text-ink'
                          : isCompleted
                          ? 'bg-paper-inset border-rule-strong text-ink'
                          : isFailed
                          ? 'bg-paper-inset border-ink text-ink'
                          : 'bg-paper border-rule text-ink-faint'
                      }`}
                    >
                      {isRunning ? (
                        <StatusMarkGlyph status="dotted" className="text-ink" />
                      ) : isCompleted ? (
                        <StatusMarkGlyph status="filled" className="text-ink" />
                      ) : isFailed ? (
                        <StatusMarkGlyph status="empty" className="text-ink" />
                      ) : (
                        <GitFork className="w-3 h-3" />
                      )}
                    </span>

                    <span className="flex-1 min-w-0">
                      <span className={`block text-label ${isRunning ? 'text-ink font-medium' : 'text-ink-muted'}`}>
                        {str(node.label) || str(node.id)}
                      </span>
                      <span className="block text-micro text-ink-faint capitalize">{str(node.type)}</span>
                    </span>

                    {ran && (
                      <span className="text-right shrink-0">
                        <span className="block text-micro text-ink-muted font-mono">
                          {num(node.latencyMs)} ms
                        </span>
                        <span className="block text-micro text-ink-faint font-mono">
                          ${money(node.tokenCost)}
                        </span>
                      </span>
                    )}

                    <span
                      className={`text-micro font-mono px-1.5 py-0.5 rounded-sm shrink-0 capitalize border ${
                        isRunning
                          ? 'bg-paper-inset text-ink border-ink/40'
                          : isCompleted
                          ? 'bg-paper-inset text-ink border-rule-strong'
                          : isFailed
                          ? 'bg-paper-inset text-ink border-ink'
                          : 'bg-paper text-ink-faint border-rule'
                      }`}
                    >
                      {node.status}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
};
export default LangGraphCanvas;
