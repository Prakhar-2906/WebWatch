'use strict';

const pageUrl = document.querySelector('#page-url');
const sendButton = document.querySelector('#send-page');
const manageButton = document.querySelector('#manage-pages');
const status = document.querySelector('#status');
const dashboardAddress = 'http://127.0.0.1:5000/';
let activePageUrl = '';

chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const candidate = tabs?.[0]?.url || '';
  try {
    const parsed = new URL(candidate);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
      throw new Error('Only public HTTP or HTTPS pages without embedded credentials can be sent.');
    }
    activePageUrl = parsed.href;
    pageUrl.textContent = activePageUrl;
    sendButton.disabled = false;
  } catch (error) {
    sendButton.disabled = true;
    pageUrl.textContent = candidate || 'No active webpage is available.';
    status.textContent = error.message || 'Open a public webpage before using WebWatch Quick Add.';
  }
});

sendButton.addEventListener('click', async () => {
  if (!activePageUrl) return;
  const dashboardUrl = new URL(dashboardAddress);
  dashboardUrl.hash = new URLSearchParams({ addUrl: activePageUrl }).toString();
  try {
    await chrome.tabs.create({ url: dashboardUrl.href });
    status.textContent = 'WebWatch opened. Review the URL and submit the Add website form.';
  } catch (_error) {
    status.textContent = 'Could not open WebWatch. Start the local app and try again.';
  }
});

manageButton.addEventListener('click', async () => {
  try {
    await chrome.tabs.create({ url: dashboardAddress });
    status.textContent = 'WebWatch opened. Manage monitored pages from the dashboard.';
  } catch (_error) {
    status.textContent = 'Could not open WebWatch. Start the local app and try again.';
  }
});
