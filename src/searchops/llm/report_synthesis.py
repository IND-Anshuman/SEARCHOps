"""Front matter for a report: title, executive summary, key findings, closing.

Sections are written in parallel and none of them sees the whole report, so
none can summarise it. Without this pass the report opened straight into its
first body section: the reader had to read everything to learn the answer.

This pass runs once, after the sections, from what they actually concluded
(their key takeaways) plus the strongest findings. Its output is JSON so each
part lands in a fixed place in the report; citations it uses are checked
against the source registry like everything else. If the call fails or returns
junk, the front matter is built in code from the section takeaways — the
report still opens with an answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from searchops.llm.json_repair import repair_json
from searchops.llm.report_polish import normalise_refs, validate_citations

if TYPE_CHECKING:
    from searchops.llm.report_profiles import ReportProfile
    from searchops.llm.router import LLMRouter

log = structlog.get_logger(__name__)

_SYSTEM = """You are the editor-in-chief of a research publication. You write the opening
of a report whose body is already written: the title, the executive summary and the key
findings. You state only what the body and the listed findings establish, and you keep
every citation marker [n] exactly as given. Return ONLY a JSON object."""


@dataclass
class Point:
    headline: str
    detail: str


@dataclass
class FrontMatter:
    title: str
    summary: list[str]  # paragraphs
    bottom_line: str
    key_findings: list[Point] = field(default_factory=list)
    closing: list[Point] = field(default_factory=list)
    source: str = "llm"


async def synthesise_front_matter(
    llm_router: LLMRouter,
    *,
    query: str,
    profile: ReportProfile,
    planned_title: str,
    takeaways: list[tuple[str, str]],
    top_claim_lines: list[str],
    valid_refs: set[int],
    custom_instructions: str = "",
) -> FrontMatter:
    closing_spec = ""
    if profile.closing_heading:
        closing_spec = (
            f',\n  "closing": [{{"headline": "short imperative or claim", "detail": "1-2 sentences with [n]"}}]'
            f"  // 3-5 items for the '{profile.closing_heading}' section: {profile.closing_guidance}"
        )
    body = "\n".join(f"- {name}: {take}" for name, take in takeaways if take)
    prompt = f"""Report: {profile.label} answering "{query}"
Reader: {profile.audience}
Voice: {profile.voice}
Working title: {planned_title}

## What each part of the body concluded
{body or "(no conclusions available)"}

## Strongest findings (with their source numbers)
{chr(10).join(top_claim_lines)}
{f"{chr(10)}## Instructions from the user{chr(10)}{custom_instructions}" if custom_instructions else ""}

Return this JSON:
{{
  "title": "specific, informative title, at most 12 words",
  "executive_summary": ["paragraph 1: the direct answer to the question, 2-3 sentences with [n]",
                        "paragraph 2: the most important nuance, trade-off or uncertainty, 2-3 sentences with [n]"],
  "bottom_line": "one sentence a reader should remember",
  "key_findings": [{{"headline": "4-8 word finding", "detail": "one sentence with the evidence and [n]"}}]  // 4-6 items{closing_spec}
}}"""

    try:
        raw = await llm_router.generate(prompt=prompt, system_prompt=_SYSTEM, temperature=0.2, max_tokens=6000)
        parsed = repair_json(raw or "", record_key="key_findings", field_keys=("headline",)).parsed
    except Exception as exc:
        log.warning("report_synthesis.failed", error=str(exc))
        parsed = None

    fm = _from_json(parsed, planned_title, valid_refs) if isinstance(parsed, dict) else None
    if fm is None:
        log.warning("report_synthesis.fallback", query=query)
        return fallback_front_matter(query=query, planned_title=planned_title, takeaways=takeaways)
    return fm


def _clean(text: Any, valid_refs: set[int]) -> str:
    cleaned, _ = validate_citations(" ".join(str(text or "").split()), valid_refs)
    return normalise_refs(cleaned).strip()


def _points(items: Any, valid_refs: set[int], limit: int) -> list[Point]:
    out: list[Point] = []
    for item in items or []:
        if isinstance(item, dict):
            head = _clean(item.get("headline"), valid_refs).strip(" .*")
            detail = _clean(item.get("detail"), valid_refs)
        else:
            head, detail = "", _clean(item, valid_refs)
        if detail or head:
            out.append(Point(head, detail))
        if len(out) >= limit:
            break
    return out


def _from_json(data: dict[str, Any], planned_title: str, valid_refs: set[int]) -> FrontMatter | None:
    summary_raw = data.get("executive_summary")
    if isinstance(summary_raw, str):
        summary_raw = summary_raw.split("\n\n")
    summary = [_clean(p, valid_refs) for p in summary_raw or [] if str(p).strip()][:3]
    findings = _points(data.get("key_findings"), valid_refs, 6)
    if not summary or len(" ".join(summary)) < 60:
        return None
    title = " ".join(str(data.get("title") or "").replace("#", "").split()).strip(" *\"'")
    return FrontMatter(
        title=title if 8 <= len(title) <= 140 else planned_title,
        summary=summary,
        bottom_line=_clean(data.get("bottom_line"), valid_refs),
        key_findings=findings,
        closing=_points(data.get("closing"), valid_refs, 6),
    )


def fallback_front_matter(*, query: str, planned_title: str, takeaways: list[tuple[str, str]]) -> FrontMatter:
    """Front matter assembled from the sections' own takeaways — nothing new is stated."""
    takes = [(n, t) for n, t in takeaways if t]
    lead = " ".join(t for _, t in takes[:3])
    summary = [lead or f"The findings on “{query.strip()}” are set out part by part below."]
    if len(takes) > 3:
        summary.append(" ".join(t for _, t in takes[3:6]))
    return FrontMatter(
        title=planned_title,
        summary=summary,
        bottom_line="",
        key_findings=[Point(n, t) for n, t in takes[:6]],
        source="fallback",
    )
