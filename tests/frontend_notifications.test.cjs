const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');

class MockElement {
  constructor(selector = '', resolveElement = () => new MockElement()) {
    this.selector = selector;
    this.resolveElement = resolveElement;
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this.isConnected = true;
    this.focused = false;
    this.checked = false;
    this.value = '';
    this.textContent = '';
    this.title = '';
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.children = [];
    this.listeners = new Map();
    this.classList = {
      toggle() {},
      add() {},
      remove() {},
    };
  }

  querySelector(selector) { return this.resolveElement(`${this.selector} ${selector}`); }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  append(...children) { children.forEach((child) => { child.parentNode = this; }); this.children.push(...children); }
  prepend(...children) { children.forEach((child) => { child.parentNode = this; }); this.children.unshift(...children); }
  replaceWith(replacement) {
    if (!this.parentNode) return;
    replacement.parentNode = this.parentNode;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1, replacement);
  }
  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      for (const child of node.children || []) {
        if (child.selector === selector) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  }
  replaceChildren(...children) { this.children = [...children]; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.get('close')?.(); }
  reset() {}
}

function makeHarness({ permission = 'granted', supported = true, events = [], storage = new Map(), lastRunAt = '2026-01-01T00:00:00Z', sites = [], jobs = [], alertSettings = null, alertSaveError = '', alertTestResponse = { ok: true }, alertTestError = '', historyBySite = {}, historyPagesBySite = {}, historyPage404BySite = [], historyPageJson404BySite = [], comparisonsBySite = {}, previewsBySite = {}, metricsBySite = {}, confirmResult = true, extensionHash = '' } = {}) {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, new MockElement(selector, element));
    return elements.get(selector);
  }

  const notifications = [];
  let permissionRequests = 0;
  let schedulePoll;
  let scheduleResponse = {
    enabled: true,
    interval_minutes: 1,
    last_run_at: lastRunAt,
    next_run_at: lastRunAt,
    run_status: 'completed',
    run_summary: { total: 1, successful: 1, failed: 0, changed: 0 },
  };
  let notificationEvents = events;
  let notificationTotal = events.length;
  const historyRequests = [];
  const legacyHistoryRequests = [];
  const selectorSaves = [];
  const requests = [];
  const alertSaves = [];
  const confirmations = [];
  let loadedSites = sites;
  const loadedAlertSettings = alertSettings || {
    email_enabled: false, email_recipient: '', email_configured: false, email_status: 'SMTP not configured',
    webhook_enabled: false, webhook_configured: false, webhook_chat_id: '',
    daily_digest_enabled: false, ai_summary_enabled: false, ai_summary_configured: false,
  };

  function MockNotification(title, options) {
    notifications.push({ title, options });
  }
  MockNotification.permission = permission;
  MockNotification.requestPermission = async () => {
    permissionRequests += 1;
    return MockNotification.permission;
  };

  const window = {
    isSecureContext: true,
    matchMedia: () => ({ matches: false }),
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval(callback) { schedulePoll = callback; return 1; },
    confirm(message) { confirmations.push(message); return confirmResult; },
  };
  const location = { hash: extensionHash, pathname: '/', search: '' };
  const history = { replaceState(_state, _title, url) { location.hash = ''; location.pathname = url; } };
  if (supported) window.Notification = MockNotification;

  const document = {
    title: '',
    cookie: 'webwatch_csrf=mock-csrf',
    body: new MockElement('body', element),
    documentElement: { dataset: {} },
    querySelector: element,
    createElement: (tag) => new MockElement(tag, element),
    createElementNS: (namespaceURI, tag) => {
      const node = new MockElement(tag, element);
      node.namespaceURI = namespaceURI;
      return node;
    },
    createTextNode: (text) => ({ textContent: text }),
  };
  const localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
  };

  async function fetch(url, options = {}) {
    requests.push({ url, method: options.method || 'GET' });
    let payload;
    let status = 200;
    let bodyText;
    if (url.startsWith('/api/schedule')) payload = scheduleResponse;
    else if (url.startsWith('/api/notifications')) payload = notificationEvents;
    else if (url.startsWith('/api/jobs')) payload = jobs;
    else if (url === '/api/alerts' && options.method === 'PUT') {
      const saved = JSON.parse(options.body);
      alertSaves.push(saved);
      if (alertSaveError) { status = 400; payload = { error: alertSaveError }; }
      else payload = { ...loadedAlertSettings, ...saved, email_configured: true,
        webhook_configured: Boolean(saved.webhook_url || loadedAlertSettings.webhook_configured) };
    }
    else if (url === '/api/alerts/test' && options.method === 'POST') {
      if (alertTestError) { status = 502; payload = { error: alertTestError }; }
      else payload = alertTestResponse;
    }
    else if (url === '/api/alerts') payload = loadedAlertSettings;
    else if (/^\/api\/websites\/\d+\/history\/page/.test(url)) {
      const websiteId = Number(url.split('/')[3]);
      historyRequests.push(websiteId);
      if (historyPage404BySite.includes(websiteId)) {
        status = 404;
        bodyText = '<!doctype html><title>Not Found</title>';
      } else if (historyPageJson404BySite.includes(websiteId)) {
        status = 404;
        bodyText = JSON.stringify({ error: 'Website not found.' });
      }
      const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset') || 0);
      if (status === 200) {
        payload = historyPagesBySite[websiteId]?.[offset]
          || { history: historyBySite[websiteId] || [], has_more: false, next_offset: 20 };
      }
    }
    else if (/^\/api\/websites\/\d+\/history$/.test(url)) {
      const websiteId = Number(url.split('/')[3]);
      legacyHistoryRequests.push(websiteId);
      payload = historyBySite[websiteId] || [];
    }
    else if (/^\/api\/websites\/\d+\/compare\?/.test(url)) {
      const websiteId = Number(url.split('/')[3]);
      payload = comparisonsBySite[websiteId] || {};
    }
    else if (/^\/api\/websites\/\d+\/metrics\?/.test(url)) {
      const websiteId = Number(url.split('/')[3]);
      const selector = new URL(url, 'http://localhost').searchParams.get('selector');
      payload = metricsBySite[websiteId]?.[selector] || { selector, points: [] };
    }
    else if (/^\/api\/websites\/\d+\/preview$/.test(url)) {
      const websiteId = Number(url.split('/')[3]);
      payload = previewsBySite[websiteId] || { regions: [], watched: [], ignored: [] };
    }
    else if (/^\/api\/websites\/\d+$/.test(url) && options.method === 'DELETE') {
      const websiteId = Number(url.split('/').at(-1));
      loadedSites = loadedSites.filter((site) => site.id !== websiteId);
      payload = { ok: true };
    }
    else if (/^\/api\/websites\/\d+\/selectors$/.test(url) && options.method === 'PUT') {
      selectorSaves.push(JSON.parse(options.body));
      payload = {};
    }
    else if (url.startsWith('/api/websites')) payload = loadedSites;
    else if (url.startsWith('/api/stats')) {
      payload = { websites: 0, successful_checks: 0, detected_changes: 0, demo_runs: 0 };
    } else payload = {};
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        has: (name) => name.toLowerCase() === 'x-total-count' && url.startsWith('/api/notifications'),
        get: () => String(notificationTotal),
      },
      text: async () => bodyText ?? JSON.stringify(payload),
    };
  }

  const context = {
    document,
    window,
    location,
    history,
    localStorage,
    fetch,
    URL,
    URLSearchParams,
    AbortController,
    Notification: supported ? MockNotification : undefined,
  };
  vm.runInNewContext(appSource, context, { filename: 'static/app.js' });

  async function settle() {
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
  }

  return {
    elements,
    notifications,
    historyRequests,
    legacyHistoryRequests,
    selectorSaves,
    requests,
    alertSaves,
    confirmations,
    storage,
    get permissionRequests() { return permissionRequests; },
    get schedulePoll() { return schedulePoll; },
    setEvents(value, total = value.length) { notificationEvents = value; notificationTotal = total; },
    setSchedule(value) { scheduleResponse = value; },
    async start() { await settle(); },
    async poll() { await schedulePoll(); },
    async clickNotificationPermission() {
      await element('#enable-browser-notifications').listeners.get('click')();
    },
    setPermission(value) { MockNotification.permission = value; },
    async click(selector) {
      const target = element(selector);
      await target.listeners.get('click')({ currentTarget: target });
    },
    async submit(selector) {
      await element(selector).listeners.get('submit')({ preventDefault() {} });
    },
  };
}

