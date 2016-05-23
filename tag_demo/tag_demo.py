import discord
import asyncio
import random
import time
import logging

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
        'search ^', '^'],[('create', 'c'), ('private', 'p'), ('sound', 's'),
        ('random', 'rand'), ('remove', 'r'), ('info', 'i'), ('edit', 'e'),
        ('add', 'a'), ('list', 'l')])

    shortcuts['tc'] = ('tag -create {} {}', ':^')
    shortcuts['tr'] = ('tag -remove {}', '^')
    shortcuts['t'] = ('tag {}', '^')
    shortcuts['tl'] = ('tag -list {}', '&')
    shortcuts['ts'] = ('tag -search {}', '^')
    shortcuts['stc'] = ('tag -create {} -sound {}', ':^')

    manual['tag'] = {
        'description': 'Proof of concept tags using the data framework.',
        'usage': [
            ('<tag name>', 'Retrieves the given tag. If it is a sound tag, it '
                'plays the tag in the voice channel you are in.'),
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

        tag_database = data.get(bot, __name__, 'tags',
                server_id=message.server.id, default={}, create=True)

        if plan_index in (0, 1): # create
            length_limit = bot.configurations[__name__]['max_tag_name_length']
            tag_name = options['create']
            database_name = cleaned_tag_name(tag_name)
            if len(tag_name) > length_limit:
                response = ("The tag name cannot be longer than {} characters "
                    "long.").format(length_limit)
            elif len(database_name) == 0:
                response = "No."
            elif database_name in tag_database:
                response = "Tag already exists."
            else:
                new_tag = {
                    'raw_name': tag_name,
                    'name': database_name,
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
            tag = tag_database.get(tag_name, None)
            if tag is None:
                response = "Tag '{}' not found.".format(tag_name)
            elif (tag['author'] != message.author.id and
                    not data.is_mod(bot, message.server, message.author.id)):
                response = "You are not the tag owner, {}".format(
                        data.get_id(bot, message.author.id,
                                server=message.server, name=True))
            else:
                del tag_database[tag_name]
                response = "Tag removed."

        elif plan_index == 8: # retrieve tag
            tag_name = cleaned_tag_name(arguments)
            tag = tag_database.get(tag_name, None)
            if tag is None:
                response = "Tag '{}' not found.".format(tag_name)
            else:
                tag['last_used'] = int(time.time())
                tag['hits'] += 1
                if tag.get('random', False):
                    response = random.choice(tag['raw'])
                else:
                    response = tag['value']

    return (response, tts, message_type, extra)

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

