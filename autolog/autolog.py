import asyncio
import collections
import json
import time
import discord

from datetime import datetime, timezone
from psycopg2.extras import Json

from jshbot import utilities, plugins, configurations, data, logger
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import (
    Command, SubCommand, ArgTypes, Arg, Opt, Response, MessageTypes, Elevation)

__version__ = '0.1.1'
CBException = ConfiguredBotException('Auto chat logger')
uses_configuration = True

DATA_VERSION = 1


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'autolog', subcommands=[
            SubCommand(
                Opt('channels'),
                Arg('channel', argtype=ArgTypes.SPLIT_OPTIONAL,
                    convert=utilities.ChannelConverter(constraint=discord.TextChannel),
                    doc='Toggles logging in these channels.'),
                doc='Shows logged channels or toggles logging in the given channel(s).',
                function=autolog_channels),
            SubCommand(
                Opt('messages'),
                Arg('number', quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 50 <= v <= 500,
                    check_error='Must be between 50 and 500 messages inclusive.'),
                doc='Sets the number of messages to log.',
                function=autolog_messages),
            SubCommand(
                Opt('setchannel'),
                Arg('channel', argtype=ArgTypes.MERGED,
                    convert=utilities.ChannelConverter(constraint=discord.TextChannel)),
                doc='Sets the logging channel.', function=autolog_setchannel),
            SubCommand(
                Opt('dump'),
                Opt('details', optional=True, attached='text', always_include=True,
                    default='No details provided.'),
                Opt('query', optional=True, attached='user ID', convert=int,
                    always_include=True, quotes_recommended=False),
                Arg('channel', argtype=ArgTypes.SPLIT_OPTIONAL,
                    convert=utilities.ChannelConverter(constraint=discord.TextChannel)),
                doc='Dumps logs.', function=autolog_dump)],
        description='Automatically logs chat when a user is banned.',
        elevated_level=1, allow_direct=False, category='tools')]


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'autolog_template': (
            "session            text,"
            "details            text,"
            "query              text,"
            "moderatorid        bigint,"
            "messageid          bigint,"
            "timestamp          bigint,"
            "notes              json,"  # Unfinished
            "id                 serial UNIQUE"
        )
    }


def _check_log_channel(bot, guild):
    """Checks and returns the log channel."""
    log_channel = guild.get_channel(data.get(bot, __name__, 'log_channel', guild_id=guild.id))
    if not log_channel:
        raise CBException("No log channel configured.")
    return log_channel


def _set_logger(bot, channel):
    """Sets a logger for the given channel."""
    default_message_limit = configurations.get(bot, __name__, key='message_limit')
    message_limit = data.get(
        bot, __name__, 'message_limit', guild_id=channel.guild.id, default=default_message_limit)
    logs = data.get(
        bot, __name__, 'logs', guild_id=channel.guild.id,
        default={}, create=True, volatile=True)
    logs[channel.id] = collections.deque(maxlen=message_limit)


def _delete_logger(bot, channel):
    """Removes a logger for the given channel."""
    logs = data.get(
        bot, __name__, 'logs', guild_id=channel.guild.id,
        default={}, create=True, volatile=True)
    if channel.id in logs:
        del logs[channel.id]


async def autolog_channels(bot, context):
    """Sets the channels that will be logged."""
    log_channel = _check_log_channel(bot, context.guild)

    # Toggle channels for logging
    if context.arguments[0]:
        changes = []
        for channel in context.arguments:
            appended = data.list_data_toggle(
                bot, __name__, 'channels', channel.id, guild_id=context.guild.id)
            if appended:
                changes.append('Now logging {}'.format(channel.mention))
                _set_logger(bot, channel)
            else:
                changes.append('No longer logging {}'.format(channel.mention))
                _delete_logger(bot, channel)
        embed = discord.Embed(title='Logging changes', description='\n'.join(changes))

    # Show channels that are currently logged
    else:
        default_message_limit = configurations.get(bot, __name__, key='message_limit')
        message_limit = data.get(
            bot, __name__, 'message_limit', guild_id=context.guild.id,
            default=default_message_limit)
        logged_channel_ids = data.get(bot, __name__, 'channels', guild_id=context.guild.id)
        if not logged_channel_ids:
            raise CBException("No channels are currently logged.")

        # Check logged channels
        # Removes channels that were deleted and have no logged messages
        logged_channels = []
        logs = data.get(bot, __name__, 'logs', guild_id=context.guild.id, volatile=True)
        for channel_id in logged_channel_ids:
            channel = data.get_channel(bot, channel_id, safe=True)
            if channel:
                logged_channels.append(channel)
            else:
                if len(logs[channel_id]):
                    channel = discord.Object(id=channel_id)
                    channel.mention = 'Deleted channel ({})'.format(channel_id)
                    logged_channels.append(channel)
                else:  # No logged messages for removed channel. Delete log.
                    del logs[channel_id]

        embed = discord.Embed(title='Logging info')
        embed.add_field(
            inline=False, name='Logged channels',
            value=', '.join(it.mention for it in logged_channels))
        embed.add_field(name='Dump channel', value=log_channel.mention)
        embed.add_field(name='Logged messages', value=message_limit)

    return Response(embed=embed)


