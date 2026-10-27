import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, Moon, Sun, CornerLeftUp } from 'lucide-react';
import { StatusMarkGlyph, type StatusMark } from '../shared/components/StatusMark';
import { GraphOrb } from './GraphOrb';

/**
 * The landing page. It explains the product in the product's own register:
 * a printed broadside, ink on paper, ruled. Everything it claims is true of
 * the code in this repository; it shows no metrics, logos or testimonials,
 * because none exist (PRODUCT.md, "Evidence on Hand").
 */

const APP_URL = '/app/';

// ── Small hooks ─────────────────────────────────────────────────────────────

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    try { localStorage.setItem('searchops:theme', dark ? 'dark' : 'light'); } catch { /* storage blocked */ }
  }, [dark]);
  return [dark, () => setDark((d) => !d)];
}

/** Adds `is-in` once the element scrolls into view. */
function useInView<T extends HTMLElement>(threshold = 0.2) {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { setInView(true); io.disconnect(); }
    }, { threshold });
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return [ref, inView] as const;
}

const Reveal: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => {
  const [ref, inView] = useInView<HTMLDivElement>(0.15);
  return <div ref={ref} className={`reveal ${inView ? 'is-in' : ''} ${className}`}>{children}</div>;
};

const Kicker: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
  <p className={`small-caps text-ink-faint ${className}`}>{children}</p>
);

/** The running head every section carries, like a printed page. */
const Folio: React.FC<{ left: string; right: string }> = ({ left, right }) => (
  <div className="flex items-baseline justify-between border-b border-rule-strong pb-2 small-caps text-ink-faint">
    <span>{left}</span>
    <span>{right}</span>
  </div>
);

// ── Masthead ────────────────────────────────────────────────────────────────

