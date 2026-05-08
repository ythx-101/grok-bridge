# grok-bridge v3.1.1

Talk to Grok via Safari automation — CLI tool + REST API.

`v3.1.1` 是当前可发布版本：保留本机 loopback + 后台专用 tab 的稳定边界，并包含 grok.com 中文提交按钮支持。

## 架构
- Safari AppleScript `do JavaScript` = CDP Runtime.evaluate
- `document.execCommand('insertText')` = 输入（绕过 React 受控组件）
- JS `button.click()` = 提交（支持 `Send` / `Submit` / `发送` / `提交`，不依赖 System Events 权限）
- 默认使用 `window.name="grok-bridge-agent"` 标记的 Safari 后台专用 tab；`--shared-tab` 仅用于手动调试当前 tab。
- **默认不需要辅助功能权限**；只有显式 `--foreground-fallback` 才允许激活 Safari 并用 System Events Enter。

## 用法

### REST API 模式（推荐）
```bash
# 先检查本机 Safari / Grok 环境
python3 scripts/grok_bridge.py --doctor

# 在 Mac 上启动服务
python3 scripts/grok_bridge.py --bind 127.0.0.1 --port 19998

# 默认后台专用 tab；需要手动驱动当前 Safari tab 时再加 --shared-tab

# 本机调用
curl -X POST http://127.0.0.1:19998/chat \
  -d '{"prompt":"你好","timeout":120}'

# 其他端点
GET  /health   — 健康检查
GET  /history  — 读取当前页面对话
POST /new      — 新建对话
POST /chat     — 发送问题
```

### 部署边界

稳定入口是 Mac 本机 `127.0.0.1:19998`。这是有意收窄：bridge 会驱动本机已登录的 Safari/Grok 会话，不应直接暴露到 LAN、Tailscale 或公网地址。

远端 Claw/agent 通过 SSH tunnel 或受控 proxy 访问：

```bash
# 在远端 agent host 上建立 tunnel
ssh -N -L 19998:127.0.0.1:19998 user@mac

# 远端随后访问自己的 loopback
curl -X POST http://127.0.0.1:19998/chat \
  -d '{"prompt":"你好","timeout":120}'
```

只有在明确完成网络 ACL / 防火墙隔离时，才显式使用 `--bind <trusted-interface-ip>`；不要把 `0.0.0.0` 当作 stable/release 默认策略。

### CLI 模式（旧版 bash，备用）
```bash
MAC_SSH="ssh user@mac" bash scripts/grok_chat.sh "your question"
```

## Reviewer smoke / review gate

先做只读检查，再决定是否需要真实发问：

```bash
python3 scripts/grok_bridge.py --help
python3 scripts/grok_bridge.py --doctor
curl -s http://127.0.0.1:19998/health
curl -s http://127.0.0.1:19998/history
lsof -nP -iTCP:19998 -sTCP:LISTEN
# if a listener exists, capture its PID provenance before trusting the output
ps -p <PID> -o pid=,ppid=,lstart=,command=
```

注意：
- `bash scripts/grok_chat.sh --help` 不是只读命令；当前脚本会把第一个位置参数当作 prompt 并触发 Safari 自动化。
- `--doctor` 不发送 prompt，只检查平台、`osascript`、Safari 可达性、bridge tab/current tab 的 Grok URL 和输入框可用性。
- `Address already in use` 应先归类为环境端口冲突，不要直接判成 bridge 逻辑故障。
- 如果 `19998` 上已经有 listener，补抓 `ps -p <PID> -o pid=,ppid=,lstart=,command=`，否则 reviewer 无法判断 `/health` / `/history` 是当前 checkout 还是旧进程返回的证据。
- 统一证据格式 / 错误分类 / owner 划分见 `docs/review-playbook.md`。

## 前置条件
1. Safari > 设置 > 高级 > 显示"网页开发者"功能 ✓
2. Safari > 开发 > 允许来自 Apple Events 的 JavaScript ✓
3. Safari 已登录 grok.com（SuperGrok 推荐）

## 文件
- `scripts/grok_bridge.py` — REST API 服务（v3.1.1, stdlib only）
- `scripts/grok_chat.sh` — CLI 工具（v3, bash + CGEvent）

## 设计决策（AG Opus 的判断）
> Safari 不支持标准 Chrome DevTools Protocol。macOS 上控制 Safari 最可靠的方式是 AppleScript + JavaScript 注入。
