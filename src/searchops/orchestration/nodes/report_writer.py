"""Report Writer node: plan → write sections → polish → front matter → assemble.

The report is built in five stages, each with a fixed job:

1. **Profile.** The requested ``ReportType`` resolves to a profile (audience,
   voice, section skeleton, closing section) — see ``llm/report_profiles.py``.
2. **Plan.** The planner sees the actual claims and returns a titled outline;
   code then gives every claim to at most one section, so sections do not
   repeat each other (``llm/report_planner.py``).
3. **Write.** Each section is written in parallel from its own claims, with a
   key question, a format (table, bullets, timeline...) and an output contract:
   a one-line key takeaway, ``###`` subheadings, short paragraphs.
4. **Polish.** Every section is cleaned in code — citation runs collapsed,
   headings normalised, tables repaired, long paragraphs split
   (``llm/report_polish.py``).
5. **Front matter + assembly.** One pass writes the title, executive summary,
   key findings and closing from what the sections concluded
   (``llm/report_synthesis.py``); code adds the evidence-at-a-glance table,
   limitations, methodology and the numbered Sources list.

Citations are resolvable by construction:

- One **source registry** numbers every page that backs a usable claim,
  once, for the whole report. Every ``[n]`` the writer sees, and the
  ``## Sources`` list at the end, use those same numbers.
- Any ``[n]`` that does not resolve to the registry is removed, so the
  report never carries a citation that points nowhere.
- Refuted claims never reach the writer; contested ones are labelled so the
  prose has to present them as disputed.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

import structlog

from searchops.llm.report_planner import PlannedSection, ReportPlan, ReportPlanner
from searchops.llm.report_polish import polish_section, validate_citations
from searchops.llm.report_profiles import SECTION_FORMATS, ReportProfile, resolve_profile
from searchops.llm.report_shaper import shape_report
from searchops.llm.report_synthesis import FrontMatter, synthesise_front_matter
from searchops.orchestration.nodes.claim_dedup import absorb_contained, dedupe_semantically

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.evidence import Claim
    from searchops.orchestration.states.research_state import ResearchState

__all__ = ["report_writer_node", "validate_citations"]

log = structlog.get_logger(__name__)

# Per-section output ceiling (reasoning models spend part of it thinking).
_SECTION_MAX_TOKENS = 5000
_SECTION_CONCURRENCY = 4
_MAX_ENTITIES_PER_SECTION = 12
_TOP_CLAIMS_FOR_SUMMARY = 24
# A grounded claim supports roughly this many words of honest prose; the
# report is not padded past what the evidence can carry.
_WORDS_PER_CLAIM = 90
_MIN_REPORT_WORDS = 600

_STATUS_ORDER = {"supported": 0, "unverified": 1, "contested": 2, "refuted": 3}


# ── Evidence preparation ─────────────────────────────────────────────────────


def usable_claims(claims: list[Claim]) -> list[Claim]:
    """Claims the report may use, strongest first. Refuted claims are dropped."""
    kept = [c for c in claims if c.status != "refuted" and c.text.strip()]
    kept = absorb_contained(dedupe_semantically(kept))
    return sorted(
        kept,
        key=lambda c: (_STATUS_ORDER.get(c.status, 1), -len(set(c.supporting_urls)), -c.confidence),
    )


def build_source_registry(
    claims: list[Claim], scraped: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Number every page that backs a usable claim, once, for the whole report."""
    titles = {str(d.get("url", "")): str(d.get("title", "") or "") for d in scraped if isinstance(d, dict)}
    numbering: dict[str, int] = {}
    registry: list[dict[str, Any]] = []
    for claim in claims:
        for url in [c.url for c in claim.citations] + list(claim.supporting_urls):
            if url and url.startswith("http") and url not in numbering:
                numbering[url] = len(registry) + 1
                title = titles.get(url) or next((c.title for c in claim.citations if c.url == url and c.title), "")
                registry.append({"n": numbering[url], "url": url, "title": title})
    return registry, numbering


def claim_refs(claim: Claim, numbering: dict[str, int]) -> list[int]:
    urls = [c.url for c in claim.citations] + list(claim.supporting_urls)
    return sorted({numbering[u] for u in urls if u in numbering})


