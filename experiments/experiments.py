# This is where the experiments live. Some continue to become features,
#   others may not have such a *future* ahead of them. Get it? Ha ha.

import discord
import asyncio
import random
import time
import logging
import youtube_dl

import datetime

from jshbot import data, utilities, configurations
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '¯\_(ツ)_/¯'
EXCEPTION = 'Experiments'
uses_configuration = True


def get_commands():
    commands = []
    commands.append(Command(
        'test', SubCommands(
            ('order', 'order', 'This should have higher priority.'),
            ('shortcutone', 'shortcutone', ''),
            ('shortcuttwo', 'shortcuttwo', ''),
            ('^', '(aesthetic)', 'Make text aesthetic. Or do the test '
             'function. Up to you, really.')),
        shortcuts=Shortcuts(
            ('short1', 'shortcutone', '', 'shortcutone', '')),
        description='Testing commands.', elevated_level=3, hidden=True))

    commands.append(Command(
        'test2', SubCommands(
            ('?args: :::::^', '(args <attached>) <arg1> <arg2> <the rest>',
             'Argument testing.'),
            ('nope: :#', 'nope <nope attached> <arg1> (arg2) (arg3) ...',
             'More argument testing'),
            ('junk stuff', 'junk stuff', 'Even more...'),
            ('final test please', 'final test please', 'blargh')),
        description='Testing commands 2: Electric Boogaloo.',
        elevated_level=3))

    commands.append(Command(
        'test3', SubCommands(('', '', '')), elevated_level=3))

    commands.append(Command(
        'timemasheen', SubCommands(
            ('^', '<dd/mm/yy>', 'Retrieves chat logs on the given day.')),
        description='Carter\'s time machine.', other='Nice meme!'))

    commands.append(Command(
        'play', SubCommands(
            ('?file ^', '(file) <url or file>',
             'Plays the given URL or file.')),
        description='Plays stuff using youtube-dl. Neat.', allow_direct=False))

    commands.append(Command(
        'volume', SubCommands(
            ('&', '<volume>', 'Sets the volume of the experiments player.')),
        shortcuts=Shortcuts(
            ('volumeshortcut', '{}', '^', '<volume>', '<volume>')),
        other='Volume must be between 0.1 and 2.0', allow_direct=False))

    commands.append(Command(
        'rip', SubCommands(('^', '<thing>', 'Rips a thing.'))))

    commands.append(Command(
        'nuke', SubCommands(
            ('^', '<number of messages>', 'Deletes the specified number of '
             'messages, including the authoring message.')),
        description='Deletes messages.', other='Be careful with this!',
        elevated_level=1, allow_direct=False))

    return commands


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if base == 'test':

        if blueprint_index == 0:  # order
            response = "Anarchy!"*2000

        elif blueprint_index == 1:  # shortcut 1
            response = "You reached shortcut 1!"

        elif blueprint_index == 2:  # shortcut 2
            response = "You reached shortcut 2!"

        elif blueprint_index == 3:  # that's pretty aesthetic mate
            text = arguments[0].replace(' ', '').lower()
            response = ' '.join([char for char in text])
            await utilities.notify_owners(
                bot, "Somebody made {} aesthetic.".format(arguments[0]))

        else:  # asyncio testing
            long_future = bot.loop.run_in_executor(None, long_function)
            await long_future
            response = "Finished sleeping"

    elif base == 'test2':
        if blueprint_index == 0:
            if 'args' in options:
                response = "Args was included: {}\n".format(options['args'])
            response += str(arguments)
        elif blueprint_index == 1:
            response = "Nope\n"
            response += str(options) + '\n'
            response += str(arguments)
        elif blueprint_index == 2:
            asyncio.ensure_future(exceptional(bot))
            assert False
        else:
            response = str(options) + '\n'
            response += str(arguments) + '\n'
            response = "Blah blah, empty response."

    elif base == 'test3':
        raise BotException(EXCEPTION, "Blah", 1, 2, 3, True)
        response = "Called!"

    elif base == 'rip':
        response = get_rip(arguments[0])

    elif base == 'nuke':
        if not data.is_owner(bot, message.author.id):
            raise BotException(
                EXCEPTION, "Can't nuke unless you're the owner.")
        limit = int(arguments[0]) + 1
        await bot.purge_from(message.channel, limit=limit)

    elif base == 'timemasheen':  # carter's time masheen
        for delimiter in ('/', '.', '-'):
            if delimiter in arguments[0]:
                break
        start_date = datetime.datetime.strptime(
            arguments[0], '%d{0}%m{0}%y'.format(delimiter))
        end_date = start_date + datetime.timedelta(days=1)
        log_text = await utilities.get_log_text(
            bot, message.channel, limit=20000,
            before=end_date, after=start_date)
        await utilities.send_text_as_file(
            bot, message.channel, log_text, 'carter')
        message_type = 1

    elif base == 'play':  # ytdl stuff
        voice_channel = message.author.voice_channel
        if voice_channel:
            use_file = 'file' in options
            await play_this(
                bot, message.server, voice_channel, arguments[0], use_file)
            response = "Playing your stuff."
        else:
            raise BotException(EXCEPTION, "You're not in a voice channel.")

    elif base == 'volume':  # change volume
        player = utilities.get_player(bot, message.server.id)
        if arguments[0]:
            volume = float(arguments[0])
            if volume < 0 or volume > 2:
                raise BotException(EXCEPTION, "Valid range is [0.0-2.0].")
            else:
                response = "Set volume to {:.1f}%".format(volume*100)
        else:
            volume = 1.0
            response = "Volume set to 100%"
        data.add(bot, __name__, 'volume', volume, server_id=message.server.id)
        if player:
            player.volume = volume

    else:
        response = "You forgot to set up the test command, you dummy!"

    return (response, tts, message_type, extra)


