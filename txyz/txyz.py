# NOTE: This plugin is really only supposed to be used by Jsh.
# If you're curious, it's used for modifying parts of the site in real-time.

import discord
import time
from enum import IntEnum

from jshbot import utilities, data, plugins, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

class TextTypes(IntEnum):
    THOUGHT, FOOTER = range(2)

__version__ = '0.1.0'
CBException = ConfiguredBotException('txyz plugin')
uses_configuration = False

TXYZ_GUILD = 371885514049191937
DEFAULTS = ["Looks like my head is a bit empty right now.", "💔"]
TYPE_NAMES = ['thoughts', 'footers']


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
    new_commands = []

    return [Command(
        'txyz', subcommands=[
            SubCommand(
                Opt('cycle'),
                Arg('type', convert=TXYZTypeConverter(), quotes_recommended=False),
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
            SubCommand(Opt('list'), function=list_text)],
        elevated_level=3, category='tools')]


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
    if not utilities.get_schedule_entries(bot, __name__, search='txyz_cycler'):
        utilities.schedule(bot, __name__, time.time(), _cycle_timer, search='txyz_cycler')


async def _cycle_timer(bot, scheduled_time, payload, search, destination, late):
    utilities.schedule(bot, __name__, time.time()+86400, _cycle_timer, search='txyz_cycler')
    for text_type in TextTypes:
        try:
            await _cycle_specific(bot, text_type)
        except Exception as e:
            logger.warn("Failed to automatically cycle txyz text: %s", e)


async def _cycle_specific(bot, cycle_type):
    table_name = 'txyz_' + TYPE_NAMES[cycle_type]
    cursor = data.db_select(bot, from_arg=table_name, additional='ORDER BY RANDOM()', limit=1)
    result = cursor.fetchone()[1]
    if not result:
        result = DEFAULTS[cycle_type]
    txyz_guild = bot.get_guild(TXYZ_GUILD)
    selected_channel = txyz_guild.voice_channels[cycle_type]
    await selected_channel.edit(name='_' + result)
    logger.debug('New %s: %s', table_name[5:-1], result)
    return result


async def cycle(bot, context):
    cycle_type = context.arguments[0]
    type_name = TYPE_NAMES[cycle_type]
    result = await _cycle_specific(bot, cycle_type)
    return Response(content='Cycled {} to: {}'.format(type_name[:-1], result))


async def add_text(bot, context):
    text_type, new_text = context.arguments
    table_name = 'txyz_' + TYPE_NAMES[text_type]
    data.db_insert(bot, table_name, specifiers='value', input_args=new_text, safe=False)
    return Response(content='Added a {}.'.format(table_name[5:-1]))


async def remove_text(bot, context):
    text_type, entry_id = context.arguments
    table_name = 'txyz_' + TYPE_NAMES[text_type]
    data.db_delete(bot, table_name, where_arg='key=%s', input_args=[entry_id], safe=False)
    return Response(content='Removed {} entry {}.'.format(table_name[5:-1], entry_id))


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
