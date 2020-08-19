# NOTE: This plugin is really only supposed to be used by Jsh.
# If you're curious, it's used for modifying parts of the site in real-time.

import json
import time

from enum import IntEnum

import discord

from jshbot import configurations, utilities, data, plugins, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, Response)

__version__ = '0.2.0'
CBException = ConfiguredBotException('txyz plugin')
uses_configuration = True

# Populated on startup
UPDATE_HOURS = None
MAIN_BOT = None
TXYZ_GUILD = None
COMMENT_CHANNEL = None
DATA_CHANNELS = None
DEFAULTS = None

TYPE_NAMES = ['thoughts', 'footers']

# Channel name data is structured like so:
# t: thought
# f: footer (aka disposition)
# c: comment data
# b: main bot stats
# l: live data


class TextTypes(IntEnum):
    THOUGHT, FOOTER = range(2)


class TXYZTypeConverter():
    def __call__(self, bot, message, value, *a):
        value = value.lower().strip()
        identity = {'thought': TextTypes.THOUGHT, 'footer': TextTypes.FOOTER}
        if value not in identity:
            raise CBException("Must be either `thought` or `footer`.")
        return identity[value]


@plugins.command_spawner
def get_commands(bot):
    """Returns a list of commands associated with this plugin."""
    return [Command(
        'txyz', subcommands=[
            SubCommand(
                Opt('cycle'),
                function=cycle),
            SubCommand(
                Opt('add'),
                Arg('type', convert=TXYZTypeConverter(), quotes_recommended=False),
                Arg('text', argtype=ArgTypes.MERGED,
                    check=lambda b, m, v, *a: 1 <= len(v) <= 99,
                    check_error="Text must be between 2 and 100 characters long."),
                function=add_text),
            SubCommand(
                Opt('remove'),
                Arg('type', convert=TXYZTypeConverter(), quotes_recommended=False),
                Arg('id', convert=int, quotes_recommended=False),
                function=remove_text),
            SubCommand(Opt('list'), function=list_text),
            SubCommand(
                Opt('live'), Opt('disable'), doc="Disables the live page.",
                function=live_disable),
            SubCommand(
                Opt('live'),
                Opt('invisible', optional=True),
                Opt('fullscreen', attached='value', optional=True,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 2,
                    default=0, always_include=True, quotes_recommended=False,
                    doc="0: Default, 1: Hides nav bar, 2: Viewport fullscreen."),
                Opt('theme', attached='value', optional=True,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 4,
                    default=0, always_include=True, quotes_recommended=False,
                    doc="0: Default, 1: Auto, 2: Light, 3: Dark, 4: Migraine."),
                Opt('weather', attached='value', optional=True,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 4,
                    default=0, always_include=True, quotes_recommended=False,
                    doc="0: Default, 1: Clear, 2: Rain, 3: Storm, 4: Snow."),
                Opt('audio', attached='value', optional=True,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 2,
                    default=0, always_include=True, quotes_recommended=False,
                    doc="0: Default, 1: Silence, 2: Rain."),
                Attachment('HTML', doc='HTML file to show.'),
                function=live_enable),
            SubCommand(
                Opt('comment'), doc="Toggles the comment feature.", function=toggle_comment)],
        elevated_level=3, hidden=True, category='tools')]


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'txyz_thoughts_template': (
            "key                serial PRIMARY KEY,"
            "value              text"),
        'txyz_footers_template': (
            "key                serial PRIMARY KEY,"
            "value              text")
    }


@plugins.on_load
def create_txyz_tables(bot):
    data.db_create_table(bot, 'txyz_thoughts', template='txyz_thoughts_template')
    data.db_create_table(bot, 'txyz_footers', template='txyz_footers_template')

    global UPDATE_HOURS, MAIN_BOT, TXYZ_GUILD, COMMENT_CHANNEL, DATA_CHANNELS, DEFAULTS
    config = configurations.get(bot, __name__)
    UPDATE_HOURS = config['update_hours']
    MAIN_BOT = config['main_bot']
    TXYZ_GUILD = config['txyz_guild']
    COMMENT_CHANNEL = config['comment_channel']
    DATA_CHANNELS = config['data_channels']
    DEFAULTS = [config['default_thought'], config['default_footer']]

    if not utilities.get_schedule_entries(bot, __name__, search='txyz_cycler'):
        utilities.schedule(bot, __name__, time.time(), _cycle_timer, search='txyz_cycler')


