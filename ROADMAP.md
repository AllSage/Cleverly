# Roadmap / Help Wanted

Cleverly is usable, but it is still moving quickly. Feedback, testing, and
focused fixes are welcome, especially around fresh installs, integrations, and
security-sensitive workflows.

If you see weird CSS, strange layout behavior, or a suspiciously murky corner of
the codebase, file an issue or keep the change small and well-tested.

## High Priority

- Fresh Docker install smoke tests on Linux, macOS, and Windows.
- Integration audit: confirm what works, what needs setup docs, and what should be removed or hidden.
- Self-host troubleshooting docs for Dovecot, ntfy, Radicale, Tailscale, and common reverse-proxy setups.
- Cookbook reliability across different machines, GPUs, drivers, shells, and Python environments.
- Tile/window management correctness for popups, dropdowns, and fixed-position UI inside transformed modals.
- Esc key behavior across modal surfaces.
- Skill injection and skill parsing audit.
- Better degraded-state reporting for ChromaDB, SearXNG, email, ntfy, and provider probes.
- Provider setup/probing audit for Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, and DeepSeek.

## Refactor Targets

- Gradual CSS extraction from `static/style.css`.
- Shared `tour-core.js` helper for onboarding tours.
- Better comments or linting around desktop/mobile paired CSS rules.
- Dead-code pass for old routes, stale feature flags, and unused UI states.

## Frontend

- Mobile gallery/editor polish.
- Accessibility pass: keyboard navigation, focus states, contrast, reduced motion.
- Improve empty states and error messages on fresh installs.
- Tighten first-run setup, hints, and tours so they do not repeat or conflict.
- Vendor CDN assets eventually for a more fully self-hosted/offline mode.

## Backend

- More tests around endpoint probing and provider setup.
- Better task scheduler defaults and visibility.
- Backup/restore guide and helper flow for `data/`.
- Security hardening around admin-only tools and clear docs for their risk.

## Not The Focus Right Now

- Adding more themes before the current UI surface is cleaned up.
