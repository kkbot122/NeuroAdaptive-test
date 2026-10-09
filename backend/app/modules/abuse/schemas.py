"""Owner-scoped attempt accounting; unknown provider tokens stay unknown."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AIUsageTotalsOut(BaseModel):
    generation_attempts: int
    embedding_attempts: int
    succeeded_attempts: int
    failed_attempts: int
    unfinished_attempts: int
    cancelled_reservations: int
    reported_input_tokens: int
    reported_output_tokens: int
    reported_cached_input_tokens: int
    reported_total_tokens: int
    calls_with_token_usage: int
    token_usage_complete: bool
    provider_elapsed_ms: int
    capacity_wait_ms: int


class AIProviderCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    operation_id: UUID
    resource_id: UUID | None
    feature: str
    phase: str
    provider: str
    model_id: str
    operation: Literal["generation", "embedding"]
    retry_index: int
    input_items: int
    status: Literal["STARTED", "SUCCEEDED", "FAILED", "CANCELLED"]
    started_at: datetime
    finished_at: datetime | None
    elapsed_ms: int | None
    capacity_wait_ms: int
    error_category: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    total_tokens: int | None


class AIUsageOut(BaseModel):
    usage_date: date
    accounting_policy: Literal["provider-attempt-v1"] = "provider-attempt-v1"
    daily_generation_limit: int
    generation_budget_used: int
    generation_budget_exempt: bool
    unitemized_generation_reservations: int
    totals: AIUsageTotalsOut
    features: dict[str, AIUsageTotalsOut]
    recent_calls: list[AIProviderCallOut]
