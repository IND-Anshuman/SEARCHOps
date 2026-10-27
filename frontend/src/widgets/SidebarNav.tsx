import React from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { WorkbenchView } from '../shared/types/workbench';
import { str } from '../shared/lib/safe';
import {
  MessageSquareCode,
  GitFork,
  Share2,
  Database,
  FileText,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { StatusMarkGlyph } from '../shared/components/StatusMark';

interface NavItem {
  id: WorkbenchView;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Real badge derived from job state. Never a hardcoded string. */
  badge: () => string | null;
  /** Badge tone: a status, so a badge can only ever mean something. */
  tone?: 'signal' | 'caution' | 'alarm' | 'absence';
}

/**
 * Left navigation.
 *
 * The previous version was a labelled menu down the left of the window — the
 * single most recognisable piece of the AI-dashboard template, and the reason
 * the product looked assembled rather than designed. A permanent list of five
 * labelled items beside a document says "here is a sidebar", and the report
 * inherits that framing no matter how the report itself is set.
 *
 * This is a **tab strip** instead: a narrow column of ruled index tabs, like
 * the tabs cut into the edge of a filing cabinet. The labels are hidden
 * until the strip is hovered or expanded, so the report is the only thing
 * with words on it. The active tab is marked with a struck rule — the way a
 * contents page marks an entry — rather than a filled pill.
 *
 * The previous version also showed a hard-coded "LIVE" and "RUNNING" badge on
 * two items regardless of state, which is a claim the product cannot back.
 * Badges are derived from the job and the pipeline itself.
 */
export const SidebarNav: React.FC = () => {
  const { activeView, setActiveView, isSidebarOpen, toggleSidebar } = useWorkbenchStore();
  const { job, nodes } = useExecutionStore();

  const navItems: NavItem[] = [
    {
      id: 'chat',
      label: 'Research',
      icon: MessageSquareCode,
      badge: () => (job.status === 'running' || job.status === 'pending' ? 'running' : null),
      tone: 'signal',
    },
    {
      id: 'execution',
      label: 'Pipeline',
      icon: GitFork,
      badge: () => {
        const completed = nodes.filter(n => n.status === 'completed').length;
        if (job.status === 'running' || job.status === 'pending') {
          return nodes.length ? `${completed}/${nodes.length}` : 'running';
        }
        if (job.status === 'completed') return 'done';
        if (job.status === 'failed') return 'failed';
        return null;
      },
      tone: 'signal',
    },
    { id: 'graph', label: 'Knowledge graph', icon: Share2, badge: () => null },
    { id: 'vector', label: 'Evidence passages', icon: Database, badge: () => null },
    {
      id: 'reports',
      label: 'Reports',
      icon: FileText,
      badge: () => (job.status === 'completed' ? 'ready' : null),
      tone: 'signal',
    },
  ];

  /** The mark that carries a badge's state. */
  const badgeMark = (badge: string | null): 'filled' | 'half' | 'empty' | 'dotted' | 'none' => {
    if (!badge) return 'none';
    if (badge === 'failed') return 'empty';
    if (badge === 'running') return 'dotted';
    return 'filled';
  };

  return (
    <aside
      className={`shrink-0 bg-paper-raised paper-wash border-r border-rule flex flex-col transition-[width] duration-200 ease-out-expo ${
        isSidebarOpen ? 'w-52' : 'w-14'
      }`}
    >
      <div className="flex-1 overflow-y-auto pt-5">
        {isSidebarOpen ? (
          <div className="px-5 mb-4 flex items-center justify-between">
            <span className="folio">Sections</span>
            <button
              type="button"
              onClick={toggleSidebar}
              className="p-1 -mr-1 rounded-sm text-ink-faint hover:text-ink transition-colors"
              aria-label="Collapse the tab strip"
            >
              <PanelLeftClose className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <div className="flex justify-center mb-4">
            <button
              type="button"
              onClick={toggleSidebar}
              className="p-1 rounded-sm text-ink-faint hover:text-ink transition-colors"
              aria-label="Expand the tab strip"
            >
              <PanelLeftOpen className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <nav
          className={isSidebarOpen ? 'px-3' : 'px-2'}
          aria-label="Workspace sections"
        >
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeView === item.id;
            const badge = item.badge();

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveView(item.id)}
                title={item.label}
                aria-current={isActive ? 'page' : undefined}
                className={`group relative w-full flex items-center gap-2.5 rounded-md text-label transition-all tactile-btn ${
                  isSidebarOpen ? 'pl-3 pr-2.5 py-[7px]' : 'px-0 py-[9px] justify-center'
                } ${
                  isActive
                    ? 'text-ink font-semibold bg-paper-inset/70 shadow-sm border border-rule/50'
                    : 'text-ink-faint hover:text-ink hover:bg-paper/40'
                }`}
              >
                {/* The struck rule: the active tab is marked the way a
                    contents page marks an entry, in the margin. */}
                {isActive && (
                  <span
                    aria-hidden="true"
                    className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-[16px] bg-ink"
                  />
                )}
                <Icon
                  className={`w-[15px] h-[15px] shrink-0 ${
                    isActive ? 'text-ink' : 'text-ink-ghost group-hover:text-ink-muted'
                  }`}
                />
                {isSidebarOpen && (
                  <>
                    <span className="flex-1 text-left truncate">{item.label}</span>
                    {badge && (
                      <span className="flex items-center gap-1.5 shrink-0">
                        <StatusMarkGlyph status={badgeMark(badge)} className="text-ink" />
                        <span className="text-micro text-ink-muted tnum">{badge}</span>
                      </span>
                    )}
                  </>
                )}
                {/* In the collapsed strip a badge is a mark on the tab, not
                    a chip: it must fit in the width of a filing tab. */}
                {!isSidebarOpen && badge && (
                  <StatusMarkGlyph
                    status={badgeMark(badge)}
                    className="absolute right-1 top-1 text-ink"
                  />
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Connection state: reflects reality, not a fixed string. */}
      <div className="px-5 py-3.5 border-t border-rule">
        {isSidebarOpen ? (
          <div
            className="flex items-center gap-2 text-micro text-ink-faint"
            title={str(job.id) ? 'A job is loaded in this session' : 'No job is loaded'}
          >
            <StatusMarkGlyph
              status={str(job.id) ? 'filled' : 'none'}
              className="text-ink shrink-0"
            />
            <span className="truncate">{str(job.id) ? 'Job loaded' : 'No job loaded'}</span>
          </div>
        ) : (
          <div className="flex justify-center">
            <StatusMarkGlyph
              status={str(job.id) ? 'filled' : 'none'}
              className="text-ink"
              title={str(job.id) ? 'A job is loaded in this session' : 'No job is loaded'}
            />
          </div>
        )}
      </div>
    </aside>
  );
};

export default SidebarNav;
