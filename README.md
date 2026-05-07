# 🌉 grok-bridge v3.0

Turn **SuperGrok** into a REST API + CLI tool. No API key needed.

## How it works

```
Your Terminal/Script → Safari JS injection → grok.com → Response extracted via DOM
```

Two modes:

### REST API (recommended)
```bash
# Check local Safari/Grok setup first
python3 scripts/grok_bridge.py --doctor

# Start the server on your Mac
python3 scripts/grok_bridge.py --bind 127.0.0.1 --port 19998

# Default mode uses a dedicated background Safari tab named grok-bridge-agent.
# Use --shared-tab only when you intentionally want to drive the current Safari tab.

# Query from the Mac itself
curl -X POST http://127.0.0.1:19998/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is the mass of the sun?","timeout":60}'

# Health check
curl http://127.0.0.1:19998/health

# Read current conversation
curl http://127.0.0.1:19998/history
```

Use `--doctor --json` when another tool or agent needs structured setup diagnostics.

## Deployment boundary

Stable local deployment listens on `127.0.0.1:19998`. This is intentional: the bridge drives the signed-in Safari session on the Mac, so it should not be exposed directly on LAN, Tailscale, or a public interface.

Remote Claw/agent access should use an operator-controlled tunnel or proxy:

```bash
# From the remote agent host:
ssh -N -L 19998:127.0.0.1:19998 user@your-mac

# Then call the tunnel-local endpoint:
curl -X POST http://127.0.0.1:19998/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"hello","timeout":60}'
```

If a deployment must listen beyond loopback, start it explicitly with `--bind <trusted-interface-ip>` and put host firewall / network ACLs in front of it. Do not use `0.0.0.0` as the stable default.

### CLI (legacy)
```bash
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
- **No Accessibility permission needed by default** (v3 uses JS injection, not System Events)
- Optional `--foreground-fallback` may activate Safari and use System Events Enter if pure JS submit fails.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send prompt, wait for response |
| POST | `/new` | Start new conversation |
| GET | `/health` | Health check (Safari URL, grok status) |
| GET | `/history` | Read current page conversation |

## Safe review smoke (read-only)

For reviewer-style onboarding, prefer these commands before sending any prompt:

```bash
python3 scripts/grok_bridge.py --help
python3 scripts/grok_bridge.py --doctor
curl -s http://127.0.0.1:19998/health
curl -s http://127.0.0.1:19998/history
lsof -nP -iTCP:19998 -sTCP:LISTEN
# if a listener exists, capture its PID provenance before trusting the output
ps -p <PID> -o pid=,ppid=,lstart=,command=
```

Notes:
- `bash scripts/grok_chat.sh --help` is **not** read-only; the current script treats the first positional argument as a prompt and starts Safari automation.
- `--doctor` does not send prompts. It only checks platform, `osascript`, Safari reachability, current Grok URL, and input availability.
- By default, server and `--doctor` operate on a dedicated background tab marked with `window.name="grok-bridge-agent"`. Use `--shared-tab` for manual current-tab debugging.
- If port `19998` is already occupied, classify it as an environment conflict before blaming bridge logic.
- If a listener already exists on `19998`, capture `ps -p <PID> -o pid=,ppid=,lstart=,command=` so reviewers can tell whether `/health` and `/history` came from the current checkout or a stale long-lived process.
- See `docs/review-playbook.md` for evidence format, error taxonomy, and multi-agent ownership split.

## Version History

| | v1 | v2 | v3 |
|---|---|---|---|
| Input | Peekaboo UI | pbcopy + Cmd+V | JS `execCommand('insertText')` |
| Submit | UI click | System Events Return | JS `button.click()` |
| Permissions | Peekaboo + Accessibility | Accessibility | **None by default** (pure JS injection) |
| Interface | CLI only | CLI only | **REST API** + CLI |
| Dependencies | Peekaboo (brew) | None | None (stdlib only) |
| Speed | ~30s | ~3s | ~3s |

## Architecture

```
┌──────────────┐                     ┌───────────────────────┐
│  HTTP Client │  POST /chat         │      macOS            │
│  (local/tunnel)│ ─────────────────→ │                       │
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
- ✅ Dedicated Safari tab via `window.name="grok-bridge-agent"` — avoids taking over the user's current Grok tab

Zero Accessibility permissions by default, zero dependencies, pure JavaScript injection via AppleScript.

## Credits

v3 architecture designed by Claude Opus 4.6 (via [Antigravity](https://antigravity.so)), System Events bypass by 小灵 🦞.

## License

MIT
