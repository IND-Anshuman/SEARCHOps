import React, { useState } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { Play } from 'lucide-react';
import { obj, str } from '../shared/lib/safe';

import {
  DEFAULT_CONFIG,
  type ResearchConfig,
} from '../shared/types/researchConfig';
import { ResearchConfigPanel } from './ResearchConfigPanel';

/**
 * Research entry point.
 *
 * The previous version opened with an "Autonomous AI Research Engine" hero and
 * marketing copy about what the system does internally ("the planner will
 * compile the LangGraph execution layout and query dense search vector
 * databases and Neo4j Graph databases live"). That is how the product is
 * built, not what the user gets, and it is the clearest example of the
 * internal-jargon problem. The empty state now names the user's next action.
 *
 * This is the *workbench* half of the archive world: plainer than the report,
 * because a bench is a place where you do work, not a place you read. The
 * question field is the only large thing on the page, and the depth selector
 * is a set of letterspaced options rather than a segmented pill — a row of
 * choices on a form, not a slider widget.
 */
export const AIChatWorkspace: React.FC = () => {
  const { job, logs, startResearch } = useExecutionStore();
  const [config, setConfig] = useState<ResearchConfig>(DEFAULT_CONFIG);
  const { setActiveView } = useWorkbenchStore();

  const [topicInput, setTopicInput] = useState('');
  const [depth, setDepth] = useState<'quick' | 'standard' | 'deep' | 'exhaustive'>('standard');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  React.useEffect(() => {
    const prefill = sessionStorage.getItem('searchops:prefill_query');
    if (prefill) {
      setTopicInput(prefill);
      sessionStorage.removeItem('searchops:prefill_query');
    }
  }, []);

  const running = job.status === 'running' || job.status === 'pending';

  const handleStartResearch = async () => {
    const query = topicInput.trim();
    if (!query || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      await startResearch(query, depth, config);
      setActiveView('execution');
    } catch (e) {
      // The server's refusal reason (e.g. "pages > budget") is the useful
      // part. It used to be swallowed into the console, leaving the user with
      // a button that did nothing and no explanation.
      const message = e instanceof Error ? e.message : String(e);
      console.error('[AIChatWorkspace] research failed:', e);
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const getLogPayloadString = (payload: unknown): string => {
    try {
      if (typeof payload === 'string') return payload;
      return JSON.stringify(obj(payload));
    } catch {
      return '';
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-paper/60 text-ink overflow-hidden">
      {/* Query composer.
          The band scrolls rather than clips: the advanced-settings panel
          expands to more content than fits beside the input, and a `shrink-0`
          band with no overflow made its lower half unreachable. */}
      <div className="bg-paper-raised sheet-raised border-b border-rule px-8 py-6 shrink-0 max-h-[60vh] overflow-y-auto shadow-sm">
        <label htmlFor="research-query" className="block font-display text-display-sm text-ink mb-4">
          What do you want to know?
        </label>

        <div className="flex flex-col md:flex-row gap-3 items-stretch">
          <input
            id="research-query"
            type="text"
            value={topicInput}
            onChange={(e) => setTopicInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleStartResearch();
              }
            }}
            placeholder="Ask a question worth reading an answer to"
            className="flex-1 bg-paper intaglio-well border border-rule rounded-lg px-4 py-3 text-body-lg text-ink placeholder:text-ink-faint focus:outline-none focus:border-rule-strong transition-all"
            disabled={running}
          />

          {/* Depth: tactile pills with subtle sheen */}
          <div
            role="radiogroup"
            aria-label="Research depth"
            className="flex items-center gap-2 self-stretch px-1"
          >
            {(['quick', 'standard', 'deep', 'exhaustive'] as const).map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={depth === d}
                onClick={() => setDepth(d)}
                disabled={running}
                className={`text-micro font-medium uppercase tracking-[0.12em] px-2.5 py-1.5 rounded-md border transition-all tactile-btn ${
                  depth === d
                    ? 'text-ink border-rule-strong bg-paper-inset intaglio-well font-semibold'
                    : 'text-ink-faint border-transparent hover:text-ink-muted hover:bg-paper-raised/60'
                }`}
              >
                {d}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={handleStartResearch}
            disabled={running || submitting || !topicInput.trim()}
            className="text-paper-raised font-medium px-5 py-3 rounded-lg flex items-center justify-center gap-2 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-body bg-ink hover:bg-ink-muted border border-ink shadow-sm tactile-btn hover:shadow"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>{submitting ? 'Starting…' : 'Start research'}</span>
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="mt-4 border border-rule-strong bg-paper-inset px-4 py-2.5 rounded-md"
          >
            <p className="text-label text-ink leading-relaxed">{error}</p>
            <button
              type="button"
              onClick={() => setError('')}
              className="text-micro text-ink-muted hover:text-ink mt-1 transition-colors"
            >
              dismiss
            </button>
          </div>
        )}

        <div className="mt-4">
          <ResearchConfigPanel value={config} onChange={setConfig} />
        </div>
      </div>

      {/* Run output. A finished run's log is below the fold of the composer
          and behind a rule, so the previous run never competes with the
          question the user is about to ask. */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {job.status === 'idle' ? (
          <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto py-12">
            {/* An empty sheet: an archival card with subtle 3D lift. */}
            <div
              aria-hidden="true"
              className="w-16 h-[86px] border border-rule-strong card-beveled sheet-raised relative mb-6 transition-transform hover:scale-[1.02]"
            >
              <span className="absolute left-3.5 right-3.5 top-5 h-px bg-rule" />
              <span className="absolute left-3.5 right-6 top-10 h-px bg-rule" />
              <span className="absolute left-3.5 right-5 top-[58px] h-px bg-rule" />
            </div>
            <p className="font-display text-[19px] text-ink leading-snug">No research running</p>
            <p className="text-label text-ink-muted mt-2 leading-relaxed">
              Ask a question above and start a run. You will see each stage of the
              pipeline as it completes, and the finished report when it is ready.
            </p>
          </div>
        ) : (
          <div className="max-w-[76ch]">
            <div className="folio pb-2 border-b border-rule flex items-baseline justify-between gap-3">
              <span>Run log</span>
              <span className="tnum normal-case tracking-normal text-ink-ghost">
                {logs.length} {logs.length === 1 ? 'event' : 'events'}
              </span>
            </div>

            <div className="space-y-2 pt-3">
              {logs.length === 0 ? (
                <p className="text-label text-ink-faint">Waiting for the first event…</p>
              ) : (
                logs.map((log) => (
                  <div
                    key={log.id || `${log.timestamp}-${log.eventType}`}
                    className="border-b border-rule/60 pb-2 last:border-b-0"
                  >
                    <div className="flex items-center justify-between gap-3 text-micro text-ink-muted">
                      <span className="font-medium text-ink truncate">{str(log.eventType) || 'Event'}</span>
                      <span className="font-mono shrink-0 tnum text-ink-faint">{str(log.timestamp)}</span>
                    </div>
                    <pre className="text-micro text-ink-muted leading-relaxed mt-1 whitespace-pre-wrap break-words font-mono">
                      {getLogPayloadString(log.payload)}
                    </pre>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
export default AIChatWorkspace;
