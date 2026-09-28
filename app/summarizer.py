"""Optional AI summaries (news, email).

Two providers:
- gemini (default): Google's Gemini API. The free tier (key from
  https://aistudio.google.com/apikey) is plenty for a personal dashboard.
- anthropic: the Claude API (paid).

Disabled unless `ai.enabled: true` and an API key is configured; widgets then
fall back to showing plain headlines / the first line of each email.
"""
from __future__ import annotations

import hashlib
import logging
import re

import httpx

from .config import AIConfig

log = logging.getLogger(__name__)

_MAX_CACHE = 300
DEFAULT_MODELS = {"gemini": "gemini-flash-latest", "anthropic": "claude-opus-5"}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_NUMBERED = re.compile(r"^\s*(\d+)[.):\-]\s*(.+?)\s*$")


class Summarizer:
    def __init__(self, config: AIConfig, http: httpx.AsyncClient | None = None):
        self.config = config
        self.http = http
        self.model = config.model or DEFAULT_MODELS[config.provider]
        self._anthropic = None
        enabled = config.enabled and bool(config.api_key)
        if enabled and config.provider == "anthropic":
            import anthropic

            self._anthropic = anthropic.AsyncAnthropic(api_key=config.api_key)
        self._enabled = enabled and (config.provider == "anthropic" or http is not None)
        self._cache: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return self._enabled

    async def summarize(self, instructions: str, content: str) -> str | None:
        """Return a short bullet summary, or None if AI is disabled or the call failed."""
        if not content.strip():
            return None
        system = (
            "You write the summaries shown on a personal dashboard. Be concise and factual, "
            "use short '- ' bullet points, no preamble, and no markdown other than bullets. "
            f"Write in {self.config.language}."
        )
        return await self._complete(system, f"{instructions}\n\n<content>\n{content}\n</content>")

    async def summarize_lines(self, instructions: str, entries: list[str]) -> list[str] | None:
        """One short line per entry, in the same order. None if unavailable or unparseable."""
        if not entries:
            return None
        system = (
            "You write one-line summaries for a personal dashboard. For each numbered input, output "
            "exactly one line: the same number, a period, then a summary of at most 15 words. "
            f"No preamble, no blank lines. Write in {self.config.language}."
        )
        content = "\n".join(f"{i}. {e}" for i, e in enumerate(entries, 1))
        text = await self._complete(system, f"{instructions}\n\n<content>\n{content}\n</content>")
        if not text:
            return None
        lines: dict[int, str] = {}
        for line in text.splitlines():
            m = _NUMBERED.match(line)
            if m:
                lines[int(m.group(1))] = m.group(2)
        if not lines:
            return None
        return [lines.get(i, "") for i in range(1, len(entries) + 1)]

    async def _complete(self, system: str, prompt: str) -> str | None:
        if not self._enabled:
            return None
        key = hashlib.sha256(f"{self.config.provider}\n{self.model}\n{system}\n{prompt}".encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        if self.config.provider == "gemini":
            text = await self._gemini(system, prompt)
        else:
            text = await self._claude(system, prompt)
        if text:
            if len(self._cache) >= _MAX_CACHE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = text
        return text or None

    async def _gemini(self, system: str, prompt: str) -> str | None:
        try:
            resp = await self.http.post(
                GEMINI_URL.format(model=self.model),
                headers={"x-goog-api-key": self.config.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                },
                timeout=60,
            )
        except httpx.HTTPError as exc:
            log.warning("Gemini API unreachable: %s", exc)
            return None
        if resp.status_code != 200:
            log.warning("Gemini API error %s: %s", resp.status_code, resp.text[:300])
            return None
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, ValueError):
            return None
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()

    async def _claude(self, system: str, prompt: str) -> str | None:
        import anthropic

        try:
            response = await self._anthropic.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system,
                output_config={"effort": "low"},
                # If the model declines, the API retries on Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
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
        return "".join(b.text for b in response.content if b.type == "text").strip()
