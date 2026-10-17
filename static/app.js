// Theme preference stays in this browser; website records live in the Flask database.
const THEME_KEY = 'webwatch.theme.v1';
// The server allows at most 120 seconds for rendering; leave room for response handling.
const CHECK_REQUEST_TIMEOUT_MS = 135000;
const form = document.querySelector('#add-form');
const urlInput = document.querySelector('#website-url');
const nameInput = document.querySelector('#website-name');
const websiteIntervalInput = document.querySelector('#website-check-interval');
const message = document.querySelector('#form-message');
const list = document.querySelector('#website-list');
const emptyState = document.querySelector('#empty-state');
const loadingState = document.querySelector('#loading-state');
const loadErrorState = document.querySelector('#load-error-state');
const filterEmptyState = document.querySelector('#filter-empty-state');
const filterTools = document.querySelector('#watchlist-tools');
const searchInput = document.querySelector('#website-search');
const statusFilter = document.querySelector('#status-filter');
const matchSummary = document.querySelector('#match-summary');
const count = document.querySelector('#item-count');
const watchStats = document.querySelector('#watch-stats');
const note = document.querySelector('#implementation-note');
const themeToggle = document.querySelector('#theme-toggle');
const hiddenViewToggle = document.querySelector('#hidden-view-toggle');
const hiddenViewLabel = hiddenViewToggle.querySelector('.hidden-nav-label');
let hiddenView = false;
let websites = [];
const guestMode = document.body.dataset.guest === 'true';
const pendingChecks = new Set();
const pendingRemovals = new Set();
const pendingVisibilityChanges = new Set();
const pendingDemoVersions = new Set();
const checkAllWebsiteIds = new Set();
let watchlistRevision = 0;
let loadState = 'loading';
let watchlistRequestId = 0;
let totalWebsiteCount = 0;
let searchTimer;
let checkAllRunning = false;
let checkAllRunId = 0;
const checkAllButton = document.querySelector('#check-all');
const checkAllProgress = document.querySelector('#check-all-progress');
const checkAllMeter = document.querySelector('#check-all-meter');
const checkAllStatus = document.querySelector('#check-all-status');
const checkAllFailures = document.querySelector('#check-all-failures');
const notificationList = document.querySelector('#notification-list');
const notificationTotalCount = document.querySelector('#notification-total-count');
const notificationUnreadCount = document.querySelector('#notification-unread-count');
const notificationViewToggle = document.querySelector('#notification-view-toggle');
const browserNotificationButton = document.querySelector('#enable-browser-notifications');
const browserNotificationStatus = document.querySelector('#browser-notification-status');
const notificationToast = document.querySelector('#notification-toast');
const historyDialog = document.querySelector('#history-dialog');
const historyDialogTitle = document.querySelector('#history-dialog-title');
const historyDialogUrl = document.querySelector('#history-dialog-url');
const historyDialogClose = document.querySelector('#history-dialog-close');
const historyDialogContent = document.querySelector('#history-dialog-content');
const regionDialog = document.querySelector('#region-dialog');
const regionDialogClose = document.querySelector('#region-dialog-close');
const regionDialogContent = document.querySelector('#region-dialog-content');
const guideDialog = document.querySelector('#guide-dialog');
const notificationHistory = [];
const seenNotificationIds = new Set();
const notificationReadIds = new Set();
const sessionNotificationIds = new Set();
const expandedNotificationIds = new Set();
const NOTIFICATION_READ_KEY = 'webwatch.notifications.read.v1';
let notificationReadStateInitialized = false;
let notificationTotal = 0;
let notificationHistoryExpanded = false;
let notificationToastTimer;
const notificationToastQueue = [];
const scheduleForm = document.querySelector('#schedule-form');
const alertForm = document.querySelector('#alert-settings-form');
const alertEmailEnabled = document.querySelector('#alert-email-enabled');
const alertEmailRecipient = document.querySelector('#alert-email-recipient');
const alertEmailSetup = document.querySelector('#alert-email-setup');
const alertWebhookEnabled = document.querySelector('#alert-webhook-enabled');
const alertWebhookSetup = document.querySelector('#alert-webhook-setup');
const alertWebhookUrl = document.querySelector('#alert-webhook-url');
const alertWebhookChatId = document.querySelector('#alert-webhook-chat-id');
const alertDailyDigestEnabled = document.querySelector('#alert-daily-digest-enabled');
const aiSummaryEnabled = document.querySelector('#ai-summary-enabled');
const aiSummarySetup = document.querySelector('#ai-summary-setup');
const alertSettingsStatus = document.querySelector('#alert-settings-status');
const jobQueueStatus = document.querySelector('#job-queue-status');
const jobQueueList = document.querySelector('#job-queue-list');
const jobQueueToggle = document.querySelector('#job-queue-toggle');
let jobQueueExpanded = false;
const scheduleEnabledInput = document.querySelector('#schedule-enabled');
const scheduleIntervalInput = document.querySelector('#schedule-interval');
const scheduleSaveButton = document.querySelector('#schedule-save');
const scheduleState = document.querySelector('#schedule-state');
const scheduleStatus = document.querySelector('#schedule-status');
const scheduleRunStatus = document.querySelector('#schedule-run-status');
const scheduleRunLast = document.querySelector('#schedule-run-last');
const scheduleRunNext = document.querySelector('#schedule-run-next');
const scheduleRunStats = document.querySelector('#schedule-run-stats');
let scheduleFormDirty = false;
let websiteIntervalTouched = false;
let observedScheduleRunAt = null;
let hasObservedScheduleRun = false;
let historyDialogReturnFocus = null;
let historyDialogRequestId = 0;
let historyDataRequestId = 0;

function applyWebsiteView() {
  const viewTitle = hiddenView ? 'Hidden Websites' : 'Websites';
  hiddenViewToggle.setAttribute('aria-pressed', String(hiddenView));
  hiddenViewToggle.setAttribute('aria-label', hiddenView ? 'Back to Dashboard' : 'Open Hidden Websites');
  hiddenViewToggle.title = hiddenView ? 'Back to Dashboard' : 'Open Hidden Websites';
  hiddenViewLabel.textContent = hiddenView ? 'Back to Dashboard' : 'Hidden Websites';
  document.querySelector('#view-eyebrow').textContent = hiddenView ? 'HIDDEN WEBSITES' : 'YOUR WATCHLIST';
  document.querySelector('#page-title').textContent = viewTitle;
  document.querySelector('#page-intro').textContent = hiddenView
    ? 'Hidden pages stay monitored and keep their check history.'
    : 'Track pages and see when their content changes.';
  document.querySelector('#watchlist-heading').textContent = hiddenView ? 'Hidden websites' : 'Watchlist';
  document.querySelector('#empty-state-heading').textContent = hiddenView ? 'No hidden websites' : 'No websites yet';
  document.querySelector('#empty-state-copy').textContent = hiddenView
    ? 'Websites you hide from the main dashboard will appear here.'
    : 'Your watchlist is ready. Add a page above to get started.';
  document.querySelector('#add-panel').hidden = hiddenView;
  document.querySelector('#focus-url').hidden = hiddenView;
  document.querySelector('#check-all').hidden = hiddenView;
  note.textContent = hiddenView
    ? 'These websites remain monitored and can be checked or restored at any time.'
    : 'Websites are saved by this app. Checks run manually or on the automatic schedule.';
  document.title = `${viewTitle} — WebWatch`;
}

function setTheme(isDark, savePreference = false) {
  const theme = isDark ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  themeToggle.setAttribute('aria-checked', String(isDark));
  themeToggle.setAttribute('aria-label', isDark ? 'Dark mode, on' : 'Dark mode, off');
  document.querySelector('meta[name="theme-color"]').content = isDark ? '#171a21' : '#f5f6f4';
  if (savePreference) {
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* Theme still applies for this page view. */ }
  }
}

let savedTheme = null;
try { savedTheme = localStorage.getItem(THEME_KEY); } catch { /* Use the system preference. */ }
setTheme(savedTheme ? savedTheme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches);
themeToggle.addEventListener('click', () => {
  setTheme(themeToggle.getAttribute('aria-checked') !== 'true', true);
});

async function api(path, options = {}) {
  const { timeoutMs = 15000, ...fetchOptions } = options;
  const method = (fetchOptions.method || 'GET').toUpperCase();
  const headers = { ...(fetchOptions.body ? { 'Content-Type': 'application/json' } : {}), ...fetchOptions.headers };
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    headers['X-WebWatch-Request'] = '1';
    let tokenResponse;
    try {
      tokenResponse = await fetch('/auth/csrf', { cache: 'no-store', credentials: 'same-origin' });
    } catch {
      throw new Error('Could not reach WebWatch while requesting a fresh security token. Check your connection and try again.');
    }
    const tokenResult = await tokenResponse.json().catch(() => ({}));
    if (!tokenResponse.ok) {
      const reason = tokenResult.error || `HTTP ${tokenResponse.status}`;
      throw new Error(`A fresh security token could not be created: ${reason}. Reload the page and try again.`);
    }
    if (typeof tokenResult.csrf_token !== 'string' || !tokenResult.csrf_token) {
      throw new Error(`A fresh security token could not be created (HTTP ${tokenResponse.status}; response contained no token). Reload the page and try again.`);
    }
    headers['X-CSRF-Token'] = tokenResult.csrf_token;
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  let result;
  try {
    response = await fetch(path, {
      ...fetchOptions,
      cache: 'no-store',
      headers,
      signal: controller.signal,
    });
    const responseText = await response.text();
    try {
      result = responseText ? JSON.parse(responseText) : {};
    } catch {
      result = null;
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(`WebWatch did not respond within ${Math.ceil(timeoutMs / 1000)} seconds. The check may still be running; refresh the page to see its status.`);
    }
    throw new Error('Could not reach WebWatch. Check that the Flask server is running and try again.');
  } finally {
    window.clearTimeout(timeout);
  }
  if (result === null && response.ok) {
    throw new Error('WebWatch returned an unreadable response. Restart the current Flask app and try again.');
  }
  if (result === null) result = {};
  if (Array.isArray(result) && response.headers.has('X-Total-Count')) {
    result.totalCount = Number(response.headers.get('X-Total-Count'));
  }
  if (!response.ok) {
    const fallback = `WebWatch returned HTTP ${response.status} without a JSON error. Restart the current Flask app and try again.`;
    const error = new Error(result.error || fallback);
    error.status = response.status;
    error.data = result;
    throw error;
  }
  return result;
}

function renderSchedule(settings, forceControls = false) {
  if (forceControls || !scheduleFormDirty) {
    scheduleEnabledInput.checked = settings.enabled;
    scheduleIntervalInput.value = String(settings.interval_minutes);
  }
  if (!websiteIntervalTouched) {
    websiteIntervalInput.value = String(Math.max(15, Math.min(1440, settings.interval_minutes)));
  }
  scheduleState.textContent = settings.enabled ? 'Enabled' : 'Disabled';
  scheduleState.dataset.enabled = String(settings.enabled);
  scheduleState.dataset.runStatus = settings.run_status || 'never';
  const runStatusLabels = {
    running: 'Run in progress',
    completed: 'Last run completed',
    partial_failure: 'Last run completed with failures',
    failed: 'Last run failed',
    never: 'No automatic run yet',
  };
  scheduleRunStatus.textContent = runStatusLabels[settings.run_status] || runStatusLabels.never;
  scheduleRunStatus.dataset.runStatus = settings.run_status || 'never';
  scheduleRunLast.textContent = settings.last_run_at ? displayTime(settings.last_run_at) : 'Not yet';
  scheduleRunNext.textContent = settings.enabled && settings.next_run_at
    ? displayTime(settings.next_run_at)
    : (settings.enabled ? 'Pending' : 'Disabled');
  scheduleRunStats.replaceChildren();
  const summary = settings.run_status === 'running' ? null : settings.run_summary;
  scheduleRunStats.hidden = !summary;
  if (summary) {
    const values = [
      ['Attempted', summary.total],
      ['Successful', summary.successful],
      ['Failed', summary.failed],
      ['Changed', summary.changed],
    ];
    for (const [label, value] of values) {
      const item = document.createElement('div');
      item.className = 'schedule-run-stat';
      const itemLabel = document.createElement('span');
      itemLabel.textContent = label;
      const itemValue = document.createElement('strong');
      itemValue.textContent = String(value);
      item.append(itemLabel, itemValue);
      scheduleRunStats.append(item);
    }
  }
  scheduleStatus.textContent = '';
}

