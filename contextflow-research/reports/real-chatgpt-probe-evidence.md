# 真实 ChatGPT 页面探测证据（CF-EXP-001 补充，2026-11-04 22:20–22:40）

> 环境：用户已登录的 Edge 156（BrowserSkill 扩展 0.4.1 / bsk daemon 0.4.1），真实 Plus 账号。
> 方法：只读观察（observe / get-html 落盘后本地分析）。**未发送任何消息、未修改任何会话、未读取 Cookie/Token。**
> 隐私：落盘 HTML 仅用于本地结构分析，分析后已删除；本文件只记录标签/属性统计，不含对话正文。

## 1. 会话 URL 形态（实测）

| 对象 | URL | 结论 |
|---|---|---|
| Project 内会话 A（学习链表代码表征） | `https://chatgpt.com/g/g-p-6ae0676626fc8191aeec2862b944f219-11yue-xia-xun-mian-shi-zhun-bei/c/6ae5eb8b-00ac-83ee-83de-166f286f895c` | 项目内会话 = `/g/g-p-<project-id>-<slug>/c/<chat-id>` |
| Project 内会话 B（contextflow项目设计） | `https://chatgpt.com/g/g-p-6ae0676626fc8191aeec2862b944f219-11yue-xia-xun-mian-shi-zhun-bei/c/6ae06905-a0f0-83ee-9fd1-90b8f363850b` | 同上；chat-id 为 UUID |
| Project 页面 | `https://chatgpt.com/g/g-p-6ae0676626fc8191aeec2862b944f219/project` | 项目 URL **不带 slug**（与第三方观察的 `-<slug>` 形态不同） |
| 新聊天 | `https://chatgpt.com/` | 新会话在首条消息前**没有** `/c/<id>` |

- 同一 chat-id 在 URL 与 DOM 中一致：`data-chatgpt-selection-conversation-id="6ae06905-…"`。
- 项目 id 前缀 `g-p-` 与 `data-above-composer-conversation-id="chatgpt:<chat-id>"` 可交叉校验。

## 2. 导航行为（实测）

- **刷新**：`bsk reload` 后 URL 完全不变，仍恢复同一会话（`previous_url == final_url`）。
- **新标签页打开同一 URL**：恢复同一会话（标题、消息一致）。
- **点击项目面包屑**：URL 变为 `/g/g-p-<project-id>/project`，标题变为 `ChatGPT - <项目名>`，**无整页文档请求**（仅加载 async JS/CSS chunk）→ **SPA 路由确认**。
- **点击项目内会话链接**：URL 变为 `/g/g-p-…/c/<chat-id>`，标题变为会话名，SPA。
- **点击"新聊天"**：URL 变为 `https://chatgpt.com/`，SPA。

## 3. DOM 结构（2026-11 版 ChatGPT，实测）

页面 HTML 约 1.29 MB（当前会话渲染部分）。关键结构：

| 用途 | 选择器 / 属性 | 说明 |
|---|---|---|
| 消息角色 | `h4[data-conversation-role="user\|assistant"]` | 每个回合的角色标题（"你说："/"ChatGPT 说："） |
| 角色（备用） | `[data-chatgpt-message-metadata-role="user\|assistant"]`、`[data-role="user\|assistant"]` | 多处冗余标记 |
| 回合容器 | `div[data-talvt-turn-state="complete"]` | **回合状态**；生成中预期为其他值（待实测） |
| 回合 key | `[data-turn-key="<uuid>"]` | 每回合稳定 id |
| 消息 id | `[data-chatgpt-selection-message-id="<uuid>"]` | 每条消息 UUID |
| 会话 id | `[data-chatgpt-selection-conversation-id="<uuid>"]`、`[data-above-composer-conversation-id="chatgpt:<uuid>"]`、`[data-map-composer-conversation="<uuid>"]` | **DOM 内可直接拿到会话 id**，不必只依赖 URL |
| 用户消息气泡 | `[data-user-message-bubble="true"]` | 用户消息专用 |
| 消息元数据 | `[data-chatgpt-message-metadata="true"]`（12 处）、`data-markdown-han-text="true"`（12 处） | 每条消息的元数据/中文文本标记 |
| 复制相关 | `data-markdown-copy="exclude\|inline-code\|contents\|rich-block"` | 复制/富文本块标记 |
| 输入框 | `contenteditable` + `class="ProseMirror"`（ProseMirror 编辑器） | 非 textarea |
| 发送按钮 | `aria-label="发送"` / 生成中变为停止类按钮（待实测） | |
| 数学公式 | `<math>`（MathML）+ `katex` 类 | 结构化公式可提取 |
| 懒加载 | 页面含"正在加载更早的消息…" | **注意：用户说明这是其自用优化脚本造成，非 ChatGPT 原生行为；禁用脚本后应显示全部消息（待复测）** |
| 侧边栏 | 项目列表 + 项目内会话列表（`link` 元素） | 可枚举项目与会话标题 |

