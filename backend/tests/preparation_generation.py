"""Shared offline fixtures for the preparation batch-validation wire contract."""
import json

from app.services.generation.fake import FakeGenerationGateway


class PreparationGenerationGateway(FakeGenerationGateway):
    """Translate registered per-claim judgments into the batch wire contract."""

    def generate(self, prompt, **kwargs):
        if "BATCH CHECKS:\n" not in prompt:
            return super().generate(prompt, **kwargs)
        self.calls.append(prompt)
        self.system_instructions.append(kwargs.get("system_instruction"))
        payload = json.loads(prompt.split("BATCH CHECKS:\n", 1)[1].split("\n\nReturn ONLY JSON:", 1)[0])
        results = []
        for check in payload["checks"]:
            source = payload["sources"][check["source_id"]]
            single_prompt = f'SOURCE TEXT:\n{source}\n\nCLAIM:\n{check["claim"]}'
            judgment = json.loads(self.response_for(single_prompt))["supported"]
            results.append({"id": check["id"], "supported": judgment})
        return json.dumps({"results": results})