def _claim_line(claim: Claim, numbering: dict[str, int]) -> str:
    refs = "".join(f"[{n}]" for n in claim_refs(claim, numbering)[:3])
    n_support = len(set(claim.supporting_urls))
    if claim.status == "supported":
        tag = f"verified by {n_support} sources" if n_support > 1 else "verified"
    elif claim.status == "contested":
        tag = "CONTESTED — present as disputed"
        if claim.verification_note:
            tag += f": {claim.verification_note[:140]}"
    else:
        tag = "single source"
    quote = next((c.excerpt for c in claim.citations if c.excerpt), "")
    line = f"- {claim.text} {refs} ({tag})"
    if quote and not _quote_adds_figures(quote, claim.text):
        line += f'\n  quote: "{quote[:180]}"'
    return line


_FIGURE = re.compile(r"\d+(?:[.,]\d+)?")


def _quote_adds_figures(quote: str, claim_text: str) -> bool:
    """True when the quote states numbers its claim does not.

    Such a quote is usually a whole comparison row; shown beside one claim, it
    hands the section figures that belong to sibling claims, and the section
    restates them.
    """
    return bool(set(_FIGURE.findall(quote)) - set(_FIGURE.findall(claim_text)))


def _section_entities(entities: list[Any], claims: list[Claim]) -> list[str]:
    """Entities that actually appear in this section's claims."""
    corpus = " ".join(c.text for c in claims).lower()
    lines: list[str] = []
    for e in entities:
        name = str(getattr(e, "name", "") or "").strip()
        if not name or name.lower() not in corpus:
            continue
        desc = str(getattr(e, "description", "") or "").strip()
        lines.append(f"- {name} ({getattr(e, 'entity_type', 'Entity')}){f': {desc[:140]}' if desc else ''}")
        if len(lines) >= _MAX_ENTITIES_PER_SECTION:
            break
    return lines


# ── Prompting ────────────────────────────────────────────────────────────────

_SECTION_SYSTEM = (
    "You are a principal research analyst writing one part of a publication-grade report. "
    "You write for a reader who skims: every part opens with its answer, is organised under "
    "clear subheadings, and uses tables and lists wherever they communicate better than prose. "
    "You never state a fact that is not in the evidence you were given."
)


def _build_section_prompt(
    *,
    query: str,
    profile: ReportProfile,
    section: PlannedSection,
    index: int,
    outline: list[PlannedSection],
    claims: list[Claim],
    numbering: dict[str, int],
    registry: list[dict[str, Any]],
    entities: list[Any],
    custom_instructions: str,
) -> str:
    """Build a focused prompt for one report section."""
    claim_lines = [_claim_line(c, numbering) for c in claims]
    used = {n for c in claims for n in claim_refs(c, numbering)}
    source_lines = [f"[{s['n']}] {s['title'] or s['url']}" for s in registry if s["n"] in used]
    entity_lines = _section_entities(entities, claims)
    others = "\n".join(f"- {s.name}: {s.key_question}" for s in outline if s is not section)
    subsections = (
        "\nSuggested ### subheadings (adapt if the evidence suggests better ones):\n"
        + "\n".join(f"- {s}" for s in section.subsections)
        if section.subsections
        else ""
    )

    return f"""You are writing one part of a {profile.label.lower()} answering: {query}
Reader: {profile.audience}
Voice: {profile.voice}

## Your part: "{section.name}" ({index} of {len(outline)})
Key question it must answer: {section.key_question or section.name}
Length: about {section.target_words} words — less if the evidence is thin. Never pad.
Layout: {SECTION_FORMATS[section.format]}{subsections}

Other parts of the report (do NOT cover their ground):
{others or "- none"}
{f"{chr(10)}## Instructions from the user (these take precedence){chr(10)}{custom_instructions}{chr(10)}" if custom_instructions else ""}
## Evidence — the ONLY facts you may state
Each finding lists the source numbers that back it, e.g. [3][7].

### Findings
{chr(10).join(claim_lines) if claim_lines else "No findings are assigned to this part."}

### Sources
{chr(10).join(source_lines) if source_lines else "None."}

### Entities mentioned (context only — not citable on their own)
{chr(10).join(entity_lines) if entity_lines else "None."}

## Output contract
1. The FIRST line is exactly: > **Key takeaway:** <one sentence that directly answers the key question> [n]
   The takeaway is concrete — it names the specific options, figures or conditions — and ends with its
   source numbers. Not acceptable: "X, Y and Z offer distinct approaches tailored to different needs."
2. Then the body, organised under 2-4 ### subheadings. Never use # or ## headings, and do not repeat the part's title.
3. Paragraphs are at most 4 sentences, one idea each. Lead with the point, then the evidence.
4. Put source numbers at the end of the sentence, bullet or table cell that states the fact — at most 3
   numbers in one place. If a whole paragraph rests on the same source, cite it once, at the end.
5. Use ONLY source numbers shown next to a finding above. Never invent a number or a fact. State what a
   finding says; a quote is there to confirm its wording, not as a source of further facts.
6. Prefer specifics: names, figures, dates, versions. Bold (**...**) at most one key term or figure per paragraph.
7. Tables: a header row, a separator row, the same number of cells in every row, short cells.
   A cell says "No", "None" or "Not supported" ONLY if a finding states that. When your findings simply
   do not cover a cell, write "—" (not covered). Absence of evidence is not evidence of absence.
8. Findings marked CONTESTED are presented as disputed ("one source reports ..., but this is disputed [n]").
9. Interpretation that connects findings is welcome, but frame it as analysis ("This suggests ...").
10. Do NOT write an introduction to the report, a conclusion, a summary of other parts, a references list,
    or any commentary about the evidence, this task, or "this section".

Write the part now, in Markdown."""