def get_rip(name):
    rip_messages = [
        'rip {}',
        'you will be missed, {}',
        'rip in pizza, {}',
        'press f to pay respects to {}',
        '{} will be in our hearts',
        '{} didn\'t stand a chance',
        '{} is kill',
        '{} got destroyed',
        '{} got rekt',
        '{} got noscoped',
        'it is sad day, as {} has been ripped',
        '{} got tactical nuked',
        '{} couldn\'t handle the mlg'
    ]
    return random.choice(rip_messages).format(name)


def long_function():
    time.sleep(10)


async def exceptional(bot):
    await asyncio.sleep(5)
    bot.loop.close()
    raise Exception('ded')


async def play_this(bot, server, voice_channel, location, use_file):
    voice_client = await utilities.join_and_ready(bot, voice_channel)
    try:
        if use_file:
            player = voice_client.create_ffmpeg_player(
                '{0}/audio/{1}'.format(bot.path, location))
        else:
            player = await voice_client.create_ytdl_player(location)
    except Exception as e:
        raise BotException(EXCEPTION, "Something bad happened.", e=e)
    volume = data.get(
        bot, __name__, 'volume', server_id=server.id, default=1.0)
    player.volume = volume
    print(player.is_done())
    player.start()
    utilities.set_player(bot, server.id, player)


bad = ['none', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
       'nine']

async def on_member_update(bot, before, after):
    use_changer = configurations.get(bot, __name__, 'use_channel_change')
    if not use_changer:
        return
    if (before.server.id == '98336902637195264' and
            bot.user.id == '176088256721453056'):
        total_online = len(list(filter(
            lambda m: str(m.status) != 'offline', before.server.members))) - 1
        previous = data.get(bot, __name__, 'online', default=0)
        if total_online >= 0 and total_online != previous:
            channel = data.get_channel(bot, '98336902637195264', before.server)
            if total_online >= len(bad):
                remaining = "way too many"
            else:
                remaining = bad[total_online]
            text = "And then there {0} {1}.".format(
                'was' if total_online == 1 else 'were', remaining)
            data.add(bot, __name__, 'online', total_online)
            await bot.edit_channel(channel, topic=text)
