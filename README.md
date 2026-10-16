# WebWatch

WebWatch is a small Flask application for keeping a record of public webpage text and noticing when it changes. It is designed as a beginner-friendly engineering project: the user adds a webpage, saves its first successful check as a baseline, and can check sites manually or enable automatic checks.

## Features

- Add and remove websites, with duplicate detection and SQLite persistence.
- Give each website a display name and its own 15-minute to 24-hour automatic-check interval. The existing schedule interval remains the default for new websites.
- Run a manual check, save the first successful response as a baseline, and compare later readable text against the last successful snapshot.
- Reject high-confidence HTTP 200 bot challenges and short server-error templates so they cannot replace a good snapshot or appear as content changes.
- Normalize Unicode composition and whitespace while retaining capitalization, words, and punctuation.
- Review paginated successful snapshot history, filter to content changes, inspect added and removed text, and open a printable HTML report or download JSON.
- Select any two saved versions from History to compare their text and inspect the earlier and later screenshots side by side (stacked on mobile).
- Capture a bounded rendered viewport screenshot for each distinct content version; screenshots are optional artifacts and unchanged checks do not create duplicate versions.
- Preview a public page through the protected browser path and choose semantic CSS-addressable regions to watch or ignore.
- Set per-website history retention in **More → Settings**: defaults are 200 no-change records, 400 content-change records, and 600 serious availability events.
- Use the compact navigation for Watchlist, Serious events, Automatic monitoring, and the in-app Guide.
- Search the watchlist by name or URL and filter it by check status.
- Use the bundled local V1/V2 demo to exercise the same extraction, snapshot, and comparison path without contacting an external website.
- Use the optional WebWatch Chromium extension to open the dashboard for managing monitored pages or prefill Add website from a selected public browser tab.
- Switch between light and dark themes. The selected theme is stored in browser local storage.
- View database-backed totals for real monitored websites. Local demo websites and their checks are labelled separately and excluded from those totals.
- Hide websites from the main dashboard without removing their records, snapshots, or eligibility for checks. Switch to Hidden Websites in place from the dashboard header and restore them there.
- Run Check All to check every saved real website, including hidden websites. Local demo fixtures are excluded; results use the same single-website check and snapshot comparison path.
- Optionally run automatic checks on a configurable interval. The default is disabled with a 60-minute interval; hidden real websites are included and local demo websites are excluded.
- See a clear changed-site count after Check All. Ordinary content changes remain in the watchlist and do not create a toast or browser notification.
- Review persistent serious-event history for suspected availability loss and observed recovery. Browser notifications are optional and permission is requested only after clicking the enable button.
- Limit content-change alerts to one per website per hour while retaining every meaningful version in History and the daily digest.

## Technology and architecture

- **Python and Flask** serve the dashboard and JSON API.
- **SQLite** stores website rows, successful text and optional screenshot snapshots, per-site region selectors, availability state, persistent notification history, and automatic-monitoring settings. `instance/webwatch.sqlite3` is the default; `WEBWATCH_DATABASE` can select a different file, and its parent directory is created at startup. Additive migrations preserve website IDs and snapshot text/timestamps. Backups are not created automatically.
- **Playwright** renders public pages in headless Chromium so JavaScript-generated text can be monitored. Every document and allowed subresource is fetched by WebWatch's DNS-validated, IP-pinned Requests client; Chromium does not connect directly. By default, each resource is limited to 512 KiB and a render to 8 MiB total, 100 requests, and 35 seconds. More → Settings can raise only a website's main HTML document limit to at most 1 MiB; subresources keep the 512 KiB cap and all page-wide limits remain. WebSockets, downloads, and media remain blocked; images and fonts are allowed only through the same pinned, byte-limited fetch path.
- Playwright navigation timeouts emit a bounded server-log trace with hostnames, resource types, roles, and elapsed times only; URL paths, queries, credentials, and page content are omitted. The trace diagnoses stalled rendering without changing security or resource limits.
- **Requests** follows redirects manually after validating every destination, with DNS/IP checks and bounded connection/read timeouts. Direct HTML fetches retain the 2 MB response limit; browser-rendered resources have tighter individual and aggregate limits.
- **BeautifulSoup and lxml** remove non-readable HTML elements and extract page text.
- **Vanilla JavaScript and CSS** implement the dashboard, theme switch, filtering, Check All progress, serious-event history, and toast display without a frontend framework.
- **pytest** tests the comparison, persistence, routes, security-sensitive behavior, and local demo on isolated temporary databases.