test('browser extension link pre-fills a reviewable public URL without submitting it', async () => {
  const encodedUrl = encodeURIComponent('https://news.example/path?section=world');
  const harness = makeHarness({ extensionHash: `#addUrl=${encodedUrl}` });
  await harness.start();
  assert.equal(harness.elements.get('#website-url').value, 'https://news.example/path?section=world');
  assert.equal(harness.elements.get('#website-name').value, 'news.example');
  assert.match(harness.elements.get('#form-message').textContent, /Review the public URL and click Add website/);
  assert.equal(harness.elements.get('#website-url').focused, true);
  assert.equal(harness.requests.some((request) => request.url === '/api/websites' && request.method === 'POST'), false);
});

test('persistent check queue renders retry and failure details as text', async () => {
  const harness = makeHarness({ jobs: [
    { id: 8, website_id: 1, display_name: 'News & updates', status: 'retry', source: 'scheduled', attempts: 2, created_at: '2026-10-16T08:00:00Z', updated_at: '2026-10-16T08:01:00Z', next_run_at: '2026-10-16T08:05:00Z', last_error: 'Temporary connection failure' },
    { id: 7, website_id: 2, display_name: '<script>bad</script>', status: 'failed', source: 'manual', attempts: 3, created_at: '2026-10-16T07:00:00Z', updated_at: '2026-10-16T07:03:00Z', next_run_at: null, last_error: 'HTTP 403' },
  ] });
  await harness.start();
  const list = harness.elements.get('#job-queue-list');
  assert.equal(list.children.length, 2);
  const content = findAll(list, (node) => typeof node.textContent === 'string').map((node) => node.textContent).join(' ');
  assert.match(content, /News & updates/);
  assert.match(content, /Waiting to retry/);
  assert.match(content, /Temporary connection failure/);
  assert.match(content, /HTTP 403/);
  assert.match(content, /<script>bad<\/script>/, 'untrusted names are assigned through textContent');
  assert.match(harness.elements.get('#job-queue-status').textContent, /1 active/);
  assert.equal(harness.elements.get('#job-queue-toggle').hidden, true);
});