async function loadSchedule(forceControls = false) {
  try {
    const settings = await api('/api/schedule');
    const firstScheduleObservation = !hasObservedScheduleRun;
    const completedRunChanged = !firstScheduleObservation
      ? Boolean(settings.last_run_at && settings.last_run_at !== observedScheduleRunAt)
      : Boolean(settings.last_run_at);
    hasObservedScheduleRun = true;
    observedScheduleRunAt = settings.last_run_at || null;
    renderSchedule(settings, forceControls);
    if (completedRunChanged) {
      await Promise.all([
        loadWebsites(),
        loadStats(),
        loadNotificationHistory({ notifyNew: !firstScheduleObservation }),
      ]);
    }
  } catch (error) {
    scheduleState.textContent = 'Unavailable';
    scheduleState.dataset.enabled = 'false';
    scheduleState.dataset.runStatus = 'failed';
    scheduleRunStatus.textContent = 'Status unavailable';
    scheduleRunLast.textContent = 'Unavailable';
    scheduleRunNext.textContent = 'Unavailable';
    scheduleRunStats.replaceChildren();
    scheduleRunStats.hidden = true;
    scheduleStatus.textContent = 'Could not load automatic monitoring settings: ' + error.message;
  }
}

async function loadAlertSettings() {
  try {
    const settings = await api('/api/alerts');
    alertEmailEnabled.checked = settings.email_enabled;
    alertEmailRecipient.value = settings.email_recipient;
    alertEmailSetup.textContent = !settings.email_recipient
      ? 'Add a recipient email. Delivery also requires SMTP settings on the WebWatch server.'
      : !settings.email_configured
        ? 'Recipient saved; SMTP is not configured on the WebWatch server yet.'
        : settings.email_enabled ? 'Email is enabled and SMTP is configured.' : 'SMTP is configured. Turn on email alerts to send notifications.';
    alertWebhookEnabled.checked = settings.webhook_enabled;
    alertWebhookSetup.textContent = settings.webhook_configured
      ? (settings.webhook_enabled ? 'Webhook is enabled; the saved HTTPS destination is hidden.' : 'HTTPS destination saved. Turn on webhook alerts to use it.')
      : 'Add an HTTPS destination to configure a webhook. The URL is checked and then kept hidden.';
    alertWebhookChatId.value = settings.webhook_chat_id;
    alertDailyDigestEnabled.checked = settings.daily_digest_enabled;
    aiSummaryEnabled.checked = settings.ai_summary_enabled;
    aiSummarySetup.textContent = settings.ai_summary_configured
      ? 'Server API key found; summaries are optional and page excerpts are sent only when enabled.'
      : 'Set WEBWATCH_SUMMARY_API_KEY (and optional URL/model) in the server environment to enable AI summaries.';
    alertWebhookUrl.placeholder = settings.webhook_configured ? 'A webhook is saved; enter a new URL to replace it' : 'https://…';
  } catch (error) {
    alertSettingsStatus.textContent = 'Could not load alert settings: ' + error.message;
  }
}

async function refreshJobQueue() {
  try {
    const jobs = await api('/api/jobs');
    const active = jobs.filter((job) => ['queued', 'running', 'retry'].includes(job.status));
    const running = active.filter((job) => job.status === 'running').length;
    const retry = active.filter((job) => job.status === 'retry').length;
    const queued = active.filter((job) => job.status === 'queued').length;
    const recentJobs = [...jobs].sort((left, right) => {
      const idOrder = Number(right.id) - Number(left.id);
      if (Number.isFinite(idOrder) && idOrder !== 0) return idOrder;
      return Date.parse(right.updated_at || right.created_at || 0) - Date.parse(left.updated_at || left.created_at || 0);
    });
    if (recentJobs.length <= 2) jobQueueExpanded = false;
    const visibleCount = Math.min(2, recentJobs.length);
    jobQueueStatus.textContent = `${active.length} active · ${running} running · ${queued} queued · ${retry} waiting to retry. Showing the latest ${visibleCount} of ${recentJobs.length} recent jobs.`;
    jobQueueStatus.dataset.status = active.length ? 'active' : 'idle';
    jobQueueList.replaceChildren();
    jobQueueToggle.hidden = recentJobs.length <= 2;
    jobQueueToggle.textContent = jobQueueExpanded ? 'Show less' : 'View more';
    jobQueueToggle.setAttribute('aria-expanded', String(jobQueueExpanded));
    const labels = { queued: 'Queued', running: 'Running', retry: 'Waiting to retry', succeeded: 'Completed', failed: 'Failed' };
    for (const [index, job] of recentJobs.entries()) {
      const item = document.createElement('li');
      item.className = 'job-queue-entry';
      item.dataset.status = job.status;
      item.dataset.jobId = String(job.id);
      item.hidden = !jobQueueExpanded && index >= 2;
      const heading = document.createElement('div');
      heading.className = 'job-queue-entry-heading';
      const name = document.createElement('strong');
      name.textContent = job.display_name || `Website ${job.website_id}`;
      const status = document.createElement('span');
      status.className = 'job-queue-entry-status';
      status.textContent = labels[job.status] || 'Status unavailable';
      heading.append(name, status);
      const meta = document.createElement('p');
      meta.className = 'job-queue-entry-meta';
      const updated = job.updated_at ? `Updated ${displayTime(job.updated_at)}` : `Queued ${displayTime(job.created_at)}`;
      const retryAt = job.status === 'retry' && job.next_run_at ? ` · Retry after ${displayTime(job.next_run_at)}` : '';
      meta.textContent = `${job.source || 'check'} · attempt ${job.attempts || 0} · ${updated}${retryAt}`;
      item.append(heading, meta);
      if (job.last_error) {
        const error = document.createElement('p');
        error.className = 'job-queue-entry-error';
        error.textContent = job.last_error;
        item.append(error);
      }
      jobQueueList.append(item);
    }
    if (!jobs.length) {
      const empty = document.createElement('li');
      empty.className = 'job-queue-empty';
      empty.textContent = 'No check jobs yet. Run Check now or Check all to start.';
      jobQueueList.append(empty);
    }
  } catch (error) {
    jobQueueStatus.textContent = 'Could not load check jobs: ' + error.message;
    jobQueueList.replaceChildren();
  }
}

jobQueueToggle.addEventListener('click', () => {
  jobQueueExpanded = !jobQueueExpanded;
  jobQueueToggle.textContent = jobQueueExpanded ? 'Show less' : 'View more';
  jobQueueToggle.setAttribute('aria-expanded', String(jobQueueExpanded));
  for (const [index, item] of [...jobQueueList.children].entries()) {
    if (item.className === 'job-queue-entry') item.hidden = !jobQueueExpanded && index >= 2;
  }
});

function alertSettingsErrorMessage(error) {
  const message = String(error?.message || 'Unknown error.');
  const secretUrl = alertWebhookUrl.value.trim();
  return secretUrl ? message.replaceAll(secretUrl, 'the supplied webhook URL') : message;
}

alertForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const save = document.querySelector('#alert-save');
  save.disabled = true;
  delete alertSettingsStatus.dataset.kind;
  alertSettingsStatus.textContent = 'Saving alert settings…';
  try {
    const settings = await api('/api/alerts', { method: 'PUT', body: JSON.stringify({
      email_enabled: alertEmailEnabled.checked,
      email_recipient: alertEmailRecipient.value.trim(),
      webhook_enabled: alertWebhookEnabled.checked,
      webhook_url: alertWebhookUrl.value.trim(),
      webhook_chat_id: alertWebhookChatId.value.trim(),
      daily_digest_enabled: alertDailyDigestEnabled.checked,
      ai_summary_enabled: aiSummaryEnabled.checked,
    }) });
    alertWebhookUrl.value = '';
    alertWebhookUrl.placeholder = settings.webhook_configured ? 'A webhook is saved; enter a new URL to replace it' : 'https://…';
    alertEmailSetup.textContent = !settings.email_recipient
      ? 'Add a recipient email. Delivery also requires SMTP settings on the WebWatch server.'
      : !settings.email_configured
        ? 'Recipient saved; SMTP is not configured on the WebWatch server yet.'
        : settings.email_enabled ? 'Email is enabled and SMTP is configured.' : 'SMTP is configured. Turn on email alerts to send notifications.';
    alertWebhookSetup.textContent = settings.webhook_configured
      ? (settings.webhook_enabled ? 'Webhook is enabled; the saved HTTPS destination is hidden.' : 'HTTPS destination saved. Turn on webhook alerts to use it.')
      : 'Add an HTTPS destination to configure a webhook. The URL is checked and then kept hidden.';
    alertSettingsStatus.textContent = 'Alert settings saved. Webhook URL is kept in the local database and is not returned to the browser.';
    alertSettingsStatus.dataset.kind = 'success';
  } catch (error) {
    alertSettingsStatus.textContent = 'Could not save alert settings: ' + alertSettingsErrorMessage(error);
    alertSettingsStatus.dataset.kind = 'error';
  } finally { save.disabled = false; }
});

document.querySelector('#alert-test').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  delete alertSettingsStatus.dataset.kind;
  alertSettingsStatus.textContent = 'Sending a test alert…';
  try {
    const result = await api('/api/alerts/test', { method: 'POST' });
    alertSettingsStatus.textContent = result.ok ? 'Test alert delivered.' : 'Test delivery failed; see the delivery queue for details.';
    alertSettingsStatus.dataset.kind = result.ok ? 'success' : 'error';
  } catch (error) {
    alertSettingsStatus.textContent = 'Test alert failed: ' + alertSettingsErrorMessage(error);
    alertSettingsStatus.dataset.kind = 'error';
  } finally { button.disabled = false; refreshJobQueue(); }
});

scheduleEnabledInput.addEventListener('change', () => { scheduleFormDirty = true; });
scheduleIntervalInput.addEventListener('input', () => { scheduleFormDirty = true; });

scheduleForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  scheduleSaveButton.disabled = true;
  scheduleStatus.textContent = 'Saving schedule…';
  try {
    const settings = await api('/api/schedule', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: scheduleEnabledInput.checked,
        interval_minutes: scheduleIntervalInput.valueAsNumber,
      }),
    });
    scheduleFormDirty = false;
    hasObservedScheduleRun = true;
    observedScheduleRunAt = settings.last_run_at || observedScheduleRunAt;
    renderSchedule(settings, true);
    scheduleStatus.textContent = settings.enabled
      ? 'Automatic checks enabled. Next run: ' + displayTime(settings.next_run_at) + '.'
      : 'Automatic checks disabled.';
    await loadWebsites();
  } catch (error) {
    scheduleStatus.textContent = 'Could not save schedule: ' + error.message;
  } finally {
    scheduleSaveButton.disabled = false;
  }
});

