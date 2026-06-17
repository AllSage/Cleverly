import re
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent.parent


def _frontend_sources() -> tuple[str, str, BeautifulSoup]:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    return html, app_js, BeautifulSoup(html, "html.parser")


def test_visible_feature_launchers_are_wired():
    _, app_js, soup = _frontend_sources()
    ids = {tag["id"] for tag in soup.find_all(id=True)}
    all_feature_js = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "static" / "js").rglob("*.js")
    )

    expected_tools = {
        "tool-memory-btn": "Brain",
        "tool-calendar-btn": "Calendar",
        "tool-compare-btn": "Compare",
        "tool-cookbook-btn": "Cookbook",
        "tool-training-btn": "Training",
        "tool-tutorials-btn": "Tutorials",
        "tool-agent-loops-btn": "Loops",
        "tool-code-workspace-btn": "Code",
        "tool-offline-btn": "Offline",
        "tool-research-btn": "Deep Research",
        "tool-gallery-btn": "Gallery",
        "tool-library-btn": "Library",
        "tool-notes-btn": "Notes",
        "tool-tasks-btn": "Tasks",
        "tool-theme-btn": "Theme",
    }

    for tool_id, label in expected_tools.items():
        tool = soup.find(id=tool_id)
        assert tool is not None, f"{label} launcher is missing from the sidebar"
        assert label in tool.get_text(" ", strip=True)
        assert tool_id in app_js or tool_id in all_feature_js, f"{tool_id} is not referenced by frontend JS"


def test_icon_rail_delegates_to_existing_feature_launchers():
    _, app_js, soup = _frontend_sources()
    ids = {tag["id"] for tag in soup.find_all(id=True)}

    map_match = re.search(r"const _railToolMap = \{(?P<body>.*?)\n\s*\};", app_js, re.S)
    assert map_match is not None, "rail-to-tool map is missing from app.js"

    mappings = dict(re.findall(r"'([^']+)'\s*:\s*'([^']+)'", map_match.group("body")))
    assert mappings, "rail-to-tool map is empty"

    for rail_id, tool_id in mappings.items():
        assert rail_id in ids, f"rail source missing: {rail_id}"
        assert tool_id in ids, f"rail target missing: {tool_id}"


def test_static_feature_modals_have_required_shells():
    _, _, soup = _frontend_sources()

    feature_modals = {
        "cookbook-modal": "cookbook-body",
        "training-lab-modal": "training-lab-body",
        "code-workspace-modal": "code-workspace-body",
        "offline-control-modal": "offline-control-body",
        "setup-wizard-modal": "setup-wizard-body",
        "tutorials-modal": "tutorials-body",
        "agent-loops-modal": "agent-loops-body",
    }

    for modal_id, body_class in feature_modals.items():
        assert soup.find(id=modal_id) is not None, f"{modal_id} is missing"
        assert soup.select_one(f"#{modal_id} .{body_class}") is not None, f"{modal_id} body is missing"
        assert soup.find(id=f"close-{modal_id}") is not None, f"{modal_id} close button is missing"


def test_active_frontend_has_no_old_brand_tokens():
    checked_files = [
        ROOT / "static" / "index.html",
        ROOT / "static" / "login.html",
        ROOT / "static" / "app.js",
        ROOT / "static" / "style.css",
        *sorted((ROOT / "static" / "js").rglob("*.js")),
    ]
    old_brand = re.compile(r"Odysseus|odysseus|(?<![bB])ody-")

    leftovers = []
    for path in checked_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in old_brand.finditer(text):
            leftovers.append(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, match.start()) + 1}:{match.group(0)}")

    assert leftovers == []


