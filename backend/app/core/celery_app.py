"""Celery application for durable processing-job execution."""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "neurolearn",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.modules.jobs.tasks", "app.modules.preparation.tasks",
        "app.modules.learning.tasks", "app.modules.privacy.tasks",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.WORKER_TASK_SOFT_TIME_LIMIT_SECONDS_V1,
    task_time_limit=settings.WORKER_TASK_TIME_LIMIT_SECONDS_V1,
    broker_transport_options={"visibility_timeout": 1800},
    result_backend_transport_options={"visibility_timeout": 1800},
    task_default_priority=3,
)
# Redis emulates Celery priorities with several lists per queue. Lower values
# run first; student preparation at 0 stays ahead of lookahead at 9.
celery_app.conf.broker_transport_options.update(
    {"queue_order_strategy": "priority", "priority_steps": [0, 3, 6, 9]}
)
