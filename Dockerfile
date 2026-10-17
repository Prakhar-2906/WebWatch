FROM python:3.14.6-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/app/.playwright-browsers

WORKDIR /app

# Keep dependency layers cacheable across frontend, template, and documentation changes.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

# The installer invokes Playwright's Linux --with-deps flow and verifies the
# headless-shell executable. This runs in the final image, so both its browser
# and shared libraries are present when the container starts.
COPY app.py auth.py async_public_fetch.py ./
COPY tools/install_playwright_browser.py tools/install_playwright_browser.py
RUN python tools/install_playwright_browser.py

COPY . .

# Flask's default SQLite location must be writable when no database override is set.
RUN groupadd --system webwatch \
    && useradd --system --gid webwatch --create-home --home-dir /home/webwatch webwatch \
    && mkdir -p /app/instance \
    && chown -R webwatch:webwatch /app/instance /app/.playwright-browsers

USER webwatch
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 CMD python -c "import json,os,urllib.request; url='http://127.0.0.1:%s/auth/status' % os.getenv('PORT', '3000'); data=json.load(urllib.request.urlopen(url, timeout=3)); raise SystemExit(0 if data.get('authentication_available') else 1)"
CMD ["sh", "-c", "exec python -m gunicorn --config gunicorn.conf.py --bind 0.0.0.0:${PORT:-3000} app:app"]
