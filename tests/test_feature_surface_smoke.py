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

