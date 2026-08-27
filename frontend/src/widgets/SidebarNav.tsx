import React from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { WorkbenchView } from '../shared/types/workbench';
import { 
  MessageSquareCode, 
  GitFork, 
  Share2, 
  Database, 
  FileText, 
  ChevronLeft,
  ChevronRight
} from 'lucide-react';

interface NavItem {
  id: WorkbenchView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string | number;
  shortcut: string;
}

export const SidebarNav: React.FC = () => {
  const { activeView, setActiveView, isSidebarOpen, toggleSidebar } = useWorkbenchStore();

  const navItems: NavItem[] = [
    { id: 'chat', label: 'Research Workspace', icon: MessageSquareCode, badge: 'LIVE', shortcut: 'Ctrl+R' },
    { id: 'execution', label: 'Live Execution', icon: GitFork, badge: 'RUNNING', shortcut: 'Ctrl+E' },
    { id: 'graph', label: 'Knowledge Graph', icon: Share2, shortcut: 'Ctrl+G' },
    { id: 'vector', label: 'Vector Search', icon: Database, shortcut: 'Ctrl+V' },
    { id: 'reports', label: 'Reports', icon: FileText, shortcut: 'Ctrl+D' },
  ];

  return (
    <aside className={`bg-slate-950 border-r border-slate-800 flex flex-col justify-between transition-all duration-200 z-20 ${
      isSidebarOpen ? 'w-56' : 'w-12'
    }`}>
      {/* Top Navigation Items */}
      <div className="flex-1 overflow-y-auto py-2">
        <div className="px-2 mb-2 flex items-center justify-between text-slate-500 font-mono text-[10px] uppercase">
          {isSidebarOpen && <span>Subsystems</span>}
          <button 
            onClick={toggleSidebar}
            className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-200 transition"
            title={isSidebarOpen ? "Collapse Sidebar (Ctrl+B)" : "Expand Sidebar (Ctrl+B)"}
          >
            {isSidebarOpen ? <ChevronLeft className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </button>
        </div>

        <nav className="space-y-0.5 px-1.5">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeView === item.id;
            
            return (
              <button
                key={item.id}
                onClick={() => setActiveView(item.id)}
                className={`w-full flex items-center ${
                  isSidebarOpen ? 'justify-between px-2.5' : 'justify-center px-0'
                } py-1.5 rounded transition group ${
                  isActive 
                    ? 'bg-slate-850 text-emerald-400 font-medium border border-slate-700/80 shadow-sm' 
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
                }`}
                title={`${item.label} (${item.shortcut})`}
              >
                <div className="flex items-center space-x-2.5 min-w-0">
                  <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-emerald-400' : 'text-slate-400 group-hover:text-slate-200'}`} />
                  {isSidebarOpen && (
                    <span className="text-xs truncate font-mono tracking-tight">{item.label}</span>
                  )}
                </div>

                {isSidebarOpen && item.badge && (
                  <span className={`text-[10px] font-mono px-1.5 py-0.2 rounded font-semibold ${
                    typeof item.badge === 'string' && item.badge === 'LIVE'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                      : typeof item.badge === 'string' && item.badge === 'RUNNING'
                      ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 animate-pulse'
                      : 'bg-slate-800 text-slate-400 border border-slate-700'
                  }`}>
                    {item.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Footer System Status Indicator */}
      {isSidebarOpen && (
        <div className="p-2.5 border-t border-slate-800 bg-slate-900/60 text-[11px] font-mono flex items-center justify-between text-slate-400">
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            <span>Redis/Neo4j Online</span>
          </div>
        </div>
      )}
    </aside>
  );
};
