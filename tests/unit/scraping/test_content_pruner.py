"""
Unit tests for ContentPruner: HTML → Markdown pruning.

Covers config defaults, script/style/nav stripping, main-content selection,
table and link handling, post-processing (blank-line and header collapsing),
the no-selectolax fallback path, HTML entity decoding, statistics, and the
global singleton accessor.
"""

from __future__ import annotations

import pytest

from searchops.scraping import content_pruner as cp_mod
from searchops.scraping.content_pruner import (
    ContentPruner,
    PruningConfig,
    get_content_pruner,
)

# ── Config ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_default_remove_selectors_cover_boilerplate():
    cfg = PruningConfig()

    assert "script" in cfg.remove_selectors
    assert "nav" in cfg.remove_selectors
    assert ".ad" in cfg.remove_selectors
    assert "[aria-hidden='true']" in cfg.remove_selectors


@pytest.mark.unit
def test_default_preserve_selectors_target_main_content():
    cfg = PruningConfig()

    assert "main" in cfg.preserve_selectors
    assert "article" in cfg.preserve_selectors


@pytest.mark.unit
def test_explicit_selectors_are_not_overwritten():
    cfg = PruningConfig(remove_selectors=["p"], preserve_selectors=["body"])

    assert cfg.remove_selectors == ["p"]
    assert cfg.preserve_selectors == ["body"]


@pytest.mark.unit
def test_default_flags():
    cfg = PruningConfig()

    assert cfg.preserve_tables is True
    assert cfg.preserve_links is True
    assert cfg.preserve_images is False, "Images add tokens and default to off"
    assert cfg.min_text_length == 50


# ── prune(): guards and dispatch ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("html", ["", "   ", "\n\t ", None])
def test_prune_empty_input_returns_empty_string(html):
    assert ContentPruner().prune(html) == ""


@pytest.mark.unit
def test_prune_falls_back_to_tag_stripping_on_internal_error():
    pruner = ContentPruner()

    class Boom:
        def css(self, *a, **k):
            raise RuntimeError("parser exploded")

        def css_first(self, *a, **k):
            raise RuntimeError("parser exploded")

    original = cp_mod.HTMLParser
    cp_mod.HTMLParser = Boom  # type: ignore[assignment]
    try:
        out = pruner.prune("<p>rescue me</p>")
    finally:
        cp_mod.HTMLParser = original  # type: ignore[assignment]

    assert "rescue me" in out, "Failure path must still return tag-stripped text"


# ── prune(): boilerplate removal ──────────────────────────────────────────────


@pytest.mark.unit
def test_prune_removes_script_and_style():
    html = """
    <html><head><style>.x{color:red}</style></head>
    <body><script>alert(1)</script><main><p>Real content here.</p></main></body></html>
    """
    out = ContentPruner().prune(html)

    assert "alert(1)" not in out
    assert "color:red" not in out
    assert "Real content here." in out


@pytest.mark.unit
def test_prune_removes_navigation_and_ads():
    html = """
    <body>
      <nav><a href="/x">Home</a></nav>
      <div class="ad">Buy now!</div>
      <main><p>Substantive article body that survives pruning.</p></main>
    </body>
    """
    out = ContentPruner().prune(html)

    assert "Buy now!" not in out
    assert "Substantive article body" in out


@pytest.mark.unit
def test_prune_removes_aria_hidden_content():
    html = "<body><span aria-hidden='true'>decorative</span><main><p>Visible text.</p></main></body>"
    out = ContentPruner().prune(html)

    assert "decorative" not in out
    assert "Visible text." in out


@pytest.mark.unit
def test_prune_falls_back_to_body_when_no_main_selector_matches():
    html = "<html><body><div><p>Loose content with no semantic wrapper at all.</p></div></body></html>"
    out = ContentPruner().prune(html)

    assert "Loose content" in out


@pytest.mark.unit
def test_prune_prefers_article_over_body_fallback():
    html = """
    <body>
      <div>Page chrome that should be ignored.</div>
      <article><p>The article body is what we want to keep here.</p></article>
    </body>
    """
    out = ContentPruner().prune(html)

    assert "The article body" in out


# ── prune(): structure conversion ─────────────────────────────────────────────


@pytest.mark.unit
def test_prune_converts_headings_to_markdown():
    html = "<main><h1>Title</h1><h2>Section</h2><p>Body text follows.</p></main>"
    out = ContentPruner().prune(html)

    assert "# Title" in out
    assert "## Section" in out


@pytest.mark.unit
def test_prune_converts_lists_to_markdown_bullets():
    html = "<main><ul><li>First item</li><li>Second item</li></ul></main>"
    out = ContentPruner().prune(html)

    assert "First item" in out and "Second item" in out
    assert out.count("-") >= 2 or out.count("*") >= 2, "List markers emitted"


@pytest.mark.unit
def test_prune_renders_tables_when_preserve_tables_enabled():
    html = "<main><table><tr><th>H1</th><th>H2</th></tr><tr><td>a</td><td>b</td></tr></table></main>"
    out = ContentPruner().prune(html)

    assert "|" in out, "Table converted to Markdown pipes"
    assert "H1" in out and "a" in out


