import discord
import asyncio
import random
import time
import logging

from itertools import groupby
from operator import itemgetter

from jshbot import data
from jshbot.exceptions import ErrorTypes, BotException

__version__ = '0.1.0'
EXCEPTION = 'Tag demo'
uses_configuration = True

def get_commands():

    commands = {}
    shortcuts = {}
    manual = {}

    commands['tag'] = ([
        'create: ?private ?sound ^', 'create: random ?private ?sound :+',
        'remove ^', 'raw ^', 'info ^', 'edit: ?add: ?remove: &', 'list &',
        'search ^', '?text ?sound ^'],[('create', 'c'), ('private', 'p'),
        ('sound', 's'), ('random', 'rand'), ('remove', 'r'), ('info', 'i'),
        ('edit', 'e'), ('add', 'a'), ('list', 'l'), ('text', 't')])

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
            ('-create <tag name> (-private) (-sound) <tag text>', 'Creates a '
                'tag that follows the given options. A private tag can only be '
                'called by its owner or moderators, and a sound tag is played '
                'in the voice channel of the message author.'),
            ('-create <tag name> -random (-private_ (-sound) <selection 1> '
                '<selection 2> (selection 3) (...)', 'Works the same as the '
                'regular create command, but will select a random selection '
                'when the tag is called.'),
            ('-remove <tag name>', 'Removes the specified tag. You must the '
                'tag owner or a moderator.'),
            ('-raw <tag name>', 'Gets the raw tag data. Useful for figuring '
                'out what is inside a random tag.'),
            ('-info <tag name>', 'Gets basic tag information.'),
            ('-edit <tag name> (-add <new selection>) (-remove '
                '<old selection>) (modified tag text)', 'Edits the given tag '
                'given the modified tag text. If you are editing a random tag, '
                'use the add or remove options to manipulate the random list.'),
            ('-list (user)', 'Lists all tags, or if a user is specified, only '
                'tags made by that user.'),
            ('-search <text>', 'Searches all tags for the given text.')],
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

