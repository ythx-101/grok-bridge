# grok-bridge v3.1.1 release notes

`v3.1.1` is the publishable release candidate that supersedes the `v3.1.0` release-candidate attempt on `origin/main`.

## What changed

- Keeps the stable v3 bridge implementation: local REST API, Safari JavaScript injection, dedicated background tab, and `127.0.0.1` default binding.
- Adds explicit Chinese send-button support for grok.com UI labels: `发送` and `提交`.
- Keeps response extraction focused on the latest assistant message instead of page/sidebar text.
- Keeps `--doctor` diagnostics for setup checks without sending a prompt.
- Documents the safe deployment boundary: remote agents should use SSH tunnel or a controlled proxy, not direct public/LAN binding.

## Release boundary

This is not an official Grok API and not a public multi-user service. It is a local bridge for a signed-in Safari/Grok session on the operator's Mac.

Use:

```bash
python3 scripts/grok_bridge.py --doctor
python3 scripts/grok_bridge.py --bind 127.0.0.1 --port 19998
```

Remote agents should connect through an operator-controlled tunnel:

```bash
ssh -N -L 19998:127.0.0.1:19998 user@your-mac
```

## Verification

```bash
python3 -m py_compile scripts/grok_bridge.py tests/test_grok_bridge.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q
git diff --check
```
