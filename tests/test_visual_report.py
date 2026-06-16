from bs4 import BeautifulSoup

import src.visual_report as vr
from src.visual_report import generate_visual_report


def test_visual_report_toc_links_match_rendered_heading_ids():
    report = """
# Automated Crypto Trading Bot Strategies

### **1.0 Introduction & Research Scope**

Intro body.

### **2.0 Determining the "Best" Configuration**

Configuration body.
"""

    html = generate_visual_report(
        "crypto bot strategies",
        report,
        sources=[],
        stats={},
        session_id="rp-test",
    )
    soup = BeautifulSoup(html, "html.parser")

    links = soup.select(".toc-sidebar nav a")
    assert [link.get_text(strip=True) for link in links] == [
        "1.0 Introduction & Research Scope",
        '2.0 Determining the "Best" Configuration',
    ]

    for link in links:
        target_id = link["href"].removeprefix("#")
        target = soup.find(id=target_id)
        assert target is not None
        assert target.name in {"h2", "h3"}


def test_visual_report_helpers_cover_fallbacks_and_json_safety(monkeypatch):
    assert vr._extract_report_title("", "Fallback") == ("Fallback", "")
    assert vr._extract_report_title("# Summary\n\n## Overview\nBody", "Fallback") == ("Fallback", "# Summary\n\n## Overview\nBody")

    headings = vr._extract_headings("## !!!\n\n## Same\n\n## Same\n\n## ** **")
    assert [h["slug"] for h in headings[:3]] == ["section", "same", "same-1"]
    assert vr._extract_headings("**Fallback Heading:**\n\n**No**") == [
        {"level": 2, "text": "Fallback Heading", "slug": "fallback-heading"}
    ]

    assert vr._apply_heading_ids("<p>No headings</p>", []) == "<p>No headings</p>"
    mismatched = vr._apply_heading_ids("<h2>Rendered</h2>", [{"level": 3, "text": "Expected", "slug": "expected"}, {"level": 2, "text": "Missing", "slug": "missing"}])
    assert 'id="expected"' in mismatched

    assert vr._inject_images("<p>No h2</p>", ["https://example.test/a.jpg"]) == ("<p>No h2</p>", 0)
    assert vr._inject_images("<h2>A</h2>", []) == ("<h2>A</h2>", 0)
    injected_once, consumed_once = vr._inject_images("<h2>A</h2><h2>B</h2><h2>C</h2><h2>D</h2>", ["https://example.test/one.jpg"])
    assert consumed_once == 1
    assert "one.jpg" in injected_once

    assert vr._category_css(None) == ""
    assert "body.category-product" in vr._category_css("product")
    assert "Category palettes" in vr._category_css("unknown")
    assert vr.json_dumps_str("</script>") == '"<\\/script>"'

    def bad_urlparse(_url):
        raise ValueError("bad url")

    monkeypatch.setattr(vr, "urlparse", bad_urlparse)
    html = generate_visual_report(
        "fallback title",
        "**Fallback Heading:**\n\nBody",
        sources=[{"url": "not a url", "title": "", "image": ""}],
    )
    assert "not a url" in html


def test_visual_report_full_render_with_images_stats_sources_and_category():
    report = """
# Product Research Title

## Quick Guide
Intro with a bare URL https://example.test/bare.

### Product Alpha
Alpha body.

## Details
Details body.

## More Details
More body.

### Product Beta
Beta body.
"""
    sources = [
        {"url": "https://www.example.com/a", "title": "Example A", "image": "https://cdn.example.com/hero.jpg"},
        {"url": "https://example.com/b", "title": "Example B", "image": "https://cdn.example.com/section-one.jpg"},
        {"url": "https://example.com/c", "title": "Example C", "image": "https://cdn.example.com/section-two.jpg"},
        {"url": "https://example.com/d", "title": "Example D", "image": "https://cdn.example.com/spare.jpg"},
        {"url": "https://example.com/e", "title": "Duplicate", "image": "https://cdn.example.com/hero.jpg"},
        {"url": "https://example.com/f", "title": "Hidden", "image": "https://cdn.example.com/hidden.jpg"},
        {"url": "https://example.com/g", "title": "Icon", "image": "https://cdn.example.com/icon.png"},
        {"url": "https://example.com/h", "title": "Logo", "image": "https://cdn.example.com/logo.png"},
        {"url": "https://example.com/i", "title": "Favicon", "image": "https://cdn.example.com/favicon.ico"},
        {"url": "https://example.com/j", "title": "Svg", "image": "https://cdn.example.com/chart.svg"},
        {"url": "http://example.com/k", "title": "Insecure", "image": "http://cdn.example.com/no.jpg"},
    ]

    rendered = generate_visual_report(
        "fallback title",
        report,
        sources=sources,
        stats={"Duration": "12s", "Rounds": 2, "Queries": 3, "URLs": 4, "Model": "local", "Search": "offline"},
        category="product",
        session_id="session</script>",
        hidden_images=["https://cdn.example.com/hidden.jpg"],
    )
    soup = BeautifulSoup(rendered, "html.parser")

    assert soup.title.string == "Product Research Title"
    assert soup.select_one("body.category-product")
    assert soup.select_one(".hero-image")["data-img-url"] == "https://cdn.example.com/hero.jpg"
    assert [img["data-img-url"] for img in soup.select(".section-image")] == ["https://cdn.example.com/section-one.jpg"]
    assert soup.select_one(".quick-links-bar .quick-link").get_text(strip=True) == "Product Alpha"
    assert soup.select_one("meta[property='og:image']")["content"] == "https://cdn.example.com/hero.jpg"
    assert "https://cdn.example.com/spare.jpg" in rendered
    assert "https://cdn.example.com/hidden.jpg" not in rendered
    assert "Show hidden (1)" in rendered
    assert "session<\\/script>" in rendered

    stat_values = [item.get_text(" ", strip=True) for item in soup.select(".stats-bar .stat")]
    assert "12s Duration" in stat_values
    assert "offline Search" in stat_values
    assert soup.select_one(".sources-panel summary").get_text(strip=True) == "Sources (11)"
    assert soup.select_one(".sdomain").get_text(strip=True) == "example.com"
    assert soup.select_one('a[href^="https://example.test/bare"]') is not None
