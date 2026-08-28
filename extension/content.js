(function () {
  'use strict';

  const HEARTBEAT_MS = 1500;
  let localSequence = 0;
  let lastSignature = '';
  let lastSentAt = 0;

  function finiteNumber(value, fallback = 0) {
    return Number.isFinite(value) ? value : fallback;
  }

  function mediaScore(element) {
    if (!element || element.readyState <= 0) return -1;
    let score = 0;
    if (!element.paused && !element.ended) score += 1000;
    else if (element.currentTime > 0 || element.readyState >= 2) score += 200;
    if (Number.isFinite(element.duration) && element.duration > 0) score += 50;
    if (!element.muted && element.volume > 0) score += 20;
    return score;
  }

  function currentMediaElement() {
    const elements = Array.from(document.querySelectorAll('video, audio'));
    let best = null;
    let bestScore = -1;
    for (const element of elements) {
      const score = mediaScore(element);
      if (score > bestScore) {
        best = element;
        bestScore = score;
      }
    }
    return best;
  }

  function metadataFor(player) {
    const media = navigator.mediaSession;
    if (media && media.metadata) {
      return {
        title: media.metadata.title || '',
        artist: media.metadata.artist || '',
        album: media.metadata.album || '',
      };
    }

    if (window === window.top && player) {
      return {
        title: document.title || '',
        artist: '',
        album: '',
      };
    }
    return { title: '', artist: '', album: '' };
  }

  function readState() {
    const player = currentMediaElement();
    const metadata = metadataFor(player);
    let status = 'Stopped';
    if (player) {
      if (!player.paused && !player.ended) status = 'Playing';
      else if (player.currentTime > 0 || player.readyState >= 2) status = 'Paused';
    }

    return {
      title: metadata.title,
      artist: metadata.artist,
      album: metadata.album,
      status,
      position: player ? Math.max(0, finiteNumber(player.currentTime)) : 0,
      duration: player ? Math.max(0, finiteNumber(player.duration)) : 0,
      rate: player ? Math.max(0.01, finiteNumber(player.playbackRate, 1.0)) : 1.0,
    };
  }

  function signature(state) {
    return JSON.stringify({
      title: state.title,
      artist: state.artist,
      album: state.album,
      status: state.status,
      duration: Math.round(state.duration * 10),
      rate: state.rate,
    });
  }

  function publish(force = false) {
    const state = readState();
    const now = Date.now();
    const nextSignature = signature(state);
    if (!force && nextSignature === lastSignature && now - lastSentAt < HEARTBEAT_MS) {
      return;
    }

    lastSignature = nextSignature;
    lastSentAt = now;
    localSequence += 1;
    chrome.runtime.sendMessage({
      type: 'MEDIA_STATE',
      local_sequence: localSequence,
      observed_at_ms: now,
      top_frame: window === window.top,
      state,
    }).catch(() => {});
  }

  const mediaEvents = [
    'play', 'playing', 'pause', 'ended', 'seeking', 'seeked',
    'ratechange', 'durationchange', 'loadedmetadata', 'emptied',
  ];
  for (const eventName of mediaEvents) {
    document.addEventListener(eventName, () => publish(true), true);
  }

  window.addEventListener('pagehide', () => {
    chrome.runtime.sendMessage({ type: 'MEDIA_SOURCE_REMOVED' }).catch(() => {});
  });

  setInterval(() => publish(false), 500);
  publish(true);
})();
