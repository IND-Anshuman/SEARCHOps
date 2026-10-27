import React from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { num } from '../shared/lib/safe';
import { StatusMarkGlyph } from '../shared/components/StatusMark';

/**
 * Run status banner.
 *
 * This used to be `return null`, so nothing ever told the user what happened
 * to their run. It states the real end state, and - importantly - the honest
 * provenance of a degraded report: when the model declined or returned nothing,
 * the report was built from the sources that were found, and must say so rather
 * than appearing as a normal model-written report.
 *
 * A status, so it wears a status colour and nothing else.
 */
export const AISuggestionBanner: React.FC = () => {
  const { setActiveView } = useWorkbenchStore();
  const { job, report } = useExecutionStore();

  const done = job.status === 'completed' || job.status === 'failed';
  if (!done) return null;

  if (job.status === 'failed') {
    return (
      <div
        role="alert"
        className="shrink-0 bg-paper-inset border-b border-rule-strong px-8 py-2 flex items-center justify-between gap-4"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <StatusMarkGlyph status="empty" className="text-ink shrink-0" />
          <span className="text-label text-ink truncate">
            The run stopped with an error, so no report was produced.
          </span>
        </div>
        <button
          type="button"
          onClick={() => setActiveView('chat')}
          className="text-micro text-ink-muted hover:text-ink shrink-0 transition-colors"
        >
          See log
        </button>
      </div>
    );
  }

  const degraded = Boolean(report?.quality && report.quality !== 'ok');
  const sources = num(report?.sourcesCount);

  // A finished, healthy run needs no banner at all. The report is the news;
  // a strip saying "the report is ready" above the report is noise, and it
  // pushed the page down by a line every time. Only a degraded run — where
  // the report is NOT what it claims to be — earns a banner.
  if (!degraded) return null;

  return (
    <div
      role="status"
      className="shrink-0 bg-paper-inset border-b border-rule-strong/25 px-8 py-2 flex items-center justify-between gap-4"
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <StatusMarkGlyph status="half" className="text-ink shrink-0" />
        <span className="text-label text-ink truncate">
          Report built from {sources} source{sources === 1 ? '' : 's'} — see the note in the report for why.
        </span>
      </div>
      <button
        type="button"
        onClick={() => setActiveView('reports')}
        className="text-micro text-ink-muted hover:text-ink shrink-0 transition-colors"
      >
        Open report
      </button>
    </div>
  );
};

export default AISuggestionBanner;
