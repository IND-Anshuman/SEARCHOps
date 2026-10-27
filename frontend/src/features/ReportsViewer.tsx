import React, { useState, useCallback, useRef, useEffect } from 'react';
import { ReportMarkdown, sourcesFromLines } from './ReportMarkdown';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { num, str, dateLabel } from '../shared/lib/safe';
import { ReportInsights } from './ReportInsights';
import {
  BlockEditor,
  type ReportDocumentData,
} from './BlockEditor';
import { API_BASE } from '../shared/store/executionStore';
import { apiFetch } from '../shared/lib/api';
import { printReport } from '../shared/lib/printReport';
import { FileText, Download, Copy, Check, Search, X, FileDown, Loader2, BookOpen } from 'lucide-react';

interface TocEntry {
  text: string;
  level: 1 | 2 | 3;
  slug: string;
}

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

function buildToc(markdown: string): TocEntry[] {
  const entries: TocEntry[] = [];
  const seen: Record<string, number> = {};
  for (const line of markdown.split('\n')) {
    const h3 = line.match(/^### (.+)/);
    const h2 = line.match(/^## (.+)/);
    const h1 = line.match(/^# (.+)/);
    const match = h1 ?? h2 ?? h3;
    const level = h1 ? 1 : h2 ? 2 : h3 ? 3 : null;
    if (!match || !level) continue;
    const text = match[1].trim();
    let slug = slugify(text);
    seen[slug] = (seen[slug] ?? 0) + 1;
    if (seen[slug] > 1) slug = `${slug}-${seen[slug] - 1}`;
    entries.push({ text, level: level as 1 | 2 | 3, slug });
  }
  return entries;
}

const ExportBtn: React.FC<{
  label: string;
  busy: boolean;
  onClick: () => void;
}> = ({ label, busy, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={busy}
    title={`Download as ${label}`}
    className="text-label text-ink-muted hover:text-ink border border-rule bg-paper hover:bg-paper-raised px-2.5 py-1.5 rounded-md transition-all tactile-btn flex items-center gap-1.5 disabled:opacity-40 disabled:hover:text-ink-muted shadow-sm hover:shadow"
  >
    {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
    <span>{label}</span>
  </button>
);

/**
 * Drop the report's own first heading when it repeats the title.
 *
 * The page already carries the title as its h1. The report document almost
 * always opens with a heading naming the same subject, so the reader sees
 * "quantum error correction" twice within a few lines. The heading is hidden
 * from the *view* only — it stays in the block list, so exporting, editing
 * and the contents rail are unaffected.
 */
function _withoutDuplicateTitle(
  doc: ReportDocumentData,
  title: string,
): ReportDocumentData {
  const wanted = title.trim().toLowerCase();
  if (!wanted) return doc;

  const first = doc.blocks[0];
  if (!first || first.kind !== 'heading') return doc;

  const heading = first.text.replace(/^#+\s*/, '').trim().toLowerCase();
  // Only an exact match is a duplicate. A heading that merely contains the
  // title is doing real work and must stay.
  if (heading !== wanted) return doc;

  return {
    ...doc,
    blocks: doc.blocks.map((b, i) => (i === 0 ? { ...b, hidden: true } : b)),
  };
}

/**
 * Report reader.
 *
 * The hero surface, so it is built as a *document*, not a dashboard panel:
 * a sheet of paper on a desk, a running head (the folio), a display-face
 * title, prose set in a reading face at a literary measure, and a contents
 * rail that is a table of contents.
 *
 * The previous version was a three-column split whose body was set in 14px
 * Inter with 1.75 line-height — a chat transcript wearing a report's
 * clothes. The report is the thing a reader is meant to *read*, so it is
 * set like one: serif, 68ch, ruled sections, and a title that carries the
 * authority a paper's title carries.
 */
export const ReportsViewer: React.FC = () => {
  const { report } = useExecutionStore();
  const [copied, setCopied] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [searchVisible, setSearchVisible] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [blockDoc, setBlockDoc] = useState<ReportDocumentData | null>(null);
  const [exporting, setExporting] = useState<'deck' | 'docx' | 'pdf' | null>(null);
  const [exportError, setExportError] = useState('');
  // On a narrow window the apparatus is a drawer over the page, not a column
  // beside it, so it starts closed and is opened deliberately.
  const [apparatusOpen, setApparatusOpen] = useState(false);

  useEffect(() => {
    if (!report?.id) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiFetch(`${API_BASE}/api/v1/research/${report.id}`);
        if (!res.ok) return;
        const data = (await res.json()) as { report_document?: ReportDocumentData | null };
        if (!cancelled && data.report_document) setBlockDoc(data.report_document);
      } catch {
        /* leave document null - the markdown fallback covers this */
      }
    })();
    return () => { cancelled = true; };
  }, [report?.id]);

  useEffect(() => {
    if (searchVisible) searchRef.current?.focus();
  }, [searchVisible]);

  const handleCopy = useCallback(() => {
    if (!report) return;
    navigator.clipboard.writeText(report.markdownContent).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [report]);

  const handleDownload = useCallback(() => {
    if (!report) return;
    const blob = new Blob([report.markdownContent], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${report.title.toLowerCase().replace(/[^a-z0-9]+/g, '_')}_report.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [report]);

  const handleExport = useCallback(
    async (format: 'deck' | 'docx' | 'pdf') => {
      if (!report) return;
      setExporting(format);
      setExportError('');
      try {
        const res = await apiFetch(`${API_BASE}/api/v1/research/${report.id}/export/${format}`);
        // No PDF renderer on this server (the API-only deployment has no
        // browser): print the report page through the user's browser, which
        // offers "Save as PDF", styled like the server export.
        if (format === 'pdf' && res.status === 503 && articleRef.current) {
          printReport(articleRef.current, str(report.title));
          return;
        }
        if (!res.ok) {
          const detail = (await res.json().catch(() => null)) as { detail?: string } | null;
          setExportError(detail?.detail ?? `${format.toUpperCase()} export failed (HTTP ${res.status}).`);
          return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${report.title.toLowerCase().replace(/[^a-z0-9]+/g, '_')}_report.${format}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch {
        setExportError(`Could not reach the server for the ${format.toUpperCase()} export.`);
      } finally {
        setExporting(null);
      }
    },
    [report],
  );

  const scrollToHeading = useCallback((slug: string) => {
    const el = contentRef.current?.querySelector(`#${CSS.escape(slug)}`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  if (!report) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-12 bg-paper">
        <div className="flex flex-col items-center max-w-sm text-center py-12">
          {/* An empty sheet: a ruled rectangle, not an icon in a box. */}
          <div
            aria-hidden="true"
            className="w-12 h-16 border border-rule-strong rounded-sm bg-paper-raised mb-5 relative"
          >
            <span className="absolute left-2 right-2 top-4 h-px bg-rule" />
            <span className="absolute left-2 right-4 top-7 h-px bg-rule" />
            <span className="absolute left-2 right-3 top-10 h-px bg-rule" />
          </div>
          <p className="font-display text-display-sm text-ink leading-snug">No report yet</p>
          <p className="text-label text-ink-muted mt-2 leading-relaxed">
            A report is written when a run finishes. Start a run and it will appear here.
          </p>
        </div>
      </div>
    );
  }

  const toc = buildToc(report.markdownContent);

  const filteredContent = searchText.trim()
    ? report.markdownContent
        .split('\n')
        .filter(line => str(line).toLowerCase().includes(searchText.toLowerCase()))
        .join('\n') || report.markdownContent
    : report.markdownContent;

  return (
    <div className="flex-1 flex h-full bg-paper overflow-hidden min-h-0">
      {/* ── The page ──────────────────────────────────────────────────────
          A three-column page: the contents in the left margin, the document
          in the middle, the apparatus in the right margin.

          The previous layout was the AI-dashboard arrangement — a document
          column pushed to the left of a docked insights drawer, with a
          separate contents rail on the far right. The report lived in a
          middle column with chrome on both sides, and its own apparatus was
          a drawer that squeezed the text.

          Now the report is a page with real margins, the way a printed
          document has them. The contents and the apparatus are marginalia,
          not sidebars that compete with the text. The text block keeps a
          literary measure and sits in the optical centre of the window, and
          the margins narrow away on a small screen rather than squeezing. */}
      <div className="flex-1 flex min-w-0">
        {/* Left margin — the contents of this document. */}
        <nav
          aria-label="Report contents"
          className="w-52 shrink-0 hidden xl:block overflow-y-auto border-r border-rule/70 bg-paper/80 paper-wash px-5 py-8"
        >
          <div className="folio pb-2 border-b border-rule mb-3">Contents</div>
          {toc.length === 0 ? (
            <p className="text-micro text-ink-faint leading-relaxed">
              This report has no headings.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {toc.map((item, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => scrollToHeading(item.slug)}
                    className={`w-full text-left hover:text-ink transition-colors truncate block text-ink-muted ${
                      item.level === 1
                        ? 'text-label font-medium pl-0 py-[3px]'
                        : item.level === 2
                          ? 'text-label pl-3 py-[2px]'
                          : 'text-micro pl-6 py-[1px]'
                    }`}
                    title={item.text}
                  >
                    {item.text}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>

        {/* The document. */}
        <div className="flex-1 min-w-0 flex flex-col">
          {/* The folio — a running head. */}
          <div className="shrink-0 bg-paper-raised paper-wash border-b border-rule px-8 py-2.5 flex items-center justify-between gap-4 shadow-sm">
          <div className="min-w-0 flex items-baseline gap-3">
            <span className="folio shrink-0">Report</span>
            <span aria-hidden="true" className="text-rule-strong">·</span>
            <span className="text-label text-ink-muted truncate">{str(report.title) || 'Untitled'}</span>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={() => setSearchVisible(v => !v)}
              title="Find in report"
              aria-pressed={searchVisible}
              className="p-1.5 text-ink-faint hover:text-ink hover:bg-paper rounded-md transition-colors"
            >
              {searchVisible ? <X className="w-3.5 h-3.5" /> : <Search className="w-3.5 h-3.5" />}
            </button>
            <button
              type="button"
              onClick={handleCopy}
              title="Copy markdown"
              className="p-1.5 text-ink-faint hover:text-ink hover:bg-paper rounded-md transition-colors"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-ink" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            <button
              type="button"
              onClick={handleDownload}
              title="Download markdown"
              className="p-1.5 text-ink-faint hover:text-ink hover:bg-paper rounded-md transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
            </button>
            <span aria-hidden="true" className="w-px h-4 bg-rule mx-1" />
            {/* On a narrow window the apparatus is a drawer, so it needs a
                way in. On a wide one it is already visible and this
                button is redundant, so it is hidden there. */}
            <button
              type="button"
              onClick={() => setApparatusOpen(true)}
              title="Show evidence, gaps and terms"
              className="lg:hidden p-1.5 text-ink-faint hover:text-ink hover:bg-paper rounded-md transition-colors"
            >
              <BookOpen className="w-3.5 h-3.5" />
            </button>
            <ExportBtn label="PPTX" busy={exporting === 'deck'} onClick={() => void handleExport('deck')} />
            <ExportBtn label="DOCX" busy={exporting === 'docx'} onClick={() => void handleExport('docx')} />
            <ExportBtn label="PDF" busy={exporting === 'pdf'} onClick={() => void handleExport('pdf')} />
          </div>
        </div>

        {exportError ? (
          <div
            role="status"
            className="shrink-0 bg-paper-inset border-b border-rule-strong px-8 py-2.5 text-label text-ink flex items-center justify-between gap-3"
          >
            <span className="truncate">{exportError}</span>
            <button
              type="button"
              onClick={() => setExportError('')}
              className="text-ink-muted hover:text-ink shrink-0"
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        ) : null}

        {searchVisible && (
          <div className="shrink-0 bg-paper-raised border-b border-rule px-8 py-2.5 flex items-center gap-2.5">
            <Search className="w-3.5 h-3.5 text-ink-faint shrink-0" />
            <input
              ref={searchRef}
              type="text"
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              placeholder="Search this report"
              className="flex-1 bg-transparent text-label text-ink focus:outline-none placeholder:text-ink-faint"
            />
            {searchText && (
              <button
                type="button"
                onClick={() => setSearchText('')}
                className="text-ink-faint hover:text-ink"
                aria-label="Clear search"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}

          {/* The sheet. The measure is fixed; the sheet is centred in
              whatever space the margins leave. */}
          <div ref={contentRef} className="flex-1 overflow-y-auto min-h-0 px-4">
            <article ref={articleRef} className="max-w-[72ch] mx-auto px-10 pt-12 pb-24 my-6 bg-paper-raised sheet-raised card-beveled border border-rule/70 animate-settle shadow-sheet dark:shadow-sheet-dark">
              <h1 className="font-display text-display-lg text-ink text-balance">
                {str(report.title) || 'Report'}
              </h1>

              {report.userMessage ? (
                <div
                  role="status"
                  className="mt-6 border border-rule-strong/25 bg-paper-inset intaglio-well px-4 py-3 rounded-md"
                >
                  <p className="text-label text-ink leading-relaxed">{str(report.userMessage)}</p>
                </div>
              ) : null}

              {/* The colophon: the facts about this document, set as a
                  colophon on a title page rather than as a status strip. */}
              <div className="mt-5 pb-8 border-b border-rule flex items-center gap-3 text-micro text-ink-faint">
                <span className="tnum">{dateLabel(report.generatedAt)}</span>
                <span aria-hidden="true">·</span>
                <span className="tnum">
                  {num(report.sourcesCount)} {num(report.sourcesCount) === 1 ? 'source' : 'sources'}
                </span>
                <span aria-hidden="true">·</span>
                <span className="tnum">
                  {num(report.entitiesCount)} {num(report.entitiesCount) === 1 ? 'entity' : 'entities'}
                </span>
              </div>

              <div className="mt-8">
                {blockDoc ? (
                  <BlockEditor
                    jobId={report.id}
                    doc={_withoutDuplicateTitle(blockDoc, report.title)}
                    onDocumentChange={setBlockDoc}
                  />
                ) : (
                  <div className="prose">
                    <ReportMarkdown
                      sources={sourcesFromLines([report.markdownContent])}
                      text={(() => {
                        let content = filteredContent;
                        const title = (report.title || '').trim().toLowerCase();
                        if (title) {
                          const lines = content.split('\n');
                          if (lines[0] && lines[0].replace(/^#+\s*/, '').trim().toLowerCase() === title) {
                            content = lines.slice(1).join('\n').trimStart();
                          }
                        }
                        return content;
                      })()}
                    />
                  </div>
                )}
              </div>
            </article>
          </div>
        </div>

        {/* Right margin — the apparatus: trust, gaps, terms. On a wide window
            it is a column of the page; on a narrow one it is a drawer that
            slides over the document, so the text never loses width. */}
        <ReportInsights
          jobId={report.id}
          enabled
          drawerOpen={apparatusOpen}
          onDismiss={() => setApparatusOpen(false)}
        />
      </div>
    </div>
  );
};
export default ReportsViewer;
