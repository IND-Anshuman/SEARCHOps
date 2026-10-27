"""LaTeX math rendering for report exports.

The LLM emits LaTeX math notation (\\zeta(s), \\mathbb{C}, \\frac{1}{2}).
Without rendering, this shows up as raw backslash commands in the PDF/PPTX/DOCX.

Two rendering paths:
- PDF: latex2mathml converts to MathML, which Chromium renders natively
- PPTX/DOCX: pylatexenc converts to Unicode text (ζ(s), ℂ, ½)
"""

from __future__ import annotations

import re

import structlog

log = structlog.getLogger(__name__)

# ── Patterns ────────────────────────────────────────────────────────────────

# Display math: $$...$$ or \[...\]
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
# Inline math: $...$ or \(...\)
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)|\\\((.+?)\\\)")

# Bare LaTeX commands that appear in LLM output without $ delimiters.
# These are matched as complete commands (command + optional args) and
# converted to MathML/Unicode. Order matters: longer commands first.
_BARE_LATEX_RE = re.compile(
    r"\\(mathbb\{[A-Z]\}|mathfrak\{[A-Z]\}|mathcal\{[A-Z]\})"
    r"|\\(zeta|xi|Xi|nu|Nu|rho|tau|chi|psi|Psi|phi|Phi|theta|Theta|kappa|epsilon|varepsilon|sigma|Sigma|pi|Pi|mu|Mu|lambda|Lambda|delta|Delta|gamma|Gamma|beta|alpha|Omega|omega|Gamma|Lambda|Phi|Sigma|Psi|Theta|Xi|Pi|infty)"
    r"|\\(frac|sqrt|sum|prod|int|oint|bigcup|bigcap|bigoplus|bigotimes)\{([^{}]+)\}\{([^{}]+)\}"
    r"|\\(frac|sqrt)\{([^{}]+)\}\{([^{}]+)\}"
    r"|\\(frac|sqrt)\{([^{}]+)\}"
    r"|\\(text|mathrm|mathbf|mathit|mathsf|mathcal|mathbb)\{([^{}]+)\}"
    r"|\\(partial|nabla|ell|hbar|Re|Im|arg|deg|det|dim|exp|gcd|hom|inf|ker|lg|lim|liminf|limsup|ln|max|min|mod|Pr|sec|sin|cos|tan|cot|arcsin|arccos|arctan|sinh|cosh|tanh|sup|det|log)"
    r"|\\(dots|cdots|ldots|vdots|ddots)"
    r"|\\(cdot|times|div|pm|mp|circ|bullet|oplus|otimes|oslash|odot)"
    r"|\\(leq|geq|neq|approx|equiv|sim|propto|cong|simeq|asymp|bowtie)"
    r"|\\(in|notin|ni|supset|supseteq|subset|subseteq|cup|cap|setminus)"
    r"|\\(forall|exists|emptyset|varnothing|neg|wedge|vee|top|bot|vdash|models)"
    r"|\\(rightarrow|leftarrow|Rightarrow|Leftarrow|leftrightarrow|Leftrightarrow|uparrow|downarrow|mapsto|hookrightarrow|hookleftarrow)"
    r"|\\(overline|underline|widehat|overrightarrow|overleftarrow|hat|tilde|bar|vec|dot|ddot)"
    r"|\\([a-zA-Z]+)"  # any other \command
)


# ── PDF: LaTeX → MathML ────────────────────────────────────────────────────


def _latex_to_mathml(latex: str) -> str:
    """Convert a LaTeX math expression to MathML."""
    try:
        from latex2mathml.converter import convert as latex2mathml_convert

        result = latex2mathml_convert(latex.strip())
        # If the result still contains LaTeX commands, latex2mathml didn't
        # understand the input — fall back to inline code.
        if "\\" in result or "{" in result:
            return f"<code>{latex}</code>"
        return result
    except Exception as e:
        log.debug("latex2mathml failed", latex=latex[:50], error=str(e))
        return f"<code>{latex}</code>"


