import asyncio
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
                function=tagremote_stop),
            SubCommand(
                Opt('update'),
                doc='Provides a refreshed tag list. Updates can be '
                    'applied in the settings menu of the tag remote app.',
                function=tagremote_update)
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


def _get_tag_dictionary(bot, guild):
    """Retrieves the tag dictionary of the server."""
    if configurations.get(bot, 'tags.py', 'global_tags'):
        table_suffix = 'global'
    else:
        table_suffix = str(guild.id)
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
    return tag_dictionary


async def _upload_session_data(bot, channel, voice_channel, webhook, tag_dictionary):
    """Uploads the tag dictionary and returns the session code."""
    tag_data = utilities.get_text_as_file(json.dumps({
        'version': DATA_VERSION,
        'guild': str(channel.guild.id),
        'guild_name': channel.guild.name,
        'channel': str(channel.id),
        'channel_name': channel.name,
        'voice_channel': str(voice_channel.id),
        'voice_channel_name': voice_channel.name,
        'webhook': [str(webhook.id), webhook.token],
        'tags': tag_dictionary
    }))
    url = await utilities.upload_to_discord(bot, tag_data, filename='remote_data', close=True)
    url_segments = [it[::-1] for it in url[::-1].split('/')[2:0:-1]]
    return '{}:{}'.format(*url_segments)


async def tagremote_start(bot, context):
    """Starts a tag remote session."""

    # Check for an existing session
    session_data = data.get(bot, __name__, 'data', guild_id=context.guild.id)
    if session_data:
        raise CBException("Session already exists.")
    if not context.channel.permissions_for(context.guild.me).manage_webhooks:
        raise CBException("Missing the `Manage Webhooks` permission.")

    # Retrieve and format tag data
    tag_dictionary = _get_tag_dictionary(bot, context.guild)

    # Check that the user is in an unblocked voice channel
    if not context.author.voice:
        raise CBException("You must be in a voice channel.")
    voice_channel = context.author.voice.channel
    await utilities.join_and_ready(bot, voice_channel, is_mod=context.elevation >= 1)

    # Create webhook
    webhook = await context.channel.create_webhook(name='Tag Remote []')

    # Upload session data
    session_code = await _upload_session_data(
        bot, context.channel, voice_channel, webhook, tag_dictionary)

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


async def tagremote_update(bot, context):
    """Renames the webhook with an updated tag list file."""

    # Check for an existing session
    session_data = data.get(bot, __name__, 'data', guild_id=context.guild.id)
    if not session_data:
        raise CBException("No session available.")
    channel = data.get_channel(bot, session_data['channel'])
    if not channel:
        await _delete_session(bot, context.guild)
        raise CBException("Failed to get the channel.")
    voice_channel = data.get_channel(bot, session_data['voice_channel'])
    if not voice_channel:
        await _delete_session(bot, context.guild)
        raise CBException("Failed to get the voice channel.")
    webhooks = await channel.webhooks()
    if not webhooks:
        await _delete_session(bot, context.guild)
        raise CBException("No webhooks available.")
    for webhook in webhooks:
        if webhook.id == session_data['webhook']:
            break
    else:
        await _delete_session(bot, context.guild)
        raise CBException("Webhook not found.")

    tag_dictionary = _get_tag_dictionary(bot, context.guild)
    session_code = await _upload_session_data(bot, channel, voice_channel, webhook, tag_dictionary)

    updated_code = session_code.split(':')[1]
    await webhook.edit(name='Tag Remote [{}]'.format(updated_code))

    return Response(
        content="Tag data refreshed. Update the remote on your phone via the options menu.")


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


async def on_message(bot, message):
    """Reads webhook messages."""
    if message.author.id in WEBHOOK_SET:
        session_data = data.get(bot, __name__, 'data', guild_id=message.guild.id)
        voice_channel = data.get_channel(bot, session_data['voice_channel'], guild=message.guild)

        # Ignore if nobody is in the channel
        if not [it for it in voice_channel.members if not it.bot]:
            pass

        # Retrieve tag
        elif message.content.startswith('[Retrieve]'):
            tag_name = message.content[10:].strip()
            try:
                tag = TAG_CONVERTER(bot, message, tag_name, channel_bypass=voice_channel)
            except BotException as e:
                logger.warn("Failed to retrieve tag: %s", e)
            else:
                tags_plugin = bot.plugins['tags.py']
                url = random.choice(tag.value)
                try:
                    await tags_plugin._play_sound_tag(bot, tag, url, voice_channel, delay=-1)
                except BotException as e:
                    logger.warn("Failed to play tag: %s", e)
                else:
                    tags_plugin._update_hits(bot, tag.key, message.author.id, message.guild.id)

        # Stop audio
        elif message.content == '[Stop audio]':
            voice_client = message.guild.voice_client
            if (voice_client and
                    voice_client.channel == voice_channel and
                    voice_client.is_playing()):
                voice_client.stop()

        # Always remove messages
        await asyncio.sleep(3)
        try:
            await message.delete()
        except:
            pass
