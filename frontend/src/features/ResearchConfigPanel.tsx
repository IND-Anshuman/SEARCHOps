import React, { useMemo, useState } from 'react';
import { AlertTriangle, RotateCcw, SlidersHorizontal } from 'lucide-react';
import {
  CONFIG_BOUNDS,
  DEFAULT_CONFIG,
  REPORT_THEMES,
  REPORT_TYPES,
  estimateCost,
  validateConfig,
  type ResearchConfig,
} from '../shared/types/researchConfig';

/**
 * Advanced run settings.
 *
 * Previously a dense block of tiny (9-10px) uppercase labels with long
 * internal hints ("How many pages this run may read"). Labels are now plain
 * sentences at a readable size, and the cost estimate keeps its honest
 * "estimate, not measured" caveat.
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

  const setStr = <K extends keyof ResearchConfig>(key: K, v: string) => {
    onChange({ ...value, [key]: v });
  };

  return (
    <div className="border border-rule rounded-md bg-paper">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2 text-label text-ink-muted">
          <SlidersHorizontal className="w-3.5 h-3.5" />
          Advanced settings
        </span>
        <span className="text-micro text-ink-faint">
          {value.maxPages} pages · depth {value.maxDepth}
        </span>
      </button>

      {open ? (
        /* The expanded panel is tall. It gets its own bounded scroll so its
           last control is always reachable, whatever the composer around it
           is doing. */
        <div className="px-4 pb-4 space-y-4 border-t border-rule pt-4 max-h-[46vh] overflow-y-auto">
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

          <Select
            label="Report type"
            value={value.reportType}
            onChange={(v) => setStr('reportType', v)}
            options={REPORT_TYPES.map((rt) => ({ value: rt.value, label: rt.label, hint: rt.desc }))}
          />

          <Slider
            bound={{ min: 1, max: 100, step: 1, label: 'Target pages', hint: 'Approximate page count. The report is not padded to hit it, and it does not stop short either.' }}
            value={value.targetPages}
            onChange={(v) => set('targetPages', v)}
          />

          <Select
            label="PDF theme"
            value={value.reportTheme}
            onChange={(v) => setStr('reportTheme', v)}
            options={REPORT_THEMES.map((t) => ({ value: t.value, label: t.label, hint: t.desc }))}
          />

          {value.reportType === 'custom' ? (
            <div>
              <label className="block text-label text-ink-muted mb-1" htmlFor="custom-structure">
                Custom report structure
              </label>
              <textarea
                id="custom-structure"
                value={value.reportStructurePrompt}
                onChange={(e) => setStr('reportStructurePrompt', e.target.value)}
                placeholder="How should the report be structured?"
                rows={3}
                className="w-full bg-paper border border-rule rounded-md px-2.5 py-2 text-label text-ink focus:outline-none focus:border-ink focus:ring-1 focus:ring-ink resize-y"
              />
            </div>
          ) : null}

          {value.verifySamplingRate < 1 ? (
            <p className="text-micro text-ink leading-relaxed">
              Only {Math.round(value.verifySamplingRate * 100)}% of claims will be checked
              against their sources. The report will say which claims are unverified.
            </p>
          ) : null}

          {problems.length > 0 ? (
            <div role="alert" className="border border-rule-strong bg-paper-inset rounded-md px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-micro font-medium text-ink">
                <AlertTriangle className="w-3.5 h-3.5" />
                This run would be refused
              </p>
              <ul className="mt-1.5 space-y-1">
                {problems.map((p) => (
                  <li key={p} className="text-micro text-ink/90 leading-relaxed">{p}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="flex items-center justify-between pt-1 border-t border-rule">
            <span className="text-micro text-ink-faint">
              Estimated ${estimate.toFixed(4)} <span className="text-ink-faint/70">(estimate, not measured)</span>
            </span>
            <button
              type="button"
              onClick={() => onChange(DEFAULT_CONFIG)}
              className="flex items-center gap-1.5 text-micro text-ink-muted hover:text-ink"
            >
              <RotateCcw className="w-3.5 h-3.5" />
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
    <span className="flex items-baseline justify-between gap-3 mb-1">
      <span className="text-label text-ink-muted">{bound.label}</span>
      <span className="text-micro text-ink font-mono">
        {format ? format(value) : value.toLocaleString()}
      </span>
    </span>
    <input
      type="range"
      min={bound.min}
      max={bound.max}
      step={bound.step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full accent-accent"
    />
    <span className="block text-micro text-ink-faint mt-1 leading-relaxed">{bound.hint}</span>
  </label>
);

const Select: React.FC<{
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string; hint: string }[];
}> = ({ label, value, onChange, options }) => (
  <div>
    <label className="block text-label text-ink-muted mb-1">{label}</label>
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full bg-paper border border-rule rounded-md px-2.5 py-2 text-label text-ink focus:outline-none focus:border-ink focus:ring-1 focus:ring-ink"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
    <p className="text-micro text-ink-faint mt-1 leading-relaxed">
      {options.find((o) => o.value === value)?.hint}
    </p>
  </div>
);

export default ResearchConfigPanel;
