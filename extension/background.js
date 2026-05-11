const WS_URL = 'ws://localhost:56789';
let ws = null;
let reconnectTimer = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      console.log('[Lyricaod] WebSocket connected');
    };
    ws.onclose = () => {
      console.log('[Lyricaod] WebSocket disconnected');
      scheduleReconnect();
    };
    ws.onerror = (err) => {
      console.error('[Lyricaod] WebSocket error', err);
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
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message.data));
    }
  }
  sendResponse({});
  return false;
});

connect();