function displayTime(value) {
  if (!value) return 'Not checked';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown time' : date.toLocaleString();
}

function nextCheckDescription(site) {
  if (site.is_demo) return 'Demo checks are manual only';
  if (!site.automatic_checks_enabled) return 'Automatic checks disabled';
  if (!site.last_checked_at) return 'Due on next scheduler poll';
  if (!site.next_check_at) return 'Schedule unavailable';
  const nextCheck = new Date(site.next_check_at);
  if (Number.isNaN(nextCheck.getTime())) return 'Schedule unavailable';
  return nextCheck.getTime() <= Date.now() ? 'Due now' : displayTime(site.next_check_at);
}

function notificationTitle(event) {
  return event.event_type === 'recovered'
    ? `${event.website_name} recovered`
    : `${event.website_name} may be unreachable`;
}

function persistNotificationReadIds() {
  try {
    localStorage.setItem(NOTIFICATION_READ_KEY, JSON.stringify([...notificationReadIds]));
  } catch {
    // The panel still behaves for this page view if browser storage is unavailable.
  }
}

function initializeNotificationReadState(entries, newlyArrived = []) {
  if (notificationReadStateInitialized) return;
  let stored = null;
  try {
    stored = localStorage.getItem(NOTIFICATION_READ_KEY);
  } catch {
    // Treat unavailable storage like a first visit and keep the state in memory.
  }

  if (stored !== null) {
    try {
      const ids = JSON.parse(stored);
      if (Array.isArray(ids)) {
        ids.forEach((id) => {
          if (Number.isSafeInteger(id) && id > 0) notificationReadIds.add(id);
        });
      }
    } catch {
      // A malformed local value is replaced with a safe first-visit baseline.
      stored = null;
    }
  }

  if (stored === null) {
    const newIds = new Set(newlyArrived.map((entry) => entry.id));
    entries.forEach((entry) => {
      if (!newIds.has(entry.id) && !sessionNotificationIds.has(entry.id)) {
        notificationReadIds.add(entry.id);
      }
    });
  }
  notificationReadStateInitialized = true;
  persistNotificationReadIds();
}

function renderNotificationCounts() {
  const unread = notificationHistory.reduce(
    (countUnread, event) => countUnread + Number(!notificationReadIds.has(event.id)),
    0,
  );
  notificationTotalCount.textContent = `${notificationTotal} total`;
  notificationUnreadCount.hidden = unread === 0;
  notificationUnreadCount.textContent = unread === 1 ? '1 new' : `${unread} new`;
}

function markNotificationRead(id) {
  if (notificationReadIds.has(id)) return;
  notificationReadIds.add(id);
  persistNotificationReadIds();
  renderNotificationCounts();
}

function compactNotificationTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function renderNotificationHistory() {
  notificationList.replaceChildren();
  renderNotificationCounts();
  notificationViewToggle.hidden = notificationTotal <= 3;
  notificationViewToggle.setAttribute('aria-expanded', String(notificationHistoryExpanded));
  notificationViewToggle.textContent = notificationHistoryExpanded
    ? 'Show less'
    : `View all ${notificationTotal} events`;
  notificationViewToggle.setAttribute('aria-label', notificationHistoryExpanded
    ? 'Show the 3 newest serious events'
    : `View all ${notificationTotal} serious events`);
  if (!notificationHistory.length) {
    const empty = document.createElement('li');
    empty.className = 'notification-empty';
    empty.textContent = 'No serious events recorded.';
    notificationList.append(empty);
    return;
  }
  const visibleEvents = notificationHistoryExpanded ? notificationHistory : notificationHistory.slice(0, 3);
  for (const event of visibleEvents) {
    const item = document.createElement('li');
    item.className = 'notification-entry';
    item.dataset.event = event.event_type;
    const toggle = document.createElement('button');
    toggle.className = 'notification-entry-toggle';
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', String(expandedNotificationIds.has(event.id)));
    const detailsId = `notification-details-${event.id}`;
    toggle.setAttribute('aria-controls', detailsId);
    const icon = document.createElement('span');
    icon.className = 'notification-status-icon';
    icon.setAttribute('aria-hidden', 'true');
    const title = document.createElement('span');
    title.className = 'notification-entry-title';
    title.textContent = notificationTitle(event);
    const time = document.createElement('time');
    time.dateTime = event.occurred_at;
    time.className = 'notification-entry-time';
    time.title = displayTime(event.occurred_at);
    time.textContent = compactNotificationTime(event.occurred_at);
    const badge = document.createElement('span');
    badge.className = 'notification-new-badge';
    badge.textContent = 'New';
    badge.hidden = notificationReadIds.has(event.id);
    const chevron = document.createElement('span');
    chevron.className = 'notification-chevron';
    chevron.setAttribute('aria-hidden', 'true');
    toggle.append(icon, title, time, badge, chevron);

    const details = document.createElement('div');
    details.className = 'notification-details';
    details.id = detailsId;
    details.hidden = !expandedNotificationIds.has(event.id);
    const category = document.createElement('span');
    category.className = 'notification-entry-category';
    category.textContent = event.category;
    const explanation = document.createElement('p');
    explanation.className = 'notification-entry-explanation';
    explanation.textContent = event.explanation;
    const confidence = document.createElement('p');
    confidence.className = 'notification-entry-confidence';
    confidence.textContent = event.confidence === 'suspected' ? 'Suspected availability issue' : 'Observed recovery';
    details.append(category, explanation, confidence);
    toggle.addEventListener('click', () => {
      const opening = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(opening));
      details.hidden = !opening;
      if (opening) {
        expandedNotificationIds.add(event.id);
        markNotificationRead(event.id);
        badge.hidden = true;
      } else {
        expandedNotificationIds.delete(event.id);
      }
    });
    item.append(toggle, details);
    notificationList.append(item);
  }
}

async function loadNotificationHistory({ notifyNew = false } = {}) {
  try {
    const entries = await api('/api/notifications?limit=100');
    const unseenEntries = notifyNew
      ? entries.filter((entry) => !seenNotificationIds.has(entry.id))
      : [];
    initializeNotificationReadState(entries, unseenEntries);
    notificationTotal = entries.totalCount ?? entries.length;
    const mergedById = new Map(entries.map((entry) => [entry.id, entry]));
    notificationHistory.forEach((entry) => {
      if (!mergedById.has(entry.id)) mergedById.set(entry.id, entry);
    });
    const latestEntries = [...mergedById.values()].sort((left, right) => right.id - left.id).slice(0, 100);
    notificationHistory.splice(0, notificationHistory.length, ...latestEntries);
    entries.forEach((entry) => seenNotificationIds.add(entry.id));
    renderNotificationHistory();
    // Scheduled checks report events through persistent history rather than a direct check response.
    // Only newly observed entries after a completed run should raise notifications; page loads must
    // not replay older incidents.
    unseenEntries.reverse().forEach((entry) => showNotificationToast(entry));
  } catch (error) {
    notificationList.replaceChildren();
    const failure = document.createElement('li');
    failure.className = 'notification-load-error';
    failure.textContent = `Could not load notification history: ${error.message}`;
    notificationList.append(failure);
  }
}

notificationViewToggle.addEventListener('click', () => {
  notificationHistoryExpanded = !notificationHistoryExpanded;
  renderNotificationHistory();
});

function updateBrowserNotificationControl() {
  if (!('Notification' in window)) {
    browserNotificationButton.disabled = true;
    browserNotificationButton.textContent = 'Browser notifications unavailable';
    browserNotificationStatus.textContent = 'This browser does not support desktop notifications.';
    return;
  }
  if (window.isSecureContext === false) {
    browserNotificationButton.disabled = true;
    browserNotificationButton.textContent = 'Secure connection required';
    browserNotificationStatus.textContent = 'Browser notifications require HTTPS or a localhost address.';
    return;
  }
  if (Notification.permission === 'granted') {
    browserNotificationButton.disabled = true;
    browserNotificationButton.textContent = 'Browser notifications enabled';
    browserNotificationStatus.textContent = 'Serious events may also appear as browser notifications.';
  } else if (Notification.permission === 'denied') {
    browserNotificationButton.disabled = true;
    browserNotificationButton.textContent = 'Notifications blocked in browser settings';
    browserNotificationStatus.textContent = 'Change this site’s notification permission in browser settings to enable them.';
  } else {
    browserNotificationButton.disabled = false;
    browserNotificationButton.textContent = 'Enable browser notifications';
    browserNotificationStatus.textContent = 'Browser notifications are optional and currently off.';
  }
}

async function requestBrowserNotifications() {
  if (!('Notification' in window)) {
    updateBrowserNotificationControl();
    return;
  }
  if (Notification.permission === 'granted') {
    updateBrowserNotificationControl();
    return;
  }
  try {
    await Notification.requestPermission();
    updateBrowserNotificationControl();
  } catch {
    browserNotificationStatus.textContent = 'The browser could not update notification permission.';
  }
}

function showNotificationToast(event) {
  notificationToastQueue.push(event);
  displayNextNotificationToast();
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    new Notification(notificationTitle(event), {
      body: `${event.category} · ${event.explanation}`,
      tag: `webwatch-event-${event.id}`,
      renotify: false,
    });
  } catch {
    browserNotificationStatus.textContent = 'The browser could not display a desktop notification.';
  }
}

function displayNextNotificationToast() {
  if (!notificationToast.hidden || !notificationToastQueue.length) return;
  const event = notificationToastQueue.shift();
  notificationToast.dataset.event = event.event_type;
  document.querySelector('#notification-toast-title').textContent = notificationTitle(event);
  document.querySelector('#notification-toast-meta').textContent = `${event.category} · ${displayTime(event.occurred_at)} · ${event.confidence === 'suspected' ? 'Suspected' : 'Observed'}`;
  document.querySelector('#notification-toast-explanation').textContent = event.explanation;
  notificationToast.hidden = false;
  notificationToastTimer = window.setTimeout(() => {
    notificationToast.hidden = true;
    displayNextNotificationToast();
  }, 12000);
}

function recordSeriousNotification(event) {
  if (!event || seenNotificationIds.has(event.id)) return false;
  sessionNotificationIds.add(event.id);
  seenNotificationIds.add(event.id);
  if (!notificationHistory.some((entry) => entry.id === event.id)) notificationTotal += 1;
  notificationHistory.unshift(event);
  notificationHistory.sort((left, right) => right.id - left.id);
  notificationHistory.splice(100);
  renderNotificationHistory();
  showNotificationToast(event);
  return true;
}

browserNotificationButton.addEventListener('click', requestBrowserNotifications);
document.querySelector('#notification-toast-close').addEventListener('click', () => {
  window.clearTimeout(notificationToastTimer);
  notificationToast.hidden = true;
  displayNextNotificationToast();
});
updateBrowserNotificationControl();

function statusLabel(site) {
  if (['queued', 'running'].includes(site.active_check_job?.status)) return 'Check in progress';
  if (site.active_check_job?.status === 'retry') return 'Retry pending';
  if (site.last_check_status === 'baseline') return 'Baseline saved';
  if (site.last_check_status === 'changed') return 'Content changed';
  if (site.last_check_status === 'unchanged') return 'No change';
  if (site.last_check_status === 'error') {
    return ({ network_error: 'Network error', timeout: 'Timed out', http_error: 'HTTP error',
      selector_error: 'Selector needs attention', render_error: 'Render failed' }[site.last_check_category]) || 'Check failed';
  }
  if (site.last_check_status === 'rate_limited') return 'Rate limited';
  if (site.last_check_status === 'captcha') return 'CAPTCHA / challenge';
  if (site.last_check_status === 'blocked') return 'Blocked';
  return 'Not checked yet';
}

