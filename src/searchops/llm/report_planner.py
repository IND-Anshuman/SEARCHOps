"""Evidence-aware report planning.

The previous planner saw only keyword counts ("security: strength 3") and never
the claims themselves, so it designed sections blind: generic names, sections
that overlapped, and no idea which evidence belonged where. Every section was
then handed the same "strongest overall" claims, and the report repeated itself.

This planner works from the evidence:

1. The LLM sees a numbered digest of the actual claims (``C1``, ``C2``...) and
   the report profile's skeleton, and returns a title plus sections, each with
   a key question, a format and the claim ids it will use.
2. The plan is then **finalised in code**, so a weak or malformed reply still
   yields a sound plan: names are de-duplicated, formats normalised, every
   claim is assigned to at most one section (no repetition across sections),
   unassigned claims are routed to their best-matching section by BM25, and a
   section left with no evidence is dropped instead of being written from air.

If the LLM call fails entirely, the profile skeleton is used with the same
code-side assignment, so the report keeps its shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from searchops.llm.json_repair import repair_json
from searchops.llm.report_profiles import SECTION_FORMATS, ReportProfile, normalise_format
from searchops.orchestration.passages import Passage, PassageIndex

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter

log = structlog.get_logger(__name__)

# How much of the claim digest the planner sees. The digest is the expensive
# part of the prompt; past this the strongest claims are kept.
_DIGEST_CHARS = 14_000
_DIGEST_CLAIM_CHARS = 170
# A section is never handed more claims than this; the writer's prompt has to
# fit and a section carrying 60 facts reads as a list, not an argument.
MAX_CLAIMS_PER_SECTION = 36
_PLANNER_MAX_TOKENS = 8000
# Below this many findings per extra section, a short outline is fine.
_CLAIMS_PER_MIN_SECTION = 12
_MIN_SECTION_WORDS = 150
_MAX_SECTION_WORDS = 1300
# A grounded claim supports roughly this many words of honest prose.
_WORDS_PER_CLAIM = 85


_SYSTEM_PLANNER_PROMPT = """You are a research report architect. You design the outline of an
evidence-based report from a numbered list of verified findings.

Return ONLY a JSON object, no commentary:
{
  "title": "a specific, informative report title (not the question restated)",
  "sections": [
    {
      "name": "specific section heading",
      "key_question": "the one question this section answers",
      "format": "prose | mixed | bullets | table | timeline | steps",
      "target_words": 500,
      "subsections": ["2-4 specific ### subheadings"],
      "claim_ids": ["C1", "C7", "C12"]
    }
  ]
}

Rules:
- Every section must be backed by findings: list the ids of the findings it will use.
- Each finding id belongs to ONE section only. Sections must not overlap.
- Order sections so the report builds an argument: context first, then evidence, then analysis.
- Headings are specific to this topic ("How HNSW Indexing Trades Recall for Speed"),
  never generic ("Analysis", "Overview", "Background").
- Choose "table" when findings compare several items on shared attributes, "timeline" for
  dated developments, "steps" for processes, "bullets" for enumerations, "prose" for argument.
- Do NOT include an executive summary, key findings, conclusion, limitations or sources
  section: those are added automatically."""


@dataclass
class PlannedSection:
    name: str
    key_question: str
    format: str
    target_words: int
    subsections: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)  # real Claim.id values after finalising
    topics: list[str] = field(default_factory=list)  # routing hints only

    @property
    def focus(self) -> str:
        return " ".join([self.name, self.key_question, *self.subsections, *self.topics])


@dataclass
class ReportPlan:
    title: str
    sections: list[PlannedSection]
    source: str  # "llm" or "skeleton" — recorded so a degraded plan is visible in logs


def build_evidence_summary(
    claims: list[Any],
    entities: list[Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Counts the planner prompt opens with."""
    domains: list[str] = []
    for src in sources:
        url = str(src.get("url", ""))
        if "://" in url:
            domain = url.split("://")[1].split("/")[0]
            if domain not in domains:
                domains.append(domain)
    statuses: dict[str, int] = {}
    for c in claims:
        s = str(getattr(c, "status", "unverified") or "unverified")
        statuses[s] = statuses.get(s, 0) + 1
    return {
        "claim_count": len(claims),
        "entity_count": len(entities),
        "source_count": len(sources),
        "source_domains": domains[:10],
        "statuses": statuses,
    }


