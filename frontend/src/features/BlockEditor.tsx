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
import { API_BASE } from '../shared/store/executionStore';
import { apiFetch } from '../shared/lib/api';
import { ReportMarkdown, sourcesFromLines, type SourceRef } from './ReportMarkdown';

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
        const res = await apiFetch(`${API_BASE}/api/v1/research/${jobId}/blocks/${blockId}/op`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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
  const sources = sourceRegistry(doc.blocks);

  return (
    <div ref={containerRef} className="prose report-doc relative flex-1 overflow-y-auto py-2 mx-auto w-full">
      {notice ? (
        <div
          role="status"
          className="mb-3 border border-rule-strong bg-paper-inset rounded px-3 py-2 text-micro text-ink"
        >
          {notice}
          <button
            type="button"
            onClick={() => setNotice('')}
            className="ml-2 text-ink-muted hover:text-ink"
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
              ? 'border-l border-rule-strong pl-3'
              : ''
          } ${isFirstParagraph(block, blocks) ? 'dropcap' : ''}`}
        >
          {renderBlock(block, sources)}

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
          <div className="flex items-center gap-1 bg-paper-inset border border-rule-strong rounded-lg shadow-xl px-1 py-1">
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

/**
 * Is this the document's first paragraph?
 *
 * The drop cap belongs to the first paragraph of a typeset document, which is
 * usually the first paragraph of the first section — not the first block.
 * Each block is its own wrapper, so a CSS `:first-of-type` can never reach it;
 * the block has to be identified here.
 */
function isFirstParagraph(block: ReportBlockData, blocks: ReportBlockData[]): boolean {
  if (block.kind !== 'paragraph') return false;
  // The italic standfirst under the title is not the opening of the text.
  const visible = blocks.filter((b) => !b.hidden && !/^_[^_].*_$/s.test(b.text.trim()));
  const first = visible.find((b) => b.kind === 'paragraph');
  return first?.id === block.id;
}

function sourceRegistry(blocks: ReportBlockData[]): Map<number, SourceRef> {
  return sourcesFromLines(blocks.filter((b) => b.kind === 'source' || b.kind === 'list').map((b) => b.text));
}

function renderBlock(block: ReportBlockData, sources: Map<number, SourceRef> = new Map()): React.ReactNode {
  switch (block.kind) {
    case 'heading': {
      const level = Math.min(Math.max(block.level ?? 2, 1), 4);
      const Tag = (`h${level}` as unknown) as keyof JSX.IntrinsicElements;
      // The .prose stylesheet sets headings (display face, ruled section
      // heads, more space above than below), so no per-level classes here.
      const title = block.text.replace(/^#+\s*/, '').replace(/\*\*/g, '');
      // The id matches the contents rail's slug so its links scroll here.
      const id = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
      return <Tag id={id}>{title}</Tag>;
    }
    case 'paragraph':
    case 'list':
    case 'table':
    case 'quote':
      return <ReportMarkdown text={block.text} sources={sources} />;
    case 'source': {
      // A citation is data, so it is set in mono — but it is not a status, so
      // it does not wear a status colour. The markdown link is rendered as a
      // real link: printing "[IBM Research](https://ibm.com/a)" as literal
      // text is not a citation, it is a leftover from the Markdown source.
      const numbered = /^\s*(\d+)\.\s*/.exec(block.text);
      const raw = block.text.replace(/^\s*(?:[-*]|\d+\.)\s*/, '').trim();
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)\s*$/.exec(raw);
      if (link) {
        return (
          <p className="text-micro text-ink-muted font-mono my-2">
            {numbered ? <span className="text-ink-faint mr-1.5">[{numbered[1]}]</span> : null}
            <a
              href={link[2]}
              target="_blank"
              rel="noopener noreferrer"
              className="text-ink underline decoration-rule-strong underline-offset-[0.18em] hover:decoration-ink"
            >
              {link[1]}
            </a>
            <span className="text-ink-faint"> — </span>
            <a
              href={link[2]}
              target="_blank"
              rel="noopener noreferrer"
              className="text-ink-faint underline decoration-rule underline-offset-[0.18em] hover:text-ink-muted hover:decoration-rule-strong"
            >
              {link[2]}
            </a>
          </p>
        );
      }
      return <p className="text-micro text-ink-muted font-mono my-2">{raw}</p>;
    }
    case 'code':
      return (
        <pre className="bg-paper-inset border border-rule rounded-md overflow-x-auto text-micro my-4 p-4 font-mono text-ink">
          {block.text.replace(/^```\w*\n?/, '').replace(/```$/, '')}
        </pre>
      );
    default:
      return <ReportMarkdown text={block.text} sources={sources} />;
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
    <div className="mt-8 pt-4 border-t border-rule">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-2 text-micro text-ink-faint hover:text-ink transition-colors"
      >
        <Eye className="w-3 h-3" />
        <span className="folio">
          {hidden.length} hidden {hidden.length === 1 ? 'section' : 'sections'}
        </span>
        <span className="text-ink-ghost normal-case tracking-normal">
          {open ? 'hide list' : 'show list'}
        </span>
      </button>
      {open ? (
        <ul className="mt-2 space-y-1">
          {hidden.map((b) => (
            <li key={b.id} className="flex items-center justify-between gap-2">
              <span className="text-micro text-ink-faint font-sans truncate">
                {b.text.slice(0, 70)}
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => onRestore(b.id)}
                className="text-micro text-ink hover:text-ink font-mono shrink-0"
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
    className="w-5 h-5 flex items-center justify-center rounded bg-paper-deep/90 border border-rule-strong text-ink-muted hover:text-ink hover:border-rule-strong transition disabled:opacity-40"
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
    className="px-2 py-1 rounded text-ink hover:bg-paper-deep hover:text-ink transition disabled:opacity-40"
  >
    {busy ? <Spinner /> : children}
  </button>
);

export default BlockEditor;
