"""Tests for detecting bot walls disguised as HTTP 200 responses.

The problem these tests pin down: a consent banner, a CAPTCHA interstitial, or
a login gate all return status 200 while containing *zero* usable content.
Today the pipeline counts those as successful scrapes, so garbage enters the
evidence layer and every downstream stage (extraction, verification, the
report) inherits it.

Design decisions:
* Detection is **deterministic** — no LLM, no network, just the fetched text.
* A clean page must never be flagged (false positives destroy real evidence).
* The detector reports *why*, so the DLQ and metrics can act on it.
"""

from __future__ import annotations

from searchops.scraping.wall_detector import WallClass, detect_wall

CONSENT_WALL = """
<html><head><title>Cookie Notice</title></head><body>
<div id="cookie-consent-banner">
  <h2>We value your privacy</h2>
  <p>We use cookies to improve your experience.</p>
  <button>Accept all cookies</button>
  <button>Manage preferences</button>
</div>
</body></html>
"""

CAPTCHA_WALL = """
<html><head><title>Attention Required! | Cloudflare</title></head><body>
<div class="cf-browser-verification">
  <h1>Checking your browser before accessing</h1>
  <p>Please stand by, while we are checking your browser...</p>
  <div class="g-recaptcha" data-sitekey="abc"></div>
</div>
</body></html>
"""

LOGIN_WALL = """
<html><head><title>Sign in</title></head><body>
<form action="/login" method="post">
  <input type="text" name="username" placeholder="Username">
  <input type="password" name="password" placeholder="Password">
  <button type="submit">Log in</button>
</form>
<p>You must be signed in to view this content.</p>
</body></html>
"""

CLEAN_ARTICLE = """
<html><head><title>Understanding Vector Databases</title></head><body>
<nav><a href="/">Home</a> <a href="/docs">Docs</a></nav>
<article>
<h1>Understanding Vector Databases</h1>
<p>Vector databases store embeddings and support approximate nearest
neighbour search. They are the retrieval backbone of modern RAG systems.</p>
<p>Popular implementations include Qdrant, Milvus, Weaviate and pgvector.
Each offers a different trade-off between recall, latency and cost.</p>
<h2>Indexing strategies</h2>
<p>HNSW builds a navigable small-world graph over the embedding space and
remains the default choice for most workloads because it balances recall
against build time.</p>
</article>
</body></html>
"""

EMPTY_SHELL = """
<html><head><title>Loading...</title>
<script>window.location = "/app"</script>
</head><body><div id="root"></div></body></html>
"""


class TestConsentWall:
    def test_consent_banner_is_detected(self) -> None:
        result = detect_wall(CONSENT_WALL)
        assert result.wall_class is WallClass.CONSENT

    def test_detects_eu_cookie_phrasing(self) -> None:
        body = "<html><body><div class='cookiebar'>This website uses cookies.</div></body></html>"
        assert detect_wall(body).wall_class is WallClass.CONSENT


class TestCaptchaWall:
    def test_captcha_interstitial_is_detected(self) -> None:
        result = detect_wall(CAPTCHA_WALL)
        assert result.wall_class is WallClass.CAPTCHA

    def test_detects_verify_you_are_human(self) -> None:
        body = "<html><body><h1>Verify you are human</h1><div class='h-captcha'></div></body></html>"
        assert detect_wall(body).wall_class is WallClass.CAPTCHA


class TestLoginWall:
    def test_login_form_is_detected(self) -> None:
        result = detect_wall(LOGIN_WALL)
        assert result.wall_class is WallClass.LOGIN

    def test_detects_sign_in_prompt_with_password_field(self) -> None:
        body = "<html><body><h2>Please sign in to continue</h2><input type='password'></body></html>"
        assert detect_wall(body).wall_class is WallClass.LOGIN


class TestJsShell:
    def test_js_only_shell_is_detected(self) -> None:
        result = detect_wall(EMPTY_SHELL)
        assert result.wall_class is WallClass.JS_SHELL

    def test_nav_with_no_article_text_is_a_shell(self) -> None:
        body = """
        <html><body>
          <nav><a href="/a">A</a><a href="/b">B</a></nav>
          <div id="app"></div>
        </body></html>
        """
        assert detect_wall(body).wall_class is WallClass.JS_SHELL


class TestEmpty:
    def test_empty_body_is_empty(self) -> None:
        assert detect_wall("").wall_class is WallClass.EMPTY

    def test_whitespace_only_is_empty(self) -> None:
        assert detect_wall("   \n\t  ").wall_class is WallClass.EMPTY

    def test_tags_only_with_no_text_is_empty(self) -> None:
        assert detect_wall("<html><body><div></div></body></html>").wall_class is WallClass.EMPTY


class TestCleanPageIsNeverFlagged:
    def test_clean_article_is_none(self) -> None:
        result = detect_wall(CLEAN_ARTICLE)
        assert result.wall_class is WallClass.NONE

    def test_article_mentioning_cookies_is_not_a_consent_wall(self) -> None:
        """A real article *about* privacy must not be flagged as a banner."""
        body = """
        <html><body><article>
        <h1>Cookie Policy Explained</h1>
        <p>Many sites display a cookie banner asking visitors to accept
        cookies. This article explains what that means and what the options
        are, in enough detail that a reader can make an informed choice.</p>
        <p>Consent must be freely given, specific and informed under GDPR.</p>
        </article></body></html>
        """
        assert detect_wall(body).wall_class is WallClass.NONE

    def test_article_with_login_links_is_not_a_login_wall(self) -> None:
        body = """
        <html><body><article>
        <h1>Authentication Patterns</h1>
        <p>A login form typically contains a username and a password field.
        This article covers session cookies, refresh tokens and OAuth flows
        with enough surrounding prose that a reader learns the trade-offs.</p>
        </article></body></html>
        """
        assert detect_wall(body).wall_class is WallClass.NONE


class TestResultShape:
    def test_none_reports_no_marker(self) -> None:
        result = detect_wall(CLEAN_ARTICLE)
        assert result.marker is None
        assert result.is_wall is False

    def test_wall_reports_its_marker(self) -> None:
        result = detect_wall(CONSENT_WALL)
        assert result.is_wall is True
        assert result.marker is not None

    def test_text_length_is_reported(self) -> None:
        result = detect_wall(CLEAN_ARTICLE)
        assert result.text_length > 0

    def test_is_wall_is_true_for_every_wall_class(self) -> None:
        for body, expected in (
            (CONSENT_WALL, WallClass.CONSENT),
            (CAPTCHA_WALL, WallClass.CAPTCHA),
            (LOGIN_WALL, WallClass.LOGIN),
            (EMPTY_SHELL, WallClass.JS_SHELL),
            ("", WallClass.EMPTY),
        ):
            result = detect_wall(body)
            assert result.wall_class is expected
            assert result.is_wall is True