## 4. 关键结论

1. **会话 id 可从 DOM 直接读取**（`data-chatgpt-selection-conversation-id`），比解析 URL 更稳；URL 与 DOM 可交叉校验。
2. **SPA 路由确认**：pushState 改变 URL + 标题，无整页加载 → 扩展必须用 `webNavigation.onHistoryStateUpdated` + DOM 观察。
3. **项目 URL 与项目内会话 URL 形态不同**：项目页 `/g/g-p-<id>/project`（无 slug），会话页 `/g/g-p-<id>-<slug>/c/<chat-id>`。
4. **新会话在首条消息前没有 `/c/<id>`** → 不能假设"打开页面即有会话 URL"。
5. **历史消息懒加载**：DOM 中只有当前视口附近的消息，完整历史读取需要滚动触发加载（进一步增加"完整读取"的难度与合规风险）。
6. **输入框是 ProseMirror contenteditable**，不是 textarea；程序化填入遇到焦点/光标问题（本次 `bsk fill` 两次失败：`fill target changed or lost focus while positioning the caret`），说明"外部写入输入框"不是简单赋值，需要更谨慎的输入策略（或用户手动粘贴）。
7. **复制按钮存在**（用户消息有"复制消息"，助手消息有"复制"）→ "用户点击复制 → Core 读取剪贴板"是最合规的提取路径候选。

## 5. 流式生成状态（实测，2026-11-04 22:39–22:40）

用户在真实会话中手动发送一条消息，脚本每 0.6–0.9 秒轮询并抓取 4 张 DOM 快照（只读）。结果：

| 快照 | `data-talvt-turn-state` | 停止按钮 | 发送按钮 | 说明 |
|---|---|---|---|---|
| 生成中 #1 | `complete`×2 + **`in_progress`×1** | `aria-label="停止"` ×1 | 无 | 生成中 |
| 生成中 #2 | `complete`×1 + **`in_progress`×1** | 无（快照时机） | 无 | 生成中 |
| 生成中 #3 | `complete`×1 + **`in_progress`×1** | 无 | 无 | 生成中 |
| 完成后 #4 | **`complete`×2** | 无；出现 `重新生成回复` | 无 | 生成结束 |

**结论（实测）**：
- **生成/完成的可靠信号 = `[data-talvt-turn-state]`**：生成中为 `in_progress`，完成为 `complete`。
- 生成中会出现 `aria-label="停止"` 的停止按钮，发送按钮消失；完成后停止按钮消失、出现"重新生成回复"。
- `aria-busy` 始终为 `false`，**不能**作为生成信号。
- 未发现 `data-is-streaming` / `data-streaming` 属性。

## 6. 完成后的回复结构（实测）

- 代码块：`<pre>`×2、`<code>`×3，自定义 `CodeBlock-*` / `CodeSurface-*` 类；每个代码块有 `aria-label="复制代码"` 按钮（5 个）。
- 复制按钮：助手消息 `aria-label="复制"`、用户消息 `aria-label="复制消息"`。
- `data-markdown-copy` 取值：`code-block`×6、`inline-code`×2、`contents`×1、`exclude`×9 → 页面自带"按 Markdown 复制"的语义标记。
- 回合 id：`data-turn-key="<uuid>"`（每回合一个）；消息 id：`data-chatgpt-selection-message-id="<uuid>"`；会话 id 两处一致。
- **DOM 中的消息数量随滚动/懒加载变化**（不同快照中 user/assistant 计数 3/3 → 2/2），进一步确认**不是所有历史消息都在 DOM 中**。

## 7. 输入框写入：直接 fill 失败，剪贴板粘贴成功（实测）

| 方法 | 结果 |
|---|---|
| `bsk fill`（直接赋值/输入） | **失败** ×2：`fill target changed or lost focus while positioning the caret`（ProseMirror 焦点/光标问题） |
| 系统剪贴板 + `Ctrl+V` | **成功**：`Set-Clipboard` 写入文本 → 聚焦输入框 → `Ctrl+V` → 输入框 `[filled]`，发送按钮出现 |