def _evidence_fallback_section(claims: list[Claim], numbering: dict[str, int]) -> str:
    """A section built straight from its findings when the model wrote nothing."""
    lines = [
        f"- {c.text.rstrip('.')} {''.join(f'[{n}]' for n in claim_refs(c, numbering)[:3])}".rstrip() + "."
        for c in claims[:12]
    ]
    return (
        "_This part lists the underlying findings directly; the model did not return a written analysis._\n\n"
        + "\n".join(lines)
    )


def _strip_duplicated_section_title(text: str, section_name: str) -> str:
    """Drop a section title the model echoed as prose beneath its own heading.

    Only a line that *is* the title (optionally wrapped in bold/italic
    markers) is dropped. A sentence that merely starts with the title is left
    alone, because that is legitimate prose.
    """
    lines = text.split("\n")
    stripped = [ln.strip() for ln in lines]
    cleaned = section_name.strip().strip("*#_ ")
    for i, line in enumerate(stripped):
        if line.startswith("#"):
            for j in range(i + 1, min(i + 4, len(lines))):
                if stripped[j].strip("*#_ ") == cleaned:
                    lines.pop(j)
                    return "\n".join(lines)
            return text
    return text


# ── Fixed parts of the report ────────────────────────────────────────────────


def _evidence_profile(claims: list[Claim], registry: list[dict[str, Any]]) -> tuple[str, list[tuple[str, str]]]:
    """Evidence-strength label and the at-a-glance rows, computed — never estimated."""
    n = len(claims) or 1
    supported = sum(1 for c in claims if c.status == "supported")
    corroborated = sum(1 for c in claims if len(set(c.supporting_urls)) >= 2)
    contested = sum(1 for c in claims if c.status == "contested")
    domains = {s["url"].split("://", 1)[-1].split("/", 1)[0] for s in registry}
    share = supported / n
    if share >= 0.7 and corroborated / n >= 0.3:
        strength = "Strong"
    elif share >= 0.4:
        strength = "Moderate"
    else:
        strength = "Limited"
    rows = [
        ("Sources cited", f"{len(registry)} pages across {len(domains)} sites"),
        ("Findings used", str(len(claims))),
        ("Verified against source text", f"{supported} ({round(100 * supported / n)}%)"),
        ("Confirmed by 2+ independent sources", str(corroborated)),
        ("Contested", str(contested)),
        ("Overall evidence strength", f"**{strength}**"),
    ]
    return strength, rows