async def autolog_messages(bot, context):
    """Sets the number of messages to log in each channel."""
    _check_log_channel(bot, context.guild)
    data.add(bot, __name__, 'message_limit', context.arguments[0], guild_id=context.guild.id)
    logged_channels = data.get(bot, __name__, 'channels', guild_id=context.guild.id, default=[])
    for channel_id in logged_channels:
        channel = context.guild.get_channel(channel_id)
        _set_logger(bot, channel)
    return Response("{} messages will be logged for each channel.".format(context.arguments[0]))


async def autolog_setchannel(bot, context):
    """Sets the channel where logs will be dumped."""
    data.add(bot, __name__, 'log_channel', context.arguments[0].id, guild_id=context.guild.id)
    return Response("The logging channel is now set to {}".format(context.arguments[0].mention))


async def autolog_dump(bot, context):
    """Dumps logs into the set channel."""
    log_channel = _check_log_channel(bot, context.guild)
    logged_channel_ids = data.get(bot, __name__, 'channels', guild_id=context.guild.id, default=[])
    logged_channels = []
    if context.arguments[0]:
        for channel in context.arguments:
            if channel.id not in logged_channel_ids:
                raise CBException("{} is not being logged.".format(channel.mention))
            else:
                logged_channels.append(channel)
    else:
        for channel_id in logged_channel_ids:
            channel = data.get_channel(bot, channel_id, safe=True)
            if channel:
                logged_channels.append(channel)
            else:
                channel = discord.Object(id=channel_id)
                channel.mention = 'Deleted channel ({})'.format(channel_id)
                logged_channels.append(channel)

    # Build dump data
    logs = data.get(bot, __name__, 'logs', guild_id=context.guild.id, volatile=True)
    details = 'Manual dump by {0} (<@{0.id}>): {1}'.format(
        context.author, context.options['details'])
    dump_data = _build_dump_data(bot, logs, log_channel, details=details)

    # Upload dump data
    logged_messages = await _dump(
        bot, dump_data, log_channel, details=details, query=context.options['query'],
        moderator_id=context.author.id, logged_channels=logged_channels)
    if not logged_messages:
        raise CBException("No messages to log.")
    return Response("Messages dumped in {}".format(log_channel.mention))


def _build_dump_data(bot, logs, log_channel, details=None):
    """Builds dump data from the logs"""
    guild = log_channel.guild
    if not logs:
        return 0
    total_messages = 0
    members = {}
    channels = {}
    for channel_id, logged_messages in logs.items():

        messages = []
        for message_data in logged_messages:

            # Build message edit history
            edits = []
            for edit in message_data['history']:
                logger.debug("For ID: %s, content: %s", edit.id, edit.content)
                dt = (edit.edited_at or edit.created_at).replace(tzinfo=timezone.utc)
                edits.append({
                    'content': edit.content,
                    'embeds': [json.dumps(it.to_dict()) for it in edit.embeds],
                    'time': int(dt.timestamp())
                })
            edit = message_data['history'][-1]

            # Add user info if not found
            author_id = str(edit.author.id)
            if author_id not in members:
                dt = edit.author.joined_at.replace(tzinfo=timezone.utc)
                members[author_id] = {
                    'name': edit.author.name,
                    'discriminator': edit.author.discriminator,
                    'bot': edit.author.bot,
                    'id': str(edit.author.id),
                    'avatar': str(edit.author.avatar_url_as(static_format='png')),
                    'joined': int(dt.timestamp())
                }

            # Add message
            messages.append({
                'history': edits,
                'attachments': [it.url for it in edit.attachments],
                'author': author_id,
                'id': str(edit.id),
                'deleted': message_data['deleted']
            })

            total_messages += 1

        # Add all messages from the channel
        channel = guild.get_channel(channel_id)
        channel_name = channel.name if channel else 'Unknown channel ({})'.format(channel_id)
        channels[str(channel_id)] = {
            'messages': messages,
            'name': channel_name
        }

    # Build full dump
    return {
        'version': DATA_VERSION,
        'guild': {'name': guild.name, 'id': str(guild.id)},
        'members': members,
        'channels': channels,
        'generated': int(time.time()),
        'total': total_messages,
        'details': details
    }