def render_math_for_html(text: str) -> str:
    """Replace LaTeX math in *text* with MathML for HTML rendering.

    Handles display math ($$...$$, \\[...\\]), inline math ($...$),
    and bare LaTeX commands (\\zeta, \\mathbb{C}, \\frac{1}{2}).
    Non-math text is returned unchanged.
    """
    if not text or ("\\" not in text and "$" not in text):
        return text

    # Display math first ($$...$$)
    def _display_replacer(m: re.Match[str]) -> str:
        latex = m.group(1) or m.group(2) or ""
        if not latex.strip():
            return m.group(0)
        mathml = _latex_to_mathml(latex)
        return f"<div class='math-display'>{mathml}</div>"

    text = _DISPLAY_MATH_RE.sub(_display_replacer, text)

    # Inline math ($...$ or \(...\))
    def _inline_replacer(m: re.Match[str]) -> str:
        latex = m.group(1) or m.group(2) or ""
        if not latex.strip():
            return m.group(0)
        mathml = _latex_to_mathml(latex)
        return f"<span class='math-inline'>{mathml}</span>"

    text = _INLINE_MATH_RE.sub(_inline_replacer, text)

    # Bare LaTeX commands (no $ delimiters)
    def _bare_replacer(m: re.Match[str]) -> str:
        latex = m.group(0)
        if not latex.strip():
            return m.group(0)
        mathml = _latex_to_mathml(latex)
        return f"<span class='math-inline'>{mathml}</span>"

    text = _BARE_LATEX_RE.sub(_bare_replacer, text)

    return text


# ── PPTX/DOCX: LaTeX → Unicode ─────────────────────────────────────────────


def _latex_to_unicode(latex: str) -> str:
    """Convert a LaTeX math expression to Unicode text.

    Uses a comprehensive manual converter. pylatexenc is NOT used because
    it produces poor output for common math commands:
    - \\Re → nothing (should be ℜ)
    - \\sum → ∑ (should be Σ)
    - \\frac{1}{2} → rac{1}{2} (should be 1/2)
    """
    return _manual_latex_to_unicode(latex)


def _manual_latex_to_unicode(latex: str) -> str:
    """Manual fallback for common LaTeX math symbols."""
    replacements = {
        r"\zeta": "ζ",
        r"\mathbb{C}": "ℂ",
        r"\mathbb{R}": "ℝ",
        r"\mathbb{Z}": "ℤ",
        r"\mathbb{N}": "ℕ",
        r"\mathbb{Q}": "ℚ",
        r"\pi": "π",
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\epsilon": "ε",
        r"\theta": "θ",
        r"\lambda": "λ",
        r"\mu": "μ",
        r"\sigma": "σ",
        r"\phi": "φ",
        r"\omega": "ω",
        r"\Gamma": "Γ",
        r"\Delta": "Δ",
        r"\Theta": "Θ",
        r"\Lambda": "Λ",
        r"\Sigma": "Σ",
        r"\Phi": "Φ",
        r"\Omega": "Ω",
        r"\infty": "∞",
        r"\pm": "±",
        r"\times": "×",
        r"\div": "÷",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\neq": "≠",
        r"\approx": "≈",
        r"\sim": "~",
        r"\rightarrow": "→",
        r"\leftarrow": "←",
        r"\Rightarrow": "⇒",
        r"\Leftarrow": "⇐",
        r"\Re": "ℜ",
        r"\Im": "ℑ",
        r"\sum": "Σ",
        r"\prod": "Π",
        r"\int": "∫",
        r"\partial": "∂",
        r"\nabla": "∇",
        r"\forall": "∀",
        r"\exists": "∃",
        r"\in": "∈",
        r"\notin": "∉",
        r"\subset": "⊂",
        r"\supset": "⊃",
        r"\cup": "∪",
        r"\cap": "∩",
        r"\emptyset": "∅",
        r"\ldots": "…",
        r"\cdots": "⋯",
        r"\dots": "…",
        r"\frac": "",  # handled below
        r"\sqrt": "√",
        r"\bar": "̄",
        r"\hat": "̂",
        r"\vec": "→",
        r"\cdot": "·",
        r"\circ": "∘",
        r"\oplus": "⊕",
        r"\otimes": "⊗",
        r"\perp": "⊥",
        r"\parallel": "∥",
        r"\angle": "∠",
        r"\degree": "°",
        r"\prime": "′",
        r"\backslash": "\\",
        r"\ ": " ",
        r"\,": " ",
        r"\;": " ",
        r"\!": "",
        r"\quad": "  ",
        r"\qquad": "    ",
    }

    result = latex

    # Handle \mathbb{X} → blackboard bold (must run before general replacements)
    mathbb_map = {"C": "ℂ", "R": "ℝ", "Z": "ℤ", "N": "ℕ", "Q": "ℚ"}
    for letter, char in mathbb_map.items():
        result = result.replace(f"\\mathbb{{{letter}}}", char)

    # Handle \frac{a}{b} → a/b (must run BEFORE \frac → empty replacement)
    result = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", result)
    # Handle \sqrt{a} → √a
    result = re.sub(r"\\sqrt\{([^{}]+)\}", r"√\1", result)

    for latex_cmd, unicode_char in replacements.items():
        result = result.replace(latex_cmd, unicode_char)

    # Handle ^{...} → superscript (just strip braces)
    result = re.sub(r"\^\{([^{}]+)\}", r"^\1", result)
    # Handle _{...} → subscript (just strip braces)
    result = re.sub(r"_\{([^{}]+)\}", r"_\1", result)
    # Handle ^x → ^x
    result = re.sub(r"\^([a-zA-Z0-9])", r"^\1", result)
    # Handle _x → _x
    result = re.sub(r"_([a-zA-Z0-9])", r"_\1", result)
    # Strip remaining braces
    result = result.replace("{", "").replace("}", "")

    return result


