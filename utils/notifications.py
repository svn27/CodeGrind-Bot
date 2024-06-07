import asyncio
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

import discord
from beanie.odm.operators.update.general import Set
from discord.ext import tasks

from constants import GLOBAL_LEADERBOARD_ID, Period
from database.models import Server, User
from ui.embeds.problems import daily_question_embed
from utils.leaderboards import send_leaderboard_winners
from utils.roles import update_roles
from utils.stats import update_stats

if TYPE_CHECKING:
    # To prevent circular imports
    from bot import DiscordBot


@tasks.loop(
    time=[time(hour=hour, minute=minute) for hour in range(24) for minute in [0, 30]],
    reconnect=False,
)
async def schedule_question_and_stats_update(bot: "DiscordBot") -> None:
    """
    Schedule to send the daily question and update the stats.
    """
    await process_daily_question_and_stats_update(bot)


async def process_daily_question_and_stats_update(
    bot: "DiscordBot",
    update_stats: bool = True,
    force_reset_day: bool = False,
    force_reset_week: bool = False,
    force_reset_month: bool = False,
) -> None:
    """
    Send the daily question and update the stats.

    :param update_stats: Whether to update the users stats.
    :param force_reset_day: Whether to force the daily reset.
    :param force_reset_week: Whether to force the weekly reset.
    :param force_reset_month: Whether to force the monthly reset.
    """
    bot.logger.info("Sending daily notifications and updating stats started")
    await bot.channel_logger.info("Started updating")

    start = datetime.now(UTC)

    reset_day = (start.hour == 0 and start.minute == 0) or force_reset_day
    reset_week = (
        start.weekday() == 0 and start.hour == 0 and start.minute == 0
    ) or force_reset_week
    reset_month = (
        start.day == 1 and start.hour == 0 and start.minute == 0
    ) or force_reset_month

    midday = start.hour == 12 and start.minute == 0

    if reset_day:
        embed = await daily_question_embed(bot)

        async for server in Server.all(fetch_links=True):
            await send_daily_question(bot, server, embed)

        bot.logger.info("Daily question sent to all servers")

    if update_stats:
        await update_all_user_stats(bot, reset_day)

    async for server in Server.all():
        await Server.find_one(Server.id == server.id).update(
            Set(
                {
                    Server.last_update_start: start,
                    Server.last_update_end: datetime.now(UTC),
                }
            )
        )

        if server.id == GLOBAL_LEADERBOARD_ID:
            continue

        if reset_day:
            await send_leaderboard_winners(bot, server, Period.DAY)

        if reset_week:
            await send_leaderboard_winners(bot, server, Period.WEEK)

        if reset_month:
            await send_leaderboard_winners(bot, server, Period.MONTH)

        if midday:
            if guild := bot.get_guild(server.id):
                try:
                    await update_roles(guild, server.id)
                except discord.errors.Forbidden:
                    # Missing permissions are handled inside update_roles, so it
                    # shouldn't raise an error.
                    bot.logger.info(
                        f"Forbidden to add roles to members of server with ID: "
                        f"{server.id}"
                    )

    bot.logger.info("Sending daily notifications and updating stats completed")
    await bot.channel_logger.info("Completed updating", include_error_counts=True)


async def send_daily_question(
    bot: "DiscordBot", server: Server, embed: discord.Embed
) -> None:
    """
    Send the daily question to the server's daily question channels.

    :param server: The server to send the daily question to (with links fetched).
    :param embed: The embed containing the daily question.
    """
    for channel_id in server.channels.daily_question:
        channel = bot.get_channel(channel_id)

        if not channel or not isinstance(channel, discord.TextChannel):
            continue

        try:
            await channel.send(embed=embed, silent=True)
        except discord.errors.Forbidden:
            bot.logger.info(
                f"Forbidden to share daily question to channel with ID: "
                f"{channel_id}"
            )


async def update_all_user_stats(bot: "DiscordBot", reset_day: bool = False) -> None:
    """
    Update stats for all users.
    """
    counter = 0

    tasks = []
    async for user in User.all():
        task = asyncio.create_task(update_stats(bot, user, reset_day))
        tasks.append(task)

    total_users = len(tasks)
    for completed_task in asyncio.as_completed(tasks):
        await completed_task
        counter += 1
        if counter % 100 == 0 or counter == total_users:
            bot.logger.info(f"{counter} / {total_users} users stats updated")

    bot.logger.info("All users stats updated")