test('persistent queue defaults to the latest two and expands accessibly to all available recent jobs', async () => {
  const empty = makeHarness();
  await empty.start();
  assert.equal(empty.elements.get('#job-queue-list').children[0].textContent, 'No check jobs yet. Run Check now or Check all to start.');
  assert.equal(empty.elements.get('#job-queue-toggle').hidden, true);

  for (const count of [1, 2]) {
    const small = makeHarness({ jobs: Array.from({ length: count }, (_, index) => ({
      id: index + 1, website_id: index + 1, display_name: `Page ${index + 1}`, status: 'succeeded', attempts: 1,
      source: 'manual', created_at: `2026-10-16T0${index}:00:00Z`, updated_at: `2026-10-16T0${index}:00:00Z`,
    })) });
    await small.start();
    assert.equal(small.elements.get('#job-queue-list').children.length, count);
    assert.equal(small.elements.get('#job-queue-toggle').hidden, true);
  }

  const expanded = makeHarness({ jobs: [
    { id: 3, website_id: 3, display_name: 'Page 3', status: 'running', attempts: 1, source: 'manual', updated_at: '2026-10-16T03:00:00Z' },
    { id: 5, website_id: 5, display_name: 'Page 5', status: 'retry', attempts: 2, source: 'scheduled', updated_at: '2026-10-16T05:00:00Z', next_run_at: '2026-10-16T06:00:00Z' },
    { id: 1, website_id: 1, display_name: 'Page 1', status: 'failed', attempts: 3, source: 'manual', updated_at: '2026-10-16T01:00:00Z', last_error: 'HTTP 403' },
    { id: 4, website_id: 4, display_name: 'Page 4', status: 'succeeded', attempts: 1, source: 'scheduled', updated_at: '2026-10-16T04:00:00Z' },
    { id: 2, website_id: 2, display_name: 'Page 2', status: 'queued', attempts: 0, source: 'scheduled', created_at: '2026-10-16T02:00:00Z' },
  ] });
  await expanded.start();
  const list = expanded.elements.get('#job-queue-list');
  const toggle = expanded.elements.get('#job-queue-toggle');
  assert.deepEqual(list.children.map((item) => item.dataset.jobId), ['5', '4', '3', '2', '1']);
  assert.deepEqual(list.children.map((item) => item.hidden), [false, false, true, true, true]);
  assert.equal(toggle.textContent, 'View more');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.match(expanded.elements.get('#job-queue-status').textContent, /3 active · 1 running · 1 queued · 1 waiting to retry/);
  await expanded.click('#job-queue-toggle');
  assert.equal(toggle.textContent, 'Show less');
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.ok(list.children.every((item) => !item.hidden));
  await expanded.click('#job-queue-toggle');
  assert.deepEqual(list.children.map((item) => item.hidden), [false, false, true, true, true]);
});

test('alert settings save all options, clear the secret field, and report test delivery states', async () => {
  const harness = makeHarness({ alertTestResponse: { ok: true } });
  await harness.start();
  const values = {
    '#alert-email-enabled': true,
    '#alert-email-recipient': 'reader@example.test',
    '#alert-webhook-enabled': true,
    '#alert-webhook-url': 'https://hooks.example/receiver?token=private-value',
    '#alert-webhook-chat-id': '12345',
    '#alert-daily-digest-enabled': true,
    '#ai-summary-enabled': true,
  };
  for (const [selector, value] of Object.entries(values)) {
    const input = harness.elements.get(selector);
    if (typeof value === 'boolean') input.checked = value;
    else input.value = value;
  }
  await harness.submit('#alert-settings-form');
  assert.deepEqual(harness.alertSaves[0], {
    email_enabled: true, email_recipient: 'reader@example.test', webhook_enabled: true,
    webhook_url: 'https://hooks.example/receiver?token=private-value', webhook_chat_id: '12345',
    daily_digest_enabled: true, ai_summary_enabled: true,
  });
  assert.equal(harness.elements.get('#alert-webhook-url').value, '');
  assert.equal(harness.elements.get('#alert-settings-status').dataset.kind, 'success');
  await harness.click('#alert-test');
  assert.match(harness.elements.get('#alert-settings-status').textContent, /Test alert delivered/);

  const failedTest = makeHarness({ alertTestError: 'Delivery unavailable.' });
  await failedTest.start();
  await failedTest.click('#alert-test');
  assert.match(failedTest.elements.get('#alert-settings-status').textContent, /Delivery unavailable/);
  assert.equal(failedTest.elements.get('#alert-settings-status').dataset.kind, 'error');
});

test('alert validation errors never echo the submitted webhook secret', async () => {
  const secretUrl = 'https://hooks.example/receiver?token=private-value';
  const harness = makeHarness({ alertSaveError: `Webhook URL rejected: ${secretUrl}` });
  await harness.start();
  harness.elements.get('#alert-webhook-enabled').checked = true;
  harness.elements.get('#alert-webhook-url').value = secretUrl;
  await harness.submit('#alert-settings-form');
  const feedback = harness.elements.get('#alert-settings-status');
  assert.doesNotMatch(feedback.textContent, /private-value/);
  assert.match(feedback.textContent, /the supplied webhook URL/);
  assert.equal(feedback.dataset.kind, 'error');
});