## Private accounts (Stages 1 and 2)

Authentication provides invite-only account creation, Argon2id password hashes, opaque server-side sessions, CSRF protection, login throttling, and explicit first-owner setup. Dashboard/API reads and mutations, exports, feeds, settings, alerts, and worker queues are scoped to the signed-in account. Keep the app private until you have configured HTTPS, persistent hosting, backups, and single-process scheduler operation.

Authentication requires a stable `WEBWATCH_SECRET_KEY` of at least 32 characters. Initial owner setup additionally requires a temporary `WEBWATCH_OWNER_SETUP_TOKEN` of at least 32 characters. Supply both through the process environment or a secrets manager; do not commit them or place them in logs. The setup token is accepted only while no account exists, and setup claims the existing unowned websites and their associated history/settings for that first owner. The token is not stored in the database. After setup, clear it from the environment. Password recovery is unavailable until a secure delivery mechanism is configured.

The authentication API routes are `/auth/status`, `/auth/csrf`, `/auth/setup`, `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/invitations`, and `/auth/redeem`. `/auth/csrf` sets a short-lived cookie; browser mutations echo it in the `X-CSRF-Token` header. Owner setup also requires the one-time environment token in `X-WebWatch-Owner-Setup`. Invitations are email-bound, expire after seven days, and can be redeemed once; the code is delivered by configured SMTP rather than returned by the API. Password recovery is unavailable.

### Project structure

```text
webwatch/
├── app.py                       # Flask routes, SQLite helpers, URL checks, fetch/extract/compare and event logic
├── auth.py                      # Versioned auth schema and invite/session/CSRF endpoints
├── requirements.txt             # Runtime dependencies
├── requirements-dev.txt         # Runtime dependencies plus pytest
├── README.md
├── templates/
│   ├── index.html               # Flask dashboard template
│   └── history_export.html      # Printable history report
├── static/
│   ├── app.js                   # Theme, API calls, watchlist, Check All and notification rendering
│   └── styles.css               # Responsive light/dark styles
├── browser-extension/           # Optional Manifest V3 Chromium Quick Add extension
│   ├── manifest.json
│   ├── popup.html
│   ├── popup.js
│   └── popup.css
├── test-page/
│   ├── page.html                # Local demo baseline fixture
│   └── page-changed.html        # Local demo changed fixture
└── tests/
    ├── test_auth.py
    ├── test_snapshot_comparison.py
    ├── test_scheduler.py
    ├── test_frontend_notifications.py
    ├── frontend_notifications.test.cjs
    └── browser_extension.test.cjs
```

The local SQLite file and Python virtual environment are runtime/developer data and are intentionally excluded from Git. Do not delete the SQLite file to apply updates; startup migrations are additive.

## Windows PowerShell setup

Open PowerShell in the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

If PowerShell blocks script activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell window, then activate the environment.

## Run WebWatch

```powershell
python app.py
```

Open <http://127.0.0.1:5000>. Running `python app.py` starts one scheduler thread with Flask's code reloader disabled; `flask --app app run` also starts the same guarded thread when its first request arrives. Stop the server and scheduler with **Ctrl+C**. Scheduled work runs only while this process is running; it stops when the terminal closes or the computer sleeps/shuts down. After changing Python or template files, stop and start the app again. Use **Ctrl+F5** after frontend changes to request fresh dashboard files.

## Run tests

