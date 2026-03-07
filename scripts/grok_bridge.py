#!/usr/bin/env python3
"""
grok_bridge.py v1.1 — Talk to Grok via Safari automation (macOS)

Safari 不支持标准 CDP。本脚本用 AppleScript + JS 注入实现等价功能：
  - AppleScript `do JavaScript` ≈ CDP Runtime.evaluate
  - AppleScript `keystroke` / pbcopy+paste ≈ CDP Input.dispatchKeyEvent
  - 读取 document.body.innerText ≈ CDP DOM 读取

前置条件:
  Safari > 设置 > 高级 > 显示网页开发者功能 ✓
  Safari > 开发 > 允许来自 Apple Events 的 JavaScript ✓

用法:
  python3 grok_bridge.py --port 19998
  curl -X POST http://localhost:19998/chat -d '{"prompt":"hello"}'
"""
import json, time, threading, re, argparse, subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

GROK_URL = "https://grok.com"
VERSION = "v1.1"
# 多种输入框选择器（grok.com 可能更新 UI）
INPUT_SELECTORS = [
    "textarea",
    'div[contenteditable="true"]',
    '[data-testid="text-input"]',
    '[role="textbox"]',
]
# 发送按钮选择器
SEND_SELECTORS = ['button[aria-label="Send"]', 'button[data-testid="send-button"]']

