"""Detecting bot walls that disguise themselves as HTTP 200 responses.

Why this module exists
----------------------
A consent banner, a CAPTCHA interstitial and a login gate all return status
``200`` while containing no usable content. The tiered pipeline escalates only
on *failure* status codes, so these pages were counted as **successful
scrapes** — and every downstream stage (claim extraction, verification, the
final report) then reasoned over garbage.

This detector is the deterministic gate that stops that: it inspects the
fetched text itself, not the status code.

Design decisions
----------------
* **No LLM, no network.** Pure text analysis. It runs on every successful
  fetch and must stay cheap.
* **False positives are worse than false negatives.** Dropping a real page
  destroys evidence that cannot be recovered; missing an exotic wall merely
  means a useless page gets extracted (which claim-level verification will
  then catch). So markers are deliberately specific and a page is only
  condemned when a wall signature coincides with an absence of real content.
* **Order matters.** CAPTCHA beats consent beats login, because a bot check
  is the strongest signal that we are being blocked outright.
* **The marker is reported**, so operators can see *why* a page was rejected
  and the histogram shows which wall actually dominates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import structlog

log = structlog.get_logger(__name__)

# Below this many characters of visible text, a page cannot carry an article.
MIN_TEXT_CHARS = 120


class WallClass(StrEnum):
    """Classification of a page that looks fetched but yields nothing."""

    NONE = "none"
    """Real content — proceed normally."""

    CONSENT = "consent"
    """A cookie/consent gate."""

    CAPTCHA = "captcha"
    """A bot-verification challenge."""

    LOGIN = "login"
    """An authentication wall."""

    JS_SHELL = "js_shell"
    """Navigation present but no article text (client-rendered app)."""

    EMPTY = "empty"
    """No readable text at all."""


@dataclass(frozen=True)
class WallResult:
    """Outcome of inspecting a fetched page."""

    wall_class: WallClass
    marker: str | None = None
    text_length: int = 0

    @property
    def is_wall(self) -> bool:
        """True when the page must not be treated as a successful scrape."""
        return self.wall_class is not WallClass.NONE


# Markers are matched against lowercased text. Each entry is (regex, label).
# Specificity is favoured over recall on purpose — see module docstring.

_CAPTCHA_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"checking your browser before accessing"), "cloudflare_check"),
    (re.compile(r"verify you are human"), "verify_human"),
    (re.compile(r"please stand by, while we are checking"), "cloudflare_standby"),
    (re.compile(r"\bg-recaptcha\b"), "recaptcha_widget"),
    (re.compile(r"\bh-captcha\b"), "hcaptcha_widget"),
    (re.compile(r"\bturnstile\b"), "turnstile"),
    (re.compile(r"are you a robot\??"), "are_you_a_robot"),
    (re.compile(r"unusual traffic from your computer"), "unusual_traffic"),
    (re.compile(r"complete the security check"), "security_check"),
    (re.compile(r"this site can.t be reached.*captcha"), "captcha_unreachable"),
]

_CONSENT_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"we value your privacy"), "we_value_privacy"),
    (re.compile(r"this (?:website|site) uses cookies"), "uses_cookies"),
    (re.compile(r"accept (?:all )?cookies"), "accept_cookies"),
    (re.compile(r"cookie[- ](?:consent|banner|notice|settings|preferences)"), "cookie_ui"),
    (re.compile(r"manage (?:your )?(?:cookie )?preferences"), "manage_prefs"),
    (re.compile(r"by continuing (?:to browse|to use this site)"), "continue_browse"),
    (re.compile(r"consent to (?:the use of )?cookies"), "consent_cookies"),
]

_LOGIN_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:you )?must be (?:logged|signed) in"), "must_sign_in"),
    (re.compile(r"please (?:log|sign)[- ]in to (?:continue|view|access)"), "please_sign_in"),
    (re.compile(r"create an account to (?:continue|view)"), "create_account"),
    (re.compile(r"\bsign in\b.*\bto continue\b"), "sign_in_to_continue"),
]

# A password field plus a sign-in word is a login wall even without a phrase.
_HAS_PASSWORD_FIELD = re.compile(r'<input[^>]+type=["\']?password', re.I)
_HAS_SUBMIT_LOGIN = re.compile(r"(?:log ?in|sign ?in|authenticate)", re.I)

_NAV_MARKERS = re.compile(r"<(?:nav|header)\b|<ul\s+class=[\"']?(?:nav|menu)", re.I)
_SCRIPT_TAG = re.compile(r"<script\b", re.I)
_STRIP_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _visible_text(html: str) -> str:
    """Crude visible-text extraction: drop scripts/styles, then tags."""
    without_scripts = re.sub(r"<script\b.*?</script>", " ", html, flags=re.I | re.S)
    without_styles = re.sub(r"<style\b.*?</style>", " ", without_scripts, flags=re.I | re.S)
    text = _STRIP_TAGS.sub(" ", without_styles)
    return _WHITESPACE.sub(" ", text).strip()


def _first_match(text: str, markers: list[tuple[re.Pattern[str], str]]) -> str | None:
    for pattern, label in markers:
        if pattern.search(text):
            return label
    return None


def detect_wall(html: str) -> WallResult:
    """Classify a fetched page as real content or a bot wall.

    A page is only condemned when a wall signature coincides with a lack of
    substantive text, which is what keeps a genuine article that merely
    *mentions* cookies or login forms from being discarded.
    """
    if not html or not html.strip():
        return WallResult(WallClass.EMPTY, marker="no_body", text_length=0)

    text = _visible_text(html)
    text_length = len(text)
    lowered = text.lower()
    thin = text_length < MIN_TEXT_CHARS

    # 1. CAPTCHA / bot check — the strongest block signal.
    marker = _first_match(lowered, _CAPTCHA_MARKERS)
    if marker:
        return WallResult(WallClass.CAPTCHA, marker=marker, text_length=text_length)

    # 2. Consent gate.
    marker = _first_match(lowered, _CONSENT_MARKERS)
    if marker and thin:
        return WallResult(WallClass.CONSENT, marker=marker, text_length=text_length)

    # 3. Login wall — a password field is decisive; otherwise needs a phrase
    #    plus thin content.
    if _HAS_PASSWORD_FIELD.search(html):
        return WallResult(WallClass.LOGIN, marker="password_field", text_length=text_length)
    marker = _first_match(lowered, _LOGIN_MARKERS)
    if marker and thin:
        return WallResult(WallClass.LOGIN, marker=marker, text_length=text_length)

    # 4. JS shell: navigation exists but there is no article text.
    if thin:
        if _NAV_MARKERS.search(html) or _SCRIPT_TAG.search(html):
            return WallResult(WallClass.JS_SHELL, marker="nav_without_text", text_length=text_length)
        return WallResult(WallClass.EMPTY, marker="no_text", text_length=text_length)

    return WallResult(WallClass.NONE, marker=None, text_length=text_length)


def summarise_walls(results: list[WallResult]) -> dict[str, int]:
    """Return a histogram of wall classes, for telemetry.

    The histogram is how a later hardening pass learns which wall actually
    dominates in practice instead of guessing.
    """
    histogram: dict[str, int] = {w.value: 0 for w in WallClass}
    for r in results:
        histogram[r.wall_class.value] += 1
    return histogram