function event(id, eventType) {
  return {
    id,
    website_id: 1,
    website_name: 'Example test site',
    website_url: 'https://example.invalid/',
    category: 'Availability',
    event_type: eventType,
    confidence: eventType === 'recovered' ? 'observed' : 'suspected',
    explanation: eventType === 'recovered' ? 'The check succeeded again.' : 'A connection failure was observed.',
    occurred_at: `2026-01-01T00:00:0${id}Z`,
  };
}

function findBy(root, predicate) {
  if (root && predicate(root)) return root;
  for (const child of root?.children || []) {
    const match = findBy(child, predicate);
    if (match) return match;
  }
  return null;
}

function findAll(root, predicate, matches = []) {
  if (root && predicate(root)) matches.push(root);
  for (const child of root?.children || []) findAll(child, predicate, matches);
  return matches;
}

test('saved history is quiet on load; scheduled outage and recovery notify once each', async () => {
  const oldEvent = event(1, 'unreachable');
  const outage = event(2, 'unreachable');
  const recovery = event(3, 'recovered');
  const harness = makeHarness({ events: [oldEvent] });
  await harness.start();
  assert.equal(harness.notifications.length, 0, 'dashboard load must not replay saved events');

  const scheduleAt = (value) => ({
    enabled: true,
    interval_minutes: 1,
    last_run_at: value,
    next_run_at: value,
    run_status: 'completed',
    run_summary: { total: 1, successful: 1, failed: 0, changed: 0 },
  });

  harness.setEvents([oldEvent, outage]);
  harness.setSchedule(scheduleAt('2026-01-01T00:01:00Z'));
  await harness.poll();
  assert.equal(harness.notifications.length, 1);
  assert.match(harness.notifications[0].title, /may be unreachable/);

  harness.setEvents([oldEvent, outage, recovery]);
  harness.setSchedule(scheduleAt('2026-01-01T00:02:00Z'));
  await harness.poll();
  assert.equal(harness.notifications.length, 2);
  assert.match(harness.notifications[1].title, /recovered/);

  harness.setSchedule(scheduleAt('2026-01-01T00:03:00Z'));
  await harness.poll();
  assert.equal(harness.notifications.length, 2, 'previously seen event IDs must not dispatch again');
});

test('existing events start read; expanding a new event acknowledges it across reloads', async () => {
  const oldEvent = event(1, 'unreachable');
  const newEvent = event(2, 'recovered');
  const storage = new Map();
  const harness = makeHarness({ events: [oldEvent], storage });
  await harness.start();
  assert.equal(harness.elements.get('#notification-total-count').textContent, '1 total');
  assert.equal(harness.elements.get('#notification-unread-count').hidden, true);

  harness.setEvents([oldEvent, newEvent], 2);
  harness.setSchedule({
    enabled: true,
    interval_minutes: 1,
    last_run_at: '2026-01-01T00:01:00Z',
    next_run_at: '2026-01-01T00:02:00Z',
    run_status: 'completed',
    run_summary: { total: 1, successful: 1, failed: 0, changed: 0 },
  });
  await harness.poll();

  const list = harness.elements.get('#notification-list');
  const details = findBy(list, (node) => node.id === 'notification-details-2');
  const toggle = findBy(list, (node) => node.getAttribute?.('aria-controls') === 'notification-details-2');
  const badge = findBy(list, (node) => node.className === 'notification-new-badge' && !node.hidden);
  assert.ok(details);
  assert.ok(toggle);
  assert.ok(badge);
  assert.equal(harness.elements.get('#notification-total-count').textContent, '2 total');
  assert.equal(harness.elements.get('#notification-unread-count').textContent, '1 new');
  assert.equal(details.hidden, true);

  await toggle.listeners.get('click')();
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(details.hidden, false);
  assert.equal(badge.hidden, true);
  assert.equal(harness.elements.get('#notification-unread-count').hidden, true);
  assert.deepEqual(JSON.parse(storage.get('webwatch.notifications.read.v1')), [1, 2]);

  const reloaded = makeHarness({ events: [oldEvent, newEvent], storage });
  await reloaded.start();
  assert.equal(reloaded.elements.get('#notification-unread-count').hidden, true);
  assert.equal(reloaded.notifications.length, 0, 'reload must not replay serious events');
});

