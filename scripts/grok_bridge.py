#!/usr/bin/env python3
"""
grok_bridge.py v3.1.1 — Turn Grok in Safari into a local REST API (macOS)

No API key needed. Uses pure Safari JavaScript injection via AppleScript.

Key advantages (v3.1.1):
- Zero Accessibility permissions required
- Local loopback REST API for personal automation
- Dedicated background Safari tab by default
- Robust Chinese/English send-button support
- Cleaner assistant-message extraction for agent use

Preconditions:
  Safari > Settings > Advanced > Show features for web developers ✓
  Safari > Develop > Allow JavaScript from Apple Events ✓

Usage:
  python3 scripts/grok_bridge.py --doctor
  python3 scripts/grok_bridge.py --bind 127.0.0.1 --port 19998
  curl -X POST http://127.0.0.1:19998/chat -d '{"prompt":"hello"}'
"""
import json,time,threading,re,argparse,subprocess,shutil,sys
from http.server import HTTPServer,BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

GROK_URL='https://grok.com'
VERSION='v3.1.1'
BRIDGE_TAB_NAME='grok-bridge-agent'
PREREQ_HINTS=[
    'Run this on macOS with Safari installed.',
    'Open Safari and sign in to https://grok.com.',
    'Enable Safari > Settings > Advanced > Show features for web developers.',
    'Enable Safari > Develop > Allow JavaScript from Apple Events.'
]
# 多种输入框选择器（grok.com 可能更新 UI）。优先真实可见的 ProseMirror 输入框，
# 避免命中页面上用于辅助输入/布局的隐藏 textarea。
INPUT_SELECTORS=[
    '[data-testid="chat-input"] [contenteditable="true"]',
    '.ProseMirror[contenteditable="true"]',
    'div[contenteditable="true"]',
    '[data-testid="text-input"]',
    '[role="textbox"]',
    'textarea'
]
# 发送按钮选择器（支持中英文，防止 grok.com UI 更新导致按钮无法点击）
SEND_SELECTORS=[
    'button[aria-label="Send"]',
    'button[aria-label="Submit"]',
    'button[aria-label="发送"]',
    'button[aria-label="提交"]',
    'button[data-testid="send-button"]',
    'button[type="submit"]'
]
PAGE_CHROME_MARKERS=(
    'Toggle Sidebar','History','Search','New Project','Projects','Today','Yesterday',
    'Imagine','Private','Ask Grok','Enter voice mode','Upgrade to'
)

def clean_text(text):
    """Clean Grok UI artifacts from response-like text."""
    text=text or ''
    text=text.replace('\r\n','\n').replace('\r','\n')
    text=re.sub(r'(?im)^\s*Thought for .*$\n?','',text)
    text=re.sub(r'\n\s*[0-9]+(\.[0-9]+)?\s*(ms|s)\s*\n.*$','\n',text,flags=re.I|re.S)
    for m in['\nAsk anything','\nDeepSearch','\nThink Harder','\nThink\n','\nAttach','\nGrok','\nFast\n','\nAuto\n','\nUpgrade to']:
        i=text.rfind(m)
        if i>0:text=text[:i]
    text=re.sub(r'(?im)^\s*[0-9]+(\.[0-9]+)?\s*(ms|s)\s*$','',text)  # remove "1.3s"/"656ms" timing
    text=re.sub(r'\n(Share|Compare|Make it|Explain|Toggle|Like|Dislike).*','',text)
    text=re.sub(r'\n{3,}','\n\n',text)
    return text.strip()

def looks_like_page_chrome(text):
    """Detect sidebar/homepage text so it is never returned as a model answer."""
    text=text or ''
    if len(text.strip())<80:return False
    hits=sum(1 for marker in PAGE_CHROME_MARKERS if marker in text)
    has_shell=any(marker in text for marker in ('Toggle Sidebar','History','Ask Grok'))
    return has_shell and hits>=4

def _without_prompt(text,prompt):
    if not text or not prompt:return text or ''
    out=text.replace(prompt,'')
    marker=prompt[:60]
    if marker and marker!=prompt:
        out=out.replace(marker,'')
    return out

