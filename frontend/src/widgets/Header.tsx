import React from 'react';
import { 
  useExecutionStore 
} from '../shared/store/useExecutionStore';
import { 
  Cpu, 
  Database
} from 'lucide-react';

export const Header: React.FC = () => {
  const { 
    job,
    connectionStatus
  } = useExecutionStore();

  const tokenPercent = job.tokenBudget > 0 ? Math.min(Math.round((job.tokenUsed / job.tokenBudget) * 100), 100) : 0;
  
  const getStatusColor = () => {
    switch (job.status) {
      case 'running': return 'bg-cyan-500 animate-pulse';
      case 'completed': return 'bg-emerald-500';
      case 'failed': return 'bg-rose-500';
      case 'paused': return 'bg-amber-500';
      default: return 'bg-slate-500';
    }
  };

  const getWsStatusBadge = () => {
    switch (connectionStatus) {
      case 'connected':
        return <span className="text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded font-bold font-mono">WS ONLINE</span>;
      case 'connecting':
        return <span className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded font-bold font-mono animate-pulse">WS CONNECTING</span>;
      case 'disconnected':
      default:
        return <span className="text-[10px] text-rose-400 bg-rose-500/10 border border-rose-500/20 px-2 py-0.5 rounded font-bold font-mono">WS OFFLINE</span>;
    }
  };

  return (
    <header className="h-12 bg-slate-950 border-b border-slate-800 flex items-center justify-between px-3 text-xs select-none z-30 font-mono">
      {/* Brand & Connection Status */}
      <div className="flex items-center space-x-3">
        <div className="flex items-center space-x-2 bg-slate-900 border border-slate-800 px-2 py-1 rounded">
          <Cpu className="w-4 h-4 text-emerald-400" />
          <span className="font-semibold text-slate-100 tracking-wide font-sans text-xs">SEARCHOps Console</span>
        </div>
        {getWsStatusBadge()}
      </div>

      {/* Current Active Job */}
      <div className="flex items-center space-x-2 bg-slate-900/80 border border-slate-800/80 px-3 py-1 rounded max-w-lg md:max-w-xl truncate">
        <span className={`w-2 h-2 rounded-full ${getStatusColor()}`} />
        <span className="text-slate-400 font-bold shrink-0">{job.id || 'NO ACTIVE JOB'}</span>
        {job.topic && (
          <>
            <span className="text-slate-600 font-sans shrink-0">|</span>
            <span className="text-slate-200 font-medium truncate font-sans text-xs">{job.topic}</span>
          </>
        )}
      </div>

      {/* Live Telemetry Gauges */}
      <div className="flex items-center space-x-5">
        {/* Token Usage Gauge */}
        <div className="flex items-center space-x-2.5">
          <Database className="w-3.5 h-3.5 text-slate-450 shrink-0" />
          <span className="text-slate-400 text-[10px]">TOKENS:</span>
          <div className="w-16 h-1.5 bg-slate-900 border border-slate-800 rounded-full overflow-hidden shrink-0">
            <div 
              className={`h-full ${tokenPercent > 85 ? 'bg-amber-500' : 'bg-emerald-500'}`}
              style={{ width: `${tokenPercent}%` }}
            />
          </div>
          <span className="text-slate-350 text-[10px] font-bold">{job.tokenUsed.toLocaleString()} / {job.tokenBudget.toLocaleString()}</span>
        </div>

        {/* Estimated Cost Gauge */}
        <div className="flex items-center space-x-2">
          <span className="text-slate-400 text-[10px] uppercase">EST. COST:</span>
          <span className="text-emerald-400 font-bold text-xs font-mono">${job.costCurrent.toFixed(3)}</span>
        </div>
      </div>
    </header>
  );
};

export default Header;
