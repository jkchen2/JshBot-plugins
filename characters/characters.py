import discord
import datetime
import time
import json

from urllib.parse import urlparse
from psycopg2.extras import Json

from jshbot import data, utilities, configurations, plugins, logger
from jshbot.exceptions import ConfiguredBotException, BotException, ErrorTypes
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.1.0'
CBException = ConfiguredBotException('Character creator')
uses_configuration = True

DATA_VERSION = 1
data_channel = None
data_channel_webhook_ids = []
COMMON_ATTRIBUTES = ['Species', 'Height', 'Age', 'Gender', 'Sexuality']
CHARACTER_TYPES = {
    'fursona': 'A fursona',
    'oc': 'An OC',
}


class Character():
    def __init__(self, owner, json_data):
        self.owner = owner
        self.data = json_data
        self.name = json_data['name']
        self.clean_name = json_data['clean_name']
        self.attributes = json_data['attributes']
        self.embed_color = json_data['embed_color']
        self.created = json_data['created']


class CharacterConverter():
    def __call__(self, bot, message, value, *a):
        if len(value) > 50:
            raise CBException("Names cannot be longer than 50 characters.")
        clean_name = utilities.clean_text(value)
        cursor = data.db_select(
            bot, from_arg='characters', where_arg='clean_name=%s AND owner_id=%s',
            input_args=(clean_name, message.author.id))
        match = cursor.fetchone()
        if not match:
            raise CBException("A character by that name was not found under your account.")
        return Character(message.author, match.data)


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'character', subcommands=[
            SubCommand(
                Opt('create'),
                Attachment(
                    'character file', optional=True,
                    doc='If you have a saved character file saved from the entry creator, '
                        'you can use it directly here.'),
                doc='Creates a character entry under your name.',
                function=character_create),
            SubCommand(
                Opt('remove'),
                Arg('name', argtype=ArgTypes.MERGED, convert=CharacterConverter()),
                doc='Removes the given character entry under your name.',
                function=character_remove),
            SubCommand(
                Opt('edit'),
                Arg('name', argtype=ArgTypes.MERGED, convert=CharacterConverter()),
                doc='Provides a link that allows you to edit the given character.',
                function=character_edit),
            SubCommand(
                Opt('list'),
                Arg('user', argtype=ArgTypes.MERGED_OPTIONAL, convert=utilities.MemberConverter()),
                doc='Lists the characters of the given user.',
                function=character_list),
            SubCommand(
                Opt('forceremove'),
                Arg('user', convert=utilities.MemberConverter()),
                Arg('character name', argtype=ArgTypes.MERGED),
                doc='Forcibly removes the given character.',
                elevated_level=1, function=character_forceremove),
            SubCommand(
                Arg('user', argtype=ArgTypes.OPTIONAL, convert=utilities.MemberConverter()),
                Arg('character name', argtype=ArgTypes.MERGED_OPTIONAL,
                    doc='Displays the specific character.'),
                doc='Displays a list of character entries of the given user.',
                function=character_display)],
        description='Character database.', category='user data')]


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'characters_template': (
            "owner_id           bigint,"
            "name               text,"
            "clean_name         text,"
            "data               json")
    }

@plugins.on_load
def setup_global_tag_table(bot):
    data.db_create_table(bot, 'characters', template='characters_template')


async def _session_timeout_notify(bot, scheduled_time, payload, search, destination, late):
    logger.debug("Notifying a session timeout.")
    clear_result = await _clear_webhook(bot, search)
    if clear_result:
        messageable = utilities.get_messageable(bot, destination)
        await messageable.send(content="Your character creation/editing session has timed out.")


def _valid_url(url):
    """Checks that the given URL is Discord embed friendly. Or at least, it tries."""

    def _valid_string(segment, main=True):
        if not len(segment):
            return False
        for c in [ord(it.lower()) for it in segment]:
            if not (97 <= c <= 122 or (main and (48 <= c <= 57 or c == 45))):
                return False
        return True

    test = urlparse(url)
    if not (test.scheme and test.netloc and '.' in test.netloc):
        return False
    if test.scheme not in ('http', 'https'):
        return False
    netloc_split = test.netloc.split('.')
    if (len(netloc_split) < 2):
        return False
    tld = test.netloc.split('.')[-1]
    if not (len(tld) >= 2 and _valid_string(tld, main=False)):
        return False
    for segment in netloc_split[:-1]:
        if not _valid_string(segment):
            return False
    for c in [ord(it) for it in url]:
        if not 33 <= c <= 126:
            return False
    return True


