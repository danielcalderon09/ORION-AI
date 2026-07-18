const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

let mainWindow;
let pythonProcess;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, '../preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // In dev, load from Vite dev server so React + TS are compiled in real time
  // In production, load the built dist bundle
  const isDev = process.env.NODE_ENV === 'development';
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../../dist/index.html'));
  }
}

function loadViteOrFallback() {
  return new Promise((resolve) => {
    const req = http.get('http://localhost:5173/', (res) => {
      if (res.statusCode === 200 || res.statusCode === 204) {
        resolve(true); // Vite is running
      } else {
        resolve(false);
      }
    });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

async function createWindowSmart() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, '../preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const viteAvailable = await loadViteOrFallback();
  if (viteAvailable) {
    console.log('[Electron] Vite dev server detected. Loading http://localhost:5173');
    await mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    const distPath = path.join(__dirname, '../../dist/index.html');
    console.log('[Electron] Vite not running. Loading production build:', distPath);
    await mainWindow.loadFile(distPath);
  }
}

function isBackendAlive(timeoutMs = 3000) {
  return new Promise((resolve) => {
    const options = {
      hostname: '127.0.0.1',
      port: 8000,
      path: '/api/v1/videos/',
      method: 'HEAD',
      timeout: timeoutMs,
    };
    const req = http.request(options, (res) => {
      resolve(true); // Any response (even 404 or 405) means backend is up
    });
    req.on('error', (err) => {
      console.log('[Electron] Health check error:', err.message);
      resolve(false);
    });
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

function startBackend() {
  const pythonPath = process.platform === 'win32' ? 'python' : 'python3';
  pythonProcess = spawn(pythonPath, ['-m', 'backend.src.main'], {
    cwd: path.join(__dirname, '../../..'),
    stdio: 'pipe',
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python] ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python Error] ${data}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`Python backend exited with code ${code}`);
  });
}

app.whenReady().then(async () => {
  const alive = await isBackendAlive();
  if (alive) {
    console.log('[Electron] Backend already running on port 8000. Skipping spawn.');
  } else {
    console.log('[Electron] Backend not detected. Starting...');
    startBackend();
  }
  // Wait a bit for backend to start (or confirm it's alive)
  setTimeout(createWindowSmart, 2000);
});

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill();
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindowSmart();
  }
});