test('serious event history shows three newest rows and expands/collapses without marking hidden events read', async () => {
  for (const count of [0, 1, 3, 5]) {
    const events = Array.from({ length: count }, (_, index) => event(index + 1, index % 2 ? 'recovered' : 'unreachable')).reverse();
    const harness = makeHarness({ events });
    await harness.start();
    const list = harness.elements.get('#notification-list');
    const toggle = harness.elements.get('#notification-view-toggle');
    const rows = () => findAll(list, (node) => node.className === 'notification-entry');

    if (count === 0) {
      assert.equal(rows().length, 0);
      assert.match(list.children[0].textContent, /No serious events recorded/);
    } else {
      assert.equal(rows().length, Math.min(count, 3));
    }
    assert.equal(toggle.hidden, count <= 3);
    if (count > 3) {
      assert.equal(toggle.textContent, 'View all 5 events');
      assert.equal(toggle.getAttribute('aria-expanded'), 'false');
      const newestIds = findAll(list, (node) => node.className === 'notification-entry-toggle')
        .map((node) => node.getAttribute('aria-controls'));
      assert.deepEqual(newestIds, ['notification-details-5', 'notification-details-4', 'notification-details-3']);
      await toggle.listeners.get('click')();
      assert.equal(rows().length, 5);
      assert.equal(toggle.textContent, 'Show less');
      assert.equal(toggle.getAttribute('aria-expanded'), 'true');
      await toggle.listeners.get('click')();
      assert.equal(rows().length, 3);
      assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    }
  }

  const oldEvent = event(1, 'unreachable');
  const harness = makeHarness({ events: [oldEvent] });
  await harness.start();
  harness.setEvents(Array.from({ length: 7 }, (_, index) => event(index + 1, index % 2 ? 'recovered' : 'unreachable')).reverse(), 7);
  harness.setSchedule({
    enabled: true,
    interval_minutes: 1,
    last_run_at: '2026-01-01T00:01:00Z',
    next_run_at: '2026-01-01T00:02:00Z',
    run_status: 'completed',
    run_summary: { total: 1, successful: 1, failed: 0, changed: 0 },
  });
  await harness.poll();
  const list = harness.elements.get('#notification-list');
  const toggle = harness.elements.get('#notification-view-toggle');
  assert.equal(harness.elements.get('#notification-unread-count').textContent, '6 new');
  assert.equal(findAll(list, (node) => node.className === 'notification-entry').length, 3);
  await toggle.listeners.get('click')();
  assert.equal(findAll(list, (node) => node.className === 'notification-entry').length, 7);
  assert.equal(harness.elements.get('#notification-unread-count').textContent, '6 new', 'revealing older events does not acknowledge them');
  const hiddenEventToggle = findBy(list, (node) => node.getAttribute?.('aria-controls') === 'notification-details-4');
  assert.ok(hiddenEventToggle);
  await hiddenEventToggle.listeners.get('click')();
  assert.equal(harness.elements.get('#notification-unread-count').textContent, '5 new', 'only expanding that event marks it read');
});

test('permission control reflects granted, denied, and unsupported browser states', async () => {
  const granted = makeHarness({ permission: 'granted' });
  await granted.start();
  assert.equal(granted.elements.get('#enable-browser-notifications').textContent, 'Browser notifications enabled');
  assert.equal(granted.elements.get('#enable-browser-notifications').disabled, true);
  assert.equal(granted.permissionRequests, 0);

  const denied = makeHarness({ permission: 'denied' });
  await denied.start();
  assert.equal(denied.elements.get('#enable-browser-notifications').textContent, 'Notifications blocked in browser settings');
  assert.equal(denied.elements.get('#enable-browser-notifications').disabled, true);
  assert.match(denied.elements.get('#browser-notification-status').textContent, /browser settings/);

  const unsupported = makeHarness({ supported: false });
  await unsupported.start();
  assert.equal(unsupported.elements.get('#enable-browser-notifications').textContent, 'Browser notifications unavailable');
  assert.equal(unsupported.elements.get('#enable-browser-notifications').disabled, true);
});

test('permission is requested only after user action', async () => {
  const harness = makeHarness({ permission: 'default' });
  await harness.start();
  assert.equal(harness.permissionRequests, 0);
  await harness.clickNotificationPermission();
  assert.equal(harness.permissionRequests, 1);
});

test('website history opens in a modal, shows compact entries, details, and restores focus', async () => {
  const sites = [
    { id: 11, url: 'https://first.example/path', is_demo: false, last_check_status: 'unchanged' },
    { id: 22, url: 'https://second.example/page', is_demo: false, last_check_status: 'changed' },
  ];
  const history = [
    { id: 1, checked_at: '2026-10-15T10:00:00Z', status: 'baseline', added_content: '', removed_content: '', details_truncated: false, volatile_hints: [] },
    { id: 2, checked_at: '2026-10-15T11:00:00Z', status: 'changed', added_content: 'New headline', removed_content: 'Old headline', details_truncated: false, volatile_hints: [] },
  ];
  const harness = makeHarness({ sites, historyBySite: { 22: history } });
  await harness.start();
  const websiteRows = findAll(harness.elements.get('#website-list'), (node) => node.className === 'website-row');
  assert.equal(websiteRows.length, 2);
  const historyButtons = findAll(harness.elements.get('#website-list'), (node) => node.className === 'history-button');
  assert.equal(historyButtons.length, 2);
  await historyButtons[1].listeners.get('click')();

  const dialog = harness.elements.get('#history-dialog');
  const content = harness.elements.get('#history-dialog-content');
  assert.equal(dialog.open, true);
  assert.deepEqual(harness.historyRequests, [22], 'the selected card ID must be used for its history request');
  assert.equal(harness.elements.get('#history-dialog-title').textContent, 'second.example');
  assert.equal(harness.elements.get('#history-dialog-url').textContent, sites[1].url);
  const entries = findAll(content, (node) => node.className === 'history-modal-entry');
  assert.equal(entries.length, 2);
  assert.equal(findAll(content, (node) => node.className === 'change-detail').length, 0, 'full comparison details stay out of the compact list');

  const detailsButtons = findAll(content, (node) => node.className === 'history-details-button');
  await detailsButtons[1].listeners.get('click')();
  assert.ok(findBy(content, (node) => node.className === 'change-detail'));
  const backButton = findBy(content, (node) => node.className === 'history-back-button');
  await backButton.listeners.get('click')();
  assert.equal(findAll(content, (node) => node.className === 'history-modal-entry').length, 2);

  const closeButton = harness.elements.get('#history-dialog-close');
  await closeButton.listeners.get('click')();
  assert.equal(dialog.open, false);
  assert.equal(historyButtons[1].focused, true, 'focus returns to the invoking History control');

  await historyButtons[0].listeners.get('click')();
  assert.match(findBy(content, (node) => node.className === 'history-dialog-state').textContent, /No successful checks/);
  dialog.listeners.get('cancel')({ preventDefault() {} });
  assert.equal(dialog.open, false, 'Escape closes the modal');

  await historyButtons[1].listeners.get('click')();
  dialog.listeners.get('click')({ target: dialog });
  assert.equal(dialog.open, false, 'backdrop click closes the modal');
});

