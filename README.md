# 🌉 grok-bridge v3.0

Turn **SuperGrok** into a REST API + CLI tool. No API key needed.

## How it works

```
Your Terminal/Script → Safari JS injection → grok.com → Response extracted via DOM
```

Two modes:

### REST API (recommended)
```bash
# Safer default: listen on localhost only
python3 scripts/grok_bridge.py --port 19998

# Optional: require a Bearer token even for local calls
export GROK_BRIDGE_TOKEN="replace-with-a-long-random-secret"
python3 scripts/grok_bridge.py --port 19998

# Local query
curl -X POST http://127.0.0.1:19998/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GROK_BRIDGE_TOKEN" \
  -d '{"prompt":"What is the mass of the sun?","timeout":60}'

# Health check
curl -H "Authorization: Bearer $GROK_BRIDGE_TOKEN" \
  http://127.0.0.1:19998/health

# Read current conversation
curl -H "Authorization: Bearer $GROK_BRIDGE_TOKEN" \
  http://127.0.0.1:19998/history

# Remote/LAN access: only if you really need it
export GROK_BRIDGE_TOKEN="replace-with-a-long-random-secret"
python3 scripts/grok_bridge.py --host 0.0.0.0 --port 19998
```

If you start the server without `GROK_BRIDGE_TOKEN`, local requests can omit the `Authorization` header.

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
- **No Accessibility permission needed** (v3 uses JS injection, not System Events)

## Security Defaults

- `grok_bridge.py` now binds to `127.0.0.1` by default, not `0.0.0.0`
- Set `GROK_BRIDGE_TOKEN` or `--token` to require `Authorization: Bearer ...`
- Non-loopback binds such as `--host 0.0.0.0` are refused unless a token is set
- Prompt/response content is no longer logged by default; use `--log-content` only for debugging
- `/health` reports the current page host, not the full Safari URL

Recommended token generation:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send prompt, wait for response |
| POST | `/new` | Start new conversation |
| GET | `/health` | Health check (page host, grok status) |
| GET | `/history` | Read the cleaned current conversation transcript |

If auth is enabled, every endpoint requires either:

```http
Authorization: Bearer <token>
```

or:

```http
X-Grok-Bridge-Token: <token>
```

`GET /history` now returns both a cleaned transcript string and structured messages:

```json
{
  "status": "ok",
  "content": "User: ...\n\nAssistant: ...",
  "messages": [
    {"role": "user", "text": "..."},
    {"role": "assistant", "text": "..."}
  ],
  "message_count": 2
}
```

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
