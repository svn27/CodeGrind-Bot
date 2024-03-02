import aiohttp
import asyncio
import random
import string

import discord
from beanie.odm.fields import WriteRules
from beanie.odm.operators.update.array import AddToSet, Pull
from bson import DBRef
from discord.ext import commands

from bot_globals import logger
from database.models.projections import IdProjection
from database.models.server_model import Server
from database.models.user_model import DisplayInformation, Scores, Submissions, User
from embeds.users_embeds import (
    account_not_found_embed,
    account_permanently_deleted_embed,
    account_removed_embed,
    connect_account_instructions_embed,
    profile_added_embed,
    synced_existing_user_embed,
    user_already_added_in_server_embed,
)
from middleware import defer_interaction, ensure_server_document, track_analytics
from middleware.database_middleware import update_user_preferences_prompt
from utils.common_utils import calculate_scores
from utils.questions_utils import get_problems_solved_and_rank
from utils.roles_utils import give_verified_role


class Users(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    @discord.app_commands.command(
        name="add",
        description="Connect your LeetCode account to this server"
    )
    @defer_interaction(ephemeral_default=True)
    @ensure_server_document
    @track_analytics
    async def add(self, interaction: discord.Interaction, leetcode_id: str) -> None:
        leetcode_username = leetcode_id

        logger.info(
            'file: cogs/users.py ~ add ~ run ~ leetcode_username: %s', leetcode_username)

        server_id = interaction.guild.id
        user_id = interaction.user.id

        display_information = DisplayInformation(
            server_id=server_id, name=interaction.user.display_name)

        user_exists = await User.find_one(User.id == user_id).project(IdProjection)

        if user_exists:
            await give_verified_role(interaction.user, interaction.guild.id)

            display_information_exists = await User.find_one(User.id == user_id, User.display_information.server_id == server_id)

            if display_information_exists:
                logger.info(
                    'file: cogs/users.py ~ add ~ user has already been added in the server ~ leetcode_username: %s', leetcode_username)

                # User has already been added in the server
                embed = user_already_added_in_server_embed()
                await interaction.followup.send(embed=embed)
                return

            else:
                logger.info(
                    'file: cogs/users.py ~ add ~ add user\' display information for this server ~ leetcode_username: %s', leetcode_username)

                # Add user's display information for this server
                await User.find_one(User.id == user_id).update(AddToSet({User.display_information: display_information}))
                await Server.find_one(Server.id == server_id).update(AddToSet({Server.users: DBRef("users", user_id)}))

                embed = synced_existing_user_embed()
                await interaction.followup.send(embed=embed)

        else:
            # Generate a random string for account linking
            generated_string = ''.join(
                random.choices(string.ascii_letters, k=8))

            embed = connect_account_instructions_embed(
                generated_string, leetcode_username)
            await interaction.followup.send(embed=embed)

            profile_name = None
            check_interval = 5  # seconds

            # Check if the profile name matches the generated string
            clientSession = aiohttp.ClientSession()
            for _ in range(60 // check_interval):
                stats = await get_problems_solved_and_rank(clientSession, leetcode_username)

                if not stats:
                    break

                rank = stats["profile"]["realName"]
                profile_name = stats["profile"]["realName"]

                if profile_name == generated_string:
                    break

                await asyncio.sleep(check_interval)
            await clientSession.close()

            if profile_name == generated_string:
                clientSession = aiohttp.ClientSession()
                stats = await get_problems_solved_and_rank(clientSession, leetcode_username)
                await clientSession.close()

                if not stats:
                    return

                rank = stats["profile"]["ranking"]
                easy = stats["submitStatsGlobal"]["acSubmissionNum"]["Easy"]
                medium = stats["submitStatsGlobal"]["acSubmissionNum"]["Medium"]
                hard = stats["submitStatsGlobal"]["acSubmissionNum"]["Hard"]

                total_score = calculate_scores(easy, medium, hard)

                submissions = Submissions(
                    easy=easy, medium=medium, hard=hard, total_score=total_score)

                scores = Scores(start_of_week_total_score=total_score,
                                start_of_day_total_score=total_score)

                user = User(id=user_id, leetcode_username=leetcode_username, rank=rank, display_information=[
                            display_information, DisplayInformation(server_id=0, name=interaction.user.name,)], submissions=submissions, scores=scores)

                await Server.find_one(Server.id == server_id).update(AddToSet({Server.users: user}))
                server = await Server.get(server_id)
                # link rule to create a new document for the new link
                await server.save(link_rule=WriteRules.WRITE)

                # Add to the global leaderboard.
                await Server.find_one(Server.id == 0).update(AddToSet({Server.users: DBRef("users", user_id)}))

                await give_verified_role(interaction.user, interaction.guild.id)

                logger.info(
                    'file: cogs/users.py ~ add ~ user has been added successfully ~ leetcode_username: %s', leetcode_username)

                embed = profile_added_embed(leetcode_username)
                await interaction.edit_original_response(embed=embed)
            else:
                logger.info(
                    'file: cogs/users.py ~ add ~ user has not been added ~ leetcode_username: %s', leetcode_username)

                embed = profile_added_embed(leetcode_username, added=False)
                await interaction.edit_original_response(embed=embed)
                return

        await update_user_preferences_prompt(interaction)

    @discord.app_commands.command(name="update", description="Update your profile on this server's leaderboards")
    @defer_interaction(ephemeral_default=True)
    @ensure_server_document
    @track_analytics
    async def update(self, interaction: discord.Interaction) -> None:
        logger.info(
            'file: cogs/users.py ~ update ~ run')

        user_id = interaction.user.id

        user = await User.find_one(User.id == user_id)

        if user:
            await update_user_preferences_prompt(interaction)
            return

        logger.info(
            'file: cogs/users.py ~ remove ~ profile not found in this server ~ user_id: %s', user_id)

        embed = account_not_found_embed()
        await interaction.followup.send(embed=embed)

    @discord.app_commands.command(name="remove", description="Remove your profile from this server's leaderboard")
    @defer_interaction(ephemeral_default=True)
    @ensure_server_document
    @track_analytics
    async def remove(self, interaction: discord.Interaction, permanently_delete: bool = False) -> None:
        logger.info(
            'file: cogs/users.py ~ remove ~ run')

        server_id = interaction.guild.id
        user_id = interaction.user.id

        logger.info(
            'file: cogs/users.py ~ remove ~ user_id: %s', user_id)

        user_exists = await User.find_one(User.id == user_id).project(IdProjection)

        if user_exists:
            # delete the user
            if permanently_delete:
                await Server.find_one(Server.id == server_id).update(Pull({Server.users: {"$id": user_id}}))
                # Global leaderboard
                # TODO: create global_leaderboard_id enum
                await Server.find_one(Server.id == 0).update(Pull({Server.users: {"$id": user_id}}))
                await User.find_one(User.id == user_id).delete()
                embed = account_permanently_deleted_embed()
                await interaction.followup.send(embed=embed)
                return

            # unlink user from server(interaction, user_id)
            # TODO: this is getting the entire user.
            display_information = await User.find_one(
                User.id == user_id, User.display_information.server_id == server_id)

            if display_information:
                await User.find_one(
                    User.id == user_id).update(Pull({User.display_information: {"server_id": server_id}}))

                await Server.find_one(Server.id == server_id).update(Pull({Server.users: {"$id": user_id}}))

                logger.info(
                    'file: cogs/users.py ~ remove ~ profile removed - references removed from User and Server documents ~ user_id: %s', user_id)

                embed = account_removed_embed()
                await interaction.followup.send(embed=embed)
                return

        logger.info(
            'file: cogs/users.py ~ remove ~ profile not found in this server ~ user_id: %s', user_id)

        embed = account_not_found_embed()
        await interaction.followup.send(embed=embed)


async def setup(client: commands.Bot):
    await client.add_cog(Users(client))
