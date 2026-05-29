import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from app.db.session import AsyncSessionLocal
from app.models.task import Task
from app.models.notification import Notification
from app.core.constants import TaskStatus, NotificationType
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def check_overdue_tasks():
    """Flag overdue tasks and send notifications. Runs every 15 minutes."""
    async with AsyncSessionLocal() as db:
        try:
            now = now_utc()
            result = await db.execute(
                select(Task).where(
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                    Task.due_date < now,
                )
            )
            overdue_tasks = result.scalars().all()

            for task in overdue_tasks:
                if task.status != TaskStatus.OVERDUE:
                    task.status = TaskStatus.OVERDUE

                    notif = Notification(
                        user_id=task.assigned_to,
                        company_id=task.company_id,
                        type=NotificationType.TASK_OVERDUE,
                        title="Task Overdue",
                        message=f"Task '{task.title}' is overdue.",
                        lead_id=task.lead_id,
                        task_id=task.id,
                    )
                    db.add(notif)

            await db.commit()
            if overdue_tasks:
                logger.info("Flagged %d overdue tasks", len(overdue_tasks))
        except Exception:
            logger.exception("Error in overdue task checker")
            await db.rollback()


async def daily_task_rollover():
    """Daily midnight job — currently a placeholder for future rollover logic."""
    logger.info("Daily task rollover executed")


async def cleanup_voice_call_state():
    """Remove orphaned voice call states older than 30 minutes."""
    try:
        from app.services.voice_engine.call_state import call_state_manager
        removed = call_state_manager.cleanup_stale(max_age_minutes=30)
        if removed:
            logger.info("Cleaned up %d orphaned voice call states", removed)
    except Exception as e:
        logger.warning("voice call cleanup failed: %s", e)


async def run_campaign_worker():
    """Process active campaigns — dispatch calls to pending leads."""
    try:
        from app.workers.campaign_worker import campaign_worker
        await campaign_worker.run_cycle()
    except Exception as e:
        logger.error("Campaign worker error: %s", e)


async def run_meta_retry_worker():
    """Process queued Meta webhook events — routes leads to FMC/AV,
    retries failures with exponential backoff. Idempotent across workers.
    """
    try:
        from app.workers.meta_retry import run_meta_retry_cycle
        await run_meta_retry_cycle()
    except Exception as e:
        logger.error("Meta retry worker error: %s", e)


def start_scheduler():
    scheduler.add_job(check_overdue_tasks, "interval", minutes=15, id="overdue_checker", replace_existing=True)
    scheduler.add_job(daily_task_rollover, "cron", hour=0, minute=0, id="daily_rollover", replace_existing=True)
    scheduler.add_job(cleanup_voice_call_state, "interval", minutes=10, id="voice_call_cleanup", replace_existing=True)
    scheduler.add_job(run_campaign_worker, "interval", seconds=30, id="campaign_worker", max_instances=1, replace_existing=True)
    scheduler.add_job(run_meta_retry_worker, "interval", seconds=20, id="meta_retry_worker", max_instances=1, replace_existing=True)
    scheduler.start()
    logger.info("Background scheduler started (campaign 30s, meta retry 20s)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
