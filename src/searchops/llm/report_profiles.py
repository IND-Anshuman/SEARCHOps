"""Report profiles: what each ``ReportType`` means for the finished report.

A report type used to reach the writer only as one line of free text
("Report type: policy_brief.") while the writer hard-coded a technical tone and
a prose-only format, so every type came out as the same wall of paragraphs.

A profile turns the type into concrete writing decisions:

- **audience / voice** — who reads it and how it should sound;
- **skeleton** — the section archetypes the planner starts from (it may rename,
  merge or add sections to fit the evidence, but not ignore the shape);
- **default format** per section — prose, bullets, a table, a timeline...;
- **closing section** — recommendations, implications, or none;
- **words per page** — how dense the type is.

Every ``ReportType`` must have a profile; a test enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from searchops.core.context.research import ReportType

# The section formats the writer knows how to produce. The planner may only
# choose from these; anything else falls back to "mixed".
SECTION_FORMATS: dict[str, str] = {
    "prose": (
        "Short analytical paragraphs (3-4 sentences each), organised under ### subheadings. "
        "Use a bullet list only where the content is genuinely a list."
    ),
    "mixed": (
        "Open with one framing paragraph, then use ### subheadings. Combine short paragraphs with "
        "bullet lists for enumerations and a table where items are compared on shared attributes."
    ),
    "bullets": (
        "Mostly bullet points. Each bullet starts with a **bold lead phrase** followed by one or two "
        "sentences of explanation. Group related bullets under ### subheadings."
    ),
    "table": (
        "Build the section around at least one Markdown comparison table (items as rows, attributes "
        "as columns, 3-6 columns, concise cells). Before the table, one sentence saying what it "
        "compares; after it, a short paragraph on what the comparison shows."
    ),
    "timeline": (
        "A chronological account. Use a Markdown table with columns | Date | Development | "
        "Significance | or a dated bullet list ('**2021** — ...'), followed by a short paragraph on the trend."
    ),
    "steps": (
        "A numbered sequence of steps or stages (1., 2., 3.), each with a **bold step name** and one "
        "or two sentences. Follow with a short paragraph on risks or prerequisites."
    ),
}


@dataclass(frozen=True)
class SectionArchetype:
    name: str
    key_question: str
    format: str = "mixed"
    weight: float = 1.0  # share of the body word budget, relative to siblings


@dataclass(frozen=True)
class ReportProfile:
    label: str
    audience: str
    voice: str
    skeleton: tuple[SectionArchetype, ...]
    words_per_page: int = 450
    closing_heading: str = ""  # e.g. "Recommendations"; empty = no closing section
    closing_guidance: str = ""
    planner_guidance: str = ""
    max_sections: int = 7
    extras: tuple[str, ...] = field(default_factory=tuple)


_A = SectionArchetype

REPORT_PROFILES: dict[ReportType, ReportProfile] = {
    ReportType.AUTO: ReportProfile(
        label="Research report",
        audience="an informed reader who wants a clear, well-organised answer",
        voice="Clear, precise and analytical. Plain English; define jargon on first use.",
        skeleton=(
            _A("Background and Context", "What is this and why does it matter?", "prose", 0.8),
            _A("Core Findings", "What does the evidence establish?", "mixed", 1.4),
            _A("How It Works", "What are the mechanisms, components or drivers?", "mixed", 1.0),
            _A("Comparison and Trade-offs", "How do the options or positions differ?", "table", 1.0),
            _A("Outlook", "What happens next and what is still uncertain?", "bullets", 0.7),
        ),
        closing_heading="Implications",
        closing_guidance="What the findings mean in practice for the reader.",
        planner_guidance="Pick the structure that best answers the question; drop archetypes the evidence does not support.",
    ),
    ReportType.EXECUTIVE_BRIEF: ReportProfile(
        label="Executive brief",
        audience="a busy decision-maker who will read for five minutes",
        voice="Direct and decisive. Lead with conclusions. Short sentences. No academic hedging beyond what the evidence demands.",
        skeleton=(
            _A("The Situation", "What is happening and why it matters now?", "bullets", 0.8),
            _A("What the Evidence Shows", "What are the decisive facts?", "bullets", 1.2),
            _A("Options and Trade-offs", "What choices exist and how do they compare?", "table", 1.0),
            _A("Risks to Watch", "What could change the picture?", "bullets", 0.6),
        ),
        words_per_page=350,
        closing_heading="Recommendations",
        closing_guidance="Concrete, prioritised actions a decision-maker can take, each justified by the evidence.",
        max_sections=5,
    ),
    ReportType.DETAILED_ANALYSIS: ReportProfile(
        label="Detailed analysis",
        audience="a specialist who wants depth, mechanisms and numbers",
        voice="Rigorous and technical. Quantify wherever the evidence allows. Explain mechanisms, not just outcomes.",
        skeleton=(
            _A("Scope and Definitions", "What exactly is being analysed?", "prose", 0.6),
            _A("Technical Foundations", "How does it work under the hood?", "mixed", 1.3),
            _A("Evidence and Measurements", "What do the data and benchmarks show?", "table", 1.2),
            _A("Comparative Assessment", "How do the alternatives stack up?", "table", 1.0),
            _A("Challenges and Limitations", "Where does it break down?", "mixed", 0.9),
            _A("Future Directions", "Where is the field heading?", "bullets", 0.7),
        ),
        words_per_page=500,
        closing_heading="Implications",
        closing_guidance="What the analysis implies for practitioners and for further research.",
        max_sections=8,
    ),
    ReportType.COMPARATIVE_ANALYSIS: ReportProfile(
        label="Comparative analysis",
        audience="someone choosing between options",
        voice="Even-handed and concrete. Compare like with like on named criteria. Never declare a winner without evidence.",
        skeleton=(
            _A("The Options at a Glance", "What is being compared, in one view?", "table", 1.0),
            _A("Criterion-by-Criterion Comparison", "How do they differ on each criterion that matters?", "table", 1.5),
            _A("Strengths and Weaknesses", "Where does each option shine or fall short?", "bullets", 1.0),
            _A("Fit by Use Case", "Which option suits which situation?", "table", 0.9),
        ),
        closing_heading="Recommendations",
        closing_guidance="Which option to choose under which conditions, as a short decision guide.",
        planner_guidance="At least two sections MUST be tables. The first section is an overview table of all options.",
        max_sections=6,
    ),
    ReportType.NARRATIVE: ReportProfile(
        label="Narrative report",
        audience="a general reader who wants the story behind the topic",
        voice="Engaging, story-driven, still exact. Flowing paragraphs with clear transitions; vivid but never embellished.",
        skeleton=(
            _A("Origins", "Where did this start?", "prose", 0.9),
            _A("Key Milestones", "What were the turning points?", "timeline", 1.0),
            _A("The Present Picture", "Where do things stand today?", "prose", 1.2),
            _A("What Comes Next", "Where is the story going?", "prose", 0.8),
        ),
        words_per_page=500,
        closing_heading="",
        max_sections=6,
    ),
    ReportType.ACADEMIC_RESEARCH: ReportProfile(
        label="Academic research report",
        audience="an academic reader who expects scholarly rigour",
        voice="Formal, objective, precisely hedged. Distinguish established findings from contested ones. Third person.",
        skeleton=(
            _A("Introduction and Research Question", "What is the question and why is it significant?", "prose", 0.8),
            _A("Literature and Prior Work", "What has been established so far?", "prose", 1.2),
            _A("Key Findings", "What does the evidence show?", "mixed", 1.4),
            _A("Discussion", "How should the findings be interpreted, and what conflicts exist?", "prose", 1.1),
            _A("Open Problems", "What remains unresolved?", "bullets", 0.6),
        ),
        words_per_page=500,
        closing_heading="Conclusion",
        closing_guidance="A concise scholarly conclusion: contribution, limits, and future research.",
        max_sections=7,
    ),
    ReportType.BUSINESS_ANALYSIS: ReportProfile(
        label="Business analysis",
        audience="a business leader or investor",
        voice="Commercially sharp. Focus on market, money, competition and risk. Quantify sizes, growth and share.",
        skeleton=(
            _A("Market Landscape", "How big is the market and how is it moving?", "mixed", 1.1),
            _A("Key Players", "Who competes and how are they positioned?", "table", 1.1),
            _A("Business Models and Economics", "How is money made?", "mixed", 1.0),
            _A("Opportunities and Threats", "Where is the upside and the downside?", "table", 0.9),
            _A("Strategic Outlook", "What should a business expect next?", "bullets", 0.7),
        ),
        closing_heading="Recommendations",
        closing_guidance="Strategic moves, each tied to a finding, with the main risk of each.",
        max_sections=7,
    ),
    ReportType.POLICY_BRIEF: ReportProfile(
        label="Policy brief",
        audience="a policymaker or regulator",
        voice="Neutral, evidence-led and actionable. Name stakeholders and trade-offs explicitly.",
        skeleton=(
            _A("The Policy Problem", "What is the problem and who is affected?", "prose", 0.9),
            _A("Current Landscape", "What rules, programmes or approaches exist today?", "mixed", 1.0),
            _A("Policy Options", "What options exist and what are their trade-offs?", "table", 1.3),
            _A("Stakeholder Impacts", "Who gains and who loses under each option?", "table", 0.8),
        ),
        words_per_page=400,
        closing_heading="Recommendations",
        closing_guidance="Specific, feasible policy recommendations, each tied to evidence and to a stakeholder.",
        max_sections=6,
    ),
    ReportType.CUSTOM: ReportProfile(
        label="Custom report",
        audience="the reader described in the custom instructions",
        voice="Follow the custom instructions for tone; otherwise clear and analytical.",
        skeleton=(
            _A("Context", "What is the subject and why does it matter?", "prose", 0.8),
            _A("Findings", "What does the evidence establish?", "mixed", 1.4),
            _A("Analysis", "What do the findings mean together?", "mixed", 1.0),
        ),
        closing_heading="Conclusion",
        closing_guidance="Answer the question directly in light of the findings.",
        planner_guidance="The custom instructions take precedence over this skeleton.",
    ),
}


def resolve_profile(report_type: str | ReportType | None) -> tuple[ReportType, ReportProfile]:
    """Map a report-type string (``"policy_brief"``, ``"ReportType.POLICY_BRIEF"``...) to its profile."""
    raw = str(report_type or "").strip().lower()
    if raw.startswith("reporttype."):
        raw = raw.split(".", 1)[1]
    try:
        rtype = ReportType(raw)
    except ValueError:
        rtype = ReportType.AUTO
    return rtype, REPORT_PROFILES[rtype]


def normalise_format(value: object) -> str:
    fmt = str(value or "").strip().lower()
    return fmt if fmt in SECTION_FORMATS else "mixed"
