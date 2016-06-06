import random
import time
import logging
import os

from itertools import groupby
from operator import itemgetter
from youtube_dl import YoutubeDL
from tinytag import TinyTag

from jshbot import data, utilities
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Tag demo'
uses_configuration = True

flag_list = ['Sound', 'Private', 'NSFW', 'Complex', 'Random']
simple_flag_list = list(map(str.lower, flag_list))


def get_commands():

    commands = {}
    shortcuts = {}
    manual = {}

    commands['tag'] = ([
        'create: ?private ?sound ?nsfw ?complex ^',
        'create: random ?private ?sound ?nsfw ?complex :+', 'remove ^',
        'raw ?file ^', 'info ^', 'edit: ?add: ?remove: ?volume: ?nsfw &',
        'list ?sound ?server: &', 'search ^', 'toggle ::', '?text ?sound ^'], [
        ('create', 'c'), ('private', 'p'), ('sound', 's'), ('random', 'rand'),
        ('remove', 'r'), ('info', 'i'), ('edit', 'e'), ('add', 'a'),
        ('list', 'l'), ('text', 't')])

    shortcuts['tc'] = ('tag -create {}', '^')
    shortcuts['tr'] = ('tag -remove {}', '^')
    shortcuts['t'] = ('tag {}', '^')
    shortcuts['tl'] = ('tag -list {}', '&')
    shortcuts['ts'] = ('tag -search {}', '^')
    shortcuts['stc'] = ('tag -create {} -sound {}', ':^')

    manual['tag'] = {
        'description': 'Proof of concept tags using the data framework.',
        'usage': [
            ('(-text) (-sound) <tag name>', 'Retrieves the given tag. If it '
                'is a sound tag, it plays the tag in the voice channel you '
                'are in. If a tag has both sound and text components, you can '
                'specify which one you want with the options. By default, it '
                'attempts to do both if they exist.'),
            ('-create <tag name> (-private) (-sound) (-nsfw) (-complex) '
                '<tag text>', 'Creates a tag that follows the given options. '
                'A private tag can only be called by its owner or moderators, '
                'and a sound tag is played in the voice channel of the '
                'message author.'),
            ('-create <tag name> -random (-private_ (-sound) <selection 1> '
                '<selection 2> (selection 3) (...)', 'Works the same as the '
                'regular create command, but will select a random selection '
                'when the tag is called.'),
            ('-remove <tag name>', 'Removes the specified tag. You must the '
                'tag owner or a moderator.'),
            ('-raw (-file) <tag name>', 'Gets the raw tag data. Useful for '
                'figuring out what is inside a random tag. If the file option '
                'is included, it will send the contents as a text file.'),
            ('-info (tag name)', 'Gets basic tag information. If a tag is not '
                'specified, it will get tag statistics for the server.'),
            ('-edit <tag name> (-add <new selection>) (-remove '
                '<old selection>) (-volume <new volume>) (-nsfw) '
                '(modified tag text)', 'Edits the given tag given the '
                'modified tag text. If you are editing a random tag, use the '
                'add or remove options to manipulate the random list. '
                'Including the NSFW flag toggles the flag as NSFW. If this is '
                'a sound tag, the new volume will be applied to it if '
                'specified.'),
            ('-list (user)', 'Lists all tags, or if a user is specified, only '
                'tags made by that user.'),
            ('-search <text>', 'Searches all tags for the given text.'),
            ('-toggle <channel name> <type>', 'Toggles the channel\'s tag '
                'filter settings. <type> must be "all" or "nsfw". <channel '
                'name> can be either a text or voice channel.')],
        'shortcuts': [
            ('t <arguments>', 'tag <arguments>'),
            ('tc <arguments>', 'tag -create <arguments>'),
            ('tr <tag name>', 'tag -remove <tag name>'),
            ('tl (user)', 'tag -list (user)'),
            ('ts <text>', 'tag -search <text>'),
            ('stc <tag name> <tag url>',
                'tag -create <tag name> -sound <tag url>')],
        'other': 'This is just proof of concept. Nothing is final yet.'}

    return (commands, shortcuts, manual)

