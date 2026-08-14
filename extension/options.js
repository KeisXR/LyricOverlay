'use strict';

const DEFAULT_PORT = 56789;
const portInput = document.getElementById('port');
const status = document.getElementById('status');

function validPort(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 1024 && number <= 65535;
}

async function restore() {
  const settings = await chrome.storage.local.get({ wsPort: DEFAULT_PORT });
  portInput.value = settings.wsPort;
}

async function save() {
  const value = Number(portInput.value);
  if (!validPort(value)) {
    status.textContent = '1024〜65535の整数を指定してください。';
    return;
  }
  await chrome.storage.local.set({ wsPort: value });
  status.textContent = '保存しました。Lyricaod側も同じポートに設定してください。';
}

document.getElementById('save').addEventListener('click', save);
restore();