async def _create_session(bot, owner, editing=None):
    """Creates a session for character creation or editing"""
    webhook = await data_channel.create_webhook(name='ready:{}'.format(owner.id))

    # Upload data as a single file
    cursor = data.db_select(
        bot, from_arg='characters', where_arg='owner_id=%s', input_args=[owner.id])
    characters = cursor.fetchall() if cursor else []
    create_data = utilities.get_text_as_file(json.dumps({
        "version": DATA_VERSION,
        "webhook": [str(webhook.id), webhook.token],
        "existing_names": list(character.clean_name for character in characters),
        "editing": editing
    }))
    url = await utilities.upload_to_discord(bot, create_data, filename='data', close=True)
    url_segments = [it[::-1] for it in url[::-1].split('/')[2:0:-1]]
    session_code = '{}:{}'.format(*url_segments)

    # Track webhook usage
    data.add(bot, __name__, 'tracker', webhook, user_id=owner.id, volatile=True)
    data.add(bot, __name__, 'owner', owner, user_id=webhook.id, volatile=True)
    data.add(bot, __name__, 'stage', 0, user_id=webhook.id, volatile=True)

    # Add webhook ID to global IDs
    global data_channel_webhook_ids
    data_channel_webhook_ids.append(webhook.id)

    # Send the owner the link
    embed = discord.Embed(
        title='Click here to access the template creator',
        url='https://jkchen2.github.io/character-template/#{}'.format(session_code),
        description='Your session code is:\n`{}`'.format(session_code))
    await owner.send(embed=embed)

    # Schedule a session timeout
    utilities.schedule(
        bot, __name__, time.time() + 7200, _session_timeout_notify,
        search=str(webhook.id), destination='u{}'.format(owner.id),
        info='Character creator session timeout')

    return session_code


async def _process_data(bot, author, url, pass_error=False):
    raw_data = await utilities.download_url(bot, url, use_fp=True)
    try:
        parsed = json.load(raw_data)
    except Exception as e:
        raise CBException("Failed to load the raw data.", e=e)

    # Data version checks would go here

    # Check that values are within proper ranges
    try:
        if 'version' not in parsed:
            raise CBException("Missing version number.")
        if 'type' not in parsed:
            raise CBException("Missing character type.")
        if 'name' not in parsed:
            raise CBException("Missing name.")
        if 'attributes' not in parsed:
            raise CBException("Missing attributes.")
        if 'thumbnail' not in parsed:
            raise CBException("Missing thumbnail.")
        if 'images' not in parsed:
            raise CBException("Missing images.")
        if 'embed_color' not in parsed:
            raise CBException("Missing embed color.")

        error_code = 1
        total_characters = 0
        version = parsed['version']
        if not isinstance(version, int):
            raise CBException("Invalid version type. [int]")
        if version != 1:  # NOTE: Change for newer versions
            raise CBException("Invalid version number.")
        character_type = parsed['type']
        if character_type not in CHARACTER_TYPES:
            raise CBException("Invalid character type.")
        name = parsed['name']
        if not isinstance(name, str):
            raise CBException("Invalid name type. [string]")
        if not 1 <= len(name) <= 100:
            raise CBException("Invalid name length. [1-100]")
        total_characters += len(name)
        attributes = parsed['attributes']
        if not isinstance(attributes, dict):
            raise CBException("Invalid attributes type. [dictionary]")
        if not 0 <= len(attributes) <= 20:
            raise CBException("Invalid number of attributes. [1-20]")
        for key, value in attributes.items():
            if not isinstance(value, str):
                raise CBException("An attribute has an invalid type. [string]")
            if not 1 <= len(key) <= 50:
                raise CBException("Invalid attribute name length. [1-50]")
            if key in COMMON_ATTRIBUTES and not 1 <= len(value) <= 50:
                raise CBException("Invalid common attribute value length. [1-50]")
            elif not 1 <= len(value) <= 1000:
                raise CBException("Invalid attribute value length. [1-1000]")
            total_characters += len(key) + len(value)
        thumbnail = parsed['thumbnail']
        if not isinstance(thumbnail, (str, type(None))):
            raise CBException("Invalid thumbnail type. [string]")
        if isinstance(thumbnail, str) and not _valid_url(thumbnail):
            error_code = 2
            raise CBException("Invalid thumbnail URL.")
        images = parsed['images']
        if not isinstance(images, list):
            raise CBException("Invalid images type. [list]")
        for image in images:
            if not isinstance(image, str):
                raise CBException("Invalid image type. [string]")
            if not 1 <= len(image) <= 500:
                raise CBException("Invalid image URL length. [1-500]")
            if not _valid_url(image):
                error_code = 3
                raise CBException("Invalid image URL.")
        embed_color = parsed['embed_color']
        if not isinstance(embed_color, (int, type(None))):
            raise CBException("Invalid embed color type. [int]")
        if isinstance(embed_color, int) and not 0x0 <= embed_color <= 0xffffff:
            raise CBException("Invalid embed color range. [0x0-0xffffff")

        if total_characters > 3000:
            raise CBException("Total characters exceeded 3000.")

    except BotException as e:
        if pass_error:
            raise e
        else:
            await author.send("The data checks failed. Error:\n{}".format(e.error_details))
            return error_code

    clean_name = utilities.clean_text(name)
    json_data = Json({
        'type': character_type,
        'version': DATA_VERSION,
        'name': name,
        'clean_name': clean_name,
        'owner_id': author.id,
        'attributes': attributes,
        'thumbnail': thumbnail,
        'images': images,
        'embed_color': embed_color,
        'created': int(time.time())
    })

    # Check for edit or entry creation
    cursor = data.db_select(
        bot, select_arg='clean_name', from_arg='characters', where_arg='owner_id=%s',
        input_args=[author.id])
    existing_names = [it[0] for it in cursor.fetchall()] if cursor else []
    if clean_name in existing_names:  # Edit
        data.db_update(
            bot, 'characters', set_arg='data=%s', where_arg='owner_id=%s AND clean_name=%s',
            input_args=(json_data, author.id, clean_name))
        content = "Edited the entry for {}.".format(name)
    else:  # Create
        data.db_insert(bot, 'characters', input_args=[author.id, name, clean_name, json_data])
        content = "Created a new entry for {}.".format(name)

    if pass_error:
        return content
    else:
        await author.send(content)
        return 0


