(function () {
  'use strict';

  let lastState = {};
  let lastSend = 0;

  function finiteNumber(value, fallback = 0) {
    return Number.isFinite(value) ? value : fallback;
  }

  function currentMediaElement() {
    const elements = Array.from(document.querySelectorAll('video, audio'));
    return elements.find((el) => !el.paused && el.readyState > 0) || elements[0] || null;
  }

  function readState() {
    const media = navigator.mediaSession;
    const player = currentMediaElement();

    const meta = media && media.metadata
      ? {
          title: media.metadata.title || '',
          artist: media.metadata.artist || '',
          album: media.metadata.album || '',
        }
      : { title: '', artist: '', album: '' };

    const state = {
      title: meta.title,
      artist: meta.artist,
      album: meta.album,
      status: player ? (player.paused ? 'Paused' : 'Playing') : 'Stopped',
      position: player ? finiteNumber(player.currentTime) : 0,
      duration: player ? finiteNumber(player.duration) : 0,
      rate: player ? finiteNumber(player.playbackRate, 1.0) : 1.0,
    };

    return state;
  }

  function hasChanged(curr) {
    if (!lastState.title) return true;
    return (
      curr.title !== lastState.title ||
      curr.artist !== lastState.artist ||
      curr.album !== lastState.album ||
      curr.status !== lastState.status ||
      Math.abs(curr.position - lastState.position) > 2
    );
  }

  setInterval(() => {
    const state = readState();
    const hasMedia = state.title || state.status !== 'Stopped';
    const wasTracking = lastState.title || lastState.status !== 'Stopped';

    if ((hasMedia || wasTracking) && hasChanged(state)) {
      lastState = state;
      chrome.runtime.sendMessage({ type: 'MEDIA_STATE', data: state }).catch(() => {});
      lastSend = Date.now();
    } else if (Date.now() - lastSend > 3000) {
      // keep-alive ping so the service worker does not sleep
      chrome.runtime.sendMessage({ type: 'KEEP_ALIVE' }).catch(() => {});
      lastSend = Date.now();
    }
  }, 500);
})();
