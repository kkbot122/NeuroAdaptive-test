import json

import pytest

from app.core.config import settings
from app.modules.tutor.entailment import EntailmentUnavailable, GeminiEntailmentChecker, check_support
from app.services.generation.fake import FakeGenerationGateway
from app.services.generation.gateway import GenerationError


class BatchGateway(FakeGenerationGateway):
    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)
        self.options = []

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        self.options.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps(result)


def response(*judgments):
    return {"results": [{"id": index, "supported": value} for index, value in enumerate(judgments)]}


def payload(prompt):
    return json.loads(prompt.split("BATCH CHECKS:\n", 1)[1].split("\n\nReturn ONLY JSON:", 1)[0])


def test_results_are_matched_by_id_and_each_check_uses_only_its_source():
    gateway = BatchGateway({"results": [{"id": 1, "supported": False}, {"id": 0, "supported": True}]})
    checker = GeminiEntailmentChecker(gateway, raise_on_error=True)
    assert checker.check_many([("first claim", "shared source"), ("second claim", "shared source")]) == [True, False]
    batch = payload(gateway.calls[0])
    for item, source in zip(batch["checks"], ["shared source", "shared source"]):
        assert batch["sources"][item["source_id"]] == source
    assert "shared source" not in gateway.options[0]["system_instruction"]
    assert gateway.options[0]["json_mode"] is True
    assert "all its factual assertions must be supported" in gateway.calls[0]
    assert "If only part is supported, return false" in gateway.calls[0]


def test_different_cited_passages_are_never_sent_in_the_same_batch():
    gateway = BatchGateway(response(True), response(False))
    checks = [("first claim", "first source"), ("second claim", "second source")]
    assert GeminiEntailmentChecker(gateway).check_many(checks) == [True, False]
    assert len(gateway.calls) == 2
    assert list(payload(gateway.calls[0])["sources"].values()) == ["first source"]
    assert list(payload(gateway.calls[1])["sources"].values()) == ["second source"]


def test_exact_pairs_are_deduplicated_and_cached_but_different_sources_are_not():
    gateway = BatchGateway(response(True, False), response(False))
    checker = GeminiEntailmentChecker(gateway)
    checks = [("claim", "source"), ("other", "source"), ("claim", "source")]
    assert checker.check_many(checks) == [True, False, True]
    assert len(payload(gateway.calls[0])["sources"]) == 1
    assert len(payload(gateway.calls[0])["checks"]) == 2
    assert checker.check_many(checks) == [True, False, True]
    assert len(gateway.calls) == 1
    assert checker.check_many([("claim", "different source")]) == [False]
    assert len(gateway.calls) == 2


def test_batch_bound_covers_every_check_without_sampling(monkeypatch):
    monkeypatch.setattr(settings, "P2_VALIDATION_BATCH_SIZE_V1", 2)
    gateway = BatchGateway(response(True, True), response(True, False), response(True))
    checks = [(f"claim {index}", "source") for index in range(5)]
    assert GeminiEntailmentChecker(gateway).check_many(checks) == [True, True, True, False, True]
    assert [len(payload(prompt)["checks"]) for prompt in gateway.calls] == [2, 2, 1]


@pytest.mark.parametrize("invalid", [
    {"results": []},
    response(True),
    response(True, False, True),
    {"results": [{"id": 0, "supported": True}, {"id": 0, "supported": False}]},
    {"results": [{"id": 0, "supported": True}, {"id": 2, "supported": False}]},
    {"results": [{"id": False, "supported": True}, {"id": 1, "supported": False}]},
    response(True, "false"),
    response(True, 0),
    {"results": [{"id": 0, "supported": True, "extra": "ignored?"}, {"id": 1, "supported": False}]},
    {"results": [], "supported": False},
    "not JSON",
    GenerationError("private upstream error"),
])
def test_incomplete_or_invalid_batches_never_become_negative_distractor_judgments(invalid):
    gateway = BatchGateway(invalid, response(True, False))
    checker = GeminiEntailmentChecker(gateway, raise_on_error=False)
    checks = [("correct answer", "source"), ("distractor", "source")]
    with pytest.raises(EntailmentUnavailable):
        checker.check_many(checks)
    assert checker.check_many(checks) == [True, False]
    assert len(gateway.calls) == 2


def test_empty_batch_does_not_call_provider():
    gateway = BatchGateway()
    assert GeminiEntailmentChecker(gateway).check_many([]) == []
    assert gateway.calls == []


def test_custom_batch_checker_cannot_omit_a_required_judgment():
    class IncompleteChecker:
        def check_many(self, checks):
            return [True]
    with pytest.raises(EntailmentUnavailable):
        check_support(IncompleteChecker(), [("answer", "source"), ("distractor", "source")])
