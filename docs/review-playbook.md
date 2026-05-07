# Grok Bridge Review Playbook

A read-only checklist for agents reviewing or onboarding `grok-bridge` without burning Grok state, leaking secrets, or confusing transport failures with product bugs.

## Goal

Standardize how multi-agent reviewers collect evidence for `grok-bridge` from the **review gate / skill / reuse** angle.

This playbook is intentionally conservative:
- prefer read-only checks first
- do not bypass missing Grok login/cookies
- do not post prompts to Grok unless the issue explicitly asks for live chat validation
- capture failures as categorized evidence, not as vague "doesn't work"

## Safe smoke sequence

Run in repo root.

## Deployment boundary

Release evidence should treat `127.0.0.1:19998` as the stable listener boundary. The bridge controls the signed-in Safari session on the Mac, so direct LAN/Tailscale/public binding is not the default release posture.

For remote Claw or remote CI-style agents, use an operator-controlled tunnel:

```bash
ssh -N -L 19998:127.0.0.1:19998 user@your-mac
curl -s http://127.0.0.1:19998/health
```

If an operator intentionally exposes a non-loopback listener, record the explicit `--bind <trusted-interface-ip>` command and the firewall/proxy boundary in the review packet. Treat accidental `0.0.0.0:19998` as a deployment risk, not as the stable default.

### 1) Static discovery

```bash
git status --short --branch
git log -1 --oneline
python3 scripts/grok_bridge.py --help
python3 scripts/grok_bridge.py --doctor
```

Expected outcome:
- confirms repo state and current revision
- confirms Python entrypoint parses normally
- confirms local macOS / Safari / Grok readiness without sending a prompt

### 2) Read-only HTTP health check

Before starting a new server, first check whether one is already running on the default port:

```bash
curl -s http://127.0.0.1:19998/health
lsof -nP -iTCP:19998 -sTCP:LISTEN
```

If a listener already exists, capture **listener provenance** before attributing behavior to the checked-out repo state:

```bash
ps -p <PID> -o pid=,ppid=,lstart=,command=
```

Interpretation:
- `{"status":"ok",...}` → bridge reachable; inspect reported URL/on_grok
- `{"status":"error","error":"safari not reachable"}` → bridge process reachable, Safari session unavailable
- connection refused → no bridge listening
- listener exists but startup fails with `Address already in use` → environment conflict, not product logic failure
- listener exists but its command/start time does not match your current checkout/session → classify as stale listener evidence drift, not repo regression

### 3) Read-only conversation inspection

```bash
curl -s http://127.0.0.1:19998/history
```

Interpretation:
- `status=ok` with content → active tab readable
- AppleScript/timeout error → Safari tab exists but DOM read is blocked, hung, or not ready

### 4) What NOT to use for read-only smoke

Avoid these unless you intentionally want side effects:

```bash
bash scripts/grok_chat.sh "..."
curl -X POST http://127.0.0.1:19998/chat ...
curl -X POST http://127.0.0.1:19998/new
```

Also note:
- `bash scripts/grok_chat.sh --help` is **not** a safe help command; the current script treats the first positional argument as a prompt and begins Safari automation.
- Missing Grok login is a valid review finding. Do not work around it with borrowed cookies/tokens.

## Error taxonomy

Use one of these labels in review comments so owners know who should pick it up.

| Class | What it means | Typical evidence | Likely owner |
|---|---|---|---|
| `ENV_PORT_CONFLICT` | default port already occupied | server startup traceback, `lsof` output | Codex or local operator |
| `ENV_STALE_LISTENER` | an existing local bridge process is serving results from an older shell/session or unknown checkout | `lsof` + `ps -p <PID> -o ...` provenance mismatch | local operator / reviewer |
| `ENV_SAFARI_UNREACHABLE` | Safari not running / not scriptable / wrong session | `/health` returns `safari not reachable` | local operator |
| `ENV_OSASCRIPT_MISSING` | reviewer is on Linux/VPS or another host without AppleScript | `--doctor` reports `osascript` missing | local operator |
| `ENV_GROK_AUTH` | browser reachable but not logged into Grok | Safari URL/screenshot/history indicates login wall | local operator |
| `PRODUCT_UI_SELECTOR` | Grok page changed and selectors stopped matching | input/button not found, DOM evidence | NanoClaw or Codex |
| `PRODUCT_DOC_GAP` | docs/skill do not explain expected setup or failure mode | README/SKILL mismatch, no troubleshooting path | Hermes Reviewer |
| `PRODUCT_REVIEW_GAP` | evidence format / handoff unclear across agents | inconsistent comments, missing commands, vague verdicts | Hermes Reviewer / OpenClaw |