function historyStatusLabel(status) {
  if (status === 'baseline') return 'Baseline';
  if (status === 'changed') return 'Content changed';
  return 'No change';
}

function addChangeDetail(parent, label, text) {
  if (!text) return;
  const detail = document.createElement('div');
  detail.className = 'change-detail';
  const heading = document.createElement('strong');
  heading.textContent = `${label}: `;
  const content = document.createElement('pre');
  content.textContent = text;
  detail.append(heading, content);
  parent.append(detail);
}

function historyHasDetails(entry) {
  return Boolean(entry.added_content || entry.removed_content || entry.details_truncated
    || entry.volatile_hints?.length || entry.status === 'baseline');
}

function shortHistorySummary(entry) {
  if (entry.status === 'baseline') return 'First successful snapshot; no earlier check to compare.';
  if (entry.status === 'unchanged') return 'Content matched the previous successful check.';
  const snippets = [];
  if (entry.added_content) snippets.push(`Added: ${entry.added_content}`);
  if (entry.removed_content) snippets.push(`Removed: ${entry.removed_content}`);
  const summary = snippets.join(' · ');
  return summary.length > 150 ? `${summary.slice(0, 147)}…` : (summary || 'Content changed.');
}

function renderHistoryDetails(entry) {
  historyDialogContent.replaceChildren();
  const back = document.createElement('button');
  back.className = 'history-back-button';
  back.type = 'button';
  back.textContent = '← Back to history';
  back.addEventListener('click', () => renderHistoryList(historyDialogContent, historyDialogContent.historyEntries, historyDialogContent.historyPageState));
  const heading = document.createElement('h3');
  heading.className = 'history-detail-heading';
  heading.textContent = `${historyStatusLabel(entry.status)} · ${displayTime(entry.checked_at)}`;
  const details = document.createElement('div');
  details.className = 'history-detail-content';
  if (entry.change_summary) {
    const summary = document.createElement('p');
    summary.className = 'change-context-note';
    summary.textContent = `Optional summary: ${entry.change_summary}`;
    details.append(summary);
  }
  addChangeDetail(details, 'Added', entry.added_content);
  addChangeDetail(details, 'Removed', entry.removed_content);
  if (entry.volatile_hints?.length) {
    const note = document.createElement('p');
    note.className = 'change-context-note';
    const labels = entry.volatile_hints.map((hint) => ({
      relative_time: 'relative time',
      score_like: 'score-like value',
    }[hint] || 'dynamic value'));
    note.textContent = `This comparison includes text shaped like a ${labels.join(' and ')}. It is retained in the recorded comparison.`;
    details.append(note);
  }
  if (entry.details_truncated) {
    const note = document.createElement('p');
    note.className = 'change-context-note';
    note.textContent = 'The displayed change excerpt is limited to 6,000 characters. Full snapshots remain saved.';
    details.append(note);
  }
  if (entry.status === 'baseline') {
    const baseline = document.createElement('p');
    baseline.className = 'change-context-note';
    baseline.textContent = 'This was the first successful snapshot; no earlier page was available for comparison.';
    details.append(baseline);
  }
  historyDialogContent.append(back, heading, details);
  back.focus();
}

function renderHistoryList(container, entries, pageState = {}) {
  container.replaceChildren();
  container.historyEntries = entries;
  container.historyPageState = pageState;
  if (pageState.site) container.historySite = pageState.site;
  const site = pageState.site || container.historySite;
  container.selectedVersionIds ||= new Set();
  let compareButton = null;
  if (site) {
    const toolbar = document.createElement('div');
    toolbar.className = 'history-filter-toolbar';
    const filterLabel = document.createElement('label');
    filterLabel.textContent = 'Show';
    const filter = document.createElement('select');
    filter.setAttribute('aria-label', 'Filter website history');
    for (const [value, label] of [['all', 'All successful checks'], ['changes', 'Content changes']]) {
      const option = document.createElement('option'); option.value = value; option.textContent = label;
      option.selected = (pageState.filter || 'all') === value;
      filter.append(option);
    }
    filterLabel.append(filter); toolbar.append(filterLabel);
    compareButton = document.createElement('button');
    compareButton.className = 'history-compare-button';
    compareButton.type = 'button';
    compareButton.textContent = `Compare selected (${container.selectedVersionIds.size}/2)`;
    compareButton.disabled = container.selectedVersionIds.size !== 2;
    compareButton.addEventListener('click', () => {
      return compareSelectedVersions(site, [...container.selectedVersionIds], entries, pageState);
    });
    toolbar.append(compareButton);
    container.append(toolbar);
    filter.addEventListener('change', async () => {
      container.selectedVersionIds.clear();
      await loadHistoryPage(site, filter.value, 0, []);
    });
  }
  if (!entries.length) {
    const empty = document.createElement('p');
    empty.className = 'history-dialog-state';
    empty.textContent = 'No successful checks have been recorded yet.';
    container.append(empty);
    if (pageState.hasMore && site) {
      const more = document.createElement('button');
      more.className = 'history-load-more'; more.type = 'button'; more.textContent = 'Load older checks';
      more.addEventListener('click', async () => {
        more.disabled = true; more.textContent = 'Loading…';
        await loadHistoryPage(site, pageState.filter || 'all', pageState.nextOffset || 0, entries);
      });
      container.append(more);
    }
    return;
  }
  const list = document.createElement('ol');
  list.className = 'history-modal-list';
  for (const entry of entries) {
    const item = document.createElement('li');
    item.className = 'history-modal-entry';
    item.dataset.status = entry.status;
    const header = document.createElement('div');
    header.className = 'history-modal-entry-header';
    const badge = document.createElement('span');
    badge.className = 'history-status-badge';
    badge.dataset.status = entry.status;
    badge.textContent = historyStatusLabel(entry.status);
    const time = document.createElement('time');
    time.dateTime = entry.checked_at;
    time.textContent = displayTime(entry.checked_at);
    header.append(badge, time);
    const selectVersionLabel = document.createElement('label');
    selectVersionLabel.className = 'history-version-select';
    const selectVersion = document.createElement('input');
    selectVersion.type = 'checkbox';
    selectVersion.checked = container.selectedVersionIds.has(entry.id);
    selectVersion.setAttribute('aria-label', `Select version from ${displayTime(entry.checked_at)} for comparison`);
    selectVersion.addEventListener('change', () => {
      if (selectVersion.checked) {
        if (container.selectedVersionIds.size >= 2) {
          selectVersion.checked = false;
          return;
        }
        container.selectedVersionIds.add(entry.id);
      } else container.selectedVersionIds.delete(entry.id);
      compareButton.disabled = container.selectedVersionIds.size !== 2;
      compareButton.textContent = `Compare selected (${container.selectedVersionIds.size}/2)`;
    });
    selectVersionLabel.append(selectVersion, document.createTextNode('Compare'));
    header.append(selectVersionLabel);
    const summary = document.createElement('p');
    summary.className = 'history-modal-summary';
    summary.textContent = shortHistorySummary(entry);
    item.append(header, summary);
    if (historyHasDetails(entry)) {
      const detailsButton = document.createElement('button');
      detailsButton.className = 'history-details-button';
      detailsButton.type = 'button';
      detailsButton.textContent = 'Details';
      detailsButton.setAttribute('aria-label', `View details for ${historyStatusLabel(entry.status)} on ${displayTime(entry.checked_at)}`);
      detailsButton.addEventListener('click', () => renderHistoryDetails(entry));
      item.append(detailsButton);
    }
    list.append(item);
  }
  container.append(list);
  if (pageState.hasMore && site) {
    const more = document.createElement('button');
    more.className = 'history-load-more'; more.type = 'button'; more.textContent = 'Load older checks';
    more.addEventListener('click', async () => {
      more.disabled = true; more.textContent = 'Loading…';
      await loadHistoryPage(site, pageState.filter || 'all', pageState.nextOffset || 0, entries);
    });
    container.append(more);
  }
}

async function compareSelectedVersions(site, selectedIds, entries, pageState) {
  if (selectedIds.length !== 2) return;
  const ordered = selectedIds.slice().sort((left, right) => left - right);
  historyDialogContent.replaceChildren();
  const loading = document.createElement('p');
  loading.className = 'history-dialog-state';
  loading.textContent = 'Comparing saved versions…';
  historyDialogContent.append(loading);
  try {
    const base = websiteApiBase(site);
    const comparison = await api(`${base}/compare?older=${ordered[0]}&newer=${ordered[1]}`);
    renderVersionComparison(site, comparison, entries, pageState);
  } catch (error) {
    const failure = document.createElement('div');
    failure.className = 'history-dialog-error';
    failure.setAttribute('role', 'alert');
    failure.textContent = `Could not compare these versions: ${error.message}`;
    historyDialogContent.replaceChildren(failure);
  }
}

function renderVersionComparison(site, comparison, entries, pageState) {
  historyDialogContent.replaceChildren();
  const back = document.createElement('button');
  back.className = 'history-back-button';
  back.type = 'button';
  back.textContent = '← Back to history';
  back.addEventListener('click', () => renderHistoryList(historyDialogContent, entries, pageState));
  const heading = document.createElement('h3');
  heading.className = 'history-detail-heading';
  heading.textContent = 'Saved version comparison';
  const result = document.createElement('p');
  result.className = 'change-context-note';
  result.textContent = comparison.same
    ? 'These versions have no text differences at the current sensitivity setting.'
    : 'Text changes between the earlier and later saved versions.';
  const textDiff = document.createElement('div');
  textDiff.className = 'history-detail-content';
  addChangeDetail(textDiff, 'Added', comparison.added_content);
  addChangeDetail(textDiff, 'Removed', comparison.removed_content);
  if (comparison.details_truncated) {
    const note = document.createElement('p');
    note.className = 'change-context-note';
    note.textContent = 'The displayed text diff is truncated. Full page text remains saved.';
    textDiff.append(note);
  }
  const imageGrid = document.createElement('div');
  imageGrid.className = 'version-screenshot-grid';
  for (const [label, version] of [['Earlier version', comparison.older], ['Later version', comparison.newer]]) {
    const panel = document.createElement('section');
    panel.className = 'version-screenshot-panel';
    const title = document.createElement('h4');
    title.textContent = `${label} · ${displayTime(version.checked_at)}`;
    panel.append(title);
    if (version.has_screenshot) {
      const image = document.createElement('img');
      image.alt = `${label} screenshot`;
      image.src = `${websiteApiBase(site)}/snapshots/${version.id}/screenshot`;
      image.addEventListener('error', () => {
        const warning = document.createElement('p');
        warning.className = 'history-dialog-state';
        warning.textContent = 'This screenshot is unavailable.';
        image.replaceWith(warning);
      }, { once: true });
      panel.append(image);
    } else {
      const missing = document.createElement('p');
      missing.className = 'history-dialog-state';
      missing.textContent = version.screenshot_error || 'No screenshot was saved for this version.';
      panel.append(missing);
    }
    imageGrid.append(panel);
  }
  historyDialogContent.append(back, heading, result, textDiff, imageGrid);
}