test('website history falls back to the legacy route when a server lacks the paginated endpoint', async () => {
  const site = { id: 74, url: 'https://indiatv.example/', display_name: 'IndiaTV', is_demo: false };
  const history = [
    { id: 1, checked_at: '2026-10-15T10:00:00Z', status: 'baseline', added_content: '', removed_content: '', details_truncated: false, volatile_hints: [] },
    { id: 2, checked_at: '2026-10-15T11:00:00Z', status: 'changed', added_content: 'New headline', removed_content: 'Old headline', details_truncated: false, volatile_hints: [] },
  ];
  const harness = makeHarness({
    sites: [site],
    historyBySite: { 74: history },
    historyPage404BySite: [74],
  });
  await harness.start();

  const historyButton = findBy(harness.elements.get('#website-list'), (node) => node.className === 'history-button');
  await historyButton.listeners.get('click')();

  assert.deepEqual(harness.historyRequests, [74]);
  assert.deepEqual(harness.legacyHistoryRequests, [74]);
  assert.equal(findAll(harness.elements.get('#history-dialog-content'), (node) => node.className === 'history-modal-entry').length, 2);
});

test('website history does not hide a genuine JSON 404 behind the legacy route', async () => {
  const site = { id: 75, url: 'https://deleted.example/', display_name: 'Deleted', is_demo: false };
  const harness = makeHarness({ sites: [site], historyPageJson404BySite: [75] });
  await harness.start();
  const button = findBy(harness.elements.get('#website-list'), (node) => node.className === 'history-button');
  await button.listeners.get('click')();

  assert.deepEqual(harness.historyRequests, [75]);
  assert.deepEqual(harness.legacyHistoryRequests, []);
  assert.match(findBy(harness.elements.get('#history-dialog-content'), (node) => node.className === 'history-dialog-error').textContent, /Website not found/);
});

test('website rows distinguish due, disabled, never-checked, invalid-time, and demo schedules', async () => {
  const sites = [
    { id: 81, url: 'https://due.example/', display_name: 'Due', is_demo: false, automatic_checks_enabled: true, last_checked_at: '2000-01-01T00:00:00+00:00', next_check_at: '2000-01-01T00:15:00+00:00', check_interval_minutes: 15 },
    { id: 82, url: 'https://new.example/', display_name: 'New', is_demo: false, automatic_checks_enabled: true, last_checked_at: null, next_check_at: null, check_interval_minutes: 60 },
    { id: 83, url: 'https://invalid.example/', display_name: 'Invalid time', is_demo: false, automatic_checks_enabled: true, last_checked_at: 'not-a-time', next_check_at: null, check_interval_minutes: 30 },
    { id: 84, url: 'https://disabled.example/', display_name: 'Disabled', is_demo: false, automatic_checks_enabled: false, last_checked_at: '2026-10-15T10:00:00Z', next_check_at: null, check_interval_minutes: 180 },
    { id: 85, url: 'demo://webwatch/run/85', display_name: 'Demo', is_demo: true, automatic_checks_enabled: false, last_checked_at: null, next_check_at: null, check_interval_minutes: 60, demo_version: 1 },
  ];
  const harness = makeHarness({ sites });
  await harness.start();
  const rows = findAll(harness.elements.get('#website-list'), (node) => node.className === 'website-row');
  const descriptions = rows.map((row) => findBy(row, (node) => node.className === 'site-next-check').textContent);
  assert.match(descriptions[0], /Due now/);
  assert.match(descriptions[1], /Due on next scheduler poll/);
  assert.match(descriptions[2], /Schedule unavailable/);
  assert.match(descriptions[3], /Automatic checks disabled/);
  assert.match(descriptions[4], /Demo checks are manual only/);
  assert.ok(findAll(rows[2], (node) => /Unknown time/.test(node.textContent)).length > 0);
});

