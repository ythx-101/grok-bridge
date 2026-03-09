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
import argparse
import hmac
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

GROK_URL='https://x.com/i/grok'
VERSION='v1.1'
DEFAULT_HOST='127.0.0.1'
DEFAULT_PORT=19998
MAX_BODY_BYTES=64*1024
# 多种输入框选择器（grok.com 可能更新 UI）
INPUT_SELECTORS=['textarea','div[contenteditable="true"]','[data-testid="text-input"]','[role="textbox"]']
# 发送按钮选择器
SEND_SELECTORS=[
    'button[aria-label="Send"]',
    'button[data-testid="send-button"]',
    'button[aria-label="Grok something"]',
]
UI_NOISE_LINES={
    'See new posts',
    'Think Harder',
    'Auto',
    'History',
    'Private',
    'Create Images',
    'Edit Image',
    'Latest News',
    'Talk to Grok',
    'Explore',
}


def _is_loopback_host(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_grok_url(url):
    return ('grok.com' in url) or bool(re.search(r'^https?://x\.com/i/grok(?:[/?#]|$)', url))


class GrokBridge:
    def __init__(s,auth_token='',log_content=False):
        s.lock=threading.Lock()
        s.auth_token=auth_token or ''
        s.log_content=log_content
    def _osa(s,script,timeout=30):
        r=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=timeout)
        if r.returncode!=0:raise RuntimeError(f'osascript: {r.stderr.strip()[:200]}')
        return r.stdout.strip()
    def _js(s,js,timeout=30):
        """Execute JS in Safari front tab (≈ CDP Runtime.evaluate)"""
        esc=js.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
        return s._osa(f'tell application "Safari" to do JavaScript "{esc}" in current tab of front window',timeout)
    def _js_json(s,js,timeout=30):
        raw=s._js(js,timeout)
        if not raw:
            return {}
        return json.loads(raw)
    def _normalize_text(s,text):
        text=(text or '').replace('\r','').replace('\u00a0',' ')
        text=re.sub(r'[ \t]+\n','\n',text)
        text=re.sub(r'\n[ \t]+','\n',text)
        text=re.sub(r'[ \t]{2,}',' ',text)
        text=re.sub(r'\n{3,}','\n\n',text)
        return text.strip()
    def _conversation_snapshot(s):
        """Extract visible conversation messages from the Grok main column."""
        js=r"""
(()=> {
  const normalize = (text) => (text || '')
    .replace(/\u00a0/g, ' ')
    .replace(/\r/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  const ignoreExact = new Set([
    'See new posts',
    'Think Harder',
    'Auto',
    'History',
    'Private',
    'Create Images',
    'Edit Image',
    'Latest News',
    'Talk to Grok',
    'Explore',
  ]);
  const ignorePatterns = [
    /^To view keyboard shortcuts/i,
    /^View keyboard shortcuts$/i,
    /^Get access to more features on grok\.com$/i,
    /^New posts are available\./i,
  ];
  const main = document.querySelector('main');
  if (!main) {
    return JSON.stringify({status: 'error', error: 'main_not_found', messages: []});
  }
  const mainRect = main.getBoundingClientRect();
  const raw = [];
  for (const el of Array.from(main.querySelectorAll('*'))) {
    const text = normalize(el.innerText);
    if (!text || text.length > 12000) continue;
    if (Array.from(el.children).some((child) => normalize(child.innerText))) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    if (el.closest('textarea, input, button, [role="button"], nav, header, aside')) continue;
    if (ignoreExact.has(text)) continue;
    if (ignorePatterns.some((pattern) => pattern.test(text))) continue;
    raw.push({
      text,
      top: Math.round(rect.top),
      left: Math.round(rect.left),
      bottom: Math.round(rect.bottom),
      height: Math.round(rect.height),
      width: Math.round(rect.width),
    });
  }
  raw.sort((a, b) => a.top - b.top || a.left - b.left || a.text.localeCompare(b.text));
  const deduped = [];
  const seen = new Set();
  for (const item of raw) {
    const key = [item.text, item.top, item.left].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }
  const lefts = deduped.map((item) => item.left).sort((a, b) => a - b);
  let userThreshold = mainRect.left + (mainRect.width * 0.58);
  if (lefts.length >= 2) {
    const minLeft = lefts[0];
    const maxLeft = lefts[lefts.length - 1];
    if ((maxLeft - minLeft) >= 120) {
      userThreshold = (minLeft + maxLeft) / 2;
    }
  }
  const rows = [];
  for (const item of deduped) {
    const role = item.left > userThreshold ? 'user' : 'assistant';
    const last = rows[rows.length - 1];
    const sameRow = last &&
      last.role === role &&
      Math.abs(item.top - last.top) <= 10 &&
      Math.abs(item.left - last.left) <= 120;
    if (sameRow) {
      if (!last.fragments.includes(item.text)) last.fragments.push(item.text);
      last.bottom = Math.max(last.bottom, item.bottom);
      last.height = Math.max(last.height, item.height);
      continue;
    }
    rows.push({
      role,
      top: item.top,
      left: item.left,
      bottom: item.bottom,
      height: item.height,
      fragments: [item.text],
    });
  }
  const normalizedRows = rows.map((row) => ({
    role: row.role,
    top: row.top,
    left: row.left,
    bottom: row.bottom,
    height: row.height,
    text: normalize(row.fragments.join(' ')),
  })).filter((row) => row.text && !ignoreExact.has(row.text) && !ignorePatterns.some((pattern) => pattern.test(row.text)));
  const messages = [];
  for (const row of normalizedRows) {
    const last = messages[messages.length - 1];
    const canMerge = last &&
      last.role === row.role &&
      (row.top - last.bottom) <= Math.max(48, Math.round(last.avgHeight * 2));
    if (canMerge) {
      last.rows.push(row.text);
      last.bottom = row.bottom;
      last.avgHeight = Math.round((last.avgHeight + row.height) / 2);
      continue;
    }
    messages.push({
      role: row.role,
      top: row.top,
      left: row.left,
      bottom: row.bottom,
      avgHeight: row.height,
      rows: [row.text],
    });
  }
  return JSON.stringify({
    status: 'ok',
    user_threshold: Math.round(userThreshold),
    messages: messages
      .map((message) => ({
        role: message.role,
        text: normalize(message.rows.join('\n')),
        top: message.top,
        left: message.left,
      }))
      .filter((message) => message.text),
  });
})()
"""
        try:
            snapshot=s._js_json(js,timeout=30)
        except Exception:
            return {'status':'error','error':'conversation_snapshot_failed','messages':[]}
        messages=[]
        for message in snapshot.get('messages',[]):
            text=s._normalize_text(message.get('text',''))
            if not text or text in UI_NOISE_LINES:
                continue
            if re.match(r'^Get access to more features on grok\.com$',text,re.I):
                continue
            messages.append({
                'role':message.get('role','assistant'),
                'text':text,
                'top':message.get('top',0),
                'left':message.get('left',0),
            })
        snapshot['messages']=messages
        return snapshot
    def _messages_to_text(s,messages):
        parts=[]
        for message in messages:
            label='User' if message.get('role')=='user' else 'Assistant'
            parts.append(f'{label}: {message.get("text","")}')
        return '\n\n'.join(parts).strip()
    def _find_response_after_prompt(s,messages,prompt,start_index=0):
        norm_prompt=s._normalize_text(prompt)
        candidates=messages[start_index:] if start_index < len(messages) else []
        prompt_index=None
        for idx in range(len(candidates)-1,-1,-1):
            message=candidates[idx]
            if message.get('role')!='user':
                continue
            text=s._normalize_text(message.get('text',''))
            if text==norm_prompt or norm_prompt in text:
                prompt_index=start_index+idx
                break
        if prompt_index is None:
            return ''
        response_parts=[]
        for message in messages[prompt_index+1:]:
            if message.get('role')=='user':
                break
            if message.get('role')=='assistant':
                response_parts.append(message.get('text',''))
        return s._normalize_text('\n\n'.join(p for p in response_parts if p))
    def _ensure_grok(s):
        """Navigate to a Grok chat page if not already there."""
        try:
            url=s._osa('tell application "Safari" to get URL of current tab of front window')
        except:url=''
        if not _is_grok_url(url):
            s._osa(f'tell application "Safari" to set URL of current tab of front window to "{GROK_URL}"')
            time.sleep(4)
    def _find_input(s):
        """Find the input element using multiple selectors"""
        for sel in INPUT_SELECTORS:
            r=s._js(f"!!document.querySelector('{sel}')")
            if r=='true':return sel
        return None
    def _wait_ready(s,timeout=20):
        start=time.time()
        while time.time()-start<timeout:
            sel=s._find_input()
            if sel:return sel
            time.sleep(0.5)
        return None
    def _type_and_send(s,text,input_sel):
        """Type text via JS insertText + click Send (no System Events needed)"""
        # Activate Safari
        s._osa('tell application "Safari" to activate')
        time.sleep(0.3)
        # Focus input and insert text via execCommand (works with React contenteditable)
        safe=text.replace('\\','\\\\').replace("'","\\'").replace('\n','\\n').replace('\r','')
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
            r=s._js(f"(()=>{{const b=document.querySelector('{btn_sel}');if(b&&!b.disabled){{b.click();return'OK'}};return'NO'}})()")
            if 'OK' in str(r):return True
        # Fallback: find button with Send/Submit text
        r=s._js("""(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>/send|发送|submit|grok something|ask grok/i.test(x.textContent||x.ariaLabel||''));if(b&&!b.disabled){b.click();return'OK'}return'NO'})()""")
        if 'OK' in str(r):return True
        # Last resort: dispatch Enter KeyboardEvent on the input
        s._js(f"document.querySelector('{input_sel}')?.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',code:'Enter',keyCode:13,bubbles:true}}))")
        return True
    def chat(s,prompt,timeout=120):
        with s.lock:return s._chat(prompt,timeout)
    def _chat(s,prompt,timeout):
        try:
            s._ensure_grok()
            sel=s._wait_ready()
            if not sel:return{'status':'error','error':'input not found'}
            snapshot_before=s._conversation_snapshot()
            baseline_count=len(snapshot_before.get('messages',[]))
            s._type_and_send(prompt,sel)
            # Poll for response stability
            start=time.time();last='';stable=0
            while time.time()-start<timeout:
                time.sleep(2)
                snapshot=s._conversation_snapshot()
                response=s._find_response_after_prompt(snapshot.get('messages',[]),prompt,start_index=baseline_count)
                # Response appeared and stabilized
                if response and response==last:
                    stable+=1
                    if stable>=3:
                        return{'status':'ok','response':response,'elapsed':round(time.time()-start,1)}
                else:stable=0
                last=response
            # Timeout
            resp=last or ''
            return{'status':'timeout','response':resp,'elapsed':round(time.time()-start,1)}
        except Exception as e:
            return{'status':'error','error':str(e)}
    def history(s):
        try:
            snapshot=s._conversation_snapshot()
            messages=snapshot.get('messages',[])
            return{
                'status':'ok',
                'content':s._messages_to_text(messages),
                'messages':messages,
                'message_count':len(messages),
            }
        except Exception as e:
            return{'status':'error','error':str(e)}
    def health(s):
        try:
            url=s._osa('tell application "Safari" to get URL of current tab of front window')
            page_host=''
            m=re.match(r'^https?://([^/]+)',url)
            if m:page_host=m.group(1)
            return{'status':'ok','page_host':page_host,'on_grok':_is_grok_url(url),'version':VERSION,'auth_enabled':bool(s.auth_token)}
        except:
            return{'status':'error','error':'safari not reachable','version':VERSION,'auth_enabled':bool(s.auth_token)}