async function loadHistoryPage(site, filter, offset, existingEntries) {
  const requestId = historyDialogRequestId;
  const dataRequestId = ++historyDataRequestId;
  try {
    const page = await requestHistoryPage(site, filter, offset);
    if (!historyDialog.open || requestId !== historyDialogRequestId || dataRequestId !== historyDataRequestId) return;
    const entries = [...existingEntries, ...page.history];
    renderHistoryList(historyDialogContent, entries, {
      site, filter, hasMore: page.has_more, nextOffset: page.next_offset,
    });
  } catch (error) {
    if (!historyDialog.open || requestId !== historyDialogRequestId || dataRequestId !== historyDataRequestId) return;
    historyDialogContent.replaceChildren();
    const failure = document.createElement('div'); failure.className = 'history-dialog-error';
    failure.setAttribute('role', 'alert'); failure.textContent = `Could not load history: ${error.message}`;
    historyDialogContent.append(failure);
  }
}

async function requestHistoryPage(site, filter, offset) {
  const base = websiteApiBase(site);
  try {
    return await api(`${base}/history/page?offset=${offset}&filter=${encodeURIComponent(filter)}`);
  } catch (error) {
    // Older WebWatch servers expose /history but not the paginated endpoint.
    // Fall back only for a non-JSON 404; real API errors (such as a deleted site)
    // keep their original message and are never presented as empty history.
    if (error.status !== 404 || error.data?.error) throw error;
    const legacyHistory = await api(`${base}/history`);
    if (!Array.isArray(legacyHistory)) {
      throw new Error('WebWatch returned an unexpected history response.');
    }
    const filteredHistory = filter === 'changes'
      ? legacyHistory.filter((entry) => entry.status === 'changed')
      : legacyHistory;
    const pageSize = 20;
    const history = filteredHistory.slice(offset, offset + pageSize);
    return {
      history,
      has_more: offset + history.length < filteredHistory.length,
      next_offset: offset + pageSize,
    };
  }
}

function finishHistoryDialogClose() {
  document.body.classList.remove('history-dialog-open');
  historyDialogRequestId += 1;
  const returnTarget = historyDialogReturnFocus;
  historyDialogReturnFocus = null;
  if (returnTarget?.isConnected) returnTarget.focus();
}

function closeHistoryDialog() {
  if (historyDialog.open) historyDialog.close();
  finishHistoryDialogClose();
}

async function openHistoryDialog(site, trigger) {
  historyDialogReturnFocus = trigger;
  historyDialogTitle.textContent = site.is_demo
    ? 'WebWatch controlled fixture'
    : (site.display_name || new URL(site.url).hostname.replace(/^www\./, ''));
  historyDialogUrl.textContent = site.is_demo ? 'Bundled local HTML fixture' : site.url;
  historyDialogUrl.removeAttribute?.('href');
  if (!site.is_demo) {
    const source = new URL(site.url);
    if (source.protocol === 'http:' || source.protocol === 'https:') {
      historyDialogUrl.href = source.href;
      historyDialogUrl.target = '_blank';
      historyDialogUrl.rel = 'noopener noreferrer';
      historyDialogUrl.setAttribute('aria-label', `Open monitored webpage: ${source.hostname}`);
    }
  }
  historyDialogContent.replaceChildren();
  historyDialogContent.selectedVersionIds = new Set();
  const loading = document.createElement('p');
  loading.className = 'history-dialog-state';
  loading.textContent = 'Loading check history…';
  historyDialogContent.append(loading);
  document.body.classList.add('history-dialog-open');
  historyDialog.showModal();
  historyDialogClose.focus();
  const requestId = ++historyDialogRequestId;
  const dataRequestId = ++historyDataRequestId;
  try {
    const entries = await requestHistoryPage(site, 'all', 0);
    if (!historyDialog.open || requestId !== historyDialogRequestId || dataRequestId !== historyDataRequestId) return;
    renderHistoryList(historyDialogContent, entries.history, {
      site, filter: 'all', hasMore: entries.has_more, nextOffset: entries.next_offset,
    });
  } catch (error) {
    if (!historyDialog.open || requestId !== historyDialogRequestId) return;
    historyDialogContent.replaceChildren();
    const failure = document.createElement('div');
    failure.className = 'history-dialog-error';
    failure.setAttribute('role', 'alert');
    failure.textContent = `Could not load history: ${error.message}`;
    historyDialogContent.append(failure);
  }
}

async function openRegionPicker(site) {
  regionDialogContent.replaceChildren();
  const loading = document.createElement('p');
  loading.className = 'history-dialog-state';
  loading.textContent = 'Loading a safe rendered preview…';
  regionDialogContent.append(loading);
  regionDialog.showModal();
  regionDialogClose.focus();
  try {
    const preview = await api(`${websiteApiBase(site)}/preview`, { method: 'POST' });
    renderRegionPicker(site, preview);
  } catch (error) {
    const failure = document.createElement('div');
    failure.className = 'history-dialog-error';
    failure.setAttribute('role', 'alert');
    failure.textContent = `Could not preview this page: ${error.message}`;
    regionDialogContent.replaceChildren(failure);
  }
}

function renderRegionPicker(site, preview) {
  regionDialogContent.replaceChildren();
  const note = document.createElement('p');
  note.className = 'region-picker-note';
  note.textContent = 'Choose a page region and mark it Watch or Ignore. If no regions are marked Watch, WebWatch monitors the whole page minus ignored regions.';
  regionDialogContent.append(note);
  let previewOverlay = null;
  if (preview.screenshot) {
    const previewWrap = document.createElement('div');
    previewWrap.className = 'region-preview-wrap';
    const image = document.createElement('img');
    image.className = 'region-preview-image';
    image.alt = 'Protected rendered preview of the monitored webpage';
    image.src = preview.screenshot;
    previewOverlay = document.createElement('div');
    previewOverlay.className = 'region-preview-overlay';
    previewWrap.append(image, previewOverlay);
    regionDialogContent.append(previewWrap);
  } else {
    const failure = document.createElement('p');
    failure.className = 'history-dialog-state';
    failure.textContent = preview.screenshot_error || 'The preview screenshot is unavailable. You can still choose regions from the list.';
    regionDialogContent.append(failure);
  }
  if (preview.warnings?.length) {
    const warning = document.createElement('p');
    warning.className = 'region-picker-warning';
    warning.setAttribute('role', 'status');
    warning.textContent = preview.warnings.join(' ');
    regionDialogContent.append(warning);
  }
  const actionBySelector = new Map();
  for (const selector of preview.watched || []) actionBySelector.set(selector, 'watched');
  for (const selector of preview.ignored || []) actionBySelector.set(selector, 'ignored');
  const candidates = [...(preview.regions || [])];
  const known = new Set(candidates.map((candidate) => candidate.selector));
  for (const selector of [...(preview.watched || []), ...(preview.ignored || [])]) {
    if (!known.has(selector)) candidates.push({ selector, label: 'Saved region not found in this preview', text: 'This selector may be stale and will not match until repaired.', stale: true });
  }
  const list = document.createElement('div');
  list.className = 'region-candidate-list';
  const actionControls = new Map();
  const overlayControls = new Map();
  const setAction = (selector, mode) => {
    actionBySelector.delete(selector);
    if (mode !== 'none') actionBySelector.set(selector, mode);
    const control = actionControls.get(selector);
    if (control && control.value !== mode) control.value = mode;
    const overlayControl = overlayControls.get(selector);
    if (overlayControl) {
      overlayControl.dataset.mode = mode;
      overlayControl.setAttribute('aria-label', `Set ${selector} to ${mode === 'none' ? 'no rule' : mode}`);
    }
  };
  for (const candidate of candidates) {
    const card = document.createElement('section');
    card.className = 'region-candidate';
    const heading = document.createElement('strong');
    heading.textContent = candidate.label || 'Page region';
    const excerpt = document.createElement('p');
    excerpt.textContent = candidate.text || candidate.selector;
    const selector = document.createElement('code');
    selector.textContent = candidate.selector;
    const label = document.createElement('label');
    label.textContent = 'Monitor action';
    const action = document.createElement('select');
    action.setAttribute('aria-label', `Monitor action for ${candidate.selector}`);
    for (const [value, text] of [['none', 'No rule'], ['watched', 'Watch'], ['ignored', 'Ignore']]) {
      const option = document.createElement('option');
      option.value = value; option.textContent = text; action.append(option);
    }
    action.value = actionBySelector.get(candidate.selector) || 'none';
    actionControls.set(candidate.selector, action);
    action.addEventListener('change', () => {
      setAction(candidate.selector, action.value);
    });
    label.append(action);
    card.append(heading, excerpt, selector, label);
    if (candidate.stale) {
      const stale = document.createElement('p');
      stale.className = 'region-picker-warning';
      stale.textContent = 'Remove this stale rule with No rule, or select a current region above.';
      card.append(stale);
    }
    list.append(card);
    if (previewOverlay && !candidate.stale && preview.viewport?.width && preview.viewport?.height) {
      const regionButton = document.createElement('button');
      regionButton.className = 'region-preview-marker';
      regionButton.type = 'button';
      regionButton.dataset.mode = action.value;
      regionButton.style.left = `${Math.max(0, candidate.x / preview.viewport.width * 100)}%`;
      regionButton.style.top = `${Math.max(0, candidate.y / preview.viewport.height * 100)}%`;
      regionButton.style.width = `${Math.min(100, candidate.width / preview.viewport.width * 100)}%`;
      regionButton.style.height = `${Math.min(100, candidate.height / preview.viewport.height * 100)}%`;
      regionButton.title = candidate.label || candidate.selector;
      regionButton.setAttribute('aria-label', `Set ${candidate.selector} to ${action.value === 'none' ? 'no rule' : action.value}`);
      regionButton.addEventListener('click', () => {
        const current = actionBySelector.get(candidate.selector) || 'none';
        const next = ({ none: 'watched', watched: 'ignored', ignored: 'none' })[current];
        setAction(candidate.selector, next);
      });
      overlayControls.set(candidate.selector, regionButton);
      previewOverlay.append(regionButton);
    }
  }
  if (!candidates.length) {
    const empty = document.createElement('p');
    empty.className = 'history-dialog-state';
    empty.textContent = 'No semantic page regions were found. Try a page with headings, articles, or sections.';
    list.append(empty);
  }
  regionDialogContent.append(list);
  const actions = document.createElement('div');
  actions.className = 'region-picker-actions';
  const reset = document.createElement('button');
  reset.className = 'secondary-button'; reset.type = 'button'; reset.textContent = 'Reset selectors';
  reset.addEventListener('click', () => {
    for (const selector of [...actionControls.keys()]) setAction(selector, 'none');
    for (const selector of [...actionBySelector.keys()]) setAction(selector, 'none');
  });
  const save = document.createElement('button');
  save.className = 'secondary-button'; save.type = 'button'; save.textContent = 'Save region rules';
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const watched = [...actionBySelector].filter(([, mode]) => mode === 'watched').map(([selector]) => selector);
      const ignored = [...actionBySelector].filter(([, mode]) => mode === 'ignored').map(([selector]) => selector);
      await api(`${websiteApiBase(site)}/selectors`, { method: 'PUT', body: JSON.stringify({ watched, ignored }) });
      regionDialog.close();
      await loadWebsites();
    } catch (error) {
      const failure = document.createElement('p');
      failure.className = 'history-dialog-error';
      failure.setAttribute('role', 'alert');
      failure.textContent = `Could not save region rules: ${error.message}`;
      regionDialogContent.prepend(failure);
    } finally { save.disabled = false; }
  });
  actions.append(reset, save);
  regionDialogContent.append(actions);
}

