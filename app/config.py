"""Loading and validating config.yaml.

Values may reference environment variables as ``${NAME}`` or ``${NAME:-default}``
so secrets can live in ``.env`` instead of the YAML file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


def expand_env(value: Any) -> Any:
    """Recursively substitute ${VAR} / ${VAR:-default} in strings."""
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    return value


@dataclass
class WidgetConfig:
    id: str
    type: str
    title: str
    refresh_minutes: float | None = None
    size: str = "medium"  # small | medium | large | full
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIConfig:
    enabled: bool = False
    provider: str = "gemini"  # gemini (free tier) | anthropic
    model: str = ""  # empty = the provider's default model
    api_key: str = ""
    language: str = "Spanish"


@dataclass
class AppConfig:
    title: str = "My Dashboard"
    timezone: str = "America/Argentina/Buenos_Aires"
    ai: AIConfig = field(default_factory=AIConfig)
    widgets: list[WidgetConfig] = field(default_factory=list)


_WIDGET_KEYS = {"id", "type", "title", "refresh_minutes", "size", "enabled", "options"}


def parse_config(raw: dict[str, Any], expand: bool = True) -> AppConfig:
    raw = expand_env(raw or {}) if expand else (raw or {})
    ai_raw = raw.get("ai") or {}
    provider = (ai_raw.get("provider") or AIConfig.provider).lower()
    if provider not in ("gemini", "anthropic"):
        raise ConfigError(f"ai.provider must be 'gemini' or 'anthropic', not '{provider}'")
    env_key = os.environ.get("GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY", "")
    ai = AIConfig(
        enabled=bool(ai_raw.get("enabled", False)),
        provider=provider,
        model=ai_raw.get("model") or AIConfig.model,
        api_key=ai_raw.get("api_key") or env_key,
        language=ai_raw.get("language") or AIConfig.language,
    )

    widgets: list[WidgetConfig] = []
    seen: set[str] = set()
    for i, w in enumerate(raw.get("widgets") or []):
        if not isinstance(w, dict) or "type" not in w:
            raise ConfigError(f"widgets[{i}] must be a mapping with a 'type'")
        wid = str(w.get("id") or f"{w['type']}-{i}")
        if wid in seen:
            raise ConfigError(f"duplicate widget id '{wid}'")
        seen.add(wid)
        # Anything that isn't a known top-level key is treated as a widget option,
        # so both styles work:  `options: {feeds: [...]}`  or just  `feeds: [...]`.
        options = dict(w.get("options") or {})
        options.update({k: v for k, v in w.items() if k not in _WIDGET_KEYS})
        widgets.append(
            WidgetConfig(
                id=wid,
                type=w["type"],
                title=w.get("title") or wid,
                refresh_minutes=w.get("refresh_minutes"),
                size=w.get("size", "medium"),
                enabled=w.get("enabled", True),
                options=options,
            )
        )

    return AppConfig(
        title=raw.get("title", AppConfig.title),
        timezone=raw.get("timezone", AppConfig.timezone),
        ai=ai,
        widgets=widgets,
    )


def load_raw_config(path: str | Path) -> dict[str, Any]:
    """Read config.yaml with ${ENV} references already expanded."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file '{path}' not found. Copy config.example.yaml to config.yaml to get started."
        )
    with path.open(encoding="utf-8") as fh:
        return expand_env(yaml.safe_load(fh) or {})


def load_config(path: str | Path) -> AppConfig:
    return parse_config(load_raw_config(path), expand=False)
