---
name: browser-cdp
description: |
  通过 CDP 直连 Chrome/Edge/QQ浏览器，可复用登录态访问网页。使用场景：访问需登录的网页（邮箱、社媒等）、获取页面结构化信息、自动化浏览器操作、批量完成网页任务。当使用browser工具无法获取内容时，使用本 skill。
metadata:
  openclaw:
    emoji: "🌐"
---

# Browser CDP — 浏览器自动化

## 依赖

> **首次使用前必须安装**：`cdp_client.py`、`browser_actions.py`、`page_snapshot.py` 依赖 `websockets` 库。
> 若未安装，CDP 通信会失败（`RuntimeError: Missing 'websockets' package`）。

```bash
pip install websockets
```

**自动检测**：在脚本开头加入以下代码可以自动安装缺失依赖：

```python
import subprocess, importlib
if importlib.util.find_spec('websockets') is None:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'websockets', '-q'])
```

## Scripts

- `scripts/browser_launcher.py` — 浏览器检测、启动、CDP 连接（**核心入口**）
- `scripts/cdp_client.py` — CDP WebSocket 通信
- `scripts/cdp_proxy.py` — CDP Proxy 常驻进程（自动管理，无需手动操作）
- `scripts/page_snapshot.py` — Accessibility Tree / DOM 快照
- `scripts/browser_actions.py` — 页面交互（click / type / navigate / screenshot）

**Always run scripts with `--help` first.** 参数细节由脚本自述，无需阅读源码。

## Decision Tree

```
用户任务 → 需要控制浏览器？
    │
    ├─ launcher.launch(browser='chrome')
    │   ├─ 成功 → 返回 CDP URL，继续操作
    │   └─ 抛出 BrowserNeedsCDPError？
    │       ├─ e.needs_restart == False (Chrome ≥ 144)
    │       │   → 脚本已自动打开 chrome://inspect 引导用户
    │       │     告知用户"请在弹窗中点击允许"，等待即可
    │       └─ e.needs_restart == True (Edge / QQ浏览器)
    │           → 告知用户关闭浏览器，用 e.launch_hint 命令重启
    │
    │   注：Chrome < 144 无论是否已运行，都自动用隔离 profile 启动新实例，
    │       不会抛异常，无需用户操作（但无法访问用户 Cookie/登录态）
    │   注：Chrome ≥ 144 未运行时，会自动启动 Chrome 并打开 chrome://inspect
    │       引导用户勾选开关 → 这是冷启动 Guide 模式，同样需要用户点击"允许"
    │
    ├─ 连接成功后 → **必须先查已有标签页**
    │   tabs = client.list_tabs()
    │   ├─ 找到目标 tab（URL/标题匹配）？
    │   │   → switch_tab(tab['id']) 直接复用，不要新开
    │   └─ 没有匹配的 tab？
    │       → create_tab(url) 创建新标签页
    │
    └─ 需要先了解页面结构？
        → page_snapshot.accessibility_tree() 获取快照
          根据快照中 [eN] 编号用 click_by_ref('eN') 交互
```

**关键行为**（已封装在脚本中，调用者无需处理）：
- 默认复用用户真实 profile（Cookie/登录态可用）
- Chrome < 144 无论是否已运行，自动回退到隔离 profile（该版本无法通过命令行正常开启 CDP）
- Chrome ≥ 144 冷启动时，不使用 `--remote-debugging-port`（该模式下 HTTP API 不可用），而是正常启动并走 Guide 流程
- 绝不 kill 用户浏览器进程
- Chrome/Edge/QQ浏览器的启动参数差异自动处理
- CDP Proxy 自动管理（避免重复弹窗）

## Quick Start

```python
import sys
sys.path.insert(0, '<skill_dir>/scripts')

from browser_launcher import BrowserLauncher, BrowserNeedsCDPError
from cdp_client import CDPClient
from page_snapshot import PageSnapshot
from browser_actions import BrowserActions

launcher = BrowserLauncher()
try:
    cdp_url = launcher.launch(browser='chrome')  # 自动处理 Probe/Guide/Cold start
except BrowserNeedsCDPError as e:
    print(f"⚠️ {e}")  # 异常消息已包含完整操作指引
    sys.exit(1)

client = CDPClient(cdp_url)
client.connect()

# ⚠️ 关键：先查已有标签页，避免重复打开
target_url = 'https://example.com'
tabs = client.list_tabs()
tab = None
for t in tabs:
    if target_url in t['url']:  # URL 匹配（也可以用 title 匹配）
        tab = t
        break

if tab:
    # 复用已有标签页
    client.attach(tab['id'])
else:
    # 没找到才新建
    tab = client.create_tab(target_url)
    client.attach(tab['id'])

# 获取 Accessibility 快照 → 找到交互元素
snapshot = PageSnapshot(client)
actions = BrowserActions(client, snapshot)
actions.wait_for_load()
tree = snapshot.accessibility_tree()
print(tree)

# 根据快照中的 ref 编号交互
actions.click_by_ref('e1')
actions.type_text('hello')
actions.screenshot('/tmp/result.png')

# ⚠️ 任务结束后 **不要** 调用 client.close() 或 launcher.stop()
# 保持连接活跃，下次任务可以直接复用，避免浏览器被关闭或需要重新授权
```

