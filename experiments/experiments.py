# This is where the experiments live. Some continue to become features,
#   others may not have such a *future* ahead of them. Get it? Ha ha.

import discord
import asyncio
import random
import time
import logging
import youtube_dl
import subprocess
import requests
import datetime
import PIL
import io

from jshbot import data, utilities, configurations
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '¯\_(ツ)_/¯'
EXCEPTION = 'Experiments'
uses_configuration = True

print("Loaded experiments")


def get_commands():
    commands = []

    commands.append(Command(
        'zoop', SubCommands(
            ('^', '<stuff>', 'Help string here.')),
        shortcuts=Shortcuts(
            ('zap', '{}', '^', '<stuff>', '<stuff>'))))

    commands.append(Command(
        'test', SubCommands(
            ('order', 'order', 'This should have higher priority.'),
            ('shortcutone', 'shortcutone', ''),
            ('shortcuttwo', 'shortcuttwo', ''),
            ('^', '<stuff>', 'This changes a lot')),
        shortcuts=Shortcuts(
            ('short1', 'shortcutone', '', 'shortcutone', '')),
        description='Testing commands.', elevated_level=3, hidden=True,
        group='testing'))

    commands.append(Command(
        'test2', SubCommands(
            ('?args: :::::^', '(args <attached>) <arg1> <arg2> <the rest>',
             'Argument testing.'),
            ('nope: :#', 'nope <nope attached> <arg1> (arg2) (arg3) ...',
             'More argument testing'),
            ('junk stuff', 'junk stuff', 'Even more...'),
            ('final test please', 'final test please', 'blargh')),
        description='Testing commands 2: Electric Boogaloo.',
        elevated_level=3, group='testing'))

    commands.append(Command(
        'test3', SubCommands(('', '', '')), elevated_level=3, group='testing'))

    commands.append(Command(
        'timemasheen', SubCommands(
            ('^', '<dd/mm/yy>', 'Retrieves chat logs on the given day.')),
        description='Carter\'s time machine.', other='Nice meme!',
        group='testing'))

    commands.append(Command(
        'play', SubCommands(
            ('?file ^', '(file) <url or file>',
             'Plays the given URL or file.')),
        description='Plays stuff using youtube-dl. Neat.', allow_direct=False,
        group='testing'))

    commands.append(Command(
        'volume', SubCommands(
            ('&', '<volume>', 'Sets the volume of the experiments player.')),
        shortcuts=Shortcuts(
            ('volumeshortcut', '{}', '^', '<volume>', '<volume>')),
        other='Volume must be between 0.1 and 2.0', allow_direct=False,
        group='testing'))

    commands.append(Command(
        'rip', SubCommands(('^', '<thing>', 'Rips a thing.')), group='memes'))

    commands.append(Command(
        'nuke', SubCommands(
            ('^', '<number of messages>', 'Deletes the specified number of '
             'messages, including the authoring message.')),
        description='Deletes messages.', other='Be careful with this!',
        elevated_level=1, allow_direct=False, group='testing'))

    commands.append(Command(
        'wh', SubCommands((':&', '<"text"> (color)', 'Stuff.')),
        elevated_level=3, group='testing'))

    commands.append(Command(
        'embed', SubCommands(('&', '(embed dictionary)',
        'Tests an embed object.')), group='testing'))

    commands.append(Command(
        'animate', SubCommands(
            ('?level: ?gradual: ?tint: ?text: ^', '(level <intensity>) '
             '(gradual <duration>) (tint <color>) (text <text>) <url>', '')),
            group='testing'))

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

        elif blueprint_index == 3:  # general test
            response = await utilities.upload_to_discord(
                bot, open('{}/command_reference.txt'.format(bot.path)))

            '''
            elif blueprint_index == 3:  # that's pretty aesthetic mate
                response = ''
                for character in arguments[0]:
                    if character.isalnum() and ord(character) < 128:
                        response += chr(ord(character) + 65248)
                    elif character == ' ':
                        response += '　'
                    else:
                        response += character
                message_type = 4
                await utilities.notify_owners(
                    bot, "Somebody made {} aesthetic.".format(arguments[0]))
            '''

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
            # asyncio.ensure_future(exceptional(bot))
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

    elif base == 'wh':  # Webhooks
        import json
        webhook_url = "https://canary.discordapp.com/api/webhooks/249456349728079872/1wKqRf6fF5h8MkubiWrSZGjc6hi8r_DFzggSLETejUawMoWxbQCtTyMwTKoTndNuJtgW"
        '''
        sent_data = {
            "embeds": [
                {
                    "color": (int(arguments[1]) if arguments[1] else 16777215),
                    "description": arguments[0],
                    "image": {
                        "url": (
                            "https://cdn.discordapp.com/attachments/"
                            "220327974434504705/234558750860509186/result.png")
                    }
                }
            ]
        }
        '''
        sent_data = {'content': arguments[0]}
        headers = {'content-type': 'application/json'}
        r = requests.post(
            webhook_url, data=json.dumps(sent_data), headers=headers)
        bot.extra = r
        response = "POST sent"

    elif base == 'embed':  # Embedded message type
        if arguments[0]:  # Test embed object
            import ast
            try:
                test_arguments = ast.literal_eval(arguments[0])
            except Exception as e:
                raise BotException(EXCEPTION, "Not a valid dictionary.", e=e)

        else:
            test_arguments = {
                'title': (
                    ':arrow_forward: **[Track Title]**'),
                    'description': '`[|||||||||||||--------]` **[ 1:55 / 3:07 ]**'
            }
        response = discord.Embed(**test_arguments)
        # response.set_author(
        #     name='Someone', icon_url=bot.user.default_avatar_url)
        response.add_field(
            name='Added by <user>',
            value=(
                '10 minutes ago '
                '[(Link)](https://www.google.com "https://www.google.com")'))
        response.add_field(
            name='4 listener(s)', value='Votes to skip: 0/3')
        response.add_field(
            name='7 tracks (runtime of ...):',
            value=(
                '**`1`**: (3:00) Another track\n'
                '**`2`**: (3:00) Another track\n'
                '**`3`**: (3:00) Another track\n'
                '**`4`**: (3:00) Another track\n'
                '**`5`**: (3:00) Another track\n'
                '(2 more tracks | page 1/2)'),
            inline=False)
        response.add_field(
            name='Notification:',
            value=(
                'The previous track was cut short because it '
                'exceeded the cutoff threshold.'))
        # response.add_field(name='_', value='_', inline=False)
        # response.set_image(url='https://discordapp.com/assets/2c21aeda16de354ba5334551a883b481.png')
        response.set_thumbnail(url='http://i.imgur.com/HCw86rq.png')
        # response.set_footer(text='This is a test footer')

    elif base == 'animate':  # Animation test
        # response = await utilities.future(_animation_test, bot)
        url = arguments[0]
        kwargs = {}
        if 'level' in options:
            try:
                level = float(options['level'])
                if not 1 <= level <= 10:
                    raise ValueError
                kwargs['level'] = level
            except ValueError:
                raise BotException(
                    EXCEPTION, "Level must be between 1 and 10 inclusive.")
        if 'gradual' in options:
            try:
                duration = int(options['gradual'])
                if not 1 <= duration <= 3:
                    raise ValueError
                kwargs['duration'] = duration
                kwargs['gradual'] = True
            except ValueError:
                raise BotException(
                    EXCEPTION, "Gradual must be between 1 and 3 inclusive.")
        if 'tint' in options:
            try:
                clean_tint = options['tint'].replace('0x', '').replace('#', '')
                if len(clean_tint) == 6:
                    clean_tint += '80'
                tint_test = int(clean_tint, 16)
                if tint_test > 0xffffffff or len(clean_tint) != 8:
                    raise ValueError
                kwargs['tint'] = clean_tint
            except ValueError:
                raise BotException(
                    EXCEPTION, "Tint must be a (full-length RGBA) hex color.")
        if 'text' in options:
            if len(options['text']) > 30:
                raise BotException(
                    EXCEPTION, "Text must be less than 30 characters long.")
            kwargs['text'] = options['text']

        response = await _animation_test(bot, url, **kwargs)
        message_type = 5
        response.seek(0)
        extra = 'why.gif'

    else:
        response = "You forgot to set up the test command, you dummy!"

    return (response, tts, message_type, extra)


