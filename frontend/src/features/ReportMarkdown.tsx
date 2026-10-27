import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export type SourceRef = { url: string; title: string };

const NUMBERED_SOURCE = /^\s*(\d+)\.\s*\[([^\]]+)\]\(([^)\s]+)\)\s*$/;

/**
 * The report's numbered source list ("3. [Title](url)" lines under Sources),
 * keyed by number. The writer numbers sources once for the whole report, so
 * every [n] marker in the prose resolves against this one list.
 */
export function sourcesFromLines(texts: string[]): Map<number, SourceRef> {
  const map = new Map<number, SourceRef>();
  for (const text of texts) {
    for (const line of text.split('\n')) {
      const m = NUMBERED_SOURCE.exec(line);
      if (m) map.set(Number(m[1]), { title: m[2], url: m[3] });
    }
  }
  return map;
}

/**
 * Turn each resolvable `[n]` into a `#cite-n` link so the Markdown renderer
 * hands it to the citation component. Unresolvable markers stay as text, and
 * `[label](url)` links are left alone.
 */
function linkCitations(text: string, sources: Map<number, SourceRef>): string {
  if (!sources.size) return text;
  return text.replace(/\[(\d{1,3})\](?!\()/g, (whole, n: string) =>
    sources.has(Number(n)) ? `[\\[${n}\\]](#cite-${n})` : whole,
  );
}

function textOf(node: React.ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (React.isValidElement(node)) return textOf((node.props as { children?: React.ReactNode }).children);
  return '';
}

const CALLOUT = /^\s*(key takeaway|bottom line)\b/i;

/**
 * Renders one piece of report Markdown — a paragraph, list, table or quote —
 * with GFM tables, inline emphasis, links and superscript citations.
 *
 * Blocks used to be printed as raw text, so a table arrived as rows of pipes
 * and every `**term**` kept its asterisks; the structure the writer produced
 * reached the reader as clutter.
 */
export const ReportMarkdown: React.FC<{ text: string; sources: Map<number, SourceRef> }> = ({
  text,
  sources,
}) => (
  <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    components={{
      a: ({ href, children }) => {
        const cite = href?.startsWith('#cite-') ? sources.get(Number(href.slice(6))) : undefined;
        if (cite) {
          return (
            <sup className="cite">
              <a href={cite.url} target="_blank" rel="noopener noreferrer" title={cite.title}>
                {children}
              </a>
            </sup>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
      table: ({ children }) => (
        <div className="table-wrap">
          <table>{children}</table>
        </div>
      ),
      blockquote: ({ children }) => (
        <blockquote className={CALLOUT.test(textOf(children)) ? 'callout' : undefined}>{children}</blockquote>
      ),
    }}
  >
    {linkCitations(text, sources)}
  </ReactMarkdown>
);