test('compact website rows keep primary actions visible and management actions in More', async () => {
  const sites = [{
    id: 41,
    url: 'https://compact.example/path',
    display_name: 'A compact test site',
    is_demo: false,
    is_hidden: false,
    last_check_status: 'unchanged',
    check_interval_minutes: 60,
    unchanged_history_limit: 17,
    changed_history_limit: 23,
    serious_event_limit: 29,
  }];
  const harness = makeHarness({ sites });
  await harness.start();
  const row = findBy(harness.elements.get('#website-list'), (node) => node.className === 'website-row');
  assert.ok(findBy(row, (node) => node.className === 'history-button'));
  assert.ok(findBy(row, (node) => node.className === 'check-button'));

  const menu = findBy(row, (node) => node.className === 'site-more-menu');
  assert.ok(menu);
  assert.equal(menu.children[0]?.textContent, 'More');
  assert.ok(findBy(menu, (node) => node.className === 'visibility-button'));
  assert.ok(findBy(menu, (node) => node.className === 'remove-button'));
  assert.ok(findBy(menu, (node) => node.className === 'site-settings'));
  const settings = findBy(menu, (node) => node.className === 'site-settings');
  const sensitivity = findBy(settings, (node) => node.selector === 'select' && node.getAttribute('aria-label') === 'Change sensitivity');
  assert.ok(sensitivity);
  assert.deepEqual(sensitivity.children.map((option) => option.value), ['low', 'normal', 'high']);
  const documentLimit = findBy(settings, (node) => node.selector === 'input' && node.getAttribute('aria-label') === 'Main document limit in KiB');
  assert.equal(documentLimit.value, '512');
  const retentionInputs = findAll(settings, (node) => node.selector === 'input' && node.attributes.has('aria-label')
    && node.getAttribute('aria-label') !== 'Main document limit in KiB');
  assert.deepEqual(retentionInputs.map((input) => input.value), ['17', '23', '29']);
  assert.ok(findBy(menu, (node) => node.className === 'export-button' && /history\.json/.test(node.href)));
  assert.ok(findBy(menu, (node) => node.className === 'export-button' && /history\.html/.test(node.href)));
});

test('numeric selector history is fetched and rendered as an accessible chart', async () => {
  const site = {
    id: 73, url: 'https://metrics.example/', display_name: 'Metrics', is_demo: false,
    numeric_selectors: ['.price'],
  };
  const harness = makeHarness({ sites: [site], metricsBySite: { 73: {
    '.price': { selector: '.price', points: [
      { checked_at: '2026-10-15T10:00:00Z', value: 12.5, raw_text: '$12.50' },
      { checked_at: '2026-10-15T11:00:00Z', value: 14, raw_text: '$14.00' },
    ] },
  } } });
  await harness.start();
  const row = findBy(harness.elements.get('#website-list'), (node) => node.className === 'website-row');
  const showValues = findBy(row, (node) => node.className === 'secondary-button' && node.textContent === 'Show values: .price');
  assert.ok(showValues);
  await showValues.listeners.get('click')();
  assert.ok(harness.requests.some((item) => item.url === '/api/websites/73/metrics?selector=.price'));
  const chart = findBy(row, (node) => node.className === 'numeric-chart');
  const svg = findBy(chart, (node) => node.selector === 'svg');
  const line = findBy(svg, (node) => node.selector === 'polyline');
  assert.equal(svg.getAttribute('role'), 'img');
  assert.equal(svg.getAttribute('aria-label'), 'Historical values for .price');
  assert.equal(line.getAttribute('points'), '10,90 310,10');
  assert.match(findBy(chart, (node) => node.selector === 'p').textContent, /Latest: \$14\.00/);
});

test('removing a website asks before deleting its saved history', async () => {
  const site = { id: 52, url: 'https://remove.example/', display_name: 'Keep or remove', is_demo: false };
  const cancelled = makeHarness({ sites: [site], confirmResult: false });
  await cancelled.start();
  let remove = findBy(cancelled.elements.get('#website-list'), (node) => node.className === 'remove-button');
  await remove.listeners.get('click')();
  assert.match(cancelled.confirmations[0], /permanently delete its saved snapshots and history/);
  assert.equal(cancelled.requests.some((item) => item.method === 'DELETE'), false);
  assert.equal(findAll(cancelled.elements.get('#website-list'), (node) => node.className === 'website-row').length, 1);

  const accepted = makeHarness({ sites: [site] });
  await accepted.start();
  remove = findBy(accepted.elements.get('#website-list'), (node) => node.className === 'remove-button');
  await remove.listeners.get('click')();
  assert.equal(accepted.requests.some((item) => item.method === 'DELETE' && item.url === '/api/websites/52'), true);
  assert.equal(findAll(accepted.elements.get('#website-list'), (node) => node.className === 'website-row').length, 0);
});

test('history modal loads older entries on demand without losing the current page', async () => {
  const sites = [{ id: 31, url: 'https://pages.example/', display_name: 'Pages', is_demo: false }];
  const entry = (id) => ({ id, checked_at: `2026-10-15T${id}:00:00Z`, status: id === 1 ? 'baseline' : 'changed', added_content: '', removed_content: '', details_truncated: false, volatile_hints: [] });
  const harness = makeHarness({ sites, historyPagesBySite: { 31: {
    0: { history: [entry(2)], has_more: true, next_offset: 20 },
    20: { history: [entry(1)], has_more: false, next_offset: 40 },
  } } });
  await harness.start();
  const button = findBy(harness.elements.get('#website-list'), (node) => node.className === 'history-button');
  await button.listeners.get('click')();
  const content = harness.elements.get('#history-dialog-content');
  const more = findBy(content, (node) => node.className === 'history-load-more');
  await more.listeners.get('click')();
  assert.equal(findAll(content, (node) => node.className === 'history-modal-entry').length, 2);
  assert.deepEqual(harness.historyRequests, [31, 31]);
});