def _line_delta(before,after):
    before_lines=(before or '').splitlines()
    after_lines=(after or '').splitlines()
    i=0
    while i<len(before_lines) and i<len(after_lines) and before_lines[i]==after_lines[i]:
        i+=1
    return '\n'.join(after_lines[i:])

def extract_response(before,after,prompt):
    """Extract only the answer portion from main-area text before/after a chat send."""
    before=before or ''
    after=after or ''
    candidates=[]
    if prompt:
        marker=prompt[:60]
        if prompt in after:
            candidates.append(after.rsplit(prompt,1)[-1])
        elif marker and marker in after:
            candidates.append(after.rsplit(marker,1)[-1])
    if before and after.startswith(before):
        candidates.append(after[len(before):])
    candidates.append(_line_delta(before,after))
    candidates.append(after)

    seen=set()
    for candidate in candidates:
        candidate=_without_prompt(candidate,prompt)
        cleaned=clean_text(candidate)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if looks_like_page_chrome(cleaned):
            continue
        return cleaned
    return ''

def redact_url(url):
    """Show enough URL context for diagnostics without leaking path/query data."""
    if not url:return ''
    try:
        parsed=urlsplit(url)
        if not parsed.scheme or not parsed.netloc:return '(non-http URL redacted)'
        suffix='/...' if parsed.path not in ('','/') or parsed.query or parsed.fragment else ''
        return f'{parsed.scheme}://{parsed.netloc}{suffix}'
    except Exception:
        return '(URL redacted)'