def claim_digest(claims: list[Any], budget: int = _DIGEST_CHARS) -> tuple[str, dict[str, str]]:
    """``C1: text`` lines for the planner, and the label → Claim.id map."""
    lines: list[str] = []
    labels: dict[str, str] = {}
    used = 0
    for i, claim in enumerate(claims, 1):
        text = " ".join(str(getattr(claim, "text", "") or "").split())
        if len(text) > _DIGEST_CLAIM_CHARS:
            text = text[: _DIGEST_CLAIM_CHARS - 1].rstrip() + "…"
        status = str(getattr(claim, "status", "") or "")
        tag = " (contested)" if status == "contested" else ""
        line = f"C{i}: {text}{tag}"
        if used + len(line) > budget:
            break
        used += len(line) + 1
        lines.append(line)
        labels[f"C{i}"] = str(getattr(claim, "id", ""))
    return "\n".join(lines), labels


class ReportPlanner:
    """Design a report outline from the evidence and a report profile."""

    def __init__(self, llm_router: LLMRouter) -> None:
        self.llm_router = llm_router

    async def plan(
        self,
        *,
        query: str,
        claims: list[Any],
        entities: list[Any],
        sources: list[dict[str, Any]],
        profile: ReportProfile,
        body_words: int,
        custom_instructions: str = "",
    ) -> ReportPlan:
        digest, labels = claim_digest(claims)
        summary = build_evidence_summary(claims, entities, sources)
        skeleton = "\n".join(f"- {a.name} — {a.key_question} [format: {a.format}]" for a in profile.skeleton)
        statuses = ", ".join(f"{n} {s}" for s, n in sorted(summary["statuses"].items()))
        prompt = f"""Design the outline of a {profile.label.lower()} answering: {query}

## Reader
{profile.audience}

## Starting skeleton (adapt it to the evidence: rename, merge, drop or add sections)
{skeleton}
{profile.planner_guidance}

## Budget
- About {body_words} words across all sections, {max(2, profile.max_sections - 3)} to {profile.max_sections} sections.
- Formats available: {", ".join(SECTION_FORMATS)}.

## Evidence base
{summary["claim_count"]} findings ({statuses}) from {summary["source_count"]} sources
({", ".join(summary["source_domains"][:6])}).

## Findings
{digest}

{f"## Instructions from the user (these take precedence){chr(10)}{custom_instructions}" if custom_instructions else ""}

Return the JSON outline now."""

        sections: list[dict[str, Any]] = []
        title = ""
        try:
            raw = await self.llm_router.generate(
                prompt=prompt,
                system_prompt=_SYSTEM_PLANNER_PROMPT,
                temperature=0.0,
                # Reasoning models spend much of this thinking; 2500 left
                # gpt-oss-20b nothing to answer with and it returned "".
                max_tokens=_PLANNER_MAX_TOKENS,
            )
            outcome = repair_json(raw or "", record_key="sections", field_keys=("name",))
            parsed = outcome.parsed
            if isinstance(parsed, dict):
                sections = [s for s in parsed.get("sections") or [] if isinstance(s, dict)]
                title = str(parsed.get("title") or "")
            elif isinstance(parsed, list):
                sections = [s for s in parsed if isinstance(s, dict)]
        except Exception as exc:
            log.warning("report_planner.llm_failed", error=str(exc))

        source = "llm"
        if not any(str(s.get("name", "")).strip() for s in sections):
            log.warning("report_planner.fallback_to_skeleton", query=query)
            source = "skeleton"
            sections = [
                {"name": a.name, "key_question": a.key_question, "format": a.format, "weight": a.weight}
                for a in profile.skeleton
            ]

        plan = finalise_plan(
            sections,
            claims=claims,
            labels=labels,
            profile=profile,
            body_words=body_words,
            title=title,
            query=query,
        )
        plan.source = source
        log.info(
            "report_planner.plan_ready",
            source=source,
            sections=len(plan.sections),
            title=plan.title,
            claims_per_section=[len(s.claim_ids) for s in plan.sections],
        )
        return plan


# ── Code-side finalisation ───────────────────────────────────────────────────

# Sections the assembler writes itself; a planned one would duplicate them.
_RESERVED = {
    "executive summary",
    "summary",
    "key findings",
    "key takeaways",
    "conclusion",
    "conclusions",
    "limitations",
    "limitations and open questions",
    "sources",
    "references",
    "methodology",
    "recommendations",
    "implications",
}


