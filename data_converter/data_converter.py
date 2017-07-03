import discord

from jshbot import utilities, data, configurations, plugins, logger
from jshbot.exceptions import BotException, ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.1.0'
CBException = ConfiguredBotException('0.3 to 0.4 plugin')

@plugins.command_spawner
def get_commands(bot):
    return [Command('convertdata')]

async def get_response(bot, context):
    for guild in bot.guilds:
        convert_core(bot, guild)
        if 'tags.py' in bot.plugins:
            convert_tags(bot, guild)
    return Response("Converted.")


def convert_core(bot, guild):
    if data.get(bot, 'core', None, guild_id=guild.id):
        logger.warn("Guild %s (%s) already had core converted", guild.name, guild.id)
        return
    base_data = data.get(bot, 'base', None, guild_id=guild.id, default={})
    if 'disabled' in base_data:
        # TODO: Iterate through toggled commands
        pass
    if 'blocked' in base_data:
        replacement = []
        for entry in base_data['blocked']:
            replacement.append(int(entry))
        base_data['blocked'] = replacement
    if 'muted_channels' in base_data:
        replacement = []
        for entry in base_data['muted_channels']:
            replacement.append(int(entry))
        base_data['muted_channels'] = replacement
    if 'moderators' in base_data:
        del base_data['moderators']
    if base_data:
        for key, value in base_data.items():
            data.add(bot, 'core', key, value, guild_id=guild.id)
        data.remove(bot, 'base', None, guild_id=guild.id)


def convert_tags(bot, guild):
    if not data.get(bot, 'tags.py', 'tags', guild_id=guild.id):
        logger.warn("Guild %s (%s) already had tags converted", guild.name, guild.id)
        return

    tags = data.get(bot, 'tags.py', 'tags', guild_id=guild.id, default={})
    add_tag = bot.plugins['tags.py']._add_tag
    #key,value,length,volume,name,flags,author,hits,created,last_used,last_used_by,complex,extra
    for key, tag in tags.items():
        to_insert = [
            key,                        # key
            tag['value'],               # value
            tag['length'],              # length
            tag['volume'],              # volume
            tag['name'],                # name
            tag['flags'],               # flags
            int(tag['author']),         # author
            tag['hits'],                # hits
            int(tag['created']),        # created
            int(tag['last_used']),      # last_used
            None,                       # last_used_by
            {},                         # complex
            {}                          # extra
        ]
        add_tag(bot, to_insert, guild.id)
    data.remove(bot, 'tags.py', 'tags', guild_id=guild.id, safe=True)