async def create_tag(
        bot, tag_database, tag_name, database_name,
        author_id, server_id, options, text, is_random):
    """Creates a tag based on the given parameters.

    If it is a tag with sound, it will check that the sound length is no
    longer than the limit.
    """
    length_limit = bot.configurations[__name__]['max_tag_name_length']
    default_max_tags = bot.configurations[__name__]['max_tags_per_server']
    server_settings = data.get(
        bot, __name__, 'settings', server_id=server_id, default={})
    tag_limit = server_settings.get('max_tags', default_max_tags)
    if tag_limit > default_max_tags:  # Safety tag limit
        tag_limit = default_max_tags

    # Check for issues
    if len(tag_name) > length_limit:
        raise BotException(
            EXCEPTION, "The tag name cannot be longer than "
            "{} characters long".format(length_limit))
    elif len(database_name) == 0:
        raise BotException(EXCEPTION, "No.")
    elif database_name in tag_database:
        raise BotException(
            EXCEPTION, "Tag '{}' already exists.".format(database_name))
    elif len(tag_database) + 1 > tag_limit:
        raise BotException(
            EXCEPTION, "The tag limit has been reached ({}).".format(
                tag_limit))

    # Create the tag
    if 'random' not in options:
        text = [text]
    elif len(text) > 100:
        raise BotException(
            EXCEPTION, "Random tags can have no more than 100 entries.")
    length = list(map(len, text))
    flag_list = list(options.keys())
    flag_list.remove('create')
    flag_bits = get_flag_bits(flag_list)
    new_tag = {
        'name': tag_name,
        'flags': flag_bits,
        'length': length,  # Sound or text length
        'created': int(time.time()),
        'last_used': 0,
        'hits': 0,
        'author': author_id,
        'volume': 1,
        'value': text
    }
    if 'sound' in options:  # Check sound tags for length limit
        return new_tag
    else:
        tag_database[database_name] = new_tag
        return tag_database[database_name]


def remove_tag(bot, tag_database, tag_name, server, author_id):
    """Removes the given tag.

    Normal users can only remove tags they own, but server moderators can
    remove any tag.
    """
    tag, tag_name = get_tag(tag_database, tag_name, include_name=True)
    if (tag['author'] != author_id and
            not data.is_mod(bot, server, author_id)):
        author = data.get_member(
            bot, tag['author'], server=server, attribute='name',
            safe=True, strict=True)
        if author is None:
            author = "who is no longer on this server."
        raise BotException(
            EXCEPTION, "You are not the tag owner, {}.".format(author))
    else:
        del tag_database[tag_name]


def get_tag_info(bot, tag_database, tag_name, server):
    """Gets a nicely formatted string of the tag's information."""
    tag, tag_name = get_tag(tag_database, tag_name, include_name=True)
    author = data.get_member(
        bot, tag['author'], server=server, safe=True, strict=True)
    created_time = time.ctime(tag['created'])
    last = tag['last_used']
    used_time = time.ctime(last) if last else 'Never'
    properties = get_flags(tag['flags'])
    volume = tag['volume'] * 100
    volume_text = '{}%'.format(volume) if 'Sound' in properties else 'n/a'
    length_type = 'second' if 'Sound' in properties else 'character'
    properties = ', '.join(properties) if properties else 'None'
    lengths = list(map(str, tag['length']))
    tag_length = '{0} {1}(s)'.format(', '.join(lengths), length_type)
    if author is None:
        author = "unknown"
    else:
        author = '{0.name}#{0.discriminator}'.format(author)
    return ("Info for tag '{0}':\n"
            "Full name: {1[name]}\n"
            "Author: {2}\n"
            "Properties: {3}\n"
            "Volume: {4}\n"
            "Length: {5}\n"
            "Created: {6}\n"
            "Last used: {7}\n"
            "Hits: {1[hits]}").format(
                tag_name, tag, author, properties, volume_text,
                tag_length, created_time, used_time)