class GrokBridge:
    def __init__(s,dedicated=True,tab_name=BRIDGE_TAB_NAME,foreground_fallback=False):
        s.lock=threading.Lock()
        s.dedicated=dedicated
        s.tab_name=tab_name
        s.foreground_fallback=foreground_fallback
    def _osa(s,script,timeout=30):
        try:
            r=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError('osascript not found (macOS with Safari is required)')
        if r.returncode!=0:raise RuntimeError(f'osascript: {r.stderr.strip()[:200]}')
        return r.stdout.strip()
    def _js(s,js,timeout=30):
        """Execute JS in Safari front tab (≈ CDP Runtime.evaluate)"""
        esc=js.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')
        if not s.dedicated:
            return s._osa(f'tell application "Safari" to do JavaScript "{esc}" in current tab of front window',timeout)
        marker=s.tab_name.replace('\\','\\\\').replace('"','\\"')
        script=f'''
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
        return s._osa(script,timeout)
    def _ensure_grok(s):
        """Navigate to grok.com if not already there"""
        try:
            url=s._js('location.href')
        except:url=''
        if 'grok.com' not in url:
            s._js(f'location.href={json.dumps(GROK_URL)}')
            time.sleep(4)
    def _find_input(s):
        """Find the first visible input element using multiple selectors."""
        selectors=json.dumps(INPUT_SELECTORS)
        r=s._js(f"""(()=>{{
            const selectors={selectors};
            const visible=(el)=>{{
                if(!el)return false;
                const style=getComputedStyle(el);
                const rect=el.getBoundingClientRect();
                return style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)!==0&&rect.width>1&&rect.height>1;
            }};
            for(const sel of selectors){{
                for(const el of document.querySelectorAll(sel)){{
                    if(visible(el))return sel;
                }}
            }}
            return '';
        }})()""")
        return r or None
    def _wait_ready(s,timeout=20):
        start=time.time()
        while time.time()-start<timeout:
            sel=s._find_input()
            if sel:return sel
            time.sleep(0.5)
        return None
    def _type_and_send(s,text,input_sel):
        """Type text into the visible editor and attempt to submit it."""
        # Focus input and insert text via execCommand (works with React contenteditable).
        # This intentionally avoids Safari activation so bridge calls can run in the background.
        selectors=[input_sel]+[x for x in INPUT_SELECTORS if x!=input_sel]
        selectors_json=json.dumps(selectors)
        text_json=json.dumps(text)
        inserted=s._js(f"""(()=>{{
            const selectors={selectors_json};
            const text={text_json};
            const visible=(el)=>{{
                if(!el)return false;
                const style=getComputedStyle(el);
                const rect=el.getBoundingClientRect();
                return style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)!==0&&rect.width>1&&rect.height>1;
            }};
            let el=null;
            for(const sel of selectors){{
                el=[...document.querySelectorAll(sel)].find(visible);
                if(el)break;
            }}
            if(!el)return'NO_INPUT';
            el.focus();
            if(el.tagName==='TEXTAREA'||el.tagName==='INPUT'){{
                el.value='';
                el.dispatchEvent(new InputEvent('input',{{bubbles:true,inputType:'deleteContentBackward',data:null}}));
                el.value=text;
                el.dispatchEvent(new InputEvent('input',{{bubbles:true,inputType:'insertText',data:text}}));
            }}else{{
                document.execCommand('selectAll',false,null);
                document.execCommand('delete',false,null);
                document.execCommand('insertText',false,text);
                el.dispatchEvent(new InputEvent('input',{{bubbles:true,inputType:'insertText',data:text}}));
            }}
            return'OK';
        }})()""")
        if inserted!='OK':
            return False
        time.sleep(0.5)
        # Try click a visible Send button (JS click, no System Events needed for this path).
        send_selectors=json.dumps(SEND_SELECTORS)
        clicked=s._js(f"""(()=>{{
            const selectors={send_selectors};
            const visible=(el)=>{{
                if(!el)return false;
                const style=getComputedStyle(el);
                const rect=el.getBoundingClientRect();
                return style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)!==0&&rect.width>1&&rect.height>1;
            }};
            for(const sel of selectors){{
                for(const b of document.querySelectorAll(sel)){{
                    if(visible(b)&&!b.disabled){{b.click();return'OK';}}
                }}
            }}
            const bs=[...document.querySelectorAll('button')];
            const b=bs.find(x=>visible(x)&&!x.disabled&&/send|发送|提交|submit/i.test(x.textContent||x.getAttribute('aria-label')||''));
            if(b){{b.click();return'OK';}}
            return'NO';
        }})()""")
        if clicked=='OK':
            return True
        synthetic=s._js(f"""(()=>{{
            const selectors={selectors_json};
            const visible=(el)=>{{
                if(!el)return false;
                const style=getComputedStyle(el);
                const rect=el.getBoundingClientRect();
                return style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)!==0&&rect.width>1&&rect.height>1;
            }};
            let el=null;
            for(const sel of selectors){{
                el=[...document.querySelectorAll(sel)].find(visible);
                if(el)break;
            }}
            if(!el)return'NO_INPUT';
            for(const type of ['keydown','keypress','keyup']){{
                el.dispatchEvent(new KeyboardEvent(type,{{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true}}));
            }}
            return'ENTER';
        }})()""")
        if synthetic=='ENTER' and not s.foreground_fallback:
            return True
        if not s.foreground_fallback:
            return False
        try:
            s._osa('tell application "Safari" to activate',timeout=5)
            time.sleep(0.2)
            s._osa('tell application "System Events" to key code 36',timeout=5)
            return True
        except Exception:
            return False
    def _get_body(s):
        return s._js('document.body.innerText',timeout=15)
    def _get_main_text(s):
        return s._js("""(()=>{
            const el=document.querySelector('main')||document.querySelector('[role="main"]')||document.body;
            return el.innerText||'';
        })()""",timeout=15)
    def _get_assistant_snapshot(s):
        raw=s._js("""(()=>{
            const msgs=[...document.querySelectorAll('[data-testid="assistant-message"]')];
            const msg=msgs[msgs.length-1];
            const content=msg&&(msg.querySelector('.response-content-markdown')||msg);
            return JSON.stringify({count:msgs.length,text:content?(content.innerText||''):''});
        })()""",timeout=15)
        try:
            return json.loads(raw)
        except Exception:
            return{'count':0,'text':raw or ''}
    def _clean(s,text):
        return clean_text(text)
    def _extract(s,before,after,prompt):
        return extract_response(before,after,prompt)
    def chat(s,prompt,timeout=120):
        with s.lock:return s._chat(prompt,timeout)
    def _chat(s,prompt,timeout):
        try:
            s._ensure_grok()
            sel=s._wait_ready()
            if not sel:return{'status':'error','error':'input not found'}
            body_before=s._get_main_text()
            assistant_before=s._get_assistant_snapshot()
            if not s._type_and_send(prompt,sel):
                return{'status':'error','error':'input not found or not writable'}
            # Poll for response stability
            start=time.time();last='';stable=0
            while time.time()-start<timeout:
                time.sleep(2)
                body=s._get_main_text()
                # Response appeared and stabilized
                if body!=body_before and body==last:
                    stable+=1
                    if stable>=3:
                        assistant_after=s._get_assistant_snapshot()
                        response=''
                        if assistant_after.get('count',0)>assistant_before.get('count',0) or assistant_after.get('text')!=assistant_before.get('text'):
                            response=s._clean(assistant_after.get('text',''))
                        if not response:
                            response=s._extract(body_before,body,prompt)
                        if response:
                            return{'status':'ok','response':response,'elapsed':round(time.time()-start,1)}
                        return{'status':'error','error':'response extraction failed','elapsed':round(time.time()-start,1),'raw_length':len(body)}
                else:stable=0
                last=body
            # Timeout
            assistant_after=s._get_assistant_snapshot()
            resp=s._clean(assistant_after.get('text','')) if assistant_after.get('text') else ''
            if not resp:
                resp=s._extract(body_before,last,prompt) if last else ''
            return{'status':'timeout','response':resp,'elapsed':round(time.time()-start,1)}
        except Exception as e:
            return{'status':'error','error':str(e)}
    def history(s):
        try:
            body=s._get_main_text()
            return{'status':'ok','content':s._clean(body),'raw_length':len(body)}
        except Exception as e:
            return{'status':'error','error':str(e)}
    def health(s):
        try:
            url=s._js('location.href')
            on_grok='grok.com' in url
            result={
                'status':'ok',
                'url':redact_url(url),
                'on_grok':on_grok,
                'version':VERSION,
                'dedicated_tab':s.dedicated,
                'tab_name':s.tab_name if s.dedicated else None,
                'foreground_fallback':s.foreground_fallback
            }
            if not on_grok:
                result['next_steps']=['Open https://grok.com in Safari and sign in.']
            return result
        except Exception as e:
            return{'status':'error','error':str(e),'version':VERSION,'next_steps':PREREQ_HINTS}

def doctor(bridge=None,check_input=True):
    """Return actionable local setup diagnostics without sending a prompt."""
    bridge=bridge or GrokBridge()
    checks=[]

    def add(name,ok,detail='',next_steps=None):
        checks.append({'name':name,'ok':bool(ok),'detail':detail,'next_steps':next_steps or []})

    add('macOS',sys.platform=='darwin',sys.platform,[] if sys.platform=='darwin' else [PREREQ_HINTS[0]])
    osa=shutil.which('osascript')
    add('osascript',bool(osa),osa or 'not found',[] if osa else [PREREQ_HINTS[0]])

    url=''
    safari_ok=False
    if osa:
        try:
            url=bridge._osa('tell application "Safari" to get URL of current tab of front window',timeout=10)
            safari_ok=True
            add('Safari reachable',True,redact_url(url) or '(blank tab)')
        except Exception as e:
            add('Safari reachable',False,str(e),PREREQ_HINTS[1:])
    else:
        add('Safari reachable',False,'skipped: osascript is unavailable',PREREQ_HINTS)

    target_url=url
    if safari_ok:
        try:
            target_url=bridge._js('location.href',timeout=15)
        except Exception as e:
            add('Bridge tab reachable',False,str(e),PREREQ_HINTS[2:])
            target_url=''
        else:
            mode='dedicated tab' if getattr(bridge,'dedicated',False) else 'current tab'
            add('Bridge tab reachable',True,f'{mode}: {redact_url(target_url) or "(blank tab)"}')

    on_grok='grok.com' in target_url
    add('grok.com open',on_grok,redact_url(target_url) or 'unknown',[] if on_grok else [PREREQ_HINTS[1]])

    if check_input and safari_ok and on_grok:
        try:
            sel=bridge._wait_ready(timeout=3)
            add('Grok input found',bool(sel),sel or 'input not found',['Wait for grok.com to finish loading, then run --doctor again.'] if not sel else [])
        except Exception as e:
            add('Grok input found',False,str(e),PREREQ_HINTS[2:])
    else:
        add('Grok input found',False,'skipped until Safari is on grok.com',[] if on_grok else [PREREQ_HINTS[1]])

    ok=all(c['ok'] for c in checks)
    return{'status':'ok' if ok else 'error','version':VERSION,'checks':checks}

def format_doctor(result):
    lines=[f'Grok Bridge {result["version"]} doctor: {result["status"]}']
    for c in result['checks']:
        mark='OK' if c['ok'] else 'FAIL'
        lines.append(f'[{mark}] {c["name"]}: {c["detail"]}')
        for step in c.get('next_steps',[]):
            lines.append(f'      - {step}')
    return '\n'.join(lines)

def build_parser():
    pa=argparse.ArgumentParser(
        description='Expose grok.com in Safari as a small REST API.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''examples:
  python3 scripts/grok_bridge.py --doctor
  python3 scripts/grok_bridge.py --bind 127.0.0.1 --port 19998
  curl http://localhost:19998/health
  curl http://localhost:19998/history
  curl -X POST http://localhost:19998/chat -H "Content-Type: application/json" -d '{"prompt":"hello","timeout":60}'
'''
    )
    pa.add_argument('--bind','--host',dest='host',default='127.0.0.1',help='bind address (default: 127.0.0.1)')
    pa.add_argument('--port',type=int,default=19998,help='bind port (default: 19998)')
    pa.add_argument('--shared-tab',action='store_true',help='use Safari current tab instead of the dedicated background bridge tab')
    pa.add_argument('--tab-name',default=BRIDGE_TAB_NAME,help=f'dedicated Safari tab window.name marker (default: {BRIDGE_TAB_NAME})')
    pa.add_argument('--foreground-fallback',action='store_true',help='allow Safari activation + System Events Enter if pure JS submit fails')
    pa.add_argument('--doctor',action='store_true',help='check local Safari/Grok setup and exit')
    pa.add_argument('--json',action='store_true',help='print --doctor output as JSON')
    return pa

