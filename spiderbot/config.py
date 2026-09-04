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


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = _env(name, default) or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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

    # -- intake -------------------------------------------------------------
    # Fail-closed by default: absent token means the GitHub client refuses
    # every publish by name and the report stays queued. Nothing invents a
    # credential and nothing pretends to have published.
    github_token: str | None = None
    github_repo: str = "menno420/spider-swing"
    #: Publication is OFF until the owner turns it on, even with a token
    #: present. Two independent locks, because the first live report reaching a
    #: public tracker should be a decision rather than a side effect of setting
    #: a variable.
    intake_publish_enabled: bool = False

    # -- moderation ---------------------------------------------------------
    #: off | shadow | enforce. Anything unrecognised is treated as shadow by
    #: `operations.executor_for`, so a typo does nothing rather than acting.
    mod_mode: str = "off"
    #: Unconfigured = silent (invariant 4). Empty means moderation runs nowhere,
    #: not everywhere.
    mod_watch_channels: tuple[str, ...] = ()
    #: The rollout lever. The whole classifier and policy path runs at
    #: `flag_for_review` and nothing a member can see ever changes.
    mod_ceiling: str = "flag_for_review"
    mod_model: str = ""

    # -- the game support feed ----------------------------------------------
    #: Where spider-swing publishes the versioned facts this bot needs. Empty
    #: means fall back to the built-in knowledge, with the staleness stated.
    support_feed_url: str = (
        "https://raw.githubusercontent.com/menno420/spider-swing/main/"
        "support/spider-bot-support-feed.json"
    )
    support_feed_refresh_s: int = 3600
    # Channel names resolved at runtime (ids change less often than names,
    # but names are what the owner sees; env can override with ids later).
    ch_start_here: str = "start-here"
    ch_general: str = "general"
    ch_mod_log: str = "mod-log"
    ch_feedback: str = "feedback"
    ch_bug_reports: str = "bug-reports"
    ch_announcements: str = "announcements"
    # Private staff channel used as the bot's durable memory (spiderbot/memory.py).
    # Absent = membership memory silently off, like any other unconfigured feature.
    ch_bot_state: str = "bot-state"
    # Where reports and moderation cases live. Separate from bot-state so the
    # membership snapshots stay readable by eye and a full history window on one
    # collection does not push another out of reach.
    ch_intake_state: str = "intake-state"
    ch_case_state: str = "case-state"
    tester_role_name: str = "Slingy Tester"
    group_url: str = "https://groups.google.com/g/slingy-spider-testers"
    optin_url: str = "https://play.google.com/apps/testing/com.menno420.slingyspider"

    def __repr__(self) -> str:  # never leak secrets via repr/logs
        return (
            f"Config(guild_id={self.guild_id}, ai_enabled={self.ai_enabled}, "
            f"ai_model={self.ai_model!r}, ai_effort={self.ai_effort!r}, "
            f"initiative_channels={self.initiative_channels}, "
            f"mod_mode={self.mod_mode!r}, mod_ceiling={self.mod_ceiling!r}, "
            f"mod_watch_channels={self.mod_watch_channels}, "
            f"intake_publish_enabled={self.intake_publish_enabled}, "
            f"github_repo={self.github_repo!r}, "
            f"discord_token=<redacted:{len(self.discord_token)}>, "
            f"anthropic_api_key={'<redacted>' if self.anthropic_api_key else None}, "
            f"github_token={'<redacted>' if self.github_token else None})"
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
        github_token=_env("GITHUB_TOKEN"),
        github_repo=_env("GITHUB_REPO", "menno420/spider-swing") or "menno420/spider-swing",
        intake_publish_enabled=_env_bool("INTAKE_PUBLISH_ENABLED", False),
        mod_mode=(_env("MOD_MODE", "off") or "off").strip().lower(),
        mod_watch_channels=_csv("MOD_WATCH_CHANNELS"),
        mod_ceiling=(_env("MOD_CEILING", "flag_for_review") or "flag_for_review").strip().lower(),
        mod_model=_env("MOD_MODEL", "") or "",
        # `os.environ.get`, not `_env`: the documented way to turn the feed off
        # is to set this to an empty string, and `_env` replaces an empty value
        # with the default. Codex, spider-bot#3, 2026-09-04 — the advertised
        # way to select the built-in block could not be selected.
        support_feed_url=os.environ.get(
            "SUPPORT_FEED_URL", Config.support_feed_url
        ).strip(),
        support_feed_refresh_s=_env_int("SUPPORT_FEED_REFRESH_SECONDS", 3600),
    )
