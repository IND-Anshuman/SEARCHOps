import React, { useState } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { 
  Database, 
  Search, 
  ExternalLink 
} from 'lucide-react';

export const VectorSearchInspector: React.FC = () => {
  const { chunks, job } = useExecutionStore();
  const { inspectItem } = useWorkbenchStore();
  const [filterText, setFilterText] = useState('');

  const filteredChunks = chunks.filter(c => 
    c.documentTitle.toLowerCase().includes(filterText.toLowerCase()) ||
    c.chunkPreview.toLowerCase().includes(filterText.toLowerCase()) ||
    c.sourceUrl.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <div className="flex-1 flex flex-col h-full bg-slate-950 text-xs font-mono select-none overflow-hidden">
      {/* Top Filter Bar */}
      <div className="bg-slate-900 border-b border-slate-800 p-3 flex items-center justify-between shrink-0">
        <div className="flex items-center space-x-2 flex-1 max-w-md">
          <Search className="w-4 h-4 text-slate-500 shrink-0" />
          <input
            type="text"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            placeholder="Filter vector chunks by text, URL or document..."
            className="w-full bg-slate-950 border border-slate-850 focus:border-emerald-500/50 rounded px-2.5 py-1 text-slate-200 placeholder-slate-600 focus:outline-none"
          />
        </div>
        
        <div className="text-[10px] text-slate-500">
          Mined Chunks: {filteredChunks.length} of {chunks.length} total
        </div>
      </div>

      {/* Main Chunks Container */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5">
        {chunks.length === 0 ? (
          <div className="h-60 flex flex-col items-center justify-center border border-dashed border-slate-855 rounded-lg text-slate-600 bg-slate-900/10">
            <Database className="w-8 h-8 text-slate-750 mb-2" />
            <p className="font-sans text-xs">No vector embeddings found.</p>
            <p className="font-sans text-[10px] text-slate-650 mt-1">Submit a research question to see dense vector hits processed from Firecrawl and Qdrant.</p>
          </div>
        ) : filteredChunks.length === 0 ? (
          <div className="p-8 text-center text-slate-500 italic">No matches found for your filter.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filteredChunks.map((chunk) => {
              const scorePercent = Math.round(chunk.similarityScore * 100);
              
              return (
                <div
                  key={chunk.id}
                  onClick={() => inspectItem('chunk', chunk)}
                  className="bg-slate-900/60 border border-slate-850 hover:border-slate-700/80 rounded-lg p-3.5 transition cursor-pointer flex flex-col justify-between group"
                >
                  <div>
                    {/* Title & Cosine Score */}
                    <div className="flex items-start justify-between space-x-4 mb-2.5">
                      <h4 className="font-bold text-slate-200 text-xs font-sans group-hover:text-emerald-400 transition leading-tight line-clamp-2">
                        {chunk.documentTitle}
                      </h4>
                      <span className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/25 text-[10px] font-bold px-2 py-0.5 rounded shrink-0 font-mono">
                        {scorePercent}% MATCH
                      </span>
                    </div>

                    {/* Content Preview */}
                    <p className="text-slate-400 text-xs leading-relaxed font-sans line-clamp-3 mb-3 select-none">
                      {chunk.chunkPreview}
                    </p>
                  </div>

                  {/* Metadata & Anchor Citation */}
                  <div className="border-t border-slate-850/80 pt-2.5 flex items-center justify-between text-[9px] text-slate-500 font-mono">
                    <span>Tokens: <strong className="text-slate-350">{chunk.tokenCount}</strong></span>
                    <a
                      href={chunk.sourceUrl}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-cyan-400 hover:underline flex items-center space-x-1 shrink-0"
                    >
                      <span className="truncate max-w-[130px]">{chunk.sourceUrl}</span>
                      <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
export default VectorSearchInspector;
