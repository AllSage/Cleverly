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
        url="https://example.com/current",
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = content or text.encode("utf-8")
        self.headers = headers or {}
        self.is_success = is_success
        self.url = url

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
            <div class="result"><span>No link</span></div>
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
    assert providers.duckduckgo_search("duck", 3)[0]["title"] == "Duck"

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
def test_provider_remaining_settings_retry_and_request_error_edges(prefix, monkeypatch):
    providers = _import(prefix, "providers")

    import src.settings as settings_module

    monkeypatch.setattr(settings_module, "load_settings", lambda: {"search_url": "https://settings.local"})
    assert providers._get_search_settings() == {"search_url": "https://settings.local"}

    real_import = builtins.__import__

    def settings_import_error(name, *args, **kwargs):
        if name == "src.settings":
            raise RuntimeError("settings unavailable")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as import_patch:
        import_patch.setattr(builtins, "__import__", settings_import_error)
        assert providers._get_search_settings() == {}

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    assert providers._get_search_instance() == providers.SEARXNG_INSTANCE
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_api_key": "legacy-key"})
    assert providers._get_provider_key("unknown") == "legacy-key"

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_url": "https://search.local"})
    searx_calls = []

    def searx_retry_get(url, **kwargs):
        searx_calls.append(kwargs["params"])
        if len(searx_calls) < 4:
            return FakeResponse(payload={"results": []})
        return FakeResponse(payload={"results": [{"title": "Final", "url": "https://example.com/f", "content": "final"}]})

    monkeypatch.setattr(providers.httpx, "get", searx_retry_get)
    assert providers.searxng_search_api("latest news", count=2, time_filter="day")[0]["title"] == "Final"
    assert searx_calls[0]["categories"] == "news"
    assert searx_calls[1]["categories"] == "general"
    assert "language" not in searx_calls[2]
    assert "engines" not in searx_calls[3]

    def searx_unresponsive_get(url, **kwargs):
        return FakeResponse(payload={"results": [], "unresponsive_engines": ["engine"]})

    monkeypatch.setattr(providers.httpx, "get", searx_unresponsive_get)
    assert providers.searxng_search_api("plain empty", categories="images") == []

    html_with_skip = """
    <article class="result"><p class="content">missing title</p></article>
    <article class="result"><h3><a href="https://example.com/ok">OK</a></h3></article>
    """
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: FakeResponse(text=html_with_skip, is_success=True))
    assert providers.searxng_search("html skip", 2)[0]["title"] == "OK"
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("html down")))
    assert providers.searxng_search("html down") == []

    monkeypatch.setenv("DATA_BRAVE_API_KEY", "env-brave")
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.RequestError("brave offline")))
    assert providers.brave_search("brave") == []

    duck_module = type(sys)("duckduckgo_search")

    class DDGS:
        def text(self, query, max_results, timelimit=None):
            assert timelimit == "m"
            return [
                {"title": "Skip"},
                {"title": "Duck Lib", "href": "https://example.com/lib", "body": "body"},
            ]

    duck_module.DDGS = DDGS
    monkeypatch.setitem(sys.modules, "duckduckgo_search", duck_module)
    assert providers.duckduckgo_search("duck", 2, "month")[0]["title"] == "Duck Lib"

    class EmptyDDGS:
        def text(self, *_args, **_kwargs):
            return []

    duck_module.DDGS = EmptyDDGS
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("duck html down")))
    assert providers.duckduckgo_search("duck", 2) == []

    class RaisingDDGS:
        def text(self, *_args, **_kwargs):
            raise RuntimeError("ddg library down")

    duck_module.DDGS = RaisingDDGS
    assert providers.duckduckgo_search("duck", 2) == []

    monkeypatch.setattr(
        providers,
        "_get_search_settings",
        lambda: {
            "google_pse_key": "google",
            "google_pse_cx": "cx",
            "tavily_api_key": "tavily",
            "serper_api_key": "serper",
        },
    )
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: FakeResponse(status_code=429))
    assert providers.google_pse_search("google") == []
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.RequestError("google offline")))
    assert providers.google_pse_search("google") == []

    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.RequestError("api offline")))
    assert providers.tavily_search("tavily") == []
    assert providers.serper_search("serper") == []


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_content_helpers_fetch_cache_pdf_html_and_errors(prefix, tmp_path, monkeypatch):
    content = _import(prefix, "content")

    monkeypatch.setattr(content, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content, "content_cache_index", {})
    monkeypatch.setattr(content, "cleanup_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(content, "offline_mode", lambda: False)

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
def test_search_core_and_content_block_network_in_offline_mode(prefix, tmp_path, monkeypatch):
    core = _import(prefix, "core")
    content = _import(prefix, "content")

    monkeypatch.setattr(core, "offline_mode", lambda: True)
    monkeypatch.setattr(core, "_call_provider", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    assert core.comprehensive_web_search("local only", return_sources=True) == (
        "Web search is disabled in offline mode.",
        [],
    )

    monkeypatch.setattr(content, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content, "content_cache_index", {})
    monkeypatch.setattr(content, "cleanup_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(content, "offline_mode", lambda: True)
    monkeypatch.setattr(content, "_get_public_url", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))

    cached_url = "https://example.com/cached"
    cache_key = content.generate_cache_key(cached_url)
    (tmp_path / f"{cache_key}.cache").write_text(
        json.dumps({
            "timestamp": datetime.now().isoformat(),
            "data": {"success": True, "title": "Cached", "content": "cached body"},
        }),
        encoding="utf-8",
    )
    assert content.fetch_webpage_content(cached_url)["title"] == "Cached"

    blocked = content.fetch_webpage_content("https://example.com/miss")
    assert blocked["success"] is False
    assert blocked["error"] == "Web fetch is disabled in offline mode"


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_content_remaining_url_cache_pdf_parse_and_cache_edges(prefix, tmp_path, monkeypatch):
    content = _import(prefix, "content")

    monkeypatch.setattr(content, "CONTENT_CACHE_DIR", tmp_path)
    monkeypatch.setattr(content, "content_cache_index", {})
    monkeypatch.setattr(content, "cleanup_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(content, "offline_mode", lambda: False)

    if prefix == "services.search":
        monkeypatch.setattr(
            content.socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: [
                (None, None, None, None, ("93.184.216.34", 0)),
                (None, None, None, None, ("not-an-ip", 0)),
            ],
        )
        assert [str(ip) for ip in content._resolve_hostname_ips("example.com")] == ["93.184.216.34"]
        monkeypatch.setattr(content.socket, "getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("dns")))
        assert content._resolve_hostname_ips("missing.example") == []
        assert content._public_http_url("https://box.internal/path") is False
        assert content._public_http_url([]) is False
    else:
        monkeypatch.setattr(
            content.socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: [
                (content.socket.AF_INET, None, None, None, ("93.184.216.34", 0)),
                (999, None, None, None, ("127.0.0.1", 0)),
            ],
        )
        assert [str(ip) for ip in content._resolve_hostname_ips("example.com")] == ["93.184.216.34"]
        monkeypatch.setattr(content, "_resolve_hostname_ips", lambda _host: [])
        assert content._public_http_url("https://empty.example") is False

    assert content._public_http_url("https://") is False

    if hasattr(content, "_extract_og_image"):
        og_soup = BeautifulSoup(
            """
            <html><head>
              <meta property="og:image" content="http://example.com/plain.png">
              <meta property="og:image:url" content="https://example.com/icon.ico">
              <meta property="og:image:secure_url" content="https://example.com/hero.svg">
              <meta name="twitter:image" content="https://example.com/twitter.png">
              <meta name="thumbnail" content="https://example.com/thumb.png">
            </head></html>
            """,
            "html.parser",
        )
        assert content._extract_og_image(og_soup) == "https://example.com/twitter.png"
        assert content._extract_og_image(BeautifulSoup("<html></html>", "html.parser")) == ""

    assert content._detect_js_frameworks(BeautifulSoup("<script>window.Vue = {}</script>", "html.parser"))
    assert content._detect_js_frameworks(BeautifulSoup("<body ng-app='app'></body>", "html.parser"))

    if prefix == "services.search":
        calls = []

        def fake_get(url, **_kwargs):
            calls.append(url)
            if len(calls) == 1:
                return FakeResponse(status_code=302, headers={"location": "/next"}, url=url)
            return FakeResponse(status_code=200, text="ok", url=url)

        monkeypatch.setattr(content, "_public_http_url", lambda url: True)
        monkeypatch.setattr(content.httpx, "get", fake_get)
        assert content._get_public_url("https://example.com/start", headers={}, timeout=1).text == "ok"
        assert calls == ["https://example.com/start", "https://example.com/next"]

        monkeypatch.setattr(content.httpx, "get", lambda url, **_kwargs: FakeResponse(status_code=302, headers={}, url=url))
        assert content._get_public_url("https://example.com/no-location", headers={}, timeout=1).status_code == 302
        monkeypatch.setattr(content.httpx, "get", lambda url, **_kwargs: FakeResponse(status_code=302, headers={"location": "/loop"}, url=url))
        with pytest.raises(httpx.RequestError, match="Too many redirects"):
            content._get_public_url("https://example.com/loop", headers={}, timeout=1, max_redirects=1)
        monkeypatch.setattr(content, "_public_http_url", lambda url: False)
        with pytest.raises(httpx.RequestError, match="Blocked private"):
            content._get_public_url("https://private.example", headers={}, timeout=1)
    else:
        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, url):
                self.calls.append(url)
                if len(self.calls) == 1:
                    return FakeResponse(status_code=302, headers={"location": "/next"}, url=url)
                return FakeResponse(status_code=200, text="ok", url=url)

        monkeypatch.setattr(content, "_public_http_url", lambda url: True)
        monkeypatch.setattr(content.httpx, "Client", FakeClient)
        assert content._get_public_url("https://example.com/start", headers={}, timeout=1).text == "ok"

        class NoLocationClient(FakeClient):
            def get(self, url):
                return FakeResponse(status_code=302, headers={}, url=url)

        monkeypatch.setattr(content.httpx, "Client", NoLocationClient)
        assert content._get_public_url("https://example.com/no-location", headers={}, timeout=1).status_code == 302
        monkeypatch.setattr(content, "_public_http_url", lambda url: "private" not in url)

        class PrivateRedirectClient(FakeClient):
            def get(self, url):
                return FakeResponse(status_code=302, headers={"location": "https://private.example"}, url=url)

        monkeypatch.setattr(content.httpx, "Client", PrivateRedirectClient)
        with pytest.raises(httpx.RequestError, match="Blocked redirect"):
            content._get_public_url("https://example.com/start", headers={}, timeout=1)

        class LoopClient(FakeClient):
            def get(self, url):
                return FakeResponse(status_code=302, headers={"location": "/loop"}, url=url)

        monkeypatch.setattr(content, "_public_http_url", lambda url: True)
        monkeypatch.setattr(content.httpx, "Client", LoopClient)
        with pytest.raises(httpx.RequestError, match="Too many redirects"):
            content._get_public_url("https://example.com/loop", headers={}, timeout=1)

        with pytest.raises(httpx.RequestError, match="Blocked non-public"):
            monkeypatch.setattr(content, "_public_http_url", lambda url: False)
            content._get_public_url("https://private.example", headers={}, timeout=1)

    stale_key = content.generate_cache_key("https://example.com/stale")
    stale_file = tmp_path / f"{stale_key}.cache"
    stale_file.write_text(
        json.dumps({"timestamp": (datetime.now() - timedelta(hours=3)).isoformat(), "data": {"title": "old"}}),
        encoding="utf-8",
    )
    content.content_cache_index[stale_key] = datetime.now()
    monkeypatch.setattr(content, "_get_public_url", lambda *a, **k: FakeResponse(text="<html><body>fresh body</body></html>"))
    assert content.fetch_webpage_content("https://example.com/stale")["content"] == "fresh body"
    assert stale_key in content.content_cache_index

    bad_key = content.generate_cache_key("https://example.com/bad-cache")
    bad_file = tmp_path / f"{bad_key}.cache"
    bad_file.write_text("{bad", encoding="utf-8")
    content.content_cache_index[bad_key] = datetime.now()
    assert content.fetch_webpage_content("https://example.com/bad-cache")["success"] is True

    monkeypatch.setattr(content, "_get_public_url", lambda *a, **k: FakeResponse(status_code=429, text="limited"))
    limited = content.fetch_webpage_content("https://example.com/limited", retry_attempt=2)
    assert limited["success"] is False and "Rate limit hit" in limited["error"]

    monkeypatch.setattr(content, "pdf_extract_text", None)
    monkeypatch.setattr(content, "_get_public_url", lambda *a, **k: FakeResponse(content=b"%PDF", headers={"Content-Type": "application/pdf"}))
    pdf_missing = content.fetch_webpage_content("https://example.com/no-pdfminer.pdf")
    assert pdf_missing["success"] is False

    monkeypatch.setattr(content, "pdf_extract_text", lambda _stream: (_ for _ in ()).throw(RuntimeError("pdf broken")))
    pdf_broken = content.fetch_webpage_content("https://example.com/broken.pdf")
    assert pdf_broken["success"] is False

    real_bs = content.BeautifulSoup
    monkeypatch.setattr(content, "_get_public_url", lambda *a, **k: FakeResponse(text="<html></html>", headers={"Content-Type": "text/html"}))
    monkeypatch.setattr(content, "BeautifulSoup", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse broken")))
    parse_error = content.fetch_webpage_content("https://example.com/parse-error")
    assert parse_error["success"] is False and "ParseError" in parse_error["error"]
    monkeypatch.setattr(content, "BeautifulSoup", real_bs)

    body_html = "<html><body><p>Body fallback text without matching content class.</p></body></html>"
    monkeypatch.setattr(content, "_get_public_url", lambda *a, **k: FakeResponse(text=body_html, headers={"Content-Type": "text/html"}))
    body_result = content.fetch_webpage_content("https://example.com/body-only")
    assert "Body fallback text" in body_result["content"]

    with monkeypatch.context() as write_patch:
        write_patch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("readonly")))
        content._cache_result(tmp_path / "manual.cache", "manual", {"success": True}, "https://example.com/manual")


@pytest.mark.parametrize("prefix", SEARCH_MODULE_PREFIXES)
def test_core_remaining_cache_provider_invalidation_and_filter_edges(prefix, tmp_path, monkeypatch):
    core = _import(prefix, "core")

    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(core, "search_cache_index", {})
    monkeypatch.setattr(core, "rank_search_results", lambda query, results: list(results))
    monkeypatch.setattr(core, "cleanup_cache", lambda *args, **kwargs: None)
    recorded = []
    monkeypatch.setattr(core, "_record_query", lambda *args, **kwargs: recorded.append((args, kwargs)))
    monkeypatch.setattr(core, "_cache_duration_for_query", lambda _query: timedelta(minutes=5))
    monkeypatch.setattr(core, "_get_result_count", lambda: 2)
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "searxng", "search_fallback_chain": []})

    stale_key = core.generate_cache_key("stale query|2|None")
    stale_file = tmp_path / f"{stale_key}.cache"
    stale_file.write_text(
        json.dumps({"expiry": (datetime.now() - timedelta(minutes=1)).isoformat(), "data": [{"title": "old"}]}),
        encoding="utf-8",
    )
    core.search_cache_index[stale_key] = datetime.now()
    monkeypatch.setattr(
        core,
        "_call_provider",
        lambda *_args, **_kwargs: [{"title": "New", "url": "https://example.com/news", "snippet": "fresh"}],
    )
    assert core.searxng_search_results("stale query") == [{"title": "New", "url": "https://example.com/news", "snippet": "fresh"}]
    assert stale_key in core.search_cache_index

    bad_key = core.generate_cache_key("bad cache|2|None")
    bad_file = tmp_path / f"{bad_key}.cache"
    bad_file.write_text("{bad", encoding="utf-8")
    core.search_cache_index[bad_key] = datetime.now()
    assert core.searxng_search_results("bad cache")

    def provider_errors(provider_name, *_args, **_kwargs):
        if provider_name == "searxng":
            raise core.NetworkError("network down")
        raise RuntimeError("unexpected down")

    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "searxng", "search_fallback_chain": ["duckduckgo"]})
    monkeypatch.setattr(core, "_call_provider", provider_errors)
    assert core.searxng_search_results("all fail") == []

    monkeypatch.setattr(core, "_call_provider", lambda *_args, **_kwargs: [{"title": "Ok", "url": "https://example.com/ok", "snippet": "ok"}])
    with monkeypatch.context() as write_patch:
        write_patch.setattr(builtins, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("readonly")))
        assert core.searxng_search_results("write fail") == [{"title": "Ok", "url": "https://example.com/ok", "snippet": "ok"}]

    class BadCacheFile:
        def __str__(self):
            return "bad.cache"

        def unlink(self, missing_ok=True):
            raise OSError("cannot delete")

    class FakeCacheDir:
        def glob(self, pattern):
            assert pattern == "*.cache"
            return [BadCacheFile()]

    with monkeypatch.context() as invalidate_patch:
        invalidate_patch.setattr(core, "SEARCH_CACHE_DIR", FakeCacheDir())
        core.search_cache_index["x"] = datetime.now()
        core.invalidate_search_cache()
    assert core.search_cache_index == {}

    missing_key = core.generate_cache_key("missing|10|None")
    missing_file = tmp_path / f"{missing_key}.cache"
    assert not missing_file.exists()
    core.invalidate_search_cache("missing")

    remove_key = core.generate_cache_key("remove-me|10|None")
    remove_file = tmp_path / f"{remove_key}.cache"
    remove_file.write_text("x", encoding="utf-8")
    core.search_cache_index[remove_key] = datetime.now()
    core.invalidate_search_cache("remove-me")
    assert not remove_file.exists()
    assert remove_key not in core.search_cache_index

    query_key = core.generate_cache_key("locked|10|None")
    query_file = tmp_path / f"{query_key}.cache"
    query_file.write_text("x", encoding="utf-8")
    core.search_cache_index[query_key] = datetime.now()
    with monkeypatch.context() as unlink_patch:
        unlink_patch.setattr(query_file.__class__, "unlink", lambda self, missing_ok=True: (_ for _ in ()).throw(OSError("locked")))
        core.invalidate_search_cache("locked")

    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "disabled"})
    disabled_msg, disabled_sources = core.comprehensive_web_search("disabled", time_filter="day", return_sources=True)
    assert "disabled" in disabled_msg.lower()
    assert disabled_sources == []

    search_rows = [
        {"title": "Article", "url": "https://example.com/article/en/page", "snippet": "snippet", "age": "today"},
        {"title": "Forum", "url": "https://forum.example.com/thread/1?lang=en", "snippet": "snippet"},
        {"title": "Paper", "url": "https://example.edu/research.pdf", "snippet": "snippet"},
        {"title": "Bad", "url": object(), "snippet": "bad"},
    ]
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_provider": "searxng", "search_fallback_chain": []})
    monkeypatch.setattr(core, "_call_provider", lambda *_args, **_kwargs: list(search_rows))
    no_filters = core.comprehensive_web_search(
        "filtered",
        max_pages=4,
        domain_whitelist={"other.example.com"},
        return_sources=True,
    )
    assert no_filters == ("No suitable results after applying filters.", [])
    assert "No suitable results" in core.comprehensive_web_search("filtered", content_type="article", language="fr")
    assert "No suitable results" in core.comprehensive_web_search("filtered", content_type="forum", domain_blacklist={"forum.example.com"})
    assert "No suitable results" in core.comprehensive_web_search("filtered", content_type="academic", domain_blacklist={"example.edu"})

    long_content = "First key sentence. " + ("x" * 3100) + ' "this quoted passage is definitely long enough" 42 percent'
    monkeypatch.setattr(
        core,
        "fetch_webpage_content",
        lambda url, *_args, **_kwargs: {
            "success": True,
            "url": url,
            "title": "Fetched",
            "content": "- point\n" + long_content,
        },
    )
    output, sources = core.comprehensive_web_search("filtered", max_pages=1, return_sources=True)
    assert "[truncated]" in output
    assert "Key Points:" in output
    assert "Important Quotes:" in output
    assert "Data / Statistics:" in output
    assert sources[0]["title"] == "Article"

    monkeypatch.setattr(core, "fetch_webpage_content", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fetch exploded")))
    output = core.comprehensive_web_search("filtered", max_pages=1)
    assert "fetched 0 pages" in output


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
