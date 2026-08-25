"""spiderbot/__main__.py - how the process talks to its host.

`logging.basicConfig` puts everything on stderr, and Railway tags every stderr
line as an error. That made routine boot chatter indistinguishable from a real
crash in the log view, which is the same as having no error signal at all.
"""

from __future__ import annotations

import logging
import sys

import pytest

from spiderbot.__main__ import configure_logging


@pytest.fixture
def clean_root():
    """Restore the root logger, so configuring it cannot leak between tests."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_ordinary_lines_and_problems_go_to_different_streams(clean_root):
    configure_logging(logging.INFO)
    streams = {
        ("stdout" if h.stream is sys.stdout else "stderr"): h for h in clean_root.handlers
    }
    assert set(streams) == {"stdout", "stderr"}
    assert streams["stderr"].level == logging.ERROR


def test_a_real_error_never_lands_on_stdout(capsys, clean_root):
    configure_logging(logging.INFO)
    log = logging.getLogger("spiderbot.entrypoint_test")
    log.info("routine boot chatter")
    log.error("something actually broke")
    out, err = capsys.readouterr()

    assert "routine boot chatter" in out
    assert "routine boot chatter" not in err
    assert "something actually broke" in err
    assert "something actually broke" not in out, "an error on stdout reads as info"


def test_warnings_still_count_as_ordinary(capsys, clean_root):
    # discord.py's PyNaCl/voice warnings fire on every boot. They are noise,
    # not incidents, and must not be what an error filter surfaces.
    configure_logging(logging.INFO)
    logging.getLogger("spiderbot.entrypoint_test").warning("voice not supported")
    out, err = capsys.readouterr()
    assert "voice not supported" in out and err == ""


def test_configuring_twice_does_not_double_every_line(capsys, clean_root):
    configure_logging(logging.INFO)
    configure_logging(logging.INFO)
    assert len(clean_root.handlers) == 2
    logging.getLogger("spiderbot.entrypoint_test").info("once")
    out, _err = capsys.readouterr()
    assert out.count("once") == 1
