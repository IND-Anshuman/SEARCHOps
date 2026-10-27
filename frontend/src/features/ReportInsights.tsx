import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Compass,
  Loader2,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import type {
  EvidenceGap,
  GlossaryTerm,
  StudyGuide,
  TrustReport,
} from '../shared/types/workbench';
import { API_BASE } from '../shared/store/executionStore';
import { apiFetch } from '../shared/lib/api';
import { StatusCount, StatusMarkGlyph, markForStatus } from '../shared/components/StatusMark';

type Tab = 'next' | 'study' | 'glossary';

interface ReportInsightsProps {
  jobId: string;
  /** Rendered only for completed jobs; the parent decides. */
  enabled: boolean;
  /**
   * Whether the narrow-screen drawer is open. The parent owns it so there is
   * one truth: the drawer is opened by a button in the folio and closed by
   * the panel's own close control.
   */
  drawerOpen?: boolean;
  /** Called when the narrow-screen drawer is dismissed. */
  onDismiss?: () => void;
}

/**
 * Track whether the window is wide enough for the apparatus to sit beside
 * the document rather than over it.
 *
 * A media query would be enough for the styling, but the layout needs the
 * same answer in JavaScript: the panel is `position: absolute` when narrow,
 * and that cannot be expressed in a Tailwind class without duplicating the
 * breakpoint. One source of truth, read by both.
 */