async def _animation_test(
        bot, url, level=3, duration=1, tint=None, text='', gradual=False):
    file_location, cleaned_name = await utilities.download_url(
        bot, url, include_name=True)
    try:
        base_image = PIL.Image.open(file_location).convert("RGBA")
    except Exception as e:
        raise BotException(EXCEPTION, "Failed to open the image.", e=e)
    base_size = base_image.size
    if max(base_size) > 200:  # Resize so largest dimension is 200px
        ratio = 200/max(base_size)
        base_image = base_image.resize(
            (int(ratio * base_size[0]), int(ratio * base_size[1])))
        base_size = base_image.size
    border_ratio = 1 - (0.5 * level / 10)
    border_size = (
        int(border_ratio * base_size[0]), int(border_ratio * base_size[1]))
    max_offset = (
        int((1 - border_ratio) * base_size[0]),
        int((1 - border_ratio) * base_size[1]))

    if tint is not None:
        tint = tuple(int(it0, 16) for it0 in [
            tint[it1:it1 + 2] for it1 in range(0, 8, 2)])

    def _composite_tint(original_tint, given_tint):
        ratio = given_tint[3]
        return tuple(
            max((int(a + b * ratio), 255)) for a,b in zip(
                original_tint, given_tint))

    def _process_images():
        frames = []
        ref_image = base_image
        offsets = max_offset
        preoffsets = (0, 0)
        dark_tint = (54, 57, 62, 255)
        tint_step = tint
        if text:
            font_directory = (
                bot.path + '/plugins/plugin_data/OpenSans-CondBold.ttf')
            font = PIL.ImageFont.truetype(font_directory, 20)
            text_dummy = PIL.ImageDraw.Draw(base_image)
            text_size = text_dummy.textsize(text, font=font)
            text_image = PIL.Image.new('RGBA', (text_size), (0,)*4)
            text_draw = PIL.ImageDraw.Draw(text_image)
            text_draw.text((0, 0), text, font=font, fill=(255,)*4)
            if (max(text_size) + 10) > max(border_size):
                ratio = max(border_size)/max(text_size)
                text_image = text_image.resize(
                    (int(ratio * text_size[0]), int(ratio * text_size[1])))
            text_size = text_image.size
            

        for it in range(duration * 50):
            if gradual:
                progress = it / (duration * 50)
                ratio = 0.5 + 0.5 * progress
                offsets = (
                    int(progress * max_offset[0]),
                    int(progress * max_offset[1]))
                ref_image = base_image.resize(
                    (int(ratio * base_size[0]), int(ratio * base_size[1])))
                preoffsets = (
                    int(border_size[0]/2 - (ref_image.size[0] - offsets[0])/2),
                    int(border_size[1]/2 - (ref_image.size[1] - offsets[1])/2))
                if tint is not None:
                    tint_step = tuple(int(it * progress) for it in tint)
            new_image = PIL.Image.new('RGBA', border_size, dark_tint)
            new_image.paste(ref_image,
                (
                    preoffsets[0] - random.randint(0, offsets[0]),
                    preoffsets[1] - random.randint(0, offsets[1])
                ), ref_image
            )
            if tint:
                tint_image = PIL.Image.new('RGBA', border_size, tint_step)
                new_image.paste(tint_image, (0, 0), tint_image)
            if text:
                text_alignment = (
                    int(border_size[0]/2 - (text_size[0])/2),
                    int(border_size[1] - text_size[1]))
                new_image.paste(text_image, text_alignment, text_image)

            frames.append(new_image)
        return frames
    
    frames = await utilities.future(_process_images)
    image_bytes = io.BytesIO()
    frames[0].save(
        image_bytes, 'gif',
        save_all=True, append_images=frames[1:],
        duration=20, optimize=True, loop=9999)
    utilities.delete_temporary_file(bot, cleaned_name)
    return image_bytes


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
    # bot.loop.close()
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


async def bot_on_ready_boot(bot):
    print("Started up fresh.")
