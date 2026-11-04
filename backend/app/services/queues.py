"""Queue depths from the RabbitMQ management API, for the admin System panel."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

import httpx
from pydantic import BaseModel

from app.core.config import Settings
from app.worker.broker import DEAD_LETTER_QUEUE

TASK_QUEUE = "videio.tasks"
DELAY_QUEUE = "videio.tasks.delay"
WATCHED_QUEUES = (TASK_QUEUE, DELAY_QUEUE, DEAD_LETTER_QUEUE)


class QueueStats(BaseModel):
    name: str
    ready: int
    unacked: int
    consumers: int


def management_endpoint(settings: Settings) -> tuple[str, str, str, str]:
    """(base URL, vhost, user, password) for the management API."""
    amqp = urlsplit(settings.rabbitmq_url)
    user = unquote(amqp.username or "guest")
    password = unquote(amqp.password or "guest")
    vhost = unquote(amqp.path[1:]) if len(amqp.path) > 1 else "/"
    base = settings.rabbitmq_management_url or f"http://{amqp.hostname}:15672"
    return base.rstrip("/"), vhost, user, password


async def queue_stats(settings: Settings, timeout_s: float = 3.0) -> list[QueueStats]:
    """Raises httpx.HTTPError (or KeyError on an odd reply) if RabbitMQ cannot say."""
    base, vhost, user, password = management_endpoint(settings)
    async with httpx.AsyncClient(auth=(user, password), timeout=timeout_s) as client:
        response = await client.get(
            f"{base}/api/queues/{quote(vhost, safe='')}",
            params={"columns": "name,messages_ready,messages_unacknowledged,consumers"},
        )
        response.raise_for_status()
    found = {item["name"]: item for item in response.json()}
    return [
        QueueStats(
            name=name,
            ready=int(found.get(name, {}).get("messages_ready", 0) or 0),
            unacked=int(found.get(name, {}).get("messages_unacknowledged", 0) or 0),
            consumers=int(found.get(name, {}).get("consumers", 0) or 0),
        )
        for name in WATCHED_QUEUES
    ]