async def _dump(
        bot, dump_data, log_channel, details='No details provided.',
        query=None, moderator_id=None, logged_channels=[]):
    """Dumps the given built dump data to the log channel.
    
    logged_channels specifies what channels to log. If no channels are given, this logs
        all channels by default.
    """
    built_query = '&highlight={}'.format(query) if query else ''
    logged_channel_ids = [it.id for it in logged_channels]
    guild = log_channel.guild

    # Remove extra channels and members
    if logged_channels:
        valid_members = set()
        to_remove = []
        total_messages = 0
        for channel_id, channel_data in dump_data['channels'].items():
            if int(channel_id) in logged_channel_ids:
                for message in channel_data['messages']:
                    valid_members.add(message['author'])
                total_messages += len(channel_data['messages'])
            else:
                to_remove.append(channel_id)
        for it in to_remove:
            del dump_data['channels'][it]
        to_remove = [it for it in dump_data['members'] if it not in valid_members]
        for it in to_remove:
            del dump_data['members'][it]
    else:
        total_messages = dump_data['total']

    # Build full dump string
    full_dump = json.dumps(dump_data)

    # Send logs and get session code
    log_message = await utilities.send_text_as_file(log_channel, full_dump, 'logs')
    url = log_message.attachments[0].url
    session_code = '{}:{}'.format(*[it[::-1] for it in url[::-1].split('/')[2:0:-1]])

    # Build embed data
    embed = discord.Embed(
        title='Click here to view the message logs',
        url='https://jkchen2.github.io/log-viewer?session={}{}'.format(session_code, built_query),
        timestamp=datetime.utcnow())
    embed.add_field(name='Details', value=details, inline=False)
    if logged_channels:
        embed.add_field(name='Channels', value=', '.join(it.mention for it in logged_channels))

    # Add incident number
    entry_data = [
        session_code,
        details,
        query,
        moderator_id,
        None,  # messageid
        int(time.time()),
        Json({})
    ]
    cursor = data.db_insert(
        bot, 'autolog', table_suffix=guild.id, input_args=entry_data, create='autolog_template')
    inserted = cursor.fetchone()
    embed.set_footer(text='Incident #{}'.format(inserted.id))

    # Send embed and update messageid
    message = await log_channel.send(embed=embed)
    data.db_update(
        bot, 'autolog', table_suffix=guild.id, set_arg='messageid=%s',
        where_arg='id=%s', input_args=[message.id, inserted.id])
    return total_messages


def _get_message_logger(bot, channel=None):
    """Gets the message logger if it exists."""
    if isinstance(channel, discord.abc.PrivateChannel):
        return None
    message_loggers = data.get(
        bot, __name__, 'logs', guild_id=channel.guild.id, volatile=True, default={})
    if channel:
        return message_loggers.get(channel.id, None)
    return message_loggers


