import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import grok_bridge


class FakeBridge:
    def __init__(self, url='https://grok.com/', selector='textarea'):
        self.url = url
        self.selector = selector

    def _osa(self, script, timeout=30):
        return self.url

    def _js(self, script, timeout=30):
        return self.url

    def _wait_ready(self, timeout=20):
        return self.selector


class DoctorTests(unittest.TestCase):
    def test_release_version_is_publishable_patch(self):
        self.assertEqual(grok_bridge.VERSION, 'v3.1.1')

    def test_redact_url_removes_path_query_and_fragment(self):
        redacted = grok_bridge.redact_url('https://grok.com/chat/private-id?q=private#frag')

        self.assertEqual(redacted, 'https://grok.com/...')
        self.assertNotIn('private', redacted)

    @mock.patch.object(grok_bridge.sys, 'platform', 'darwin')
    @mock.patch.object(grok_bridge.shutil, 'which', return_value='/usr/bin/osascript')
    def test_doctor_ok_on_grok(self, _which):
        result = grok_bridge.doctor(FakeBridge())

        self.assertEqual(result['status'], 'ok')
        self.assertTrue(all(check['ok'] for check in result['checks']))

    @mock.patch.object(grok_bridge.sys, 'platform', 'darwin')
    @mock.patch.object(grok_bridge.shutil, 'which', return_value='/usr/bin/osascript')
    def test_doctor_guides_when_safari_not_on_grok(self, _which):
        result = grok_bridge.doctor(FakeBridge(url='https://example.com/'))

        self.assertEqual(result['status'], 'error')
        grok_check = next(c for c in result['checks'] if c['name'] == 'grok.com open')
        self.assertFalse(grok_check['ok'])
        self.assertIn('Open Safari and sign in to https://grok.com.', grok_check['next_steps'])

    @mock.patch.object(grok_bridge.sys, 'platform', 'linux')
    @mock.patch.object(grok_bridge.shutil, 'which', return_value=None)
    def test_doctor_reports_missing_macos_tools(self, _which):
        result = grok_bridge.doctor(FakeBridge())

        self.assertEqual(result['status'], 'error')
        self.assertIn('[FAIL] macOS: linux', grok_bridge.format_doctor(result))

    def test_parser_accepts_bind_alias(self):
        args = grok_bridge.build_parser().parse_args(['--bind','127.0.0.1','--port','19999'])

        self.assertEqual(args.host, '127.0.0.1')
        self.assertEqual(args.port, 19999)
        self.assertFalse(args.shared_tab)
        self.assertFalse(args.foreground_fallback)

    def test_parser_defaults_to_loopback_bind(self):
        args = grok_bridge.build_parser().parse_args([])

        self.assertEqual(args.host, '127.0.0.1')
        self.assertEqual(args.port, 19998)

    def test_parser_accepts_background_tab_options(self):
        args = grok_bridge.build_parser().parse_args([
            '--shared-tab',
            '--foreground-fallback',
            '--tab-name',
            'test-tab',
        ])

        self.assertTrue(args.shared_tab)
        self.assertTrue(args.foreground_fallback)
        self.assertEqual(args.tab_name, 'test-tab')

    def test_send_selectors_cover_chinese_and_english_ui(self):
        selectors = set(grok_bridge.SEND_SELECTORS)

        self.assertIn('button[aria-label="Send"]', selectors)
        self.assertIn('button[aria-label="Submit"]', selectors)
        self.assertIn('button[aria-label="发送"]', selectors)
        self.assertIn('button[aria-label="提交"]', selectors)


class ExtractResponseTests(unittest.TestCase):
    def test_extracts_answer_after_prompt_from_main_text(self):
        before = 'Imagine\nPrivate\n\nAsk Grok\n\n'
        prompt = '请只回复 grok-bridge-ok'
        after = f'{before}{prompt}\ngrok-bridge-ok\nShare\nCompare'

        self.assertEqual(grok_bridge.extract_response(before, after, prompt), 'grok-bridge-ok')

    def test_extracts_answer_from_delta_when_prompt_is_not_rendered(self):
        before = 'Imagine\nPrivate\n\nAsk Grok\n\n'
        after = f'{before}grok-bridge-ok'

        self.assertEqual(grok_bridge.extract_response(before, after, 'hidden prompt'), 'grok-bridge-ok')

    def test_rejects_sidebar_history_page_chrome(self):
        chrome = '\n'.join([
            'Toggle Sidebar',
            'Search',
            'Chat',
            'History',
            'Today',
            'Yesterday',
            'Imagine',
            'Private',
            'Ask Grok',
            'Enter voice mode',
        ]) + '\n' + ('old title ' * 20)

        self.assertTrue(grok_bridge.looks_like_page_chrome(chrome))
        self.assertEqual(grok_bridge.extract_response('', chrome, '请只回复 grok-bridge-ok'), '')

    def test_cleans_thinking_timing_and_followup_suggestions(self):
        raw = 'Thought for 5s\ngrok-bridge-ok\n\n656ms\n请详细解释 grok-bridge-ok\n介绍 xAI 最新项目'

        self.assertEqual(grok_bridge.clean_text(raw), 'grok-bridge-ok')


if __name__ == '__main__':
    unittest.main()
