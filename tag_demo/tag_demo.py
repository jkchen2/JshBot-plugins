import discord
import asyncio
import random
import time
import logging

from itertools import groupby
from operator import itemgetter

from jshbot import data
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Tag demo'
uses_configuration = True

def get_commands():

    commands = {}
    shortcuts = {}
    manual = {}

    commands['tag'] = ([
        'create: ?private ?sound ?nsfw ?complex ^',
        'create: random ?private ?sound ?nsfw ?complex :+', 'remove ^',
        'raw ?file ^', 'info ^', 'edit: ?add: ?remove: ?nsfw &', 'list &',
        'search ^', 'toggle ::', '?text ?sound ^'],[('create', 'c'),
        ('private', 'p'), ('sound', 's'), ('random', 'rand'), ('remove', 'r'),
        ('info', 'i'), ('edit', 'e'), ('add', 'a'), ('list', 'l'),
        ('text', 't')])

    shortcuts['tc'] = ('tag -create {}', '^')
    shortcuts['tr'] = ('tag -remove {}', '^')
    shortcuts['t'] = ('tag {}', '^')
    shortcuts['tl'] = ('tag -list {}', '&')
    shortcuts['ts'] = ('tag -search {}', '^')
    shortcuts['stc'] = ('tag -create {} -sound {}', ':^')

    manual['tag'] = {
        'description': 'Proof of concept tags using the data framework.',
        'usage': [
            ('(-text) (-sound) <tag name>', 'Retrieves the given tag. If it is '
                'a sound tag, it plays the tag in the voice channel you are '
                'in. If a tag has both sound and text components, you can '
                'specify which one you want with the options. By default, it '
                'attempts to do both if they exist.'),
            ('-create <tag name> (-private) (-sound) (-nsfw) (-complex) '
                '<tag text>', 'Creates a tag that follows the given options. '
                'A private tag can only be called by its owner or moderators, '
                'and a sound tag is played in the voice channel of the message '
                'author.'),
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
                '<old selection>) (modified tag text)', 'Edits the given tag '
                'given the modified tag text. If you are editing a random tag, '
                'use the add or remove options to manipulate the random list.'),
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

async def create_tag(bot, tag_database, tag_name, database_name, author_id,
        server_id, options, text, is_random):
    '''
    Creates a tag based on the given parameters. If it is a tag with sound,
    it will check that the sound length is no longer than the limit.
    '''
    length_limit = bot.configurations[__name__]['max_tag_name_length']
    default_max_tags = bot.configurations[__name__]['max_tags_per_server']
    server_settings = data.get(bot, __name__, 'settings', server_id=server_id,
            default={})
    tag_limit = server_settings.get('max_tags', default_max_tags)
    if tag_limit > default_max_tags: # Safety tag limit
        tag_limit = default_max_tags

    # Check for issues
    if len(tag_name) > length_limit:
        raise BotException(EXCEPTION, "The tag name cannot be longer than {} "
                "characters long".format(length_limit))
    elif len(database_name) == 0:
        raise BotException(EXCEPTION, "No.")
    elif database_name in tag_database:
        raise BotException(EXCEPTION,
                "Tag '{}' already exists.".format(database_name))
    elif len(tag_database) + 1 > tag_limit:
        raise BotException(EXCEPTION,
                "The tag limit has been reached ({})".format(tag_limit))
    # Create the tag
    else:
        new_tag = {
            'name': tag_name,
            'random': bool(is_random),
            'private': 'private' in options,
            'complex': 'complex' in options,
            'sound': 'sound' in options,
            'length': 0, # Temporary
            'nsfw': 'nsfw' in options,
            'created': int(time.time()),
            'last_used': 0,
            'hits': 0,
            'author': author_id,
            'url': text,
            'value': text, # Temporary
            'raw': text
        }
        tag_database[database_name] = new_tag

def remove_tag(bot, tag_database, tag_name, server, author_id):
    '''
    Removes the given tag. Normal users can only remove tags they own, but
    server moderators can remove any tag.
    '''
    tag, tag_name = get_tag(tag_database, tag_name, include_name=True)
    if (tag['author'] != author_id and
            not data.is_mod(bot, server, author_id)):
        author = data.get_member(bot, tag['author'], server=server,
                attribute='name', safe=True, strict=True)
        if author is None:
            author = "who is no longer on this server."
        raise BotException(EXCEPTION,
                "You are not the tag owner, {}.".format(author))
    else:
        del tag_database[tag_name]

def get_tag_info(bot, tag_database, tag_name, server):
    '''
    Returns a formatted chunk of text that lists the given tag's information.
    '''
    tag, tag_name = get_tag(tag_database, tag_name, include_name=True)
    author = data.get_member(bot, tag['author'], server=server, safe=True,
            strict=True)
    created_time = time.ctime(tag['created'])
    last = tag['last_used']
    used_time = time.ctime(last) if last else 'Never'
    properties = []
    for flag in ('sound', 'private', 'nsfw', 'complex', 'random'):
        if tag[flag]:
            properties.append(flag)
    properties = ', '.join(properties) if properties else 'None'
    if tag['length']:
        tag_length = '{} second(s)'.format(tag['length'])
    else:
        tag_length = 'n/a'
    if author is None:
        author = "unknown"
    else:
        author = '{0.name}#{0.discriminator}'.format(author)
    return ("Info for tag '{0}':\n"
            "Full name: {1[name]}\n"
            "Author: {2}\n"
            "Properties: {3}\n"
            "Length: {4}\n"
            "Created: {5}\n"
            "Last used: {6}\n"
            "Hits: {1[hits]}").format(tag_name, tag, author, properties,
                    tag_length, created_time, used_time)

def list_search_tags(bot, message, plan_index, arguments):
    '''
    Gets a list of the tags give the parameters. If the message is sent
    directly, it lists all of the tags that the user can see. Arguments may
    define the list or search arguments.
    '''

    if message.channel.is_private:
        servers = [server for server in bot.servers if (message.author in
                server.members)]
    else:
        servers = [message.server]
    author = None
    search = None
    response = ''

    # Mark list or search arguments
    if arguments and plan_index == 6:
        author = data.get_member(bot, arguments, server=message.server,
                strict=(not direct))
        response += "Tags by '{}':\n".format(author.name)
    elif arguments and plan_index == 7:
        search = cleaned_tag_name(arguments)
        response += "Tags with '{}' in it:\n".format(search)

    # Get tags for each given server
    for server in servers:
        tag_database = data.get(bot, __name__, 'tags', server_id=server.id,
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
                    tag_filter = lambda t: t['author'] == author.id
                    tags = list(filter(tag_filter, tags))
                elif search:
                    tag_pairs = zip(tag_names, tags)
                    tag_pairs = filter(lambda t: search in t[0], tag_pairs)
                    tags = [tag_pair[1] for tag_pair in tag_pairs]

                if tags:
                    tag_names = []
                    for tag in tags:
                        special = []
                        for flag in ('sound', 'private', 'nsfw', 'complex',
                                'random'):
                            if tag[flag]:
                                special.append(flag[0].upper())
                        if special: # Mark special tags
                            tag_names.append('[{0}]({1})'.format(tag['name'],
                                    '/'.join(special)))
                        else: # Just add the name
                            tag_names.append(tag['name'])
                    tag_names = map(lambda t: t.replace('#', '\#'), tag_names)
                    response_buffer += '# {0} #\n{1}\n'.format( letter.upper(),
                            ', '.join(tag_names))

            if response_buffer:
                response += '\n### Tags for {0}: ###\n{1}'.format(server.name,
                        response_buffer)
            else:
                response += '\nNo tags match query for {}.\n'.format(
                        server.name)
        else:
            response += '\nNo tags for {}.\n'.format(server.name)

    return response

async def retrieve_tag(bot, tag_database, tag_name, author_id, channel_id,
        server, options):
    '''
    Retrieves the given tag. If either 'sound' or 'text' is found in options,
    display that only. Otherwise display both if possible.
    '''
    tag = get_tag(tag_database, tag_name)

    # if tag_author == author_id or author_id is mod
    # if tag_author != author_id and author_id is not
    if tag['private'] and author_id != tag['author'] and not data.is_mod(bot,
            server, author_id):
        raise BotException(EXCEPTION, "This tag is private.")
    # TODO: Add mute checking and nsfw checking
    #mute_settings = data.get(bot, __name__, None, server_id=server.id,
    #    channel_id=channel_id)
    #elif tag['sound'] and data.get(bot, __name__, 'muted'

    tag['last_used'] = int(time.time())
    tag['hits'] += 1
    if tag.get('random', False):
        return (random.choice(tag['value']), 0)
    else:
        return (tag['value'], 0)

async def get_response(bot, message, parsed_command, direct):

    response = ''
    tts = False
    message_type = 0
    extra = None
    base, plan_index, options, arguments = parsed_command

    if base == 'tag':

        if not direct:
            tag_database = data.get(bot, __name__, 'tags',
                    server_id=message.server.id, default={}, create=True,
                    save=True)

        elif plan_index not in (6, 7):
            raise BotException(EXCEPTION,
                    "This command cannot be used in a direct message.")

        if plan_index in (0, 1): # create
            tag_name = options['create']
            database_name = cleaned_tag_name(tag_name)
            await create_tag(bot, tag_database, tag_name, database_name,
                    message.author.id, message.server.id, options, arguments,
                    plan_index)
            response = "Tag '{0}' created. (Stored as '{1}')".format(
                    tag_name, database_name)

        elif plan_index == 2: # remove tag
            remove_tag(bot, tag_database, arguments, message.server,
                    message.author.id)
            response = "Tag removed."

        elif plan_index == 3: # raw
            tag = get_tag(tag_database, arguments)
            raw_tag = str(tag['raw'])
            if len(raw_tag) > 1950 or 'file' in options:
                await bot.send_text_as_file(message.channel, raw_tag, 'raw')
            else:
                response = '```\n{}```'.format(raw_tag)

        elif plan_index == 4: # tag info
            info = get_tag_info(bot, tag_database, arguments, message.server)
            response = '```\n{}```'.format(info)

        elif plan_index == 5: # edit
            response = "Coming soon :tm:"

        elif plan_index in (6, 7): # list and search
            response = list_search_tags(bot, message, plan_index, arguments)
            if len(response) > 1950:
                await bot.send_text_as_file(message.channel, response, 'tags')
            else:
                response = '```markdown\n' + response + '```'

        elif plan_index == 8: # toggle
            response = "Coming soon :tm:"

        elif plan_index == 9: # retrieve tag
            response, message_type = await retrieve_tag(bot, tag_database,
                    arguments, message.author.id, message.channel.id,
                    message.server, options)

    return (response, tts, message_type, extra)

def get_tag(tag_database, tag_name, include_name=False):
    '''
    Wrapper function that gets the tag reference, or throws a bot exception.
    If include_name is True, returns a tuple of the tag and the database name.
    '''
    tag_name = cleaned_tag_name(tag_name)
    tag = tag_database.get(tag_name, None)
    if tag is None:
        # TODO: Add a didyoumean feature
        raise BotException(EXCEPTION, "Tag '{}' not found.".format(tag_name))
    elif include_name:
        return (tag, tag_name)
    else:
        return tag

def cleaned_tag_name(name):
    '''
    Returns a cleaned up version of the tag name that only has standard ascii
    alphanumerical characters.
    '''
    cleaned_list = []
    for char in name: # I /could/ do list comprehension, but nah.
        num = ord(char)
        if 48 <= num <= 57 or 65 <= num <= 90 or 97 <= num <= 122:
            cleaned_list.append(char)
    return ''.join(cleaned_list).lower()

