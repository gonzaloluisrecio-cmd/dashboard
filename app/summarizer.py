"""Optional AI summaries (news, email) using the Claude API.

Disabled unless `ai.enabled: true` and an API key is configured; widgets then
fall back to showing plain headlines / subjects.
"""
from __future__ import annotations

import hashlib
import logging

import anthropic

from .config import AIConfig

log = logging.getLogger(__name__)

_MAX_CACHE = 200


class Summarizer:
    def __init__(self, config: AIConfig):
        self.config = config
        self._client = (
            anthropic.AsyncAnthropic(api_key=config.api_key)
            if config.enabled and config.api_key
            else None
        )
        self._cache: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return self._client is not None

    async def summarize(self, instructions: str, content: str) -> str | None:
        """Return a short summary, or None if AI is disabled or the call failed."""
        if not self._client or not content.strip():
            return None
        key = hashlib.sha256(f"{self.config.model}\n{instructions}\n{content}".encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]

        system = (
            "You write the summaries shown on a personal dashboard. Be concise and factual, "
            "use short '- ' bullet points, no preamble, and no markdown other than bullets. "
            f"Write in {self.config.language}."
        )
        try:
            response = await self._client.beta.messages.create(
                model=self.config.model,
                max_tokens=2000,
                system=system,
                output_config={"effort": "low"},
                # If the model declines, the API retries on Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": f"{instructions}\n\n<content>\n{content}\n</content>"}],
            )
        except anthropic.APIConnectionError as exc:
            log.warning("Claude API unreachable: %s", exc)
            return None
        except anthropic.RateLimitError:
            log.warning("Claude API rate limited; skipping summary")
            return None
        except anthropic.APIStatusError as exc:
            log.warning("Claude API error %s: %s", exc.status_code, exc.message)
            return None

        if response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if text:
            if len(self._cache) >= _MAX_CACHE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = text
        return text or None
