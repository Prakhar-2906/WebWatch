# WebWatch

WebWatch is a Flask application for monitoring public webpages for text and availability changes. It includes private user watchlists, temporary guest sessions, rendered-page checks, per-site history, scheduled checks, and optional email/webhook alerts.

## Features

- Account sessions, invite-only registration, owner setup, and isolated guest data.
- HTTP and JavaScript-rendered monitoring with content snapshots, comparisons, screenshots, and optional numeric selectors.
- Opt-in automatic monitoring for registered accounts, with per-site intervals and a persistent SQLite queue for check and alert status/retries. Scheduling starts disabled; guests cannot use scheduled monitoring.
- Email, HTTPS webhook, Telegram, and optional OpenAI-compatible change summaries.
- Browser extension for adding a page from the current browser tab.

## Local setup

From the repository directory in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python tools/install_playwright_browser.py
$env:WEBWATCH_SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:WEBWATCH_OWNER_SETUP_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(32))")
python app.py
```

Open <http://127.0.0.1:5000>, select **Set up owner**, and enter the setup token. Keep both generated values private and stable for the lifetime of the database. The database defaults to `instance/webwatch.sqlite3`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `WEBWATCH_SECRET_KEY` | Required stable Flask signing key; use at least 32 random characters. |
| `WEBWATCH_DATABASE` | Optional SQLite database path. Defaults to `instance/webwatch.sqlite3`. |
| `WEBWATCH_OWNER_SETUP_TOKEN` | One-time first-owner setup token. Remove it after setup. |
| `WEBWATCH_TRUST_PROXY` | Optional `true`/`false` override for trusting one hosting proxy. By default this is enabled when `PORT` is set. |
| `WEBWATCH_GUEST_TTL_SECONDS` | Guest session lifetime, 900–86400 seconds; default is 21600 (6 hours). |
| `WEBWATCH_DEMO_ACCOUNT` | Set to `true` to provision the optional demo account at startup. `WEBWATCH_DEMO_EMAIL` and `WEBWATCH_DEMO_PASSWORD` supply its credentials. |
| `WEBWATCH_SMTP_HOST`, `WEBWATCH_SMTP_PORT`, `WEBWATCH_SMTP_FROM`, `WEBWATCH_SMTP_USER`, `WEBWATCH_SMTP_PASSWORD` | Optional SMTP delivery settings. Port defaults to 587; STARTTLS is used. |
| `WEBWATCH_SUMMARY_API_KEY` | Optional key for AI summaries. `WEBWATCH_SUMMARY_API_URL` and `WEBWATCH_SUMMARY_MODEL` can select an OpenAI-compatible endpoint and model. |

Never place secrets in source control or browser code. The demo credentials are public and should be used only for non-sensitive test data. On Voroa, `/tmp/webwatch.sqlite3` may be ephemeral; the database and demo account can disappear after a restart or redeploy.

## Voroa deployment

Voroa installs `requirements.txt` before its custom build command. Set these in the Voroa dashboard:

**Build command**

```sh
python tools/install_playwright_browser.py
```

**Start command**

```sh
python -m gunicorn --config gunicorn.conf.py --bind 0.0.0.0:$PORT app:app
```

The installer invokes the installed Playwright package with the same Python interpreter used by Gunicorn. It stores Chromium's headless shell in the project-local `.playwright-browsers` directory. Gunicorn verifies the expected executable before starting the scheduler. `render.yaml` contains the separate Render blueprint command, which also installs `requirements.txt`; Voroa does not read that file.

Run one Gunicorn worker. The scheduler and shared-fetch cycle cache are process-local, and SQLite is configured for one application process. The shared-fetch cache reuses a successful compatible result across users due in the same scheduler cycle and coalesces overlapping compatible requests. Each user's snapshots, status, history, and alert handling remain separate. This is not cross-process coordination; horizontal scaling or multiple workers needs a separate shared scheduler/cache design.

Automatic checks run only for registered accounts that enable them in the dashboard; they are off by default, and guests cannot access the schedule controls. The scheduler polls while the web process is running and checks each page when its own interval is due. Webhook settings are per account: the dashboard accepts public HTTPS destinations, keeps saved URLs hidden, and can send a test. Deliveries use an owner-scoped persistent queue with bounded retries. The HTTPS webhook request is DNS validated and pinned to a public IP.

## API overview

The browser UI authenticates and adds CSRF protection to state-changing requests. The API requires the same account session and authorization checks.

| Method and path | Use |
| --- | --- |
| `GET /api/websites` | List the signed-in account's websites. |
| `POST /api/websites` | Add a website with JSON such as `{"url":"https://example.com/"}`. |
| `POST /api/websites/{id}/check` | Start a manual check. |
| `GET /api/websites/{id}/history` | Read recent successful history. |
| `GET /api/jobs` | Read the account's check-job statuses. |
| `GET /api/schedule`, `PUT /api/schedule` | Read or update automatic monitoring. |
| `GET /api/alerts`, `PUT /api/alerts` | Read or update alert settings. |
| `GET /feed/websites/{id}.xml` | Read an account-scoped website feed. |

Other website settings, history pages, comparisons, metrics, screenshots, and exports are exposed under `/api/websites/{id}`. See the Flask routes in `app.py` for the full route list.

## Security and limits

WebWatch accepts HTTP and HTTPS URLs, rejects local and non-public destinations, validates DNS answers, and pins each actual connection to a validated public IP. It disables automatic redirects and validates each redirect destination before following it. Requests have timeouts and streamed size limits. Playwright network requests are routed through the same destination and redirect checks; request count, response size, and render time are bounded. WebWatch does not log in to monitored sites or bypass access controls.

Sessions use server-side random tokens, secure HTTP-only cookies, expiration, and CSRF checks. Keep production behind HTTPS and a trusted reverse proxy. User data is scoped by account in server-side checks; guests cannot access registered-user data. Protect the SQLite file because it contains monitored content and alert configuration.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check static/app.js
node --test tests/frontend_notifications.test.cjs tests/browser_extension.test.cjs
```

Python tests use temporary databases and controlled fetches. The browser extension tests require Node.js; Node is not a server runtime dependency.

## Troubleshooting and limitations

- **Chromium executable missing:** check that the Voroa build command ran successfully. Startup logs report the expected executable path. Rebuild with `python tools/install_playwright_browser.py`; install and runtime must use the same app version and Playwright requirements.
- **Login says the request could not be verified:** reload the sign-in page, confirm HTTPS and trusted forwarded scheme/host headers, and keep CSRF enabled. Set `WEBWATCH_TRUST_PROXY` only according to the hosting proxy setup.
- **Check stays queued or waiting to retry:** inspect `GET /api/jobs` and the server logs. Temporary network or renderer errors use bounded retries; failed attempts update the website's visible latest status.
- **Monitoring pauses:** scheduled checks run inside the web process. They stop when the process sleeps or stops; free hosting may also sleep when idle.
- **History scope:** successful snapshots are retained per website. The latest failure is shown on the website, but failed attempts do not have a separate full historical timeline.
- **Password recovery:** not implemented. Registration is invite-only, and the initial owner is created through the one-time setup flow.
- **Persistent multi-process scheduling/shared fetches:** not implemented. Use the single-worker configuration; multiple instances require external coordination and shared persistent storage.
