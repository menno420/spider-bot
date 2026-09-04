"""spiderbot/config.py - env parsing, defaults, and secret redaction."""

from __future__ import annotations

import dataclasses

import pytest

from spiderbot import config

_ALL_VARS = (
    "DISCORD_TOKEN",
    "DISCORD_BOT_TOKEN_SPIDERBOT",
    "GUILD_ID",
    "ANTHROPIC_API_KEY",
    "AI_ENABLED",
    "AI_MODEL",
    "AI_EFFORT",
    "AI_MAX_RESPONSE_TOKENS",
    "AI_MEMORY_TURNS",
    "AI_INITIATIVE_CHANNELS",
    "AI_INITIATIVE_COOLDOWN_SECONDS",
    "AI_INITIATIVE_HOURLY_CAP",
    "LOG_LEVEL",
    "GITHUB_TOKEN",
    "GITHUB_REPO",
    "GITHUB_REPO_BOT",
    "INTAKE_PUBLISH_ENABLED",
)

_FAKE_TOKEN = "NOT-A-REAL-TOKEN-" + "x" * 42


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not see the machine's real environment."""
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def test_no_token_is_fatal():
    with pytest.raises(SystemExit):
        config.load()


def test_defaults_with_only_token(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    cfg = config.load()
    assert cfg.guild_id == 1541447750628147351
    assert cfg.ai_enabled is True
    assert cfg.ai_model == "claude-opus-5"
    assert cfg.ai_effort == "low"
    assert cfg.ai_max_response_tokens == 1000
    assert cfg.initiative_channels == ("general",)
    assert cfg.initiative_cooldown_s == 120
    assert cfg.initiative_hourly_cap == 10
    assert cfg.anthropic_api_key is None


def test_local_token_var_is_the_fallback(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN_SPIDERBOT", _FAKE_TOKEN)
    assert config.load().discord_token == _FAKE_TOKEN


def test_empty_railway_token_falls_through_to_local(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "")
    monkeypatch.setenv("DISCORD_BOT_TOKEN_SPIDERBOT", _FAKE_TOKEN)
    assert config.load().discord_token == _FAKE_TOKEN


def test_initiative_channels_parse_and_strip(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    monkeypatch.setenv("AI_INITIATIVE_CHANNELS", "general, testing ,")
    assert config.load().initiative_channels == ("general", "testing")


def test_initiative_channels_can_be_emptied(monkeypatch):
    # "," yields no names: the unconfigured=silent state is reachable.
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    monkeypatch.setenv("AI_INITIATIVE_CHANNELS", ",")
    assert config.load().initiative_channels == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), ("on", True),
     ("0", False), ("false", False), ("banana", False)],
)
def test_ai_enabled_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    monkeypatch.setenv("AI_ENABLED", raw)
    assert config.load().ai_enabled is expected


def test_bad_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    monkeypatch.setenv("AI_MAX_RESPONSE_TOKENS", "many")
    assert config.load().ai_max_response_tokens == 1000


def test_repr_never_leaks_secrets(monkeypatch):
    # Invariant 10: secrets are env references only - repr/logs included.
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "NOT-A-REAL-KEY-abcdef")
    text = repr(config.load())
    assert _FAKE_TOKEN not in text
    assert "NOT-A-REAL-KEY-abcdef" not in text
    assert "<redacted" in text


def test_config_is_frozen(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    cfg = config.load()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.ai_enabled = False


def test_the_two_trackers_default_to_the_two_repositories(monkeypatch):
    """Owner, 2026-09-04: game reports to spider-swing, bot reports to
    spider-bot. Both overridable, neither ever empty."""
    monkeypatch.setenv("DISCORD_TOKEN", _FAKE_TOKEN)
    cfg = config.load()
    assert cfg.github_repo == "menno420/spider-swing"
    assert cfg.github_repo_bot == "menno420/spider-bot"
    assert "github_repo_bot='menno420/spider-bot'" in repr(cfg)
    monkeypatch.setenv("GITHUB_REPO_BOT", "someone/elsewhere")
    monkeypatch.setenv("GITHUB_REPO", "")
    cfg = config.load()
    assert cfg.github_repo_bot == "someone/elsewhere"
    assert cfg.github_repo == "menno420/spider-swing"  # empty falls back, never blank


def test_every_documented_variable_is_preserved_in_the_railway_iac():
    """Railway IaC is omit-means-delete: a variable set in the dashboard but
    absent from `.railway/railway.ts` is removed by the next apply (measured
    2026-09-04, read-only plan). Codex, spider-bot#5: `GITHUB_REPO_BOT` was
    loaded here and absent there. So the rule is mechanical — every name
    `.env.example` documents, except the local-only token, is preserve()d."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    documented = {
        m.group(1)
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)=", (root / ".env.example").read_text(), re.M)
    } - {"DISCORD_BOT_TOKEN_SPIDERBOT"}
    iac = (root / ".railway/railway.ts").read_text()
    preserved = set(re.findall(r"^\s+([A-Z][A-Z0-9_]*): preserve\(\),", iac, re.M))
    assert documented, "the .env.example scan found nothing — positive control failed"
    assert documented <= preserved, sorted(documented - preserved)
