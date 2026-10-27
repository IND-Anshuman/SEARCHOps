# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences share one product, and the design must serve both without splitting into two products:

- **Researchers and analysts** who ask a question, then need to *trust* the answer — read it, check it, cite it, and know what the run could not establish. For them the report is the product; the machinery is backstage.
- **Engineers and operators** who run the pipeline, watch it work, and intervene when it stalls or fails. For them the pipeline view is the instrument; density and control beat polish.

The report is the hero. The pipeline is backstage. A view that serves the operator must still look like it belongs to the same object as the report.

## Product Purpose

SEARCHOps takes a question, runs an autonomous multi-stage research pipeline (plan → search → scrape → extract → critique → verify → write → evaluate), and returns a report whose claims are individually cited and individually graded.

Success is not "an answer was produced". Success is that a reader can look at any claim in the report, see where it came from, and see whether the run could support it. Every design decision follows from that: provenance is visible, uncertainty is named, and nothing is presented more confidently than the evidence allows.

## Positioning

The mechanism that distinguishes it: **evidence is a first-class, inspectable object**. Claims carry their own citations, the critic's coverage gaps survive to the surface, verification status is computed per claim, and the report is a block model that can be audited rather than a string of prose. Most research tools hand back prose and ask for trust. This one hands back a report you can check.

The honesty contract is the product, not a disclaimer: an empty study guide says so rather than inventing questions; a glossary entry with no description stays empty; a report that could not be written says why; "no gaps found" is never shown when gaps existed but were lost.

## Operating Context

A single analyst or engineer sits at a workstation, running a question through a pipeline that takes minutes, and reads the result carefully enough to cite it. The work is done in one sitting: start a run, watch it, read the report, export it. There is no team feature, no collaboration, no account system. The tool is used in long focused sessions where the report is the deliverable that leaves the building.

## Capabilities and Constraints

- Views: Research (compose + log), Pipeline (live graph), Knowledge graph, Vector search, Reports (read + block edit).
- Derived artifacts surfaced alongside a report: trust summary, coverage gaps, glossary, study guide, FAQ.
- Exports: PDF, DOCX, PPTX, each rendered from the same canonical block document.
- A run is a Redis-backed job with a 1-hour TTL; state does not survive a server restart.
- The backend is a FastAPI service on :8000; the SPA is served from `frontend/dist` in production and Vite on :3000 in development.
- The full pipeline is LLM-driven and slow (5–7 minutes). The UI must make a long wait legible and calm, never anxious.
- Real measured metrics exist and must be shown as measured: citation coverage, hallucination rate, claim status mix, per-node latency and token cost.

## Brand Commitments

- Name: **SEARCHOps**.
- The product is an instrument, not a toy. It should feel like precision equipment — considered, quiet, and confident enough not to shout.
- No chromatic accent. The user chose a bone/ivory and near-black world deliberately: high contrast, letterpress/archive register, colour reserved for meaning that would otherwise be lost in words.
- The report must read as a document set on a desk, not as a chat transcript or a dashboard widget.
- Copy is plain and human. Name the action, name the problem, name the recovery. No internal jargon in user-facing text.

## Evidence on Hand

- Real pipeline output: 19–20 claims per run, 1–9 entities, real coverage gaps, real citation URLs.
- Real per-node latency and token cost from the LangGraph stream.
- Real measured evaluation metrics (`corpus_chars`, `hallucination_rate`, `citation_coverage`, `status_mix`).
- Real exports in PDF, DOCX and PPTX, rendered from the canonical block document.
- **No** testimonials, customer logos, press, pricing or benchmark numbers exist. Nothing in the UI may imply them.

## Product Principles

1. **The report is the product.** Every other view is backstage, and should read as such.
2. **Provenance is visible.** A claim's evidence should be one glance away, always.
3. **Uncertainty is named.** Gaps, contested claims and unmeasured metrics are stated, never smoothed over.
4. **Measured is not estimated.** Show the real number, or say it was not measured. Never a placeholder.
5. **A long wait should be calm.** The pipeline is minutes long; the interface should make that legible without performing urgency.

## Accessibility & Inclusion

WCAG AA: body and placeholder text ≥4.5:1, large text ≥3:1. Keyboard operation for every control, with visible focus. Reduced-motion honoured. Tabular numerals for all measured figures. The ivory/near-black world is chosen for contrast, not decoration, and must not regress below AA.