def _front_matter_md(fm: FrontMatter, profile: ReportProfile, query: str, glance: list[tuple[str, str]]) -> str:
    parts = [f"# {fm.title}", f"_{profile.label} · Research question: {query.strip()}_", "## Executive Summary"]
    parts += fm.summary
    if fm.bottom_line:
        parts.append(f"> **Bottom line:** {fm.bottom_line}")
    if fm.key_findings:
        parts.append("### Key Findings")
        parts.append("\n".join(_point_line(p, "-") for p in fm.key_findings))
    parts.append("### Evidence at a Glance")
    parts.append("\n".join(["| Measure | Value |", "|---|---|", *[f"| {k} | {v} |" for k, v in glance]]))
    return "\n\n".join(parts)


def _point_line(p: Any, bullet: str) -> str:
    head = p.headline.strip().rstrip(".")
    if head and p.detail:
        return f"{bullet} **{head}.** {p.detail}"
    return f"{bullet} {p.detail or head}"


def _closing_md(fm: FrontMatter, profile: ReportProfile) -> str:
    if not (profile.closing_heading and fm.closing):
        return ""
    items = "\n".join(_point_line(p, f"{i}.") for i, p in enumerate(fm.closing, 1))
    return f"## {profile.closing_heading}\n\n{items}"


def _is_id_token(token: str, last: bool) -> bool:
    """Slug noise: hashes, letter-digit ids, and a trailing vowel-less suffix like "ppwff"."""
    t = token.lower()
    if t.isdigit():
        return len(t) > 4  # keep "8" and "2026", drop "1234567"
    return bool(
        (re.search(r"\d", t) and re.search(r"[a-z]", t) and (len(t) >= 6 or last))
        or (last and len(t) >= 5 and not re.search(r"[aeiouy]", t))
    )


# Path segments that name a format or a folder, not the page.
_GENERIC_SEGMENTS = frozenset({"html", "htm", "pdf", "abs", "index", "docs", "blog", "en", "post", "posts", "pulse"})


def source_label(title: str, url: str) -> str:
    """A readable label for a source: its title, or "site — page name" built from the URL."""
    title = " ".join((title or "").split())
    if title and not title.startswith("http"):
        return title.replace("[", "(").replace("]", ")")
    rest = url.split("://", 1)[-1]
    host, _, path = rest.partition("/")
    host = host.removeprefix("www.")
    segments = [s for s in path.split("?", 1)[0].split("#", 1)[0].split("/") if s]
    if host == "github.com" and len(segments) >= 2:
        return f"GitHub — {segments[0]}/{segments[1]}" + (f" ({segments[-1]})" if len(segments) > 2 else "")
    # The last segment that reads like words, not an id or file extension.
    words = ""
    for seg in reversed(segments):
        seg = re.sub(r"\.(html?|php|aspx?|md)$", "", seg, flags=re.IGNORECASE)
        if seg.lower() in _GENERIC_SEGMENTS:
            continue
        if host.endswith("arxiv.org") and re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", seg):
            return f"arXiv {seg}"
        tokens = [p for p in re.split(r"[-_+]", seg) if p]
        parts = [p for i, p in enumerate(tokens) if not _is_id_token(p, i == len(tokens) - 1)]
        if len(parts) >= 2 or (parts and len(parts[0]) > 3):
            words = " ".join(parts)
            break
    if not words:
        return host
    words = words[:1].upper() + words[1:]
    return f"{host} — {words}"


def _sources_section(registry: list[dict[str, Any]], scraped: list[dict[str, Any]]) -> str:
    # One source per paragraph, so each becomes its own immutable SOURCE
    # block in the report document and renders as a numbered link.
    lines = ["## Sources", ""]
    for s in registry:
        lines += [f"{s['n']}. [{source_label(s['title'], s['url'])}]({s['url']})", ""]
    cited = {s["url"] for s in registry}
    others = [d for d in scraped if isinstance(d, dict) and d.get("url") and d["url"] not in cited]
    if others:
        lines += ["", "### Also read (no claims extracted)", ""]
        for d in others:
            lines.append(f"- [{source_label(str(d.get('title') or ''), d['url'])}]({d['url']})")
    return "\n".join(lines)


