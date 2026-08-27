import React, { useState } from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { 
  Terminal as TerminalIcon, 
  ChevronDown, 
  ChevronUp, 
  Filter,
  Maximize2,
  Minimize2
} from 'lucide-react';

export const BottomConsole: React.FC = () => {
  const { 
    isConsoleOpen, 
    toggleConsole, 
    activeConsoleTab, 
    setActiveConsoleTab 
  } = useWorkbenchStore();

  const { logs } = useExecutionStore();
  const [isFullscreen, setIsFullscreen] = useState(false);

  if (!isConsoleOpen) {
    return (
      <div className="h-7 bg-slate-950 border-t border-slate-800 px-3 flex items-center justify-between text-xs font-mono select-none z-20">
        <button 
          onClick={toggleConsole}
          className="flex items-center space-x-2 text-slate-400 hover:text-slate-200"
        >
          <TerminalIcon className="w-3.5 h-3.5 text-emerald-400" />
          <span className="font-semibold text-[11px] uppercase">Console Console</span>
          <span className="text-slate-500 text-[10px]">(Collapsed - Ctrl+~ to expand)</span>
        </button>
        <button onClick={toggleConsole} className="text-slate-400 hover:text-slate-200">
          <ChevronUp className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  // Filter logs based on tabs
  const getTabLogs = () => {
    switch (activeConsoleTab) {
      case 'events':
        return logs.filter(l => l.eventType.startsWith("NODE_") || l.eventType.startsWith("JOB_"));
      case 'errors':
        return logs.filter(l => l.level === 'error');
      case 'websocket':
        return logs.filter(l => l.stream.includes("websocket") || l.eventType.includes("WS_") || l.eventType === "JOB_STARTED" || l.eventType === "JOB_COMPLETED");
      case 'network':
        return logs.filter(l => l.stream.includes("network") || l.eventType.includes("HTTP_") || l.eventType === "JOB_STARTED");
      case 'workers':
        return logs.filter(l => l.stream.includes("worker") || l.eventType.includes("WORKER_"));
      case 'logs':
      default:
        return logs;
    }
  };

  const currentTabLogs = getTabLogs();

  return (
    <div className={`bg-slate-950 border-t border-slate-800 flex flex-col transition-all duration-150 z-20 ${
      isFullscreen ? 'h-96' : 'h-48'
    }`}>
      {/* Header Tabs */}
      <div className="h-8 bg-slate-900 border-b border-slate-800 px-3 flex items-center justify-between text-xs font-mono select-none">
        <div className="flex items-center space-x-1">
          {([
            { id: 'logs', label: 'LOGS', color: 'text-emerald-400' },
            { id: 'events', label: 'EVENTS', color: 'text-cyan-400' },
            { id: 'errors', label: 'ERRORS', color: 'text-rose-400' },
            { id: 'websocket', label: 'WEBSOCKET', color: 'text-indigo-400' },
            { id: 'network', label: 'NETWORK', color: 'text-amber-400' },
            { id: 'workers', label: 'WORKER OUTPUT', color: 'text-purple-400' }
          ] as const).map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveConsoleTab(tab.id)}
              className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-t border-t border-x text-[10px] transition ${
                activeConsoleTab === tab.id
                  ? `bg-slate-950 ${tab.color} border-slate-800 font-semibold`
                  : 'text-slate-400 hover:text-slate-200 border-transparent bg-slate-900/40'
              }`}
            >
              <span>{tab.label}</span>
            </button>
          ))}
        </div>

        {/* Console Controls */}
        <div className="flex items-center space-x-2">
          <button 
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="p-1 text-slate-450 hover:text-slate-200 rounded"
            title="Toggle Expand"
          >
            {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
          <button 
            onClick={toggleConsole}
            className="p-1 text-slate-450 hover:text-slate-200 rounded"
            title="Collapse"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Stream Area */}
      <div className="flex-1 overflow-y-auto p-2 bg-slate-950 text-[11px] font-mono leading-relaxed space-y-0.5">
        {currentTabLogs.length === 0 ? (
          <div className="text-slate-650 italic p-4 text-center">No logs streaming in this channel yet.</div>
        ) : (
          currentTabLogs.map((log) => (
            <div key={log.id} className="flex items-start space-x-2 hover:bg-slate-900/60 p-0.5 rounded">
              <span className="text-slate-500 shrink-0 select-none">[{log.timestamp}]</span>
              <span className={`shrink-0 font-bold px-1 rounded text-[9px] uppercase select-none ${
                log.level === 'info' ? 'bg-emerald-500/10 text-emerald-400' :
                log.level === 'warn' ? 'bg-amber-500/10 text-amber-400' :
                log.level === 'error' ? 'bg-rose-500/10 text-rose-450' :
                'bg-cyan-500/10 text-cyan-400'
              }`}>
                {log.eventType}
              </span>
              <span className="text-slate-300 overflow-x-auto whitespace-pre-wrap font-sans text-xs">
                {JSON.stringify(log.payload)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
