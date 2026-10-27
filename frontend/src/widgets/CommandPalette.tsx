import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { WorkbenchView } from '../shared/types/workbench';
import { str } from '../shared/lib/safe';
import { Search, X, CornerDownLeft } from 'lucide-react';

type Result =
  | { kind: 'view'; view: WorkbenchView; label: string; hint: string }
  | { kind: 'node'; id: string; label: string; hint: string }
  | { kind: 'entity'; id: string; label: string; hint: string }
  | { kind: 'chunk'; id: string; label: string; hint: string };

/** Latency as text, without ever throwing on an absent value. */
function latencyLabel(v: unknown): string {
  const n = typeof v === 'number' ? v : Number(v);
  return `${Number.isFinite(n) ? n : 0}ms`;
}

/**
 * Command palette.
 *
 * Previously a fixed grid of view buttons with a different accent colour per
 * row (emerald, cyan, indigo, violet, amber) and no keyboard support beyond
 * typing. Now a single flat result list with arrow-key navigation, and one
 * accent colour.
 */
export const CommandPalette: React.FC = () => {
  const {
    isCommandPaletteOpen,
    setCommandPaletteOpen,
    setActiveView,
    inspectItem,
  } = useWorkbenchStore();

  const { nodes, entities, chunks } = useExecutionStore();

  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  // Ctrl+K is ours, so it is captured - but only outside a text field.
  useEffect(() => {
    const isTypingTarget = (el: Element | null): boolean => {
      if (!el) return false;
      const tag = el.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
      return el instanceof HTMLElement && el.isContentEditable;
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        if (isTypingTarget(e.target as Element | null)) return;
        e.preventDefault();
        setCommandPaletteOpen(!isCommandPaletteOpen);
        return;
      }
      if (e.key === 'Escape' && isCommandPaletteOpen) {
        setCommandPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isCommandPaletteOpen, setCommandPaletteOpen]);

  useEffect(() => {
    if (isCommandPaletteOpen) {
      setQuery('');
      setCursor(0);
    }
  }, [isCommandPaletteOpen]);

  const q = query.trim().toLowerCase();

  const results = useMemo<Result[]>(() => {
    const viewResults: Result[] = [
      { kind: 'view', view: 'chat', label: 'New research', hint: 'start a run' },
      { kind: 'view', view: 'execution', label: 'Pipeline', hint: 'live stages' },
      { kind: 'view', view: 'graph', label: 'Knowledge graph', hint: 'entities' },
      { kind: 'view', view: 'vector', label: 'Evidence passages', hint: 'passages' },
      { kind: 'view', view: 'reports', label: 'Reports', hint: 'finished output' },
    ];

    const matches = (label: string) => !q || label.toLowerCase().includes(q);

    const out: Result[] = [];
    for (const v of viewResults) if (matches(v.label)) out.push(v);

    for (const n of nodes) {
      const label = str(n.label);
      if (matches(label)) out.push({ kind: 'node', id: n.id, label, hint: `node · ${latencyLabel(n.latencyMs)}` });
    }
    for (const e of entities) {
      const label = str(e.name);
      if (matches(label)) out.push({ kind: 'entity', id: e.id, label, hint: `entity · ${str(e.type)}` });
    }
    for (const c of chunks) {
      const label = str(c.documentTitle);
      if (matches(label)) out.push({ kind: 'chunk', id: c.id, label, hint: 'chunk' });
    }
    return out.slice(0, 40);
  }, [q, nodes, entities, chunks]);

  useEffect(() => { setCursor(0); }, [query]);

  const run = (r: Result) => {
    if (r.kind === 'view') setActiveView(r.view);
    else if (r.kind === 'node') inspectItem('node', nodes.find(n => n.id === r.id));
    else if (r.kind === 'entity') inspectItem('entity', entities.find(e => e.id === r.id));
    else inspectItem('chunk', chunks.find(c => c.id === r.id));
    setCommandPaletteOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor(c => Math.min(c + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor(c => Math.max(c - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const r = results[cursor];
      if (r) run(r);
    }
  };

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    el?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  if (!isCommandPaletteOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4"
      style={{ background: 'rgba(4, 6, 8, 0.72)', backdropFilter: 'blur(3px)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) setCommandPaletteOpen(false); }}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div className="w-full max-w-xl bg-paper-raised border border-rule-strong rounded-lg shadow-overlay overflow-hidden">
        <div className="flex items-center gap-2 px-3 border-b border-rule">
          <Search className="w-4 h-4 text-ink-faint shrink-0" />
          <input
            type="text"
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search views, nodes, entities or chunks"
            className="flex-1 bg-transparent py-3 text-label text-ink placeholder:text-ink-faint focus:outline-none"
          />
          <button
            type="button"
            onClick={() => setCommandPaletteOpen(false)}
            className="p-1 text-ink-faint hover:text-ink rounded transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div ref={listRef} className="max-h-[50vh] overflow-y-auto p-1.5">
          {results.length === 0 ? (
            <p className="px-2 py-6 text-center text-micro text-ink-faint">No matches.</p>
          ) : (
            results.map((r, i) => (
              <button
                key={`${r.kind}-${r.kind === 'view' ? r.view : r.id}`}
                type="button"
                data-active={i === cursor}
                onMouseEnter={() => setCursor(i)}
                onClick={() => run(r)}
                className={`w-full flex items-center justify-between gap-3 px-2.5 py-2 rounded-md text-left text-label transition-colors ${
                  i === cursor ? 'bg-paper-inset text-ink' : 'text-ink-muted hover:bg-paper'
                }`}
              >
                <span className="truncate">{r.label}</span>
                <span className="text-micro text-ink-faint font-mono shrink-0 flex items-center gap-1">
                  {r.hint}
                  {i === cursor && <CornerDownLeft className="w-3 h-3" />}
                </span>
              </button>
            ))
          )}
        </div>

        <div className="px-3 py-2 border-t border-rule flex items-center gap-3 text-micro text-ink-faint">
          <span><kbd className="font-mono">↑↓</kbd> navigate</span>
          <span><kbd className="font-mono">↵</kbd> select</span>
          <span><kbd className="font-mono">esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
};
export default CommandPalette;
