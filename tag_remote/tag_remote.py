import random
import json
import discord

from jshbot import utilities, configurations, plugins, logger, data
from jshbot.exceptions import BotException, ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.1.0'
CBException = ConfiguredBotException('Tag remote')
uses_configuration = False

DATA_VERSION = 1
WEBHOOK_SET = set()
TAG_CONVERTER = None

@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'tagremote', subcommands=[
            SubCommand(doc='Gets the current remote session.', function=tagremote),
            SubCommand(
                Opt('start'),
                doc='Starts a sound tag remote session.',
                function=tagremote_start),
            SubCommand(
                Opt('stop'),
                doc='Stops the current sound tag remote session.',
                function=tagremote_stop)
        ],
        description='Call sound tags through your phone.'
    )]


async def tagremote(bot, context):
    """Gets the current session data as a link."""
    session_data = data.get(bot, __name__, 'data', guild_id=context.guild.id)
    if not session_data:
        raise CBException(
            "No session available.\nStart one with `{}tagremote start`".format(
                utilities.get_invoker(bot, guild=context.guild)))

    channel_id, session_code = session_data['channel'], session_data['session']
    voice_channel_id = session_data['voice_channel']
    channel_mention = data.get_channel(bot, channel_id, guild=context.guild).mention
    voice_channel_mention = data.get_channel(bot, voice_channel_id, guild=context.guild).mention
    description = 'The session code is:\n`{}`\nThe session is attached to {} and {}'.format(
        session_code, channel_mention, voice_channel_mention)
    return Response(embed=discord.Embed(
        title='Tap here on your phone to use the tag remote',
        url='https://jkchen2.github.io/tag-remote/#{}'.format(session_code),
        description=description))


async def tagremote_start(bot, context):
    """Starts a tag remote session."""

    # Check for an existing session
    session_data = data.get(bot, __name__, 'data', guild_id=context.guild.id)
    if session_data:
        raise CBException("Session already exists.")
    if not context.channel.permissions_for(context.guild.me).manage_webhooks:
        raise CBException("Missing the `Manage Webhooks` permission.")

    # Retrieve and format tag data
    if configurations.get(bot, 'tags.py', 'global_tags'):
        table_suffix = 'global'
    else:
        table_suffix = str(context.guild.id)
    tags_plugin = bot.plugins['tags.py']
    sound_bit = tags_plugin._get_flag_bits(['sound'])
    private_bit = tags_plugin._get_flag_bits(['private'])
    cursor = data.db_select(
        bot, from_arg='tags', table_suffix=table_suffix,
        where_arg='flags & %s = %s AND flags & %s = 0',
        input_args=[sound_bit, sound_bit, private_bit])
    raw_tag_list = cursor.fetchall() if cursor else []
    if not raw_tag_list:
        raise CBException("No sound tags available.")
    tag_dictionary = {}
    for tag in raw_tag_list:
        tag_dictionary[tag.key] = {'name': tag.name, 'hits': tag.hits}

    # Check that the user is in an unblocked voice channel
    if not context.author.voice:
        raise CBException("You must be in a voice channel.")
    voice_channel = context.author.voice.channel
    await utilities.join_and_ready(bot, voice_channel, is_mod=context.elevation >= 1)

    # Create webhook
    webhook = await context.channel.create_webhook(name='Tag Remote []')

    # Upload session data
    tag_data = utilities.get_text_as_file(json.dumps({
        'version': DATA_VERSION,
        'guild': str(context.guild.id),
        'guild_name': context.guild.name,
        'channel': str(context.channel.id),
        'channel_name': context.channel.name,
        'voice_channel': str(voice_channel.id),
        'voice_channel_name': voice_channel.name,
        'webhook': [str(webhook.id), webhook.token],
        'tags': tag_dictionary
    }))
    url = await utilities.upload_to_discord(bot, tag_data, filename='remote_data', close=True)
    url_segments = [it[::-1] for it in url[::-1].split('/')[2:0:-1]]
    session_code = '{}:{}'.format(*url_segments)

    # Track session data
    session_data = {
        'webhook': webhook.id,
        'channel': context.channel.id,
        'voice_channel': voice_channel.id,
        'session': session_code
    }
    data.add(bot, __name__, 'data', session_data, guild_id=context.guild.id)
    data.list_data_append(bot, __name__, 'webhooks', webhook.id, duplicates=False)
    WEBHOOK_SET.add(webhook.id)

    return await tagremote(bot, context)


