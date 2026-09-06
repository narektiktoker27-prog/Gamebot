import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///last_night.db")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set. "
        "Get a token from @BotFather and set it as an environment variable "
        "(never hardcode it in source code)."
    )

MIN_PLAYERS = 6
MAX_PLAYERS = 20
NIGHT_SECONDS = 90
DISCUSSION_SECONDS = 180
VOTE_SECONDS = 60
RESCUE_ROUNDS = 6  # how many rounds until the rescue team arrives