test('History compares any two selected saved versions and handles missing screenshots', async () => {
  const site = { id: 91, url: 'https://compare.example/', display_name: 'Compare', is_demo: false };
  const history = [
    { id: 101, checked_at: '2026-10-15T10:00:00Z', status: 'baseline', added_content: '', removed_content: '', has_screenshot: false },
    { id: 102, checked_at: '2026-10-15T11:00:00Z', status: 'changed', added_content: 'New', removed_content: 'Old', has_screenshot: false },
  ];
  const harness = makeHarness({ sites: [site], historyBySite: { 91: history }, comparisonsBySite: { 91: {
    older: { id: 101, checked_at: history[0].checked_at, has_screenshot: false },
    newer: { id: 102, checked_at: history[1].checked_at, has_screenshot: false },
    same: false, added_content: 'New', removed_content: 'Old', details_truncated: false,
  } } });
  await harness.start();
  const historyButton = findBy(harness.elements.get('#website-list'), (node) => node.className === 'history-button');
  await historyButton.listeners.get('click')();
  const content = harness.elements.get('#history-dialog-content');
  const selectors = findAll(content, (node) => node.selector === 'input' && /Select version/.test(node.getAttribute('aria-label') || ''));
  selectors[0].checked = true; await selectors[0].listeners.get('change')();
  selectors[1].checked = true; await selectors[1].listeners.get('change')();
  const compare = findBy(content, (node) => node.className === 'history-compare-button');
  assert.equal(compare.disabled, false);
  await compare.listeners.get('click')();
  assert.equal(findAll(content, (node) => node.className === 'history-dialog-error').map((node) => node.textContent).join(' '), '');
  const diffBlocks = findAll(content, (node) => node.selector === 'pre').map((node) => node.textContent);
  assert.deepEqual(diffBlocks, ['New', 'Old']);
  assert.equal(findAll(content, (node) => node.className === 'version-screenshot-panel').length, 2);
  assert.equal(findAll(content, (node) => /No screenshot was saved/.test(node.textContent)).length, 2);
  assert.equal(findAll(content, (node) => node.selector === 'canvas').length, 0);
});

test('History comparison keeps both screenshots separate and reports image loading errors', async () => {
  const site = { id: 93, url: 'https://images.example/', display_name: 'Images', is_demo: false };
  const history = [
    { id: 201, checked_at: '2026-10-15T10:00:00Z', status: 'baseline', added_content: '', removed_content: '', has_screenshot: true },
    { id: 202, checked_at: '2026-10-15T11:00:00Z', status: 'changed', added_content: 'New', removed_content: 'Old', has_screenshot: true },
  ];
  const harness = makeHarness({ sites: [site], historyBySite: { 93: history }, comparisonsBySite: { 93: {
    older: { id: 201, checked_at: history[0].checked_at, has_screenshot: true },
    newer: { id: 202, checked_at: history[1].checked_at, has_screenshot: true },
    same: false, added_content: 'New', removed_content: 'Old', details_truncated: false,
  } } });
  await harness.start();
  const historyButton = findBy(harness.elements.get('#website-list'), (node) => node.className === 'history-button');
  await historyButton.listeners.get('click')();
  const content = harness.elements.get('#history-dialog-content');
  const selectors = findAll(content, (node) => node.selector === 'input' && /Select version/.test(node.getAttribute('aria-label') || ''));
  selectors[0].checked = true; await selectors[0].listeners.get('change')();
  selectors[1].checked = true; await selectors[1].listeners.get('change')();
  await findBy(content, (node) => node.className === 'history-compare-button').listeners.get('click')();

  const panels = findAll(content, (node) => node.className === 'version-screenshot-panel');
  assert.equal(panels.length, 2);
  assert.match(panels[0].children[0].textContent, /Earlier version/);
  assert.match(panels[1].children[0].textContent, /Later version/);
  const images = panels.map((panel) => panel.children.find((child) => child.selector === 'img'));
  assert.deepEqual(images.map((image) => image.src), [
    '/api/websites/93/snapshots/201/screenshot',
    '/api/websites/93/snapshots/202/screenshot',
  ]);
  assert.equal(findAll(content, (node) => node.selector === 'canvas').length, 0);
  assert.equal(findAll(content, (node) => /Highlighted visual differences/.test(node.textContent)).length, 0);

  images[1].listeners.get('error')();
  assert.equal(panels[1].children.some((child) => /This screenshot is unavailable/.test(child.textContent)), true);
});

test('region picker previews semantic selectors and saves watch rules', async () => {
  const site = { id: 92, url: 'https://regions.example/', display_name: 'Regions', is_demo: false };
  const harness = makeHarness({ sites: [site], previewsBySite: { 92: {
    screenshot: 'data:image/jpeg;base64,ZmFrZQ==', warnings: [], watched: [], ignored: [],
    viewport: { width: 1280, height: 720 },
    regions: [{ selector: 'main > article', label: 'Main article', text: 'Admissions are open.', x: 80, y: 120, width: 600, height: 300 }],
  } } });
  await harness.start();
  const open = findBy(harness.elements.get('#website-list'), (node) => node.className === 'region-picker-open');
  await open.listeners.get('click')();
  const dialog = harness.elements.get('#region-dialog');
  const content = harness.elements.get('#region-dialog-content');
  assert.equal(dialog.open, true);
  const action = findBy(content, (node) => node.selector === 'select' && /main > article/.test(node.getAttribute('aria-label') || ''));
  const marker = findBy(content, (node) => node.className === 'region-preview-marker');
  await marker.listeners.get('click')();
  assert.equal(action.value, 'watched');
  const save = findBy(content, (node) => node.textContent === 'Save region rules');
  await save.listeners.get('click')();
  assert.deepEqual(harness.selectorSaves, [{ watched: ['main > article'], ignored: [] }]);
});
