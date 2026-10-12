import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChevronsDownUp,
  Eye,
  EyeOff,
  Loader2,
  Minus,
  PenLine,
  Scissors,
  Sparkles,
} from 'lucide-react';
import { API_BASE, API_KEY } from '../shared/store/executionStore';

export interface ReportBlockData {
  id: string;
  kind: string;
  text: string;
  level: number | null;
  claimIds: string[];
  hidden: boolean;
  origin: string;
  parentId: string | null;
}

export interface ReportDocumentData {
  jobId: string;
  query: string;
  blocks: ReportBlockData[];
  quality: string;
  userMessage: string;
}

export interface BlockOpResponse {
  ok: boolean;
  op: string;
  reason: string;
  touchedIds: string[];
  document: ReportDocumentData;
}

interface BlockEditorProps {
  jobId: string;
  /** Current document. The parent owns it so every view reads one source of truth. */
  doc: ReportDocumentData | null;
  onDocumentChange: (next: ReportDocumentData) => void;
}

type Pending = 'condense' | 'deepen' | 'rewrite' | 'hide' | 'unhide' | 'split' | null;

/**
 * Selection-driven block editing.
 *
 * Select text inside a block and a floating menu offers condense / deepen /
 * rewrite / split. The operations are server-side, so the report stays the
 * single source of truth and an edit survives a reload.
 *
 * Contract: a failed operation never changes the document. The server says
 * why in plain language and that reason is shown to the user — a failed edit
 * must never look like a successful one.
 */
