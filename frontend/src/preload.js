const { contextBridge } = require('electron');

// Expose safe APIs to renderer
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
});