async def _cycle_timer(bot, scheduled_time, payload, search, destination, late, info, id, *args):
    new_time = time.time() + 60 * 60 * UPDATE_HOURS
    utilities.schedule(bot, __name__, new_time, _cycle_timer, search='txyz_cycler')
    if bot.user.id == MAIN_BOT:
        txyz_guild = bot.get_guild(TXYZ_GUILD)
        try:
            new_data = '{}|{}'.format(len(bot.guilds), sum(1 for it in bot.get_all_members()))
            await _update_data(bot, new_data, 'bot')
        except Exception as e:
            logger.warn("Failed to update guild count: %s", e)
    else:
        try:
            await _cycle(bot)
        except Exception as e:
            logger.warn("Failed to automatically cycle txyz text: %s", e)
            raise e


async def _update_data(bot, new_data, data_type):
    channel_id = DATA_CHANNELS[data_type]
    voice_channels = bot.get_guild(TXYZ_GUILD).voice_channels
    data_channel = next(it for it in voice_channels if it.id == channel_id)
    await data_channel.edit(name=f'_{new_data}')
    logger.debug('Updated txyz %s data with: %s', data_type, new_data)


async def _cycle(bot):
    new_data = []
    for text_type in TextTypes:
        type_name = TYPE_NAMES[text_type]
        cursor = data.db_select(
            bot, from_arg=f'txyz_{type_name}', additional='ORDER BY RANDOM()', limit=1)
        result = cursor.fetchone()
        if result:
            result = result[1]
        else:
            result = DEFAULTS[text_type]
        new_data.append(result)
    await _update_data(bot, '|'.join(new_data), 'text')
    logger.debug('New text pair: %s', new_data)


async def cycle(bot, context):
    cycle_type = context.arguments[0]
    await _cycle(bot)
    return Response(content='Cycled')


async def add_text(bot, context):
    text_type, new_text = context.arguments
    type_name = TYPE_NAMES[text_type]
    data.db_insert(bot, f'txyz_{type_name}', specifiers='value', input_args=new_text, safe=False)
    return Response(content='Added a {}.'.format(type_name))


async def remove_text(bot, context):
    text_type, entry_id = context.arguments
    type_name = TYPE_NAMES[text_type]
    data.db_delete(bot, f'txyz_{type_name}', where_arg='key=%s', input_args=[entry_id], safe=False)
    return Response(content='Removed {} entry {}.'.format(type_name, entry_id))


async def list_text(bot, context):
    list_lines = []
    for text_type in TYPE_NAMES:
        list_lines.append('\n\n' + text_type)
        cursor = data.db_select(bot, from_arg='txyz_' + text_type)
        for key, value in cursor.fetchall():
            list_lines.append('\t{}: {}'.format(key, value))
    text_file = utilities.get_text_as_file('\n'.join(list_lines))
    discord_file = discord.File(text_file, 'txyz_list.txt')
    return Response(content='Table contents:', file=discord_file)


async def live_disable(bot, context):
    await _update_data(bot, '000000|', 'live')
    return Response(content='Live page disabled and the attachment URL has been cleared.')


async def live_enable(bot, context):
    url_suffix = context.message.attachments[0].url.split('/', 4)[-1]
    live_data = '1{visible}{fullscreen}{theme}{weather}{audio}|{url_suffix}'.format(
        visible=0 if 'invisible' in context.options else 1,
        url_suffix=url_suffix,
        **context.options)
    await _update_data(bot, live_data, 'live')
    return Response(content='Live page enabled with code: {}'.format(live_data))


async def toggle_comment(bot, context):
    comment_channel = data.get_channel(bot, COMMENT_CHANNEL)
    webhooks = await comment_channel.webhooks()
    if webhooks:
        for webhook in webhooks:
            await webhook.delete()
        await _update_data(bot, '', 'comment')
    else:
        webhook = await comment_channel.create_webhook(name="txyz_comment")
        await _update_data(bot, f'{webhook.id}/{webhook.token}', 'comment')
    return Response(content=f"Comment function {'dis' if webhooks else 'en'}abled")
