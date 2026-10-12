import React, { useMemo, useState } from 'react';
import { AlertTriangle, RotateCcw, SlidersHorizontal } from 'lucide-react';
import {
  CONFIG_BOUNDS,
  DEFAULT_CONFIG,
  estimateCost,
  validateConfig,
  type ResearchConfig,
} from '../shared/types/researchConfig';

/**
 * Advanced research configuration.
 *
 * Every control is clamped to the server's bound and the cost is labelled as
 * an estimate. The point is that a user can see what they are about to spend
 * before they spend it — and that an inconsistent plan is refused here with a
 * reason rather than silently truncated at run time.
 *
 * The types and bounds live in shared/types/researchConfig so the store can
 * import them without a cycle back into features/.
 */
export const ResearchConfigPanel: React.FC<{
  value: ResearchConfig;
  onChange: (next: ResearchConfig) => void;
}> = ({ value, onChange }) => {
  const [open, setOpen] = useState(false);

  const problems = useMemo(() => validateConfig(value), [value]);
  const estimate = useMemo(() => estimateCost(value), [value]);

  const set = <K extends keyof ResearchConfig>(key: K, v: number) => {
    onChange({ ...value, [key]: v });
  };

  return (
    <div className="border border-slate-800 rounded-lg bg-slate-900/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-slate-300">
          <SlidersHorizontal className="w-3.5 h-3.5 text-emerald-400" />
          Advanced
        </span>
        <span className="text-[10px] text-slate-500 font-mono">
          {value.maxPages} pages · depth {value.maxDepth}
          {value.verifySamplingRate < 1 ? ` · ${Math.round(value.verifySamplingRate * 100)}% verified` : ''}
        </span>
      </button>

      {open ? (
        <div className="px-3 pb-3 space-y-3 border-t border-slate-800 pt-3">
          <Slider
            bound={CONFIG_BOUNDS.maxPages}
            value={value.maxPages}
            onChange={(v) => set('maxPages', v)}
          />
          <Slider bound={CONFIG_BOUNDS.maxDepth} value={value.maxDepth} onChange={(v) => set('maxDepth', v)} />
          <Slider
            bound={CONFIG_BOUNDS.verifySamplingRate}
            value={value.verifySamplingRate}
            onChange={(v) => set('verifySamplingRate', v)}
            format={(v) => `${Math.round(v * 100)}%`}
          />
          <Slider
            bound={CONFIG_BOUNDS.maxReportSections}
            value={value.maxReportSections}
            onChange={(v) => set('maxReportSections', v)}
          />
          <Slider
            bound={CONFIG_BOUNDS.scrapeBudget}
            value={value.scrapeBudget}
            onChange={(v) => set('scrapeBudget', v)}
          />

          {value.verifySamplingRate < 1 ? (
            <p className="text-[10px] text-amber-300/80 font-sans leading-relaxed">
              Only {Math.round(value.verifySamplingRate * 100)}% of claims will be checked against
              their sources. The report will say which claims are unverified.
            </p>
          ) : null}

          {problems.length > 0 ? (
            <div role="alert" className="border border-red-500/30 bg-red-500/5 rounded px-2.5 py-2">
              <p className="flex items-center gap-1.5 text-[10px] font-bold uppercase text-red-300">
                <AlertTriangle className="w-3 h-3" />
                This run would be refused
              </p>
              <ul className="mt-1 space-y-0.5">
                {problems.map((p) => (
                  <li key={p} className="text-[10px] text-red-200/80 font-sans">{p}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="flex items-center justify-between pt-1 border-t border-slate-800">
            <span className="text-[10px] text-slate-500 font-mono">
              Estimated ${estimate.toFixed(4)} <span className="text-slate-600">(estimate, not measured)</span>
            </span>
            <button
              type="button"
              onClick={() => onChange(DEFAULT_CONFIG)}
              className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 font-mono"
            >
              <RotateCcw className="w-3 h-3" />
              Reset
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};

const Slider: React.FC<{
  bound: { min: number; max: number; step: number; label: string; hint: string };
  value: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
}> = ({ bound, value, onChange, format }) => (
  <label className="block">
    <div className="flex items-baseline justify-between mb-1">
      <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">{bound.label}</span>
      <span className="text-[10px] text-emerald-400 font-mono">
        {format ? format(value) : value.toLocaleString()}
      </span>
    </div>
    <input
      type="range"
      min={bound.min}
      max={bound.max}
      step={bound.step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full accent-emerald-500"
    />
    <p className="text-[9px] text-slate-600 font-sans mt-0.5 leading-relaxed">{bound.hint}</p>
  </label>
);

export default ResearchConfigPanel;
