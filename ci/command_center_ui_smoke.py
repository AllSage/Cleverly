"""Command Center UI smoke checks for Cleverly.

The static checks are dependency-free and always run. The live browser checks
run only when a Chrome/Edge executable and UI smoke credentials are available,
or when --require-live is passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "dist" / "command-center-ui-smoke.json"
STATIC_IDS = {
    "chat-container",
    "welcome-screen",
    "command-center",
    "command-center-input",
    "command-center-run",
    "command-center-voice",
    "command-route-preview",
    "cc-command-readiness-deck",
    "cc-target-health",
    "cc-target-command-list",
    "cc-targets-summary",
    "cc-activity-list",
    "cc-alert-list",
}
STATIC_TEXT = (
    "Cleverly Command Center",
    "Command Targets",
    "Activity",
    "Alerts",
    "Toolchain",
    "Workflows",
)
STATIC_JS_NEEDLES = {
    "static/app.js": (
        "commandCenter.js?v=20260626-operator-console",
        "commandPalette.js?v=20260626-operator-console",
    ),
    "static/js/commandCenter.js": (
        "const COMMAND_CENTER_VERSION = '20260626-operator-console';",
        "function renderCommandReadiness(snapshot)",
        "function renderTargetCommands(snapshot = _lastSnapshot || {})",
        "renderCommandReadiness(_lastSnapshot);",
        "renderTargetCommands(_lastSnapshot);",
        "backendRouteProofRows(snapshot)",
        "backendExperiencePlanRows(snapshot)",
        "operatorCommands.backendRouteText(value, { source: 'dashboard-preview'",
        "const galleryPlan = readData(source, 'operatorGalleryPlan') || {};",
        "const galleryOk = source.operatorGalleryPlan?.ok === true;",
    ),
    "static/js/commandPalette.js": (
        "operatorCommands.backendRouteText(value, { source: 'palette-preview'",
        "await operatorCommands.routeText(value, { source: 'palette' });",
    ),
    "static/js/voiceCommand.js": (
        "source: 'voice-command'",
        "privacy_note: 'Voice controller activity stores provider/status metadata only; no audio is stored.'",
    ),
    "tests/bombadil-spec.ts": (
        "commandCenterBecomesReady",
        "commandCenterDoesNotStayLoading",
        "commandCenterHasResponsiveClearance",
        "commandCenterTargetRoutesVisible",
    ),
}
CSS_NEEDLES = (
    ".chat-container.command-center-home {",
    "margin-top: 64px;",
    "margin-top: 48px;",
    ".chat-container.command-center-home #welcome-screen {",
    "top: 88px;",
    "max-height: calc(100dvh - 276px);",
    "@media (max-width: 768px)",
    "top: 40px;",
    "max-height: calc(100dvh - 160px);",
    ".command-center-targets",
    ".cc-command-readiness-deck",
)
LIVE_CHECK_NAMES = {
    "desktop": "live-desktop-command-center",
    "mobile": "live-mobile-command-center",
}


class IdTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key == "id" and value:
                self.ids.add(value)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text.append(data.strip())


def result(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "status": status, "detail": detail}
    row.update(extra)
    return row


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def static_checks() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = read_text("static/index.html")
    parser = IdTextParser()
    parser.feed(index)
    missing_ids = sorted(STATIC_IDS - parser.ids)
    rows.append(
        result(
            "static-command-center-dom",
            "ok" if not missing_ids else "fail",
            "Command Center DOM anchors are present" if not missing_ids else f"Missing ids: {', '.join(missing_ids)}",
            missing=missing_ids,
        )
    )
    page_text = " ".join(parser.text)
    missing_text = [text for text in STATIC_TEXT if text not in page_text and text not in index]
    rows.append(
        result(
            "static-command-center-labels",
            "ok" if not missing_text else "fail",
            "Command Center labels are present" if not missing_text else f"Missing labels: {', '.join(missing_text)}",
            missing=missing_text,
        )
    )

    for rel, needles in STATIC_JS_NEEDLES.items():
        text = read_text(rel)
        missing = [needle for needle in needles if needle not in text]
        rows.append(
            result(
                f"static-js:{rel}",
                "ok" if not missing else "fail",
                f"{rel} contains operator-console UI contracts" if not missing else f"{rel} missing {len(missing)} contract string(s)",
                missing=missing,
            )
        )

    css = read_text("static/style.css")
    missing_css = [needle for needle in CSS_NEEDLES if needle not in css]
    rows.append(
        result(
            "static-responsive-css",
            "ok" if not missing_css else "fail",
            "desktop and mobile Command Center layout rules are present" if not missing_css else f"Missing CSS strings: {', '.join(missing_css)}",
            missing=missing_css,
        )
    )
    return rows


def find_browser(explicit: str = "") -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_browser = os.getenv("CLEVERLY_UI_SMOKE_BROWSER", "")
    if env_browser:
        candidates.append(env_browser)
    candidates.extend(
        [
            shutil.which("chrome") or "",
            shutil.which("chromium") or "",
            shutil.which("google-chrome") or "",
            shutil.which("msedge") or "",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def login_cookie(url: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password, "remember": False}).encode("utf-8")
    req = urllib.request.Request(
        urllib.parse.urljoin(url.rstrip("/") + "/", "api/auth/login"),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        if res.status != 200:
            raise RuntimeError(f"login returned HTTP {res.status}")
        cookies = res.headers.get_all("Set-Cookie") or []
    parts = []
    for cookie in cookies:
        parts.append(cookie.split(";", 1)[0])
    if not parts:
        raise RuntimeError("login did not return a session cookie")
    return "; ".join(parts)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class CdpSocket:
    def __init__(self, ws_url: str):
        parsed = urllib.parse.urlparse(ws_url)
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw = socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=10)
        if parsed.scheme == "wss":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        self.sock = raw
        self.sock.settimeout(20)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("DevTools websocket handshake failed")
        self.next_id = 1

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = secrets.token_bytes(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv_json(self) -> dict[str, Any]:
        while True:
            first, second = recv_exact(self.sock, 2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", recv_exact(self.sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", recv_exact(self.sock, 8))[0]
            mask = recv_exact(self.sock, 4) if masked else b""
            payload = recv_exact(self.sock, length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("DevTools websocket closed")
            if opcode == 0x9:
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 20) -> dict[str, Any]:
        msg_id = self.next_id
        self.next_id += 1
        self.send_json({"id": msg_id, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.recv_json()
            if message.get("id") == msg_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
        raise TimeoutError(method)


def wait_for(predicate, timeout: float = 15, interval: float = 0.25):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


def browser_check(
    page: CdpSocket,
    url: str,
    cookie: str,
    width: int,
    height: int,
    label: str,
) -> dict[str, Any]:
    page.call("Network.enable")
    page.call("Page.enable")
    page.call("Runtime.enable")
    page.call("Network.setExtraHTTPHeaders", {"headers": {"Cookie": cookie}})
    page.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 700},
    )
    page.call("Page.navigate", {"url": url})
    time.sleep(3)
    script = r"""
