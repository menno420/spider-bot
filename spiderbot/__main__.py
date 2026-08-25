"""Entry point: python -m spiderbot"""

import logging
import sys

from spiderbot import config
from spiderbot.bot import SpiderBot

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: int) -> None:
    """Split the log streams so the host's severity matches reality.

    `logging.basicConfig` sends everything to stderr, and Railway tags every
    stderr line as an error - so routine boot chatter and a genuine crash look
    identical in the log view, and nothing stands out when something breaks.
    Below ERROR goes to stdout, ERROR and above to stderr.

    The audit trail is unaffected: `audit.stdout_event` prints JSON straight to
    stdout, which Railway parses into structured fields.
    """
    formatter = logging.Formatter(LOG_FORMAT)

    ordinary = logging.StreamHandler(sys.stdout)
    ordinary.setFormatter(formatter)
    ordinary.addFilter(lambda record: record.levelno < logging.ERROR)

    problems = logging.StreamHandler(sys.stderr)
    problems.setFormatter(formatter)
    problems.setLevel(logging.ERROR)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()  # idempotent: never stack a second pair of handlers
    root.addHandler(ordinary)
    root.addHandler(problems)


def main() -> None:
    cfg = config.load()
    configure_logging(getattr(logging, cfg.log_level.upper(), logging.INFO))
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("spiderbot").info("starting with %r", cfg)
    bot = SpiderBot(cfg)
    bot.run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
