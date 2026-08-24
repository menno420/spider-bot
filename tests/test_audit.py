"""spiderbot/audit.py - both sinks are best-effort and never raise (invariant 7)."""

from __future__ import annotations

import asyncio
import json

from spiderbot import audit


class _RecordingChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class _ExplodingChannel:
    async def send(self, *args, **kwargs):
        raise RuntimeError("discord went away")


def test_stdout_event_emits_one_json_line(capsys):
    audit.stdout_event("test_kind", a=1, b="x")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    rec = json.loads(out[0])
    assert rec["kind"] == "test_kind"
    assert rec["a"] == 1
    assert rec["b"] == "x"
    assert isinstance(rec["ts"], float)


def test_stdout_event_survives_unserializable_fields(capsys):
    audit.stdout_event("k", obj=object())  # default=str, must not raise
    rec = json.loads(capsys.readouterr().out.strip())
    assert rec["kind"] == "k"


def test_modlog_none_channel_is_a_noop():
    asyncio.run(audit.modlog_event(None, "title", "desc"))  # must not raise


def test_modlog_truncates_embed_fields():
    ch = _RecordingChannel()
    asyncio.run(audit.modlog_event(ch, "t" * 300, "d" * 5000))
    [(args, kwargs)] = ch.sent
    embed = kwargs["embed"]
    assert len(embed.title) == 256
    assert len(embed.description) == 4000


def test_modlog_send_failure_is_swallowed():
    asyncio.run(audit.modlog_event(_ExplodingChannel(), "t", "d"))  # must not raise