def test_frontend_avoids_removed_or_unregistered_feature_api_calls():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    sessions_js = (ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")

    assert "/api/ai/name" not in app_js
    assert "rename-ai-modal" not in app_js
    assert "/api/session/${sid}/restore" not in sessions_js
    assert "/api/session/${s.id}/restore" not in sessions_js
    assert "/api/session/${sid}/unarchive" in sessions_js
    assert "/api/session/${s.id}/unarchive" in sessions_js


def test_frontend_avoids_removed_sidebar_launcher_ids():
    checked = {
        "static/app.js": (ROOT / "static" / "app.js").read_text(encoding="utf-8"),
        "static/js/documentLibrary.js": (ROOT / "static" / "js" / "documentLibrary.js").read_text(encoding="utf-8"),
        "static/js/sidebar-layout.js": (ROOT / "static" / "js" / "sidebar-layout.js").read_text(encoding="utf-8"),
    }
    stale_ids = {
        "rail-admin",
        "rail-agents",
        "tool-admin-btn",
        "tool-agents-btn",
        "tool-archive-btn",
        "tool-doclib-btn",
    }

    leftovers = [
        f"{path}:{stale_id}"
        for path, text in checked.items()
        for stale_id in stale_ids
        if stale_id in text
    ]

    assert leftovers == []
    assert "tool-library-btn" in checked["static/js/documentLibrary.js"]


def test_ui_visibility_controls_target_existing_static_elements():
    html, app_js, soup = _frontend_sources()

    map_match = re.search(r"const UI_VIS_MAP = \{(?P<body>.*?)\n\s*\};", app_js, re.S)
    assert map_match is not None, "UI visibility selector map is missing"

    ui_map = dict(re.findall(r"'([^']+)'\s*:\s*'([^']+)'", map_match.group("body")))
    special_controls = {"show-thinking", "text-emojis"}

    control_keys = {
        tag["data-ui-key"]
        for tag in soup.select("input[data-ui-key]")
    }
    unmapped = sorted(control_keys - set(ui_map) - special_controls)
    assert unmapped == []

    dead_selectors = []
    for key, selector in ui_map.items():
        if not soup.select(selector):
            dead_selectors.append(f"{key}: {selector}")

    assert dead_selectors == []


def test_settings_tabs_and_modal_restore_triggers_are_wired():
    _, _, soup = _frontend_sources()
    modal_manager_js = (ROOT / "static" / "js" / "modalManager.js").read_text(encoding="utf-8")
    ids = {tag["id"] for tag in soup.find_all(id=True)}

    tabs = {tag["data-settings-tab"] for tag in soup.select("[data-settings-tab]")}
    panels = {tag["data-settings-panel"] for tag in soup.select("[data-settings-panel]")}
    assert sorted(tabs - panels) == []
    assert sorted(panels - tabs) == []

    map_match = re.search(r"const _AUTO_WIRE = \{(?P<body>.*?)\n\};", modal_manager_js, re.S)
    assert map_match is not None, "modal auto-wire map is missing"

    trigger_entries = re.findall(
        r"'([^']+)'\s*:\s*\{\s*rail:\s*(null|'[^']+')\s*,\s*sidebar:\s*(null|'[^']+')",
        map_match.group("body"),
    )
    assert trigger_entries, "modal auto-wire map has no parseable trigger entries"

    missing_triggers = []
    for modal_id, rail_id, sidebar_id in trigger_entries:
        for kind, raw_id in (("rail", rail_id), ("sidebar", sidebar_id)):
            if raw_id == "null":
                continue
            trigger_id = raw_id.strip("'")
            if trigger_id not in ids:
                missing_triggers.append(f"{modal_id}:{kind}:{trigger_id}")

    assert missing_triggers == []


def test_ui_control_panel_launchers_use_current_feature_entry_points():
    chat_stream_js = (ROOT / "static" / "js" / "chatStream.js").read_text(encoding="utf-8")

    assert "skills-btn" not in chat_stream_js
    assert "open-settings-btn" not in chat_stream_js
    assert "tool-memory-btn" in chat_stream_js
    assert 'data-memory-tab="' in chat_stream_js
    assert "user-bar-settings" in chat_stream_js
    assert "settings.js" in chat_stream_js


def test_chat_bar_uses_current_controls_for_docs_group_and_privileges():
    _, _, soup = _frontend_sources()
    checked = {
        "static/app.js": (ROOT / "static" / "app.js").read_text(encoding="utf-8"),
        "static/js/init.js": (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8"),
        "static/style.css": (ROOT / "static" / "style.css").read_text(encoding="utf-8"),
    }
    stale_ids = {
        "agent-mode-toggle",
        "overflow-group-btn",
        "tool-bash-btn",
        "tool-doc-btn",
        "tool-image-btn",
    }

    assert soup.find(id="doc-indicator-btn") is not None

    leftovers = [
        f"{path}:{stale_id}"
        for path, text in checked.items()
        for stale_id in stale_ids
        if stale_id in text
    ]
    assert leftovers == []
    assert "bash-toggle-btn" in checked["static/app.js"]
    assert "mode-chat-btn" in checked["static/app.js"]