async def _cancel_menu(bot, context, response, result, timed_out):
    if timed_out:
        await response.edit(content="Timed out.")
        return
    if not result:
        return
    else:
        selection = ['🇾', '🇳'].index(result[0].emoji)

    if selection == 0:  # Confirm
        webhook = data.get(bot, __name__, 'tracker', user_id=context.author.id, volatile=True)
        if not webhook:
            raise CBException("The session has already been cancelled.")
        await _clear_webhook(bot, webhook.id)
        utilities.remove_schedule_entries(bot, __name__, search=str(webhook.id))
        await response.message.edit(content=(
            "The session has been cancelled. Run the command "
            "again to create a new session."))
    else:  # Cancel the cancellation
        await response.message.edit(content="Your current session was not cancelled.")
    return False


async def character_create(bot, context):
    """Creates a new character entry."""

    # Check if an entry is currently being created/edited
    tracker = data.get(bot, __name__, 'tracker', user_id=context.author.id, volatile=True)
    if tracker:
        return Response(
            content=(
                "You are currently already creating or editing an entry. "
                "Would you like to cancel your current session?"),
            message_type=MessageTypes.INTERACTIVE,
            extra_function=_cancel_menu,
            extra={'buttons': ['🇾', '🇳']})

    # 10 character limit
    cursor = data.db_select(
        bot, from_arg='characters', where_arg='owner_id=%s', input_args=[context.author.id])
    characters = cursor.fetchall() if cursor else []
    if len(characters) >= 10:
        raise CBException("Cannot create more than 10 characters.")

    # Use the provided character file
    if context.message.attachments:
        content = await _process_data(
            bot, context.author, conext.message.attachments[0].url, pass_error=True)
        return Response(content=content)

    # Use the online entry creator
    else:
        await _create_session(bot, context.author)
        if not context.direct:
            return Response(content="A link to the creation site has been sent to you via DMs.")


async def character_remove(bot, context):
    """Removes a character entry."""
    clean_name = context.arguments[0].clean_name
    data.db_delete(
        bot, 'characters', where_arg='clean_name=%s AND owner_id=%s',
        input_args=(clean_name, context.author.id))
    return Response(content="Character deleted.")