(() => {
  const rows = (sel) => Array.from(document.querySelectorAll(sel)).map((node) => node.getAttribute('data-state') || '');
  const text = (sel) => (document.querySelector(sel)?.textContent || '').replace(/\s+/g, ' ').trim();
  const rect = document.querySelector('#chat-container')?.getBoundingClientRect();
  return {
    title: document.title,
    url: location.href,
    ready: document.body?.dataset?.cleverlyCommandCenterReady || '',
    version: document.body?.dataset?.cleverlyCommandCenterVersion || '',
    chatTop: rect ? rect.top : null,
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    readinessCount: document.querySelectorAll('#cc-command-readiness-deck [data-state]').length,
    targetCount: document.querySelectorAll('#cc-target-command-list [data-state]').length,
    loadingReadiness: rows('#cc-command-readiness-deck [data-state]').filter((state) => state === 'loading').length,
    loadingTargets: rows('.command-center-targets [data-state]').filter((state) => state === 'loading').length,
    targetSummary: text('#cc-targets-summary'),
  };
})()
"""
    def _state():
        result = page.call("Runtime.evaluate", {"expression": script, "returnByValue": True}, timeout=10)
        value = result.get("result", {}).get("value") or {}
        if value.get("ready") == "ready" and value.get("targetSummary"):
            return value
        return None

    state = wait_for(_state, timeout=20)
    if not state:
        result_eval = page.call("Runtime.evaluate", {"expression": script, "returnByValue": True}, timeout=10)
        state = result_eval.get("result", {}).get("value") or {}
    ok = (
        state.get("ready") == "ready"
        and state.get("chatTop") is not None
        and state.get("chatTop") >= 0
        and not state.get("horizontalOverflow")
        and state.get("readinessCount", 0) >= 8
        and state.get("targetCount", 0) >= 9
        and state.get("loadingReadiness") == 0
        and state.get("loadingTargets") == 0
        and "9/9 route-ready" in str(state.get("targetSummary", ""))
    )
    return result(
        LIVE_CHECK_NAMES.get(label, f"live-{label}-command-center"),
        "ok" if ok else "fail",
        f"{label} Command Center rendered with route-ready dashboard" if ok else f"{label} Command Center did not satisfy runtime UI contract",
        state=state,
        viewport={"width": width, "height": height},
    )


def live_checks(args: argparse.Namespace) -> list[dict[str, Any]]:
    username = os.getenv(args.username_env, "")
    password = os.getenv(args.password_env, "")
    if not username or not password:
        return [
            result(
                "live-auth",
                "warn",
                f"Skipped live browser check; set {args.username_env} and {args.password_env} for an authenticated UI smoke",
            )
        ]
    browser = find_browser(args.browser)
    if not browser:
        return [result("live-browser", "warn", "Skipped live browser check; Chrome or Edge executable was not found")]
    cookie = login_cookie(args.url, username, password)
    user_data = tempfile.mkdtemp(prefix="cleverly-ui-smoke-")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=0",
            f"--user-data-dir={user_data}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        active_port = Path(user_data) / "DevToolsActivePort"
        for _ in range(80):
            if active_port.exists():
                break
            if proc.poll() is not None:
                raise RuntimeError("browser exited before DevTools became available")
            time.sleep(0.25)
        if not active_port.exists():
            raise RuntimeError("DevToolsActivePort was not created")
        lines = active_port.read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as res:
            tabs = json.loads(res.read().decode("utf-8"))
        page_url = tabs[0]["webSocketDebuggerUrl"]
        page = CdpSocket(page_url)
        try:
            return [
                browser_check(page, args.url, cookie, 1280, 720, "desktop"),
                browser_check(page, args.url, cookie, 390, 844, "mobile"),
            ]
        finally:
            page.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(user_data, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Cleverly Command Center UI contracts")
    parser.add_argument("--url", default="http://127.0.0.1:7000/", help="Cleverly base URL")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="JSON report path")
    parser.add_argument("--browser", default="", help="Chrome/Edge executable path")
    parser.add_argument("--username-env", default="CLEVERLY_BOMBADIL_USERNAME")
    parser.add_argument("--password-env", default="CLEVERLY_BOMBADIL_PASSWORD")
    parser.add_argument("--static-only", action="store_true", help="Only run dependency-free static checks")
    parser.add_argument("--require-live", action="store_true", help="Fail when live authenticated browser checks are skipped")
    args = parser.parse_args()

    rows = static_checks()
    if not args.static_only:
        try:
            rows.extend(live_checks(args))
        except Exception as exc:
            rows.append(result("live-command-center", "fail", str(exc)))

    fail = sum(1 for row in rows if row["status"] == "fail")
    warn = sum(1 for row in rows if row["status"] == "warn")
    if args.require_live and not any(row["name"].startswith("live-desktop") and row["status"] == "ok" for row in rows):
        rows.append(result("live-required", "fail", "Live desktop/mobile UI smoke was required but did not pass"))
        fail += 1
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": args.url,
        "static_only": args.static_only,
        "require_live": args.require_live,
        "ok": sum(1 for row in rows if row["status"] == "ok"),
        "warn": warn,
        "fail": fail,
        "results": rows,
    }
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Report: {report_path}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
