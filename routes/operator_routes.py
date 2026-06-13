"""Operator readiness and air-gap checks."""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core.middleware import require_admin
from src.operator_checks import run_operator_checks


def setup_operator_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/operator",
        tags=["operator"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/checks")
    def operator_checks():
        return {"ok": True, **run_operator_checks()}

    @router.get("/page", response_class=HTMLResponse)
    def operator_page(request: Request):
        nonce = html.escape(getattr(request.state, "csp_nonce", "") or "")
        return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cleverly Operator Status</title>
  <style>
    :root {{ color-scheme: dark; --bg:#101216; --panel:#171a20; --fg:#eef1f5; --muted:#a8b0bd; --border:#2a2f38; --ok:#49c37b; --warn:#e3b341; --fail:#ef5b5b; }}
    body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
    main {{ max-width:980px; margin:0 auto; padding:28px 18px; }}
    h1 {{ font-size:24px; margin:0 0 4px; letter-spacing:0; }}
    .sub {{ color:var(--muted); margin:0 0 20px; }}
    .summary {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
    .pill {{ border:1px solid var(--border); background:var(--panel); border-radius:8px; padding:8px 10px; font-weight:700; }}
    .checks {{ border:1px solid var(--border); border-radius:8px; overflow:hidden; background:var(--panel); }}
    .row {{ display:grid; grid-template-columns:96px minmax(170px,260px) 1fr; gap:10px; padding:11px 12px; border-top:1px solid var(--border); align-items:start; }}
    .row:first-child {{ border-top:0; }}
    .status {{ text-transform:uppercase; font-weight:800; font-size:12px; }}
    .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .fail {{ color:var(--fail); }}
    .label {{ font-weight:700; }}
    .detail {{ color:var(--muted); word-break:break-word; }}
    button {{ border:1px solid var(--border); border-radius:6px; padding:7px 10px; background:#20242c; color:var(--fg); cursor:pointer; }}
    @media(max-width:680px) {{ .row {{ grid-template-columns:1fr; gap:3px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>Cleverly Operator Status</h1>
    <p class="sub" id="policy-note">Loading checks...</p>
    <div class="summary" id="summary"></div>
    <section class="checks" id="checks"></section>
    <p><button id="refresh">Refresh</button></p>
  </main>
  <script nonce="{nonce}">
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    async function loadChecks() {{
      const res = await fetch('/api/operator/checks', {{ credentials: 'same-origin' }});
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || 'Operator checks failed');
      document.getElementById('policy-note').textContent = `strict=${{data.strict}} offline=${{data.offline}} break_glass=${{data.break_glass}}`;
      const s = data.summary || {{}};
      document.getElementById('summary').innerHTML = ['ok','warn','fail'].map(k => `<div class="pill ${{k}}">${{k}}: ${{s[k] || 0}}</div>`).join('');
      document.getElementById('checks').innerHTML = (data.checks || []).map(item => `
        <div class="row">
          <div class="status ${{esc(item.status)}}">${{esc(item.status)}}</div>
          <div class="label">${{esc(item.label)}}</div>
          <div class="detail">${{esc(item.detail)}}</div>
        </div>`).join('');
    }}
    document.getElementById('refresh').addEventListener('click', () => loadChecks().catch(err => alert(err.message)));
    loadChecks().catch(err => {{ document.getElementById('checks').innerHTML = `<div class="row"><div class="status fail">fail</div><div class="label">Status load</div><div class="detail">${{esc(err.message)}}</div></div>`; }});
  </script>
</body>
</html>""")

    return router
