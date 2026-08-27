import React, { useEffect } from 'react';
import { useWorkbenchStore } from './shared/store/useWorkbenchStore';
import { useExecutionStore } from './shared/store/useExecutionStore';
import { Header } from './widgets/Header';
import { SidebarNav } from './widgets/SidebarNav';
import { RightInspector } from './widgets/RightInspector';
import { BottomConsole } from './widgets/BottomConsole';
import { CommandPalette } from './widgets/CommandPalette';
import { NotificationCenter } from './widgets/NotificationCenter';
import { AISuggestionBanner } from './widgets/AISuggestionBanner';

// Subsystem Views
import { AIChatWorkspace } from './features/AIChatWorkspace';
import { LangGraphCanvas } from './features/LangGraphCanvas';
import { KnowledgeGraphExplorer } from './features/KnowledgeGraphExplorer';
import { VectorSearchInspector } from './features/VectorSearchInspector';
import { ReportsViewer } from './features/ReportsViewer';

export const App: React.FC = () => {
  const { activeView, setActiveView, toggleSidebar, toggleConsole } = useWorkbenchStore();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey) {
        switch (e.key.toLowerCase()) {
          case 'b':
            e.preventDefault();
            toggleSidebar();
            break;
          case '`':
            e.preventDefault();
            toggleConsole();
            break;
          case 'r':
            e.preventDefault();
            setActiveView('chat');
            break;
          case 'e':
            e.preventDefault();
            setActiveView('execution');
            break;
          case 'g':
            e.preventDefault();
            setActiveView('graph');
            break;
          case 'v':
            e.preventDefault();
            setActiveView('vector');
            break;
          case 'd':
            e.preventDefault();
            setActiveView('reports');
            break;
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [setActiveView, toggleSidebar, toggleConsole]);

  const { loadJob } = useExecutionStore();

  // Single hydration on app mount — reads localStorage once, never in child hooks
  useEffect(() => {
    const storedJobId = localStorage.getItem('searchops:active_job_id');
    if (storedJobId) {
      loadJob(storedJobId);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — runs once on mount only

  const renderActiveView = () => {
    switch (activeView) {
      case 'chat': return <AIChatWorkspace />;
      case 'execution': return <LangGraphCanvas />;
      case 'graph': return <KnowledgeGraphExplorer />;
      case 'vector': return <VectorSearchInspector />;
      case 'reports': return <ReportsViewer />;
      default: return <AIChatWorkspace />;
    }
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-slate-950 text-slate-100 overflow-hidden font-mono select-none">
      {/* Top Header */}
      <Header />

      {/* Proactive AI Suggestion Banner */}
      <AISuggestionBanner />

      {/* Main Workspace Body */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left Sidebar Navigation */}
        <SidebarNav />

        {/* Center Main Workspace Canvas */}
        <main className="flex-1 flex flex-col overflow-hidden bg-slate-950">
          {renderActiveView()}
        </main>

        {/* Right Floating / Dockable Inspector */}
        <RightInspector />
      </div>

      {/* Dockable Multi-Tab Bottom Console */}
      <BottomConsole />

      {/* Global Cmd+K Search Modal */}
      <CommandPalette />

      {/* Notification Center Alert History Drawer */}
      <NotificationCenter />
    </div>
  );
};

export default App;