export const BlockEditor: React.FC<BlockEditorProps> = ({ jobId, doc, onDocumentChange }) => {
  const [menu, setMenu] = useState<{ blockId: string; x: number; y: number } | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [notice, setNotice] = useState<string>('');
  const containerRef = useRef<HTMLDivElement>(null);

  const runOp = useCallback(
    async (blockId: string, op: Pending, atChar = 0) => {
      if (!op) return;
      setPending(op);
      setNotice('');
      try {
        const res = await fetch(`${API_BASE}/api/v1/research/${jobId}/blocks/${blockId}/op`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
          },
          body: JSON.stringify({ op, at_char: atChar }),
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => null)) as { detail?: string } | null;
          setNotice(detail?.detail ?? `That action failed (HTTP ${res.status}).`);
          return;
        }
        const body = (await res.json()) as BlockOpResponse;
        if (body.ok) {
          onDocumentChange(body.document);
        } else {
          // Honest failure: show the reason, change nothing.
          setNotice(body.reason || 'That action did not change the report.');
        }
      } catch (err) {
        setNotice('Could not reach the server, so nothing was changed.');
      } finally {
        setPending(null);
        setMenu(null);
      }
    },
    [jobId, onDocumentChange],
  );

  // A selection inside the report opens the action menu.
  useEffect(() => {
    const handler = () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !containerRef.current) {
        setMenu(null);
        return;
      }
      const anchor = selection.anchorNode;
      const host = anchor?.parentElement?.closest('[data-block-id]');
      if (!host || !containerRef.current.contains(host)) {
        setMenu(null);
        return;
      }
      const range = selection.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      const containerRect = containerRef.current.getBoundingClientRect();
      setMenu({
        blockId: host.getAttribute('data-block-id') ?? '',
        x: rect.left - containerRect.left + rect.width / 2,
        y: rect.top - containerRect.top,
      });
    };
    document.addEventListener('selectionchange', handler);
    return () => document.removeEventListener('selectionchange', handler);
  }, []);

  if (!doc) return null;

  const blocks = doc.blocks.filter((b) => !b.hidden);

  return (
    <div ref={containerRef} className="relative flex-1 overflow-y-auto p-6 max-w-4xl mx-auto w-full">
      {notice ? (
        <div
          role="status"
          className="mb-3 border border-amber-500/30 bg-amber-500/5 rounded px-3 py-2 text-[11px] text-amber-200 font-sans"
        >
          {notice}
          <button
            type="button"
            onClick={() => setNotice('')}
            className="ml-2 text-amber-400/70 hover:text-amber-200"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ) : null}

      {blocks.map((block) => (
        <div
          key={block.id}
          data-block-id={block.id}
          data-origin={block.origin}
          className={`group relative ${
            block.origin === 'deepened' || block.origin === 'condensed'
              ? 'border-l-2 border-violet-500/40 pl-3'
              : ''
          }`}
        >
          {renderBlock(block)}

          {/* Per-block controls. Hidden until hover so the report stays readable. */}
          {block.kind !== 'source' ? (
            <div className="absolute -right-2 top-0 opacity-0 group-hover:opacity-100 transition flex flex-col gap-1">
              <IconBtn
                title="Condense this section"
                disabled={pending !== null}
                onClick={() => void runOp(block.id, 'condense')}
              >
                {pending === 'condense' ? <Spinner /> : <ChevronsDownUp className="w-3 h-3" />}
              </IconBtn>
              <IconBtn
                title="Add more detail from its sources"
                disabled={pending !== null}
                onClick={() => void runOp(block.id, 'deepen')}
              >
                {pending === 'deepen' ? <Spinner /> : <Sparkles className="w-3 h-3" />}
              </IconBtn>
              <IconBtn
                title="Rewrite for clarity"
                disabled={pending !== null}
                onClick={() => void runOp(block.id, 'rewrite')}
              >
                {pending === 'rewrite' ? <Spinner /> : <PenLine className="w-3 h-3" />}
              </IconBtn>
              <IconBtn
                title="Hide this section"
                disabled={pending !== null}
                onClick={() => void runOp(block.id, 'hide')}
              >
                <EyeOff className="w-3 h-3" />
              </IconBtn>
            </div>
          ) : null}
        </div>
      ))}

      {/* Hidden sections, restorable. */}
      <HiddenSections doc={doc} onRestore={(id) => void runOp(id, 'unhide')} busy={pending !== null} />

      {/* Floating selection menu */}
      {menu ? (
        <div
          className="absolute z-20 -translate-x-1/2 -translate-y-full pb-2"
          style={{ left: menu.x, top: menu.y }}
        >
          <div className="flex items-center gap-1 bg-slate-900 border border-slate-700 rounded-lg shadow-xl px-1 py-1">
            <MenuBtn
              title="Shorten to its key points"
              busy={pending === 'condense'}
              onClick={() => void runOp(menu.blockId, 'condense')}
            >
              <Minus className="w-3 h-3" />
            </MenuBtn>
            <MenuBtn
              title="Add detail from this section's sources"
              busy={pending === 'deepen'}
              onClick={() => void runOp(menu.blockId, 'deepen')}
            >
              <Sparkles className="w-3 h-3" />
            </MenuBtn>
            <MenuBtn
              title="Rewrite for clarity"
              busy={pending === 'rewrite'}
              onClick={() => void runOp(menu.blockId, 'rewrite')}
            >
              <PenLine className="w-3 h-3" />
            </MenuBtn>
            <MenuBtn
              title="Split at the selection"
              busy={pending === 'split'}
              onClick={() => {
                const offset = selectionOffsetIn(menu.blockId);
                if (offset === null) {
                  setNotice('Select some text first, then split at it.');
                  setMenu(null);
                  return;
                }
                void runOp(menu.blockId, 'split', offset);
              }}
            >
              <Scissors className="w-3 h-3" />
            </MenuBtn>
          </div>
        </div>
      ) : null}
    </div>
  );
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function selectionOffsetIn(blockId: string): number | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;
  const host = selection.anchorNode?.parentElement?.closest('[data-block-id]');
  if (!host || host.getAttribute('data-block-id') !== blockId) return null;
  const range = selection.getRangeAt(0);
  const pre = range.cloneRange();
  pre.selectNodeContents(host);
  pre.setEnd(range.startContainer, range.startOffset);
  return pre.toString().length;
}

