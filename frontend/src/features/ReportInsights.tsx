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
import { API_BASE, API_KEY } from '../shared/store/executionStore';

type Tab = 'next' | 'study' | 'glossary';

interface ReportInsightsProps {
  jobId: string;
  /** Rendered only for completed jobs; the parent decides. */
  enabled: boolean;
}

/**
 * Insights panel for a finished report.
 *
 * Three things the backend already computed and nobody was showing:
 *  - "Where to go next": real coverage gaps from the critic, as follow-ups.
 *  - Study guide / FAQ: generated from the report's own blocks.
 *  - Glossary: the entities the extractor found, at zero LLM cost.
 *
 * Every empty state is honest. A study guide that could not be generated says
 * so rather than showing plausible-looking invented questions.
 */
export const ReportInsights: React.FC<ReportInsightsProps> = ({ jobId, enabled }) => {
  const [tab, setTab] = useState<Tab>('next');
  const [trust, setTrust] = useState<TrustReport | null>(null);
  const [gaps, setGaps] = useState<EvidenceGap[] | null>(null);
  const [glossary, setGlossary] = useState<GlossaryTerm[] | null>(null);
  const [study, setStudy] = useState<StudyGuide | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  const headers = useCallback(
    () => ({ 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) }),
    [],
  );

  const fetchJson = useCallback(
    async <T,>(path: string): Promise<T | null> => {
      try {
        const res = await fetch(`${API_BASE}${path}`, { headers: headers() });
        if (!res.ok) return null;
        return (await res.json()) as T;
      } catch {
        return null; // a failed fetch must never break the report view
      }
    },
    [headers],
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

  return (
    <div className="w-72 bg-slate-900/40 border-l border-slate-800 flex flex-col shrink-0">
      <TrustHeader trust={trust} />

      <div className="flex border-b border-slate-800 shrink-0">
        <TabButton active={tab === 'next'} onClick={() => setTab('next')} label="NEXT" />
        <TabButton active={tab === 'study'} onClick={() => setTab('study')} label="STUDY" />
        <TabButton active={tab === 'glossary'} onClick={() => setTab('glossary')} label="TERMS" />
      </div>

      <div className="flex-1 overflow-y-auto p-3">
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
      <div className="px-3 py-2.5 border-b border-slate-800 text-[10px] text-slate-600 font-mono">
        Checking claim support…
      </div>
    );
  }

  const { summary } = trust;
  const problems = summary.contested + summary.refuted;
  const Icon = problems > 0 ? AlertTriangle : CheckCircle2;
  const tone = problems > 0 ? 'text-amber-400 border-amber-500/30 bg-amber-500/5' : 'text-emerald-400 border-emerald-500/30 bg-emerald-500/5';

  return (
    <div className={`px-3 py-2.5 border-b border-slate-800 ${tone}`} role="status">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className="w-3.5 h-3.5 shrink-0" />
        <span className="text-[10px] font-bold uppercase tracking-wide">Evidence check</span>
      </div>
      <p className="text-[11px] leading-relaxed font-sans text-slate-200">{trust.headline}</p>
      {problems > 0 && (
        <p className="text-[10px] text-amber-300/80 mt-1 font-sans">
          {problems} {problems === 1 ? 'finding needs' : 'findings need'} checking before you rely on it.
        </p>
      )}
    </div>
  );
};

// ── Next steps ──────────────────────────────────────────────────────────────

