from __future__ import annotations

import logging
from datetime import timedelta
from sqlalchemy import select, update
from app.db.session import AsyncSessionLocal
from app.models.meta_webhook_event import MetaWebhookEvent
from app.services.meta_webhook_service import MetaWebhookService
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)


# Exponential backoff per attempt. After 6 attempts (~6.5h elapsed)
# we give up — by then Meta is also done retrying (36h window).
_BACKOFF_MINUTES = [1, 5, 15, 60, 180, 360]
_MAX_ATTEMPTS = len(_BACKOFF_MINUTES)
_BATCH_SIZE = 25  # process at most N rows per cycle to keep DB load bounded


def _next_attempt_at(attempts: int):
    """Return when to re-try after `attempts` previous failures.

    attempts=1 means we just finished the first attempt → schedule the
    second `_BACKOFF_MINUTES[0]` minutes from now.
    """
    idx = min(max(attempts - 1, 0), len(_BACKOFF_MINUTES) - 1)
    return now_utc() + timedelta(minutes=_BACKOFF_MINUTES[idx])


async def run_meta_retry_cycle():
    """Pick up to _BATCH_SIZE pending meta_webhook_events that are due
    and process each. Marks done/failed/retried based on outcome.

    Idempotent and safe to run on multiple workers — each row is
    locked with SELECT ... FOR UPDATE SKIP LOCKED.
    """
    async with AsyncSessionLocal() as db:
        try:
            # Pull due rows with row-level lock so concurrent workers
            # don't double-process. Skip locked rows so we don't wait.
            rows = (await db.execute(
                select(MetaWebhookEvent)
                .where(
                    MetaWebhookEvent.status == "pending",
                    MetaWebhookEvent.next_attempt_at <= now_utc(),
                )
                .order_by(MetaWebhookEvent.next_attempt_at.asc())
                .limit(_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )).scalars().all()

            if not rows:
                return

            # Flip to 'processing' so a concurrent cycle ignores them
            ids = [r.id for r in rows]
            await db.execute(
                update(MetaWebhookEvent)
                .where(MetaWebhookEvent.id.in_(ids))
                .values(status="processing", last_attempt_at=now_utc())
            )
            await db.commit()

            logger.info("Meta retry: picked %d due events", len(rows))

            # Process each event in its own transaction so one failure
            # doesn't roll back the others
            for event in rows:
                await _process_one(event.id)
        except Exception:
            logger.exception("Meta retry: cycle failed")
            await db.rollback()


async def _process_one(event_id):
    """Run the routing+ingest pipeline for one event. Updates the row's
    status to 'done' / 'failed' / 'pending' (with next_attempt_at bumped).
    """
    async with AsyncSessionLocal() as db:
        event = (await db.execute(
            select(MetaWebhookEvent).where(MetaWebhookEvent.id == event_id)
        )).scalar_one_or_none()
        if not event:
            return

        try:
            svc = MetaWebhookService(db)
            await svc.process_leadgen_event(
                leadgen_id=event.leadgen_id,
                form_id=event.form_id,
                page_id=event.page_id,
                raw_change=event.raw_payload.get("change", {}),
            )
            # Success: mark done. attempts++ so the row history is honest.
            await db.execute(
                update(MetaWebhookEvent)
                .where(MetaWebhookEvent.id == event_id)
                .values(
                    status="done",
                    attempts=MetaWebhookEvent.attempts + 1,
                    last_attempt_at=now_utc(),
                    last_error=None,
                )
            )
            await db.commit()
            logger.info("Meta retry: event %s done (leadgen=%s)", event_id, event.leadgen_id)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:500]}"
            new_attempts = (event.attempts or 0) + 1
            if new_attempts >= _MAX_ATTEMPTS:
                new_status = "failed"
                logger.error("Meta retry: event %s FAILED after %d attempts — %s",
                             event_id, new_attempts, err)
            else:
                new_status = "pending"
                logger.warning("Meta retry: event %s will retry (attempt %d/%d) — %s",
                               event_id, new_attempts, _MAX_ATTEMPTS, err)
            await db.execute(
                update(MetaWebhookEvent)
                .where(MetaWebhookEvent.id == event_id)
                .values(
                    status=new_status,
                    attempts=new_attempts,
                    last_attempt_at=now_utc(),
                    last_error=err,
                    next_attempt_at=_next_attempt_at(new_attempts),
                )
            )
            await db.commit()
