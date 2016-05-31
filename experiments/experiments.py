# This is where the experiments live. Some continue to become features,
#   others may not have such a *future* ahead of them. Get it? Ha ha.

import discord
import asyncio
import random
import time
import logging

import datetime

from jshbot import data
from jshbot.exceptions import BotException

__version__ = '¯\_(ツ)_/¯'
EXCEPTION = 'Experiments'
uses_configuration = False


def get_commands():

    commands = {}
    shortcuts = {}
    manual = {}

    commands['test'] = (['&'], [()])
    commands['timemasheen'] = (['^'], [()])
    commands['play'] = (['?file ^'], [()])
    commands['volume'] = (['&'], [()])
    commands['stfu'] = ([''], [()])

    return (commands, shortcuts, manual)

async def get_response(bot, message, parsed_command, direct):
    response = ''
    tts = False
    message_type = 0
    extra = None
    base, plan_index, options, arguments = parsed_command

    if base == 'test':
        if arguments:  # that's pretty aesthetic mate
            text = arguments.replace(' ', '').lower()
            response = ' '.join([char for char in text])

        else:  # asyncio testing
            long_future = bot.loop.run_in_executor(None, long_function)
            await long_future
            response = "Finished sleeping"

    elif base == 'timemasheen':  # carter's time masheen
        for delimiter in ('/', '.', '-'):
            if delimiter in arguments:
                break
        start_date = datetime.datetime.strptime(
            arguments, '%d{0}%m{0}%y'.format(delimiter))
        end_date = start_date + datetime.timedelta(days=1)
        await send_logs_as_file(bot, message.channel, start_date, end_date)
        message_type = 1

    elif base == 'play':  # ytdl stuff
        voice_channel = message.author.voice_channel
        if voice_channel:
            use_file = 'file' in options
            await play_this(
                bot, message.server, voice_channel, arguments, use_file)
            response = "Playing your stuff."
        else:
            raise BotException(EXCEPTION, "You're not in a voice channel.")

    elif base == 'stfu':  # stop voice stuff
        voice_connection = bot.voice_client_in(message.server)
        if voice_connection:
            await voice_connection.disconnect()
            response = "Was I too obnoxious? Sorry mate."
        else:
            raise BotException(EXCEPTION, "No audio is playing.")

    elif base == 'volume':  # change volume
        player = data.get(
            bot, __name__, 'voice_client',
            server_id=message.server.id, volatile=True)
        if player is None or not player.is_playing():
            raise BotException(EXCEPTION, "No audio is playing.")
        if arguments:
            volume = float(arguments)
            if volume < 0 or volume > 2:
                raise BotException(EXCEPTION, "Valid range is [0.0-2.0].")
            else:
                response = "Set volume to {:.1f}%".format(volume*100)
        else:
            volume = 1.0
            response = "Volume set to 100%"
        player.volume = volume

    return (response, tts, message_type, extra)


def long_function():
    time.sleep(10)


async def play_this(bot, server, voice_channel, location, use_file):
    if not bot.is_voice_connected(server):
        voice_connection = await bot.join_voice_channel(voice_channel)
    else:
        voice_connection = bot.voice_client_in(server)
    player = data.get(
        bot, __name__, 'voice_client', server_id=server.id, volatile=True)
    if player is not None and player.is_playing():
        player.stop()
    try:
        if use_file:
            player = voice_connection.create_ffmpeg_player(
                '{0}/audio/{1}'.format(bot.path, location))
        else:
            player = await voice_connection.create_ytdl_player(location)
    except Exception as e:
        raise BotException(EXCEPTION, "Something bad happened.", e=e)
    player.start()
    data.add(
        bot, __name__, 'voice_client', player,
        server_id=server.id, volatile=True)


async def send_logs_as_file(bot, channel, start_date, end_date):
    """Wrapper function for Carter's time machine."""
    messages = []
    large_text = ''
    async for message in bot.logs_from(
            channel, limit=20000, before=end_date, after=start_date):
        messages.append(message)
    for message in reversed(messages):
        if message.edited_timestamp:
            edited = ' (edited {})'.format(message.edited_timestamp)
        else:
            edited = ''
        if message.attachments:
            urls = []
            for attachment in message.attachments:
                urls.append(attachment['url'])
            attached = ' (attached {})'.format(urls)
        else:
            attached = ''
        text = ("{0.author.id} ({0.author.name}) at {0.timestamp}{1}{2}: "
                "\n\t{0.content}\n").format(message, edited, attached)
        large_text += text
    await bot.send_text_as_file(channel, large_text, 'carter')
