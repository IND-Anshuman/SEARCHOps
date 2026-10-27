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
import { ErrorBoundary } from './shared/components/ErrorBoundary';

export const App: React.FC = () => {
  const { activeView, setActiveView, toggleSidebar, toggleConsole } = useWorkbenchStore();

  /**
   * Global shortcuts.
   *
   * Two rules that used to be missing, and both produced real breakage:
   *
   * 1. Never fire while focus is in a text field. The old handler matched on
   *    `ctrlKey` alone, so Ctrl+V in the query box was swallowed and the view
   *    jumped to Vector Search — paste was broken everywhere, not just in one
   *    input. The same handler also hijacked Ctrl+R (reload), Ctrl+B
   *    (bookmarks), Ctrl+G (find-next) and Ctrl+D (bookmark).
   * 2. Never hijack a browser-level default. Those belong to the browser, so
   *    they are removed here and the sidebar no longer advertises them.
   */
  useEffect(() => {
    const isTypingTarget = (el: Element | null): boolean => {
      if (!el) return false;
      const tag = el.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
      return el instanceof HTMLElement && el.isContentEditable;
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      // A keyboard shortcut is only ours when the user is not typing.
      if (isTypingTarget(e.target as Element | null)) return;

      if (!(e.ctrlKey || e.metaKey)) return;

      switch (e.key.toLowerCase()) {
        // Ctrl+` is not a browser default, so it stays ours.
        case '`':
          e.preventDefault();
          toggleConsole();
          break;
        // Ctrl+K opens the palette and is handled in CommandPalette, which
        // already ignores typing targets. Skip it here so the two handlers
        // cannot fight over the same keypress.
        default:
          break;
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
    // Each view gets its own boundary: a crash in one panel degrades that
    // panel, never the whole window. Without a boundary at all, React 18
    // unmounts the entire tree on an uncaught render error and the user is
    // left with a blank screen and nothing clickable.
    switch (activeView) {
      case 'chat':
        return <ErrorBoundary><AIChatWorkspace /></ErrorBoundary>;
      case 'execution':
        return <ErrorBoundary><LangGraphCanvas /></ErrorBoundary>;
      case 'graph':
        return <ErrorBoundary><KnowledgeGraphExplorer /></ErrorBoundary>;
      case 'vector':
        return <ErrorBoundary><VectorSearchInspector /></ErrorBoundary>;
      case 'reports':
        return <ErrorBoundary><ReportsViewer /></ErrorBoundary>;
      default:
        return <ErrorBoundary><AIChatWorkspace /></ErrorBoundary>;
    }
  };

  /**
   * The shell.
   *
   * The previous shell was the universal AI-dashboard arrangement: a top bar
   * across the full width, a nav rail down the left, the content squeezed
   * into the middle, an inspector drawer on the right and a console along the
   * bottom. Five chrome regions, all permanently on, all claiming space from
   * the thing the product actually exists to show — a report. The result
   * looked assembled rather than designed, because that arrangement is a
   * template, not a decision.
   *
   * The new structure is a **desk**, and it changes with the work:
   *
   * - The nav rail is a narrow strip of ruled index tabs, not a labelled
   *   menu. The labels are hidden until you hover or expand it.
   * - The report view gets the full width of the window. Its apparatus (trust,
   *   gaps, terms) is a *margin* of the page, not a docked drawer that
   *   squeezes the page.
   * - The console is not a permanent band. It only appears while a run is
   *   live, because a log of a finished run is history, not workspace.
   * - The status banner is a marginal note on the report, not a full-width
   *   strip that pushes the page down.
   */
  return (
    <div className="h-screen w-screen flex flex-col bg-paper text-ink overflow-hidden font-sans">
      <Header />

      <div className="flex-1 flex overflow-hidden relative min-h-0">
        <SidebarNav />

        <main className="flex-1 flex flex-col overflow-hidden bg-paper min-w-0">
          {renderActiveView()}
        </main>

        <RightInspector />
      </div>

      <BottomConsole />
      <CommandPalette />
      <NotificationCenter />
    </div>
  );
};

export default App;
