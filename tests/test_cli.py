import json
import importlib.util
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'grok_bridge.py'
SPEC = importlib.util.spec_from_file_location('grok_bridge', SCRIPT)
grok_bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grok_bridge)


class MockBridge(BaseHTTPRequestHandler):
    last_prompt = None

    def do_GET(self):
        if self.path == '/health':
            self._json({'status': 'ok', 'version': 'test', 'on_grok': True, 'url': 'https://grok.com/', 'dedicated_tab': True})
        elif self.path == '/state':
            self._json({'status': 'ok', 'version': 'test', 'on_grok': True, 'url': 'https://grok.com/', 'dedicated_tab': True, 'model': 'Grok Test'})
        elif self.path == '/model':
            self._json({'status': 'ok', 'model': 'Grok Test'})
        elif self.path == '/images':
            self._json({'status': 'ok', 'images': [{'src': 'https://example.com/existing.png'}]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        payload = json.loads(self.rfile.read(length) or b'{}')
        if self.path == '/chat':
            MockBridge.last_prompt = payload.get('prompt')
            self._json({'status': 'ok', 'response': 'echo:' + MockBridge.last_prompt})
        elif self.path == '/new':
            self._json({'status': 'ok'})
        elif self.path == '/model':
            self._json({'status': 'ok', 'requested': payload.get('model'), 'model': payload.get('model')})
        elif self.path == '/mode':
            self._json({'status': 'ok', 'target': payload.get('mode'), 'clicked': payload.get('mode')})
        elif self.path == '/project':
            self._json({'status': 'ok', 'target': payload.get('name'), 'clicked': payload.get('name')})
        elif self.path == '/imagine':
            self._json({'status': 'ok', 'response': 'made image', 'images': [{'src': 'https://example.com/image.png'}]})
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), MockBridge)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def run_cli(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=input_text,
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=10,
        )

    def test_chat_json(self):
        result = self.run_cli('chat', 'hello', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['response'], 'echo:hello')

    def test_chat_stdin_dash(self):
        result = self.run_cli('chat', '-', '--server', self.url, input_text='from stdin')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('echo:from stdin', result.stdout)

    def test_chat_new_posts_new_before_chat(self):
        result = self.run_cli('chat', 'fresh', '--new', '--server', self.url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('echo:fresh', result.stdout)

    def test_health_json(self):
        result = self.run_cli('health', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['version'], 'test')

    def test_state_json(self):
        result = self.run_cli('state', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['model'], 'Grok Test')

    def test_model_get_and_set(self):
        result = self.run_cli('model', '--server', self.url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Grok Test', result.stdout)
        result = self.run_cli('model', 'Grok 4.3', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['requested'], 'Grok 4.3')

    def test_mode_and_project(self):
        result = self.run_cli('mode', 'Imagine', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['clicked'], 'Imagine')
        result = self.run_cli('project', 'My Project', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['clicked'], 'My Project')

    def test_imagine_json(self):
        result = self.run_cli('imagine', 'a calm terminal UI', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['images'][0]['src'], 'https://example.com/image.png')

    def test_images_json(self):
        result = self.run_cli('images', '--server', self.url, '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['images'][0]['src'], 'https://example.com/existing.png')

    def test_connection_failure_is_nonzero(self):
        result = self.run_cli('chat', 'hello', '--server', 'http://127.0.0.1:1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[Error]', result.stderr)

    def test_extract_strips_long_prompt_by_last_line(self):
        prompt = (
            'Dogfood project 3: summarize this git diff into release notes.\n'
            'diff --git a/file.py b/file.py\n'
            '+added agent-friendly json output'
        )
        body = (
            'Sidebar\n'
            + prompt[:60]
            + prompt[60:].replace('release notes', 'release  notes')
            + '\n\n- Added JSON output for agents'
        )
        self.assertEqual(grok_bridge.GrokBridge()._extract(body, prompt), '- Added JSON output for agents')


if __name__ == '__main__':
    unittest.main()
