import React, { useState, useMemo } from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { str } from '../shared/lib/safe';
import { Terminal as TerminalIcon, ChevronDown, ChevronUp, Maximize2, Minimize2 } from 'lucide-react';
import { StatusMarkGlyph } from '../shared/components/StatusMark';

/**
 * Bottom console.
 *
 * The tab row used a different accent colour per tab (emerald, cyan, rose,
 * indigo, amber, purple), which made the console look like a rainbow rather
 * than a set of filters. Tabs are now neutral with a single accent for the
 * active one, and log levels use the semantic palette.
 */
const TABS = [
  { id: 'logs', label: 'All' },
  { id: 'events', label: 'Events' },
  { id: 'errors', label: 'Errors' },
  { id: 'websocket', label: 'Stream' },
  { id: 'network', label: 'Network' },
  { id: 'workers', label: 'Workers' },
] as const;

type TabId = (typeof TABS)[number]['id'];

export const BottomConsole: React.FC = () => {
  const {
    isConsoleOpen,
    toggleConsole,
    activeConsoleTab,
    setActiveConsoleTab,
  } = useWorkbenchStore();

  const { logs } = useExecutionStore();
  const { job } = useExecutionStore();
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [levelFilter, setLevelFilter] = useState<'all' | 'error' | 'warn' | 'info'>('all');

  // The console is a bench instrument, not furniture. It belongs on screen
  // while a run is live; once the run is over its output is history, and a
  // permanent band across the foot of the window is the last piece of the
  // dashboard template this product had left.
  const runIsLive = job.status === 'running' || job.status === 'pending';

  const getTabLogs = (tab: TabId) => {
    switch (tab) {
      case 'events':
        return logs.filter(l => str(l.eventType).startsWith('NODE_') || str(l.eventType).startsWith('JOB_'));
      case 'errors':
        return logs.filter(l => l.level === 'error');
      case 'websocket':
        return logs.filter(l => str(l.stream).includes('websocket') || str(l.eventType).includes('WS_') || str(l.eventType) === 'JOB_STARTED' || str(l.eventType) === 'JOB_COMPLETED');
      case 'network':
        return logs.filter(l => str(l.stream).includes('network') || str(l.eventType).includes('HTTP_') || str(l.eventType) === 'JOB_STARTED');
      case 'workers':
        return logs.filter(l => str(l.stream).includes('worker') || str(l.eventType).includes('WORKER_'));
      default:
        return logs;
    }
  };

  const currentTabLogs = useMemo(() => {
    const base = getTabLogs(activeConsoleTab);
    if (levelFilter === 'all') return base;
    return base.filter(l => l.level === levelFilter);
  }, [logs, activeConsoleTab, levelFilter]);

  if (!runIsLive) return null;

  if (!isConsoleOpen) {
    // The collapsed console is a status line, not an empty drawer: it says
    // how much was logged and whether any of it is an error, so the strip at
    // the foot of the page is worth the pixels it takes.
    const errors = logs.filter(l => l.level === 'error').length;
    const warnings = logs.filter(l => l.level === 'warn').length;

    return (
      <div className="h-8 shrink-0 bg-paper-raised border-t border-rule px-5 flex items-center justify-between z-20">
        <button
          type="button"
          onClick={toggleConsole}
          className="flex items-center gap-2.5 text-micro text-ink-faint hover:text-ink transition-colors"
        >
          <TerminalIcon className="w-3 h-3" />
          <span className="folio">Console</span>
          {logs.length > 0 && (
            <span className="text-micro tnum text-ink-ghost">
              {logs.length} {logs.length === 1 ? 'event' : 'events'}
            </span>
          )}
        </button>

        <div className="flex items-center gap-3">
          {warnings > 0 && (
            <span className="text-micro text-ink-muted flex items-center gap-1.5">
              <StatusMarkGlyph status="half" className="text-ink" />
              <span className="tnum">{warnings}</span>
              {warnings === 1 ? 'warning' : 'warnings'}
            </span>
          )}
          {errors > 0 && (
            <span className="text-micro text-ink-muted flex items-center gap-1.5">
              <StatusMarkGlyph status="empty" className="text-ink" />
              <span className="tnum">{errors}</span>
              {errors === 1 ? 'error' : 'errors'}
            </span>
          )}
          <button
            type="button"
            onClick={toggleConsole}
            className="p-1 text-ink-faint hover:text-ink rounded transition-colors"
            aria-label="Expand console"
          >
            <ChevronUp className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`shrink-0 bg-paper-raised border-t border-rule flex flex-col transition-[height] duration-200 ease-out-expo ${isFullscreen ? 'h-96' : 'h-52'}`}>
      <div className="h-9 px-3 flex items-center justify-between border-b border-rule">
        <div className="flex items-center gap-0.5" role="tablist" aria-label="Console channels">
          {TABS.map((tab) => {
            const active = activeConsoleTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setActiveConsoleTab(tab.id)}
                className={`px-2.5 py-1.5 rounded-md text-micro transition-colors ${
                  active ? 'bg-paper-inset text-ink' : 'text-ink-muted hover:text-ink hover:bg-paper'
                }`}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-1">
          <select
            value={levelFilter}
            onChange={e => setLevelFilter(e.target.value as 'all' | 'error' | 'warn' | 'info')}
            className="bg-paper border border-rule rounded-md text-micro text-ink-muted px-1.5 py-1 focus:outline-none focus:border-ink"
            aria-label="Filter by level"
          >
            <option value="all">All levels</option>
            <option value="error">Errors</option>
            <option value="warn">Warnings</option>
            <option value="info">Info</option>
          </select>
          <button
            type="button"
            onClick={() => setIsFullscreen(v => !v)}
            className="p-1.5 text-ink-faint hover:text-ink rounded-md hover:bg-paper transition-colors"
            aria-label={isFullscreen ? 'Collapse' : 'Expand'}
          >
            {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
          <button
            type="button"
            onClick={toggleConsole}
            className="p-1.5 text-ink-faint hover:text-ink rounded-md hover:bg-paper transition-colors"
            aria-label="Hide console"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 font-mono text-micro">
        {currentTabLogs.length === 0 ? (
          <p className="text-ink-faint py-6 text-center font-sans text-micro">
            Nothing logged in this channel yet.
          </p>
        ) : (
          <div className="space-y-0.5">
            {currentTabLogs.map((log) => (
              <div key={log.id || `${log.timestamp}-${log.eventType}`} className="flex items-start gap-2 py-0.5 hover:bg-paper rounded px-1">
                <span className="text-ink-faint shrink-0">[{str(log.timestamp)}]</span>
                <span
                  className={`shrink-0 font-medium ${
                    log.level === 'error'
                      ? 'text-ink'
                      : log.level === 'warn'
                      ? 'text-ink'
                      : 'text-ink-faint'
                  }`}
                >
                  {str(log.eventType) || 'EVENT'}
                </span>
                <span className="text-ink-muted whitespace-pre-wrap break-words font-sans">
                  {JSON.stringify(log.payload ?? {})}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
