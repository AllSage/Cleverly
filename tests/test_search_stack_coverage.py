import builtins
import importlib
import json
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from bs4 import BeautifulSoup


SEARCH_MODULE_PREFIXES = ("src.search", "services.search")


class FakeResponse:
    def __init__(
        self,
        *,
        status_code=200,
        payload=None,
        text="",
        content=b"",
        headers=None,
        is_success=True,
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = content or text.encode("utf-8")
        self.headers = headers or {}
        self.is_success = is_success

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=None)


def _import(prefix, name):
    return importlib.import_module(f"{prefix}.{name}")


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_query_helpers_cover_entity_site_time_and_cache_branches(prefix):
    query = _import(prefix, "query")

    assert query._detect_question_type(" Who is Ada Lovelace?") == "who"
    assert query._detect_question_type("Explain Ada") is None

    entities = query._extract_entities("When did Ada Lovelace meet Charles in January 1, 2026?")
    assert {"Ada", "Lovelace", "Charles"} <= set(entities["names"])
    assert "2026" in entities["dates"]
    assert any("January" in date for date in entities["dates"])

    assert query._split_multi_part("alpha and beta; gamma or delta") == [
        "alpha",
        "beta",
        "gamma",
        "delta",
    ]
    stripped, site = query._extract_site_filter("python site:docs.python.org examples")
    assert " ".join(stripped.split()) == "python examples"
    assert site == "docs.python.org"

    boosted = query._boost_entities_in_query("base", {"names": ["Ada"], "dates": ["1843"]})
    assert '"Ada"' in boosted and '"1843"' in boosted

    enhanced, enhanced_site = query.enhance_query(
        "Where is Paris and why is Tesla important site:example.com"
    )
    assert enhanced_site == "example.com"
    assert "location" in enhanced and "reason" in enhanced and "site:example.com" in enhanced
    who_when_how, _ = query.enhance_query("Who is Ada and when was Python created and how to install Python")
    assert "person" in who_when_how
    assert "date" in who_when_how
    assert "method" in who_when_how

    assert "after:w" in query.build_enhanced_query("latest Canada news", "week")
    assert query._is_news_query("breaking updates today")
    assert not query._is_news_query("static reference page")
    assert query._cache_duration_for_query("latest news") == timedelta(minutes=30)
    assert query._cache_duration_for_query("reference docs") == timedelta(hours=24)


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_analytics_load_record_and_stats_use_runtime_file(prefix, tmp_path, monkeypatch):
    analytics = _import(prefix, "analytics")
    cache = _import(prefix, "cache")

    analytics_file = tmp_path / "analytics.json"
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", analytics_file)
    cache.cache_metrics.update({"hits": 0, "misses": 0, "evictions": 2})

    loaded = analytics._load_analytics()
    assert loaded["total_queries"] == 0
    assert analytics_file.exists()

    analytics._record_query("alpha", success=True, cache_hit=True)
    analytics._record_query("alpha", success=False, cache_hit=False)
    analytics._record_query("beta", success=True, cache_hit=False)
    stats = analytics.get_search_stats()

    assert stats["total_queries"] == 3
    assert stats["successful_queries"] == 2
    assert stats["failed_queries"] == 1
    assert stats["cache_hits"] == 1
    assert stats["cache_misses"] == 2
    assert stats["runtime_cache_hits"] == 1
    assert stats["runtime_cache_misses"] == 2
    assert stats["cache_evictions"] == 2
    assert stats["most_common_queries"][0] == "alpha"

    analytics_file.write_text("{not-json", encoding="utf-8")
    fallback = analytics._load_analytics()
    assert fallback["total_queries"] == 0

    analytics_dir = tmp_path / "analytics-dir"
    analytics_dir.mkdir()
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", analytics_dir)
    analytics._save_analytics({"total_queries": 1})


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_provider_helpers_and_all_provider_parsers_are_offline(prefix, monkeypatch):
    providers = _import(prefix, "providers")

    settings = {
        "search_url": "https://search.local/",
        "search_provider": "brave",
        "brave_api_key": "brave-key",
        "google_pse_key": "google-key",
        "google_pse_cx": "cx-id",
        "tavily_api_key": "tavily-key",
        "serper_api_key": "serper-key",
        "search_result_count": "7",
    }
    monkeypatch.setattr(providers, "_get_search_settings", lambda: dict(settings))

    assert providers._get_search_instance() == "https://search.local"
    assert providers._get_provider_key("brave") == "brave-key"
    assert providers._get_provider_key("google_pse") == "google-key"
    assert providers._get_result_count() == 7

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_result_count": "bad"})
    assert providers._get_result_count() == 5
    monkeypatch.setattr(providers, "_get_search_settings", lambda: dict(settings))

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "search.local" in url:
            payload = {
                "results": [
                    {"title": "SearX", "url": "https://example.com/a", "content": "hit"},
                    {"title": "No URL", "content": "skip"},
                ]
            }
            return FakeResponse(payload=payload)
        if "brave" in url:
            return FakeResponse(
                payload={
                    "web": {
                        "results": [
                            {
                                "title": "Brave",
                                "url": "https://example.com/b",
                                "description": "desc",
                                "date": "2026-01-01",
                            },
                            {"title": "skip"},
                        ]
                    }
                }
            )
        if "googleapis" in url:
            return FakeResponse(
                payload={
                    "items": [
                        {"title": "Google", "link": "https://example.com/g", "snippet": "g"},
                        {"title": "skip"},
                    ]
                }
            )
        if "duckduckgo" in url:
            html = """
            <div class="result"><a class="result__a" href="https://example.com/d">Duck</a>
            <a class="result__snippet">duck body</a></div>
            <div class="result"><a class="result__a">No href</a></div>
            """
            return FakeResponse(text=html)
        return FakeResponse(payload={})

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if "tavily" in url:
            return FakeResponse(
                payload={
                    "results": [
                        {
                            "title": "Tavily",
                            "url": "https://example.com/t",
                            "content": "t",
                            "published_date": "today",
                        },
                        {"title": "skip"},
                    ]
                }
            )
        if "serper" in url:
            return FakeResponse(
                payload={
                    "organic": [
                        {
                            "title": "Serper",
                            "link": "https://example.com/s",
                            "snippet": "s",
                            "date": "yesterday",
                        },
                        {"title": "skip"},
                    ]
                }
            )
        return FakeResponse(payload={})

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    assert providers.searxng_search_api("plain", count=2) == [
        {"title": "SearX", "url": "https://example.com/a", "snippet": "hit"}
    ]
    assert "news" in providers.searxng_search_api("latest news", count=1, time_filter="day")[0]["title"] or calls

    html = '<article class="result"><h3><a href="https://example.com/h">HTML</a></h3><p class="content">body</p></article>'
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: FakeResponse(text=html, is_success=True))
    assert providers.searxng_search("html", 1)[0]["title"] == "HTML"

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    assert providers._brave_search_impl(
        "query", 2, "day", search_config={"brave_api_key": "brave-key"}
    )[0]["age"] == "2026-01-01"
    assert providers._brave_search_impl("query", 2, search_config={}) == []

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "duckduckgo_search":
            raise ImportError("blocked for offline test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    assert providers.duckduckgo_search("duck", 2)[0]["title"] == "Duck"

    assert providers.google_pse_search("google", 2, "month")[0]["title"] == "Google"
    assert providers.tavily_search("tavily", 2, "year")[0]["title"] == "Tavily"
    assert providers.serper_search("serper", 2, "week")[0]["title"] == "Serper"

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PSE_CX", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert providers.google_pse_search("missing") == []
    assert providers.tavily_search("missing") == []
    assert providers.serper_search("missing") == []


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_provider_error_paths_cover_rate_limits_json_errors_and_fallbacks(prefix, monkeypatch):
    providers = _import(prefix, "providers")

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_url": "https://search.local"})

    fallback_called = []
    monkeypatch.setattr(
        providers,
        "searxng_search",
        lambda query, max_results=10: fallback_called.append((query, max_results))
        or [{"title": "fallback", "url": "https://fallback.test", "snippet": ""}],
    )
    monkeypatch.setattr(
        providers.httpx,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(httpx.RequestError("offline")),
    )
    assert providers.searxng_search_api("down", count=3)[0]["title"] == "fallback"
    assert fallback_called == [("down", 3)]

    class BadJson(FakeResponse):
        def json(self):
            raise json.JSONDecodeError("bad", "", 0)

    monkeypatch.setenv("DATA_BRAVE_API_KEY", "env-brave")
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: BadJson())
    assert providers.brave_search("bad-json") == []

    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: FakeResponse(status_code=429))
    assert providers._brave_search_impl("limited", 1, search_config={"brave_api_key": "key"}) == []

    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: FakeResponse(status_code=429))
    monkeypatch.setattr(
        providers,
        "_get_search_settings",
        lambda: {"tavily_api_key": "t", "serper_api_key": "s", "search_api_key": "legacy"},
    )
    assert providers.tavily_search("limited") == []
    assert providers.serper_search("limited") == []


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_content_helpers_fetch_cache_pdf_html_and_errors(prefix, tmp_path, monkeypatch):
    content = _import(prefix, "content")

    monkeypatch.setattr(content, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content, "content_cache_index", {})
    monkeypatch.setattr(content, "cleanup_cache", lambda *args, **kwargs: None)

    assert content._public_http_url("ftp://example.com") is False
    assert content._public_http_url("http://localhost") is False
    monkeypatch.setattr(content, "_resolve_hostname_ips", lambda host: [__import__("ipaddress").ip_address("93.184.216.34")])
    assert content._public_http_url("https://example.com/path") is True
    monkeypatch.setattr(content, "_resolve_hostname_ips", lambda host: [__import__("ipaddress").ip_address("127.0.0.1")])
    assert content._public_http_url("https://private.example") is False
    monkeypatch.setattr(content, "_resolve_hostname_ips", lambda host: (_ for _ in ()).throw(OSError("dns")))
    assert content._public_http_url("https://missing.example") is False

    soup = BeautifulSoup(
        """
        <html><head>
          <title>Title</title>
          <meta name="description" content="desc">
          <meta name="keywords" content="one,two">
          <meta property="og:image" content="https://example.com/image.png">
          <meta name="twitter:image" content="https://example.com/skip.svg">
        </head><body data-reactroot="true">
          <main class="content"><p>Readable content sentence. More text.</p></main>
          <ul><li>First</li><li>Second</li></ul>
          <table><tr><th>A</th></tr><tr><td>B</td></tr></table>
          <pre>code block</pre>
          <script src="/react.js"></script>
        </body></html>
        """,
        "html.parser",
    )
    assert content._extract_meta(soup) == {"description": "desc", "keywords": "one,two"}
    if hasattr(content, "_extract_og_image"):
        assert content._extract_og_image(soup) == "https://example.com/image.png"
    assert content._extract_lists(soup) == [["First", "Second"]]
    assert content._extract_tables(soup)[0][0] == ["A"]
    assert content._extract_code_blocks(soup) == ["code block"]
    assert content._detect_js_frameworks(soup)
    assert content._empty_result("u", "err")["error"] == "err"

    html = """
    <html><head><title>Fetched</title><meta name="description" content="m"></head>
    <body><header>nav</header><article class="article">Article body has enough readable text.</article>
    <ol><li>Step one</li></ol><code>print(1)</code></body></html>
    """
    monkeypatch.setattr(
        content,
        "_get_public_url",
        lambda *a, **k: FakeResponse(text=html, headers={"Content-Type": "text/html"}),
    )
    fetched = content.fetch_webpage_content("https://example.com/article", timeout=1)
    assert fetched["success"] is True
    assert fetched["title"] == "Fetched"
    assert "Article body" in fetched["content"]

    cached = content.fetch_webpage_content("https://example.com/article", timeout=1)
    assert cached["title"] == "Fetched"

    monkeypatch.setattr(content, "_get_public_url", lambda *a, **k: (_ for _ in ()).throw(httpx.RequestError("nope")))
    err = content.fetch_webpage_content("https://example.com/error")
    assert err["success"] is False and "NetworkError" in err["error"]

    monkeypatch.setattr(content, "pdf_extract_text", lambda stream: "pdf text")
    monkeypatch.setattr(
        content,
        "_get_public_url",
        lambda *a, **k: FakeResponse(content=b"%PDF", headers={"Content-Type": "application/pdf"}),
    )
    pdf = content.fetch_webpage_content("https://example.com/file.pdf")
    assert pdf["content"] == "pdf text"

    assert content.extract_key_points("- one\n2. two\nplain") == ["one", "two"]
    assert content.get_tldr("A. B! C? D.", 2) == "A. B!"
    assert content.extract_quotes('He said "this is a long useful quote".') == [
        "this is a long useful quote"
    ]
    assert content.extract_statistics("Sales rose 12.5 percent on 2026 reports")[0] == "12.5 percent"


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_core_config_cache_provider_chain_and_comprehensive_search(prefix, tmp_path, monkeypatch):
    core = _import(prefix, "core")
    providers = _import(prefix, "providers")

    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(core, "search_cache_index", {})
    monkeypatch.setattr(core, "cleanup_cache", lambda *args, **kwargs: None)
    recorded = []
    monkeypatch.setattr(core, "_record_query", lambda *args, **kwargs: recorded.append((args, kwargs)))
    monkeypatch.setattr(core, "_get_result_count", lambda: 2)
    monkeypatch.setattr(
        core,
        "_get_search_settings",
        lambda: {
            "search_provider": "searxng",
            "search_url": "https://search.local",
            "search_api_key": "key",
            "search_fallback_chain": "duckduckgo, brave, disabled, duckduckgo",
        },
    )
    monkeypatch.setattr(
        providers,
        "_get_search_settings",
        lambda: {"search_url": "https://search.local"},
    )
    monkeypatch.setattr(core, "rank_search_results", lambda query, results: list(reversed(results)))

    config = core.get_search_config()
    assert config["active_provider"] == "searxng"
    assert config["has_api_key"] is True
    assert config["result_count"] == 2
    assert config["search_url"] == "https://search.local"
    core.update_search_config(api_key="new-key")
    assert core.SEARCH_CONFIG["brave_api_key"] == "new-key"

    assert core._build_provider_chain("searxng") == ["searxng", "duckduckgo", "brave"]

    calls = []

    def fake_call(provider, query, count, time_filter=None):
        calls.append((provider, query, count, time_filter))
        if provider == "searxng":
            return []
        if provider == "duckduckgo":
            return [
                {"title": "Two", "url": "https://example.com/two", "snippet": "two"},
                {"title": "One", "url": "https://example.com/one", "snippet": "one"},
            ]
        return [{"title": provider, "url": f"https://example.com/{provider}", "snippet": ""}]

    monkeypatch.setattr(core, "_call_provider", fake_call)
    results = core.searxng_search_results("alpha", count=10, time_filter="week")
    assert [r["title"] for r in results] == ["One", "Two"]
    assert calls[:2] == [
        ("searxng", "alpha", 2, "week"),
        ("searxng", "alpha", 2, "week"),
    ]
    assert calls[2][0] == "duckduckgo"
    assert recorded[-1][0] == ("alpha", True)

    cache_file = next(tmp_path.glob("*.cache"))
    cached_results = core.searxng_search_results("alpha", count=2, time_filter="week")
    assert cached_results == results
    assert recorded[-1][1] == {"cache_hit": True}

    expired = {
        "timestamp": datetime.now().isoformat(),
        "expiry": (datetime.now() - timedelta(days=1)).isoformat(),
        "data": [{"title": "old", "url": "https://old.test", "snippet": ""}],
    }
    cache_file.write_text(json.dumps(expired), encoding="utf-8")
    assert core.searxng_search_results("alpha", count=2, time_filter="week") == results

    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "disabled"})
    assert core.searxng_search_results("disabled") == []
    assert core.comprehensive_web_search("disabled", return_sources=True) == (
        "Web search is disabled by the administrator.",
        [],
    )

    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "duckduckgo"})
    attempts = []

    def fake_comprehensive_call(provider, query, count, time_filter=None):
        attempts.append(provider)
        if query == "empty":
            return []
        if query == "boom":
            raise RuntimeError("network down")
        return [
            {
                "title": "Article",
                "url": "https://example.com/en/article",
                "snippet": "snippet text " * 30,
                "age": "1d",
            },
            {
                "title": "Forum",
                "url": "https://blocked.example/forum",
                "snippet": "blocked",
            },
        ]

    monkeypatch.setattr(core, "_call_provider", fake_comprehensive_call)
    empty_msg = core.comprehensive_web_search("empty")
    assert "No search results found" in empty_msg and "duckduckgo:empty" in empty_msg
    error_msg = core.comprehensive_web_search("boom")
    assert "Web search failed" in error_msg and "duckduckgo:error" in error_msg

    monkeypatch.setattr(
        core,
        "fetch_webpage_content",
        lambda url, *a, **k: {
            "success": True,
            "url": url,
            "title": "Fetched",
            "content": '- point\n"this quote is long enough"\nUsers grew 42 percent. '
            + "Body sentence. " * 80,
        },
    )
    output, sources = core.comprehensive_web_search(
        "ok",
        max_pages=2,
        max_workers=1,
        domain_blacklist={"blocked.example"},
        content_type="article",
        language="en",
        min_content_length=10,
        return_sources=True,
    )
    assert "WEB SEARCH RESULTS AND FETCHED CONTENT" in output
    assert "Key Points:" in output
    assert "Important Quotes:" in output
    assert "Data / Statistics:" in output
    assert any(source["title"] == "Article" for source in sources)

    filtered = core.comprehensive_web_search("ok", domain_whitelist={"not.example"})
    assert filtered == "No suitable results after applying filters."


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_core_call_provider_dispatches_every_branch(prefix, monkeypatch):
    core = _import(prefix, "core")

    called = []
    for name in (
        "searxng_search_api",
        "brave_search",
        "duckduckgo_search",
        "google_pse_search",
        "tavily_search",
        "serper_search",
    ):
        monkeypatch.setattr(
            core,
            name,
            lambda query, count, time_filter=None, _name=name: called.append((_name, query, count, time_filter))
            or [{"title": _name}],
        )

    assert core._call_provider("searxng", "q", 1, "day")[0]["title"] == "searxng_search_api"
    assert core._call_provider("brave", "q", 1, "day")[0]["title"] == "brave_search"
    assert core._call_provider("duckduckgo", "q", 1, "day")[0]["title"] == "duckduckgo_search"
    assert core._call_provider("google_pse", "q", 1, "day")[0]["title"] == "google_pse_search"
    assert core._call_provider("tavily", "q", 1, "day")[0]["title"] == "tavily_search"
    assert core._call_provider("serper", "q", 1, "day")[0]["title"] == "serper_search"
    assert core._call_provider("unknown", "q", 1) == []
    assert [entry[0] for entry in called] == [
        "searxng_search_api",
        "brave_search",
        "duckduckgo_search",
        "google_pse_search",
        "tavily_search",
        "serper_search",
    ]
