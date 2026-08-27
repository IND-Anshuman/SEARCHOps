import React from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { 
  X, 
  Copy, 
  Check, 
  Database, 
  Cpu, 
  FileText, 
  ExternalLink 
} from 'lucide-react';

export const RightInspector: React.FC = () => {
  const { isInspectorOpen, closeInspector, inspectorSelection } = useWorkbenchStore();
  const [copied, setCopied] = React.useState(false);

  if (!isInspectorOpen || inspectorSelection.type === 'none') {
    return null;
  }

  const { type, data } = inspectorSelection;

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <aside className="w-80 bg-slate-950 border-l border-slate-800 flex flex-col h-full z-20 text-xs font-mono select-text shrink-0">
      {/* Inspector Header */}
      <div className="h-10 px-3 border-b border-slate-800 flex items-center justify-between bg-slate-900/80">
        <div className="flex items-center space-x-2 text-slate-200">
          <span className="font-semibold uppercase tracking-wider text-[11px] text-emerald-400">
            {type} Inspector
          </span>
        </div>
        <button 
          onClick={closeInspector}
          className="p-1 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded transition"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Inspector Content Scrollable */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* LANGGRAPH NODE INSPECTOR */}
        {type === 'node' && data && (
          <div className="space-y-4">
            <div className="bg-slate-900 border border-slate-800 p-2.5 rounded space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-slate-500 font-bold uppercase text-[9px]">{data.type}</span>
                <span className={`px-1.5 py-0.2 rounded text-[9px] uppercase font-bold ${
                  data.status === 'completed' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' :
                  data.status === 'running' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 animate-pulse' :
                  'bg-slate-800 text-slate-400'
                }`}>
                  {data.status}
                </span>
              </div>
              <h4 className="text-slate-100 font-semibold text-sm font-sans">{data.label}</h4>
              <p className="text-slate-500 text-[10px] truncate">ID: {data.id}</p>
            </div>

            {/* Performance Stats */}
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-slate-900 border border-slate-800 p-2 rounded">
                <span className="text-slate-500 text-[9px] uppercase">LATENCY</span>
                <p className="text-slate-200 font-bold text-xs">{data.latencyMs} ms</p>
              </div>
              <div className="bg-slate-900 border border-slate-800 p-2 rounded">
                <span className="text-slate-500 text-[9px] uppercase">TOKEN COST</span>
                <p className="text-emerald-400 font-bold text-xs">${data.tokenCost.toFixed(3)}</p>
              </div>
            </div>

            {/* Prompt Template */}
            {data.prompt && (
              <div className="space-y-1">
                <div className="flex items-center justify-between text-slate-400 text-[10px]">
                  <span>JINJA PROMPT TEMPLATE</span>
                  <button onClick={() => handleCopy(data.prompt)} className="hover:text-slate-200">
                    {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                  </button>
                </div>
                <pre className="bg-slate-900 border border-slate-800 p-2 rounded text-slate-350 whitespace-pre-wrap text-[10px] max-h-36 overflow-y-auto font-mono">
                  {data.prompt}
                </pre>
              </div>
            )}

            {/* Input Payload */}
            <div className="space-y-1">
              <span className="text-slate-500 text-[10px] uppercase">Input State Delta</span>
              <pre className="bg-slate-900 border border-slate-800 p-2 rounded text-cyan-400 text-[10px] overflow-x-auto max-h-40 overflow-y-auto">
                {JSON.stringify(data.inputPayload || {}, null, 2)}
              </pre>
            </div>

            {/* Output Payload */}
            <div className="space-y-1">
              <span className="text-slate-500 text-[10px] uppercase">Output State Delta</span>
              <pre className="bg-slate-900 border border-slate-800 p-2 rounded text-emerald-400 text-[10px] overflow-x-auto max-h-40 overflow-y-auto">
                {JSON.stringify(data.outputPayload || {}, null, 2)}
              </pre>
            </div>
          </div>
        )}

        {/* KNOWLEDGE ENTITY INSPECTOR */}
        {type === 'entity' && data && (
          <div className="space-y-4 font-sans text-xs">
            <div className="bg-slate-900 border border-slate-800 p-2.5 rounded space-y-1 font-mono">
              <span className="text-slate-450 text-[9px] font-bold block truncate">{data.canonicalId}</span>
              <h4 className="text-slate-100 font-semibold text-sm font-sans">{data.name}</h4>
              <span className="text-[10px] text-emerald-400 uppercase bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded font-bold">{data.type}</span>
            </div>

            <div className="bg-slate-900 border border-slate-800 p-2.5 rounded">
              <span className="text-slate-500 font-mono text-[9px] uppercase">Cypher Summary</span>
              <p className="text-slate-300 text-xs mt-1 leading-relaxed">{data.summary || 'No entity summary generated.'}</p>
            </div>
          </div>
        )}

        {/* VECTOR CHUNK INSPECTOR */}
        {type === 'chunk' && data && (
          <div className="space-y-4">
            <div className="bg-slate-900 border border-slate-800 p-2.5 rounded space-y-1">
              <span className="text-slate-500 font-bold text-[9px] block">CHUNK #{data.id}</span>
              <h4 className="text-slate-100 font-semibold text-xs font-sans line-clamp-2">{data.documentTitle}</h4>
              <a href={data.sourceUrl} target="_blank" rel="noreferrer" className="text-cyan-400 hover:underline flex items-center space-x-1 text-[10px] font-mono mt-1 select-all">
                <span className="truncate max-w-[200px]">{data.sourceUrl}</span>
                <ExternalLink className="w-3 h-3 shrink-0" />
              </a>
            </div>

            <div className="bg-slate-900 border border-slate-800 p-2 rounded">
              <span className="text-slate-500 text-[9px] uppercase">COSINE SCORE</span>
              <p className="text-emerald-400 font-bold text-sm font-mono">{(data.similarityScore * 100).toFixed(1)}%</p>
            </div>

            <div className="space-y-1">
              <span className="text-slate-500 text-[9px] uppercase">Retrieved Content Chunk</span>
              <p className="bg-slate-900 border border-slate-800 p-2 rounded text-slate-300 text-xs leading-relaxed font-sans">
                {data.chunkPreview}
              </p>
            </div>
          </div>
        )}

        {/* REPORT INSPECTOR */}
        {type === 'report' && data && (
          <div className="space-y-4 font-sans text-xs">
            <div className="bg-slate-900 border border-slate-800 p-2.5 rounded space-y-1">
              <span className="text-slate-500 font-mono text-[9px] block">REPORT TELEMETRY</span>
              <h4 className="text-slate-100 font-semibold text-xs truncate">{data.title}</h4>
              <p className="text-slate-400 text-[10px] font-mono">Gen time: {data.generatedAt}</p>
            </div>

            <div className="bg-slate-900 border border-slate-800 p-2 rounded font-mono">
              <div className="flex items-center justify-between text-[11px] border-b border-slate-800 pb-1.5 mb-1.5">
                <span>Sources Scraped:</span>
                <span className="text-cyan-400 font-bold">{data.sourcesCount}</span>
              </div>
              <div className="flex items-center justify-between text-[11px]">
                <span>KG Triples Mined:</span>
                <span className="text-purple-400 font-bold">{data.entitiesCount}</span>
              </div>
            </div>
          </div>
        )}

        {/* CITATION INSPECTOR */}
        {type === 'citation' && data && (
          <div className="space-y-3 font-sans text-xs">
            <span className="text-slate-500 font-mono text-[9px] block">SOURCE CITATION</span>
            <div className="bg-slate-900 border border-slate-800 p-2.5 rounded space-y-2">
              <a href={data} target="_blank" rel="noreferrer" className="text-cyan-400 hover:underline flex items-center space-x-1 select-all break-all">
                <span>{data}</span>
                <ExternalLink className="w-3.5 h-3.5 shrink-0" />
              </a>
            </div>
          </div>
        )}

        {/* EXECUTION PROMPT INSPECTOR */}
        {type === 'prompt' && data && (
          <div className="space-y-3 font-sans text-xs">
            <div className="flex items-center justify-between text-slate-500 font-mono text-[9px]">
              <span>PROMPT TEXT</span>
              <button onClick={() => handleCopy(data)} className="hover:text-slate-200">
                {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
              </button>
            </div>
            <pre className="bg-slate-900 border border-slate-800 p-2 rounded text-slate-300 text-[10px] font-mono whitespace-pre-wrap max-h-96 overflow-y-auto">
              {data}
            </pre>
          </div>
        )}
      </div>
    </aside>
  );
};
