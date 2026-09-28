# Contributing

Jev-Jarvis turns a request into a preview, then an approved action. Keep that flow easy to inspect.

## Local setup

Use Python 3.11+ in a virtual environment. `python -m pip install -e '.[dev]'` installs development tools. Run `python -m jarvis` for rehearsal and `python -m unittest discover -s tests -v` for the core tests. `ruff check .` checks Python code. No model credentials are needed for tests.

## Adding a capability

1. Add a strict argument schema and preview in `jarvis/actions.py`. Reject unknown fields; resolve aliases from local configuration. State what cannot be undone.
2. Add a command form in `jarvis/planner.py` and a bounded intent in `jarvis/providers.py` if relevant. Model output never bypasses validation.
3. Implement the smallest native adapter with fixed argv. Do not interpolate prompts into AppleScript or a shell command.
4. Test invalid inputs, rehearsal without effects, native error reporting, and any claimed undo. Mock messages and other external effects.
5. Add an example, update the capability table, and inspect the UI with the exact preview text.

Keep optional model integrations optional. An unavailable provider should explain the problem without silently changing privacy settings. Never add real contacts, messages, credentials, model caches, or local activity files to a change.

For interface changes, check keyboard navigation, visible focus, a narrow viewport, long previews, loading, errors, cancellation, and duplicate clicks. Use the local server so these checks exercise the actual API.

## Reporting issues

Include OS/Python versions, selected decision/planning providers, a minimal non-private command, and expected versus actual behavior. Remove keys, personal destinations, and message contents from logs. For security issues, follow [SECURITY.md](SECURITY.md).

Optional browser smoke: install Playwright with `npm install --no-save playwright` and `npx playwright install chromium`, start a fresh rehearsal server, then run `node tests/browser-smoke.cjs`. It refuses a live-mode server. Set `JARVIS_URL` to test a different loopback port. This checks preview/approval, receipts, navigation, errors, changed drafts, and responsive widths.