b=None
class H(BaseHTTPRequestHandler):
    def do_POST(s):
        d=json.loads(s.rfile.read(int(s.headers.get('Content-Length',0))) or b'{}')
        if s.path=='/chat':
            p=d.get('prompt','');to=d.get('timeout',120)
            ts=time.strftime('%H:%M:%S');print(f'[{ts}] >> {p[:80]}',flush=True)
            try:
                r=b.chat(p,to);s._j(200,r)
                print(f'[{ts}] << [{r.get("status")}] {str(r.get("response",r.get("error","")))[:80]}',flush=True)
            except Exception as e:s._j(500,{'error':str(e),'status':'error'})
        elif s.path=='/new':
            try:
                b._js(f'location.href={json.dumps(GROK_URL)}')
                time.sleep(3);s._j(200,{'status':'ok'})
            except Exception as e:s._j(500,{'error':str(e),'status':'error'})
        else:s.send_response(404);s.end_headers()
    def do_GET(s):
        if s.path=='/health':s._j(200,b.health())
        elif s.path=='/history':
            try:s._j(200,b.history())
            except Exception as e:s._j(500,{'error':str(e),'status':'error'})
        else:s.send_response(404);s.end_headers()
    def _j(s,c,d):s.send_response(c);s.send_header('Content-Type','application/json');s.end_headers();s.wfile.write(json.dumps(d,ensure_ascii=False).encode())
    def log_message(s,*a):pass

class ThreadedHTTPServer(ThreadingMixIn,HTTPServer):daemon_threads=True
if __name__=='__main__':
    a=build_parser().parse_args()
    b=GrokBridge(dedicated=not a.shared_tab,tab_name=a.tab_name,foreground_fallback=a.foreground_fallback)
    if a.doctor:
        result=doctor(b)
        print(json.dumps(result,ensure_ascii=False,indent=2) if a.json else format_doctor(result),flush=True)
        raise SystemExit(0 if result['status']=='ok' else 2)
    print(f'Grok Bridge {VERSION} http://{a.host}:{a.port}',flush=True)
    mode=f'dedicated background tab: {a.tab_name}' if not a.shared_tab else 'shared current Safari tab'
    fallback='enabled' if a.foreground_fallback else 'disabled'
    print(f'Mode: {mode}; foreground fallback: {fallback}',flush=True)
    print('Health:  GET /health    History: GET /history    Chat: POST /chat',flush=True)
    print('Run `python3 scripts/grok_bridge.py --doctor` for setup diagnostics.',flush=True)
    ThreadedHTTPServer((a.host,a.port),H).serve_forever()