**常用选项**：
- `launcher.launch(reuse_profile=True, wait_for_user=True)` — **推荐默认用法**：复用用户浏览器（保留登录态/Cookie），等待用户授权 CDP
- `launcher.launch(browser='edge')` / `browser='qqbrowser'` — 切换浏览器（同样默认复用 profile）


## Accessibility 快照格式

智能体根据此格式解读页面结构并决定交互目标：

```
document "Example Page"
  navigation "主导航"
    [e1] link "首页"
    [e2] link "产品"
  main
    heading "欢迎"
    [e3] textbox "搜索..."
    [e4] button "搜索"
    article "最新文章"
      [e5] link "如何使用 CDP"
```

- `[eN]` 元素 → `click_by_ref('eN')` 直接交互
- 角色：button/textbox/link 等 INTERACTIVE 元素总有 ref；heading/article 等 CONTENT 有名称时有 ref
- **ref 会过期**：页面变化后必须重新获取快照

## API 速查

### BrowserLauncher
- `launch(browser, headless, port, reuse_profile, executable_path, wait_for_user, ...)` → CDP URL
- `stop()` — 断开连接（**不要在任务结束时调用**，保持连接以复用）
- `detect_browser(browser)` → 可执行文件路径 | None

### CDPClient
- `connect()` / `close()` — **不要在任务结束时调用 close()**
- `create_tab(url)` / `list_tabs()` / `attach(target_id)` / `close_tab(target_id)`
- `send(method, params)` — 发送原始 CDP 命令

### PageSnapshot
- `accessibility_tree(max_depth=0, interactive_only=False)` — 格式化无障碍树。**⚠️ 不要设 `max_depth`**，保持默认 `0`（无限制），浮层/弹窗在 10+ 层深，设小值会丢失。长度由 `max_chars=80000` 自动截断
- `dom_snapshot(selector)` / `get_text()`

### BrowserActions
- `navigate(url)` / `go_back()` / `go_forward()` / `reload()`
- `click_by_ref(ref)` / `click(x, y)` / `type_text(text)` / `press_key(key)`
- `screenshot(path, full_page)` / `evaluate(expression)`
- `wait_for_load()` / `wait_for_selector(selector, timeout)`
- `new_tab(url)` / `switch_tab(id)` / `close_tab(id)` / `list_tabs()`

> 参数详情：运行对应脚本 `--help` 或查看函数 docstring。

## 元素交互降级策略 ⚡

与页面元素交互时，**必须按以下优先级链逐步降级**，不要跳过或死磕某一种方式：

```
click_by_ref('eN')          ← 首选：通过 AX 快照 ref 交互（最精确）
    │  失败？
    ▼
click_selector('css')       ← 次选：通过 CSS 选择器定位
    │  失败？（Shadow DOM / 自定义组件）
    ▼
screenshot() + click(x, y)  ← 兜底：截图确认位置，坐标点击（最暴力，不依赖 DOM）
```

### 每一级何时该放弃

| 策略 | 放弃条件 | 典型场景 |
|------|---------|---------|
| `click_by_ref` | ref 不存在 / 点击后页面无变化 | Shadow DOM 内元素在 AX 树中缺失 |
| `click_selector` | 选择器找不到 / 元素被遮挡 / Shadow DOM 隔离 | QQ邮箱、飞书等重度自定义组件 |
| `click(x, y)` | 最终手段——截图确认元素可见后直接点坐标 | 所有上述方法失败时 |

### 重要原则

- **`click_by_ref` 已内置多层降级**（backendDOMNodeId → DOM click → JS heuristic → elementFromPoint 验证），绝大多数场景无需手动降级
- **同一操作最多重试 2 次**，仍失败则降级到下一策略
- **坐标点击前必须先 `screenshot()`** 确认目标元素在视口中可见
- **不要在 URL 跳转上死磕** — SPA 单页应用直接改 URL 大概率被重定向，优先用 DOM 交互

## ⛔ 登录 / 认证 / 人机验证 — 必须让用户协助 [MANDATORY]

