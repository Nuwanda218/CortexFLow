const { app, BrowserWindow } = require("electron");
console.log("minimal: main start, argv=" + JSON.stringify(process.argv.slice(1)));
app.whenReady().then(async () => {
  console.log("minimal: app ready");
  const win = new BrowserWindow({ width: 800, height: 600, show: false, webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
  console.log("minimal: window created");
  await win.loadURL("data:text/html,<h1>hello</h1>");
  console.log("minimal: loaded, title=" + (await win.webContents.executeJavaScript("document.body.innerText")));
  setTimeout(() => { console.log("minimal: quitting"); app.quit(); }, 500);
});
