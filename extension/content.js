(function () {
  'use strict';

  let lastState = {};
  let lastSend = 0;

  function readState() {
    const media = navigator.mediaSession;
    const video = document.querySelector('video');

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
      status: video ? (video.paused ? 'Paused' : 'Playing') : 'Stopped',
      position: video ? video.currentTime : 0,
      duration: video && video.duration ? video.duration : 0,
      rate: video && video.playbackRate ? video.playbackRate : 1.0,
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

    if (hasMedia && hasChanged(state)) {
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
