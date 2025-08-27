import os
import logging

import discord
from discord.ext import commands
from isolation import IsolationCog


# =========================
# Configuration placeholders
# =========================
# Set your Discord application token here, or via the TIKO_HELPER_TOKEN env var
TOKEN = os.getenv("TIKO_HELPER_TOKEN", "MTQxMDI2NzE3OTA1MTY0NzExOQ.Gso5gv.0plpAtOxt0NgQE0OcNhSBMD5DZyPrsiHtkI2ls")

# Server owner ID placeholder (used to gate certain commands to the guild owner)
OWNER_ID = int(os.getenv("TIKO_HELPER_OWNER_ID", "887330488593842177"))

# Placeholder for a staff-only channel we will use later
# Replace with the actual numeric channel ID when ready
STAFF_CHANNEL_ID = 1349774308309733397


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
logging.basicConfig(level=logging.INFO)
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!", "."),  # support both '!' and '.'
    intents=intents,
)


@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")
    logging.info("Connected to %d guild(s)", len(bot.guilds))
    # Placeholder usage for staff channel (not used yet)
    logging.info("Staff channel placeholder: %s", STAFF_CHANNEL_ID)


@bot.event
async def setup_hook():
    # Register cogs/extensions here
    await bot.add_cog(IsolationCog(bot, owner_id=OWNER_ID))


def main() -> None:
    if not TOKEN or TOKEN == "PUT_YOUR_TOKEN_HERE":
        logging.warning(
            "No token set. Please set TOKEN in Main.py or the TIKO_HELPER_TOKEN environment variable."
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()