def _limitations_section(claims: list[Claim], gaps: list[Any], failed: list[str]) -> str:
    """What the run could not establish — stated, never smoothed over."""
    refuted = [c for c in claims if c.status == "refuted"]
    contested = [c for c in claims if c.status == "contested"]
    open_gaps = [g for g in gaps if str(getattr(g, "description", "") or "").strip()]
    if not (refuted or contested or open_gaps or failed):
        return ""
    parts = ["## Limitations and Open Questions"]
    if open_gaps:
        parts.append("**Open questions.** The research ended with these points still unresolved:")
        parts.append("\n".join(f"- {g.description}" for g in open_gaps[:8]))
    if contested:
        parts.append(
            f"**Contested evidence.** {len(contested)} finding{'s' if len(contested) != 1 else ''} could not be "
            "confirmed by other sources and {} presented as uncertain where used.".format(
                "are" if len(contested) != 1 else "is"
            )
        )
    if refuted:
        parts.append(
            f"**Excluded claims.** {len(refuted)} claim{'s were' if len(refuted) != 1 else ' was'} contradicted "
            "by other sources and left out of this report:"
        )
        parts.append("\n".join(f"- ~~{c.text}~~" for c in refuted[:8]))
    if failed:
        parts.append(
            "**Thin coverage.** These parts are listed from the findings directly because no written "
            f"analysis could be produced: {', '.join(failed)}."
        )
    return "\n\n".join(parts)


def _methodology_section(n_claims: int, registry: list[dict[str, Any]], n_read: int, plan: ReportPlan) -> str:
    return (
        "## Methodology\n\n"
        f"{n_read} web pages were read for this report and {len(registry)} of them supplied usable evidence. "
        f"Each of the {n_claims} findings was extracted together with a verbatim quote from its page and then "
        "checked against passages from other pages; findings that other sources contradicted were excluded. "
        "Bracketed numbers such as [1] refer to the numbered Sources list below."
        + (
            " The outline was generated from a fixed template because the planning step failed."
            if plan.source == "skeleton"
            else ""
        )
    )


# ── Node ─────────────────────────────────────────────────────────────────────


