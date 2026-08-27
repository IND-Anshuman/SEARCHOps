import React, { useState } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { 
  Bot, 
  Send, 
  Play, 
  Search,
  Zap,
  Globe,
  Database
} from 'lucide-react';

export const AIChatWorkspace: React.FC = () => {
  const { job, logs, startResearch } = useExecutionStore();
  const { setActiveView } = useWorkbenchStore();
  
  const [topicInput, setTopicInput] = useState('');
  const [depth, setDepth] = useState<'quick' | 'standard' | 'deep' | 'exhaustive'>('standard');
  const [submitting, setSubmitting] = useState(false);

  const handleStartResearch = async () => {
    if (!topicInput.trim() || submitting) return;
    setSubmitting(true);
    try {
      await startResearch(topicInput, depth);
      // Automatically switch to live execution graph on trigger
      setActiveView('execution');
    } catch (e) {
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  const getLogPayloadString = (payload: any) => {
    try {
      if (typeof payload === 'string') return payload;
      return JSON.stringify(payload);
    } catch {
      return '';
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-slate-950 text-xs font-mono select-none overflow-hidden">
      {/* Top Header / Topic Configuration Panel */}
      <div className="bg-slate-900 border-b border-slate-800 p-4 flex flex-col space-y-3 shrink-0">
        <div className="flex items-center space-x-3">
          <Search className="w-5 h-5 text-emerald-400 shrink-0" />
          <h2 className="text-slate-200 font-semibold font-sans text-sm tracking-tight">Autonomous AI Research Engine</h2>
        </div>
        
        <div className="flex flex-col md:flex-row gap-3">
          {/* Query input */}
          <input 
            type="text" 
            value={topicInput}
            onChange={(e) => setTopicInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleStartResearch();
              }
            }}
            placeholder="Type your enterprise intelligence research question (e.g. 'Analyze LangGraph cyclical state preservation and Neo4j scale bottlenecks')..."
            className="flex-1 bg-slate-950 border border-slate-800 focus:border-emerald-500/50 rounded px-3 py-2 text-slate-100 placeholder-slate-650 focus:outline-none text-sm font-sans"
            disabled={job.status === 'running'}
          />

          {/* Depth selection */}
          <div className="flex items-center space-x-1.5 bg-slate-950 border border-slate-800 p-1 rounded">
            {(['quick', 'standard', 'deep', 'exhaustive'] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDepth(d)}
                className={`px-2.5 py-1 rounded text-[10px] uppercase font-bold transition ${
                  depth === d 
                    ? 'bg-slate-800 text-emerald-400 border border-slate-700' 
                    : 'text-slate-500 hover:text-slate-350'
                }`}
                disabled={job.status === 'running'}
              >
                {d}
              </button>
            ))}
          </div>

          {/* Start button */}
          <button 
            onClick={handleStartResearch}
            className="bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-bold px-4 py-2 rounded flex items-center space-x-1.5 transition shrink-0 cursor-pointer text-sm font-sans"
            disabled={job.status === 'running' || submitting || !topicInput.trim()}
          >
            <Play className="w-4 h-4 fill-current" />
            <span>{submitting ? 'DISPATCHING...' : 'RUN RESEARCH'}</span>
          </button>
        </div>
      </div>

      {/* Main logs display / active state stream */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-950 font-mono">
        {job.status === 'idle' ? (
          <div className="h-full flex flex-col items-center justify-center space-y-3 p-8 border border-dashed border-slate-800/80 rounded-lg m-4">
            <Bot className="w-8 h-8 text-slate-600" />
            <div className="text-center max-w-sm">
              <h3 className="text-slate-400 font-bold font-sans text-xs">Awaiting Research Initiation</h3>
              <p className="text-slate-600 text-[11px] leading-relaxed mt-1 font-sans">
                Enter your query above and click "RUN RESEARCH". The planner will compile the LangGraph execution layout and query dense search vector databases and Neo4j Graph databases live.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3.5">
            <div className="flex items-center space-x-2 bg-slate-900/60 p-2.5 rounded border border-slate-850">
              <Bot className="w-4 h-4 text-emerald-400" />
              <span className="font-bold text-slate-200 uppercase text-[10px]">Planner Agent Telemetry Logs ({job.status})</span>
            </div>

            <div className="space-y-2">
              {logs.map((log) => (
                <div key={log.id} className="bg-slate-900 border border-slate-800 rounded p-3 flex items-start space-x-3">
                  <div className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${
                    log.level === 'error' ? 'bg-rose-500' :
                    log.level === 'warn' ? 'bg-amber-500' :
                    log.level === 'success' ? 'bg-emerald-500' : 'bg-cyan-500'
                  }`} />
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center justify-between text-slate-500 text-[10px]">
                      <span className="font-bold text-slate-400">{log.eventType}</span>
                      <span>{log.timestamp}</span>
                    </div>
                    <pre className="text-slate-200 leading-relaxed text-xs whitespace-pre-wrap font-sans">
                      {getLogPayloadString(log.payload)}
                    </pre>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
export default AIChatWorkspace;
