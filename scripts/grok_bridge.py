#!/usr/bin/env python3
"""
grok_bridge.py

Safari 自动化调用 grok.com 的 REST API 服务器 + CLI 客户端

使用方式：
  # 作为服务器运行
  python scripts/grok_bridge.py server --port 19998

  # CLI one-shot 模式
  python scripts/grok_bridge.py chat "你好"
  python scripts/grok_bridge.py chat "你好" --server http://192.168.1.100:19998

策略：
- 底层保持简单模块化
- 不追求自愈
- 接受“坏了就修”的维护模式
"""
import json
import time
import threading
import re
import argparse
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

GROK_URL = 'https://grok.com'
VERSION = 'v1-cli'
BRIDGE_TAB_NAME = 'grok-bridge-agent'

CAPABILITIES = {
    'api': {
        'GET': ['/health', '/history', '/version', '/capabilities', '/state', '/model', '/images'],
        'POST': ['/chat', '/new', '/model', '/mode', '/project', '/imagine']
    },
    'cli': {
        'commands': ['server', 'chat', 'health', 'new', 'state', 'model', 'mode', 'project', 'imagine', 'images'],
        'chat_flags': ['--server', '--timeout', '--json', '--stdin', '--new'],
        'server_flags': ['--port', '--shared-tab']
    },
    'constraints': ['macOS', 'Safari', 'grok.com login', 'Safari JavaScript from Apple Events'],
    'experimental': ['model switching', 'mode navigation', 'project navigation', 'image generation via Imagine']
}

# ==================== 配置与工具函数 ====================

def load_config():
    """ 简单配置加载（可扩展为文件） """
    config = {
        'server': 'http://localhost:19998',
        'timeout': 120
    }
    # TODO: 后续可从 ~/.config/grok-bridge/config.json 加载
    return config


def capabilities():
    return {'status': 'ok', 'version': VERSION, 'capabilities': CAPABILITIES}


def print_response(response, json_output=False):
    """ 简单美观输出 """
    if json_output:
        print(json.dumps(response, ensure_ascii=False))
        return 0 if response.get('status') == 'ok' else 1
    if response.get('status') == 'ok':
        print(response.get('response', ''))
        for image in response.get('images') or []:
            print(image.get('src', ''))
        return 0
    elif response.get('status') == 'timeout':
        print(f"[Timeout] {response.get('response', '')}")
        return 1
    else:
        print(f"[Error] {response.get('error', 'Unknown error')}")
        return 1


# ==================== 服务端核心逻辑 ====================

INPUT_SELECTORS = [
    'textarea',
    'div[contenteditable="true"]',
    '[data-testid="text-input"]',
    '[role="textbox"]'
]

SEND_SELECTORS = [
    'button[aria-label="Send"]',
    'button[aria-label="Submit"]',
    'button[aria-label="发送"]',
    'button[aria-label="提交"]',
    'button[data-testid="send-button"]',
    'button[data-testid="chat-submit"]'
]


class GrokBridge:
    def __init__(s, dedicated=True, tab_name=BRIDGE_TAB_NAME):
        s.lock = threading.Lock()
        s.dedicated = dedicated
        s.tab_name = tab_name

    def _osa(self, script, timeout=30):
        r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f'osascript: {r.stderr.strip()[:200]}')
        return r.stdout.strip()

    def _js(self, js, timeout=30):
        esc = js.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        if not self.dedicated:
            return self._osa(f'tell application "Safari" to do JavaScript "{esc}" in current tab of front window', timeout)
        marker = self.tab_name.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''
tell application "Safari"
    if (count of windows) = 0 then
        make new document with properties {{URL:"{GROK_URL}"}}
        delay 4
        do JavaScript "window.name=\\"{marker}\\"" in current tab of front window
    end if
    repeat with w in windows
        repeat with t in tabs of w
            try
                if (do JavaScript "window.name" in t) is "{marker}" then
                    return do JavaScript "{esc}" in t
                end if
            end try
        end repeat
    end repeat
    set targetWindow to front window
    set targetTab to make new tab at end of tabs of targetWindow with properties {{URL:"{GROK_URL}"}}
    delay 4
    do JavaScript "window.name=\\"{marker}\\"" in targetTab
    return do JavaScript "{esc}" in targetTab