async def report_writer_node(
    state: ResearchState,
    *,
    llm_router: LLMRouter,
) -> ResearchState:
    """Synthesise verified evidence into a structured, citable Markdown report."""
    query = state.get("query", "")
    scraped = state.get("scraped_contents", []) or []
    entities = state.get("entities", []) or []
    all_claims: list[Claim] = list(state.get("evidence_claims", []) or [])
    target_pages = int(state.get("target_pages", 12) or 12)
    custom = str(state.get("report_structure_prompt", "") or "").strip()
    _, profile = resolve_profile(state.get("report_type", ""))

    claims = usable_claims(all_claims)
    registry, numbering = build_source_registry(claims, scraped)
    valid_refs = set(numbering.values())

    if not claims:
        # No usable evidence: never ask a model to write from nothing.
        shaped = shape_report(None, query=query, claims=[], entities=entities, sources=scraped)
        return {
            "final_report": shaped.markdown,
            "citations": [d.get("url", "") for d in scraped if d.get("url")],
            "report_sources": [],
            "report_sections": [shaped.markdown],
            "report_quality": shaped.quality.value,
            "report_user_message": shaped.user_message,
        }

    # Never ask for more prose than the evidence can carry.
    body_words = min(
        target_pages * profile.words_per_page,
        max(_MIN_REPORT_WORDS, _WORDS_PER_CLAIM * len(claims)),
    )

    plan = await ReportPlanner(llm_router).plan(
        query=query,
        claims=claims,
        entities=entities,
        sources=registry,
        profile=profile,
        body_words=body_words,
        custom_instructions=custom,
    )
    by_id = {c.id: c for c in claims}

    log.info(
        "Report writer node executing",
        query=query,
        profile=profile.label,
        claims=len(claims),
        sources=len(registry),
        sections=len(plan.sections),
        body_words=body_words,
        plan_source=plan.source,
    )

    semaphore = asyncio.Semaphore(_SECTION_CONCURRENCY)

    async def _write(i: int, section: PlannedSection) -> tuple[str | None, list[Claim]]:
        section_claims = [by_id[cid] for cid in section.claim_ids if cid in by_id]
        prompt = _build_section_prompt(
            query=query,
            profile=profile,
            section=section,
            index=i,
            outline=plan.sections,
            claims=section_claims,
            numbering=numbering,
            registry=registry,
            entities=entities,
            custom_instructions=custom,
        )
        async with semaphore:
            try:
                written = await llm_router.generate(
                    prompt=prompt,
                    system_prompt=_SECTION_SYSTEM,
                    temperature=0.3,
                    max_tokens=_SECTION_MAX_TOKENS,
                )
            except Exception as exc:
                log.warning("report_writer.section_failed", section=section.name, error=str(exc))
                return None, section_claims
        text = (written or "").strip()
        return (text or None), section_claims

    results = await asyncio.gather(*(_write(i, s) for i, s in enumerate(plan.sections, 1)))

    section_md: list[str] = []
    takeaways: list[tuple[str, str]] = []
    failed: list[str] = []
    dropped_refs = 0
    for section, (text, section_claims) in zip(plan.sections, results, strict=True):
        show_takeaway = False
        if text is None:
            failed.append(section.name)
            body, takeaway = _evidence_fallback_section(section_claims, numbering), ""
        else:
            text, dropped = validate_citations(text, valid_refs)
            dropped_refs += dropped
            polished = polish_section(text, section.name)
            body, takeaway = polished.body, polished.takeaway
            show_takeaway = polished.takeaway_explicit
            if len(body) < 40:  # a takeaway with nothing under it is not a section
                failed.append(section.name)
                body = _evidence_fallback_section(section_claims, numbering)
        parts = [f"## {section.name}"]
        if takeaway and text is not None and show_takeaway:
            parts.append(f"> **Key takeaway:** {takeaway}")
        parts.append(body)
        section_md.append("\n\n".join(parts))
        takeaways.append((section.name, takeaway))

    if len(failed) == len(plan.sections):
        # Every section failed: fall back to a report assembled from the
        # evidence itself rather than an unsourced model essay.
        log.warning("report_writer.all_sections_failed; building from evidence")
        shaped = shape_report(None, query=query, claims=claims, entities=entities, sources=scraped)
        return {
            "final_report": shaped.markdown,
            "citations": [s["url"] for s in registry],
            "report_sources": registry,
            "report_sections": [shaped.markdown],
            "report_quality": shaped.quality.value,
            "report_user_message": shaped.user_message,
        }

    top_lines = [_claim_line(c, numbering).split("\n", 1)[0] for c in claims[:_TOP_CLAIMS_FOR_SUMMARY]]
    front = await synthesise_front_matter(
        llm_router,
        query=query,
        profile=profile,
        planned_title=plan.title,
        takeaways=takeaways,
        top_claim_lines=top_lines,
        valid_refs=valid_refs,
        custom_instructions=custom,
    )
    _, glance = _evidence_profile(claims, registry)

    parts = [_front_matter_md(front, profile, query, glance), *section_md]
    closing = _closing_md(front, profile)
    if closing:
        parts.append(closing)
    limitations = _limitations_section(all_claims, state.get("evidence_gaps", []) or [], failed)
    if limitations:
        parts.append(limitations)
    parts.append(_methodology_section(len(claims), registry, max(len(scraped), len(registry)), plan))
    parts.append(_sources_section(registry, scraped))
    combined = "\n\n".join(parts) + "\n"

    shaped = shape_report(combined, query=query, claims=claims, entities=entities, sources=registry)

    log.info(
        "report_writer.complete",
        sections_written=len(plan.sections) - len(failed),
        sections_failed=len(failed),
        front_matter=front.source,
        unresolvable_citations_removed=dropped_refs,
        chars=len(shaped.markdown),
    )

    return {
        "final_report": shaped.markdown,
        "citations": [s["url"] for s in registry],
        "report_sources": registry,
        "report_sections": section_md,
        "report_quality": shaped.quality.value,
        "report_user_message": shaped.user_message,
    }
