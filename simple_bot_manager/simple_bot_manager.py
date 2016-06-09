import discord
import urllib.request
import random

from jshbot import utilities, configurations
from jshbot.commands import Command, SubCommands
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Simple bot manager'
uses_configuration = False


def get_commands():
    commands = []

    commands.append(Command(
        'botman', SubCommands(
            ('change ?avatar ?status', 'change (avatar) (status)', 'Changes '
             'the avatar, status, or both from the config file.'),
            ('nick &', 'nick <nickname>', 'Sets or clears the nickname.'),
            ('name ^', 'name <name>', 'Changes the bot\'s name.'),
            ('status &', 'status <text>', 'Changes the status to the text.'),
            ('avatar &', 'avatar <url>', 'Changes the avatar to the URL.')),
        description='Change simple bot stuff, like the avatar and status.',
        other='Only bot moderators can use these commands', elevated_level=3))

    return commands


async def change_avatar(bot, url=None):
    """Clears or sets the avatar if the URL is given."""
    try:
        if url:
            avatar = await utilities.future(urllib.request.urlopen, url)
            avatar_bytes = avatar.read()
        else:
            avatar_bytes = None
        await bot.edit_profile(bot.get_token(), avatar=avatar_bytes)
    except Exception as e:
        raise BotException(EXCEPTION, "Failed to update the avatar.", e=e)


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    response = "Bot stuff updated!"

    if blueprint_index == 0:  # Change avatar, status, or both

        if len(options) == 0:
            raise BotException(
                EXCEPTION,
                "Either the avatar, status, or both flags must be used.")

        if 'avatar' in options:
            text = configurations.get(
                bot, __name__, extra='avatars', extension='txt')
            url = random.choice(text.splitlines()).rstrip()
            await change_avatar(bot, url=url)
        if 'status' in options:
            text = configurations.get(
                bot, __name__, extra='statuses', extension='txt')
            status = random.choice(text.splitlines()).rstrip()
            try:
                await bot.change_status(discord.Game(name=status))
            except Exception as e:
                raise BotException(
                    EXCEPTION, "Failed to update the status.", e=e)

    elif blueprint_index == 1:  # Change nickname
        if not message.channel.is_private:
            try:
                await bot.change_nickname(
                    message.server.me, arguments[0] if arguments[0] else None)
            except Exception as e:
                raise BotException(
                    EXCEPTION, "Failed to change the nickname.", e=e)
        else:
            response = "Cannot change nickname in a direct message."
    elif blueprint_index == 2:  # Change name
        if len(arguments[0]) > 20:
            raise BotException(EXCEPTION, "Name is longer than 20 characters.")
        try:
            await bot.edit_profile(bot.get_token(), username=arguments[0])
        except Exception as e:
            raise BotException(EXCEPTION, "Failed to update the name.", e=e)
    elif blueprint_index == 3:  # Change status
        try:
            await bot.change_status(
                discord.Game(name=arguments[0]) if arguments[0] else None)
        except Exception as e:
            raise BotException(EXCEPTION, "Failed to update the status.", e=e)
    elif blueprint_index == 4:  # Change avatar
        await change_avatar(bot, arguments[0])

    return (response, tts, message_type, extra)