@pytest.mark.unit
def test_prune_keeps_links_when_preserve_links_enabled():
    html = "<main><p>See <a href='https://example.com'>the docs</a> for more.</p></main>"
    out = ContentPruner().prune(html)

    assert "https://example.com" in out
    assert "the docs" in out


@pytest.mark.unit
def test_prune_keeps_code_blocks_when_preserve_code_enabled():
    html = "<main><pre><code>def f(): return 1</code></pre></main>"
    out = ContentPruner().prune(html)

    assert "def f(): return 1" in out


# ── prune(): post-processing ──────────────────────────────────────────────────


@pytest.mark.unit
def test_post_process_collapses_excess_blank_lines():
    pruner = ContentPruner()
    out = pruner._post_process("A\n\n\n\n\nB")

    assert "\n\n\n" not in out
    assert "A" in out and "B" in out


@pytest.mark.unit
def test_post_process_collapses_consecutive_headers():
    pruner = ContentPruner()
    out = pruner._post_process("# One\n## Two\n### Three\n\nBody")

    # Only the first of a consecutive header run survives.
    assert out.count("#") == 1
    assert "# One" in out
    assert "Body" in out


@pytest.mark.unit
def test_post_process_strips_trailing_whitespace():
    pruner = ContentPruner()
    out = pruner._post_process("line one   \nline two\t\n")

    assert "line one   " not in out
    assert "line one" in out


@pytest.mark.unit
def test_post_process_ends_with_newline():
    pruner = ContentPruner()
    out = pruner._post_process("Some content")

    assert out.endswith("\n")


@pytest.mark.unit
def test_post_process_handles_empty_input():
    assert ContentPruner()._post_process("") == ""


# ── prune(): the no-selectolax fallback path ──────────────────────────────────


@pytest.fixture
def no_selectolax(monkeypatch):
    """Force the regex fallback by hiding selectolax."""
    monkeypatch.setattr(cp_mod, "SELECTOLAX_AVAILABLE", False)
    return ContentPruner()


@pytest.mark.unit
def test_basic_path_strips_script_and_style(no_selectolax):
    html = "<html><head><style>p{color:red}</style></head><body><p>Kept text.</p></body></html>"
    out = no_selectolax.prune(html)

    assert "color:red" not in out
    assert "Kept text." in out


@pytest.mark.unit
def test_basic_path_removes_semantic_boilerplate_tags(no_selectolax):
    html = "<body><nav>Menu junk</nav><p>Real paragraph content.</p></body>"
    out = no_selectolax.prune(html)

    assert "Menu junk" not in out
    assert "Real paragraph content." in out


@pytest.mark.unit
def test_basic_strip_tags_decodes_html_entities(no_selectolax):
    out = no_selectolax._basic_strip_tags("<p>A &amp; B &lt;tag&gt; &quot;q&quot; &#39;s&#39;</p>")

    assert "&" in out and "&amp;" not in out
    assert "<tag>" in out and "&lt;" not in out
    assert '"q"' in out
    assert "'s'" in out


@pytest.mark.unit
def test_basic_strip_tags_decodes_nbsp(no_selectolax):
    assert "\u00a0" not in no_selectolax._basic_strip_tags("<p>a&nbsp;b</p>")


@pytest.mark.unit
def test_basic_strip_tags_replaces_block_tags_with_newlines(no_selectolax):
    out = no_selectolax._basic_strip_tags("<p>one</p><p>two</p>")

    assert "one" in out and "two" in out
    assert "\n" in out


@pytest.mark.unit
def test_basic_strip_tags_strips_remaining_tags(no_selectolax):
    out = no_selectolax._basic_strip_tags("<span class='x'>inner</span>")

    assert "inner" in out
    assert "<span" not in out


@pytest.mark.unit
def test_basic_path_converts_bold_markers(no_selectolax):
    out = no_selectolax.prune("<p>__bold text__</p>")

    assert "**bold text**" in out


# ── get_stats ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_stats_reports_lengths_and_reduction():
    pruner = ContentPruner()
    html = "x" * 400
    markdown = "x" * 100

    stats = pruner.get_stats(html, markdown)

    assert stats["original_length"] == 400
    assert stats["pruned_length"] == 100
    assert stats["reduction_percent"] == pytest.approx(75.0)


@pytest.mark.unit
def test_get_stats_token_estimate_is_quarter_of_chars():
    pruner = ContentPruner()
    stats = pruner.get_stats("x" * 400, "x" * 100)

    assert stats["original_tokens_estimate"] == 100
    assert stats["pruned_tokens_estimate"] == 25
    assert stats["token_reduction_percent"] == pytest.approx(75.0)


@pytest.mark.unit
def test_get_stats_with_empty_html_has_no_division_error():
    stats = ContentPruner().get_stats("", "content")

    assert stats["reduction_percent"] == 0
    assert stats["token_reduction_percent"] == 0


# ── Singleton ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_content_pruner_returns_singleton():
    cp_mod._pruner = None
    try:
        a = get_content_pruner()
        b = get_content_pruner()
        assert a is b, "Second call must reuse the cached instance"
    finally:
        cp_mod._pruner = None


@pytest.mark.unit
def test_get_content_pruner_accepts_custom_config():
    cp_mod._pruner = None
    try:
        cfg = PruningConfig(min_text_length=10)
        assert get_content_pruner(cfg).config.min_text_length == 10
    finally:
        cp_mod._pruner = None
