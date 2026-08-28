"""
Tier-2 semantic entailment checking. "Use a cheaper model for this check
than the one generating the answer" (mandate step 6): GeminiEntailmentChecker
takes its own GenerationGateway instance so the caller can point it at a
smaller/cheaper model than the main answer-generation gateway.
"""
import json

from app.services.generation.gateway import GenerationError, GenerationGateway


class GeminiEntailmentChecker:
    def __init__(self, generation: GenerationGateway):
        self.generation = generation

    def __call__(self, claim_text: str, chunk_text: str) -> bool:
        prompt = (
            "Does the SOURCE TEXT support the CLAIM? Answer only based on what the source actually says.\n\n"
            f"SOURCE TEXT:\n{chunk_text}\n\nCLAIM:\n{claim_text}\n\n"
            'Return ONLY JSON: {"supported": bool}'
        )
        try:
            raw = self.generation.generate(prompt, temperature=0.0)
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else ""
                text = text[:-3] if text.endswith("```") else text
            return bool(json.loads(text.strip())["supported"])
        except (GenerationError, json.JSONDecodeError, KeyError, ValueError):
            # An unparseable or failed entailment check is treated as
            # unsupported -- fail closed, never let an unverifiable claim
            # through as if it had passed.
            return False
