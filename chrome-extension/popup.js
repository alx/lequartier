'use strict';

const input  = document.getElementById('backend-url');
const status = document.getElementById('status');

chrome.storage.sync.get('backendUrl', ({ backendUrl }) => {
  input.value = backendUrl || 'http://localhost:5010';
});

document.getElementById('save-btn').addEventListener('click', () => {
  const val = input.value.trim();
  chrome.storage.sync.set({ backendUrl: val }, () => {
    status.textContent = 'Saved!';
    setTimeout(() => { status.textContent = ''; }, 2000);
  });
});