async def tagremote_stop(bot, context):
    await _delete_session(bot, context.guild)
    return Response(content="The session has been stopped.")


async def _delete_session(bot, guild):
    """Deletes the session for the given guild."""
    session_data = data.remove(bot, __name__, 'data', guild_id=guild.id, safe=True)
    if not session_data:
        raise CBException("Session does not exist.")
    channel_id, webhook_id = session_data['channel'], session_data['webhook']
    channel = data.get_channel(bot, channel_id, safe=True)
    webhooks = await channel.webhooks()
    for webhook in webhooks:
        if webhook.id == webhook_id:
            await webhook.delete()
            break
    else:
        logger.warn('Webhook to delete (%s) not found!', webhook_id)
    try:
        WEBHOOK_SET.remove(webhook_id)
    except KeyError:
        logger.warn("Webhook not found in WEBHOOK_SET")
    data.list_data_remove(bot, __name__, 'webhooks', value=webhook_id, safe=True)

    if guild.voice_client and guild.voice_client.channel.id == session_data['voice_channel']:
        await utilities.stop_audio(bot, guild)


async def bot_on_ready_boot(bot):
    global WEBHOOK_SET, TAG_CONVERTER
    TAG_CONVERTER = bot.plugins['tags.py'].TagConverter(
        apply_checks=True, voice_channel_bypass=True)
    WEBHOOK_SET = set(data.get(bot, __name__, 'webhooks', default=[]))
    permissions = { 'manage_webhooks': "Allows tags to be called by webhook." }
    utilities.add_bot_permissions(bot, __name__, **permissions)


async def on_voice_state_update(bot, member, before, after):
    """Detects when all members have left the channel."""
    session_data = data.get(bot, __name__, 'data', guild_id=member.guild.id)
    if (session_data and before.channel and
            before.channel.id == session_data['voice_channel'] and
            not [it for it in before.channel.members if not it.bot]):
        await _delete_session(bot, member.guild)
        channel = data.get_channel(bot, session_data['channel'], safe=True)
        await channel.send('The session has been stopped automatically.')


async def on_message(bot, message):
    """Reads webhook messages."""
    if message.author.id in WEBHOOK_SET:
        session_data = data.get(bot, __name__, 'data', guild_id=message.guild.id)
        voice_channel = data.get_channel(bot, session_data['voice_channel'], guild=message.guild)
        if not [it for it in voice_channel.members if not it.bot]:
            await _delete_session(bot, message.guild)
            channel = data.get_channel(bot, session_data['channel'], safe=True)
            await channel.send('The session has been stopped automatically.')
            return

        if message.content.startswith('[Retrieve]'):
            tag_name = message.content[10:].strip()
            try:
                tag = TAG_CONVERTER(bot, message, tag_name, channel_bypass=voice_channel)
            except BotException as e:
                logger.warn("Failed to retrieve tag: %s", e)
                return
            tags_plugin = bot.plugins['tags.py']
            url = random.choice(tag.value)
            try:
                await tags_plugin._play_sound_tag(bot, tag, url, voice_channel, delay=-1)
            except BotException as e:
                logger.warn("Failed to play tag: %s", e)
                return
            tags_plugin._update_hits(bot, tag.key, message.author.id, message.guild.id)

        elif message.content == '[Stop audio]':
            voice_client = message.guild.voice_client
            if (voice_client and
                    voice_client.channel == voice_channel and
                    voice_client.is_playing()):
                voice_client.stop()