async def edit_tag(bot, tag_database, options, new_text, server, user_id):
    """Edits the tag from options with the given options."""
    tag, tag_name = get_tag(
        tag_database, options['edit'], include_name=True,
        permissions=(bot, server, user_id))
    tag_flags = get_flags(tag['flags'], simple=True)
    additions = []

    if 'nsfw' in options:
        if 'nsfw' in tag_flags:
            tag_flags.remove('nsfw')
            additions.append("Tag is no longer marked as NSFW")
        else:
            tag_flags.append('nsfw')
            additions.append("Tag is now marked as NSFW")

    if 'volume' in options:
        if 'sound' not in tag_flags:
            raise BotException(
                EXCEPTION, "Cannot change the volume of a text only tag.")
        try:
            new_volume = float(options['volume'])
        except ValueError:
            raise BotException(EXCEPTION, "Volume isn't a valid number.")
        if not (0.1 <= new_volume <= 2.0):
            raise BotException(EXCEPTION, "New volume must be in [0.1-2.0].")
        tag['volume'] = new_volume
        additions.append("Volume changed to {:.2f}%.".format(
            new_volume * 100))

    if 'add' in options or 'remove' in options:
        if new_text:
            raise BotException(
                EXCEPTION,
                "Cannot set tag value while also adding/removing entries.")
        if 'add' in options:
            length = len(tag['value'])
            if length >= 100:
                raise BotException(
                    EXCEPTION,
                    "Random tags can have no more than 100 entries.")
            if 'sound' in tag_flags:  # Check audio length
                length = await get_checked_durations(bot, [options['add']])
                length = length[0]
            else:
                length = len(options['add'])
            tag['length'].append(length)
            tag['value'].append(options['add'])
        if 'remove' in options:
            if options['remove'] not in tag['value']:
                raise BotException(
                    EXCEPTION, "Entry '{}' not found in the tag.".format(
                        options['remove']))
            else:
                value_index = tag['value'].index(options['remove'])
                tag['length'].pop(value_index)
                tag['value'].pop(value_index)

        length = len(tag['value'])
        if length == 0:
            del tag_database[tag_name]
            additions = ["Tag removed (last entry removed)."]
        elif len(tag['value']) > 1 and 'random' not in tag_flags:
            tag_flags.append('random')
            additions.append("Added an entry. Tag is now random.")
        elif len(tag['value']) == 1 and 'random' in tag_flags:
            tag_flags.remove('random')
            additions.append("Removed an entry. Tag is no longer random.")
        elif 'add' in options:
            additions.append("Added an entry.")
        elif 'remove' in options:
            additions.append("Removed an entry.")

    if new_text:
        if 'random' in tag_flags:
            raise BotException(EXCEPTION, "Cannot set text for a random tag.")
        tag['value'] = [new_text]
    elif len(options) == 1:
        raise BotException(EXCEPTION, "Nothing was changed!")

    tag['flags'] = get_flag_bits(tag_flags)  # Last. Avoids exceptions
    return '\n'.join(additions)


