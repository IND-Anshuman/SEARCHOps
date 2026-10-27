import React, { useState, useMemo } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { num, str } from '../shared/lib/safe';
import { Search, ExternalLink, FileText } from 'lucide-react';

/**
 * Evidence passages: the page text the run actually read, chunked.
 *
 * Every card is a real passage from a fetched page. The relevance figure is
 * the passage's BM25 score against the question, relative to the best
 * passage; "cited" marks passages a claim's verified quote came from. Cited
 * passages sort first.
 */
export const VectorSearchInspector: React.FC = () => {
  const { chunks } = useExecutionStore();
  const { inspectItem } = useWorkbenchStore();
  const [filterText, setFilterText] = useState('');

  const q = filterText.trim().toLowerCase();

  const filteredChunks = useMemo(() => chunks.filter(c =>
    str(c.documentTitle).toLowerCase().includes(q) ||
    str(c.chunkPreview).toLowerCase().includes(q) ||
    str(c.sourceUrl).toLowerCase().includes(q)
  ), [chunks, q]);

  return (
    <div className="flex-1 flex flex-col h-full bg-paper text-ink overflow-hidden">
      <div className="bg-paper-raised border-b border-rule px-4 py-3 shrink-0 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 flex-1 max-w-md">
          <Search className="w-4 h-4 text-ink-faint shrink-0" />
          <input
            type="text"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            placeholder="Filter passages by text or source"
            className="w-full bg-paper border border-rule rounded-md px-2.5 py-1.5 text-label text-ink placeholder:text-ink-faint focus:outline-none focus:border-ink focus:ring-1 focus:ring-ink"
          />
        </div>
        <p className="text-micro text-ink-faint shrink-0">
          {filteredChunks.length} of {chunks.length} passages · {chunks.filter((c) => c.cited).length} cited
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {chunks.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto py-12">
            <FileText className="w-5 h-5 text-ink-faint mb-2" />
            <p className="text-label font-medium text-ink">No passages read yet</p>
            <p className="text-micro text-ink-muted mt-1.5 leading-relaxed">
              Passages from the pages a run reads will appear here, with the ones its claims cite first.
            </p>
          </div>
        ) : filteredChunks.length === 0 ? (
          <p className="text-micro text-ink-muted py-8 text-center">No passages match that filter.</p>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {filteredChunks.map((chunk) => (
              <button
                key={chunk.id}
                type="button"
                onClick={() => inspectItem('chunk', chunk)}
                className="text-left bg-paper-raised border border-rule rounded-md p-3 transition-colors hover:border-rule-strong"
              >
                <p className="text-label text-ink leading-snug line-clamp-2">
                  {chunk.cited ? (
                    <span className="mr-1.5 px-1 py-px border border-rule-strong rounded-sm text-micro font-mono text-ink">
                      cited
                    </span>
                  ) : null}
                  {str(chunk.documentTitle) || 'Untitled document'}
                </p>
                <p className="text-micro text-ink-muted leading-relaxed mt-2 line-clamp-3">
                  {str(chunk.chunkPreview) || 'No preview text.'}
                </p>
                <div className="flex items-center justify-between gap-3 mt-3 pt-2.5 border-t border-rule text-micro text-ink-faint font-mono">
                  <span>{num(chunk.tokenCount).toLocaleString()} tokens</span>
                  <span className="flex items-center gap-1 min-w-0">
                    <span className="shrink-0" title="BM25 relevance to the question, relative to the best passage">
                      relevance {num(chunk.similarityScore).toFixed(2)}
                    </span>
                    {str(chunk.sourceUrl) && (
                      <a
                        href={str(chunk.sourceUrl)}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-ink hover:underline flex items-center gap-0.5 shrink-0"
                      >
                        <span className="truncate max-w-[120px]">{str(chunk.sourceUrl).replace(/^https?:\/\//, '')}</span>
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
export default VectorSearchInspector;