## Troubleshooting matrix

| Symptom | Classification | Meaning | Recommended next owner |
|---|---|---|---|
| `--doctor` => `osascript not found` / `osascript` missing | `ENV_OSASCRIPT_MISSING` | command was run outside the required macOS/Safari host | local operator |
| `/health` => `safari not reachable` | `ENV_SAFARI_UNREACHABLE` | bridge process is reachable but Safari session is unavailable | local operator |
| `/history` => AppleScript timeout | `ENV_SAFARI_UNREACHABLE` or `PRODUCT_UI_SELECTOR` | Safari front tab exists, but DOM read is blocked or hung | NanoClaw if UI-specific, otherwise local operator |
| server start fails with `Address already in use` | `ENV_PORT_CONFLICT` | another local process already owns the default port | Codex or local operator |
| `/health`/`/history` are reachable but served by an unexpected long-lived PID | `ENV_STALE_LISTENER` | evidence may be coming from an older bridge instance, not the current checkout | local operator / reviewer |
| `bash scripts/grok_chat.sh --help` starts a conversation | `PRODUCT_REVIEW_GAP` | CLI lacks a safe discovery path and mixes help with side effects | Codex + Hermes |

## Evidence template

Use this compact structure in issue comments or handoff notes.

```md
Verdict: pass | concerns | blocked

Pain point:
- one sentence on the highest-value reviewer finding

Evidence:
- Repo: `git log -1 --oneline` => ...
- Help: `python3 scripts/grok_bridge.py --help` => ...
- Health: `curl -s http://127.0.0.1:19998/health` => ...
- History: `curl -s http://127.0.0.1:19998/history` => ...
- Port: `lsof -nP -iTCP:19998 -sTCP:LISTEN` => ...
- Provenance (if listener exists): `ps -p <PID> -o pid=,ppid=,lstart=,command=` => ...

Classification:
- `ENV_PORT_CONFLICT`
- `ENV_STALE_LISTENER`
- `PRODUCT_DOC_GAP`

Suggested owner:
- Hermes Reviewer / Codex / NanoClaw / OpenClaw Governor

Next safe step:
- one concrete action
```

## Ownership split

Use this boundary to avoid duplicated work.

### Hermes Reviewer
- README / SKILL / troubleshooting / evidence template quality
- review gate language
- repeatable onboarding and smoke-check playbooks

### Codex
- implementation changes with low product ambiguity
- CLI/server ergonomics, flags, better error messages
- docs updates that directly match shipped behavior

### NanoClaw
- live product interaction evidence when Grok UI behavior changes
- selector validation against current grok.com DOM
- screenshots / reproduction prompts for flaky browser-side failures

### OpenClaw Governor
- accept or reject review packet
- decide smallest next task boundary
- route follow-up to the correct implementation owner

## Artifact boundary

When writing a review packet, separate **Hermes artifacts** from **code-side artifacts** explicitly.

- Hermes artifacts: `README.md`, `SKILL.md`, `docs/review-playbook.md`, review checklist text, troubleshooting matrix, evidence template.
- Code-side artifacts: `scripts/grok_bridge.py`, `scripts/grok_chat.sh`, server behavior, flags such as a future `--doctor` or `--bind`.
- If code-side behavior is observed but not changed in this issue, report it as evidence / follow-up scope, not as Hermes-delivered work.
- If README or SKILL mention a capability not present on the checked-out revision, classify it as `PRODUCT_DOC_GAP` and either align docs or hand off the implementation delta to Codex.

## Review gate rule

When moving an issue to `in_review`, include all of:
- what changed or what artifact was added
- exact evidence commands used
- remaining blocker or uncertainty
- explicit line: `请 OpenClaw Governor / 小灵总控 review。`
