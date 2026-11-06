"""
Background scheduler: runs the five signal-detection rules periodically
inside the same process — no Redis, no Celery, appropriately sized for
this app's scale (BackgroundScheduler runs jobs in a worker thread, not
the async event loop, which matters since our DB session is synchronous).

New notifications get handed from that worker thread to the main asyncio
event loop via run_coroutine_threadsafe, since the actual WebSocket
broadcast has to happen on the loop that owns the connections.
"""
import asyncio
import logging
from apscheduler.schedulers.background import BackgroundScheduler

from signals import run_signal_checks
from notifications import notification_manager
from settings import get_setting_int

logger = logging.getLogger("procureos.scheduler")

_scheduler = BackgroundScheduler()
_main_loop = None  # set by init(), called from FastAPI's startup event


def _run_checks_job():
    try:
        created = run_signal_checks()
    except Exception:
        logger.exception("Signal check run failed")
        return
    if not created or _main_loop is None:
        return
    for notification in created:
        asyncio.run_coroutine_threadsafe(
            notification_manager.broadcast(notification), _main_loop
        )


def init(loop):
    """Call once from FastAPI's startup event, passing the running event loop."""
    global _main_loop
    _main_loop = loop
    if not _scheduler.running:
        interval = get_setting_int("signal_check_interval_minutes")
        _scheduler.add_job(
            _run_checks_job, "interval",
            minutes=interval, id="signal_checks",
        )
        _scheduler.start()
        # also run once immediately so signals don't sit waiting for the first interval
        _run_checks_job()


def apply_interval_change():
    """Called after the Configuration page changes the check interval —
    APScheduler needs to be explicitly told to reschedule a running job,
    changing the stored setting alone does nothing to the live scheduler."""
    if not _scheduler.running:
        return
    interval = get_setting_int("signal_check_interval_minutes")
    _scheduler.reschedule_job("signal_checks", trigger="interval", minutes=interval)


def shutdown():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