def list_search_tags(bot, message, plan_index, arguments, direct):
    """Gets a list of the tags given the parameters.

    If the message is sent directly, it lists all of the tags that the user
    can see. Arguments may define the list or search arguments.
    """
    if message.channel.is_private:
        servers = [server for server in bot.servers if (
            message.author in server.members)]
    else:
        servers = [message.server]
    author = None
    search = None
    response = ''

    # Mark list or search arguments
    if arguments and plan_index == 6:
        author = data.get_member(
            bot, arguments, server=message.server, strict=(not direct))
        response += "Tags by '{}':\n".format(author.name)
    elif arguments and plan_index == 7:
        search = cleaned_tag_name(arguments)
        response += "Tags with '{}' in it:\n".format(search)

    # Get tags for each given server
    for server in servers:
        tag_database = data.get(
            bot, __name__, 'tags', server_id=server.id,
            default={}, create=True)
        current_tags = sorted(list(tag_database.items()))

        if current_tags:

            # Split tags by first letter
            response_buffer = ''
            sorted_names, sorted_tags = zip(*current_tags)
            for letter, names in groupby(sorted_names, itemgetter(0)):
                tag_names = list(names)
                start_index = sorted_names.index(tag_names[0])
                end_index = len(tag_names) + start_index
                tags = sorted_tags[start_index:end_index]

                # Filter tags if necessary
                if author:
                    tags = list(
                        filter(lambda t: t['author'] == author.id, tags))
                elif search:
                    tag_pairs = zip(tag_names, tags)
                    tag_pairs = filter(lambda t: search in t[0], tag_pairs)
                    tags = [tag_pair[1] for tag_pair in tag_pairs]

                if tags:
                    tag_names = []
                    for tag in tags:
                        flags = get_flags(tag['flags'])
                        special = [flag[0] for flag in flags]
                        if flags:  # Mark special tags
                            tag_names.append('[{0}]({1})'.format(
                                tag['name'], '/'.join(special)))
                        else:  # Just add the name
                            tag_names.append(tag['name'])
                    tag_names = map(lambda t: t.replace('#', '\#'), tag_names)
                    response_buffer += '# {0} #\n{1}\n'.format(
                        letter.upper(), ', '.join(tag_names))

            if response_buffer:
                response += '\n### Tags for {0}: ###\n{1}'.format(
                    server.name, response_buffer)
            else:
                response += '\nNo tags match query for {}.\n'.format(
                        server.name)
        else:
            response += '\nNo tags for {}.\n'.format(server.name)

    return response


def toggle_channel_filters(bot, server, user_id, channel_name, flag):
    """Toggles the given channel's filter via the flag. Moderators only."""
    if not data.is_mod(bot, server, user_id):
        raise BotException(
            EXCEPTION, "Only moderators can change channel tag filters.")
    flag = flag.lower()
    valid_types = ('all', 'nsfw')
    if flag not in valid_types:
        raise BotException(
            EXCEPTION, "Invalid type. Type must be one of: {}.".format(
                ', '.join(valid_types)))
    channel = data.get_channel(bot, channel_name, server)
    channel_filter = data.get(
        bot, __name__, 'filter', server_id=server.id,
        channel_id=channel.id, default=[], create=True)

    arguments = [bot, __name__, 'filter']
    keyword_arguments = {'server_id': server.id, 'channel_id': channel.id}
    if flag in channel_filter:
        action = data.list_data_remove
        keyword_arguments['value'] = flag
    else:
        action = data.list_data_append
        arguments.append(flag)
    action(*arguments, **keyword_arguments)

    voice_text = 'voice ' if channel.type == 'voice' else ''
    channel_text = "for {0}channel {1}".format(voice_text, channel.name)
    if 'all' in channel_filter:
        return "All tags are now disabled {}.".format(channel_text)
    elif channel_filter:
        return "Disallowed tags {0}: {1}".format(
            channel_text, ', '.join(channel_filter))
    else:
        return "All tags are now allowed {}.".format(channel_text)


