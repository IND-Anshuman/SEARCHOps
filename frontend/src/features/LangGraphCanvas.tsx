import React from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { 
  GitFork, 
  Clock, 
  Play, 
  CheckCircle2, 
  AlertTriangle,
  ArrowRight,
  Database
} from 'lucide-react';

export const LangGraphCanvas: React.FC = () => {
  const { nodes, job } = useExecutionStore();
  const { inspectItem } = useWorkbenchStore();

  return (
    <div className="flex-1 flex flex-col h-full bg-slate-950 text-xs font-mono select-none overflow-hidden">
      {/* Topology Header */}
      <div className="bg-slate-900 border-b border-slate-800 p-3 flex items-center justify-between shrink-0">
        <div className="flex items-center space-x-2">
          <GitFork className="w-4 h-4 text-cyan-400" />
          <span className="font-bold text-slate-100 uppercase text-[11px] font-sans">LangGraph Live Node State Machine</span>
        </div>
        <div className="text-[10px] text-slate-500 font-mono">
          Click any node to inspect Input payloads, prompts & Outputs in the inspector.
        </div>
      </div>

      {/* Pipeline execution canvas */}
      <div className="flex-1 p-6 overflow-auto flex flex-col items-center justify-start bg-[radial-gradient(#1e293b_1px,transparent_1px)] [background-size:18px_18px] py-10">
        <div className="w-full max-w-3xl space-y-6">
          <div className="flex items-center justify-between mb-4 border-b border-slate-850 pb-2">
            <span className="font-bold text-sm text-slate-200 font-sans">Pipeline Sequence Topology</span>
            <span className="text-slate-500 font-mono">Job: {job.id || 'N/A'} ({job.status})</span>
          </div>

          {nodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-12 border border-dashed border-slate-850 rounded-lg text-slate-655 bg-slate-950/60 mt-8">
              <GitFork className="w-8 h-8 text-slate-750 mb-2" />
              <p className="font-sans text-xs">No active execution telemetry stream found.</p>
              <p className="font-sans text-[10px] text-slate-600 mt-1">Start a research job from the Research Workspace to view real-time state changes.</p>
            </div>
          ) : (
            <div className="flex flex-col space-y-4">
              {nodes.map((node, index) => {
                const isCompleted = node.status === 'completed';
                const isRunning = node.status === 'running';
                const isRetrying = node.status === 'retrying';

                return (
                  <div key={node.id} className="flex flex-col items-center">
                    {/* Node Card */}
                    <div
                      onClick={() => inspectItem('node', node)}
                      className={`w-full max-w-xl p-3.5 rounded-lg border transition cursor-pointer relative group flex items-center justify-between ${
                        isRunning
                          ? 'bg-slate-900 border-cyan-500 shadow-md shadow-cyan-500/5 ring-1 ring-cyan-500/30'
                          : isCompleted
                          ? 'bg-slate-900/60 border-slate-800/80 hover:border-slate-700'
                          : 'bg-slate-950/40 border-slate-900 opacity-60 hover:opacity-80'
                      }`}
                    >
                      <div className="flex items-center space-x-3.5 min-w-0">
                        <div className={`p-1.5 rounded shrink-0 ${
                          isCompleted ? 'bg-emerald-500/10 text-emerald-400' :
                          isRunning ? 'bg-cyan-500/10 text-cyan-400 animate-pulse' :
                          'bg-slate-800 text-slate-500'
                        }`}>
                          <GitFork className="w-4 h-4" />
                        </div>
                        
                        <div className="min-w-0">
                          <h4 className="font-bold text-slate-200 text-xs font-sans group-hover:text-emerald-400 transition truncate">
                            {node.label}
                          </h4>
                          <div className="flex items-center space-x-2 text-[9px] text-slate-500 mt-0.5">
                            <span className="uppercase">{node.type}</span>
                            <span>•</span>
                            <span className="truncate max-w-[200px]">Template: {node.prompt}</span>
                          </div>
                        </div>
                      </div>

                      {/* Status / Telemetry badge right side */}
                      <div className="flex items-center space-x-3 shrink-0">
                        <div className="text-right text-[9px] text-slate-500 font-mono">
                          <div>{node.latencyMs > 0 ? `${node.latencyMs}ms` : '0ms'}</div>
                          <div className="text-emerald-450">${node.tokenCost.toFixed(3)}</div>
                        </div>
                        
                        <span className={`px-1.5 py-0.5 rounded text-[9px] uppercase font-bold ${
                          isCompleted ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
                          isRunning ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 animate-pulse' :
                          'bg-slate-900 text-slate-600 border border-slate-800'
                        }`}>
                          {node.status}
                        </span>
                      </div>
                    </div>

                    {/* Connecting arrow indicator between nodes */}
                    {index < nodes.length - 1 && (
                      <div className="h-6 w-px bg-slate-800 flex items-center justify-center my-0.5">
                        <ArrowRight className="w-3 h-3 text-slate-700 transform rotate-90" />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
export default LangGraphCanvas;
