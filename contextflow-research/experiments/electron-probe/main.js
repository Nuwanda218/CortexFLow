// ContextFlow Electron probe (CF-EXP-001, task F2).
// Minimal two-pane window: left = remote chat page (WebContentsView), right =
// local ContextFlow panel. No Node integration in either remote/local view;
// contextIsolation on; sandbox on. The panel talks to the main process over IPC.
//
// Run:  npx electron .            (normal)
//       npx electron . --probe    (automated probe, prints JSON and exits)

const { app, BrowserWindow, WebContentsView, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");

const MOCK = "http://127.0.0.1:8787/mock/chat/session-a";
const ARTIFACTS = path.join(__dirname, "..", "..", "_artifacts");
const PROBE = process.argv.includes("--probe");

// The default userData dir (%APPDATA%\Electron) is blocked by the local security
// software on this machine ("拒绝访问" / access denied), which crashes Chromium's
// network sandbox. Point it at a writable workspace directory instead.
const USER_DATA = path.join(ARTIFACTS, "electron-userdata");
fs.mkdirSync(USER_DATA, { recursive: true });
app.setPath("userData", USER_DATA);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, timeoutMs = 15000, interval = 200) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const v = await fn();
    if (v) return v;
    await sleep(interval);
  }
  return null;
}

async function capture(view, name) {
  try {
    const img = await view.webContents.capturePage();
    const file = path.join(ARTIFACTS, name);
    fs.writeFileSync(file, img.toPNG());
    return file;
  } catch (err) {
    return "capture failed: " + String(err);
  }
}

async function runProbe(win, left, right) {
  const evidence = { checks: [], notes: [] };
  const check = (name, ok, detail = "") => {
    evidence.checks.push({ name, ok: !!ok, detail });
    console.log((ok ? "PASS " : "FAIL ") + name + (detail ? " :: " + detail : ""));
  };

  // F-01: two-pane layout with a remote page in WebContentsView
  const bounds = left.getBounds();
  check("F-01 WebContentsView hosts remote page", left.webContents.getURL().startsWith("http://127.0.0.1:8787"), left.webContents.getURL());
  check("F-02 two-pane layout (left/right bounds)", bounds.width > 0 && right.getBounds().width > 0,
    JSON.stringify({ left: bounds, right: right.getBounds() }));

  // F-03: drive the remote page from the main process (executeJavaScript)
  await left.webContents.executeJavaScript(`(() => {
    const input = document.getElementById('prompt-input');
    input.value = 'Electron typed this message.';
    input.dispatchEvent(new Event('input', {bubbles:true}));
    document.getElementById('send').click();
    return true;
  })()`);
  const idle = await waitFor(() => left.webContents.executeJavaScript("document.body.dataset.appState === 'idle'"), 20000);
  check("F-03 remote page interaction from main process", !!idle, "generation finished");
  const msgInfo = await left.webContents.executeJavaScript(`(() => {
    const msgs = Array.from(document.querySelectorAll('.message'));
    const last = msgs[msgs.length - 1];
    return { count: msgs.length, lastRole: last && last.dataset.role, lastStatus: last && last.dataset.status,
             lastText: last ? last.innerText.slice(0, 120) : null };
  })()`);
  check("F-04 read remote page DOM from main process", msgInfo.count >= 4 && msgInfo.lastStatus === "complete", JSON.stringify(msgInfo));

  // F-05: main -> panel IPC event
  right.webContents.send("core-event", { type: "worker_completed", task_id: "test-task-001", summary: "Mock worker finished." });
  const panelEvents = await waitFor(() => right.webContents.executeJavaScript("window.__cfEvents && window.__cfEvents.length ? window.__cfEvents : null"), 5000);
  check("F-05 main -> panel IPC event delivered", !!panelEvents && panelEvents.some((e) => e.task_id === "test-task-001"), JSON.stringify(panelEvents));
  const panelText = await right.webContents.executeJavaScript("document.getElementById('events').innerText");
  check("F-06 panel rendered the event", panelText.includes("test-task-001"), panelText.slice(0, 160));

  // F-07: remote page isolation - no Node in the remote page
  const nodeInRemote = await left.webContents.executeJavaScript("typeof require + '/' + typeof process + '/' + typeof module");
  check("F-07 remote page has no Node.js globals", nodeInRemote === "undefined/undefined/undefined", nodeInRemote);
  const nodeInPanel = await right.webContents.executeJavaScript("typeof require + '/' + typeof process");
  check("F-08 panel has no Node.js globals (contextIsolation)", nodeInPanel === "undefined/undefined", nodeInPanel);

  evidence.screenshots = [await capture(left, "electron-left-remote.png"), await capture(right, "electron-right-panel.png")];

  // F-09: can WebContentsView load chatgpt.com (top-level, not iframe)?
  try {
    await left.webContents.loadURL("https://chatgpt.com/", { timeout: 30000 });
    await sleep(4000);
    const info = { url: left.webContents.getURL(), title: await left.webContents.executeJavaScript("document.title") };
    evidence.chatgpt = info;
    check("F-09 WebContentsView loads chatgpt.com (top-level)", true, JSON.stringify(info));
    evidence.screenshots.push(await capture(left, "electron-chatgpt.png"));
  } catch (err) {
    evidence.chatgpt = { error: String(err) };
    check("F-09 WebContentsView loads chatgpt.com (top-level)", false, String(err));
  }

  evidence.summary = { passed: evidence.checks.filter((c) => c.ok).length, total: evidence.checks.length };
  fs.writeFileSync(path.join(ARTIFACTS, "electron-evidence.json"), JSON.stringify(evidence, null, 2));
  console.log("\n" + evidence.summary.passed + "/" + evidence.summary.total + " checks passed");
  app.quit();
  return evidence;
}

app.whenReady().then(async () => {
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  const win = new BrowserWindow({
    width: 1440, height: 900, show: true, title: "ContextFlow Electron Probe",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });

  const left = new WebContentsView({
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  const right = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  win.contentView.addChildView(left);
  win.contentView.addChildView(right);

  function layout() {
    const [w, h] = win.getContentSize();
    const leftW = Math.floor(w * 0.68);
    left.setBounds({ x: 0, y: 0, width: leftW, height: h });
    right.setBounds({ x: leftW, y: 0, width: w - leftW, height: h });
  }
  layout();
  win.on("resize", layout);

  ipcMain.handle("cf:get-state", () => ({ ok: true, mode: "electron-probe" }));

  await left.webContents.loadURL(MOCK);
  await right.webContents.loadFile(path.join(__dirname, "panel.html"));
  await sleep(800);

  if (PROBE) {
    try {
      await runProbe(win, left, right);
    } catch (err) {
      console.error("PROBE ERROR", err);
      app.exit(1);
    }
  }
});

app.on("window-all-closed", () => app.quit());