function renderBlock(block: ReportBlockData): React.ReactNode {
  switch (block.kind) {
    case 'heading': {
      const level = Math.min(Math.max(block.level ?? 2, 1), 4);
      const Tag = (`h${level}` as unknown) as keyof JSX.IntrinsicElements;
      const tone =
        level === 1
          ? 'text-lg font-bold text-emerald-400 border-b border-slate-800 pb-2 pt-4'
          : level === 2
            ? 'text-base font-bold text-cyan-400 pt-3 pb-1'
            : 'text-sm font-bold text-indigo-400 pt-2';
      return <Tag className={`${tone} font-sans tracking-tight`}>{block.text.replace(/^#+\s*/, '')}</Tag>;
    }
    case 'list':
      return (
        <ul className="list-disc pl-5 space-y-1 text-slate-300 text-sm font-sans my-2">
          {block.text
            .split('\n')
            .filter((l) => l.trim())
            .map((line, i) => (
              <li key={i}>{line.replace(/^\s*[-*•]\s*/, '')}</li>
            ))}
        </ul>
      );
    case 'source':
      return (
        <p className="text-xs text-emerald-400/80 font-mono my-1">
          {block.text.replace(/^\s*[-*]\s*/, '')}
        </p>
      );
    case 'quote':
      return (
        <blockquote className="border-l-2 border-emerald-500/40 pl-4 italic text-slate-400 my-2 text-sm">
          {block.text.replace(/^>\s*/, '')}
        </blockquote>
      );
    case 'code':
      return (
        <pre className="bg-slate-900 border border-slate-800 rounded-lg overflow-x-auto text-xs my-4 p-4 font-mono">
          {block.text.replace(/^```\w*\n?/, '').replace(/```$/, '')}
        </pre>
      );
    default:
      return <p className="text-slate-300 leading-relaxed font-sans text-sm my-3">{block.text}</p>;
  }
}

const HiddenSections: React.FC<{
  doc: ReportDocumentData;
  onRestore: (id: string) => void;
  busy: boolean;
}> = ({ doc, onRestore, busy }) => {
  const [open, setOpen] = useState(false);
  const hidden = doc.blocks.filter((b) => b.hidden);
  if (hidden.length === 0) return null;

  return (
    <div className="mt-6 border-t border-slate-800 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[10px] text-slate-500 hover:text-slate-300 font-mono uppercase"
      >
        <Eye className="w-3 h-3" />
        {hidden.length} hidden {hidden.length === 1 ? 'section' : 'sections'}
      </button>
      {open ? (
        <ul className="mt-2 space-y-1">
          {hidden.map((b) => (
            <li key={b.id} className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-slate-500 font-sans truncate">
                {b.text.slice(0, 70)}
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => onRestore(b.id)}
                className="text-[10px] text-emerald-400 hover:text-emerald-300 font-mono shrink-0"
              >
                Restore
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
};

const Spinner: React.FC = () => <Loader2 className="w-3 h-3 animate-spin" />;

const IconBtn: React.FC<{
  title: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}> = ({ title, disabled, onClick, children }) => (
  <button
    type="button"
    title={title}
    aria-label={title}
    disabled={disabled}
    onClick={onClick}
    className="w-5 h-5 flex items-center justify-center rounded bg-slate-800/90 border border-slate-700 text-slate-400 hover:text-emerald-400 hover:border-emerald-500/50 transition disabled:opacity-40"
  >
    {children}
  </button>
);

const MenuBtn: React.FC<{
  title: string;
  busy: boolean;
  onClick: () => void;
  children: React.ReactNode;
}> = ({ title, busy, onClick, children }) => (
  <button
    type="button"
    title={title}
    aria-label={title}
    disabled={busy}
    onClick={onClick}
    className="px-2 py-1 rounded text-slate-300 hover:bg-slate-800 hover:text-emerald-400 transition disabled:opacity-40"
  >
    {busy ? <Spinner /> : children}
  </button>
);

export default BlockEditor;
