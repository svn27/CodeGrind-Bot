import io
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
import requests

from constants import StatsCardExtensions
from database.models import Record, Submissions, User
from utils.common import to_thread
from utils.problems import fetch_problems_solved_and_rank

if TYPE_CHECKING:
    # To prevent circular imports
    from bot import DiscordBot


async def update_stats(
    bot: "DiscordBot",
    user: User,
    reset_day: bool = False,
) -> None:
    """
    Update a user's problem-solving statistics and optionally store them as a record.

    This function fetches updated statistics for a user, assigns the new values to the
    user's submission statistics, and optionally creates a record with the updated
    stats.

    :param user: The user whose stats are being updated.
    :param reset_day: If `True`, a new record is created and stored with the updated
    stats.
    """

    stats = await fetch_problems_solved_and_rank(bot, user.leetcode_id)
    if not stats:
        return

    user = await User.find_one(User.id == user.id)
    if not user:
        return

    (
        user.stats.submissions.easy,
        user.stats.submissions.medium,
        user.stats.submissions.hard,
        user.stats.submissions.score,
    ) = (
        stats.submissions.easy,
        stats.submissions.medium,
        stats.submissions.hard,
        stats.submissions.score,
    )

    if reset_day:
        record = Record(
            timestamp=datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            ),
            user_id=user.id,
            submissions=Submissions(
                easy=stats.submissions.easy,
                medium=stats.submissions.medium,
                hard=stats.submissions.hard,
                score=stats.submissions.score,
            ),
        )

        await record.create()

    user.last_updated = datetime.now(UTC)
    await user.save()


@to_thread
def stats_card(
    bot: "DiscordBot",
    leetcode_id: str,
    extension: StatsCardExtensions,
) -> tuple[discord.File | None]:
    width = 500
    height = 200
    if extension in (StatsCardExtensions.ACTIVITY, StatsCardExtensions.CONTEST):
        height = 400
    elif extension == StatsCardExtensions.HEATMAP:
        height = 350

    url = f"""https://leetcard.jacoblin.cool/{leetcode_id}?theme=dark&animation=false&
    width={width}&height={height}&ext={extension.value}"""

    # Making sure the website is reachable before running hti.screenshot()
    # as the method will stall if url isn't reachable.
    try:
        response = requests.get(url)
        response.raise_for_status()

    except requests.exceptions.RequestException:
        return

    paths = bot.html2image.screenshot(url=url, size=(width, height))

    with open(paths[0], "rb") as f:
        # read the file contents
        data = f.read()
        # create a BytesIO object from the data
        image_binary = io.BytesIO(data)
        # move the cursor to the beginning
        image_binary.seek(0)

        file = discord.File(fp=image_binary, filename=f"{leetcode_id}.png")

    os.remove(paths[0])

    return file