bridge=None
class H(BaseHTTPRequestHandler):
    def do_POST(s):
        if not s._authorize():return
        try:
            d=s._read_json()
        except ValueError as e:
            s._j(400,{'status':'error','error':str(e)})
            return
        if s.path=='/chat':
            p=d.get('prompt','');to=d.get('timeout',120)
            ts=time.strftime('%H:%M:%S')
            if bridge.log_content:
                print(f'[{ts}] >> {p[:80]}',flush=True)
            else:
                print(f'[{ts}] >> prompt_chars={len(p)}',flush=True)
            try:
                r=bridge.chat(p,to);s._j(200,r)
                if bridge.log_content:
                    summary=str(r.get("response",r.get("error","")))[:80]
                else:
                    summary=f'response_chars={len(r.get("response",""))}' if "response" in r else r.get("error","")
                print(f'[{ts}] << [{r.get("status")}] {summary}',flush=True)
            except Exception as e:s._j(500,{'error':str(e),'status':'error'})
        elif s.path=='/new':
            try:
                bridge._osa(f'tell application "Safari" to set URL of current tab of front window to "{GROK_URL}"')
                time.sleep(3);s._j(200,{'status':'ok'})
            except Exception as e:s._j(500,{'error':str(e),'status':'error'})
        else:s.send_response(404);s.end_headers()
    def do_GET(s):
        if not s._authorize():return
        if s.path=='/health':s._j(200,bridge.health())
        elif s.path=='/history':
            try:s._j(200,bridge.history())
            except Exception as e:s._j(500,{'error':str(e),'status':'error'})
        else:s.send_response(404);s.end_headers()
    def _authorize(s):
        if not bridge.auth_token:return True
        token=''
        auth=s.headers.get('Authorization','')
        if auth.lower().startswith('bearer '):
            token=auth[7:].strip()
        if not token:
            token=s.headers.get('X-Grok-Bridge-Token','').strip()
        if token and hmac.compare_digest(token,bridge.auth_token):
            return True
        s.send_response(401)
        s.send_header('Content-Type','application/json')
        s.send_header('WWW-Authenticate','Bearer')
        s.end_headers()
        s.wfile.write(json.dumps({'status':'error','error':'unauthorized'},ensure_ascii=False).encode())
        return False
    def _read_json(s):
        raw_len=s.headers.get('Content-Length','0')
        try:
            n=int(raw_len)
        except ValueError:
            raise ValueError('invalid Content-Length')
        if n<0:
            raise ValueError('invalid Content-Length')
        if n>MAX_BODY_BYTES:
            raise ValueError(f'request body too large (max {MAX_BODY_BYTES} bytes)')
        raw=s.rfile.read(n) if n else b'{}'
        try:
            return json.loads(raw or b'{}')
        except json.JSONDecodeError as e:
            raise ValueError(f'invalid JSON: {e.msg}') from e
    def _j(s,c,d):
        s.send_response(c)
        s.send_header('Content-Type','application/json')
        s.end_headers()
        s.wfile.write(json.dumps(d,ensure_ascii=False).encode())
    def log_message(s,*a):pass