async def automated_dump_message(bot, guild, details, query=None, moderator_id=None):
    """Generates an automated log menu that asks the user to select channels to log."""
    try:
        log_channel = _check_log_channel(bot, guild)
    except BotException:
        return

    # Fetch and build preliminary dump data
    logs = data.get(bot, __name__, 'logs', guild_id=guild.id, volatile=True)
    if not logs:
        return
    dump_data = _build_dump_data(bot, logs, log_channel, details=details)
    channel_ids = list(logs.keys())
    channel_mentions = '<#' + '>, <#'.join(str(it) for it in channel_ids) + '>'

    # Show confirmation box
    embed = discord.Embed(
        title=':warning: Autolog event triggered', color=discord.Color(0xffcc4d))
    embed.add_field(name='Details', value=details, inline=False)
    embed.add_field(
        name='\u200b', inline=False,
        value='By default, these channels will be logged:\n{}'.format(channel_mentions))
    embed.add_field(name='\u200b', inline=False, value=(
        'Listed channels will be logged in 5 minutes.\n'
        'Click :x: to cancel, :next_track: to log now, or :grey_question: to specify channels.'))

    message = await log_channel.send(embed=embed)

    async def _menu(bot, context, response, result, timed_out):
        if not result and not timed_out:
            return

        # Timed out or skipped waiting period
        if timed_out or (result[0].emoji == '⏭'):
            response.embed.remove_field(2)
            response.embed.set_field_at(1, name='\u200b', value='Logging started.')
            await response.message.edit(embed=response.embed)
            asyncio.ensure_future(_dump(
                bot, dump_data, log_channel, details=details,
                query=query, moderator_id=moderator_id))

        # Cancelled
        elif result[0].emoji == '❌':
            response.embed.remove_field(2)
            response.embed.add_field(
                name='\u200b',
                value='Logging cancelled.')
            await response.message.edit(embed=response.embed)

        # Specify channels
        else:
            response.embed.set_field_at(
                2, name='\u200b', inline=False,
                value='**Type the channels you want to log, separated by spaces.**')
            await response.message.edit(embed=response.embed)
            try:
                await response.message.clear_reactions()
            except:
                pass

            # Read response
            kwargs = {
                'timeout': 300,
                'check': lambda m: m.author == result[1] and m.channel == context.channel
            }
            channels = []
            response.embed.remove_field(2)
            try:
                result = await bot.wait_for('message', **kwargs)
                for it in result.content.split():
                    channel = data.get_channel(bot, it, constraint=discord.TextChannel)
                    if channel.id not in channel_ids:
                        raise CBException("Channel {} not logged.".format(channel.mention))
                    channels.append(channel)
                try:
                    await result.delete()
                except:
                    pass
            except BotException as e:
                logger.debug("Error!")
                response.embed.set_field_at(
                    1, name='\u200b',
                    value='{}\nDefault channels will be logged.'.format(e.error_details))
            except Exception as e:
                logger.debug("Timeout!")
                response.embed.set_field_at(
                    1, name='\u200b',
                    value='Channel selection timed out. Default channels will be logged.')

            # Upload dump data
            await response.message.edit(embed=response.embed)
            await _dump(
                bot, dump_data, log_channel, details=details, query=query,
                moderator_id=moderator_id, logged_channels=channels)

        return False

    extra = {
        'buttons': ['❌', '⏭', '❔'],
        'elevation': Elevation.BOT_MODERATORS,
        'autodelete': 30,
        'kwargs': {'timeout': 300}
    }
    response = Response(
        embed=embed, message_type=MessageTypes.INTERACTIVE, extra=extra, extra_function=_menu)
    await bot.handle_response(message, response, message_reference=message)


@plugins.listen_for('on_message')
async def log_messages(bot, message):
    """Logs incoming messages."""
    message_logger = _get_message_logger(bot, message.channel)
    if message_logger is None or message.type is not discord.MessageType.default:
        return
    message_logger.append({'history': [message], 'deleted': 0})


@plugins.listen_for('on_message_edit')
async def log_edits(bot, before, after):
    """Logs edited messages."""
    message_logger = _get_message_logger(bot, after.channel)
    if not message_logger:
        return
    earliest_message = message_logger[0]['history'][0]
    if after.created_at < earliest_message.created_at:  # Message out of scope
        return

    # Find message in deque
    for message_data in message_logger:
        if message_data['history'][0].id == after.id:
            if before.content == after.content:  # Only embeds were added
                message_data['history'][-1] = after
            else:
                message_data['history'][-1] = before
                message_data['history'].append(after)
            return


@plugins.listen_for('on_message_delete')
async def log_deletes(bot, message):
    """Logs deleted messages."""
    message_logger = _get_message_logger(bot, message.channel)
    if not message_logger:
        return
    earliest_message = message_logger[0]['history'][0]
    if message.created_at < earliest_message.created_at:  # Message out of scope
        return

    # Find message in deque
    for message_data in message_logger:
        if message_data['history'][0].id == message.id:
            message_data['deleted'] = int(time.time())
            return


@plugins.listen_for('on_member_ban')
async def member_banned(bot, guild, user):
    """Dumps logs when a user is banned."""
    try:
        _check_log_channel(bot, guild)
    except BotException:
        return

    # Ensure audit logs have been updated and pull ban information
    await asyncio.sleep(3)
    moderator_id = None
    details = '{0} (<@{0.id}>) was banned'.format(user)
    async for entry in guild.audit_logs(limit=50, action=discord.AuditLogAction.ban):
        if entry.target == user:
            details += ' by {0} (<@{0.id}>): {1}'.format(
                entry.user, entry.reason or 'No reason provided')
            moderator_id = entry.user.id
            break
    logger.debug("Details: %s", details)

    # Show confirmation menu for logging
    await automated_dump_message(bot, guild, details, query=user.id, moderator_id=moderator_id)


@plugins.listen_for('bot_on_ready_boot')
async def setup_loggers(bot):
    """Sets up the loggers for each guild."""
    for guild in bot.guilds:
        logged_channels = data.get(bot, __name__, 'channels', guild_id=guild.id, default=[])
        for channel_id in logged_channels[:]:
            channel = guild.get_channel(channel_id)
            if channel:
                _set_logger(bot, channel)
            else:
                logged_channels.remove(channel_id)


@plugins.permissions_spawner
def setup_permissions(bot):
    return {'view_audit_log': "Allows the bot to get ban reasons."}
