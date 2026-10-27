"""The report writer produces a structured, skimmable document — not a wall of prose.

Pins the five stages: profile → evidence-aware plan → section contract →
deterministic polish → front matter and assembly.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from searchops.application.report_document import BlockKind, ReportDocument
from searchops.core.context.research import ReportType
from searchops.llm.report_planner import finalise_plan
from searchops.llm.report_polish import fix_table, normalise_refs, polish_section
from searchops.llm.report_profiles import REPORT_PROFILES, resolve_profile
from searchops.orchestration.evidence import Citation, Claim
from searchops.orchestration.nodes.report_writer import report_writer_node

pytestmark = pytest.mark.unit


def _claims(n: int = 8) -> list[Claim]:
    topics = ["licensing cost", "query latency", "managed hosting", "gpu indexing"]
    out = []
    for i in range(n):
        topic = topics[i % len(topics)]
        url = f"https://site{i % 4}.example.com/page"
        out.append(
            Claim(
                id=f"c{i}",
                text=f"Database {i} reports {topic} figure number {i} in its documentation.",
                confidence=0.9,
                status="supported",
                citations=[Citation(url=url, title=f"Site {i % 4}", excerpt=f"{topic} figure {i}")],
                supporting_urls=[url, f"https://site{(i + 1) % 4}.example.com/page"],
            )
        )
    return out


# ── Profiles ─────────────────────────────────────────────────────────────────


def test_every_report_type_has_a_profile() -> None:
    assert set(REPORT_PROFILES) == set(ReportType)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("policy_brief", ReportType.POLICY_BRIEF),
        ("ReportType.EXECUTIVE_BRIEF", ReportType.EXECUTIVE_BRIEF),
        ("", ReportType.AUTO),
        ("nonsense", ReportType.AUTO),
    ],
)
def test_resolve_profile(raw: str, expected: ReportType) -> None:
    assert resolve_profile(raw)[0] is expected


# ── Polish ───────────────────────────────────────────────────────────────────


def test_citation_runs_are_collapsed_and_moved_before_the_full_stop() -> None:
    assert normalise_refs("Fast. [1][2][2][3][4][5]") == "Fast [1][2][3]."


def test_polish_fixes_structure_without_inventing_text() -> None:
    raw = (
        "```markdown\n## Indexing\n**Indexing**\n> **Key takeaway:** HNSW trades memory for speed [2].\n"
        "Qdrant uses HNSW [1]. It is written in Rust [1].\n- one\n* two\n"
        "| A | B |\n| x |\n# Deep dive\nMore text [3].\n### References\n1. junk\n```"
    )
    out = polish_section(raw, "Indexing")
    assert out.takeaway == "HNSW trades memory for speed [2]."
    assert "Indexing" not in out.body
    assert "References" not in out.body and "junk" not in out.body
    assert "### Deep dive" in out.body and "\n# " not in out.body
    assert "Qdrant uses HNSW. It is written in Rust [1]." in out.body  # marker once per run
    assert "- one\n- two" in out.body
    assert "| x | |" in out.body  # ragged row padded to header width
    blocks = ReportDocument.from_markdown("j", "q", out.body).blocks
    assert {b.kind for b in blocks} >= {BlockKind.LIST, BlockKind.TABLE, BlockKind.HEADING, BlockKind.PARAGRAPH}


def test_missing_takeaway_is_derived_but_marked_implicit() -> None:
    out = polish_section("First point here [1]. Second point.", "X")
    assert out.takeaway.startswith("First point here")
    assert out.takeaway_explicit is False


def test_fix_table_adds_separator() -> None:
    assert fix_table("| A | B |\n| 1 | 2 |").split("\n")[1] == "|---|---|"


# ── Planning ─────────────────────────────────────────────────────────────────


def test_plan_never_gives_a_claim_to_two_sections() -> None:
    claims = _claims(12)
    labels = {f"C{i + 1}": c.id for i, c in enumerate(claims)}
    raw = [
        {"name": "Costs", "key_question": "What does licensing cost?", "claim_ids": ["C1", "C5"]},
        {"name": "Speed", "key_question": "How fast are queries?", "claim_ids": ["C1", "C2"]},
        {"name": "Executive Summary", "claim_ids": ["C3"]},  # reserved: added by the assembler
        {"name": "Hosting", "key_question": "Managed hosting options?"},
    ]
    plan = finalise_plan(raw, claims=claims, labels=labels, profile=REPORT_PROFILES[ReportType.AUTO], body_words=3000)
    names = [s.name for s in plan.sections]
    assert "Executive Summary" not in names
    assigned = [cid for s in plan.sections for cid in s.claim_ids]
    assert len(assigned) == len(set(assigned)) == len(claims)
    assert plan.sections[0].claim_ids[:2] == ["c0", "c4"]
    assert all(s.claim_ids for s in plan.sections)
    assert all(150 <= s.target_words <= 1300 for s in plan.sections)


def test_a_one_section_plan_over_many_claims_is_topped_up_and_drops_nothing() -> None:
    """Live run: a truncated plan gave 1 section for 167 claims, capping it at 36."""
    claims = _claims(167)
    profile = REPORT_PROFILES[ReportType.COMPARATIVE_ANALYSIS]
    plan = finalise_plan(
        [{"name": "Everything", "target_words": 600}], claims=claims, labels={}, profile=profile, body_words=2700
    )
    assert len(plan.sections) >= 5
    assert sum(len(s.claim_ids) for s in plan.sections) == 167


def test_top_up_skips_skeleton_sections_that_duplicate_planned_ones() -> None:
    """Live run: "Vector Database Options at a Glance" + skeleton "The Options at a Glance"."""
    plan = finalise_plan(
        [{"name": "Vector Database Options at a Glance"}],
        claims=_claims(167),
        labels={},
        profile=REPORT_PROFILES[ReportType.COMPARATIVE_ANALYSIS],
        body_words=2700,
    )
    assert "The Options at a Glance" not in [s.name for s in plan.sections]


def test_uncited_wrap_up_paragraphs_and_conclusion_subsections_are_dropped() -> None:
    body = polish_section(
        "### Speed\nQdrant is fast [1].\n### Conclusion\nEach has strengths.\n### Cost\nMilvus is free [2].\n\n"
        "These points provide a quick, fact-based snapshot of each option.",
        "X",
    ).body
    assert "Conclusion" not in body and "Each has strengths" not in body and "snapshot" not in body
    assert "Milvus is free [2]." in body
    # A cited closing paragraph is content, not filler, and is kept.
    assert "costs fall [3]" in polish_section("Intro [1].\n\nOverall, costs fall [3].", "X").body.lower()


@pytest.mark.parametrize(
    ("url", "label"),
    [
        (
            "https://www.linkedin.com/pulse/qdrant-vs-milvus-weaviate-choosing-right-vector-database-ppwff",
            "linkedin.com — Qdrant vs milvus weaviate choosing right vector database",
        ),
        (
            "https://dev.to/k/pinecone-vs-milvus-which-vector-db-in-2026-26dc",
            "dev.to — Pinecone vs milvus which vector db in 2026",
        ),
        ("https://github.com/qdrant/qdrant", "GitHub — qdrant/qdrant"),
        ("https://arxiv.org/html/2608.12812v1", "arXiv 2608.12812v1"),
        ("https://qdrant.tech/documentation/capacity-planning/", "qdrant.tech — Capacity planning"),
    ],
)
def test_untitled_sources_get_readable_labels(url: str, label: str) -> None:
    from searchops.orchestration.nodes.report_writer import source_label

    assert source_label("", url) == label
    assert source_label("Real Title", url) == "Real Title"


def test_exports_use_the_reports_own_title() -> None:
    doc = ReportDocument.from_markdown("j", "compare dbs", "# Choosing a Vector Database\n\nBody.")
    assert doc.report_title() == "Choosing a Vector Database"
    assert ReportDocument.from_markdown("j", "compare dbs", "# Research Report\n\nx").report_title() == "compare dbs"


# ── The assembled report ─────────────────────────────────────────────────────


class _Stub:
    def __init__(self) -> None:
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, prompt: str, system_prompt: str | None = None, **_: Any) -> str:
        sp = system_prompt or ""
        if "report architect" in sp:
            self.prompts.setdefault("plan", []).append(prompt)
            return json.dumps(
                {
                    "title": "Vector Databases: Cost, Speed and Hosting Compared",
                    "sections": [
                        {
                            "name": "Licensing and Cost",
                            "key_question": "What does it cost?",
                            "format": "table",
                            "claim_ids": ["C1", "C5"],
                        },
                        {
                            "name": "Query Performance",
                            "key_question": "How fast?",
                            "format": "bullets",
                            "claim_ids": ["C2", "C6"],
                        },
                        {"name": "Hosting Options", "key_question": "Where can it run?", "format": "prose"},
                    ],
                }
            )
        if "editor-in-chief" in sp:
            self.prompts.setdefault("front", []).append(prompt)
            return json.dumps(
                {
                    "title": "Choosing a Vector Database in 2026",
                    "executive_summary": [
                        "Open-source options lead on cost and latency [1][2][3][4].",
                        "Managed hosting narrows the gap but adds lock-in [3][99].",
                    ],
                    "bottom_line": "Pick on hosting model first, then on latency [2].",
                    "key_findings": [{"headline": "Licensing is free", "detail": "All three are open source [1]."}],
                    "closing": [{"headline": "Pilot two options", "detail": "Benchmark on your own data [2]."}],
                }
            )
        self.prompts.setdefault("section", []).append(prompt)
        ref = re.findall(r"\[(\d+)\]", prompt.split("### Sources")[1])[0]
        return (
            f"## Ignored Heading\n> **Key takeaway:** The evidence answers the question [{ref}].\n"
            f"### Detail\nFirst fact [{ref}][{ref}][77]. Second fact [{ref}].\n"
            f"| Item | Value |\n|---|---|\n| A | 1 [{ref}] |\n### Sources\n1. made up"
        )


@pytest.mark.asyncio
async def test_report_is_structured_front_to_back() -> None:
    llm = _Stub()
    out = await report_writer_node(
        {
            "query": "compare vector databases",
            "evidence_claims": _claims(8),
            "scraped_contents": [],
            "entities": [],
            "target_pages": 4,
            "report_type": "policy_brief",
            "report_structure_prompt": "Focus on licensing.",
        },
        llm_router=llm,  # type: ignore[arg-type]
    )
    md = out["final_report"]
    headings = re.findall(r"^(#{1,3}) (.+)$", md, re.MULTILINE)
    h2 = [t for h, t in headings if h == "##"]
    assert md.startswith("# Choosing a Vector Database in 2026")
    assert h2[0] == "Executive Summary"
    assert h2[1:4] == ["Licensing and Cost", "Query Performance", "Hosting Options"]
    assert "Recommendations" in h2  # policy brief closing section
    assert h2[-2:] == ["Methodology", "Sources"]
    assert "### Key Findings" in md and "### Evidence at a Glance" in md
    assert md.count("> **Key takeaway:**") == 3
    assert "> **Bottom line:**" in md
    assert "Ignored Heading" not in md and "made up" not in md

    valid = {s["n"] for s in out["report_sources"]}
    assert {int(n) for n in re.findall(r"\[(\d+)\](?!\()", md)} <= valid
    assert not re.search(r"(?:\[\d+\]){4,}", md), "citation runs longer than three survived"

    # The report type and user instructions reach every writing prompt.
    assert all("policymaker" in p for p in llm.prompts["section"])
    assert all("Focus on licensing." in p for p in llm.prompts["section"] + llm.prompts["plan"])
    # No finding is handed to two sections.
    finding_lines = [re.findall(r"^- (Database \d+)", p, re.MULTILINE) for p in llm.prompts["section"]]
    flat = [f for lines in finding_lines for f in lines]
    assert len(flat) == len(set(flat)) == 8

    doc = ReportDocument.from_markdown("j", "q", md)
    kinds = {b.kind for b in doc.blocks}
    assert {BlockKind.TABLE, BlockKind.QUOTE, BlockKind.LIST, BlockKind.SOURCE} <= kinds


@pytest.mark.asyncio
async def test_junk_front_matter_falls_back_to_section_takeaways() -> None:
    class Junk(_Stub):
        async def generate(self, prompt: str, system_prompt: str | None = None, **kw: Any) -> str:
            if "editor-in-chief" in (system_prompt or ""):
                return "not json at all"
            return await super().generate(prompt, system_prompt, **kw)

    out = await report_writer_node(
        {
            "query": "compare vector databases",
            "evidence_claims": _claims(6),
            "scraped_contents": [],
            "entities": [],
            "target_pages": 3,
        },
        llm_router=Junk(),  # type: ignore[arg-type]
    )
    md = out["final_report"]
    assert md.startswith("# Vector Databases: Cost, Speed and Hosting Compared")
    summary = md.split("## Executive Summary", 1)[1].split("###", 1)[0]
    assert "The evidence answers the question" in summary


# ── Repetition across sections (live run: the same P95 figures in three parts) ──

_ROW = "Query Latency (P95) Qdrant: 15-25ms for HNSW Milvus: 20-35ms (HNSW) Weaviate: 25-40ms for HNSW"


def _row_claims() -> list[Claim]:
    url = "https://bench.example.com/p"
    texts = ["Qdrant P95 latency is 15-25 ms.", "Milvus P95 latency is 20-35 ms.", "Weaviate P95 latency is 25-40 ms."]
    return [
        Claim(
            id=f"row{i}",
            text=t,
            confidence=0.9,
            status="supported",
            citations=[Citation(url=url, excerpt=_ROW)],
            supporting_urls=[url],
        )
        for i, t in enumerate(texts)
    ]


def test_claims_from_one_source_span_land_in_one_section() -> None:
    claims = [*_claims(30), *_row_claims()]
    labels = {f"C{i + 1}": c.id for i, c in enumerate(claims)}
    # The LLM split the comparison row across three sections.
    raw = [
        {"name": "Costs", "claim_ids": ["C1", "C31"]},
        {"name": "Speed", "claim_ids": ["C2", "C32"]},
        {"name": "Hosting", "claim_ids": ["C3", "C33"]},
    ]
    plan = finalise_plan(raw, claims=claims, labels=labels, profile=REPORT_PROFILES[ReportType.AUTO], body_words=3000)
    homes = {s.name for s in plan.sections for cid in s.claim_ids if cid.startswith("row")}
    assert len(homes) == 1
    assert sum(len(s.claim_ids) for s in plan.sections) == len(claims)


def test_a_quote_carrying_sibling_figures_is_not_shown_to_the_writer() -> None:
    from searchops.orchestration.nodes.report_writer import _claim_line

    row = _row_claims()[0]
    numbering = {"https://bench.example.com/p": 1}
    assert "quote:" not in _claim_line(row, numbering)
    plain = row.model_copy(update={"citations": [Citation(url=row.citations[0].url, excerpt="Qdrant: 15-25ms P95.")]})
    assert "quote:" in _claim_line(plain, numbering)


def test_a_claim_fully_stated_by_another_is_absorbed() -> None:
    from searchops.orchestration.nodes.claim_dedup import absorb_contained

    def c(i: str, text: str, status: str = "supported", url: str = "https://a.example.com") -> Claim:
        return Claim(
            id=i, text=text, confidence=0.9, status=status, citations=[Citation(url=url)], supporting_urls=[url]
        )

    big = c("big", "Milvus and Qdrant are both licensed under Apache 2.0, which permits commercial use.")
    small = c("small", "Milvus is licensed under Apache 2.0.", url="https://b.example.com")
    out = absorb_contained([small, big])
    assert [x.id for x in out] == ["big"]
    assert "https://b.example.com" in out[0].supporting_urls  # evidence moves, never vanishes

    # A different figure, or a weaker-verified absorber, keeps both.
    other = c("v1", "Milvus is licensed under Apache 1.0.")
    assert len(absorb_contained([other, big])) == 2
    assert len(absorb_contained([small, big.model_copy(update={"status": "contested"})])) == 2


# ── Source titles ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "html", "markdown", "expected"),
    [
        ("Reported Title", "<title>Other</title>", "", "Reported Title"),
        (
            None,
            '<meta property="og:title" content="Capacity Planning &amp; Sizing"><title>x | Qdrant</title>',
            "",
            "Capacity Planning & Sizing",
        ),
        ("", "<html><title>\n  Milvus Docs  </title>", "", "Milvus Docs"),
        (None, None, "Intro\n# **Weaviate** [Benchmarks](/b)\ntext", "Weaviate Benchmarks"),
        (
            None,
            '<meta property="og:title" content="Onidel Cloud"><title>VPS Vector DB Benchmarks | Onidel Cloud</title>',
            "",
            "VPS Vector DB Benchmarks | Onidel Cloud",
        ),
        (None, "<title>Just a moment...</title>", "", ""),
        (None, None, "no heading here", ""),
    ],
)
def test_page_title_is_recovered_from_the_page(title, html, markdown, expected) -> None:  # type: ignore[no-untyped-def]
    from searchops.scraping.page_title import infer_title

    assert infer_title(title, html, markdown) == expected


def test_top_up_does_not_append_a_second_overview() -> None:
    """Live run: the model opened with "A Quick Reference Guide"; the top-up then
    appended the skeleton's "The Options at a Glance" as part 4, covering the same ground."""
    raw = [
        {"name": "Qdrant, Milvus, and Weaviate: A Quick Reference Guide"},
        {"name": "Performance Benchmarks"},
        {"name": "Architecture for Scale"},
    ]
    plan = finalise_plan(
        raw, claims=_claims(167), labels={}, profile=REPORT_PROFILES[ReportType.COMPARATIVE_ANALYSIS], body_words=2700
    )
    names = [s.name for s in plan.sections]
    assert "The Options at a Glance" not in names
    assert names[0].endswith("Quick Reference Guide")
    assert sum(len(s.claim_ids) for s in plan.sections) == 167


def test_the_opener_is_a_last_resort_and_then_opens_the_report() -> None:
    plan = finalise_plan(
        [{"name": "Everything"}],
        claims=_claims(167),
        labels={},
        profile=REPORT_PROFILES[ReportType.COMPARATIVE_ANALYSIS],
        body_words=2700,
    )
    names = [s.name for s in plan.sections]
    assert names[0] == "The Options at a Glance"
    assert sum(len(s.claim_ids) for s in plan.sections) == 167


def test_title_is_found_below_a_long_navigation_menu() -> None:
    from searchops.scraping.page_title import infer_title

    nav = "\n".join(f"* [Menu item {i}](https://site.example/{i})" for i in range(200))
    assert infer_title(None, None, f"{nav}\n\n# Indexing\n\nBody.") == "Indexing"