async def character_edit(bot, context):
    """Edits a character entry."""

    # Check if an entry is currently being created/edited
    tracker = data.get(bot, __name__, 'tracker', user_id=context.author.id, volatile=True)
    if tracker:
        return Response(
            content=(
                "You are currently already creating or editing an entry. "
                "Would you like to cancel your current session?"),
            message_type=MessageTypes.INTERACTIVE,
            extra_function=_cancel_menu,
            extra={'buttons': ['🇾', '🇳']})

    await _create_session(bot, context.author, editing=context.arguments[0].data)
    if not context.direct:
        return Response(content="A link to the creation site has been sent to you via DMs.")


async def character_list(bot, context):
    """Lists the characters of the given user."""
    owner = context.arguments[0] if context.arguments[0] else context.author
    cursor = data.db_select(
        bot, from_arg='characters', where_arg='owner_id=%s', input_args=[owner.id])
    characters = cursor.fetchall() if cursor else []
    if not characters:
        raise CBException("{} has no characters.".format(owner.mention))
    embed = discord.Embed(
        title='Character list',
        description='Owner: {}\n{} character{}'.format(
            owner.mention, len(characters), '' if len(characters) == 1 else 's'))
    for character in characters:
        attributes = character.data['attributes']
        common_attributes = []
        for key in COMMON_ATTRIBUTES:
            if key in attributes:
                common_attributes.append('{}: {}'.format(key, attributes[key]))
        if not common_attributes:
            common_attributes = ['No common attributes']
        embed.add_field(name=character.name, value='\u200b{}'.format('\n'.join(common_attributes)))
    return Response(embed=embed)


def _user_character_search(bot, owner, character_search):
    cursor = data.db_select(
        bot, from_arg='characters', where_arg='owner_id=%s', input_args=[owner.id])
    characters = cursor.fetchall() if cursor else []
    if not characters:
        raise CBException("{} has no characters.".format(owner.mention))

    if character_search:
        for character_index, character in enumerate(characters):
            if character.clean_name == character_search:
                break
        else:
            raise CBException(
                "{} has no character named \"{}\".".format(
                    owner.mention, context.arguments[0]))
    else:
        character_index = 0

    return [character_index, 0, characters, owner]


async def character_forceremove(bot, context):
    """Forcibly removes the character of the given user."""
    owner = context.arguments[0]
    if data.is_mod(bot, member=owner):
        if not data.is_admin(bot, context.guild, context.author.id):
            raise CBException("Cannot remove characters of other bot moderators.")
    character_search = utilities.clean_text(context.arguments[1])
    state_data = _user_character_search(bot, owner, character_search)
    character = state_data[2][state_data[0]]

    data.db_delete(
        bot, 'characters', where_arg='clean_name=%s AND owner_id=%s',
        input_args=(character.clean_name, owner.id))

    return Response(content="Character forcefully deleted.")


async def character_display(bot, context):
    """Shows the character entry menu."""

    character_search = None
    if context.arguments[0]:
        owner = context.arguments[0]
        if context.arguments[1]:
            character_search = utilities.clean_text(context.arguments[1])
    else:
        owner = context.author

    state_data = _user_character_search(bot, owner, character_search)
    embed = discord.Embed()
    _build_profile(embed, *state_data)
    return Response(
        embed=embed,
        message_type=MessageTypes.INTERACTIVE,
        extra_function=_character_browser,
        extra={'buttons': ['⏮', '⬅', '➡', '⏭']},
        state_data=state_data)


def _build_profile(embed, character_index, image_index, characters, owner):
    """Edits the given embed for the given character."""
    character = characters[character_index]
    embed.clear_fields()
    if character.data['embed_color'] is not None:
        embed.color = discord.Color(character.data['embed_color'])
    else:
        embed.color = discord.Embed.Empty
    embed.add_field(
        name=character.name, inline=False, value='Character [{}/{}]'.format(
            character_index + 1, len(characters)))
    attributes = character.data['attributes']
    common_attributes = []
    for key in COMMON_ATTRIBUTES:
        if key in attributes:
            common_attributes.append('{}: {}'.format(key, attributes[key]))
    if common_attributes:
        embed.add_field(name='Common attributes', value='\n'.join(common_attributes))
    for key in [it for it in attributes if it not in COMMON_ATTRIBUTES]:
        embed.add_field(name=key, value=attributes[key])
    if character.data['images']:
        image = character.data['images'][image_index]
        image_text = '[Image [{}/{}]]({})\n'.format(
            image_index + 1, len(character.data['images']), image)
    else:
        image = ''
        image_text = ''
    owner_text = '{} by {}'.format(CHARACTER_TYPES[character.data['type']], owner.mention)
    embed.add_field(name='\u200b', value='{}{}'.format(image_text, owner_text), inline=False)
    embed.set_image(url=image)
    embed.set_thumbnail(url=character.data['thumbnail'] if character.data['thumbnail'] else '')
    embed.set_footer(text='Last updated')
    embed.timestamp = datetime.datetime.utcfromtimestamp(character.data['created'])
    return embed