historyDialogClose.addEventListener('click', closeHistoryDialog);
historyDialog.addEventListener('click', (event) => {
  if (event.target === historyDialog) closeHistoryDialog();
});
historyDialog.addEventListener('cancel', (event) => {
  event.preventDefault();
  closeHistoryDialog();
});
historyDialog.addEventListener('close', finishHistoryDialogClose);
regionDialogClose.addEventListener('click', () => regionDialog.close());
regionDialog.addEventListener('click', (event) => {
  if (event.target === regionDialog) regionDialog.close();
});

const guideClose = document.querySelector('#guide-close');
const openGuide = document.querySelector('#open-guide');
openGuide.addEventListener('click', () => {
  document.querySelector('.quick-menu').open = false;
  guideDialog.showModal();
  guideClose.focus();
});
guideClose.addEventListener('click', () => guideDialog.close());
guideDialog.addEventListener('click', (event) => {
  if (event.target === guideDialog) guideDialog.close();
});

function render() {
  list.replaceChildren();
  const ready = loadState === 'ready' || websites.length > 0;
  loadingState.hidden = loadState !== 'loading' || websites.length > 0;
  loadErrorState.hidden = loadState !== 'error';
  emptyState.hidden = !ready || totalWebsiteCount > 0;
  filterTools.hidden = !ready || totalWebsiteCount === 0;
  note.hidden = !ready || totalWebsiteCount === 0;
  count.textContent = `${totalWebsiteCount} ${totalWebsiteCount === 1 ? 'website' : 'websites'}`;

  const filtersActive = Boolean(searchInput.value.trim()) || statusFilter.value !== 'all';
  matchSummary.hidden = !ready || !filtersActive;
  matchSummary.textContent = filtersActive
    ? `Showing ${websites.length} of ${totalWebsiteCount} ${totalWebsiteCount === 1 ? 'website' : 'websites'}`
    : '';
  filterEmptyState.hidden = !ready || totalWebsiteCount === 0 || websites.length > 0;

  renderWebsiteRows(list, websites);
}

function websiteApiBase(site) {
  return `/api/websites/${site.id}`;
}

function renderWebsiteRows(target, sites) {
  target.replaceChildren();
  for (const site of sites) {
    const row = document.createElement('article');
    row.className = 'website-row';
    row.dataset.status = site.last_check_status;

    const main = document.createElement('div');
    main.className = 'site-main';
    const siteIcon = document.createElement('span');
    siteIcon.className = 'site-icon';
    siteIcon.setAttribute('aria-hidden', 'true');
    const siteIconGlobe = document.createElement('span');
    siteIconGlobe.className = 'site-icon-globe';
    siteIcon.append(siteIconGlobe);
    let title;
    if (site.is_demo) {
      title = document.createElement('span');
      title.className = 'site-name';
      title.textContent = 'WebWatch controlled fixture';
      const badge = document.createElement('span');
      badge.className = 'demo-badge';
      badge.textContent = `DEMO · V${site.demo_version}`;
      title.append(badge);
    } else {
      const parsedSiteUrl = new URL(site.url);
      const canLinkToSite = parsedSiteUrl.protocol === 'http:' || parsedSiteUrl.protocol === 'https:';
      title = document.createElement(canLinkToSite ? 'a' : 'span');
      title.className = 'site-name';
      if (canLinkToSite) {
        title.href = parsedSiteUrl.href;
        title.target = '_blank';
        title.rel = 'noopener noreferrer';
      }
      title.textContent = site.display_name || parsedSiteUrl.hostname.replace(/^www\./, '');
    }
    const address = document.createElement('div');
    address.className = 'site-url';
    address.textContent = site.is_demo ? 'Bundled local HTML fixture' : site.url;
    if (!site.is_demo) address.title = site.url;
    const siteIdentity = document.createElement('div');
    siteIdentity.className = 'site-identity';
    siteIdentity.append(title, address);
    title.title = site.is_demo ? 'WebWatch controlled fixture' : (site.display_name || new URL(site.url).hostname);
    main.append(siteIcon, siteIdentity);

    const status = document.createElement('span');
    status.className = 'site-status';
    status.dataset.status = site.last_check_status;
    const dot = document.createElement('span');
    dot.className = 'status-dot';
    dot.setAttribute('aria-hidden', 'true');
    if (site.last_check_status === 'changed') {
      status.title = 'The latest check detected a content change.';
      status.append(dot, document.createTextNode('Changed'));
    } else {
      status.append(dot, document.createTextNode(statusLabel(site)));
    }

    const time = document.createElement('span');
    time.className = 'site-time';
    const timeIcon = document.createElement('span');
    timeIcon.className = 'site-time-icon';
    timeIcon.setAttribute('aria-hidden', 'true');
    timeIcon.textContent = '◷';
    const timeText = document.createElement('span');
    if (['queued', 'running'].includes(site.active_check_job?.status)) {
      timeText.textContent = `${site.active_check_job.status === 'queued' ? 'Check queued' : 'Check started'}: ${displayTime(site.active_check_job.updated_at)}`;
    } else if (site.active_check_job?.status === 'retry') {
      timeText.textContent = `Retry pending: ${displayTime(site.active_check_job.next_run_at || site.active_check_job.updated_at)}`;
    } else {
      timeText.textContent = `${['error', 'blocked', 'captcha', 'rate_limited'].includes(site.last_check_status) ? 'Last attempt' : 'Last checked'}: ${displayTime(site.last_checked_at)}`;
    }
    time.append(timeIcon, timeText);
    time.title = site.last_checked_at ? `Last check attempt: ${site.last_checked_at}` : 'This page has not been checked';

    const nextCheck = document.createElement('span');
    nextCheck.className = 'site-next-check';
    nextCheck.textContent = `Every ${site.check_interval_minutes || 60} min · Next: ${nextCheckDescription(site)}`;

    const actions = document.createElement('div');
    actions.className = 'row-actions';
    const historyButton = document.createElement('button');
    historyButton.className = 'history-button';
    historyButton.type = 'button';
    historyButton.textContent = 'History';
    historyButton.setAttribute('aria-haspopup', 'dialog');
    historyButton.addEventListener('click', () => openHistoryDialog(site, historyButton));
    const check = document.createElement('button');
    check.className = 'check-button';
    check.type = 'button';
    const serverCheckActive = ['queued', 'running'].includes(site.active_check_job?.status);
    const rowBusy = pendingChecks.has(site.id) || serverCheckActive || checkAllWebsiteIds.has(site.id) || pendingRemovals.has(site.id) || pendingDemoVersions.has(site.id) || pendingVisibilityChanges.has(site.id);
    check.disabled = checkAllRunning || rowBusy;
    check.textContent = (pendingChecks.has(site.id) || site.active_check_job?.status === 'running')
      ? 'Checking…'
      : (site.active_check_job?.status === 'queued' || checkAllWebsiteIds.has(site.id) ? 'Queued for check' : 'Check now');
    check.setAttribute('aria-label', `Check ${new URL(site.url).hostname}`);
    check.addEventListener('click', () => checkWebsite(site.id));
    actions.append(historyButton, check);
    const moreActions = document.createElement('details');
    moreActions.className = 'site-more-menu';
    const moreSummary = document.createElement('summary');
    moreSummary.textContent = 'More';
    moreSummary.setAttribute('aria-label', `More actions for ${site.display_name || new URL(site.url).hostname}`);
    const morePanel = document.createElement('div');
    morePanel.className = 'site-more-panel';
    if (site.is_demo) {
      const demoVersion = document.createElement('button');
      demoVersion.className = 'demo-version-button';
      demoVersion.type = 'button';
      demoVersion.disabled = checkAllRunning || rowBusy;
      demoVersion.textContent = `Switch to fixture V${site.demo_version === 1 ? 2 : 1}`;
      demoVersion.addEventListener('click', () => switchDemoVersion(site.id));
      morePanel.append(demoVersion);
    }
    const exportHistory = document.createElement('a');
    exportHistory.className = 'export-button';
    exportHistory.href = `${websiteApiBase(site)}/history.json`;
    exportHistory.download = `webwatch-history-${site.id}.json`;
    const exportIcon = document.createElement('span');
    exportIcon.className = 'export-icon';
    exportIcon.setAttribute('aria-hidden', 'true');
    exportIcon.textContent = '↓';
    const exportLabel = document.createElement('span');
    exportLabel.textContent = 'Export';
    exportHistory.append(exportIcon, exportLabel);
    exportHistory.setAttribute('aria-label', `Export ${site.is_demo ? 'local demo' : new URL(site.url).hostname} history as JSON`);
    const remove = document.createElement('button');
    remove.className = 'remove-button';
    remove.type = 'button';
    remove.disabled = checkAllRunning || rowBusy;
    remove.textContent = 'Remove';
    remove.setAttribute('aria-label', `Remove ${site.is_demo ? 'local demo' : new URL(site.url).hostname}`);
    remove.addEventListener('click', () => removeWebsite(site.id));
    const visibilityAction = document.createElement('button');
    visibilityAction.className = 'visibility-button';
    visibilityAction.type = 'button';
    visibilityAction.disabled = checkAllRunning || rowBusy;
    visibilityAction.textContent = site.is_hidden ? 'Unhide' : 'Hide';
    visibilityAction.setAttribute('aria-label', `${site.is_hidden ? 'Unhide' : 'Hide'} ${site.is_demo ? 'local demo' : new URL(site.url).hostname}`);
    visibilityAction.addEventListener('click', () => setWebsiteHidden(site.id, !site.is_hidden));
    morePanel.append(exportHistory, visibilityAction, remove);

    if (!site.is_demo) {
      const htmlExport = document.createElement('a');
      htmlExport.className = 'export-button';
      htmlExport.href = `${websiteApiBase(site)}/history.html`;
      htmlExport.target = '_blank';
      htmlExport.rel = 'noopener noreferrer';
      htmlExport.textContent = 'HTML report';
      htmlExport.setAttribute('aria-label', `Open printable HTML history for ${site.display_name || new URL(site.url).hostname}`);
      morePanel.append(htmlExport);
      const rssFeed = document.createElement('a');
      rssFeed.className = 'export-button';
      rssFeed.href = `/feed/websites/${site.id}.xml`;
      rssFeed.textContent = 'RSS feed';
      rssFeed.setAttribute('aria-label', `Open RSS change feed for ${site.display_name || site.url}`);
      morePanel.append(rssFeed);
      const settings = document.createElement('details');
      settings.className = 'site-settings';
      const summary = document.createElement('summary');
      summary.textContent = 'Settings';
      const settingsForm = document.createElement('form');
      settingsForm.className = 'site-settings-form';
      const nameLabel = document.createElement('label');
      nameLabel.textContent = 'Name';
      const editName = document.createElement('input');
      editName.type = 'text'; editName.maxLength = 80; editName.required = true;
      editName.value = site.display_name || new URL(site.url).hostname;
      nameLabel.append(editName);
      const intervalLabel = document.createElement('label');
      intervalLabel.textContent = 'Interval (minutes)';
      const editInterval = document.createElement('input');
      editInterval.type = 'number'; editInterval.min = '15'; editInterval.max = '1440'; editInterval.step = '1';
      editInterval.setAttribute('list', 'website-interval-presets');
      editInterval.value = String(site.check_interval_minutes || 60);
      intervalLabel.append(editInterval);
      const documentLimitLabel = document.createElement('label');
      documentLimitLabel.textContent = 'Main document limit (KiB)';
      const documentLimitInput = document.createElement('input');
      documentLimitInput.type = 'number';
      documentLimitInput.min = '512';
      documentLimitInput.max = '4096';
      documentLimitInput.step = '64';
      documentLimitInput.required = true;
      documentLimitInput.value = String(site.max_document_size_kib || 2048);
      documentLimitInput.setAttribute('aria-label', 'Main document limit in KiB');
      documentLimitLabel.append(documentLimitInput);
      const documentLimitNote = document.createElement('p');
      documentLimitNote.className = 'retention-note';
      documentLimitNote.textContent = 'Applies only to this page’s main HTML document (512–4096 KiB). Subresources stay capped at 512 KiB; the whole render stays within 8 MiB and 100 requests.';
      const sensitivityLabel = document.createElement('label');
      sensitivityLabel.textContent = 'Change sensitivity';
      const sensitivitySelect = document.createElement('select');
      sensitivitySelect.setAttribute('aria-label', 'Change sensitivity');
      for (const [value, label] of [['low', 'Low'], ['normal', 'Normal'], ['high', 'High']]) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        sensitivitySelect.append(option);
      }
      sensitivitySelect.value = site.change_sensitivity || 'normal';
      sensitivityLabel.append(sensitivitySelect);
      settingsForm.append(nameLabel, intervalLabel, documentLimitLabel, documentLimitNote, sensitivityLabel);
      const keywordEnabledLabel = document.createElement('label');
      const keywordEnabled = document.createElement('input');
      keywordEnabled.type = 'checkbox';
      keywordEnabled.checked = Boolean(site.alert_keywords_enabled);
      keywordEnabledLabel.append(keywordEnabled, document.createTextNode(' Alert only when selected keywords appear in a change'));
      const keywordLabel = document.createElement('label');
      keywordLabel.textContent = 'Alert keywords (one per line)';
      const keywordInput = document.createElement('textarea');
      keywordInput.rows = 3;
      keywordInput.maxLength = 5100;
      keywordInput.value = (site.alert_keywords || []).join('\n');
      keywordLabel.append(keywordInput);
      settingsForm.append(keywordEnabledLabel, keywordLabel);
      const numericLabel = document.createElement('label');
      numericLabel.textContent = 'Numeric values to track (CSS selectors, one per line)';
      const numericInput = document.createElement('textarea');
      numericInput.rows = 2;
      numericInput.value = (site.numeric_selectors || []).join('\n');
      numericLabel.append(numericInput);
      settingsForm.append(numericLabel);
      const sensitivityNote = document.createElement('p');
      sensitivityNote.className = 'retention-note';
      sensitivityNote.textContent = 'Normal ignores labelled view counters and volatile request tokens. Low also ignores relative-time labels; High treats both as changes.';
      settingsForm.append(sensitivityNote);
      const retentionInputs = {};
      const retentionSettings = [
        ['unchanged_history_limit', 'No-change records', 200],
        ['changed_history_limit', 'Content-change records', 400],
        ['serious_event_limit', 'Serious events', 600],
      ];
      for (const [key, labelText, defaultValue] of retentionSettings) {
        const label = document.createElement('label');
        label.textContent = labelText;
        const input = document.createElement('input');
        input.type = 'number'; input.min = '1'; input.max = '10000'; input.step = '1';
        input.required = true;
        input.value = String(site[key] ?? defaultValue);
        input.setAttribute('aria-label', `${labelText} to keep`);
        label.append(input);
        retentionInputs[key] = input;
        settingsForm.append(label);
      }
      const retentionNote = document.createElement('p');
      retentionNote.className = 'retention-note';
      retentionNote.textContent = 'Oldest excess records are removed separately for each category. The baseline and latest snapshot are kept.';
      const saveSettings = document.createElement('button');
      saveSettings.type = 'submit'; saveSettings.textContent = 'Save settings';
      const settingsStatus = document.createElement('p');
      settingsStatus.className = 'schedule-status';
      settingsStatus.setAttribute('role', 'status');
      settingsStatus.setAttribute('aria-live', 'polite');
      settingsForm.append(retentionNote, settingsStatus, saveSettings);
      settingsForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        saveSettings.disabled = true;
        settingsStatus.textContent = 'Saving website settings…';
        try {
          const saved = await api(`${websiteApiBase(site)}/settings`, { method: 'PATCH', body: JSON.stringify({
            display_name: editName.value.trim(), check_interval_minutes: Number(editInterval.value),
            max_document_size_kib: Number(documentLimitInput.value),
            change_sensitivity: sensitivitySelect.value,
            alert_keywords_enabled: keywordEnabled.checked,
            alert_keywords: keywordInput.value.split('\n').map((word) => word.trim()).filter(Boolean),
            numeric_selectors: numericInput.value.split('\n').map((selector) => selector.trim()).filter(Boolean),
            unchanged_history_limit: Number(retentionInputs.unchanged_history_limit.value),
            changed_history_limit: Number(retentionInputs.changed_history_limit.value),
            serious_event_limit: Number(retentionInputs.serious_event_limit.value),
          }) });
          if (saved.check_interval_minutes !== Number(editInterval.value)) {
            throw new Error('The server did not confirm the selected interval.');
          }
          if (saved.max_document_size_kib !== Number(documentLimitInput.value)) {
            throw new Error('The server did not confirm the selected main document limit.');
          }
          settingsStatus.textContent = `Saved. This website is set to check every ${saved.check_interval_minutes} minutes.`;
          message.textContent = `Saved ${saved.display_name}: checks every ${saved.check_interval_minutes} minutes.`;
          settings.open = false;
          await loadWebsites();
        } catch (error) {
          saveSettings.title = error.message;
          settingsStatus.textContent = `Could not save settings: ${error.message}`;
          settingsStatus.setAttribute('role', 'alert');
          message.textContent = `Could not save website settings: ${error.message}`;
        } finally { saveSettings.disabled = false; }
      });
      settings.append(summary, settingsForm);
      for (const selector of site.numeric_selectors || []) {
        const chartButton = document.createElement('button');
        chartButton.type = 'button';
        chartButton.className = 'secondary-button';
        chartButton.textContent = `Show values: ${selector}`;
        const chart = document.createElement('div');
        chart.className = 'numeric-chart';
        chartButton.addEventListener('click', async () => {
          chart.textContent = 'Loading numeric history…';
          try {
            const result = await api(`${websiteApiBase(site)}/metrics?selector=${encodeURIComponent(selector)}`);
            if (!result.points.length) { chart.textContent = 'No numeric observations yet. Run a successful check after saving this selector.'; return; }
            const values = result.points.map((point) => point.value);
            const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1;
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 320 100'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `Historical values for ${selector}`);
            const line = document.createElementNS(svg.namespaceURI, 'polyline');
            line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'currentColor'); line.setAttribute('stroke-width', '3');
            line.setAttribute('points', values.map((value, index) => `${values.length < 2 ? 160 : 10 + index * 300 / (values.length - 1)},${90 - (value - min) * 80 / span}`).join(' '));
            svg.append(line);
            const latest = document.createElement('p');
            latest.textContent = `Latest: ${result.points.at(-1).raw_text} · ${result.points.at(-1).checked_at}`;
            chart.replaceChildren(svg, latest);
          } catch (error) { chart.textContent = `Could not load numeric history: ${error.message}`; }
        });
        settings.append(chartButton, chart);
      }
      morePanel.append(settings);
      const regionsButton = document.createElement('button');
      regionsButton.className = 'region-picker-open';
      regionsButton.type = 'button';
      regionsButton.textContent = 'Choose watched regions';
      regionsButton.addEventListener('click', () => openRegionPicker(site));
      morePanel.append(regionsButton);
    }

    moreActions.append(moreSummary, morePanel);
    actions.append(moreActions);

    row.append(main, status, time, nextCheck, actions);
    if (site.last_error) {
      const error = document.createElement('p');
      error.className = 'site-error';
      error.setAttribute('role', 'alert');
      error.textContent = site.last_error;
      row.append(error);
    }
    if (site.last_selector_warning) {
      const warning = document.createElement('p');
      warning.className = 'site-selector-warning';
      warning.setAttribute('role', 'status');
      const detail = Array.isArray(site.last_selector_warning)
        ? site.last_selector_warning.join(' ')
        : String(site.last_selector_warning);
      warning.textContent = `Some CSS selectors need attention: ${detail}`;
      row.append(warning);
    }
    if (site.last_screenshot_warning) {
      const warning = document.createElement('p');
      warning.className = 'site-screenshot-warning';
      warning.setAttribute('role', 'status');
      warning.textContent = `Latest screenshot unavailable: ${site.last_screenshot_warning}`;
      row.append(warning);
    }
    target.append(row);
  }
}

