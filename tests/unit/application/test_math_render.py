"""Tests for LaTeX math rendering in report exports."""

from searchops.application.exports.math_render import render_math_for_html, render_math_for_text


class TestMathRenderForHTML:
    def test_zeta_inline(self):
        result = render_math_for_html("The function $\\zeta(s)$ is central.")
        assert "<math" in result or "<code>" in result
        assert "\\zeta" not in result

    def test_display_math(self):
        result = render_math_for_html("$$ \\zeta(s) = 0 $$")
        assert "\\zeta" not in result

    def test_complex_numbers(self):
        result = render_math_for_html("$\\mathbb{C}$")
        assert "\\mathbb" not in result

    def test_fraction(self):
        result = render_math_for_html("$\\frac{1}{2}$")
        assert "\\frac" not in result

    def test_bare_latex_zeta(self):
        result = render_math_for_html("The function \\zeta(s) is central.")
        assert "\\zeta" not in result
        assert "<math" in result or "<code>" in result

    def test_bare_latex_mathbb(self):
        result = render_math_for_html("\\mathbb{C} is the complex plane.")
        assert "\\mathbb" not in result

    def test_bare_latex_fraction(self):
        result = render_math_for_html("The value \\frac{1}{2} is critical.")
        assert "\\frac" not in result

    def test_no_math_unchanged(self):
        text = "This has no math at all."
        assert render_math_for_html(text) == text

    def test_empty_string(self):
        assert render_math_for_html("") == ""


class TestMathRenderForText:
    def test_zeta_unicode(self):
        result = render_math_for_text("$\\zeta(s)$")
        assert "ζ" in result
        assert "\\zeta" not in result

    def test_complex_unicode(self):
        result = render_math_for_text("$\\mathbb{C}$")
        assert "ℂ" in result
        assert "\\mathbb" not in result

    def test_fraction_unicode(self):
        result = render_math_for_text("$\\frac{1}{2}$")
        assert "\\frac" not in result

    def test_display_math_unicode(self):
        result = render_math_for_text("$$ \\zeta(s) = 0 $$")
        assert "\\zeta" not in result

    def test_bare_latex_zeta(self):
        result = render_math_for_text("The function \\zeta(s) is central.")
        assert "ζ" in result
        assert "\\zeta" not in result

    def test_bare_latex_mathbb(self):
        result = render_math_for_text("\\mathbb{C} is the complex plane.")
        assert "ℂ" in result
        assert "\\mathbb" not in result

    def test_bare_latex_fraction(self):
        result = render_math_for_text("The value \\frac{1}{2} is critical.")
        assert "\\frac" not in result

    def test_no_math_unchanged(self):
        text = "This has no math at all."
        assert render_math_for_text(text) == text

    def test_empty_string(self):
        assert render_math_for_text("") == ""

    def test_pi(self):
        result = render_math_for_text("$\\pi$")
        assert "π" in result