class GrokBridge:
    def __init__(s):
        s.lock = threading.Lock()

    def _osa(s, script, timeout=30):
        r = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0:
            raise RuntimeError(f"osascript: {r.stderr.strip()[:200]}")
        return r.stdout.strip()

    def _js(s, js, timeout=30):
        """Execute JS in Safari front tab (≈ CDP Runtime.evaluate)"""
        esc = js.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return s._osa(
            f'tell application "Safari" to do JavaScript "{esc}" in current tab of front window',
            timeout,
        )

    def _ensure_grok(s):
        """Navigate to grok.com if not already there"""
        try:
            url = s._osa(
                'tell application "Safari" to get URL of current tab of front window'
            )
        except:
            url = ""
        if "grok.com" not in url:
            s._osa(
                f'tell application "Safari" to set URL of current tab of front window to "{GROK_URL}"'
            )
            time.sleep(4)

    def _find_input(s):
        """Find the input element using multiple selectors"""
        for sel in INPUT_SELECTORS:
            r = s._js(f"!!document.querySelector('{sel}')")
            if r == "true":
                return sel
        return None

    def _wait_ready(s, timeout=20):
        start = time.time()
        while time.time() - start < timeout:
            sel = s._find_input()
            if sel:
                return sel
            time.sleep(0.5)
        return None

    def _type_and_send(s, text, input_sel):
        """Type text via JS insertText + click Send (no System Events needed)"""
        # Activate Safari
        s._osa('tell application "Safari" to activate')
        time.sleep(0.3)
        # Focus input and insert text via execCommand (works with React contenteditable)
        safe = (
            text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "")
        )
        s._js(f"""(()=>{{
            const el=document.querySelector('{input_sel}');
            if(!el)return'NO';
            el.focus();
            if(el.tagName==='TEXTAREA'){{el.value='';}}
            else{{el.textContent='';}}
            document.execCommand('insertText',false,'{safe}');
            return'OK';
        }})()""")
        time.sleep(0.5)
        # Try click Send button (JS click, no System Events)
        for btn_sel in SEND_SELECTORS:
            r = s._js(
                f"(()=>{{const b=document.querySelector('{btn_sel}');if(b&&!b.disabled){{b.click();return'OK'}};return'NO'}})()"
            )
            if "OK" in str(r):
                return True
        # More robust button click added by ZED v4.6
        r = s._js(
            """(()=>{const btn=document.querySelector('button[aria-label="Send Message"]')||document.querySelector('button[type="submit"]')||document.querySelector('button[aria-label="Grok"]');if(btn){btn.click();return'OK'}return'NO'})()"""
        )
        if "OK" in str(r):
            return True
        # Fallback: find button with Send/Submit text
        r = s._js(
            """(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>/send|发送|submit|grok/i.test(x.textContent||x.ariaLabel||''));if(b&&!b.disabled){b.click();return'OK'}return'NO'})()"""
        )
        if "OK" in str(r):
            return True
        # Last resort: dispatch Enter KeyboardEvent on the input
        s._js(
            f"document.querySelector('{input_sel}')?.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',code:'Enter',keyCode:13,bubbles:true}}))"
        )
        return True

    def _get_body(s):
        return s._js("document.body.innerText", timeout=15)

    def _clean(s, text):
        """Clean Grok UI artifacts from response"""
        for m in [
            "\nAsk anything",
            "\nDeepSearch",
            "\nThink Harder",
            "\nThink\n",
            "\nAttach",
            "\nGrok",
            "\nFast\n",
            "\nAuto\n",
            "\nUpgrade to",
        ]:
            i = text.rfind(m)
            if i > 0:
                text = text[:i]
        text = re.sub(r"\n[0-9]+(\.[0-9]+)?s\n", "\n", text)  # remove "1.3s" timing
        text = re.sub(
            r"\n(Share|Compare|Make it|Explain|Toggle|Like|Dislike).*", "", text
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _extract(s, body, prompt):
        marker = prompt[:60]
        parts = body.split(marker)
        after = parts[-1] if len(parts) >= 2 else body
        return s._clean(after)

    def chat(s, prompt, timeout=120):
        with s.lock:
            return s._chat(prompt, timeout)

    def _chat(s, prompt, timeout):
        try:
            s._ensure_grok()
            sel = s._wait_ready()
            if not sel:
                return {"status": "error", "error": "input not found"}
            body_before = s._get_body()
            s._type_and_send(prompt, sel)
            # Poll for response stability
            start = time.time()
            last = ""
            stable = 0
            while time.time() - start < timeout:
                time.sleep(2)
                body = s._get_body()
                # Response appeared and stabilized
                if body != body_before and body == last:
                    stable += 1
                    if stable >= 3:
                        return {
                            "status": "ok",
                            "response": s._extract(body, prompt),
                            "elapsed": round(time.time() - start, 1),
                        }
                else:
                    stable = 0
                last = body
            # Timeout
            resp = s._extract(last, prompt) if last else ""
            return {
                "status": "timeout",
                "response": resp,
                "elapsed": round(time.time() - start, 1),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def history(s):
        try:
            body = s._get_body()
            return {"status": "ok", "content": s._clean(body), "raw_length": len(body)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def health(s):
        try:
            url = s._osa(
                'tell application "Safari" to get URL of current tab of front window'
            )
            return {
                "status": "ok",
                "url": url,
                "on_grok": "grok.com" in url,
                "version": VERSION,
            }
        except:
            return {
                "status": "error",
                "error": "safari not reachable",
                "version": VERSION,
            }

b = None

class H(BaseHTTPRequestHandler):
    def do_POST(s):
        d = json.loads(s.rfile.read(int(s.headers.get("Content-Length", 0))) or b"{}")
        if s.path == "/chat":
            p = d.get("prompt", "")
            to = d.get("timeout", 120)
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] >> {p[:80]}", flush=True)
            try:
                r = b.chat(p, to)
                s._j(200, r)
                print(
                    f"[{ts}] << [{r.get('status')}] {str(r.get('response', r.get('error', '')))[:80]}",
                    flush=True,
                )
            except Exception as e:
                s._j(500, {"error": str(e), "status": "error"})
        elif s.path == "/new":
            try:
                b._osa(
                    f'tell application "Safari" to set URL of current tab of front window to "{GROK_URL}"'
                )
                time.sleep(3)
                s._j(200, {"status": "ok"})
            except Exception as e:
                s._j(500, {"error": str(e), "status": "error"})
        else:
            s.send_response(404)
            s.end_headers()

    def do_GET(s):
        if s.path == "/health":
            s._j(200, b.health())
        elif s.path == "/history":
            try:
                s._j(200, b.history())
            except Exception as e:
                s._j(500, {"error": str(e), "status": "error"})
        else:
            s.send_response(404)
            s.end_headers()

    def _j(s, c, d):
        s.send_response(c)
        s.send_header("Content-Type", "application/json")
        s.end_headers()
        s.wfile.write(json.dumps(d, ensure_ascii=False).encode())

    def log_message(s, *a):
        pass

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    pa = argparse.ArgumentParser()
    pa.add_argument("--port", type=int, default=19998)
    a = pa.parse_args()
    b = GrokBridge()
    print(f"Grok Bridge {VERSION} :{a.port}", flush=True)
    print("前置: Safari > 开发 > 允许来自 Apple Events 的 JavaScript", flush=True)
    ThreadedHTTPServer(("0.0.0.0", a.port), H).serve_forever()
