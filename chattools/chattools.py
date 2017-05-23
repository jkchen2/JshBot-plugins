import discord
import logging
import asyncio

from jshbot import utilities, configurations, data
from jshbot.commands import Command, SubCommands
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Chat tools'
uses_configuration = True


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'quote', SubCommands(
            ('id: &', 'id <message ID> (subquote)', 'Quote a specific message '
             'using the message ID. (If you have developer mode enabled, you '
             'can right click on a message and copy its ID)'),
            ('^', '<text>', 'Quotes a user\'s message, or a string of text '
             'from a message.')),
        description='Quote messages.', group='tools', allow_direct=False))

    new_commands.append(Command(
        'tattle', SubCommands(
            ('?latest ?server &', '(latest) (server) (<username>)',
             'Retrieves a recently deleted or edited message from either the '
             'curent channel, or server wide. If a username is given, it will '
             'look for deleted messages by that user only.')),
        description='Retrieve recently deleted or edited messages.',
        group='tools', allow_direct=False))

    return new_commands


def _get_tattle_messages(bot, location, author, serverwide):
    tattle_messages = data.get(bot, __name__, 'tattle_messages', volatile=True)
    matches = []
    for item in tattle_messages:
        edit = isinstance(item, tuple)
        message = item[0] if edit else item
        check = getattr(message, 'server' if serverwide else 'channel')
        if location == check and (not author or message.author == author):
            matches.append(item)
    return list(reversed(matches[:50]))


async def _retrieve_specified_tattle(bot, message_reference, reply, extra):
    if reply is None:
        await bot.edit_message(
            message_reference, message_reference.content +
            '\n\nSelection cancelled (timed out).')
        return
    # Build the embed
    try:
        index = int(reply.content) - 1
        if index < 0 or index > len(extra) - 1:
            raise ValueError
    except ValueError:  # TODO: Use a BotException instead?
        await bot.edit_message(
            message_reference, message_reference.content +
            "\n\nInvalid number. Selection cancelled.")
        return
    built_embed = _build_tattle_embed(extra[index])
    await bot.edit_message(message_reference, embed=built_embed)


def _build_tattle_embed(result):
    edited = isinstance(result, tuple)
    if edited:
        message = result[0]
    else:
        message = result
    operation = "edited" if edited else "deleted"
    build = discord.Embed(timestamp=message.timestamp)
    build.add_field(
        name="The following message was {}:".format(operation),
        value=message.content, inline=False)
    if edited:
        build.add_field(name="To:", value=result[1].content)
    build.set_author(
        name=str(message.author) + ' ({})'.format(message.author.id),
        icon_url=message.author.avatar_url)
    build.set_footer(text=operation.title())
    return build


def _build_quote_embed(result, author, match=None):
    if len(result.content) <= 0:
        raise BotException(EXCEPTION, "Message is empty.")
    if match:
        try:
            begin = result.content.lower().index(match)
        except ValueError:
            raise BotException(
                EXCEPTION, "Subquote not found in that message.")
        end = begin + len(match)
        prefix = '*...* ' if begin > 0 else ''
        suffix = ' *...*' if end < len(result.content) else ''
        matching_text = '{}{}{}'.format(
            prefix, result.content[begin:end], suffix)
    else:
        matching_text = result.content
    if len(matching_text) > 1800:
        raise BotException(EXCEPTION, "Message is too large to quote.")
    build = discord.Embed(
        timestamp=result.timestamp, title="Said:", description=matching_text)
    build.set_author(
        name=str(result.author) + ' ({})'.format(result.author.id),
        icon_url=result.author.avatar_url)
    build.set_footer(
        text="[Quoted by {}] -- Original message sent".format(author))
    return build


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    # NOTE: Functionality is limited in selfbot mode
    # Consider re-implementing under the selfassist plugin
    if base == 'quote':
        message_type = 4
        if blueprint_index == 0:  # ID used
            try:
                message_id = int(options['id'])
            except ValueError:
                raise BotException(
                    EXCEPTION, "Invalid ID. Enable developer mode in the "
                    "options, and right click messages to copy its ID.")
            result = None
            for channel in message.server.channels:
                try:
                    result = await bot.get_message(channel, message_id)
                except (discord.NotFound, discord.Forbidden):
                    pass
                else:
                    break
            if result:
                response = _build_quote_embed(
                    result, message.author, match=arguments[0])
            else:
                raise BotException(EXCEPTION, "Message not found.")

        elif blueprint_index == 1:  # Find matching text
            # Get messages
            result = None
            async for current in bot.logs_from(message.channel, limit=500):
                if current.id == message.id:
                    continue
                compare_text = arguments[0].lower()
                if compare_text in current.content.lower():
                    result = current
                    break
            if result:
                response = _build_quote_embed(
                    result, message.author, match=compare_text)
            else:
                raise BotException(
                    EXCEPTION,
                    "No recent message matches the given substring.")


    elif base == 'tattle':
        if arguments[0]:  # Interpret username
            author = data.get_member(bot, arguments[0], server=message.server)
        else:
            author = None
        serverwide = 'server' in options
        location = message.server if serverwide else message.channel
        messages = _get_tattle_messages(bot, location, author, serverwide)
        if 'latest' in options:
            messages = messages[:1]

        if len(messages) > 1:
            listing = []
            for it, result in enumerate(messages):
                if isinstance(result, tuple):
                    operation = "Edited"
                    result = result[0]
                else:
                    operation = "Deleted"
                append_text = result.content.replace('\n', '')
                if len(append_text) > 20:
                    append_text = append_text[:20] + '...'
                listing.append('**`[{: <2}]`**: ({}) {}: {}'.format(
                    it + 1, operation, result.author, append_text))
            response = (
                "Multiple tattle messages found (most recent first). Please "
                "reply with the entry you would like to view.\n{}".format(
                    '\n'.join(listing)))
            message_type = 6
            extra = (
                _retrieve_specified_tattle,
                {'timeout': 300, 'author': message.author},
                messages
            )
        elif len(messages) == 0:
            response = "No edited or deleted messages found."
        else:
            response = _build_tattle_embed(messages[0])

    return (response, tts, message_type, extra)


async def on_message_delete(bot, message):
    if message.author.bot:
        return
    tattle_messages = data.get(bot, __name__, 'tattle_messages', volatile=True)
    max_length = configurations.get(bot, __name__, key='tattle_max_length')
    if len(tattle_messages) > max_length:
        del tattle_messages[0]
    tattle_messages.append(message)


async def on_message_edit(bot, before, after):
    if before.author.bot or before.content == after.content:
        return
    tattle_messages = data.get(bot, __name__, 'tattle_messages', volatile=True)
    max_length = configurations.get(bot, __name__, key='tattle_max_length')
    if len(tattle_messages) > max_length:
        del tattle_messages[0]
    tattle_messages.append((before, after))


async def bot_on_ready_boot(bot):
    data.add(bot, __name__, 'tattle_messages', [], volatile=True)
    permissions = {
        'manage_messages': (
            "Clean up quote command usage by deleting the issuing command.")
    }
    utilities.add_bot_permissions(bot, __name__, **permissions)