const NextSteps: React.FC<{ gaps: EvidenceGap[] | null }> = ({ gaps }) => {
  if (gaps === null) {
    return <PanelNote>Loading coverage gaps…</PanelNote>;
  }
  if (gaps.length === 0) {
    return (
      <div className="text-[11px] text-slate-400 font-sans leading-relaxed">
        <div className="flex items-center gap-1.5 text-emerald-400 mb-1.5">
          <ShieldCheck className="w-3.5 h-3.5" />
          <span className="text-[10px] font-bold uppercase">No gaps found</span>
        </div>
        The critic found no coverage gaps — this report appears complete for the
        question asked.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-slate-500 font-mono uppercase tracking-wide">
        What this run could not establish
      </p>
      {gaps.map((gap, i) => (
        <div
          key={`${gap.targetQuery}-${i}`}
          className="border border-slate-800 rounded p-2 bg-slate-950/60"
        >
          <div className="flex items-start gap-1.5">
            <Compass className="w-3 h-3 text-cyan-400 mt-0.5 shrink-0" />
            <p className="text-[11px] text-slate-300 font-sans leading-relaxed">{gap.description}</p>
          </div>
          {gap.targetQuery && (
            <button
              type="button"
              className="mt-1.5 flex items-center gap-1 text-[10px] text-cyan-400 hover:text-cyan-300 font-mono"
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
      <div className="text-[11px] text-slate-400 font-sans leading-relaxed">
        <div className="flex items-center gap-1.5 text-slate-300 mb-1.5">
          <BookOpen className="w-3.5 h-3.5" />
          <span className="text-[10px] font-bold uppercase">No study guide</span>
        </div>
        {study.note || 'Could not generate a study guide for this report.'}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-slate-500 font-mono uppercase tracking-wide">
        {study.questions.length} questions from this report
      </p>
      {study.questions.map((q: StudyGuide['questions'][number], i: number) => (
        <details key={i} className="border border-slate-800 rounded bg-slate-950/60 group">
          <summary className="px-2 py-1.5 text-[11px] text-slate-200 font-sans cursor-pointer list-none flex items-start gap-1.5">
            <span className="text-emerald-400 font-mono text-[10px] mt-0.5">{i + 1}.</span>
            <span className="leading-relaxed">{q.question}</span>
          </summary>
          <div className="px-2 pb-2 pl-6 text-[11px] text-slate-400 font-sans leading-relaxed border-t border-slate-800/60 pt-1.5 mt-0.5">
            {q.answer}
            {q.section && (
              <div className="text-[9px] text-slate-600 font-mono mt-1">from: {q.section}</div>
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
        Loading entities…
      </PanelNote>
    );
  }
  if (terms === null) return <PanelNote>Loading…</PanelNote>;
  if (terms.length === 0) {
    return (
      <div className="text-[11px] text-slate-400 font-sans leading-relaxed">
        No entities were extracted from this report's sources.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-slate-500 font-mono uppercase tracking-wide">
        {terms.length} terms from the sources
      </p>
      {terms.map((t, i) => (
        <div key={`${t.term}-${i}`} className="border-b border-slate-800/60 pb-2 last:border-0">
          <div className="flex items-baseline gap-1.5 flex-wrap">
            <span className="text-[11px] text-slate-100 font-sans font-medium">{t.term}</span>
            {t.entityType && (
              <span className="text-[9px] text-violet-300/70 font-mono uppercase">{t.entityType}</span>
            )}
          </div>
          {t.definition ? (
            <p className="text-[10px] text-slate-400 font-sans leading-relaxed mt-0.5">{t.definition}</p>
          ) : (
            // No description means no definition. Never invented.
            <p className="text-[10px] text-slate-600 font-sans italic mt-0.5">
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
  <div className="text-[11px] text-slate-500 font-sans leading-relaxed">{children}</div>
);

const TabButton: React.FC<{ active: boolean; onClick: () => void; label: string }> = ({
  active,
  onClick,
  label,
}) => (
  <button
    type="button"
    onClick={onClick}
    className={`flex-1 py-1.5 text-[10px] font-bold uppercase tracking-wide transition ${
      active
        ? 'text-emerald-400 border-b-2 border-emerald-500 bg-slate-950/40'
        : 'text-slate-500 hover:text-slate-300 border-b-2 border-transparent'
    }`}
  >
    {label}
  </button>
);

export default ReportInsights;
