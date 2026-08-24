"""Entry point: python -m spiderbot"""

import logging

from spiderbot import config
from spiderbot.bot import SpiderBot


def main() -> None:
    cfg = config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("spiderbot").info("starting with %r", cfg)
    bot = SpiderBot(cfg)
    bot.run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
