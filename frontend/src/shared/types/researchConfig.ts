/** Mirrors searchops.core.context.research.ResearchConfig, bounds included. */
export interface ResearchConfig {
  maxPages: number;
  maxDepth: number;
  maxContentChars: number;
  verifySamplingRate: number;
  maxReportSections: number;
  scrapeBudget: number;
  reportType: string;
  reportStructurePrompt: string;
  targetPages: number;
  reportTheme: string;
}

/** Hard bounds, mirrored from the server schema. The server re-validates. */
export const CONFIG_BOUNDS = {
  maxPages: { min: 1, max: 200, step: 1, label: 'Pages to fetch', hint: 'How many pages this run may read.' },
  maxDepth: { min: 1, max: 10, step: 1, label: 'Link depth', hint: '1 = only the pages found in search. Higher follows links.' },
  maxContentChars: { min: 1_000, max: 2_000_000, step: 10_000, label: 'Content budget (chars)', hint: 'Total page text this run will keep.' },
  verifySamplingRate: { min: 0.1, max: 1.0, step: 0.05, label: 'Claim verification', hint: 'Share of claims checked against sources. Lower is cheaper but leaves claims unverified.' },
  maxReportSections: { min: 1, max: 60, step: 1, label: 'Report sections', hint: 'Maximum sections in the finished report.' },
  scrapeBudget: { min: 1, max: 200, step: 1, label: 'Scrape budget', hint: 'Must be at least the page count, or the run is refused.' },
} as const;

export const DEFAULT_CONFIG: ResearchConfig = {
  maxPages: 10,
  maxDepth: 1,
  maxContentChars: 200_000,
  verifySamplingRate: 1.0,
  maxReportSections: 12,
  scrapeBudget: 10,
  reportType: 'auto',
  reportStructurePrompt: '',
  targetPages: 12,
  reportTheme: 'default',
};

export const REPORT_THEMES = [
  { value: 'default', label: 'Default', desc: 'Balanced typography with serif body and sans-serif headings.' },
  { value: 'minimal', label: 'Minimal', desc: 'Clean, compact layout with smaller margins and tighter spacing.' },
  { value: 'academic', label: 'Academic', desc: 'Formal academic style with numbered sections and generous whitespace.' },
  { value: 'executive', label: 'Executive', desc: 'Bold, high-contrast layout optimized for leadership reading.' },
] as const;

export const REPORT_TYPES = [
  { value: 'auto', label: 'Auto (infer from query)', desc: 'System picks the best structure based on your query.' },
  { value: 'executive_brief', label: 'Executive Brief', desc: 'Concise, decision-focused. Tables over prose.' },
  { value: 'detailed_analysis', label: 'Detailed Analysis', desc: 'In-depth report with methodology, findings, and discussion. Works for any domain.' },
  { value: 'comparative_analysis', label: 'Comparative Analysis', desc: 'Side-by-side comparisons with tables and pros/cons.' },
  { value: 'narrative', label: 'Narrative', desc: 'Storytelling arc: context → problem → solution → results.' },
  { value: 'academic_research', label: 'Academic Research', desc: 'Formal academic paper structure with abstract, definitions, analysis, and discussion.' },
  { value: 'business_analysis', label: 'Business Analysis', desc: 'Market context, competitors, financials, SWOT, and strategic recommendations.' },
  { value: 'policy_brief', label: 'Policy Brief', desc: 'Issue, stakeholders, policy options, impact assessment, and recommendations.' },
  { value: 'custom', label: 'Custom', desc: 'You define the report structure below.' },
] as const;

/** Cross-field checks, mirrored from ResearchConfig.validate_against_budget. */
export function validateConfig(cfg: ResearchConfig): string[] {
  const problems: string[] = [];
  if (cfg.maxPages > cfg.scrapeBudget) {
    problems.push(
      `Asking for ${cfg.maxPages} pages but the scrape budget only allows ${cfg.scrapeBudget}. Raise the budget or lower the page count.`,
    );
  }
  return problems;
}

/** Rough estimate. Labelled as an estimate in the UI, never as measured. */
export function estimateCost(cfg: ResearchConfig): number {
  const perPage = 0.002 + (cfg.maxDepth - 1) * 0.001;
  return cfg.maxPages * perPage;
}
