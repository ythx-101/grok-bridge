# grok-bridge for agents

Use this repository when an agent needs a scriptable Grok interface and a Safari
session is already logged in on the bridge host.

## Quick calls

Start the server on the Mac that owns the Safari session:

```bash
python3 scripts/grok_bridge.py server --port 19998
```

Send a one-shot prompt:

```bash
python3 scripts/grok_bridge.py chat "Review this approach" --server http://127.0.0.1:19998
```

Send long context through stdin and request machine-readable output:

```bash
git diff | python3 scripts/grok_bridge.py chat --stdin --json --server http://127.0.0.1:19998
```

Start from a fresh Grok thread:

```bash
python3 scripts/grok_bridge.py chat "Analyze this traceback" --new --server http://127.0.0.1:19998
```

Check availability:

```bash
python3 scripts/grok_bridge.py health --json --server http://127.0.0.1:19998
python3 scripts/grok_bridge.py state --json --server http://127.0.0.1:19998
```

Inspect or switch model:

```bash
python3 scripts/grok_bridge.py model --server http://127.0.0.1:19998
python3 scripts/grok_bridge.py model "Grok 4.3" --server http://127.0.0.1:19998
```

Navigate experimental Grok surfaces:

```bash
python3 scripts/grok_bridge.py mode Imagine --server http://127.0.0.1:19998
python3 scripts/grok_bridge.py imagine "A minimal CLI control room" --json --server http://127.0.0.1:19998
python3 scripts/grok_bridge.py images --json --server http://127.0.0.1:19998
python3 scripts/grok_bridge.py project "My Project" --server http://127.0.0.1:19998
```

## Contract

- Exit code `0` means the bridge returned `status=ok`.
- Non-zero exit means connection failure, timeout, or Grok/UI automation failure.
- `--json` prints the raw bridge response with fields such as `status`,
  `response`, `error`, and `elapsed`.
- This is a browser bridge, not an official API. If grok.com changes its UI,
  prefer a small selector/extraction fix over broad self-healing logic.
- Server mode uses a dedicated Safari tab by default, marked with
  `window.name = "grok-bridge-agent"`, so agents can keep context without
  taking over the user's current Grok tab. Use `--shared-tab` only for manual
  debugging.
- `model`, `mode`, and `project` are intentionally experimental because they
  click visible grok.com UI controls rather than a stable official API.
- `imagine` is also experimental. Use `--json` so the caller can inspect the
  returned `images` array instead of scraping stdout.
- Do not run UI-mutating calls in parallel against one server. The bridge
  serializes them, but one browser tab is still the shared execution surface.

## Good agent projects

- Failing-test debugger loop: send failure output and minimal source context,
  ask for the smallest fix, apply locally, rerun tests.
- Documentation sync: send `git diff`, ask for docstrings, changelog entries,
  or migration notes, then edit locally.
- Integration spike helper: ask Grok for implementation options under explicit
  constraints, then benchmark or test the options locally before committing.