def _strip_control_chars(text: str) -> str:
    """Remove control characters that lxml/Word cannot handle.

    Unicode math rendering can produce combining characters and control
    characters (e.g. U+0301 combining acute) that are valid Python strings
    but rejected by lxml's XML serializer. Strip everything except
    printable characters, tabs, newlines, and known Unicode math symbols.
    """
    return "".join(
        ch
        for ch in text
        if ch in "\t\n\r" or (ord(ch) >= 32 and ord(ch) != 127) or ch in "ℂℝℤℕℚℜℑζξχψφθΓΔΛΣΨΘΞΠ∞±×÷≤≥≠≈∈∉⊂⊃∪∩∫∑∏√"
    )


def render_math_for_text(text: str) -> str:
    """Replace LaTeX math in *text* with Unicode for PPTX/DOCX rendering.

    Handles display math ($$...$$, \\[...\\]), inline math ($...$),
    and bare LaTeX commands (\\zeta, \\mathbb{C}, \\frac{1}{2}).
    Non-math text is returned unchanged.
    """
    if not text or ("\\" not in text and "$" not in text):
        return text

    # Display math first ($$...$$)
    def _display_replacer(m: re.Match[str]) -> str:
        latex = m.group(1) or m.group(2) or ""
        if not latex.strip():
            return m.group(0)
        return _latex_to_unicode(latex)

    text = _DISPLAY_MATH_RE.sub(_display_replacer, text)

    # Inline math ($...$ or \(...\))
    def _inline_replacer(m: re.Match[str]) -> str:
        latex = m.group(1) or m.group(2) or ""
        if not latex.strip():
            return m.group(0)
        return _latex_to_unicode(latex)

    text = _INLINE_MATH_RE.sub(_inline_replacer, text)

    # Bare LaTeX commands (no $ delimiters) — use manual converter directly
    # because pylatexenc produces poor output for common math commands
    # (\Re → nothing, \sum → ∑ instead of Σ)
    def _bare_replacer(m: re.Match[str]) -> str:
        latex = m.group(0)
        if not latex.strip():
            return m.group(0)
        return _manual_latex_to_unicode(latex)

    text = _BARE_LATEX_RE.sub(_bare_replacer, text)

    return _strip_control_chars(text)