end tell
'''
        return self._osa(script, timeout)

    def _ensure_grok(self):
        try:
            url = self._js('location.href')
        except:
            url = ''
        if 'grok.com' not in url:
            self._js(f'location.href={json.dumps(GROK_URL)}')
            time.sleep(4)

    def _find_input(self):
        for sel in INPUT_SELECTORS:
            r = self._js(f"""(() => {{
                const visible = el => {{
                    if (!el || el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
                }};
                return [...document.querySelectorAll({json.dumps(sel)})].some(visible);
            }})()""")
            if r == 'true':
                return sel
        return None

    def _wait_ready(self, timeout=20):
        start = time.time()
        while time.time() - start < timeout:
            sel = self._find_input()
            if sel:
                return sel
            time.sleep(0.5)
        return None

    def _type_and_send(self, text, input_sel):
        selector = json.dumps(input_sel)
        payload = json.dumps(text, ensure_ascii=False)
        self._js(f"""(() => {{
            const visible = el => {{
                if (!el || el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
            }};
            const el = [...document.querySelectorAll({selector})].find(visible);
            if (!el) return 'NO';
            el.focus();
            const text = {payload};
            if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {{
                el.value = '';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }} else {{
                el.textContent = '';
            }}
            document.execCommand('insertText', false, text);
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            return 'OK';
        }})()""")
        time.sleep(0.5)

        for btn_sel in SEND_SELECTORS:
            r = self._js(f"""(() => {{
                const visible = el => {{
                    if (!el || el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
                }};
                const b = [...document.querySelectorAll({json.dumps(btn_sel)})].find(visible);
                if (b) {{ b.click(); return 'OK' }}
                return 'NO'
            }})()""")
            if 'OK' in str(r):
                return True

        # Fallback
        r = self._js("""(() => {
            const bs = [...document.querySelectorAll('button')];
            const b = bs.find(x => /send|发送|提交|submit/i.test(x.textContent || x.ariaLabel || ''));
            if (b && !b.disabled) { b.click(); return 'OK' }
            return 'NO'
        })()""")
        if 'OK' in str(r):
            return True

        # Last resort
        self._js(f"document.querySelector({selector})?.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }}))")
        return True

    def _get_body(self):
        return self._js('document.body.innerText', timeout=15)

    def _get_last_assistant(self):
        return self._js("""(() => {
            const nodes = [
                ...document.querySelectorAll('[data-testid="assistant-message"] .response-content-markdown'),
                ...document.querySelectorAll('[data-testid="assistant-message"]')
            ].filter(el => (el.innerText || '').trim());
            const el = nodes[nodes.length - 1];
            return el ? el.innerText : '';
        })()""", timeout=15)

    def _get_images(self):
        try:
            raw = self._js("""(() => {
                const seen = new Set();
                return JSON.stringify([...document.images]
                .filter(img => {
                    const style = window.getComputedStyle(img);
                    return img.src && style.display !== 'none' && style.visibility !== 'hidden' && img.getClientRects().length > 0;
                })
                .map(img => {
                    const src = img.currentSrc || img.src || '';
                    const isData = src.startsWith('data:');
                    return {
                        src: isData ? src.slice(0, 80) + '...' : src,
                        data_uri: isData,
                        alt: img.alt || '',
                        width: img.naturalWidth || img.width,
                        height: img.naturalHeight || img.height
                    };
                })
                .filter(img => {
                    const alt = (img.alt || '').toLowerCase();
                    const src = img.src || '';
                    if (alt === 'pfp' || alt.includes('company logo') || alt.includes('powered by')) return false;
                    if (src.includes('cookielaw.org')) return false;
                    const key = img.src + '|' + img.alt + '|' + img.width + 'x' + img.height;
                    if (seen.has(key)) return false;
                    seen.add(key);
                    return true;
                })
                .filter(x => x.width >= 128 || x.height >= 128)
                .slice(-12));
            })()""", timeout=15)
            return json.loads(raw)
        except Exception:
            return []

    def _clean(self, text):
        for m in ['\nAsk anything', '\nDeepSearch', '\nThink Harder', '\nThink\n', '\nAttach', '\nGrok', '\nFast\n', '\nAuto\n', '\nUpgrade to']:
            i = text.rfind(m)
            if i > 0:
                text = text[:i]
        text = re.sub(r'(?m)^Thought for [0-9]+(\.[0-9]+)?s\s*$', '', text)
        text = re.sub(r'\n+[0-9]+(\.[0-9]+)?\s*(ms|s)\s*(\n.*)?$', '', text, flags=re.S)
        text = re.sub(r'\n[0-9]+(\.[0-9]+)?s\n', '\n', text)
        text = re.sub(r'\n(Share|Compare|Make it|Explain|Toggle|Like|Dislike).*', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _extract(self, body, prompt):
        marker = prompt[:60]
        if prompt in body:
            after = body.rsplit(prompt, 1)[-1]
        elif len(prompt) > 120 and prompt[-80:] in body:
            after = body.rsplit(prompt[-80:], 1)[-1]
        elif marker in body:
            after = body.rsplit(marker, 1)[-1]
            rest = prompt[len(marker):]
            if rest and after.startswith(rest):
                after = after[len(rest):]
            else:
                for line in reversed([x.strip() for x in prompt.splitlines()]):
                    if len(line) >= 20 and line in after:
                        after = after.rsplit(line, 1)[-1]
                        break
        else:
            after = body
        return self._clean(after)

    def chat(self, prompt, timeout=120):
        with self.lock:
            return self._chat(prompt, timeout)

    def _chat(self, prompt, timeout):
        try:
            self._ensure_grok()
            sel = self._wait_ready()
            if not sel:
                return {'status': 'error', 'error': 'input not found'}
            body_before = self._get_body()
            assistant_before = self._clean(self._get_last_assistant())
            self._type_and_send(prompt, sel)

            start = time.time()
            last = ''
            last_assistant = ''
            stable = 0
            marker = prompt[:60]
            while time.time() - start < timeout:
                time.sleep(2)
                body = self._get_body()
                assistant = self._clean(self._get_last_assistant())
                if marker in body and assistant and assistant != assistant_before and assistant == last_assistant:
                    stable += 1
                    if stable >= 3:
                        return {'status': 'ok', 'response': assistant, 'elapsed': round(time.time() - start, 1)}
                elif marker in body and body != body_before and body == last:
                    stable += 1
                    if stable >= 3:
                        return {'status': 'ok', 'response': self._extract(body, prompt), 'elapsed': round(time.time() - start, 1)}
                else:
                    stable = 0
                last = body
                last_assistant = assistant
            resp = self._extract(last, prompt) if last else ''
            if last_assistant and last_assistant != assistant_before:
                return {'status': 'timeout', 'response': last_assistant, 'elapsed': round(time.time() - start, 1)}
            if last and marker in last and resp.strip():
                return {'status': 'timeout', 'response': resp, 'elapsed': round(time.time() - start, 1)}
            return {'status': 'error', 'error': 'prompt not observed after send', 'elapsed': round(time.time() - start, 1)}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def history(self):
        try:
            body = self._get_body()
            return {'status': 'ok', 'content': self._clean(body), 'raw_length': len(body)}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def health(self):
        try:
            url = self._js('location.href')
            return {'status': 'ok', 'url': url, 'on_grok': 'grok.com' in url, 'version': VERSION, 'dedicated_tab': self.dedicated, 'tab_name': self.tab_name if self.dedicated else None, 'capabilities': CAPABILITIES}
        except:
            return {'status': 'error', 'error': 'safari not reachable', 'version': VERSION, 'dedicated_tab': self.dedicated, 'capabilities': CAPABILITIES}

    def current_model(self):
        try:
            model = self._js("""(() => {
                const b = [...document.querySelectorAll('button')].find(x => (x.getAttribute('aria-label') || '') === 'Model select');
                return b ? (b.innerText || b.textContent || '').trim() : '';
            })()""")
            return {'status': 'ok', 'model': model or None}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def set_model(self, model):
        try:
            wanted = model.lower()
            self._js("""(() => {
                const b = [...document.querySelectorAll('button')].find(x => (x.getAttribute('aria-label') || '') === 'Model select');
                if (b) b.click();
                return b ? 'OK' : 'NO_MODEL_BUTTON';
            })()""")
            time.sleep(0.8)
            result = self._js(f"""(() => {{
                const wanted = {json.dumps(wanted)};
                const visible = el => {{
                    if (!el || el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
                }};
                const candidates = [...document.querySelectorAll('button,[role="option"],[role="menuitem"],[data-radix-collection-item]')].filter(visible);
                const hit = candidates.find(el => ((el.innerText || el.textContent || '').trim().toLowerCase()).includes(wanted));
                if (!hit) return JSON.stringify({{status:'error', error:'model option not found'}});
                hit.click();
                return JSON.stringify({{status:'ok'}});
            }})()""")
            data = json.loads(result)
            if data.get('status') != 'ok':
                return data
            time.sleep(0.8)
            current = self.current_model()
            current['requested'] = model
            return current
        except Exception as e:
            return {'status': 'error', 'error': str(e), 'requested': model}

    def navigate_mode(self, mode):
        try:
            mode = mode.strip()
            result = self._click_text(mode, selector='a,[role="button"],button')
            if result.get('status') == 'ok':
                time.sleep(1)
                result['url'] = self._js('location.href')
            return result
        except Exception as e:
            return {'status': 'error', 'error': str(e), 'target': mode}

    def open_project(self, name):
        try:
            result = self._click_text(name, selector='a,[role="button"],button,div')
            if result.get('status') == 'ok':
                time.sleep(1)
                result['url'] = self._js('location.href')
            return result
        except Exception as e:
            return {'status': 'error', 'error': str(e), 'target': name}

    def _click_text(self, text, selector='a,button,[role="button"]'):
        target = text.strip().lower()
        result = self._js(f"""(() => {{
            const target = {json.dumps(target)};
            const visible = el => {{
                if (!el || el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
            }};
            const nodes = [...document.querySelectorAll({json.dumps(selector)})].filter(visible);
            const hit = nodes.find(el => ((el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase()) === target)
                || nodes.find(el => ((el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase()).includes(target));
            if (!hit) return JSON.stringify({{status:'error', error:'target not found', target}});
            hit.click();
            return JSON.stringify({{status:'ok', target, clicked:(hit.innerText || hit.textContent || hit.getAttribute('aria-label') || '').trim()}});
        }})()""")
        return json.loads(result)

    def imagine(self, prompt, timeout=180):
        nav = self.navigate_mode('Imagine')
        if nav.get('status') != 'ok':
            return nav
        before = {img.get('src') for img in self._get_images()}
        sel = self._wait_ready()
        if not sel:
            return {'status': 'error', 'error': 'input not found', 'mode': 'Imagine', 'images': self._get_images()}
        self._type_and_send(prompt, sel)
        start = time.time()
        last_images = []
        while time.time() - start < timeout:
            time.sleep(5)
            images = self._get_images()
            new_images = [img for img in images if img.get('src') not in before]
            if new_images:
                return {'status': 'ok', 'response': '', 'mode': 'Imagine', 'images': new_images, 'elapsed': round(time.time() - start, 1)}
            last_images = images
        return {'status': 'timeout', 'response': '', 'mode': 'Imagine', 'images': last_images, 'elapsed': round(time.time() - start, 1)}

    def state(self):
        h = self.health()
        if h.get('status') == 'ok':
            h['model'] = self.current_model().get('model')
        return h


# ==================== HTTP 服务器 ====================

b = None

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        if self.path == '/chat':
            p = d.get('prompt', '')
            to = d.get('timeout', 120)
            ts = time.strftime('%H:%M:%S')
            print(f'[{ts}] >> {p[:80]}', flush=True)
            try:
                r = b.chat(p, to)
                self._j(200, r)
                print(f'[{ts}] << [{r.get("status")}] {str(r.get("response", r.get("error", "")))[:80]}', flush=True)
            except Exception as e:
                self._j(500, {'error': str(e), 'status': 'error'})
        elif self.path == '/new':
            try:
                with b.lock:
                    b._js(f'location.href={json.dumps(GROK_URL)}')
                    time.sleep(3)
                self._j(200, {'status': 'ok'})
            except Exception as e:
                self._j(500, {'error': str(e), 'status': 'error'})
        elif self.path == '/model':
            model = d.get('model', '')
            if not model:
                self._j(400, {'status': 'error', 'error': 'model is required'})
            else:
                with b.lock:
                    self._j(200, b.set_model(model))
        elif self.path == '/mode':
            mode = d.get('mode', '')
            if not mode:
                self._j(400, {'status': 'error', 'error': 'mode is required'})
            else:
                with b.lock:
                    self._j(200, b.navigate_mode(mode))
        elif self.path == '/project':
            name = d.get('name', '')
            if not name:
                self._j(400, {'status': 'error', 'error': 'name is required'})
            else:
                with b.lock:
                    self._j(200, b.open_project(name))
        elif self.path == '/imagine':
            p = d.get('prompt', '')
            to = d.get('timeout', 180)
            if not p:
                self._j(400, {'status': 'error', 'error': 'prompt is required'})
            else:
                with b.lock:
                    self._j(200, b.imagine(p, to))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/health':
            self._j(200, b.health())
        elif self.path == '/state':
            self._j(200, b.state())
        elif self.path == '/model':
            self._j(200, b.current_model())
        elif self.path == '/images':
            self._j(200, {'status': 'ok', 'images': b._get_images()})
        elif self.path == '/version':
            self._j(200, {'status': 'ok', 'version': VERSION})
        elif self.path == '/capabilities':
            self._j(200, capabilities())
        elif self.path == '/history':
            try:
                self._j(200, b.history())
            except Exception as e:
                self._j(500, {'error': str(e), 'status': 'error'})
        else:
            self.send_response(404)
            self.end_headers()

    def _j(self, c, d):
        self.send_response(c)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(d, ensure_ascii=False).encode())

    def log_message(self, *a):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server(port, dedicated=True):
    global b
    b = GrokBridge(dedicated=dedicated)
    print(f'Grok Bridge {VERSION} listening on :{port}', flush=True)
    mode = f'dedicated tab: {BRIDGE_TAB_NAME}' if dedicated else 'shared current tab'
    print(f'Safari 开发者选项必须开启：允许来自 Apple Events 的 JavaScript ({mode})', flush=True)
    ThreadedHTTPServer(('0.0.0.0', port), H).serve_forever()


# ==================== CLI 客户端 ====================

def post_json(server_url, path, payload, timeout):
    """ 通过 HTTP 调用服务器执行 chat """
    import urllib.request
    import urllib.error

    server_url = server_url.rstrip('/')
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f'{server_url}{path}', data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def get_json(server_url, path, timeout):
    import urllib.request

    server_url = server_url.rstrip('/')
    with urllib.request.urlopen(f'{server_url}{path}', timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def run_chat(prompt, server_url, timeout, json_output=False, new_chat=False):
    """ 通过 HTTP 调用服务器执行 chat """
    import urllib.error

    try:
        if new_chat:
            post_json(server_url, '/new', {}, 15)
        result = post_json(server_url, '/chat', {'prompt': prompt, 'timeout': timeout}, timeout + 10)
        return print_response(result, json_output)
    except urllib.error.URLError as e:
        print(f"[Error] 无法连接到 server: {server_url} ({e})", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_health(server_url, json_output=False):
    try:
        result = get_json(server_url, '/health', 10)
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"{result.get('status')} version={result.get('version')} on_grok={result.get('on_grok')} url={result.get('url', '')}")
        return 0 if result.get('status') == 'ok' else 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_state(server_url, json_output=False):
    try:
        result = get_json(server_url, '/state', 10)
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"{result.get('status')} version={result.get('version')} model={result.get('model')} dedicated_tab={result.get('dedicated_tab')} url={result.get('url', '')}")
        return 0 if result.get('status') == 'ok' else 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_new(server_url, json_output=False):
    try:
        result = post_json(server_url, '/new', {}, 15)
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(result.get('status', 'unknown'))
        return 0 if result.get('status') == 'ok' else 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_model(server_url, model=None, json_output=False):
    try:
        result = post_json(server_url, '/model', {'model': model}, 30) if model else get_json(server_url, '/model', 10)
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        elif model:
            print(f"{result.get('status')} requested={result.get('requested', model)} model={result.get('model')} {result.get('error', '')}".strip())
        else:
            print(result.get('model') or result.get('error') or 'unknown')
        return 0 if result.get('status') == 'ok' else 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_action(server_url, path, payload, json_output=False):
    try:
        result = post_json(server_url, path, payload, 30)
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            if result.get('status') == 'ok':
                print(f"ok {result.get('clicked', result.get('target', ''))} {result.get('url', '')}".strip())
            else:
                print(f"[Error] {result.get('error', 'Unknown error')}", file=sys.stderr)
        return 0 if result.get('status') == 'ok' else 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_imagine(prompt, server_url, timeout, json_output=False):
    try:
        result = post_json(server_url, '/imagine', {'prompt': prompt, 'timeout': timeout}, timeout + 120)
        return print_response(result, json_output)
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def run_images(server_url, json_output=False):
    try:
        result = get_json(server_url, '/images', 15)
        if json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for image in result.get('images') or []:
                print(image.get('src', ''))
        return 0 if result.get('status') == 'ok' else 1
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


def resolve_prompt(args):
    if args.stdin or args.prompt == '-':
        return sys.stdin.read()
    return args.prompt or ''


def main(argv=None):
    parser = argparse.ArgumentParser(description='Grok Bridge - Safari 自动化 Grok CLI')
    parser.add_argument('--port', type=int, default=19998, help='Legacy server port')
    subparsers = parser.add_subparsers(dest='command')

    # server 子命令
    server_parser = subparsers.add_parser('server', help='Start the REST API server')
    server_parser.add_argument('--port', type=int, default=19998, help='Port to listen on')
    server_parser.add_argument('--shared-tab', action='store_true', help='Use Safari current tab instead of a dedicated background tab')

    # chat 子命令
    chat_parser = subparsers.add_parser('chat', help='Send a prompt via CLI (one-shot)')
    chat_parser.add_argument('prompt', nargs='?', help='The prompt to send to Grok, or - to read stdin')
    chat_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    chat_parser.add_argument('--timeout', type=int, default=120, help='Timeout in seconds')
    chat_parser.add_argument('--json', action='store_true', help='Print raw JSON response')
    chat_parser.add_argument('--stdin', action='store_true', help='Read prompt from stdin')
    chat_parser.add_argument('--new', action='store_true', help='Start a new Grok chat before sending')

    # health 子命令
    health_parser = subparsers.add_parser('health', help='Check a bridge server')
    health_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    health_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    # state 子命令
    state_parser = subparsers.add_parser('state', help='Show bridge state including model and tab mode')
    state_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    state_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    # new 子命令
    new_parser = subparsers.add_parser('new', help='Start a new Grok chat')
    new_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    new_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    # model 子命令
    model_parser = subparsers.add_parser('model', help='Show or switch Grok model (experimental)')
    model_parser.add_argument('model', nargs='?', help='Model text to select, e.g. "Grok 4.3"')
    model_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    model_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    # mode 子命令
    mode_parser = subparsers.add_parser('mode', help='Navigate Grok mode, e.g. Chat or Imagine (experimental)')
    mode_parser.add_argument('mode', help='Mode text to click')
    mode_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    mode_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    # project 子命令
    project_parser = subparsers.add_parser('project', help='Open a Grok project/sidebar item by name (experimental)')
    project_parser.add_argument('name', help='Project text to click')
    project_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    project_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    # imagine 子命令
    imagine_parser = subparsers.add_parser('imagine', help='Generate an image through Grok Imagine (experimental)')
    imagine_parser.add_argument('prompt', nargs='?', help='Image prompt, or - to read stdin')
    imagine_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    imagine_parser.add_argument('--timeout', type=int, default=180, help='Timeout in seconds')
    imagine_parser.add_argument('--json', action='store_true', help='Print raw JSON response')
    imagine_parser.add_argument('--stdin', action='store_true', help='Read prompt from stdin')

    # images 子命令
    images_parser = subparsers.add_parser('images', help='List visible image URLs from the bridge tab')
    images_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    images_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    args = parser.parse_args(argv)

    if args.command is None:
        return run_server(args.port)

    if args.command == 'server':
        return run_server(args.port, dedicated=not args.shared_tab)

    elif args.command == 'chat':
        config = load_config()
        server_url = args.server or config['server']
        prompt = resolve_prompt(args).strip()
        if not prompt:
            print('[Error] prompt is required', file=sys.stderr)
            return 2
        return run_chat(prompt, server_url, args.timeout, args.json, args.new)

    elif args.command == 'health':
        config = load_config()
        return run_health(args.server or config['server'], args.json)

    elif args.command == 'state':
        config = load_config()
        return run_state(args.server or config['server'], args.json)

    elif args.command == 'new':
        config = load_config()
        return run_new(args.server or config['server'], args.json)

    elif args.command == 'model':
        config = load_config()
        return run_model(args.server or config['server'], args.model, args.json)

    elif args.command == 'mode':
        config = load_config()
        return run_action(args.server or config['server'], '/mode', {'mode': args.mode}, args.json)

    elif args.command == 'project':
        config = load_config()
        return run_action(args.server or config['server'], '/project', {'name': args.name}, args.json)

    elif args.command == 'imagine':
        config = load_config()
        prompt = resolve_prompt(args).strip()
        if not prompt:
            print('[Error] prompt is required', file=sys.stderr)
            return 2
        return run_imagine(prompt, args.server or config['server'], args.timeout, args.json)

    elif args.command == 'images':
        config = load_config()
        return run_images(args.server or config['server'], args.json)

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