> **核心原则：遇到需要登录或身份验证的页面，立即停下来请求用户协助，禁止自行绕过或尝试其他路径。**

### 识别登录/认证页面的信号

- 页面标题或 AX 快照包含「登录」「Login」「Sign in」「注册」「验证」「Verify」等关键词
- 页面出现用户名/密码输入框、验证码图片、滑块验证、短信验证码输入框
- 页面 URL 包含 `/login`、`/signin`、`/auth`、`/sso`、`/passport` 等路径
- 导航后被 302/303 重定向到登录页（目标 URL ≠ 实际 URL）
- 页面内容提示「请先登录」「会话已过期」「无权访问」「403 Forbidden」等

### 必须做

1. **立即停止自动化操作**，不要继续点击或尝试交互
2. **截图** (`screenshot()`) 保存当前页面状态，附给用户看
3. **明确告知用户**：当前页面需要登录/验证，需要用户手动完成
4. **等待用户确认**：用户说「已登录」「好了」后，再重新获取快照继续操作

### 绝对禁止

- ❌ 自动填写用户名、密码、验证码
- ❌ 尝试跳过登录页（如直接访问 API、修改 Cookie、伪造 token）
- ❌ 尝试换一个不需要登录的替代页面/路径来完成任务
- ❌ 反复刷新页面期望登录态自动恢复
- ❌ 在登录页上反复重试操作
- ❌ 用 `evaluate()` 注入 JS 绕过认证逻辑

### 典型处理模式

```python
# 导航后检查是否到了预期页面
actions.navigate('https://mail.qq.com/cgi-bin/frame_html')
actions.wait_for_load()
current_url = actions.get_url()

# 检查是否被重定向到登录页
if '/login' in current_url or 'passport' in current_url or current_url != target_url:
    actions.screenshot('/tmp/login_page.png')
    # ⛔ 停下来，告知用户需要登录
    print("当前页面需要登录，请在浏览器中手动完成登录后告知我")
    return  # 不要继续执行后续操作

# 或者通过快照内容判断
tree = snapshot.accessibility_tree()
if '登录' in tree or 'login' in tree.lower() or '请先登录' in tree:
    actions.screenshot('/tmp/need_login.png')
    print("检测到登录页面，请手动登录后告知我")
    return
```

## 关键注意事项

1. **先 wait_for_load() 再取快照** — 页面未加载完时快照不完整
2. **不要硬编码浏览器路径** — 用 `detect_browser()` 或 `executable_path` 参数
3. **任务结束后不要调用 `client.close()` 或 `launcher.stop()`** — 保持连接活跃，让后续任务可以直接复用已有的浏览器连接和标签页。关闭连接会导致 CDP Proxy 超时退出，下次操作需要重新授权；如果浏览器是脚本冷启动的，关闭连接还可能导致浏览器进程退出。**只有在用户明确要求关闭浏览器时**才调用 stop()
4. **CDP 命令超时？** — 如果 `launch()` 返回了 URL 但后续 `create_tab` 等命令超时，说明 CDP Proxy 的上游连接可能未就绪（用户尚未在 `chrome://inspect` 中点击允许）。先调用 `cdp_proxy.stop_proxy()` 清除残留 Proxy，再重新 `launch()`
5. **遇到无法自动解决的障碍时，立即向用户求助，禁止反复重试或绕过** — 包括但不限于：需要登录、验证码/人机验证、权限不足、页面反爬拦截、元素始终找不到、操作连续失败等。**同一操作最多重试 2 次**，仍失败则停下来告知用户具体情况并等待指示。绝不要：自动填写密码、反复刷新页面、换用其他绕过方案、或在循环中不断尝试相同操作
6. **`accessibility_tree()` 不要设 `max_depth`** — 保持默认 `0`（无限制）。弹窗/浮层/下拉菜单嵌套通常 10+ 层，设 `max_depth=3` 会完全丢失这些元素。长度由 `max_chars=80000` 自动控制
7. **优先复用已有标签页，不要每次都新建** — 在执行任何浏览器任务前，**必须先调用 `list_tabs()` 检查已打开的标签页**。如果已有标签页的 URL 或标题与目标匹配，直接用 `switch_tab(tab['id'])` 切换过去，而不是 `create_tab()` 新开一个。这对于多步骤任务尤为重要：用户可能在上一步已经打开了目标页面，甚至已完成登录，新开标签页会丢失这些状态。只有在确认没有可复用的标签页时才新建

## Reference Files

- **examples/** — 完整用例（按需查阅）：
  - `basic_navigation.py` — 打开页面、截图、获取标题
  - `form_automation.py` — 表单填写与提交
  - `page_scraping.py` — 页面数据提取
  - `multi_tab.py` — 多标签页操作
