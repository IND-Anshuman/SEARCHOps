import React from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { money, num, pct, str, obj } from '../shared/lib/safe';
import { X, Copy, Check, ExternalLink } from 'lucide-react';

/**
 * Detail panel.
 *
 * The previous version used a different layout per item type (some
 * font-sans, some font-mono, some with pills) so the panel changed shape
 * depending on what you clicked. It now has one consistent structure: a
 * header, a key/value summary, then the payload.
 */
export const RightInspector: React.FC = () => {
  const { isInspectorOpen, closeInspector, inspectorSelection } = useWorkbenchStore();
  const [copied, setCopied] = React.useState(false);

  if (!isInspectorOpen || inspectorSelection.type === 'none') {
    return null;
  }

  const { type, data } = inspectorSelection;
  const d = obj(data);
  const status = str(d.status);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const title = (() => {
    switch (type) {
      case 'node': return str(d.label) || str(d.id) || 'Node';
      case 'entity': return str(d.name) || 'Entity';
      case 'chunk': return str(d.documentTitle) || 'Chunk';
      case 'report': return str(d.title) || 'Report';
      case 'citation': return 'Source';
      case 'prompt': return 'Prompt';
      default: return 'Details';
    }
  })();

  return (
    <aside className="w-80 shrink-0 bg-paper-raised border-l border-rule flex flex-col h-full z-20">
      <div className="px-3 py-3 border-b border-rule flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-micro uppercase tracking-[0.1em] text-ink-faint">{type}</p>
          <h3 className="text-label font-medium text-ink truncate mt-0.5">{title}</h3>
        </div>
        <button
          type="button"
          onClick={closeInspector}
          className="p-1 text-ink-faint hover:text-ink hover:bg-paper rounded-md transition-colors shrink-0"
          aria-label="Close inspector"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {/* ── Node ─────────────────────────────────────────────────────────── */}
        {type === 'node' && (
          <>
            <Summary
              rows={[
                ['Status', status || '—'],
                ['Type', str(d.type) || '—'],
                ['Latency', `${num(d.latencyMs)} ms`],
                ['Cost', `$${money(d.tokenCost)}`],
              ]}
            />

            {d.prompt ? (
              <Section label="Prompt template">
                <Pre text={str(d.prompt)} onCopy={() => handleCopy(str(d.prompt))} copied={copied} maxHeight="max-h-40" />
              </Section>
            ) : null}

            <Section label="Input">
              <Pre text={JSON.stringify(obj(d.inputPayload), null, 2)} maxHeight="max-h-44" />
            </Section>

            <Section label="Output">
              <Pre text={JSON.stringify(obj(d.outputPayload), null, 2)} maxHeight="max-h-44" />
            </Section>
          </>
        )}

        {/* ── Entity ───────────────────────────────────────────────────────── */}
        {type === 'entity' && (
          <>
            <Summary rows={[['Type', str(d.type) || '—']]} />
            {str(d.canonicalId) ? (
              <p className="text-micro text-ink-faint font-mono break-all">{str(d.canonicalId)}</p>
            ) : null}
            <Section label="Summary">
              <p className="text-micro text-ink-muted leading-relaxed">
                {str(d.summary) || 'No summary was generated for this entity.'}
              </p>
            </Section>
          </>
        )}

        {/* ── Chunk ────────────────────────────────────────────────────────── */}
        {type === 'chunk' && (
          <>
            <Summary
              rows={[
                ['Score', `${pct(d.similarityScore)}%`],
                ['Tokens', num(d.tokenCount).toLocaleString()],
              ]}
            />
            {str(d.sourceUrl) ? (
              <a
                href={str(d.sourceUrl)}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 text-micro text-ink hover:underline break-all"
              >
                <span className="truncate">{str(d.sourceUrl)}</span>
                <ExternalLink className="w-3.5 h-3.5 shrink-0" />
              </a>
            ) : null}
            <Section label="Content">
              <p className="text-micro text-ink-muted leading-relaxed">{str(d.chunkPreview)}</p>
            </Section>
          </>
        )}

        {/* ── Report ───────────────────────────────────────────────────────── */}
        {type === 'report' && (
          <Summary
            rows={[
              ['Generated', str(d.generatedAt) || '—'],
              ['Sources', num(d.sourcesCount).toLocaleString()],
              ['Entities', num(d.entitiesCount).toLocaleString()],
            ]}
          />
        )}

        {/* ── Citation ─────────────────────────────────────────────────────── */}
        {type === 'citation' && (
          <a
            href={str(data)}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 text-micro text-ink hover:underline break-all"
          >
            <span className="truncate">{str(data)}</span>
            <ExternalLink className="w-3.5 h-3.5 shrink-0" />
          </a>
        )}

        {/* ── Prompt ───────────────────────────────────────────────────────── */}
        {type === 'prompt' && (
          <Pre text={str(data)} onCopy={() => handleCopy(str(data))} copied={copied} maxHeight="max-h-96" />
        )}
      </div>
    </aside>
  );
};

const Summary: React.FC<{ rows: [string, string][] }> = ({ rows }) => (
  <dl className="divide-y divide-border border border-rule rounded-md bg-paper overflow-hidden">
    {rows.map(([label, value]) => (
      <div key={label} className="flex items-center justify-between gap-3 px-3 py-2">
        <dt className="text-micro text-ink-faint">{label}</dt>
        <dd className="text-micro text-ink font-mono truncate">{value}</dd>
      </div>
    ))}
  </dl>
);

const Section: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="space-y-1.5">
    <p className="text-micro uppercase tracking-[0.1em] text-ink-faint">{label}</p>
    {children}
  </div>
);

const Pre: React.FC<{
  text: string;
  onCopy?: () => void;
  copied?: boolean;
  maxHeight?: string;
}> = ({ text, onCopy, copied, maxHeight }) => (
  <div className="relative bg-paper border border-rule rounded-md">
    {onCopy ? (
      <button
        type="button"
        onClick={onCopy}
        className="absolute top-1.5 right-1.5 p-1 text-ink-faint hover:text-ink rounded transition-colors"
        aria-label="Copy"
      >
        {copied ? <Check className="w-3.5 h-3.5 text-ink" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    ) : null}
    <pre className={`text-micro text-ink-muted font-mono whitespace-pre-wrap break-words p-2.5 overflow-y-auto ${maxHeight ?? ''}`}>
      {text}
    </pre>
  </div>
);

export default RightInspector;
