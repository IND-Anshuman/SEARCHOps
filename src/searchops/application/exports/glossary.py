"""Glossary built from extracted entities.

Costs nothing: no LLM call. The entity extractor already runs during research
and produces typed ``KGEntity`` objects with names, types and descriptions —
none of it was reaching the user. A glossary is one of the most-used study
aids in this category, and here it is pure data shaping.

The rule is absolute: if an entity has no description, the glossary entry has
an empty definition. Filling it in from model memory would produce a
definition that sounds authoritative and may be wrong — the exact failure
users complain about in AI research tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from searchops.application.report_document import ReportDocument, _field

# A glossary of 60 terms is not a glossary, it is a dump.
_MAX_TERMS = 20


@dataclass
class GlossaryEntry:
    term: str
    entity_type: str
    definition: str

    def to_dict(self) -> dict[str, str]:
        return {
            "term": self.term,
            "entity_type": self.entity_type,
            "definition": self.definition,
        }


def build_glossary(doc: ReportDocument, *, max_terms: int = _MAX_TERMS) -> list[GlossaryEntry]:
    """Return real entities as glossary entries, most confident first.

    Entities without a description are kept with an empty definition rather
    than dropped, so the reader still learns the term exists and can follow it
    into the sources.
    """
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()

    ordered = sorted(
        doc.entities,
        key=lambda e: float(_field(e, "confidence", 0.0) or 0.0),
        reverse=True,
    )

    for entity in ordered:
        name = str(_field(entity, "name", "") or "").strip()
        if not name:
            continue
        key = _normalise(name)
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(
            GlossaryEntry(
                term=name[:120],
                entity_type=str(_field(entity, "entity_type", "") or "")[:60],
                definition=str(_field(entity, "description", "") or "").strip()[:400],
            )
        )
        if len(entries) >= max_terms:
            break

    return entries


def _normalise(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())
