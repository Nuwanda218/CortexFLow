# Electron as a desktop container for chatgpt.com + a local control panel

2026-11-04. **[OFFICIAL-CONFIRMED]** = vendor/standards doc; **[SECONDARY]** = third party or doc-derived reasoning; **[UNVERIFIED]** = not confirmed here.

## 1. Which embedding API?

- Current stable is **Electron 45.2.2 (Chromium 156, Node 24.22.2)**, 2026-11-04 — "Electron 30+" is a stale baseline. [OFFICIAL-CONFIRMED] https://releases.electronjs.org/
- **`WebContentsView` is the recommended API**: "A View that displays a WebContents", main process, `win.contentView.addChildView(view)`. [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/api/web-contents-view
- **`BrowserView` deprecated**: history table `>=29.0.0` → "API DEPRECATED"; "replaced by the new `WebContentsView` class". [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/api/browser-view
- **`<webview>` discouraged**: "We currently recommend to not use the `webview` tag and to consider alternatives, like `iframe`, a `WebContentsView`, or an architecture that avoids embedded content altogether." [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/api/webview-tag
- `iframe` is not deprecated but is a DOM frame, so framing restrictions apply. [SECONDARY]

## 2. Arbitrary remote https, and XFO / frame-ancestors

- **Yes.** The official example loads `https://electronjs.org` and `https://github.com/electron/electron` into two views. [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/api/web-contents-view
- **XFO/`frame-ancestors` do not apply**: they govern *embedding* — `X-Frame-Options` says "whether a browser should be allowed to render the document in a `<frame>`, `<iframe>`, `<embed>` or `<object>`". A `WebContentsView` owns a **top-level** `WebContents` (tab-equivalent), so there is no ancestor frame to check. [SECONDARY — MDN definition + Chromium architecture; Electron never states it verbatim] https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Frame-Options
- Consequence: no `onHeadersReceived` header-stripping hack needed (that workaround is iframe/webview-only and unsafe). [SECONDARY]
- Whether chatgpt.com actually sends those headers is **[UNVERIFIED]** — `curl -I` settles it, and it only matters for the iframe option.

## 3. Session / partition

- `session.fromPartition('persist:name')`: "If `partition` starts with `persist:`, the page will use a persistent session available to all pages in the app with the same `partition`. if there is no `persist:` prefix, the page will use an in-memory session." [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/api/session
- Use a dedicated `persist:` partition so the remote site's cookies are isolated from the panel. [SECONDARY]
- **Separate cookie jar from the user's Edge/Chrome profile**: Electron sessions are app-owned storage; no API imports a browser profile. [SECONDARY — absence of such API in the session docs]

## 4. Security

From the 20-item checklist: [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/tutorial/security

- "Under no circumstances should you load and execute remote code with Node.js integration enabled… To display remote content, use the `<webview>` tag or a `WebContentsView` and make sure to disable the `nodeIntegration` and enable `contextIsolation`."
- `nodeIntegration: false` (default since 5.0.0), `contextIsolation: true` (12.0.0), `sandbox: true` (20.0.0) — don't override.
- "Do not disable `webSecurity` in production applications"; leave `allowRunningInsecureContent` false (it disables mixed-content protection).
- Items 13/14: limit navigation (`will-navigate`) and window creation — `contents.setWindowOpenHandler(...)` returning `{ action: 'deny' }`, routing approved URLs to `shell.openExternal`.
- Item 5 is the sleeper: "By default, Electron will automatically approve all permission requests unless the developer has manually configured a custom handler" → set `setPermissionRequestHandler`.
- Item 20: "Do not expose Electron APIs to untrusted web content."
- Framing: "Electron is not a web browser… displaying arbitrary content from untrusted sources poses a severe security risk that Electron is not intended to handle."

## 5. IPC

- Canonical: preload + `contextBridge.exposeInMainWorld('electronAPI', …)`, `ipcRenderer.send`/`invoke` ↔ `ipcMain.on`/`handle`; "We don't directly expose the whole `ipcRenderer.send` API for security reasons." [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/tutorial/ipc
- Local panel: full bridge is fine (your code). Remote page: **no privileged preload** (item 20) — drive it from main via `executeJavaScript`/`insertCSS`/navigation events, or expose a tiny validated bridge and check `event.senderFrame` (item 17). [OFFICIAL-CONFIRMED rules; pattern SECONDARY]

## 6. Local Python HTTP/WebSocket

- No Electron-specific constraint — ordinary Chromium networking. `http://127.0.0.1` and `ws://127.0.0.1` are **potentially trustworthy origins** ("A host value of `127.0.0.0/8` or `::1/128`", "`localhost`"), hence secure contexts exempt from mixed-content blocking. [SECONDARY — MDN spec summary] https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts
- Only real constraint: your own CSP — `default-src 'self'` (which Electron recommends) blocks `connect-src` to the Python port until allowed. [SECONDARY]

## 7. Split layout

- Yes — the official example *is* a split view: `view1.setBounds({x:0,y:0,width:400,height:400})`, `view2.setBounds({x:400,y:0,width:400,height:400})`; recompute on `win.on('resize')`. [OFFICIAL-CONFIRMED] https://www.electronjs.org/docs/latest/api/web-contents-view

## 8. Electron vs. extension side panel

`chrome.sidePanel`: Chrome 114+, "host content in the browser's side panel alongside the main content of a webpage"; "remains open when navigating between tabs"; "As an extension page, side panels have access to all Chrome APIs". [OFFICIAL-CONFIRMED] https://developer.chrome.com/docs/extensions/reference/api/sidePanel

| | Electron | Extension |
|---|---|---|
| Native page, real login | yes, but **new cookie jar** — re-login [SECONDARY] | **reuses existing profile/session** [SECONDARY] |
| Window chrome, tray, always-on-top | full control (`BaseWindow`, `Tray`) [OFFICIAL-CONFIRMED] | none [SECONDARY] |
| Global shortcuts | `globalShortcut` [OFFICIAL-CONFIRMED] | browser-focused only [SECONDARY] |
| Multi-pane geometry | arbitrary via `setBounds` [OFFICIAL-CONFIRMED] | one narrow panel [SECONDARY] |
| Local Python backend | unrestricted [OFFICIAL-CONFIRMED] | native messaging / localhost fetch [SECONDARY] |
| Size & updates | ~100–200 MB, you own updates; `autoUpdater` is macOS/Windows only, macOS needs signing [OFFICIAL-CONFIRMED] | small CRX, store-updated [SECONDARY] |

## 9. Sites that block embedded browsers

- Google, official: a "secure browser policy prohibiting Google OAuth requests in embedded browser libraries commonly referred to as embedded webviews. **All embedded webviews will be blocked starting on September 30, 2021**", returning `disallowed_useragent`; rationale is MITM risk, lost SSO/password-manager/WebAuthn, and RFC 8252 requiring external user-agents. [OFFICIAL-CONFIRMED] https://developers.googleblog.com/upcoming-security-changes-to-googles-oauth-20-authorization-endpoint-in-embedded-webviews/
- Scope: this targets **OAuth endpoints**, not general browsing. Browsing chatgpt.com in Electron is fine; **"Continue with Google"** inside it is the blocked case. [SECONDARY]
- "This browser or app may not be secure" is the user-facing symptom; UA-spoofing workarounds exist, are discouraged, and break periodically. [SECONDARY] https://stackoverflow.com/questions/69330419/electron-google-login-this-browser-or-app-may-not-be-secure

## Four conclusions

1. **The Electron container is officially supported and cheap to prototype**: `WebContentsView` + `setBounds` split + `persist:` partition. No framing wall — XFO/`frame-ancestors` are embedding checks and a `WebContentsView` is top-level. [OFFICIAL-CONFIRMED]
2. **Identity is the real risk, not layout.** A separate cookie jar forces re-authentication, and Google-SSO paths are blocked by policy, not by a fixable bug. If the account is Google-SSO, Electron may be a dead end; email/password or session-cookie reuse is the viable route. [OFFICIAL-CONFIRMED policy; SECONDARY consequence]
3. **The extension wins on "native ChatGPT web experience" by definition** — same profile, same session, no re-login, store updates. It loses window chrome, global shortcuts, pane count, and free local-backend access. [SECONDARY]
4. **Choose by which constraint is harder to move.** Real multi-pane shell + hotkeys + tight local-runtime coupling → Electron, accepting re-login and an update pipeline. "A panel next to my real ChatGPT tab" → extension. Hybrid (extension panel + local server) keeps the login and still gets Python. [SECONDARY — inference]
