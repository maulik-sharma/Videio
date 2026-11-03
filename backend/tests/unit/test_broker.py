from unittest.mock import AsyncMock

import pytest
from taskiq import TaskiqMessage, TaskiqResult

from app.worker.broker import (
    MAX_ATTEMPTS,
    RETRY_DELAYS_S,
    BackoffRetryMiddleware,
    FinalFailureDeadLetterMiddleware,
    VideioBroker,
    is_final_failure,
)
from app.worker.ffmpeg import PermanentTranscodeError, RetryableTranscodeError
from app.worker.tasks.transcode import transcode_video


def _message(**labels: object) -> TaskiqMessage:
    return TaskiqMessage(
        task_id="task-id",
        task_name="transcode_video",
        labels={"retry_on_error": True, "max_retries": MAX_ATTEMPTS, **labels},
        args=[42],
        kwargs={},
    )


def test_transcode_task_is_labelled_for_retries_and_dead_lettering() -> None:
    labels = transcode_video.labels
    assert labels["retry_on_error"] is True
    assert labels["max_retries"] == MAX_ATTEMPTS == 4  # three retries
    assert labels["dead_letter"] is True
    # A `delay` label would make the broker hold even the first send in the delay queue.
    assert "delay" not in labels


@pytest.mark.parametrize(
    ("previous_retries", "exception", "final"),
    [
        (0, RetryableTranscodeError("blip"), False),
        (MAX_ATTEMPTS - 2, RetryableTranscodeError("blip"), False),
        (MAX_ATTEMPTS - 1, RetryableTranscodeError("blip"), True),
        (0, PermanentTranscodeError("bad input"), True),
        (0, ValueError("not a retryable type"), True),
    ],
)
def test_final_failure_matches_the_retry_budget(
    previous_retries: int, exception: BaseException, final: bool
) -> None:
    assert is_final_failure(_message(_retries=previous_retries), exception) is final


def test_labels_survive_string_serialisation() -> None:
    message = _message(_retries="3", max_retries="4", retry_on_error="True")
    assert is_final_failure(message, RetryableTranscodeError("blip"))
    message = _message(retry_on_error="False")
    assert is_final_failure(message, RetryableTranscodeError("blip"))


def test_retry_delays_follow_the_schedule() -> None:
    middleware = BackoffRetryMiddleware(default_retry_count=MAX_ATTEMPTS, use_jitter=False)
    for retry, base in enumerate(RETRY_DELAYS_S, start=1):
        delay = middleware.make_delay(_message(), retry)
        assert base <= delay <= base * 1.1


async def test_dead_letter_middleware_only_fires_on_the_final_failure() -> None:
    broker = object.__new__(VideioBroker)
    broker.publish_to_dead_letter = AsyncMock()  # type: ignore[method-assign]
    handler = AsyncMock()
    broker.final_failure_handlers = {"transcode_video": handler}
    middleware = FinalFailureDeadLetterMiddleware()
    middleware.set_broker(broker)
    result = TaskiqResult(is_err=True, return_value=None, execution_time=0, error=None)

    await middleware.on_error(_message(dead_letter=True), result, RetryableTranscodeError("t"))
    assert broker.publish_to_dead_letter.await_count == 0
    assert handler.await_count == 0

    last = _message(dead_letter=True, _retries=MAX_ATTEMPTS - 1)
    await middleware.on_error(last, result, RetryableTranscodeError("final"))
    assert broker.publish_to_dead_letter.await_count == 1
    assert handler.await_count == 1

    await middleware.on_error(_message(dead_letter=True), result, PermanentTranscodeError("bad"))
    assert broker.publish_to_dead_letter.await_count == 2

    # Tasks without the label (maintenance) never go to the transcode DLQ.
    await middleware.on_error(_message(), result, PermanentTranscodeError("bad"))
    assert broker.publish_to_dead_letter.await_count == 2


async def test_a_failing_handler_still_dead_letters() -> None:
    broker = object.__new__(VideioBroker)
    broker.publish_to_dead_letter = AsyncMock()  # type: ignore[method-assign]
    broker.final_failure_handlers = {"transcode_video": AsyncMock(side_effect=OSError("db"))}
    middleware = FinalFailureDeadLetterMiddleware()
    middleware.set_broker(broker)
    result = TaskiqResult(is_err=True, return_value=None, execution_time=0, error=None)

    await middleware.on_error(_message(dead_letter=True), result, PermanentTranscodeError("bad"))
    assert broker.publish_to_dead_letter.await_count == 1