function websiteMenuIsOpen() {
  return Array.from(list.querySelectorAll('.site-more-menu')).some((menu) => menu.open);
}

async function loadWebsites({ background = false } = {}) {
  const requestedHiddenView = hiddenView;
  const requestRevision = watchlistRevision;
  const requestId = ++watchlistRequestId;
  if (!background) {
    loadState = 'loading';
    render();
  }
  try {
    const params = new URLSearchParams();
    params.set('hidden', String(requestedHiddenView));
    if (searchInput.value.trim()) params.set('q', searchInput.value.trim());
    if (statusFilter.value !== 'all') params.set('status', statusFilter.value);
    const queryString = params.toString();
    const suffix = queryString ? `?${queryString}` : '';
    const loadedWebsites = await api(`/api/websites${suffix}`);
    if (requestRevision !== watchlistRevision || requestId !== watchlistRequestId
        || requestedHiddenView !== hiddenView || (background && websiteMenuIsOpen())) return;
    websites = loadedWebsites;
    totalWebsiteCount = loadedWebsites.totalCount ?? loadedWebsites.length;
    loadState = 'ready';
    render();
    delete message.dataset.kind;
    message.textContent = '';
    await loadStats();
  } catch (error) {
    if (requestRevision !== watchlistRevision || requestId !== watchlistRequestId
        || requestedHiddenView !== hiddenView || (background && websiteMenuIsOpen())) return;
    loadState = 'error';
    render();
    message.textContent = `Could not load the watchlist: ${error.message}`;
  }
}

async function loadStats() {
  try {
    const stats = await api('/api/stats');
    watchStats.replaceChildren();
    const metrics = [
      ['Monitored websites', stats.websites],
      ['Successful checks', stats.successful_checks],
      ['Detected changes', stats.detected_changes],
    ];
    for (const [label, value] of metrics) {
      const tile = document.createElement('div');
      tile.className = 'watch-stat-tile';
      const metricLabel = document.createElement('span');
      metricLabel.className = 'watch-stat-label';
      metricLabel.textContent = label;
      const metricValue = document.createElement('strong');
      metricValue.className = 'watch-stat-value';
      metricValue.textContent = String(value);
      tile.append(metricLabel, metricValue);
      watchStats.append(tile);
    }
    if (stats.demo_runs) {
      const demoSummary = document.createElement('p');
      demoSummary.className = 'watch-stats-demo';
      demoSummary.textContent = `${stats.demo_runs} local demo website${stats.demo_runs === 1 ? '' : 's'} (checks excluded from these totals)`;
      watchStats.append(demoSummary);
    }
  } catch {
    watchStats.textContent = 'Database summary is temporarily unavailable.';
  }
}

async function switchWebsiteView() {
  hiddenView = !hiddenView;
  searchInput.value = '';
  statusFilter.value = 'all';
  applyWebsiteView();
  websites = [];
  totalWebsiteCount = 0;
  loadState = 'loading';
  render();
  await loadWebsites();
}

hiddenViewToggle.addEventListener('click', switchWebsiteView);

function updateCheckAllProgress(completed, total, currentLabel = '') {
  const percentage = total ? Math.floor((completed / total) * 100) : 100;
  checkAllMeter.max = Math.max(total, 1);
  checkAllMeter.value = completed;
  const progressText = `${completed} of ${total} complete (${percentage}%)`;
  checkAllStatus.textContent = currentLabel ? `${progressText} · Checking ${currentLabel}` : progressText;
}

