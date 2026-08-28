'use strict';

const DEFAULT_PORT = 56789;
const SOURCE_STALE_MS = 7000;
const RECONNECT_MS = 3000;
const PROTOCOL_VERSION = 1;
const instanceId = crypto.randomUUID();

let ws = null;
let reconnectTimer = null;
let port = DEFAULT_PORT;
let outboundSequence = 0;
let selectedSourceKey = null;
let latestPayload = null;
const sources = new Map();

function sourceKey(tabId, frameId) {
  return `${tabId}:${frameId}`;
}

function validPort(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 1024 && number <= 65535
    ? number
    : DEFAULT_PORT;
}

async function loadPort() {
  const settings = await chrome.storage.local.get({ wsPort: DEFAULT_PORT });
  port = validPort(settings.wsPort);
}

function webSocketUrl() {
  return `ws://127.0.0.1:${port}`;
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, RECONNECT_MS);
}

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  try {
    ws = new WebSocket(webSocketUrl());
    ws.onopen = () => {
      if (latestPayload) ws.send(JSON.stringify(latestPayload));
    };
    ws.onclose = () => {
      ws = null;
      scheduleReconnect();
    };
    ws.onerror = () => {
      if (ws) ws.close();
    };
  } catch (_error) {
    ws = null;
    scheduleReconnect();
  }
}

function sendPayload(payload) {
  latestPayload = payload;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  } else {
    connect();
  }
}

function candidateScore(record, now) {
  if (!record || now - record.receivedAt > SOURCE_STALE_MS) return -Infinity;
  const { state } = record;
  if (!state || !state.title) return -Infinity;

  let score;
  if (state.status === 'Playing') score = 10000;
  else if (state.status === 'Paused') score = 5000;
  else return -Infinity;

  if (record.audible) score += 500;
  if (record.activeTab) score += 200;
  if (record.frameId === 0 || record.topFrame) score += 100;
  score += Math.max(0, SOURCE_STALE_MS - (now - record.receivedAt)) / 100;
  return score;
}

function chooseSource(now = Date.now()) {
  let best = null;
  let bestScore = -Infinity;
  for (const record of sources.values()) {
    const score = candidateScore(record, now);
    if (score > bestScore) {
      best = record;
      bestScore = score;
    }
  }
  return best;
}

// A stored state is republished on the liveness interval, so its position is
// already up to a heartbeat old. Age it forward instead of shipping the value
// verbatim: the app reads a stale position as a backwards seek and re-anchors.
function agedState(record, now) {
  const state = record.state;
  if (!state || state.status !== 'Playing') return state;
  const elapsedMs = now - record.observedAt;
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) return state;

  const rate = Number.isFinite(state.rate) && state.rate > 0 ? state.rate : 1.0;
  const base = Number.isFinite(state.position) ? state.position : 0;
  let position = base + (elapsedMs / 1000) * rate;
  if (Number.isFinite(state.duration) && state.duration > 0) {
    position = Math.min(position, state.duration);
  }
  return { ...state, position: Math.max(0, position) };
}

function makePayload(record, stateOverride = null, now = Date.now()) {
  outboundSequence += 1;
  return {
    protocol_version: PROTOCOL_VERSION,
    sequence: outboundSequence,
    observed_at_ms: now,
    source: {
      instance_id: instanceId,
      tab_id: record.tabId,
      frame_id: record.frameId,
    },
    state: stateOverride || agedState(record, now),
  };
}

function publishSelection(now = Date.now()) {
  const next = chooseSource(now);
  if (next) {
    selectedSourceKey = next.key;
    sendPayload(makePayload(next, null, now));
    return;
  }

  if (selectedSourceKey && sources.has(selectedSourceKey)) {
    const previous = sources.get(selectedSourceKey);
    sendPayload(makePayload(previous, {
      title: '',
      artist: '',
      album: '',
      status: 'Stopped',
      position: 0,
      duration: 0,
      rate: 1.0,
    }, now));
  } else if (latestPayload && latestPayload.source) {
    const previous = {
      observedAt: now,
      tabId: latestPayload.source.tab_id,
      frameId: latestPayload.source.frame_id,
    };
    sendPayload(makePayload(previous, {
      title: '',
      artist: '',
      album: '',
      status: 'Stopped',
      position: 0,
      duration: 0,
      rate: 1.0,
    }, now));
  }
  selectedSourceKey = null;
}

function removeTabSources(tabId) {
  let changed = false;
  for (const [key, record] of sources) {
    if (record.tabId === tabId) {
      sources.delete(key);
      changed = true;
    }
  }
  if (changed) publishSelection();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab && sender.tab.id;
  const frameId = Number.isInteger(sender.frameId) ? sender.frameId : 0;
  if (!Number.isInteger(tabId)) {
    sendResponse({ accepted: false });
    return false;
  }

  const key = sourceKey(tabId, frameId);
  if (message && message.type === 'MEDIA_SOURCE_REMOVED') {
    sources.delete(key);
    publishSelection();
    sendResponse({ accepted: true });
    return false;
  }

  if (!message || message.type !== 'MEDIA_STATE' || typeof message.state !== 'object') {
    sendResponse({ accepted: false });
    return false;
  }

  const previous = sources.get(key);
  const localSequence = Number(message.local_sequence);
  if (
    !Number.isInteger(localSequence) || localSequence < 0 ||
    (previous && localSequence <= previous.localSequence)
  ) {
    sendResponse({ accepted: false });
    return false;
  }

  sources.set(key, {
    key,
    tabId,
    frameId,
    topFrame: Boolean(message.top_frame),
    localSequence,
    observedAt: Number(message.observed_at_ms) || Date.now(),
    receivedAt: Date.now(),
    activeTab: Boolean(sender.tab.active),
    audible: Boolean(sender.tab.audible),
    state: message.state,
  });
  publishSelection();
  sendResponse({ accepted: true });
  return false;
});

chrome.tabs.onRemoved.addListener(removeTabSources);
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'loading') {
    removeTabSources(tabId);
    return;
  }
  let changed = false;
  for (const record of sources.values()) {
    if (record.tabId === tabId) {
      record.activeTab = Boolean(tab.active);
      record.audible = Boolean(tab.audible);
      changed = true;
    }
  }
  if (changed) publishSelection();
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local' || !changes.wsPort) return;
  const nextPort = validPort(changes.wsPort.newValue);
  if (nextPort === port) return;
  port = nextPort;
  if (ws) ws.close();
  ws = null;
  connect();
});

setInterval(() => {
  const now = Date.now();
  let changed = false;
  for (const [key, record] of sources) {
    if (now - record.receivedAt > SOURCE_STALE_MS) {
      sources.delete(key);
      changed = true;
    }
  }
  if (changed || selectedSourceKey) publishSelection(now);
}, 2000);

loadPort().finally(connect);
