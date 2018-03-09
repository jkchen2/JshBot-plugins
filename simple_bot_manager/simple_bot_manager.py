import discord
import random

from jshbot import data, utilities, configurations, logger, plugins
from jshbot.exceptions import BotException, ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
CBException = ConfiguredBotException('Simple bot manager')
uses_configuration = False


@plugins.command_spawner
def get_commands(bot):
    new_commands = []

    new_commands.append(Command(
        'botman', subcommands=[
            SubCommand(
                Opt('change'), Opt('avatar', optional=True), Opt('status', optional=True),
                doc='Changes the avatar, status, or both from the list files.'),
            SubCommand(
                Opt('nick'), Arg('nickname', argtype=ArgTypes.MERGED_OPTIONAL, default=None),
                doc='Sets or clears the nickname for the current server.',
                elevated_level=1, allow_direct=False),
            SubCommand(
                Opt('name'), Arg('name', argtype=ArgTypes.MERGED),
                doc='Sets the bot\'s name.'),
            SubCommand(
                Opt('status'), Arg('text', argtype=ArgTypes.MERGED_OPTIONAL, default=None),
                doc='Sets or clears the bot\'s "now playing" message.'),
            SubCommand(
                Opt('avatar'), Arg('url', argtype=ArgTypes.MERGED_OPTIONAL),
                doc='Sets or clears the bot\'s avatar.')],
        description='Change simple bot stuff, like the avatar and status.',
        hidden=True, elevated_level=3, category='bot utilities', function=get_response))

    return new_commands


async def _change_avatar(bot, url=None):
    try:
        if url:
            avatar_bytes = (await utilities.download_url(bot, url, use_fp=True)).getvalue()
        else:
            avatar_bytes = None
        await bot.user.edit(avatar=avatar_bytes)
    except Exception as e:
        raise CBException("Failed to update the avatar.", e=e)


async def get_response(bot, context):

    if context.index == 0:  # Change avatar, status, or both
        if len(context.options) == 0:
            raise CBException("Either the avatar, status, or both options must be used.")
        if 'avatar' in context.options:
            text = configurations.get(bot, __name__, extra='avatars', extension='txt')
            url = random.choice(text.splitlines()).rstrip()
            await _change_avatar(bot, url=url)
        if 'status' in context.options:
            text = configurations.get(bot, __name__, extra='statuses', extension='txt')
            status = random.choice(text.splitlines()).rstrip()
            try:
                await bot.change_presence(activity=discord.Game(name=status))
            except Exception as e:
                raise CBException("Failed to update the status.", e=e)

    elif context.index == 1:  # Change nickname
        try:
            await context.guild.me.edit(nick=context.arguments[0])
        except Exception as e:
            raise CBException("Failed to change the nickname.", e=e)

    elif context.index == 2:  # Change name
        if len(context.arguments[0]) > 20:
            raise CBException("Name is longer than 20 characters.")
        try:
            await bot.user.edit(username=context.arguments[0])
        except Exception as e:
            raise CBException("Failed to update the name.", e=e)

    elif context.index == 3:  # Change status
        try:
            if context.arguments[0]:
                activity = discord.Game(name=context.arguments[0])
            else:
                activity = None
            await bot.change_presence(activity=activity)
            data.add(bot, __name__, 'status', context.arguments[0])
        except Exception as e:
            raise CBException("Failed to update the status.", e=e)

    elif context.index == 4:  # Change avatar
        await _change_avatar(bot, url=context.arguments[0])

    return Response(content="Bot updated.")


@plugins.listen_for('bot_on_ready_boot')
async def set_status_on_boot(bot):
    """Checks to see if the status was set previously."""
    previous_status = data.get(bot, __name__, 'status')
    if previous_status:
        await bot.change_presence(activity=discord.Game(name=previous_status))
        logger.info("Detected old status - setting it now!")
