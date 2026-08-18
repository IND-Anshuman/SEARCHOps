"""
Annotated type validators.

Used with Pydantic v2 Annotated fields to enforce constraints declaratively
without repeating validation logic across models.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import AnyHttpUrl, Field, StringConstraints

# ─── String validators ────────────────────────────────────────────────────────

#: A non-empty, stripped string
NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

#: A non-empty string up to 255 characters (suitable for names/labels)
ShortStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]

#: A validated HTTP/HTTPS URL string
UrlStr = Annotated[AnyHttpUrl, Field(...)]

#: A slug: lowercase alphanumeric + hyphens
SlugStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9-]+$",
    ),
]

# ─── Numeric validators ───────────────────────────────────────────────────────

#: A positive integer (>= 1)
PositiveIntField = Annotated[int, Field(ge=1)]

#: A non-negative integer (>= 0)
NonNegativeIntField = Annotated[int, Field(ge=0)]

#: A float in [0.0, 1.0]
ProportionField = Annotated[float, Field(ge=0.0, le=1.0)]

#: A positive float (> 0.0)
PositiveFloatField = Annotated[float, Field(gt=0.0)]

#: A non-negative float (>= 0.0)
NonNegativeFloatField = Annotated[float, Field(ge=0.0)]
