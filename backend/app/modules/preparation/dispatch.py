"""The API-to-worker seam for durable preparation jobs."""
from typing import Protocol
from uuid import UUID

class PreparationDispatcher(Protocol):
    def enqueue(self, preparation_id: UUID, owner_id: int, *, speculative: bool = False) -> None: ...


class CeleryPreparationDispatcher:
    def enqueue(self, preparation_id: UUID, owner_id: int, *, speculative: bool = False) -> None:
        from app.modules.preparation.tasks import run_activity_preparation

        run_activity_preparation.apply_async(
            args=[str(preparation_id), owner_id],
            priority=9 if speculative else 0,
        )
