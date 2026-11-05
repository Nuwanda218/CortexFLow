# electron-probe — Electron 双栏最小应用

验证“桌面容器承载 ChatGPT 网页 + 本地面板”的可行性。**不是产品前端。**

## 结构

| 文件 | 作用 |
|---|---|
| `main.js` | 主进程：`WebContentsView` 左远程页 / 右本地面板；`setBounds` 分栏；IPC；`--probe` 自动探针 |
| `preload.js` | 面板专用 preload，仅暴露 `cf.onCoreEvent` / `cf.getState` |
| `panel.html` | 本地面板，显示 Core 事件 |
| `minimal.js` | 最小 Electron 冒烟测试（用于定位崩溃） |

## 安全设置

- 远程页与面板均 `contextIsolation:true`、`nodeIntegration:false`、`sandbox:true`；
- 面板通过 contextBridge 暴露窄接口，不暴露整个 `ipcRenderer`；
- 未关闭 `webSecurity`。

## 运行

```powershell
.\node_modules\electron\dist\electron.exe . --probe --no-sandbox --disable-gpu --disable-crash-reporter --noerrdialogs
```

> **本机重要坑**：默认 `%APPDATA%\Electron` 被安全软件拒绝访问（`拒绝访问 (0x5)`），会导致 Electron 启动即崩溃（`0x80000003`/`0xC0000005`）。`main.js` 已 `app.setPath("userData", _artifacts/electron-userdata)` 规避。另外 `--disable-crash-reporter` 在本机是必需的。

## 实测结果（9/9）

- 双栏布局、远程页加载、主进程驱动远程页输入并等待完成、读取远程 DOM；
- 主进程 → 面板 IPC 事件送达并渲染；
- 远程页/面板均无 Node 全局；
- **WebContentsView 顶层加载 chatgpt.com 成功**（而 iframe 被 `X-Frame-Options: SAMEORIGIN` 拒绝）。

## 未验证

登录态、Google SSO 登录、与 Python Core 的真实连接、截图（本机 `capturePage` 失败）、打包分发。