def finalise_plan(
    raw_sections: list[dict[str, Any]],
    *,
    claims: list[Any],
    labels: dict[str, str],
    profile: ReportProfile,
    body_words: int,
    title: str = "",
    query: str = "",
) -> ReportPlan:
    """Turn a raw (possibly sloppy) outline into a sound, non-overlapping plan."""
    sections: list[PlannedSection] = []
    weights: list[float] = []
    seen_names: set[str] = set()
    for raw in raw_sections:
        name = " ".join(str(raw.get("name", "")).replace("#", "").split()).strip(" *:")
        key = name.lower()
        if not name or key in seen_names or key in _RESERVED:
            continue
        seen_names.add(key)
        subsections = [
            " ".join(str(s).replace("#", "").split()).strip(" *:")
            for s in raw.get("subsections") or []
            if str(s).strip()
        ][:4]
        sections.append(
            PlannedSection(
                name=name,
                key_question=str(raw.get("key_question") or raw.get("purpose") or "").strip(),
                format=normalise_format(raw.get("format")),
                target_words=0,
                subsections=subsections,
                claim_ids=[str(c).strip().upper() for c in raw.get("claim_ids") or []],
                topics=[str(t) for t in raw.get("evidence_topics") or [] if str(t).strip()],
            )
        )
        try:
            weights.append(float(raw.get("weight") or raw.get("target_words") or 1.0))
        except (TypeError, ValueError):
            weights.append(1.0)
        if len(sections) >= profile.max_sections:
            break

    # A truncated or lazy reply can yield one or two sections for a large
    # evidence base, which would either dump everything into one section or
    # drop what exceeds its cap. Top up from the profile skeleton.
    n = len(claims)
    enough_to_hold_all = -(-n // MAX_CLAIMS_PER_SECTION)  # ceil: no claim dropped by the cap
    readable = min(max(1, profile.max_sections - 2), -(-n // _CLAIMS_PER_MIN_SECTION))
    floor = max(enough_to_hold_all, readable)
    # LLM weights are word targets, skeleton weights are ratios: put the
    # skeleton's on the same scale before mixing them.
    scale = sum(weights) / len(weights) if weights else 1.0
    # Every skeleton starts with an opener ("The Options at a Glance",
    # "Background and Context"). A planned outline already opens with its own
    # first section, so the opener is the last resort, and then it goes first:
    # appended, it landed mid-report as a second overview of the same ground.
    candidates = [*profile.skeleton[1:], profile.skeleton[0]] if sections and profile.skeleton else profile.skeleton
    for a in candidates:
        if len(sections) >= floor:
            break
        if not any(_similar_names(a.name, s.name) for s in sections):
            seen_names.add(a.name.lower())
            at = 0 if sections and a is profile.skeleton[0] else len(sections)
            sections.insert(at, PlannedSection(a.name, a.key_question, a.format, 0))
            weights.insert(at, a.weight * scale)

    if not sections:
        sections = [PlannedSection(a.name, a.key_question, a.format, 0) for a in profile.skeleton]
        weights = [a.weight for a in profile.skeleton]

    _assign_claims(sections, claims, labels)

    kept = [(s, w) for s, w in zip(sections, weights, strict=True) if s.claim_ids]
    if not kept:  # no evidence routed anywhere: keep the first section with everything
        sections[0].claim_ids = [str(getattr(c, "id", "")) for c in claims[:MAX_CLAIMS_PER_SECTION]]
        kept = [(sections[0], 1.0)]

    total_weight = sum(max(w, 0.1) for _, w in kept) or 1.0
    for s, w in kept:
        share = int(body_words * max(w, 0.1) / total_weight)
        evidence_cap = max(_MIN_SECTION_WORDS, _WORDS_PER_CLAIM * len(s.claim_ids))
        s.target_words = max(_MIN_SECTION_WORDS, min(share, evidence_cap, _MAX_SECTION_WORDS))

    return ReportPlan(
        title=_clean_title(title) or _title_from_query(query),
        sections=[s for s, _ in kept],
        source="llm",
    )


def _assign_claims(sections: list[PlannedSection], claims: list[Any], labels: dict[str, str]) -> None:
    """Give every claim to at most one section.

    The LLM's explicit choices win (first section to name a claim keeps it);
    every claim it left out goes to the section whose focus it matches best,
    or to the section with the least evidence when it matches none.
    """
    valid_ids = {str(getattr(c, "id", "")) for c in claims}
    taken: set[str] = set()
    for s in sections:
        resolved: list[str] = []
        for ref in s.claim_ids:
            cid = labels.get(ref, ref if ref in valid_ids else "")
            if cid and cid not in taken and len(resolved) < MAX_CLAIMS_PER_SECTION:
                resolved.append(cid)
                taken.add(cid)
        s.claim_ids = resolved

    rest = [c for c in claims if str(getattr(c, "id", "")) not in taken]
    if not rest:
        return
    index = PassageIndex([Passage(id=str(c.id), url="", text=str(c.text)) for c in rest])
    scores: dict[str, list[float]] = {str(c.id): [0.0] * len(sections) for c in rest}
    for si, s in enumerate(sections):
        for p, score in index.search(s.focus, k=len(rest)):
            scores[p.id][si] = score

    for c in rest:  # claims arrive strongest-first, so strong ones are placed first
        cid = str(c.id)
        row = scores[cid]
        order = sorted(range(len(sections)), key=lambda i: (-row[i], len(sections[i].claim_ids)))
        if row[order[0]] <= 0:
            order = sorted(range(len(sections)), key=lambda i: len(sections[i].claim_ids))
        for si in order:
            if len(sections[si].claim_ids) < MAX_CLAIMS_PER_SECTION:
                sections[si].claim_ids.append(cid)
                break

    # A planned section left empty takes its best-matching code-routed claim
    # from a section that can spare one, rather than being dropped.
    for si, s in enumerate(sections):
        if s.claim_ids:
            continue
        donors = [
            (scores[cid][si], cid, d) for d in sections if len(d.claim_ids) > 2 for cid in d.claim_ids if cid in scores
        ]
        if donors:
            _, cid, donor = max(donors, key=lambda x: x[0])
            donor.claim_ids.remove(cid)
            s.claim_ids.append(cid)

    _keep_siblings_together(sections, claims)

    # Restore evidence-strength order inside each section.
    rank = {str(getattr(c, "id", "")): i for i, c in enumerate(claims)}
    for s in sections:
        s.claim_ids.sort(key=lambda cid: rank.get(cid, 0))


# A shared excerpt shorter than this is too generic to say two claims are one comparison.
_MIN_SIBLING_EXCERPT = 40


def _sibling_key(claim: Any) -> str:
    """Source page + excerpt: claims extracted from the same quoted span."""
    for cit in getattr(claim, "citations", None) or []:
        excerpt = " ".join(str(getattr(cit, "excerpt", "") or "").lower().split())
        if len(excerpt) >= _MIN_SIBLING_EXCERPT:
            return f"{getattr(cit, 'url', '')}|{excerpt}"
    return ""


def _keep_siblings_together(sections: list[PlannedSection], claims: list[Any]) -> None:
    """Move claims split from one source span into a single section.

    One comparison row ("Qdrant: 15-25 ms, Milvus: 20-35 ms, Weaviate: 25-40 ms")
    is extracted as three claims that share the excerpt. Spread over three
    sections, each section saw the whole row in its quote and wrote the whole
    comparison: the live report gave the same latency figures three times.
    Together, the comparison is written once, complete. This overrides the
    LLM's own grouping; the section already holding most of the group keeps it.
    """
    groups: dict[str, list[str]] = {}
    for c in claims:
        key = _sibling_key(c)
        if key:
            groups.setdefault(key, []).append(str(getattr(c, "id", "")))
    where = {cid: si for si, s in enumerate(sections) for cid in s.claim_ids}
    for ids in groups.values():
        placed = [cid for cid in ids if cid in where]  # strongest first
        homes = [where[cid] for cid in placed]
        if len(set(homes)) < 2:
            continue
        home = max(set(homes), key=lambda si: (homes.count(si), -homes.index(si)))
        for cid in placed:
            si = where[cid]
            if si == home or len(sections[home].claim_ids) >= MAX_CLAIMS_PER_SECTION:
                continue
            if len(sections[si].claim_ids) <= 1:  # never empty a section
                continue
            sections[si].claim_ids.remove(cid)
            sections[home].claim_ids.append(cid)
            where[cid] = home


_NAME_STOPWORDS = frozenset({"the", "a", "an", "and", "of", "for", "to", "in", "on", "at", "by", "vs", "with"})


def _similar_names(a: str, b: str) -> bool:
    """True when two headings cover the same ground ("The Options at a Glance" vs
    "Vector Database Options at a Glance"): most of the shorter one's words recur."""
    wa = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if w not in _NAME_STOPWORDS}
    wb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if w not in _NAME_STOPWORDS}
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def _clean_title(title: str) -> str:
    t = " ".join(title.replace("#", "").split()).strip(" *\"'")
    return t if 8 <= len(t) <= 140 else ""


def _title_from_query(query: str) -> str:
    q = " ".join(query.split()).strip(" ?.")
    if not q:
        return "Research Report"
    return q[0].upper() + q[1:]
