# 🌉 grok-bridge v3.0

Turn **SuperGrok** into a REST API + CLI tool. No API key needed.

## How it works

```
Your Terminal/Script → Safari JS injection → grok.com → Response extracted via DOM
```

Two modes:

### REST API (recommended)
```bash
# Start the server on your Mac
python3 scripts/grok_bridge.py server --port 19998

# Legacy server mode still works
python3 scripts/grok_bridge.py --port 19998

# Query from anywhere
curl -X POST http://your-mac:19998/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is the mass of the sun?","timeout":60}'

# Health check
curl http://your-mac:19998/health

# Read current conversation
curl http://your-mac:19998/history
```

### CLI
```bash
# One-shot via a running grok-bridge server
python3 scripts/grok_bridge.py chat "Explain quantum tunneling"
python3 scripts/grok_bridge.py chat "Write a haiku" --server http://your-mac:19998

# Agent-friendly modes
git diff | python3 scripts/grok_bridge.py chat --stdin --json
python3 scripts/grok_bridge.py chat "Summarize this fresh thread" --new
python3 scripts/grok_bridge.py health --json

# Local
bash scripts/grok_chat.sh "Explain quantum tunneling"

# Remote via SSH
MAC_SSH="ssh user@your-mac" bash scripts/grok_chat.sh "Write a haiku" --timeout 90
```

## Requirements

- macOS with Safari
- Logged into [grok.com](https://grok.com) (free or SuperGrok)
- Safari > Settings > Advanced > Show features for web developers ✓
- Safari > Develop > Allow JavaScript from Apple Events ✓
- **No Accessibility permission needed** (v3 uses JS injection, not System Events)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send prompt, wait for response |
| POST | `/new` | Start new conversation |
| GET | `/health` | Health check (Safari URL, grok status) |
| GET | `/version` | Bridge version |
| GET | `/capabilities` | API and CLI capability metadata |
| GET | `/history` | Read current page conversation |

## Agent usage ideas

- Failing-test debugger loop: send tracebacks and focused source snippets, ask for the smallest fix, apply locally, rerun.
- Doc/changelog sync: send `git diff`, ask for concise docstrings, release notes, or migration notes.
- Spike helper: send a parser/API/state-machine constraint, ask Grok for two implementation options, then benchmark locally.

## Version History

| | v1 | v2 | v3 |
|---|---|---|---|
| Input | Peekaboo UI | pbcopy + Cmd+V | JS `execCommand('insertText')` |
| Submit | UI click | System Events Return | JS `button.click()` |
| Permissions | Peekaboo + Accessibility | Accessibility | **None** (pure JS injection) |
| Interface | CLI only | CLI only | **REST API** + CLI |
| Dependencies | Peekaboo (brew) | None | None (stdlib only) |
| Speed | ~30s | ~3s | ~3s |

## Architecture

```
┌──────────────┐                     ┌───────────────────────┐
│  HTTP Client │  POST /chat         │      macOS            │
│  (anywhere)  │ ──────────────────→ │                       │
└──────────────┘                     │  grok_bridge.py       │
                                     │  ↓ osascript          │
                                     │  Safari do JavaScript │
                                     │  ↓ execCommand        │
                                     │  grok.com textarea    │
                                     │  ↓ button.click()     │
                                     │  Grok responds        │
                                     │  ↓ DOM poll           │
                                     │  Response extracted   │
                                     └───────────────────────┘
```

## Key Insight (v3)

React controlled inputs ignore JavaScript `value` setter, synthetic `InputEvent`, and even `nativeInputValueSetter`.

What **doesn't** work from SSH:
- ❌ `osascript keystroke` — blocked by macOS Accessibility
- ❌ CGEvent (Swift) — HID events don't reach web content
- ❌ JS `InputEvent` / `nativeInputValueSetter` — React ignores synthetic events

What **does** work:
- ✅ `document.execCommand('insertText')` — triggers real input in the browser
- ✅ JS `button.click()` on Send button — no System Events needed

Zero permissions, zero dependencies, pure JavaScript injection via AppleScript.

## Credits

v3 architecture designed by Claude Opus 4.6 (via [Antigravity](https://antigravity.so)), System Events bypass by 小灵 🦞.

## License

MIT
