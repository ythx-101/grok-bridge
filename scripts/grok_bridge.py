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

CAPABILITIES = {
    'api': {
        'GET': ['/health', '/history', '/version', '/capabilities'],
        'POST': ['/chat', '/new']
    },
    'cli': {
        'commands': ['server', 'chat', 'health', 'new'],
        'chat_flags': ['--server', '--timeout', '--json', '--stdin', '--new']
    },
    'constraints': ['macOS', 'Safari', 'grok.com login', 'Safari JavaScript from Apple Events']
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
    def __init__(s):
        s.lock = threading.Lock()

    def _osa(self, script, timeout=30):
        r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f'osascript: {r.stderr.strip()[:200]}')
        return r.stdout.strip()

    def _js(self, js, timeout=30):
        esc = js.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        return self._osa(f'tell application "Safari" to do JavaScript "{esc}" in current tab of front window', timeout)

    def _ensure_grok(self):
        try:
            url = self._osa('tell application "Safari" to get URL of current tab of front window')
        except:
            url = ''
        if 'grok.com' not in url:
            self._osa(f'tell application "Safari" to set URL of current tab of front window to "{GROK_URL}"')
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
        self._osa('tell application "Safari" to activate')
        time.sleep(0.3)

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
            url = self._osa('tell application "Safari" to get URL of current tab of front window')
            return {'status': 'ok', 'url': url, 'on_grok': 'grok.com' in url, 'version': VERSION, 'capabilities': CAPABILITIES}
        except:
            return {'status': 'error', 'error': 'safari not reachable', 'version': VERSION, 'capabilities': CAPABILITIES}


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
                b._osa(f'tell application "Safari" to set URL of current tab of front window to "{GROK_URL}"')
                time.sleep(3)
                self._j(200, {'status': 'ok'})
            except Exception as e:
                self._j(500, {'error': str(e), 'status': 'error'})
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/health':
            self._j(200, b.health())
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


def run_server(port):
    global b
    b = GrokBridge()
    print(f'Grok Bridge {VERSION} listening on :{port}', flush=True)
    print('Safari 开发者选项必须开启：允许来自 Apple Events 的 JavaScript', flush=True)
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

    # new 子命令
    new_parser = subparsers.add_parser('new', help='Start a new Grok chat')
    new_parser.add_argument('--server', default=None, help='Server URL (default: http://localhost:19998)')
    new_parser.add_argument('--json', action='store_true', help='Print raw JSON response')

    args = parser.parse_args(argv)

    if args.command is None:
        return run_server(args.port)

    if args.command == 'server':
        return run_server(args.port)

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

    elif args.command == 'new':
        config = load_config()
        return run_new(args.server or config['server'], args.json)

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