async def get_response(bot, message, parsed_command, direct):

    response = ''
    tts = False
    message_type = 0
    extra = None
    base, plan_index, options, arguments = parsed_command

    if base == 'tag':

        if not direct:
            tag_database = data.get(bot, __name__, 'tags',
                    server_id=message.server.id, default={}, create=True)

        elif direct and plan_index not in (6, 7):
            raise BotException(ErrorTypes.RECOVERABLE, EXCEPTION,
                    "This command cannot be used in a direct message.")

        if plan_index in (0, 1): # create
            length_limit = bot.configurations[__name__]['max_tag_name_length']
            tag_name = options['create']
            database_name = cleaned_tag_name(tag_name)
            if len(tag_name) > length_limit:
                raise BotException(ErrorTypes.RECOVERABLE, EXCEPTION,
                        ("The tag name cannot be longer than {} characters "
                            "long".format(length_limit)))
            elif len(database_name) == 0:
                response = "No."
            elif database_name in tag_database:
                raise BotException(ErrorTypes.RECOVERABLE, EXCEPTION,
                        "Tag '{}' already exists.".format(database_name))
            else:
                new_tag = {
                    'name': tag_name,
                    'random': plan_index == 1,
                    'sound': 'sound' in options,
                    'private': 'private' in options,
                    'created': int(time.time()),
                    'last_used': 0,
                    'hits': 0,
                    'author': message.author.id,
                    'value': str(arguments),
                    'raw': arguments
                }
                tag_database[database_name] = new_tag
                response = "Tag '{0}' created. (Stored as '{1}')".format(
                        tag_name, database_name)

        elif plan_index == 2: # remove tag
            tag_name = cleaned_tag_name(arguments)
            tag = get_tag(tag_database, tag_name)
            if (tag['author'] != message.author.id and
                    not data.is_mod(bot, message.server, message.author.id)):
                author = data.get_member(bot, tag['author'], strict=True,
                        server=message.server, attribute='name', safe=True)
                if author is None:
                    author = "who is no longer on this server."
                raise BotException(ErrorTypes.RECOVERABLE, EXCEPTION,
                    "You are not the tag owner, {}.".format(author))
            else:
                del tag_database[tag_name]
                response = "Tag removed."

        elif plan_index == 3: # raw
            tag = get_tag(tag_database, arguments)
            raw_tag = '```\n' + str(tag['raw']) + '```'
            if len(raw_tag) > 2000:
                send_raw_tag(bot, message.channel, raw_tag)
            else:
                response = raw_tag

        elif plan_index == 4: # tag info
            tag_name = cleaned_tag_name(arguments)
            tag = get_tag(tag_database, tag_name)
            created_time = time.ctime(tag['created'])
            last = tag['last_used']
            used_time = time.ctime(last) if last else 'Never'
            author = data.get_member(bot, tag['author'], server=message.server,
                    safe=True, strict=True)
            if author is None:
                author = "Unknown"
            else:
                author = '{0.name}#{0.discriminator}'.format(author)
            response = ("```\nInfo for tag '{0}':\n"
                    "Full name: {1[name]}\n"
                    "Author: {2}\n"
                    "Private: {1[private]}\n"
                    "Random: {1[random]}\n"
                    "Sound: {1[sound]}\n"
                    "Created: {3}\n"
                    "Last used: {4}\n"
                    "Hits: {1[hits]}```").format(tag_name, tag, author,
                        created_time, used_time)

        elif plan_index == 5: # edit
            response = "Coming soon :tm:"

        elif plan_index in (6, 7): # list and search
            if direct:
                servers = [server for server in bot.servers if (
                    message.author in server.members)]
            else:
                servers = [message.server]
            author = None
            search = None

            if arguments and plan_index == 6:
                author = data.get_member(bot, arguments, server=message.server,
                        strict=(not direct))
                response += "Tags by '{}':\n".format(author.name)
            elif arguments and plan_index == 7:
                search = cleaned_tag_name(arguments)
                response += "Tags with '{}' in it:\n".format(search)

            # Get tags for each given server
            for server in servers:
                tag_database = data.get(bot, __name__, 'tags',
                        server_id=server.id, default={}, create=True)
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
                        if author:
                            tag_filter = lambda t: t['author'] == author.id
                            tags = list(filter(tag_filter, tags))
                        elif search:
                            tag_filter = lambda t: search in t[0]
                            tag_pairs = zip(tag_names, tags)
                            tag_pairs = filter(tag_filter, tag_pairs)
                            tags = [tag_pair[1] for tag_pair in tag_pairs]
                        if tags:
                            tag_names = [tag['name'] for tag in tags]
                            tag_map = lambda t: t.replace('#', '\#')
                            tag_names = map(tag_map, tag_names)
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

            if len(response) > 1950:
                await send_tag_list_as_file(bot, message.channel, response)
            else:
                response = '```markdown\n' + response + '```'

        elif plan_index == 8: # retrieve tag
            tag = get_tag(tag_database, arguments)
            tag['last_used'] = int(time.time())
            tag['hits'] += 1
            if tag.get('random', False):
                response = random.choice(tag['raw'])
            else:
                response = tag['value']

    return (response, tts, message_type, extra)

def get_tag(tag_database, tag_name):
    '''
    Wrapper function that gets the tag reference, or throws a bot exception.
    '''
    tag_name = cleaned_tag_name(tag_name)
    tag = tag_database.get(tag_name, None)
    if tag is None:
        raise BotException(ErrorTypes.RECOVERABLE, EXCEPTION,
                "Tag '{}' not found.".format(tag_name))
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

async def send_tag_list_as_file(bot, channel, tag_list_result):
    '''
    Sends the list of tags as a text file because it is over 2000 characters.
    '''
    with open('tag_list.txt', 'w') as tag_list_file:
        tag_list_file.write(tag_list_result)
    await bot.send_file(channel, 'tag_list.txt')

