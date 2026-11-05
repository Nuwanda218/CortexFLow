// Preload for the local ContextFlow panel. Only a narrow, explicit API is
// exposed; the panel cannot reach Node or Electron internals.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cf", {
  onCoreEvent: (cb) => {
    const handler = (_evt, payload) => cb(payload);
    ipcRenderer.on("core-event", handler);
    return () => ipcRenderer.removeListener("core-event", handler);
  },
  getState: () => ipcRenderer.invoke("cf:get-state"),
});
