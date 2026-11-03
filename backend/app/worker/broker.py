"""RabbitMQ broker topology shared by the API, workers and scheduler."""

from __future__ import annotations

from typing import Any

from aio_pika import DeliveryMode, Message
from taskiq import SmartRetryMiddleware, TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq_aio_pika import AioPikaBroker, Queue, QueueType

from app.core.config import Settings, get_settings
from app.worker.ffmpeg import PermanentTranscodeError, RetryableTranscodeError


class VideioBroker(AioPikaBroker):
    """AioPika broker with an explicit queue for final task failures."""

    def __init__(self, settings: Settings) -> None:
        self.dead_letter_queue_name = "transcode.dlq"
        super().__init__(
            url=settings.rabbitmq_url,
            qos=1,
            task_queues=[Queue(name="videio.tasks", type=QueueType.QUORUM)],
            delay_queue=Queue(name="videio.tasks.delay", type=QueueType.QUORUM),
            dead_letter_queue=Queue(name=self.dead_letter_queue_name, type=QueueType.QUORUM),
        )

    async def publish_to_dead_letter(self, message: TaskiqMessage, error: BaseException) -> None:
        """Publish a failed task without relying on broker-level nack semantics.

        taskiq acknowledges a failed task after its result is saved. The installed
        AioPikaBroker therefore cannot route a final SmartRetry failure to its DLQ
        automatically, so the middleware publishes the original task explicitly.
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


class FinalFailureDeadLetterMiddleware(TaskiqMiddleware):
    """Send permanent and exhausted retry failures to the configured DLQ."""

    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        del result
        retry_on_error = message.labels.get("retry_on_error", False)
        if isinstance(retry_on_error, str):
            retry_on_error = retry_on_error.lower() == "true"

        retries = int(message.labels.get("_retries", 0)) + 1
        max_retries = int(message.labels.get("max_retries", 3))
        retry_scheduled = (
            not isinstance(exception, PermanentTranscodeError)
            and bool(retry_on_error)
            and retries < max_retries
        )
        if retry_scheduled:
            return

        broker = self.broker
        if isinstance(broker, VideioBroker):
            await broker.publish_to_dead_letter(message, exception)


class ResourceCleanupMiddleware(TaskiqMiddleware):
    """Close task-owned database and Redis clients during graceful shutdown."""

    async def shutdown(self) -> None:
        from app.worker.runtime import close_resources

        await close_resources()


def create_broker(settings: Settings | None = None) -> VideioBroker:
    settings = settings or get_settings()
    broker = VideioBroker(settings)
    # The DLQ middleware observes only exhausted failures; resource cleanup runs
    # after the receiver has waited for active tasks.
    broker.with_middlewares(
        SmartRetryMiddleware(
            default_retry_count=3,
            default_delay=10,
            use_jitter=True,
            use_delay_exponent=True,
            max_delay_exponent=300,
            types_of_exceptions=(RetryableTranscodeError,),
        ),
        FinalFailureDeadLetterMiddleware(),
        ResourceCleanupMiddleware(),
    )
    return broker


broker = create_broker()
