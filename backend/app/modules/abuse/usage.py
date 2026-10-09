"""Aggregate known usage without treating missing SDK token reports as zero cost."""
from sqlalchemy import case, func

from app.modules.abuse.models import AIProviderCall


def usage_totals(query):
    call = AIProviderCall
    sent = call.status != "CANCELLED"
    row = query.with_entities(
        func.sum(case((sent, 1), else_=0)),
        func.sum(case((sent & (call.operation == "generation"), 1), else_=0)),
        func.sum(case((sent & (call.operation == "embedding"), 1), else_=0)),
        func.sum(case((call.status == "SUCCEEDED", 1), else_=0)),
        func.sum(case((call.status == "FAILED", 1), else_=0)),
        func.sum(case((call.status == "STARTED", 1), else_=0)),
        func.sum(call.input_tokens), func.sum(call.output_tokens), func.sum(call.cached_input_tokens),
        func.sum(call.total_tokens), func.sum(case((call.total_tokens.is_not(None), 1), else_=0)),
        func.sum(call.elapsed_ms),
        func.sum(call.capacity_wait_ms),
        func.sum(case((call.input_tokens.is_not(None) & call.output_tokens.is_not(None) & call.total_tokens.is_not(None), 1), else_=0)),
        func.sum(case((call.status == "CANCELLED", 1), else_=0)),
    ).one()
    values = [int(value or 0) for value in row]
    names = ["generation_attempts", "embedding_attempts", "succeeded_attempts", "failed_attempts", "unfinished_attempts",
             "reported_input_tokens", "reported_output_tokens", "reported_cached_input_tokens", "reported_total_tokens",
             "calls_with_token_usage", "provider_elapsed_ms", "capacity_wait_ms"]
    return {**dict(zip(names, values[1:13])), "token_usage_complete": values[0] == values[13],
            "cancelled_reservations": values[14]}
