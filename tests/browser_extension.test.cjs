const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const extensionPath = path.join(__dirname, '..', 'browser-extension');
const manifest = JSON.parse(fs.readFileSync(path.join(extensionPath, 'manifest.json'), 'utf8'));
const popupSource = fs.readFileSync(path.join(extensionPath, 'popup.js'), 'utf8');

function runPopup(activeTabUrl) {
  const elements = new Map();
  const createdTabs = [];
  function element(selector) {
    if (!elements.has(selector)) {
      elements.set(selector, {
        selector,
        disabled: true,
        textContent: '',
        listeners: new Map(),
        addEventListener(type, callback) { this.listeners.set(type, callback); },
      });
    }
    return elements.get(selector);
  }
  const document = { querySelector: element };
  const chrome = {
    tabs: {
      query(_query, callback) { callback([{ url: activeTabUrl }]); },
      async create(options) { createdTabs.push(options); },
    },
  };
  vm.runInNewContext(popupSource, { document, chrome, URL, URLSearchParams }, { filename: 'popup.js' });
  return { elements, createdTabs };
}

test('extension manifest uses a user-initiated temporary tab permission only', () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ['activeTab']);
  assert.equal('host_permissions' in manifest, false);
  assert.equal(manifest.action.default_popup, 'popup.html');
});

test('extension opens a reviewable dashboard link only after user click', async () => {
  const pageUrl = 'https://news.example/world?section=top#latest';
  const popup = runPopup(pageUrl);
  const button = popup.elements.get('#send-page');
  assert.equal(button.disabled, false);
  assert.equal(popup.createdTabs.length, 0);
  await button.listeners.get('click')();
  assert.equal(popup.createdTabs.length, 1);
  const dashboardUrl = new URL(popup.createdTabs[0].url);
  assert.equal(dashboardUrl.origin, 'http://127.0.0.1:5000');
  assert.equal(new URLSearchParams(dashboardUrl.hash.slice(1)).get('addUrl'), pageUrl);
  assert.match(popup.elements.get('#status').textContent, /Review the URL/);
});

test('extension opens the dashboard to manage existing pages without using the active tab URL', async () => {
  const popup = runPopup('chrome://extensions/');
  const manageButton = popup.elements.get('#manage-pages');
  assert.equal(popup.createdTabs.length, 0);
  await manageButton.listeners.get('click')();
  assert.equal(popup.createdTabs.length, 1);
  assert.equal(popup.createdTabs[0].url, 'http://127.0.0.1:5000/');
  assert.match(popup.elements.get('#status').textContent, /Manage monitored pages/);
});

test('extension rejects non-web and credentialed active tabs', () => {
  for (const url of ['chrome://extensions/', 'file:///C:/private.html', 'https://user:pass@example.com/']) {
    const popup = runPopup(url);
    assert.equal(popup.elements.get('#send-page').disabled, true);
    assert.equal(popup.createdTabs.length, 0);
    assert.notEqual(popup.elements.get('#status').textContent, '');
  }
});