Install development/test dependencies separately from the runtime requirements:

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest -q
node --check static/app.js
node --test tests/frontend_notifications.test.cjs tests/browser_extension.test.cjs
```

The pytest suite uses temporary SQLite databases and substitutes network fetches with controlled test responses. It does not edit the local `instance/webwatch.sqlite3` database. Node is used for JavaScript syntax and browser-extension/UI tests; it is not a runtime dependency of WebWatch.

## Browser extension

The optional Chromium extension is a local convenience, not a second monitoring service. Follow [browser-extension/README.md](browser-extension/README.md) to load it unpacked. **Manage monitored pages** opens the dashboard without reading the active tab URL. Quick Add reads only the selected tab URL after the popup is opened, then opens the local dashboard with a reviewable URL prefilled; the user still submits the Add website form. WebWatch performs its normal URL and SSRF checks.

## Manual use and demo

1. Add a public HTTP or HTTPS webpage, give it a display name, and choose an interval from 15 to 1,440 minutes. Adding it saves settings but does not fetch the page. Use each card's Settings action to edit a name or interval.
2. Press **Check now**. Its first successful response becomes a baseline; WebWatch does not report a change until a later successful check can be compared with it.
3. Run another check to see whether readable text changed. Open **History** to review saved content versions. Select two versions to see their text diff, saved screenshots, and pixel differences when both screenshots exist. Unchanged checks do not create duplicate versions. Failed attempts do not create snapshots or replace the last successful baseline.
4. Open **More → Choose watched regions** to preview a page and set semantic regions to Watch or Ignore. Removing all Watch rules returns monitoring to the whole page (minus ignored regions); **Reset selectors** clears all region rules. Stale selectors appear with a warning and can be removed or replaced from the preview.
5. Search and filter the watchlist. History offers All and Content changes filters plus Load older checks. Historical failed attempts are not stored, so there is no historical Errors filter; the card shows the latest failure and its category. HTML report opens an escaped printable report that can be saved as PDF from the browser Print dialog. JSON export remains available.
6. For a repeatable demonstration, press **Start local demo**, check the V1 baseline, switch to fixture V2, then check again. The demo reads only the bundled HTML fixtures; it does not make an external request. Demo checks appear in the demo's own history but are excluded from the real-site totals.
7. Use **Check All** to check visible and hidden real websites. The progress panel reports each completed check, and its final summary separates changed sites, failed checks, recoveries, and serious events. Demo fixtures are excluded.
8. The **Serious event history** panel records availability transitions. A DNS or connection failure after a successful check is labelled a *suspected availability issue*; a successful page check after that is an *observed recovery*. Repeated failures during the same outage do not create more records. Click **Enable browser notifications** to opt in; WebWatch asks the browser for permission only from that button. Ordinary content changes never produce toast or browser notifications.
9. In **Automatic monitoring**, choose an interval in minutes (1 to 10,080), select **Enable automatic checks**, and save. The dashboard displays enabled state, the next run, and the last completed run summary; it refreshes website rows after a scheduled run completes. Turn off the checkbox and save to disable it. The scheduler checks real websites, including hidden ones, through the normal check path; local demo sites are excluded. Sites are checked sequentially, and an individual failure does not stop later checks. A transient read timeout gets one delayed retry; blocked responses and other failures do not. Partial failures appear in the run summary and are logged in the server console.

## Hidden Websites

Use the eye button in the dashboard header to switch to **Hidden Websites** without changing the browser URL. Choose **Hide** on a website row to move that existing record out of the main list; use **Unhide** in the hidden view to restore it. Hiding does not delete its URL, snapshots, history, demo status, or monitoring state. Hidden websites can still be checked and remain included in real-site summary totals.

The migration adds `websites.is_hidden` with a default of `0`. Existing rows marked `visibility='private'` are marked hidden once, and their existing IDs and snapshots remain unchanged. Legacy private-watchlist tables are left untouched; they are no longer used by the application. The legacy `visibility` column, if present, is left in place but is no longer used by the application.

The notification migration adds `websites.availability_state` and creates `notifications`. Existing websites with snapshots are marked last-known reachable; sites without snapshots remain unknown. Notification rows copy the website name and URL so event history remains understandable if a website is later removed. No website rows or snapshots are rebuilt or deleted.

The scheduling migration creates `app_settings` if missing and inserts defaults only for missing keys: disabled, a 60-minute interval, empty timestamps, and an initial run status/summary. It does not modify website, snapshot, or notification records. No new Python dependency is needed; scheduling uses Python's standard threading and SQLite libraries.

The website-settings migration adds `websites.display_name` and `websites.check_interval_minutes` with a 60-minute default. Existing blank names are filled from their URL hostname. Website IDs, snapshots, and serious-event records are preserved. Scheduled work selects each real site when its own interval has elapsed; unchecked sites are due on the next poll. Hidden sites remain eligible and demos remain excluded. The automatic-monitoring interval remains the default applied to new sites, clamped to the supported 15-minute to 24-hour range.

The rendering-settings migration adds `websites.max_document_size_kib` with a 512 KiB default. A site can raise only its main HTML document limit to at most 1024 KiB under More → Settings. Subresources remain limited to 512 KiB each; the aggregate 8 MiB, 100-request, and 35-second render limits remain fixed. Existing websites and snapshots are preserved.

The history-retention migration adds per-website limits with defaults of 200 unchanged checks, 400 content changes, and 600 serious availability events. It adds a `snapshots.record_type` field and classifies existing snapshots in order without rebuilding them. Retention runs independently per website and category during initialization, after new content versions, availability events, and settings saves; the baseline and latest valid snapshot are always preserved. New unchanged checks do not create duplicate content snapshots. The additive successful-check counter keeps dashboard totals accurate while preserving existing totals by backfilling from current snapshots. Settings accept positive whole-number limits from 1 to 10,000, and invalid values do not change settings or delete history. Ordinary content changes are snapshot-history records; serious events are separate rows in `notifications`.

The change-sensitivity setting is available under each website's **More → Settings** menu. **Normal** ignores explicitly labelled display counters (such as “12 views”) and volatile request/security token values; **Low** also ignores relative-time labels such as “4 minutes ago”; **High** counts those text differences. All levels continue to ignore whitespace runs and Unicode composition differences, and retain case and punctuation. Snapshots keep the full extracted text and a SHA-256 digest of the normalized comparison text, tagged with sensitivity and normalization version so old hashes are refreshed safely when needed. Their saved text and timestamps are not rewritten. Changing sensitivity can change how prior versions appear in comparison details, but does not alter or delete saved text. Screenshots are viewport captures compressed as JPEG and capped at 512 KB each; a failed or oversized screenshot leaves the text check valid and shows a dashboard warning and missing-image note. Screenshots are not available for versions saved before this migration or for the bundled local demo.

The in-app Guide summarizes the add/check/history/export workflow and the limits of rendered text monitoring. History paging supports All and Content changes. The current schema stores the latest failure on each website row but does not retain a historical sequence of failed attempts, so an Errors history filter is deliberately omitted. Serious event history continues to show the three newest events by default and preserves its existing read state.
## Security and limitations

- Only HTTP and HTTPS URLs are accepted. Localhost, local names, private, loopback, link-local, and other non-global IP addresses are blocked. DNS answers are checked and the request connection is pinned to the validated public address.
- Redirect following is disabled in Requests. WebWatch validates each redirect destination before making another request. Fetches have connect/read timeouts and a 2 MB streamed response cap.
- Requests do not use environment proxy settings, which prevents a configured proxy from bypassing the destination checks.
- Mutating JSON API calls require a same-origin request marker and reject a foreign `Origin`; the browser client adds the marker automatically. Hidden Websites is a visibility convenience, not an access-control or privacy feature: it does not encrypt data or restrict access to the SQLite file.
- WebWatch does not log in, bypass paywalls, or evade access controls. JavaScript runs only inside the routed Playwright browser; each request still passes the public-IP, redirect, request-method, size, and timeout checks. Some pages may block requests or disallow automated access. Only monitor pages you are permitted to access.
- A remote HTTP 401/403 is recorded as a blocked check with the status code; WebWatch does not retry with alternate identities or attempt to bypass the refusal. The per-website main-document size setting is bounded to 512–1024 KiB; the aggregate 8 MiB, 100-request, per-subresource 512 KiB, and 35-second render limits remain in force.
- The latest attempt distinguishes network errors, timeouts, HTTP errors, rate limits, blocked responses, and detected CAPTCHA/challenge pages. High-confidence HTTP 200 challenge and short server-error templates preserve the last good snapshot. Detection is heuristic and not exhaustive.
- Change detection compares extracted text, not layout or semantic importance. Case and punctuation are meaningful; whitespace runs and Unicode composition differences are ignored. Per-site Low/Normal/High sensitivity can suppress only the documented display noise; dynamic page regions without clear counter, token, or relative-time labels may still cause changes.
- History shows the actual added and removed text. It may mark changed excerpts containing relative-time phrases or score-shaped values as possibly volatile; these are still retained, compared, and counted as ordinary content changes. Numbers are not globally discarded, and these hints do not create serious notifications.
- The history modal loads bounded pages of successful checks; JSON export keeps its existing recent-history output. Successful snapshots remain in SQLite within each website's configured unchanged/change limits, while its baseline and latest valid snapshot are kept. A failed check updates the visible last-attempt category/error but not the successful snapshot history. Pixel comparison flags rendered pixel differences, not semantic visual changes.
- Scheduled checks run in this local app process, not in a cloud service. They do not run while WebWatch is stopped, the computer sleeps, or the machine is shut down; each website becomes due according to its own last-attempt time and interval. Durable `check_jobs` rows record queued/running/retry/succeeded/failed states, attempt counts, errors, and retry deadlines. A running job is returned to queued after restart, and a partial unique index prevents more than one active job per website. Retries use bounded exponential backoff; same-host checks reserve a persistent two-second request slot. The scheduler and manual Check now route use this same job and check path. Browser notifications require explicit user opt-in. Connection failures after a successful check are reported as suspected availability issues, not proof that a site is down. HTTP errors, read timeouts, content changes, redirect rejections, malware, and compromise are not classified as security incidents. Keep the Flask development server local; do not expose it directly to the public internet.

### Email and webhook alerts

Use the dashboard's **Email and webhook alerts** section to enable destinations, save an email recipient, configure an HTTPS webhook, and send a test. SMTP credentials stay in server environment variables; set `WEBWATCH_SMTP_HOST`, optional `WEBWATCH_SMTP_PORT` (defaults to 587), `WEBWATCH_SMTP_FROM`, optional `WEBWATCH_SMTP_USER` and `WEBWATCH_SMTP_PASSWORD`. The email recipient is saved in the dashboard per account. The SMTP connection uses STARTTLS. For a PowerShell session, set values with `$env:WEBWATCH_SMTP_HOST = 'smtp.example.com'` etc. before starting Flask; do not put passwords in source files.

Webhook URLs and optional Telegram `chat_id` are stored in the local SQLite `app_settings` table. The URL is shown only as configured/not configured after save. WebWatch accepts public HTTPS webhook endpoints, pins delivery to the validated public address, disables redirects, and uses short timeouts. Generic payloads include Discord's `content` field plus structured event fields; Telegram Bot API URLs receive `chat_id` and `text`. Alert jobs are unique by event and destination, queued durably, retried a bounded number of times, and expose delivery failures in server logs. Changes and serious availability transitions can alert; routine unchanged checks, blocked pages, CAPTCHA, and failed checks do not. Keep the SQLite file private because it contains the webhook URL. When SMTP is not configured, the UI explains that email delivery is unavailable and no email delivery job is queued.

The alert panel also supports a once-per-UTC-day digest through the same configured delivery destinations. It summarizes meaningful versions and serious availability events from the previous 24 hours. To limit repeated alerts from volatile pages, WebWatch queues at most one content-change alert per website in any 60-minute window; every meaningful snapshot remains in History and can appear in the daily digest. A website's **More → Settings** can further restrict normal change alerts to case-insensitive keywords (one per line); snapshots and History still record every meaningful change. Numeric CSS selectors saved there are sampled after each successful rendered check. **Show values** draws their local history chart; failed/missing selectors do not stop text checks. Each website also exposes an RSS feed under **More** with its latest meaningful content versions. Feeds and metric history use local API URLs and preserve website-specific settings.

Optional one-line AI summaries are disabled by default. To enable them, set `WEBWATCH_SUMMARY_API_KEY` in the server environment, optionally set `WEBWATCH_SUMMARY_API_URL` to an OpenAI-compatible HTTPS chat-completions endpoint and `WEBWATCH_SUMMARY_MODEL`, then enable the option in the alert panel. Only bounded added/removed text excerpts are sent, using a pinned public address, no redirects, and a short timeout; missing configuration, invalid endpoints, or provider errors leave the summary blank and do not fail monitoring. Never put the key in browser code or source control.

The interval settings fix cancels stale queued/retry scheduled jobs when the saved per-website interval changes; manual jobs remain intact. Scheduled queue rows no longer force a website due ahead of its own `last_checked_at + check_interval_minutes`. The dashboard's next-check display is calculated from the same saved value. The additive monitoring migration adds optional keyword and numeric-selector fields, numeric observation history, AI/digest settings, and grouped connectivity incident records; existing website and snapshot IDs and data remain in place. Failures first observed close together are grouped as a likely shared connectivity interruption after three websites participate; the group generates one recovery alert when connectivity returns, while isolated failures retain individual recovery alerts. This is a noise reduction heuristic, not proof that the computer's network caused an outage.

The queue, alert, and authentication tables are additive or transactional migrations. Existing websites, snapshots, serious-event records, IDs, and saved check settings remain in place. URL uniqueness is per owner. Scheduled checks keep a per-run render cache keyed by normalized URL and identical region selectors; where equivalent rows belong to the same owner's watchlist, the rendered result is fetched once and each website still receives its own history/version records. Daily digests are queued separately for each account once per UTC calendar day when an enabled email or webhook destination is configured.
# Authentication and private accounts (Stage 2)

WebWatch now requires a server-side account session for the dashboard, APIs, feeds, exports, and monitoring actions. Set `WEBWATCH_SECRET_KEY` to a stable random value of at least 32 characters and `WEBWATCH_OWNER_SETUP_TOKEN` to a separately generated random setup token before first use. Do not commit either value. The initial owner enters the setup token on `/setup`; setup closes permanently after the first account is created. That explicit owner setup claims the existing unowned websites and their associated history, notifications, jobs, incidents, and settings in one database transaction.

Registration is invite-only. An owner creates an invitation from Account; WebWatch sends the email-bound, single-use invitation code through configured SMTP. Invitation creation is unavailable until `WEBWATCH_SMTP_HOST` and `WEBWATCH_SMTP_FROM` (or `WEBWATCH_SMTP_USER`) are configured. The code is not returned by an API or placed in a URL. Password recovery is unavailable. Login sessions use hashed server-side random tokens, HttpOnly Secure cookies, expiration, login rotation, logout invalidation, CSRF tokens, and bounded login throttling.

All dashboard resources are scoped to the authenticated account, including sites, snapshots, metrics, feeds, exports, notifications, settings, check jobs, alert jobs, and background scheduling. Alert delivery uses each account's saved recipient and webhook configuration. Global domain request throttling remains shared across accounts. WebWatch requires HTTPS for Secure session cookies in real browser use; keep it on a private trusted origin, use persistent storage, and back up SQLite with its WAL state handled consistently before deployment. Multi-process hosting still requires a separately designed shared scheduler/worker coordination layer and is not enabled by this change.

The schema-v2 migration only adds an owner reference to grouped connectivity incidents and its index. To recover from an interrupted migration, keep WebWatch stopped and use SQLite's backup API to restore the verified pre-migration backup into a **new** database file; run `PRAGMA integrity_check` and compare its recorded counts and IDs before selecting that file in configuration. Do not copy only the main `.sqlite3` file while a WAL is active, and do not overwrite the original database or backup during recovery.

## Render free test service (ephemeral)

`render.yaml` defines a **free temporary test service**. It uses Gunicorn with one worker and four request threads; `gunicorn.conf.py` starts the process-local scheduler in that sole worker. Keep the worker count at one. Do not run a second scheduler process or scale this SQLite-backed service horizontally. The app trusts one reverse proxy only when `WEBWATCH_TRUST_PROXY=true`; use this only behind Render's proxy.

The local database path is unchanged by default (`instance/webwatch.sqlite3`). Set `WEBWATCH_DATABASE` to select another SQLite file; the Render test config uses `/tmp/webwatch.sqlite3`. Free Render filesystems are ephemeral, so its test database and test account may disappear after a restart or deploy. **Do not expect it to contain your local websites/history**, and do not point the service at a copy you have not verified. This configuration does not create backups. Continuous scheduled monitoring is not guaranteed: free web services can sleep when idle, so the in-process scheduler pauses with the service. Render documents that free services spin down after 15 minutes of inactivity and that persistent disks are not available on the free plan. [Free service limits](https://render.com/docs/free), [persistent disk limits](https://render.com/docs/disks).

### Optional demo account

The sign-in page displays the demo credentials `test123@gmail.com` / `test000`. To create that account in the service's actual SQLite database, configure these environment variables in the hosting service and restart/redeploy it:

```text
WEBWATCH_DEMO_ACCOUNT=true
WEBWATCH_DEMO_EMAIL=test123@gmail.com
WEBWATCH_DEMO_PASSWORD=test000
```

Provisioning runs during database initialization and uses the same path as `WEBWATCH_DATABASE`. It hashes the password with the existing Argon2id hasher, creates a `member` account, and does nothing if that normalized email already exists; it does not reset an existing password. It is an environment-controlled startup operation, not a public account-creation endpoint. The visible demo password is intentionally public, so do not use this account for private monitoring data. Its pages and history remain scoped to its own account.

For a new database, set `WEBWATCH_SECRET_KEY` as usual. `WEBWATCH_OWNER_SETUP_TOKEN` is still needed only if you also want to create the separate first owner through `/setup`; set it to a private random value of at least 32 characters and remove it after owner setup. The demo member does not consume the owner setup token, claim legacy unowned data, or gain owner privileges. If a database already has an owner, `/setup` remains closed as before.

The current Voroa setting `WEBWATCH_DATABASE=/tmp/webwatch.sqlite3` selects the database the provisioner will use, but `/tmp` may be ephemeral. The demo account and other database contents can disappear after restart or redeployment if Voroa does not preserve that path. A persistent database location would improve durability, but changing the path requires planning a safe migration of any existing data; this change does not alter it.

To create a temporary test service from Render's dashboard, select the Python runtime and use the build and start commands in `render.yaml`. The build installs the pinned Python packages and the matching Playwright Chromium build plus its Linux system dependencies. Render supplies `PORT`; Gunicorn binds to it. Set a fresh `WEBWATCH_SECRET_KEY` in Render's environment. Because the free service starts with a separate empty database, set a temporary `WEBWATCH_OWNER_SETUP_TOKEN` for its first owner setup, then remove that token after setup. Configure SMTP environment variables only if invitation or email delivery is being tested. Keep secrets in Render's environment settings, never in Git. Automatic deployment is disabled in the blueprint. Do not use a free test service as the durable home of WebWatch data.
