"""Celery queue declarations and routing policy."""

from enums import CeleryTaskFunction


def celery_queue_config(settings) -> dict:
    """Return queue and task-route settings for the shared Celery app."""
    queues = (
        settings.CELERY_TASK_DEFAULT_QUEUE,
        settings.CELERY_QUEUE_PROCESS,
        settings.CELERY_QUEUE_INDEX,
        settings.CELERY_QUEUE_CHAT,
        settings.CELERY_QUEUE_MAINTENANCE,
    )
    # x-queue-type is what Celery's detect_quorum_queues() looks for, and
    # finding it is what makes the worker turn global QoS off — the deprecated
    # RabbitMQ feature. Declared on every queue so the detection cannot depend
    # on which queue a given worker happens to consume.
    #
    # Why quorum rather than classic, since the choice is not reversible in
    # place (a queue's type is fixed at declaration; redeclaring with a
    # different x-queue-type fails PRECONDITION_FAILED, so switching means
    # deleting the queues while nothing is publishing):
    #
    #   * Quorum queues are Raft-replicated and disk-durable. An accepted task
    #     survives a broker restart. A classic transient queue drops its
    #     contents, which for an ingest job means silent data loss.
    #   * There is no longer an alternative for replication: RabbitMQ 4.x
    #     reports classic_queue_mirroring as `removed`/`denied` — verified on
    #     this broker, not read from the changelog.
    #   * It is what turns global QoS off, as the paragraph above describes.
    #
    # The cost is accepted knowingly. Quorum queues are heavier per queue, and
    # they cannot express the transient/auto-delete semantics that Celery's own
    # `celeryev.*` (events, gossip) and `celery@<host>.celery.pidbox` (remote
    # control) queues need — so those 8 stay classic and non-durable, and
    # RabbitMQ's management API therefore still reports `transient_nonexcl_queues`
    # as a deprecated feature in use. That cannot be resolved from this file:
    # removing it would mean giving up both Flower's task events and the
    # `celery inspect ping` healthcheck the workers are probed with. It is
    # `permitted_by_default` in 4.1.8, not removed, so it is documented here
    # rather than chased.
    arguments = {"x-queue-type": settings.CELERY_TASK_QUEUE_TYPE}

    return {
        "task_queues": {name: {"routing_key": name, "queue_arguments": dict(arguments)} for name in queues},
        "task_routes": {
            f"{settings.CELERY_PROJECT_NAME}.{CeleryTaskFunction.PROCESS.value}": {
                "queue": settings.CELERY_QUEUE_PROCESS
            },
            f"{settings.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}": {"queue": settings.CELERY_QUEUE_INDEX},
            f"{settings.CELERY_PROJECT_NAME}.{CeleryTaskFunction.CHAT.value}": {"queue": settings.CELERY_QUEUE_CHAT},
            f"{settings.CELERY_PROJECT_NAME}.{CeleryTaskFunction.MAINTENANCE.value}": {
                "queue": settings.CELERY_QUEUE_MAINTENANCE
            },
        },
    }