const Masthead: React.FC = () => {
  const [dark, toggle] = useTheme();
  return (
    <header className="sticky top-0 z-30 border-b border-rule bg-paper/85 backdrop-blur-sm">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <a href="/" className="font-display text-xl font-semibold tracking-tight text-ink">
          SEARCH<span className="italic font-normal">Ops</span>
        </a>
        <nav className="hidden items-center gap-6 md:flex" aria-label="Sections">
          {[
            ['#how', 'How it works'],
            ['#grading', 'Grading'],
            ['#memory', 'Memory'],
            ['#report', 'The report'],
          ].map(([href, label]) => (
            <a key={href} href={href} className="small-caps text-ink-muted hover:text-ink transition-colors">
              {label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggle}
            aria-label="Toggle colour theme"
            className="rounded-md border border-rule p-1.5 text-ink-muted transition-colors hover:border-rule-strong hover:text-ink"
          >
            {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>
          <a
            href={APP_URL}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink bg-ink px-3 py-1.5 text-label font-medium text-paper transition-transform hover:-translate-y-px"
          >
            Open the workbench <ArrowRight className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
    </header>
  );
};

// ── Hero: a stack of report sheets in 3D ────────────────────────────────────

const HeroSheet: React.FC = () => (
  <div className="relative h-full p-6 sm:p-7 text-ink">
    <div className="flex items-baseline justify-between border-b border-rule pb-1.5 small-caps text-[9px] text-ink-faint">
      <span>Research report</span>
      <span>Folio i</span>
    </div>
    <h3 className="mt-4 font-display text-[1.45rem] leading-[1.1] font-semibold tracking-tight">
      Where solid-state batteries stand
    </h3>
    <p className="mt-1 small-caps text-[9px] text-ink-faint">Illustrative sheet</p>
    <div className="double-rule mt-3" />
    <div className="dropcap mt-3 font-serif text-[12.5px] leading-[1.55] text-ink-muted">
      <p>
        Pilot lines exist, but volume production depends on electrolyte yields
        <sup className="font-mono text-[8px] text-ink-faint">[2]</sup> that no source yet reports at scale.
      </p>
    </div>
    <ul className="mt-4 space-y-2 text-[11.5px]">
      {([
        ['filled', 'Two manufacturers run pilot lines', '1,4'],
        ['half', 'Energy density gains hold at pack level', '3'],
        ['empty', 'Mass-market cars ship this year', '5'],
        ['dotted', 'Cost parity with Li-ion by 2028', ''],
      ] as [StatusMark, string, string][]).map(([mark, text, cite]) => (
        <li key={text} className="flex items-start gap-2 border-t border-rule pt-2">
          <StatusMarkGlyph status={mark} className="mt-[3px] text-ink" />
          <span className="flex-1 text-ink-muted">{text}</span>
          <span className="font-mono text-[9px] text-ink-faint">{cite ? `[${cite}]` : '—'}</span>
        </li>
      ))}
    </ul>
  </div>
);

const SheetStack: React.FC = () => {
  const stageRef = useRef<HTMLDivElement>(null);
  const stackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stage = stageRef.current;
    const stack = stackRef.current;
    if (!stage || !stack || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const onMove = (e: PointerEvent) => {
      const r = stage.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5;
      const py = (e.clientY - r.top) / r.height - 0.5;
      stack.style.setProperty('--ry', `${-18 + px * 22}deg`);
      stack.style.setProperty('--rx', `${14 - py * 16}deg`);
    };
    const onLeave = () => {
      stack.style.setProperty('--ry', '-18deg');
      stack.style.setProperty('--rx', '14deg');
    };
    stage.addEventListener('pointermove', onMove);
    stage.addEventListener('pointerleave', onLeave);
    return () => {
      stage.removeEventListener('pointermove', onMove);
      stage.removeEventListener('pointerleave', onLeave);
    };
  }, []);

  return (
    <div ref={stageRef} className="sheet-stage relative mx-auto h-[440px] w-full max-w-[360px] sm:h-[480px]">
      <div ref={stackRef} className="sheet-stack absolute inset-x-4 inset-y-6">
        {[3, 2, 1].map((d) => (
          <div
            key={d}
            data-depth={d}
            className="sheet sheet-under"
            style={{ transform: `translateZ(${-d * 14}px) translate(${d * 5}px, ${d * 4}px) rotateZ(${d * 1.2}deg)` }}
            aria-hidden
          />
        ))}
        <div className="sheet" data-depth="0" style={{ transform: 'translateZ(0)' }}>
          <HeroSheet />
        </div>
      </div>
    </div>
  );
};

const Hero: React.FC = () => (
  <section className="mx-auto grid max-w-6xl items-center gap-10 px-4 pb-16 pt-12 sm:px-6 md:grid-cols-[1.15fr_1fr] md:pt-20">
    <div className="animate-settle">
      <Kicker>An instrument for reading evidence</Kicker>
      <h1 className="mt-4 font-display text-[2.6rem] font-semibold leading-[1.02] tracking-[-0.03em] text-ink sm:text-[3.6rem] lg:text-[4.2rem]">
        Ask a question.<br />
        Get a report <span className="italic font-normal">you can check.</span>
      </h1>
      <p className="mt-6 max-w-[34rem] font-serif text-body-lg text-ink-muted">
        SEARCHOps runs your question through an autonomous research pipeline: it plans, searches,
        reads the sources, and checks every claim against more than one of them. You get back a report
        where each claim carries its citations and a grade, and the gaps it could not close are named.
      </p>
      <div className="mt-8 flex flex-wrap items-center gap-3">
        <a
          href={APP_URL}
          className="inline-flex items-center gap-2 rounded-md border border-ink bg-ink px-4 py-2.5 text-body font-medium text-paper transition-transform hover:-translate-y-0.5"
        >
          Start a research run <ArrowRight className="h-4 w-4" />
        </a>
        <a
          href="#how"
          className="inline-flex items-center gap-2 rounded-md border border-rule-strong px-4 py-2.5 text-body font-medium text-ink transition-colors hover:bg-paper-raised"
        >
          See how it works
        </a>
      </div>
      <p className="mt-6 text-micro text-ink-faint">
        No sign-in. A full run takes a few minutes; you can watch every stage while it works.
      </p>
    </div>
    <SheetStack />
  </section>
);

// ── The problem ─────────────────────────────────────────────────────────────

const Premise: React.FC = () => (
  <section className="border-y border-rule bg-paper-raised/60">
    <Reveal className="mx-auto grid max-w-6xl gap-8 px-4 py-14 sm:px-6 md:grid-cols-[14rem_1fr]">
      <Kicker className="pt-2">The premise</Kicker>
      <div>
        <p className="font-display text-[1.7rem] leading-[1.2] tracking-tight text-ink sm:text-[2.1rem]">
          Most research tools hand back prose and ask for your trust.
          <span className="text-ink-faint"> This one hands back a document you can audit, claim by claim.</span>
        </p>
        <div className="mt-8 grid gap-6 text-label text-ink-muted sm:grid-cols-3">
          {[
            ['Provenance is visible', 'Every claim is tied to the passage it came from, and the passage to its URL.'],
            ['Uncertainty is named', 'A claim sources disagree on is marked contested, not smoothed over.'],
            ['Gaps survive', 'What the run could not establish is listed in the report, not dropped.'],
          ].map(([h, t]) => (
            <div key={h} className="border-t border-rule-strong pt-3">
              <p className="font-medium text-ink">{h}</p>
              <p className="mt-1">{t}</p>
            </div>
          ))}
        </div>
      </div>
    </Reveal>
  </section>
);

// ── How it works: the pipeline, as a drawer of index cards ──────────────────

interface Stage { n: string; name: string; node: string; body: string }

const STAGES: Stage[] = [
  { n: '01', name: 'Plan', node: 'planner', body: 'Breaks your question into sub-questions and writes the search queries for each.' },
  { n: '02', name: 'Search', node: 'search', body: 'Runs the queries across web search providers and collects candidate sources.' },
  { n: '03', name: 'Rank', node: 'ranker', body: 'Scores and de-duplicates the results, keeping the sources most likely to answer.' },
  { n: '04', name: 'Read', node: 'scrape', body: 'Fetches the full pages through tiered scraping and files them as passages.' },
  { n: '05', name: 'Extract', node: 'extract_evidence', body: 'Pulls out claims, each grounded in a passage it can quote back.' },
  { n: '06', name: 'Critique', node: 'critic', body: 'Reads the evidence as a sceptic: thin coverage, single-source claims, missing angles.' },
  { n: '07', name: 'Verify', node: 'verifier', body: 'Checks each claim across sources and grades it supported, contested, refuted or unverified.' },
  { n: '08', name: 'Prune', node: 'state_pruner', body: 'Merges duplicate claims and compresses the working state before writing.' },
  { n: '09', name: 'Write', node: 'report_writer', body: 'Sets the report as a block document. Every sentence points at its claims and citations.' },
  { n: '10', name: 'Evaluate', node: 'evaluation', body: 'Measures the finished report: citation coverage, hallucination rate, claim status mix.' },
];

const StageCard: React.FC<{ stage: Stage; index: number; inView: boolean }> = ({ stage, index, inView }) => (
  <li className={`index-card ${inView ? 'is-in' : ''}`} style={{ ['--i' as string]: index }}>
    <div className="index-card-face h-full px-4 pb-4 pt-5">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-micro text-ink-faint">{stage.n}</span>
        <span className="font-mono text-[10px] text-ink-ghost">{stage.node}</span>
      </div>
      <h3 className="mt-3 font-display text-display-sm font-semibold text-ink">{stage.name}</h3>
      <div className="mt-2 border-t border-rule" />
      <p className="mt-2 text-label text-ink-muted">{stage.body}</p>
    </div>
  </li>
);

const HowItWorks: React.FC = () => {
  const [firstRef, firstIn] = useInView<HTMLOListElement>(0.15);
  const [secondRef, secondIn] = useInView<HTMLOListElement>(0.15);
  return (
    <section id="how" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20 sm:px-6">
      <Folio left="How it works" right="§ 1 · The pipeline" />
      <Reveal className="mt-8 grid gap-6 md:grid-cols-[1fr_1.2fr]">
        <h2 className="font-display text-display-lg font-semibold text-ink">Ten stages, one graph, a loop for what is missing.</h2>
        <p className="font-serif text-body-lg text-ink-muted">
          A run is a LangGraph state machine. Each stage is a node that reads the shared research state and
          adds to it. The stages stream their progress to the workbench as they run, so you can see which
          one is working, what it found and what it cost.
        </p>
      </Reveal>

      <div className="card-drawer mt-12">
        <p className="small-caps text-ink-faint">Gathering</p>
        <ol ref={firstRef} className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STAGES.slice(0, 4).map((s, i) => <StageCard key={s.n} stage={s} index={i} inView={firstIn} />)}
        </ol>

        <p className="mt-10 small-caps text-ink-faint">Judging</p>
        <ol ref={secondRef} className="mt-3 grid gap-4 sm:grid-cols-3">
          {STAGES.slice(4, 7).map((s, i) => <StageCard key={s.n} stage={s} index={i} inView={secondIn} />)}
        </ol>

        <Reveal className="mt-4">
          <div className="flex items-start gap-3 border border-dashed border-rule-strong bg-paper-inset/60 px-4 py-3 text-label text-ink-muted">
            <CornerLeftUp className="mt-0.5 h-4 w-4 shrink-0 text-ink" />
            <p>
              <span className="font-medium text-ink">The gap loop.</span> If the verifier leaves open gaps and the
              loop budget for your chosen depth allows, a gap analyst writes new queries and the run goes back to
              <span className="font-medium text-ink"> Plan</span>. It only loops when there is something new to search;
              otherwise it moves on to writing.
            </p>
          </div>
        </Reveal>

        <p className="mt-10 small-caps text-ink-faint">Writing</p>
        <WritingCards />
      </div>
    </section>
  );
};

const WritingCards: React.FC = () => {
  const [ref, inView] = useInView<HTMLOListElement>(0.15);
  return (
    <ol ref={ref} className="mt-3 grid gap-4 sm:grid-cols-3">
      {STAGES.slice(7).map((s, i) => <StageCard key={s.n} stage={s} index={i} inView={inView} />)}
    </ol>
  );
};

// ── Grading: the five marks, as tiles that turn over ────────────────────────

const MARKS: { mark: StatusMark; name: string; reads: string; detail: string }[] = [
  { mark: 'filled', name: 'Supported', reads: 'Settled', detail: 'Independent sources agree, and the passages say what the claim says.' },
  { mark: 'half', name: 'Contested', reads: 'Held open', detail: 'Sources disagree. Both sides are cited so you can judge.' },
  { mark: 'empty', name: 'Refuted', reads: 'Emptied out', detail: 'The evidence found says otherwise. It stays in the report, marked.' },
  { mark: 'dotted', name: 'Unverified', reads: 'Not yet filled', detail: 'Only one source, or none that could be read. Treat it as a lead.' },
  { mark: 'none', name: 'Not measured', reads: 'No mark', detail: 'The run did not grade this. It never pretends otherwise.' },
];

const FlipTile: React.FC<(typeof MARKS)[number]> = ({ mark, name, reads, detail }) => {
  const [flipped, setFlipped] = useState(false);
  return (
    <li className="flip h-56" data-flipped={flipped}>
      <button
        type="button"
        onClick={() => setFlipped((f) => !f)}
        aria-pressed={flipped}
        aria-label={`${name}: ${detail}`}
        className="flip-inner block w-full text-left"
      >
        <span className="flip-face flex flex-col justify-between p-4">
          <span className="small-caps text-ink-faint">{reads}</span>
          <span className="mark-xl text-ink">
            <StatusMarkGlyph status={mark} />
          </span>
          <span className="font-display text-display-sm font-semibold text-ink">{name}</span>
        </span>
        <span className="flip-face flip-back flex flex-col justify-between p-4">
          <span className="small-caps text-ink-faint">{name}</span>
          <span className="font-serif text-body text-ink">{detail}</span>
          <StatusMarkGlyph status={mark} className="text-ink" />
        </span>
      </button>
    </li>
  );
};

const Grading: React.FC = () => (
  <section id="grading" className="scroll-mt-20 border-y border-rule bg-paper-raised/60">
    <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
      <Folio left="Grading" right="§ 2 · The marks" />
      <Reveal className="mt-8 grid gap-6 md:grid-cols-[1fr_1.2fr]">
        <h2 className="font-display text-display-lg font-semibold text-ink">Every claim gets a mark. The mark is its shape, not its colour.</h2>
        <p className="font-serif text-body-lg text-ink-muted">
          There is no green tick or red cross. A status is the fill of a square, the way a printed form marks
          its boxes, so it reads the same in print, in greyscale and to someone who does not see colour.
          Turn a card over to read what each mark means.
        </p>
      </Reveal>
      <Reveal>
        <ul className="mt-12 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {MARKS.map((m) => <FlipTile key={m.name} {...m} />)}
        </ul>
      </Reveal>
    </div>
  </section>
);

// ── Memory: the knowledge graph and the stores behind it ────────────────────

const STORES: [string, string][] = [
  ['Redis', 'Live run state. Jobs are leased, so an interrupted run can resume.'],
  ['Qdrant', 'Passages as vectors, for retrieval across everything the run has read.'],
  ['Neo4j', 'Entities and the relations between them: the knowledge graph.'],
  ['PostgreSQL', 'Workflow records: runs, reports and their history.'],
];

const Memory: React.FC = () => (
  <section id="memory" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20 sm:px-6">
    <Folio left="Memory" right="§ 3 · What a run remembers" />
    <div className="mt-8 grid items-center gap-10 md:grid-cols-2">
      <Reveal>
        <h2 className="font-display text-display-lg font-semibold text-ink">Evidence is kept as objects, not as a transcript.</h2>
        <p className="mt-5 font-serif text-body-lg text-ink-muted">
          Sources are stored as passages, claims point at passages, and entities link claims together.
          That is what lets you search a run&apos;s evidence by meaning, explore it as a graph, and trace any
          sentence in the report back to the page it came from.
        </p>
        <dl className="mt-8 divide-y divide-rule border-y border-rule">
          {STORES.map(([name, body]) => (
            <div key={name} className="grid grid-cols-[7.5rem_1fr] gap-4 py-3">
              <dt className="font-mono text-data text-ink">{name}</dt>
              <dd className="text-label text-ink-muted">{body}</dd>
            </div>
          ))}
        </dl>
      </Reveal>
      <Reveal>
        <figure className="deboss relative aspect-square w-full overflow-hidden rounded-sm border border-rule-strong bg-paper-raised">
          <GraphOrb />
          <figcaption className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between border-t border-rule bg-paper-raised/80 px-3 py-2 small-caps text-[10px] text-ink-faint">
            <span>Knowledge graph</span>
            <span>Drag to turn</span>
          </figcaption>
        </figure>
      </Reveal>
    </div>
  </section>
);

// ── The report and what comes with it ───────────────────────────────────────

const Report: React.FC = () => (
  <section id="report" className="scroll-mt-20 border-y border-rule bg-paper-raised/60">
    <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
      <Folio left="The report" right="§ 4 · What you take away" />
      <Reveal className="mt-8 grid gap-10 md:grid-cols-[1.1fr_1fr]">
        <div>
          <h2 className="font-display text-display-lg font-semibold text-ink">A document, set in type, with its working shown.</h2>
          <p className="mt-5 font-serif text-body-lg text-ink-muted">
            The report is a block document, not a chat reply. Alongside it the run produces the apparatus
            a careful reader wants, all derived from the same evidence, and it exports to the formats that
            leave the building.
          </p>
          <div className="mt-8 flex flex-wrap gap-2">
            {['PDF', 'DOCX', 'PPTX'].map((f) => (
              <span key={f} className="rounded-sm border border-rule-strong px-2.5 py-1 font-mono text-data text-ink">{f}</span>
            ))}
          </div>
        </div>
        <ol className="divide-y divide-rule border-y border-rule-strong">
          {[
            ['Trust summary', 'How many claims landed in each mark, and how well the report is cited.'],
            ['Coverage gaps', 'What the critic flagged and the run could not close.'],
            ['Glossary', 'Terms the report uses, defined from the sources or left blank.'],
            ['Study guide', 'Questions to test your reading. Empty if the evidence cannot support one.'],
            ['FAQ', 'The follow-up questions the report answers directly.'],
          ].map(([h, t], i) => (
            <li key={h} className="grid grid-cols-[2rem_1fr] gap-3 py-3">
              <span className="font-mono text-micro text-ink-faint pt-0.5">{String(i + 1).padStart(2, '0')}</span>
              <div>
                <p className="font-medium text-ink">{h}</p>
                <p className="text-label text-ink-muted">{t}</p>
              </div>
            </li>
          ))}
        </ol>
      </Reveal>
    </div>
  </section>
);

// ── The honesty contract ────────────────────────────────────────────────────

const Contract: React.FC = () => (
  <section className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
    <Folio left="The contract" right="§ 5 · What it will not do" />
    <Reveal className="mt-10 grid gap-x-10 gap-y-6 sm:grid-cols-2">
      {[
        'It will not present a claim more confidently than its evidence allows.',
        'It will not say “no gaps found” when gaps existed but were lost.',
        'It will not invent study questions when the evidence cannot support them.',
        'It will not hide why a report could not be written.',
      ].map((line, i) => (
        <p key={line} className="flex gap-4 border-t border-rule-strong pt-4 font-serif text-body-lg text-ink">
          <span className="font-display text-display-sm italic text-ink-faint">{['i', 'ii', 'iii', 'iv'][i]}.</span>
          <span>{line}</span>
        </p>
      ))}
    </Reveal>
  </section>
);

// ── Close ───────────────────────────────────────────────────────────────────

const Closing: React.FC = () => (
  <section className="border-t border-rule-strong">
    <Reveal className="mx-auto max-w-6xl px-4 py-24 text-center sm:px-6">
      <span className="mx-auto mb-6 flex items-center justify-center gap-3 text-ink-ghost" aria-hidden>
        <span className="h-px w-16 bg-rule-strong" />
        <span className="h-2 w-2 rotate-45 border border-ink-faint" />
        <span className="h-px w-16 bg-rule-strong" />
      </span>
      <h2 className="mx-auto max-w-2xl font-display text-[2.2rem] font-semibold leading-[1.08] tracking-tight text-ink sm:text-display-xl">
        Bring a question you would have to cite.
      </h2>
      <a
        href={APP_URL}
        className="mt-8 inline-flex items-center gap-2 rounded-md border border-ink bg-ink px-5 py-3 text-body font-medium text-paper transition-transform hover:-translate-y-0.5"
      >
        Open the workbench <ArrowRight className="h-4 w-4" />
      </a>
    </Reveal>
    <footer className="border-t border-rule">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-5 small-caps text-ink-faint sm:flex-row sm:justify-between sm:px-6">
        <span>SEARCHOps</span>
        <span>Set in Fraunces, Source Serif and Instrument Sans</span>
      </div>
    </footer>
  </section>
);

export const Landing: React.FC = () => (
  <div className="relative z-[1] min-h-screen text-ink">
    <Masthead />
    <main>
      <Hero />
      <Premise />
      <HowItWorks />
      <Grading />
      <Memory />
      <Report />
      <Contract />
      <Closing />
    </main>
  </div>
);

export default Landing;