async def _character_browser(bot, context, response, result, timed_out):
    if timed_out or not result:
        return
    selection = ['⏮', '⬅', '➡', '⏭'].index(result[0].emoji)
    if selection in (1, 2):  # Image selection
        total = len(response.state_data[2][response.state_data[0]].data['images'])
        if total:
            delta = 1 if selection == 2 else -1
            response.state_data[1] = (response.state_data[1] + delta) % total
    elif selection in (0, 3):  # Character selection
        total = len(response.state_data[2])
        if total:
            delta = 1 if selection == 3 else -1
            response.state_data[0] = (response.state_data[0] + delta) % total
            response.state_data[1] = 0
    _build_profile(response.embed, *response.state_data)
    await response.message.edit(embed=response.embed)


async def _clear_webhook(bot, webhook_id):
    """Clears the webhook from volatile data."""
    logger.debug("Removing webhook: %s", webhook_id)
    owner = data.remove(bot, __name__, 'owner', user_id=webhook_id, safe=True, volatile=True)
    data.remove(bot, __name__, 'stage', user_id=webhook_id, safe=True, volatile=True)
    if not owner:
        return False
    webhook = data.remove(bot, __name__, 'tracker', user_id=owner.id, volatile=True)
    try:
        await webhook.delete()
        return True
    except Exception as e:
        logger.warn("Failed to delete webhook after data checking failure.")
        return False


async def on_message(bot, message):
    """Intercepts messages to the data channel.
    
    There are 3 separate stages:
    0 - Starting stage (webhook exists)
    1 - User has submitted the file, edit webhook name with return code
    2 - User acknowledges result, requests that the webhook be deleted
    """
    if message.channel != data_channel:
        return

    # Check for valid webhook messages
    webhook_id = message.author.id
    if webhook_id not in data_channel_webhook_ids:
        return

    stage = data.get(bot, __name__, 'stage', user_id=webhook_id, volatile=True)
    if stage is not None:

        if message.content == '1' and stage == 0:  # Progress to stage 1
            owner = data.get(bot, __name__, 'owner', user_id=webhook_id, volatile=True)
            webhook = data.get(bot, __name__, 'tracker', user_id=owner.id, volatile=True)
            result = await _process_data(bot, owner, message.attachments[0].url)

            # Parse result
            data.add(bot, __name__, 'stage', 1, user_id=webhook_id, volatile=True)
            await webhook.edit(name='ok' if result == 0 else 'err:{}'.format(result))

        elif message.content == '2' and stage == 1:  # Progress to stage 2
            await _clear_webhook(bot, webhook_id)

        else:  # Invalid state progression detected (likely duplicate)
            logger.warn("Invalid state progression detected. Message content: %s", message.content)
            await _clear_webhook(bot, webhook_id)
            pass  # TODO: Consider notifying user?

    else:  # Desync

        logger.warn("Webhook state desynchronization detected.")
        await _clear_webhook(bot, webhook_id)
        webhooks = await data_channel.webhooks()
        for webhook in webhooks:  # In case the webhook ID was invalid
            if webhook.id == webhook_id:
                await webhook.delete()
                break


async def bot_on_ready_boot(bot):
    """Sets up the data_channel global"""
    global data_channel
    data_channel = data.get_channel(bot, configurations.get(bot, __name__, key='data_channel'))
    if not data_channel:
        raise CBException("Failed to obtain data channel.", error_type=ErrorTypes.STARTUP)

    # Clear any webhooks (debug)
    webhooks = await data_channel.webhooks()
    for webhook in webhooks:
        logger.debug("Deleting webhook %s", webhook)
        await webhook.delete()