**结论**：ChatGPT 的 ProseMirror 输入框拒绝"直接 fill"，但接受**受信任的粘贴事件**。这给出两条可行路径：
1. **浏览器自动化层**（CDP/Playwright）：写剪贴板 + 派发 Ctrl+V 可用。
2. **扩展层**：扩展无法直接合成受信任的 Ctrl+V（除非用 `chrome.debugger`，会显示"正在调试"横幅）；更现实的是**扩展写剪贴板 → 提示用户按 Ctrl+V → 用户确认发送**，或直接让用户手动粘贴。

## 8. 复制按钮 → 剪贴板格式（实测）

点击助手消息的 `aria-label="复制"` 按钮后读取系统剪贴板：

- 长度 **6229 字符 / 416 行**
- 含 **``` 围栏代码块**、**Markdown 标题**、**表格管道符**
- **不含 HTML 标签**（检测到的 `<ListNode>`/`<int>`/`<std::...>` 是 C++ 代码，不是 HTML）
- 不含 LaTeX `$...$`、不含 MathML 文本

**结论**：ChatGPT 的"复制"按钮输出的是**纯 Markdown**。这是目前发现的**最合规、最结构化的提取路径**：用户点击复制 → Core 读取剪贴板（或用户粘贴到 Core），无需程序化抓取 DOM。

## 9. "假剪贴板"注入实验（2026-11-04 23:10，实测）

**目的**：验证能否用"剪贴板相同的机制"（paste 事件）注入文本，但**不触碰操作系统剪贴板**，从而与用户日常剪贴板完全隔离。

**方法**：在真实 ChatGPT 新会话页面（ProseMirror 输入框）执行：

```js
const ed = document.querySelector('.ProseMirror[contenteditable=true]');
ed.focus();
const dt = new DataTransfer();
dt.setData('text/plain', 'CF fake 456');
ed.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
```

**结果**：

| 方法 | 结果 |
|---|---|
| 合成 `ClipboardEvent('paste')` + `DataTransfer` | ✅ **成功**：输入框变为 `CF fake 456`，发送按钮出现；`isTrusted=false`、`defaultPrevented=true`（ProseMirror 正常处理了该事件） |
| `document.execCommand('insertText', false, text)` | ✅ **成功**：文本追加为 `CF fake 456 EXECCMD` |
| OS 剪贴板隔离 | ✅ **验证**：测试前把系统剪贴板设为 `SENTINEL-123`，注入后仍为 `SENTINEL-123`，**未被读写** |

**结论**：
- **"假剪贴板"可行**：合成 paste 事件可以完成与真实粘贴相同的注入效果，且完全不接触 OS 剪贴板、不需要 `clipboardWrite`/`clipboardRead` 权限。
- 这是比"真实剪贴板"更优的注入路径：无污染、无竞争、无敏感信息外泄风险。
- **局限**：依赖编辑器接受不可信（`isTrusted=false`）事件（当前 ChatGPT 接受，未来可能变化）；同一 tick 内"清空 + 粘贴"会失败（需分步/等待一帧）。

### 9.1 跨世界验证（扩展 content script → 页面编辑器，CF-EXP-002D）

- **目的**：验证扩展的 content script（隔离世界）能否派发携带 `DataTransfer` 的合成 paste 事件，并被页面（主世界）的编辑器接收。
- **方法**：本地 `prosemirror-like.html`（模拟 ProseMirror 的 paste 契约）+ 探针扩展；SW 通过 `chrome.tabs.sendMessage` 触发 content script 的 `cf:fake_paste`。
- **结果**：**6/6 通过**
  - content script 成功派发合成 paste（`isTrusted=false`、`defaultPrevented=true`）；
  - 页面收到事件，`hasDataTransfer=true`，文本成功插入；
  - 重复注入正常；
  - **OS 剪贴板哨兵值 `SENTINEL-FAKE-001` 前后不变**。
- **证据**：`_artifacts/fake-clipboard-evidence.json`、`experiments/playwright-probe/fake_clipboard_test.py`。
- **结论**：**"假剪贴板"在扩展架构中可行**——隔离世界可以跨世界派发 paste 事件，`DataTransfer` 正常传递，且完全不触碰系统剪贴板。

## 10. 未完成（需用户配合）

- 禁用优化脚本后复测历史消息是否完整渲染。
- 会话重命名 / 移入移出 Project 后 URL 是否变化（需用户操作）。
- 扩展 content script 在真实页面的注入与稳定性（需加载探针扩展）。
- 扩展写剪贴板 + 提示用户粘贴的端到端链路（需扩展原型）。


