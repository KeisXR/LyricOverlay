const WS_URL = 'ws://localhost:56789';
let ws = null;
let reconnectTimer = null;
let latestState = null;
let lastConnectAttempt = 0;

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }
  if (Date.now() - lastConnectAttempt < 3000) {
    return;
  }
  lastConnectAttempt = Date.now();
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      console.log('[Lyricaod] WebSocket connected');
      if (latestState) {
        ws.send(JSON.stringify(latestState));
      }
    };
    ws.onclose = () => {
      ws = null;
      if (latestState) scheduleReconnect();
    };
    ws.onerror = () => {
      // Lyricaod may not be running yet. Keep this quiet and retry later
      // only while media is actively being tracked.
      if (ws) ws.close();
    };
  } catch (e) {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, 3000);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'MEDIA_STATE') {
    latestState = message.data;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(latestState));
    } else {
      connect();
    }
  }
  sendResponse({});
  return false;
});

connect();
