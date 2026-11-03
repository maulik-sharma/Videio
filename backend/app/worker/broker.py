"""RabbitMQ broker topology shared by the API, workers and scheduler."""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from aio_pika import DeliveryMode, Message
from taskiq import SmartRetryMiddleware, TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq_aio_pika import AioPikaBroker, Queue, QueueType

from app.core.config import Settings, get_settings
from app.worker.ffmpeg import RetryableTranscodeError

logger = logging.getLogger(__name__)

DEAD_LETTER_QUEUE = "transcode.dlq"

# Delay before retry 1, 2 and 3 (plus up to 10% jitter). taskiq's `max_retries`
# label counts attempts, not retries, so three retries means four attempts.
RETRY_DELAYS_S = (10, 60, 300)
MAX_ATTEMPTS = len(RETRY_DELAYS_S) + 1

FinalFailureHandler = Callable[[TaskiqMessage, BaseException], Awaitable[None]]


class VideioBroker(AioPikaBroker):
    """AioPika broker with an explicit queue for final task failures."""

    def __init__(self, settings: Settings) -> None:
        self.dead_letter_queue_name = DEAD_LETTER_QUEUE
        self.final_failure_handlers: dict[str, FinalFailureHandler] = {}
        super().__init__(
            url=settings.rabbitmq_url,
            qos=1,
            task_queues=[Queue(name="videio.tasks", type=QueueType.QUORUM)],
            delay_queue=Queue(name="videio.tasks.delay", type=QueueType.QUORUM),
            dead_letter_queue=Queue(name=self.dead_letter_queue_name, type=QueueType.QUORUM),
        )

    def on_final_failure(self, task_name: str, handler: FinalFailureHandler) -> None:
        """Run `handler` in the worker when `task_name` fails for the last time."""
        self.final_failure_handlers[task_name] = handler

    async def publish_to_dead_letter(self, message: TaskiqMessage, error: BaseException) -> None:
        """Publish a failed task without relying on broker-level nack semantics.

        taskiq acknowledges a failed task after its result is saved, so a final
        failure never reaches the queue's dead-letter route by itself. The
        middleware below publishes the original task to the DLQ explicitly.
        """
        if self.write_channel is None:
            return
        broker_message = self.formatter.dumps(message)
        rmq_message = Message(
            body=broker_message.message,
            headers={
                "task_id": message.task_id,
                "task_name": message.task_name,
                "error": str(error)[:1000],
                **message.labels,
            },
            delivery_mode=DeliveryMode.PERSISTENT,
        )
        await self.write_channel.default_exchange.publish(
            rmq_message,
            routing_key=self.dead_letter_queue_name,
        )


class BackoffRetryMiddleware(SmartRetryMiddleware):
    """SmartRetryMiddleware with a fixed schedule: about 10 s, 60 s, then 5 min.

    The stock exponent multiplies the previous delay (carried in the `delay`
    label) by the retry number, which gives 10 s, 21 s, 64 s. A table is easier
    to reason about. The delay itself still goes through the broker's delay queue.
    """

    def make_delay(self, message: TaskiqMessage, retries: int) -> float:
        del message
        base = RETRY_DELAYS_S[min(retries, len(RETRY_DELAYS_S)) - 1]
        return base + random.uniform(0, base * 0.1)  # jitter, not crypto


def _flag(value: Any) -> bool:
    # Labels are strings once a message has been through the broker.
    return value.lower() == "true" if isinstance(value, str) else bool(value)


def is_final_failure(message: TaskiqMessage, exception: BaseException) -> bool:
    """True when the retry middleware will not schedule another attempt.

    Mirrors SmartRetryMiddleware.on_error: only RetryableTranscodeError is
    retried, only for tasks with retry_on_error, and only while attempts remain.
    """
    if not isinstance(exception, RetryableTranscodeError):
        return True
    if not _flag(message.labels.get("retry_on_error", False)):
        return True
    attempt = int(message.labels.get("_retries", 0)) + 1
    return attempt >= int(message.labels.get("max_retries", MAX_ATTEMPTS))


class FinalFailureDeadLetterMiddleware(TaskiqMiddleware):
    """Dead-letter tasks labelled `dead_letter=True` once they finally fail."""

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        del result
        if not _flag(message.labels.get("dead_letter", False)):
            return
        if not is_final_failure(message, exception):
            return

        broker = self.broker
        if not isinstance(broker, VideioBroker):
            return
        handler = broker.final_failure_handlers.get(message.task_name)
        if handler is not None:
            try:
                await handler(message, exception)
            except Exception:
                # Still dead-letter the message; the reaper settles the row later.
                logger.exception("Final-failure handler for %s failed", message.task_name)
        await broker.publish_to_dead_letter(message, exception)


class ResourceCleanupMiddleware(TaskiqMiddleware):
    """Close task-owned database and Redis clients during graceful shutdown."""

    async def shutdown(self) -> None:
        from app.worker.runtime import close_resources

        await close_resources()


def create_broker(settings: Settings | None = None) -> VideioBroker:
    settings = settings or get_settings()
    broker = VideioBroker(settings)
    # Order matters: the retry middleware re-kicks first, then the DLQ
    # middleware handles failures that will not be retried. Resource cleanup
    # runs after the receiver has waited for active tasks.
    broker.with_middlewares(
        BackoffRetryMiddleware(
            default_retry_count=MAX_ATTEMPTS,
            use_jitter=False,  # make_delay adds its own
            types_of_exceptions=(RetryableTranscodeError,),
        ),
        FinalFailureDeadLetterMiddleware(),
        ResourceCleanupMiddleware(),
    )
    return broker


broker = create_broker()
