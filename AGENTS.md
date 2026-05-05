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
```

## Contract

- Exit code `0` means the bridge returned `status=ok`.
- Non-zero exit means connection failure, timeout, or Grok/UI automation failure.
- `--json` prints the raw bridge response with fields such as `status`,
  `response`, `error`, and `elapsed`.
- This is a browser bridge, not an official API. If grok.com changes its UI,
  prefer a small selector/extraction fix over broad self-healing logic.

## Good agent projects

- Failing-test debugger loop: send failure output and minimal source context,
  ask for the smallest fix, apply locally, rerun tests.
- Documentation sync: send `git diff`, ask for docstrings, changelog entries,
  or migration notes, then edit locally.
- Integration spike helper: ask Grok for implementation options under explicit
  constraints, then benchmark or test the options locally before committing.