function useIsWide(): boolean {
  const [isWide, setIsWide] = useState(() =>
    typeof window === 'undefined' ? true : window.innerWidth >= 1024,
  );

  useEffect(() => {
    const onResize = () => setIsWide(window.innerWidth >= 1024);
    window.addEventListener('resize', onResize);
    onResize();
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return isWide;
}

/**
 * The margin of the report.
 *
 * This rail carries the product's actual claim: that you can check the
 * report. So it is a set of *records* — a trust line, a list of gaps, a
 * glossary — set like the apparatus at the end of a paper, not like a
 * sidebar of status chips.
 *
 * The previous version was a 288px column of icons, uppercase micro-labels
 * and coloured pills. Colour now means exactly one thing: a status. A thing
 * is green because it is supported, ochre because it is contested, oxide
 * because it failed, and grey because it was not measured.
 */
export const ReportInsights: React.FC<ReportInsightsProps> = ({
  jobId,
  enabled,
  drawerOpen = false,
  onDismiss,
}) => {
  const [tab, setTab] = useState<Tab>('next');
  const [trust, setTrust] = useState<TrustReport | null>(null);
  const [gaps, setGaps] = useState<EvidenceGap[] | null>(null);
  const [glossary, setGlossary] = useState<GlossaryTerm[] | null>(null);
  const [study, setStudy] = useState<StudyGuide | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const isWide = useIsWide();

  const fetchJson = useCallback(
    async <T,>(path: string): Promise<T | null> => {
      try {
        const res = await apiFetch(`${API_BASE}${path}`);
        if (!res.ok) return null;
        return (await res.json()) as T;
      } catch {
        return null; // a failed fetch must never break the report view
      }
    },
    [],
  );

  // Trust and gaps are cheap and local — load them with the panel.
  useEffect(() => {
    if (!enabled || !jobId) return;
    let cancelled = false;

    void (async () => {
      const [trustRes, gapRes] = await Promise.all([
        fetchJson<TrustReport>(`/api/v1/derived/${jobId}/trust`),
        fetchJson<{ gaps: EvidenceGap[] }>(`/api/v1/derived/${jobId}/next-steps`),
      ]);
      if (cancelled) return;
      setTrust(trustRes);
      setGaps(gapRes?.gaps ?? []);
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled, jobId, fetchJson]);

  // Glossary and study guide are loaded on demand — one is free, one costs a
  // model call, and neither should fire until the user asks.
  const loadGlossary = useCallback(async () => {
    if (glossary !== null) return;
    setLoading('glossary');
    const res = await fetchJson<{ terms: GlossaryTerm[] }>(`/api/v1/derived/${jobId}/glossary`);
    setGlossary(res?.terms ?? []);
    setLoading(null);
  }, [glossary, jobId, fetchJson]);

  const loadStudy = useCallback(async () => {
    if (study !== null) return;
    setLoading('study');
    const res = await fetchJson<StudyGuide>(`/api/v1/derived/${jobId}/study-guide`);
    setStudy(res ?? { questions: [], note: 'Could not reach the study guide service.' });
    setLoading(null);
  }, [study, jobId, fetchJson]);

  useEffect(() => {
    if (tab === 'glossary') void loadGlossary();
    if (tab === 'study') void loadStudy();
  }, [tab, loadGlossary, loadStudy]);

  if (!enabled) return null;

  // On a wide window the apparatus is a column of the page and is always
  // present. On a narrow one it is a drawer, and a closed drawer is not
  // rendered at all — an overlay that sits invisibly on top of the document
  // would swallow clicks meant for the text.
  if (!isWide && !drawerOpen) return null;

  return (
    // The apparatus is a margin, and a margin gives way before the text
    // does. Below the `lg` breakpoint it becomes a drawer that slides over
    // the page instead of a column that squeezes it, so the document never
    // loses width to its own annotations.
    <div
      className={`w-[304px] bg-paper-raised paper-wash border-l border-rule flex flex-col shrink-0 ${
        isWide ? 'relative' : 'absolute right-0 inset-y-0 z-30 shadow-lift'
      }`}
    >
      <TrustHeader trust={trust} />

      <div className="flex border-b border-rule shrink-0" role="tablist">
        <TabButton active={tab === 'next'} onClick={() => setTab('next')} label="Gaps" />
        <TabButton active={tab === 'study'} onClick={() => setTab('study')} label="Study" />
        <TabButton active={tab === 'glossary'} onClick={() => setTab('glossary')} label="Terms" />
        {!isWide && onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="px-3 text-ink-faint hover:text-ink transition-colors"
            aria-label="Close the apparatus"
          >
            <XCircle className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-5">
        {tab === 'next' && <NextSteps gaps={gaps} />}
        {tab === 'study' && <StudyPanel study={study} loading={loading === 'study'} />}
        {tab === 'glossary' && <GlossaryPanel terms={glossary} loading={loading === 'glossary'} />}
      </div>
    </div>
  );
};

// ── Trust header ────────────────────────────────────────────────────────────

const TrustHeader: React.FC<{ trust: TrustReport | null }> = ({ trust }) => {
  if (!trust) {
    return (
      <div className="px-5 py-4 border-b border-rule">
        <div className="folio">Evidence</div>
        <p className="text-micro text-ink-faint mt-2">Checking claim support…</p>
      </div>
    );
  }

  const { summary } = trust;
  const problems = summary.contested + summary.refuted;

  return (
    <div className="px-5 py-4 border-b border-rule" role="status">
      <div className="flex items-baseline justify-between gap-2">
        <span className="folio">Evidence</span>
        {/* A count of the total, in mono, with its label in the interface
            face — the rule that mono never carries meaning alone. */}
        <span className="font-mono tnum text-micro text-ink-faint">
          {summary.total} <span className="font-sans">claims</span>
        </span>
      </div>

      {/* The headline set in the display face: this is the line a reader
          calibrates their trust by, and it deserves the voice of the report,
          not the voice of a widget. */}
      <p className="font-display text-display-sm leading-snug text-ink mt-3 text-balance">
        {trust.headline}
      </p>

      {/* The tally, as a ruled form: a mark, a number, a word. */}
      <dl className="mt-4 space-y-1.5">
        {summary.supported > 0 && (
          <div className="field">
            <span className="field-label">Supported</span>
            <span className="field-value flex items-center gap-1.5">
              <StatusMarkGlyph status="filled" className="text-ink" />
              {summary.supported}
            </span>
          </div>
        )}
        {summary.contested > 0 && (
          <div className="field">
            <span className="field-label">Contested</span>
            <span className="field-value flex items-center gap-1.5">
              <StatusMarkGlyph status="half" className="text-ink" />
              {summary.contested}
            </span>
          </div>
        )}
        {summary.refuted > 0 && (
          <div className="field">
            <span className="field-label">Refuted</span>
            <span className="field-value flex items-center gap-1.5">
              <StatusMarkGlyph status="empty" className="text-ink" />
              {summary.refuted}
            </span>
          </div>
        )}
        {summary.unverified > 0 && (
          <div className="field">
            <span className="field-label">Unverified</span>
            <span className="field-value flex items-center gap-1.5">
              <StatusMarkGlyph status="dotted" className="text-ink" />
              {summary.unverified}
            </span>
          </div>
        )}
      </dl>

      {problems > 0 && (
        <p className="text-micro text-ink-muted mt-4 leading-relaxed flex items-start gap-2">
          <StatusMarkGlyph status="half" className="text-ink mt-[3px]" />
          <span>
            {problems} {problems === 1 ? 'finding needs' : 'findings need'} checking before you rely on it.
          </span>
        </p>
      )}
    </div>
  );
};

// ── Gaps ────────────────────────────────────────────────────────────────────

const NextSteps: React.FC<{ gaps: EvidenceGap[] | null }> = ({ gaps }) => {
  if (gaps === null) {
    return <PanelNote>Reading the run's coverage gaps…</PanelNote>;
  }
  if (gaps.length === 0) {
    return (
      <div className="text-label text-ink-muted leading-relaxed">
        <div className="flex items-center gap-1.5 text-ink mb-2">
          <ShieldCheck className="w-3.5 h-3.5" />
          <span className="folio">No gaps found</span>
        </div>
        The critic found no coverage gaps — this report appears complete for the
        question asked.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-micro text-ink-faint leading-relaxed">
        What this run could not establish
      </p>
      {gaps.map((gap, i) => (
        <div key={`${gap.targetQuery}-${i}`} className="border-t border-rule pt-3 first:border-t-0 first:pt-0">
          <div className="flex items-start gap-2">
            <Compass className="w-3.5 h-3.5 text-ink-faint mt-[2px] shrink-0" />
            <p className="text-label text-ink leading-relaxed">{gap.description}</p>
          </div>
          {gap.targetQuery && (
            <button
              type="button"
              className="mt-2 flex items-center gap-1 text-micro text-ink-muted hover:text-ink transition-colors"
              onClick={() => {
                // Hand the gap back to the chat workspace as a new research turn.
                const send = (window as unknown as { hermes?: { send?: (p: string) => void } })
                  .hermes?.send;
                send?.(`Research this gap: ${gap.targetQuery}`);
              }}
            >
              Research this
              <ChevronRight className="w-3 h-3" />
            </button>
          )}
        </div>
      ))}
    </div>
  );
};

// ── Study guide ─────────────────────────────────────────────────────────────

const StudyPanel: React.FC<{ study: StudyGuide | null; loading: boolean }> = ({ study, loading }) => {
  if (loading && study === null) {
    return (
      <PanelNote>
        <Loader2 className="w-3 h-3 animate-spin inline mr-1.5" />
        Generating questions from the report…
      </PanelNote>
    );
  }
  if (study === null) return <PanelNote>Loading…</PanelNote>;

  if (study.questions.length === 0) {
    // Honest empty state. Inventing questions here would teach the user
    // something untrue with the product's authority behind it.
    return (
      <div className="text-label text-ink-muted leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2">
          <BookOpen className="w-3.5 h-3.5 text-ink-faint" />
          <span className="folio">No study guide</span>
        </div>
        {study.note || 'Could not generate a study guide for this report.'}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-micro text-ink-faint">
        {study.questions.length} questions from this report
      </p>
      {study.questions.map((q: StudyGuide['questions'][number], i: number) => (
        <details key={i} className="group border-t border-rule pt-3 first:border-t-0 first:pt-0">
          <summary className="text-label text-ink cursor-pointer list-none flex items-start gap-2">
            <span className="font-mono tnum text-ink-faint text-micro mt-[3px] shrink-0">
              {String(i + 1).padStart(2, '0')}
            </span>
            <span className="leading-relaxed">{q.question}</span>
          </summary>
          <div className="pl-6 pt-1.5 text-label text-ink-muted leading-relaxed">
            {q.answer}
            {q.section && (
              <div className="text-micro text-ink-faint font-mono mt-1">from: {q.section}</div>
            )}
          </div>
        </details>
      ))}
    </div>
  );
};

// ── Glossary ────────────────────────────────────────────────────────────────

const GlossaryPanel: React.FC<{ terms: GlossaryTerm[] | null; loading: boolean }> = ({
  terms,
  loading,
}) => {
  if (loading && terms === null) {
    return (
      <PanelNote>
        <Loader2 className="w-3 h-3 animate-spin inline mr-1.5" />
        Reading the extracted entities…
      </PanelNote>
    );
  }
  if (terms === null) return <PanelNote>Loading…</PanelNote>;
  if (terms.length === 0) {
    return (
      <div className="text-label text-ink-muted leading-relaxed">
        No entities were extracted from this report's sources.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-micro text-ink-faint">{terms.length} terms from the sources</p>
      {terms.map((t, i) => (
        <div key={`${t.term}-${i}`} className="border-t border-rule pt-3 first:border-t-0 first:pt-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="font-display text-label text-ink leading-snug">{t.term}</span>
            {t.entityType && (
              <span className="text-micro text-ink-faint font-mono">{t.entityType}</span>
            )}
          </div>
          {t.definition ? (
            <p className="text-label text-ink-muted leading-relaxed mt-1">{t.definition}</p>
          ) : (
            // No description means no definition. Never invented.
            <p className="text-micro text-ink-faint italic mt-1">
              No description was extracted — see the sources.
            </p>
          )}
        </div>
      ))}
    </div>
  );
};

// ── Shared bits ─────────────────────────────────────────────────────────────

const PanelNote: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="text-label text-ink-faint leading-relaxed">{children}</div>
);

const TabButton: React.FC<{ active: boolean; onClick: () => void; label: string }> = ({
  active,
  onClick,
  label,
}) => (
  <button
    type="button"
    onClick={onClick}
    role="tab"
    aria-selected={active}
    className={`flex-1 py-2.5 text-micro font-medium uppercase tracking-[0.1em] transition-colors relative ${
      active
        ? 'text-ink after:absolute after:inset-x-3 after:bottom-0 after:h-[2px] after:bg-ink'
        : 'text-ink-faint hover:text-ink-muted'
    }`}
  >
    {label}
  </button>
);

export default ReportInsights;