class ThreadedHTTPServer(ThreadingMixIn,HTTPServer):
    daemon_threads=True
    allow_reuse_address=True

if __name__=='__main__':
    pa=argparse.ArgumentParser(description='Expose Grok via Safari automation as a local HTTP service.')
    pa.add_argument('--host',default=DEFAULT_HOST,help=f'Bind host (default: {DEFAULT_HOST})')
    pa.add_argument('--port',type=int,default=DEFAULT_PORT,help=f'Bind port (default: {DEFAULT_PORT})')
    pa.add_argument('--token',help='Bearer token for HTTP auth; alternatively set GROK_BRIDGE_TOKEN')
    pa.add_argument('--log-content',action='store_true',help='Log prompt/response excerpts for debugging')
    a=pa.parse_args()
    token=a.token or os.environ.get('GROK_BRIDGE_TOKEN','').strip()
    if not _is_loopback_host(a.host) and not token:
        raise SystemExit('Refusing non-loopback bind without auth token. Set --token or GROK_BRIDGE_TOKEN.')
    bridge=GrokBridge(auth_token=token,log_content=a.log_content)
    print(f'Grok Bridge {VERSION} listening on {a.host}:{a.port}',flush=True)
    print('前置: Safari > 开发 > 允许来自 Apple Events 的 JavaScript',flush=True)
    print(f'Auth: {"enabled" if token else "disabled"}',flush=True)
    if not token:
        print('提示: 默认仅绑定本机。若需远程访问，请改用 --host 0.0.0.0 并设置 GROK_BRIDGE_TOKEN。',flush=True)
    if not a.log_content:
        print('Log content: disabled (use --log-content to print prompt/response excerpts)',flush=True)
    ThreadedHTTPServer((a.host,a.port),H).serve_forever()
