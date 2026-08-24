"""Typed, fail-fast configuration from environment variables.

Follows the estate pattern (superbot-next sb/kernel/config): frozen dataclass,
redacting repr, validation at import, no secret ever printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    anthropic_api_key: str | None
    ai_enabled: bool
    ai_model: str
    ai_effort: str
    ai_max_response_tokens: int
    ai_memory_turns: int
    initiative_channels: tuple[str, ...]
    initiative_cooldown_s: int
    initiative_hourly_cap: int
    log_level: str
    # Channel names resolved at runtime (ids change less often than names,
    # but names are what the owner sees; env can override with ids later).
    ch_start_here: str = "start-here"
    ch_general: str = "general"
    ch_mod_log: str = "mod-log"
    ch_feedback: str = "feedback"
    ch_announcements: str = "announcements"
    tester_role_name: str = "Slingy Tester"
    group_url: str = "https://groups.google.com/g/slingy-spider-testers"
    optin_url: str = "https://play.google.com/apps/testing/com.menno420.slingyspider"

    def __repr__(self) -> str:  # never leak secrets via repr/logs
        return (
            f"Config(guild_id={self.guild_id}, ai_enabled={self.ai_enabled}, "
            f"ai_model={self.ai_model!r}, ai_effort={self.ai_effort!r}, "
            f"initiative_channels={self.initiative_channels}, "
            f"discord_token=<redacted:{len(self.discord_token)}>, "
            f"anthropic_api_key={'<redacted>' if self.anthropic_api_key else None})"
        )


def load() -> Config:
    token = _env("DISCORD_TOKEN") or _env("DISCORD_BOT_TOKEN_SPIDERBOT")
    if not token:
        raise SystemExit(
            "FATAL: no Discord token. Set DISCORD_TOKEN (Railway) or "
            "DISCORD_BOT_TOKEN_SPIDERBOT (local)."
        )
    channels = tuple(
        c.strip() for c in (_env("AI_INITIATIVE_CHANNELS", "general") or "").split(",") if c.strip()
    )
    return Config(
        discord_token=token,
        guild_id=_env_int("GUILD_ID", 1541447750628147351),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        ai_enabled=_env_bool("AI_ENABLED", True),
        ai_model=_env("AI_MODEL", "claude-opus-5") or "claude-opus-5",
        ai_effort=_env("AI_EFFORT", "low") or "low",
        ai_max_response_tokens=_env_int("AI_MAX_RESPONSE_TOKENS", 1000),
        ai_memory_turns=_env_int("AI_MEMORY_TURNS", 20),
        initiative_channels=channels,
        initiative_cooldown_s=_env_int("AI_INITIATIVE_COOLDOWN_SECONDS", 120),
        initiative_hourly_cap=_env_int("AI_INITIATIVE_HOURLY_CAP", 10),
        log_level=_env("LOG_LEVEL", "INFO") or "INFO",
    )