async function checkAllWebsites() {
  if (checkAllRunning) return;
  checkAllRunning = true;
  const runId = ++checkAllRunId;
  checkAllButton.disabled = true;
  checkAllButton.textContent = 'Checking…';
  checkAllProgress.hidden = false;
  checkAllMeter.max = 1;
  checkAllMeter.value = 0;
  checkAllFailures.replaceChildren();
  checkAllFailures.hidden = true;
  checkAllStatus.textContent = 'Loading eligible websites…';
  render();

  let completed = 0;
  let successful = 0;
  let failed = 0;
  let changes = 0;
  let unreachableAlerts = 0;
  let recoveries = 0;
  let seriousAlerts = 0;
  const failures = [];
  const unavailable = [];
  let queuedIds = [];
  try {
    const [mainSites, hiddenSites] = await Promise.all([
      api('/api/websites?hidden=false'),
      api('/api/websites?hidden=true'),
    ]);
    if (!checkAllRunning || runId !== checkAllRunId) return;
    const eligibleById = new Map();
    for (const site of [...mainSites, ...hiddenSites]) {
      if (!site.is_demo) eligibleById.set(site.id, site);
    }
    const eligibleSites = [...eligibleById.values()];
    const total = eligibleSites.length;
    queuedIds = eligibleSites.filter((site) => {
      const alreadyBusy = pendingChecks.has(site.id) || pendingRemovals.has(site.id)
        || pendingDemoVersions.has(site.id) || pendingVisibilityChanges.has(site.id);
      if (alreadyBusy) {
        completed += 1;
        unavailable.push(`${site.url} (already being changed or checked)`);
        return false;
      }
      return true;
    }).map((site) => site.id);
    queuedIds.forEach((id) => checkAllWebsiteIds.add(id));
    updateCheckAllProgress(completed, total);
    render();

    for (const site of eligibleSites) {
      if (!checkAllWebsiteIds.has(site.id)) continue;
      pendingChecks.add(site.id);
      render();
      updateCheckAllProgress(completed, total, site.url);
      try {
        const result = await api(`/api/websites/${site.id}/check`, { method: 'POST', timeoutMs: CHECK_REQUEST_TIMEOUT_MS });
        successful += 1;
        if (result.last_check_status === 'changed') changes += 1;
        if (recordSeriousNotification(result.notification)) {
          seriousAlerts += 1;
          if (result.notification.event_type === 'recovered') recoveries += 1;
          else if (result.notification.event_type === 'unreachable') unreachableAlerts += 1;
        }
      } catch (error) {
        failed += 1;
        failures.push(`${site.url}: ${error.message}`);
        if (recordSeriousNotification(error.data?.notification)) {
          seriousAlerts += 1;
          if (error.data.notification.event_type === 'recovered') recoveries += 1;
          else if (error.data.notification.event_type === 'unreachable') unreachableAlerts += 1;
        }
      } finally {
        pendingChecks.delete(site.id);
      }
      completed += 1;
      updateCheckAllProgress(completed, total);
      render();
    }

    if (unavailable.length || failures.length) {
      checkAllFailures.hidden = false;
      for (const detail of [...unavailable, ...failures]) {
        const item = document.createElement('li');
        item.textContent = detail;
        checkAllFailures.append(item);
      }
    }
    const failedSummary = `${failed} failed`;
    const unavailableSummary = `${unavailable.length} could not be checked`;
    checkAllStatus.textContent = `Check All complete: ${successful} successful, ${changes} changed websites, ${failedSummary}, ${unreachableAlerts} unreachable alerts, ${recoveries} recoveries, ${unavailableSummary}, ${seriousAlerts} serious alerts.`;
    message.dataset.kind = failed || unavailable.length ? 'error' : (changes ? 'warning' : 'success');
    message.textContent = checkAllStatus.textContent;
  } catch (error) {
    checkAllStatus.textContent = `Check All could not load the website lists: ${error.message}`;
    message.dataset.kind = 'error';
    message.textContent = checkAllStatus.textContent;
  } finally {
    queuedIds.forEach((id) => {
      checkAllWebsiteIds.delete(id);
      pendingChecks.delete(id);
    });
    checkAllRunning = false;
    checkAllButton.disabled = false;
    checkAllButton.textContent = 'Check All';
    render();
    await loadWebsites();
    await loadStats();
  }
}

checkAllButton.addEventListener('click', checkAllWebsites);

async function checkWebsite(id) {
  if (checkAllRunning || pendingChecks.has(id) || pendingRemovals.has(id) || pendingDemoVersions.has(id) || pendingVisibilityChanges.has(id)) return;
  watchlistRevision += 1;
  pendingChecks.add(id);
  render();
  try {
    const updated = await api(`/api/websites/${id}/check`, { method: 'POST', timeoutMs: CHECK_REQUEST_TIMEOUT_MS });
    recordSeriousNotification(updated.notification);
    await Promise.all([loadWebsites(), refreshJobQueue()]);
    message.dataset.kind = 'success';
    message.textContent = `Check complete: ${statusLabel(updated)}.`;
  } catch (error) {
    // The server stores check errors on the website; reload to show that state.
    await Promise.all([loadWebsites(), refreshJobQueue()]);
    recordSeriousNotification(error.data?.notification);
    message.dataset.kind = 'error';
    message.textContent = error.message;
  } finally {
    pendingChecks.delete(id);
    render();
    await Promise.all([loadStats(), refreshJobQueue()]);
  }
}

async function setWebsiteHidden(id, isHidden) {
  if (checkAllRunning || pendingChecks.has(id) || pendingRemovals.has(id) || pendingDemoVersions.has(id) || pendingVisibilityChanges.has(id)) return;
  pendingVisibilityChanges.add(id);
  render();
  try {
    await api(`/api/websites/${id}/hidden`, {
      method: 'PATCH',
      body: JSON.stringify({ is_hidden: isHidden }),
    });
    watchlistRevision += 1;
    await loadWebsites();
    message.dataset.kind = 'success';
    message.textContent = isHidden ? 'Website moved to Hidden Websites.' : 'Website restored to the main dashboard.';
  } catch (error) {
    await loadWebsites();
    message.dataset.kind = 'error';
    message.textContent = error.message;
  } finally {
    pendingVisibilityChanges.delete(id);
    render();
  }
}

async function switchDemoVersion(id) {
  if (checkAllRunning || pendingChecks.has(id) || pendingRemovals.has(id) || pendingDemoVersions.has(id) || pendingVisibilityChanges.has(id)) return;
  pendingDemoVersions.add(id);
  render();
  try {
    watchlistRevision += 1;
    const updated = await api(`/api/websites/${id}/demo-version`, { method: 'POST' });
    websites = websites.map((site) => site.id === id ? updated : site);
    render();
    message.dataset.kind = 'success';
    message.textContent = `Demo fixture is now version ${updated.demo_version}. Check it to compare its content.`;
  } catch (error) {
    message.dataset.kind = 'error';
    message.textContent = error.message;
  } finally {
    pendingDemoVersions.delete(id);
    render();
  }
}

async function removeWebsite(id) {
  if (checkAllRunning || pendingChecks.has(id) || pendingRemovals.has(id) || pendingDemoVersions.has(id) || pendingVisibilityChanges.has(id)) return;
  const site = websites.find((entry) => entry.id === id);
  const name = site?.display_name || (site ? new URL(site.url).hostname : 'this website');
  if (!window.confirm(`Remove ${name} and permanently delete its saved snapshots and history?`)) return;
  pendingRemovals.add(id);
  render();
  try {
    watchlistRevision += 1;
    await api(`/api/websites/${id}`, { method: 'DELETE' });
    await loadWebsites();
    message.textContent = '';
    await loadStats();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    pendingRemovals.delete(id);
    render();
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  delete message.dataset.kind;
  message.textContent = '';
  const url = urlInput.value.trim();
  if (!url) {
    message.textContent = 'Enter a webpage URL.';
    urlInput.focus();
    return;
  }
  const addButton = form.querySelector('.add-button');
  addButton.disabled = true;
  try {
    watchlistRevision += 1;
    const interval = websiteIntervalInput.valueAsNumber;
    if (!Number.isInteger(interval) || interval < 15 || interval > 1440) {
      throw new Error('Choose an interval from 15 to 1440 minutes.');
    }
    await api('/api/websites', { method: 'POST', body: JSON.stringify({
      url,
      display_name: nameInput.value.trim(),
      check_interval_minutes: interval,
    }) });
    await loadWebsites();
    form.reset();
    urlInput.focus();
  } catch (error) {
    await loadWebsites();
    message.textContent = error.message;
    urlInput.focus();
  } finally {
    addButton.disabled = false;
  }
});

function prefillBrowserExtensionLink() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (!params.has('addUrl')) return;
  const candidate = params.get('addUrl') || '';
  history.replaceState(null, '', `${location.pathname}${location.search}`);
  try {
    if (candidate.length > 2048) throw new Error('This URL is too long for WebWatch.');
    const parsed = new URL(candidate);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
      throw new Error('The extension only sends public HTTP or HTTPS pages without embedded credentials.');
    }
    urlInput.value = parsed.href;
    if (!nameInput.value.trim()) nameInput.value = parsed.hostname.replace(/^www\./i, '');
    message.dataset.kind = 'success';
    message.textContent = 'Current tab URL loaded. Review the public URL and click Add website.';
    urlInput.focus();
  } catch (error) {
    message.dataset.kind = 'error';
    message.textContent = error.message || 'The extension link did not contain a valid public page URL.';
  }
}

const focusUrlButton = document.querySelector('#focus-url');
if (focusUrlButton) focusUrlButton.addEventListener('click', () => urlInput.focus());
websiteIntervalInput.addEventListener('input', () => { websiteIntervalTouched = true; });
searchInput.addEventListener('input', () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(loadWebsites, 220);
});
statusFilter.addEventListener('change', loadWebsites);
document.querySelector('#clear-filters').addEventListener('click', () => {
  searchInput.value = '';
  statusFilter.value = 'all';
  render();
  searchInput.focus();
});
document.querySelector('#retry-watchlist').addEventListener('click', loadWebsites);
document.querySelector('#start-demo').addEventListener('click', async () => {
  const button = document.querySelector('#start-demo');
  button.disabled = true;
  try {
    watchlistRevision += 1;
    await api('/api/demo', { method: 'POST' });
    await loadWebsites();
    message.textContent = 'Local demo added. Check it once for a baseline, switch to fixture V2, then check again.';
    await loadStats();
  } catch (error) {
    await loadWebsites();
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

const guestExitButton = document.querySelector('#guest-exit');
if (guestExitButton) guestExitButton.addEventListener('click', async () => {
  guestExitButton.disabled = true;
  try {
    await api('/auth/logout', { method: 'POST' });
    location.assign('/login');
  } catch (error) {
    message.textContent = error.message;
    guestExitButton.disabled = false;
  }
});

applyWebsiteView();
loadWebsites().finally(prefillBrowserExtensionLink);
loadNotificationHistory();
if (!guestMode) {
  loadSchedule(true);
  loadAlertSettings();
}
refreshJobQueue();
window.setInterval(async () => {
  if (!guestMode) await loadSchedule();
  await refreshJobQueue();
  if (!websiteMenuIsOpen()
      && websites.some((site) => ['queued', 'running', 'retry'].includes(site.active_check_job?.status))) {
    await loadWebsites({ background: true });
  }
}, 10000);