async def retrieve_tag(
        bot, tag_database, tag_name, options, member, channel_id):
    """Retrieves the given tag.

    If either 'sound' or 'text' is found in options, display that only.
    Otherwise display both if possible.
    """
    tag = get_tag(tag_database, tag_name)
    flags = get_flags(tag['flags'], simple=True)

    is_mod = data.is_mod(bot, member.server, member.id)
    if not is_mod:
        channel_filter = data.get(
            bot, __name__, 'filter', server_id=member.server.id,
            channel_id=channel_id, default=[])
        if 'all' in channel_filter:
            raise BotException(
                EXCEPTION, "Tags are disabled in this channel.")
        elif 'nsfw' in flags and 'nsfw' in channel_filter:
            raise BotException(
                EXCEPTION, "NSFW tags are disabled in this channel.")
        elif 'private' in flags and member.id != tag['author']:
            raise BotException(EXCEPTION, "This tag is private.")

    tag_is_random = 'random' in flags
    if tag_is_random:
        value_index = int(random.random() * len(tag['value']))
    else:
        value_index = 0
    if 'sound' in flags:
        voice_channel = member.voice_channel
        if not voice_channel:  # Check channel mute filters
            raise BotException(EXCEPTION, "You are not in a voice channel.")
        voice_filter = data.get(
            bot, __name__, 'filter', server_id=member.server.id,
            channel_id=voice_channel.id, default=[])
        if 'all' in voice_filter:
            raise BotException(
                EXCEPTION, "Sound tags are disabled in this voice channel.")
        elif 'nsfw' in flags and 'nsfw' in voice_filter:
            raise BotException(
                EXCEPTION,
                "NSFW sound tags are disabled in this voice channel.")

        voice_client = await utilities.join_and_ready(
            bot, voice_channel, member.server)
        value = tag['value'][value_index]

        # Check if the url is in the cache
        file_directory = data.get_from_cache(bot, None, url=value)
        if not file_directory:  # Can't reuse URLs unfortunately
            if 'my.mixtape.moe/' in value:
                download_url = value
            else:
                try:
                    player = await voice_client.create_ytdl_player(value)
                    download_url = player.download_url
                except Exception as e:
                    logging.warn("youtube_dl failed to download file.")
                    logging.warn("Exception information: {}".format(e))
                    download_url = value
            file_directory = await data.add_to_cache(
                bot, download_url, name=value)

        player = voice_client.create_ffmpeg_player(file_directory)
        player.volume = tag['volume']
        player.start()
        utilities.set_player(bot, member.server.id, player)
        response = ''

    else:  # TODO: Add complex tags
        response = tag['value'][value_index]

    tag['last_used'] = int(time.time())
    tag['hits'] += 1
    return (response, 0)


async def get_response(bot, message, parsed_command, direct):
    response = ''
    tts = False
    message_type = 0
    extra = None
    base, plan_index, options, arguments = parsed_command

    if base == 'tag':

        if not direct:
            tag_database = data.get(
                bot, __name__, 'tags', server_id=message.server.id,
                default={}, create=True, save=True)

        elif plan_index not in (6, 7):
            raise BotException(
                EXCEPTION, "This command cannot be used in a direct message.")

        if plan_index in (0, 1):  # create
            tag_name = options['create']
            database_name = cleaned_tag_name(tag_name)
            new_tag = await create_tag(
                bot, tag_database, tag_name, database_name, message.author.id,
                message.server.id, options, arguments, plan_index)
            if 'sound' in options:
                response = "Checking the length of the audio..."
                message_type = 3
                extra = (
                    'sound_check', new_tag, tag_database,
                    tag_name, database_name)
            else:
                response = "Tag '{0}' created. (Stored as '{1}')".format(
                    tag_name, database_name)

        elif plan_index == 2:  # remove tag
            remove_tag(
                bot, tag_database, arguments,
                message.server, message.author.id)
            response = "Tag removed."

        elif plan_index == 3:  # raw
            tag = get_tag(tag_database, arguments)
            raw_tag = str(tag['value'])
            if len(raw_tag) > 1950 or 'file' in options:
                await bot.send_text_as_file(message.channel, raw_tag, 'raw')
            else:
                response = '```\n{}```'.format(raw_tag)

        elif plan_index == 4:  # tag info
            info = get_tag_info(bot, tag_database, arguments, message.server)
            response = '```\n{}```'.format(info)

        elif plan_index == 5:  # edit
            response = "Tag edited:\n"
            response += await edit_tag(
                bot, tag_database, options, arguments,
                message.server, message.author.id)

        elif plan_index in (6, 7):  # list and search
            response = list_search_tags(
                bot, message, plan_index, arguments, direct)
            if len(response) > 1950:
                await bot.send_text_as_file(message.channel, response, 'tags')
            else:
                response = '```md\n' + response + '```'

        elif plan_index == 8:  # toggle
            response = toggle_channel_filters(
                bot, message.server, message.author.id, *arguments)

        elif plan_index == 9:  # retrieve tag
            response, message_type = await retrieve_tag(
                bot, tag_database, arguments, options,
                message.author, message.channel.id)

    return (response, tts, message_type, extra)


