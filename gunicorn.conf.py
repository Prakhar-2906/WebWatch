"""Single-process Gunicorn configuration for WebWatch on Render."""

# The database is SQLite and the scheduler is process-local. Keep one worker and
# let it serve concurrent requests with threads so scheduled work is never cloned.
workers = 1
worker_class = "gthread"
threads = 4
preload_app = True
timeout = 90


def post_worker_init(worker):
    """Start background work without blocking worker readiness on Playwright."""
    from app import start_scheduler

    worker.log.info("WebWatch startup: starting scheduler")
    start_scheduler()
    worker.log.info("WebWatch startup: scheduler started; worker initialization complete")


def worker_exit(server, worker):
    """Signal the worker's scheduler to stop during graceful shutdown."""
    from app import stop_scheduler

    stop_scheduler()
