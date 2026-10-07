"""Queue grading work; PostgreSQL answer state remains authoritative."""
from uuid import UUID


class GradingDispatcher:
    def enqueue(self, answer_submission_id: UUID, owner_id: int) -> None:
        from app.modules.learning.tasks import run_assessment_grading

        run_assessment_grading.delay(str(answer_submission_id), owner_id)