async def handle_active_message(bot, message_reference, extra):
    if extra[0] == 'sound_check':
        urls = extra[1]['value']
        lengths = await get_checked_durations(bot, urls)
        extra[1]['length'] = lengths
        extra[2][extra[4]] = extra[1]  # Assign to database
        response = "Tag '{0}' created. (Stored as '{1}')".format(
            extra[3], extra[4])
        await bot.edit_message(message_reference, response)


async def get_checked_durations(bot, urls):
    """Helper function that returns a list of lengths of the given URLs.

    If any URL is over the length limit, an exception will be thrown.
    """
    length_limit = bot.configurations[__name__]['max_sound_tag_length']
    options = {'format': 'worstaudio/worst'}
    downloader = YoutubeDL(options)
    lengths = []
    over_limit = []
    for url in urls:
        try:
            info = await utilities.future(
                downloader.extract_info, url, download=False)
            if 'duration' in info:
                duration = int(info['duration'])
            else:  # Manual download and check
                chosen_format = info['formats'][0]
                extension = chosen_format['ext']
                download_url = chosen_format['url']
                file_location = await utilities.download_url(
                    bot, download_url, extension=extension)
                duration = int(TinyTag.get(file_location).duration)
                os.remove(file_location)
        except Exception as e:
            raise BotException(
                EXCEPTION, "Failed to get duration from a URL.", url, e=e)
        lengths.append(duration)
        if duration > length_limit:
            over_limit.append(url)

    if over_limit:
        raise BotException(
            EXCEPTION, "The following URL(s) have audio over the "
            "length limit of {} seconds.".format(length_limit),
            '\n'.join(over_limit))
    print("Returning lengths...", lengths)
    return lengths


def get_flags(flag_bits, simple=False):
    """Gets a list of strings representing the flags of the tag.

    If simple is set to True, this will use the simple_flag_list instead.
    """
    found_flags = []
    if simple:
        specified_flag_list = simple_flag_list
    else:
        specified_flag_list = flag_list
    for it, flag in enumerate(specified_flag_list):
        if (flag_bits >> it) & 1:
            found_flags.append(flag)
    return found_flags


def get_flag_bits(given_flags):
    """Gets the flag bits given the flags."""
    flag_value = 0
    for flag in given_flags:
        try:
            flag_index = simple_flag_list.index(flag)
            flag_value += 1 << flag_index
        except ValueError:
            pass
    return flag_value


def get_tag(tag_database, tag_name, include_name=False, permissions=None):
    """Gets the tag reference from the tag database.

    Throws an exception if the tag is not found.
    Keyword arguments:
    include_name -- returns a tuple of the tag and the database name
    permissions -- checks that the user is the tag owner or is a moderator
        permissions should be a tuple: (bot, server, user_id)
    """
    tag_name = cleaned_tag_name(tag_name)
    tag = tag_database.get(tag_name, None)
    if tag is None:
        # TODO: Add a didyoumean feature
        raise BotException(EXCEPTION, "Tag '{}' not found.".format(tag_name))
    if permissions:
        bot, server, user_id = permissions
        if user_id != tag['author'] and not data.is_mod(bot, server, user_id):
            raise BotException(EXCEPTION, "You are not the tag owner.")
    if include_name:
        return (tag, tag_name)
    else:
        return tag


def cleaned_tag_name(name):
    """Get the cleaned up version of the given name.

    The returned tag name only has standard ascii alphanumerical characters.
    """
    cleaned_list = []
    for char in name:  # I /could/ do list comprehension, but nah.
        num = ord(char)
        if 48 <= num <= 57 or 65 <= num <= 90 or 97 <= num <= 122:
            cleaned_list.append(char)
    return ''.join(cleaned_list).lower()
