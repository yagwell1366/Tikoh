import os
import logging
import json

import discord
from discord.ext import commands
from isolation import IsolationCog


# =========================
# Configuration loading
# =========================
def load_config() -> dict:
    """Load configuration from config.json file."""
    config_file = "config.json"
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file '{config_file}' not found. Please create it with your bot settings.")
    
    try:
        with open(config_file, "r", encoding="utf-8") as file:
            config = json.load(file)
            return config
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to load config file: {e}")


# Load configuration
try:
    config = load_config()
    TOKEN = config.get("token")
    OWNER_ID = config.get("owner_id")
    STAFF_CHANNEL_ID = config.get("staff_channel_id")
    COMMAND_PREFIXES = config.get("command_prefixes", ["!", "."])
    LOG_LEVEL = config.get("log_level", "INFO")
except Exception as e:
    print(f"Configuration error: {e}")
    exit(1)


# =========================
# Intents for moderation setup
# =========================
# Default intents are safe; enable specific ones needed for moderation workflows
intents = discord.Intents.default()
intents.guilds = True            # Access guild-related events
intents.members = True           # Required for member join/leave and moderation context
intents.message_content = True   # Needed if the bot reads command text or message content


# =========================
# Bot initialization
# =========================
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO))
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(*COMMAND_PREFIXES),  # support multiple prefixes
    intents=intents,
)


@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")
    logging.info("Connected to %d guild(s)", len(bot.guilds))
    logging.info("Staff channel ID: %s", STAFF_CHANNEL_ID)
    logging.info("Owner ID: %s", OWNER_ID)


@bot.event
async def setup_hook():
    # Register cogs/extensions here
    await bot.add_cog(IsolationCog(bot, owner_id=OWNER_ID, staff_channel_id=STAFF_CHANNEL_ID))


def main() -> None:
    if not TOKEN:
        logging.error("No token found in config.json")
        exit(1)
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logging.error("Invalid token. Please check your token in config.json")
        exit(1)
    except Exception as e:
        logging.error(f"Failed to start bot: {e}")
        exit(1)


if __name__ == "__main__":
    main()


