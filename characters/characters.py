import discord
import datetime
import time
import json
import codecs

from urllib.parse import urlparse
from psycopg2.extras import Json

from jshbot import data, utilities, configurations, plugins, logger
from jshbot.exceptions import ConfiguredBotException, BotException, ErrorTypes
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.2'
CBException = ConfiguredBotException('Character creator')
uses_configuration = True

DATA_VERSION = 2
DATA_CHANNEL = None
DATA_CHANNEL_WEBHOOK_IDS = []
COMMON_ATTRIBUTES = ['Species', 'Height', 'Age', 'Gender', 'Sexuality']
CHARACTER_TYPES = {
    'fursona': ['A fursona', 'Fursona'],
    'oc': ['An OC', 'OC'],
}


class Character():
    def __init__(self, owner, json_data, tags):
        self.owner = owner
        self.data = json_data
        self.name = json_data['name']
        self.clean_name = json_data['clean_name']
        self.attributes = json_data['attributes']
        self.embed_color = json_data['embed_color']
        self.created = json_data['created']
        self.tags = tags


class CharacterConverter():
    def __call__(self, bot, message, value, *a):
        if len(value) > 50:
            raise CBException("Names cannot be longer than 50 characters.")
        clean_name = _clean_text_wrapper(value)
        cursor = data.db_select(
            bot, from_arg='characters', where_arg='clean_name=%s AND owner_id=%s',
            input_args=(clean_name, message.author.id))
        match = cursor.fetchone()
        if not match:
            raise CBException("You don't have a character by that name.")
        return Character(message.author, match.data, match.tags)


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'character', subcommands=[
            SubCommand(
                Opt('create'),
                Attachment(
                    'character file', optional=True,
                    doc='If you have a character file saved from the entry creator, '
                        'you can use it directly here.'),
                doc='Creates a character entry under your name.',
                function=character_create),
            SubCommand(
                Opt('remove'),
                Arg('character name', argtype=ArgTypes.MERGED, convert=CharacterConverter()),
                doc='Removes the given character entry under your name.',
                function=character_remove),
            SubCommand(
                Opt('edit'),
                Arg('character name', argtype=ArgTypes.MERGED, convert=CharacterConverter()),
                doc='Provides a link that allows you to edit the given character.',
                function=character_edit),
            SubCommand(
                Opt('list'),
                Arg('user', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.MemberConverter(server_only=False)),
                doc='Lists the characters of the given user.',
                function=character_list),
            SubCommand(
                Opt('search'),
                Arg('tag', argtype=ArgTypes.SPLIT),
                doc='Searches for characters with the given tag(s).',
                function=character_search),
            SubCommand(
                Opt('browse'),
                Arg('letter', argtype=ArgTypes.MERGED_OPTIONAL),
                doc='Browses all characters.',
                function=character_browse),
            SubCommand(
                Opt('forceremove'),
                Arg('user', convert=utilities.MemberConverter()),
                Arg('character name', argtype=ArgTypes.MERGED),
                doc='Forcibly removes the given character.',
                elevated_level=3, function=character_forceremove),
            SubCommand(
                Opt('user', attached='owner name', optional=True,
                    convert=utilities.MemberConverter(server_only=False),
                    doc='Searches for characters this user has made.'),
                Arg('character name', argtype=ArgTypes.MERGED_OPTIONAL),
                doc='Displays a list of character entries of the given user.',
                confidence_threshold=3, function=character_display)],
        description='Character database.', category='user data')]


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'characters_template': (
            "owner_id           bigint,"
            "name               text,"
            "clean_name         text,"
            "data               json,"
            "tags               text[],"
            "modified           timestamp")
    }

@plugins.on_load
def setup_characters_table(bot):
    """Creates the characters table and updates outdated entries."""
    data.db_create_table(bot, 'characters', template='characters_template')
    cursor = data.db_execute(
        bot, 'SELECT column_name FROM INFORMATION_SCHEMA.COLUMNS WHERE table_name = %s',
        input_args=['characters'])
    columns = [it[0] for it in cursor.fetchall()]
    if 'tags' not in columns:
        data.db_execute(bot, 'ALTER TABLE characters ADD COLUMN tags text[]')
    if 'modified' not in columns:
        data.db_execute(bot, 'ALTER TABLE characters ADD COLUMN modified timestamp')

    name_index = 'IX_character_order'
    if not data.db_exists(bot, name_index):
        data.db_execute(bot, 'CREATE INDEX {} ON characters (clean_name ASC)'.format(name_index))

    # Select all entries and convert
    cursor = data.db_select(bot, from_arg='characters')
    for entry in cursor.fetchall():
        if entry.data['version'] == 1:  # NOTE: Change per version bump
            dt = datetime.datetime.utcfromtimestamp(entry.data['created'])
            new_data = entry.data
            tags = [new_data['type'], entry.clean_name]
            new_data['attribute_order'] = list(new_data['attributes'].keys())
            new_data['images'] = [[it, '', ''] for it in new_data['images']]
            new_data['tags'] = tags
            new_data['version'] = DATA_VERSION
            data.db_update(
                bot, 'characters', set_arg='data=%s, tags=%s, modified=%s',
                where_arg='clean_name=%s AND owner_id=%s',
                input_args=(Json(new_data), tags, dt, entry.clean_name, entry.owner_id))


def _clean_text_wrapper(text, lowercase=True):
    """Wraps the text cleaner for ASCII only alphanum with hyphens and underscores."""
    def _custom(x):
        c = ord(x)
        if (48 <= c <= 57 or
            65 <= c <= 90 or
            97 <= c <= 122 or
            c in (95, 45)):
            return x
        return ''
    return utilities.clean_text(text, custom=_custom, lowercase=lowercase)


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

    # Discord only accepts http or https
    if test.scheme not in ('http', 'https'):
        return False

    # Test for valid netloc
    netloc_split = test.netloc.split('.')
    if (len(netloc_split) < 2):
        return False  # http://foo
    tld = test.netloc.split('.')[-1]
    if not (len(tld) >= 2 and _valid_string(tld, main=False)):
        return False  # http://foo.123
    for segment in netloc_split[:-1]:
        if not _valid_string(segment):
            return False  # http://foo..bar or http://fo*o.bar
    for c in url:
        if not 33 <= ord(c) <= 126:
            return False  # non-ASCII only URLs
    return True


async def _create_session(bot, owner, editing=None):
    """Creates a session for character creation or editing"""
    webhook = await DATA_CHANNEL.create_webhook(name='ready:{}'.format(owner.id))

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
    url_segments = [it[::-1] for it in url[::-1].split('/')[2:0:-1]]  # sorry
    session_code = '{}:{}'.format(*url_segments)

    # Track webhook usage
    data.add(bot, __name__, 'tracker', webhook, user_id=owner.id, volatile=True)
    data.add(bot, __name__, 'owner', owner, user_id=webhook.id, volatile=True)
    data.add(bot, __name__, 'stage', 0, user_id=webhook.id, volatile=True)

    # Add webhook ID to global IDs
    global DATA_CHANNEL_WEBHOOK_IDS
    DATA_CHANNEL_WEBHOOK_IDS.append(webhook.id)

    # Send the owner the link
    embed = discord.Embed(
        title='Click here to access the character creator',
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
    """Checks the given data and edits/adds an entry to the database."""
    error_code = 1
    raw_data = await utilities.download_url(bot, url, use_fp=True)
    reader = codecs.getreader('utf-8')  # SO: 6862770
    try:
        parsed = json.load(reader(raw_data))
    except Exception as e:
        raise CBException("Failed to load the raw data.", e=e)

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
        if 'attribute_order' not in parsed:
            raise CBException("Missing attribute order.")
        if 'thumbnail' not in parsed:
            raise CBException("Missing thumbnail.")
        if 'images' not in parsed:
            raise CBException("Missing images.")
        if 'embed_color' not in parsed:
            raise CBException("Missing embed color.")
        if 'tags' not in parsed:
            raise CBException("Missing tags.")

        # Check version
        total_characters = 0
        version = parsed['version']
        if not isinstance(version, int):
            raise CBException("Invalid version type. [int]")
        if version != DATA_VERSION:
            error_code = 4
            raise CBException(
                "Invalid or outdated data format. Please use the character creator site.")

        # Check type and name
        character_type = parsed['type']
        if character_type not in CHARACTER_TYPES:
            raise CBException("Invalid character type.")
        name = parsed['name']
        clean_name = _clean_text_wrapper(name)
        if not isinstance(name, str):
            raise CBException("Invalid name type. [string]")
        if not 1 <= len(name) <= 100:
            raise CBException("Invalid name length. [1-100]")
        total_characters += len(name)

        # Check attributes
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

        # Check thumbnail
        thumbnail = parsed['thumbnail']
        if not isinstance(thumbnail, (str, type(None))):
            raise CBException("Invalid thumbnail type. [string]")
        if isinstance(thumbnail, str) and not _valid_url(thumbnail):
            error_code = 2
            raise CBException("Invalid thumbnail URL.")

        # Check images
        images = parsed['images']
        if not isinstance(images, list):
            raise CBException("Invalid images type. [list]")
        if not 0 <= len(images) <= 10:
            raise CBException("Invalid number of images. [0-10]")
        for image in images:
            if not isinstance(image, list):
                raise CBException("Invalid image type. [list]")
            if len(image) != 3:
                raise CBException("Invalid image metadata length. [3]")
            for meta in image:
                if not isinstance(meta, str):
                    raise CBException("Invalid image metadata type. [string]")
            if not 1 <= len(image[0]) <= 500:  # Direct image URL
                raise CBException("Invalid direct image URL length. [1-500]")
            if not _valid_url(image[0]):
                error_code = 3
                raise CBException("Invalid direct image URL.")
            if not 0 <= len(image[1]) <= 500:  # Source URL
                raise CBException("Invalid source URL length. [0-500]")
            if not 0 <= len(image[2]) <= 100:  # Caption
                raise CBException("Invalid image caption length. [0-100]")

        # Check embed color
        embed_color = parsed['embed_color']
        if not isinstance(embed_color, (int, type(None))):
            raise CBException("Invalid embed color type. [int]")
        if isinstance(embed_color, int) and not 0x0 <= embed_color <= 0xffffff:
            raise CBException("Invalid embed color range. [0x0-0xffffff]")

        # Version 2 stuff
        attribute_order = parsed['attribute_order']
        if not isinstance(attribute_order, list):
            raise CBException("Invalid attribute order type. [list]")
        tags = parsed['tags']
        if not isinstance(tags, list):
            raise CBException("Invalid tags type. [list]")

        # Check attribute_order
        order_set = set(attribute_order)
        attribute_set = set(attributes)
        if len(attribute_order) != len(order_set):
            raise CBException("Duplicate attribute order entry.")
        if order_set != attribute_set:
            raise CBException("Attribute order does not match attribute set.")

        # Check tags
        tags = parsed['tags']
        tags_raw = parsed['tags_raw']
        if not 0 <= len('#'.join(tags)) <= 260:  # +60 for name and type
            raise CBException("Invalid tags length. [0-200]")
        if clean_name not in tags:
            raise CBException("Character name not in tags.")
        for character_type in CHARACTER_TYPES:
            if character_type in tags:
                break
        else:
            raise CBException("Character type not in tags.")
        if len(set(tags)) != len(tags):
            raise CBException("Duplicate tags exist.")
        for tag in tags:
            test = _clean_text_wrapper(tag, lowercase=False)
            if test != tag:
                raise CBException("Invalid tag.")
            total_characters += len(tag)

        if total_characters > 3000:
            raise CBException("Total characters exceeded 3000.")

    except BotException as e:
        if pass_error:
            raise e
        else:
            await author.send("The data checks failed. Error:\n{}".format(e.error_details))
            return error_code

    created_time = int(time.time())
    dt = datetime.datetime.utcfromtimestamp(created_time)

    json_data = Json({
        'type': character_type,
        'version': DATA_VERSION,
        'name': name,
        'clean_name': clean_name,
        'owner_id': author.id,
        'attributes': attributes,
        'attribute_order': attribute_order,
        'thumbnail': thumbnail,
        'images': images,
        'embed_color': embed_color,
        'tags': tags,
        'tags_raw': tags_raw,
        'created': created_time
    })

    # Check for edit or entry creation
    cursor = data.db_select(
        bot, select_arg='clean_name', from_arg='characters', where_arg='owner_id=%s',
        input_args=[author.id])
    existing_names = [it[0] for it in cursor.fetchall()] if cursor else []
    if clean_name in existing_names:  # Edit
        data.db_update(
            bot, 'characters', set_arg='name=%s, data=%s, tags=%s, modified=%s',
            where_arg='owner_id=%s AND clean_name=%s',
            input_args=(name, json_data, tags, dt, author.id, clean_name))
        content = "Edited the entry for {}.".format(name)
    else:  # Create
        data.db_insert(
            bot, 'characters', input_args=[author.id, name, clean_name, json_data, tags, dt])
        content = "Created a new entry for {}.".format(name)

    if pass_error:
        return content
    else:
        await author.send(content)
        return 0


async def _cancel_menu(bot, context, response, result, timed_out):
    if timed_out:
        await response.message.edit(content="Timed out.")
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
                "You are currently already creating or editing a character entry. "
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
            bot, context.author, context.message.attachments[0].url, pass_error=True)
        return Response(content=content)

    # Use the online entry creator
    else:
        await _create_session(bot, context.author)
        if not context.direct:
            await context.message.add_reaction('📨')


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
                "You are currently already creating or editing a character entry. "
                "Would you like to cancel your current session?"),
            message_type=MessageTypes.INTERACTIVE,
            extra_function=_cancel_menu,
            extra={'buttons': ['🇾', '🇳']})

    await _create_session(bot, context.author, editing=context.arguments[0].data)
    if not context.direct:
        await context.message.add_reaction('📨')


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


def _user_character_search(bot, command_author, owner=None, character_search=None):
    """Finds characters under the given owner, search, or both."""
    # Setup select arguments
    where_args, input_args = [], []
    if not (owner or character_search):
        where_args.append('owner_id=%s')
        input_args.append(command_author.id)
    if owner:
        where_args.append('owner_id=%s')
        input_args.append(owner.id)
    if character_search:
        where_args.append('clean_name=%s')
        input_args.append(character_search)

    # Get character list
    cursor = data.db_select(
        bot, from_arg='characters', where_arg=' AND '.join(where_args), input_args=input_args)
    characters = cursor.fetchall() if cursor else []
    if not characters:
        if owner:
            cursor = data.db_select(
                bot, from_arg='characters', where_arg='owner_id=%s', input_args=[owner.id])
            owner_characters = cursor.fetchall() if cursor else []
            if owner_characters:
                raise CBException(
                    "{} has no character named \"{}\".".format(
                        owner.mention, character_search))
            else:
                raise CBException("{} has no character entries.".format(owner.mention))
        elif character_search:
            raise CBException("No character named \"{}\" was found.".format(character_search))
        else:
            raise CBException(
                "You have no character entries!\n"
                "You can create one with `{}character create`".format(
                    utilities.get_invoker(bot, getattr(command_author, 'guild', None))))

    # Check if character list contains characters made by the command author
    character_index = 0
    if not owner:
        for index, character in enumerate(characters):
            if character.owner_id == command_author.id:
                character_index = index
                break

    return [character_index, characters]


async def character_search(bot, context):
    """Searches for characters with the given list of tags."""
    tags = [_clean_text_wrapper(it) for it in context.arguments]
    cursor = data.db_select(
        bot, from_arg='characters', where_arg='tags @> %s',
        input_args=[tags], additional='ORDER BY clean_name ASC')
    character_listing = cursor.fetchall() if cursor else []
    if not character_listing:
        raise CBException("No characters found matching those tags.")
    embed = discord.Embed(
        title=':book: Character search', description='{} character{} matching: #{}'.format(
            len(character_listing), '' if len(character_listing) == 1 else 's', ' #'.join(tags)))
    state_data = [0, character_listing]
    return Response(
        embed=_build_browser_menu(bot, embed, *state_data),
        message_type=MessageTypes.INTERACTIVE,
        extra_function=_browser_menu,
        extra={'buttons': ['⬅', '➡'], 'userlock': False},
        state_data=state_data)


def _character_one_liner(bot, character):
    """Formats a character entry as a single line for browsing."""
    owner = data.get_member(bot, character.owner_id, safe=True, attribute='mention')
    character_type = CHARACTER_TYPES[character.data['type']][1]
    return '**{}** [{}] by {}'.format(
        character.name, character_type, owner if owner else 'Unknown')


def _build_browser_menu(bot, embed, page_index, character_listing):
    """Builds a browser page given the index and character list."""
    embed.clear_fields()

    # At most, 10 entries per page
    characters = character_listing[10*page_index:10*page_index + 10]
    search_letter = characters[0].clean_name[0]
    search_letter_names = []
    for character in characters:
        current_letter = character.clean_name[0]

        # New letter found. Add last group of characters
        if current_letter != search_letter:
            embed.add_field(
                name=search_letter.upper(), value='\n'.join(search_letter_names), inline=False)
            search_letter = current_letter
            search_letter_names = []

        # Add character to the search letter names list
        search_letter_names.append(_character_one_liner(bot, character))

    # Add last group of characters
    embed.add_field(
        name=search_letter.upper(), value='\n'.join(search_letter_names), inline=False)

    total_pages = int((len(character_listing) - 1) / 10 + 1)
    embed.add_field(
        name='\u200b', value='Page [ {} / {} ]'.format(page_index + 1, total_pages), inline=False)
    return embed


async def _browser_menu(bot, cotext, response, result, timed_out):
    """Browser for searches and the browse command."""
    if timed_out or not result:
        return
    selection = ['⬅', '➡'].index(result[0].emoji)
    total = int((len(response.state_data[1]) - 1) / 10 + 1)
    delta = 1 if selection == 1 else -1
    response.state_data[0] = (response.state_data[0] + delta) % total
    _build_browser_menu(bot, response.embed, *response.state_data)
    await response.message.edit(embed=response.embed)


async def character_browse(bot, context):
    """Browses a list of all characters."""
    character_listing = data.db_select(
        bot, from_arg='characters', additional='ORDER BY clean_name ASC').fetchall()
    if not character_listing:
        raise CBException("There are no characters in the database.")
    page_index = 0
    if context.arguments[0]:
        search = context.arguments[0]
        closest_index = 0
        for index, character in enumerate(character_listing):
            if search <= character.clean_name:
                closest_index = index
            else:
                break
        page_index = int(closest_index / 10)  # 10 entries per page
    embed = discord.Embed(
        title=':book: Character browser', description='{} total character{}'.format(
            len(character_listing), '' if len(character_listing) == 1 else 's'))
    state_data = [page_index, character_listing]
    return Response(
        embed=_build_browser_menu(bot, embed, *state_data),
        message_type=MessageTypes.INTERACTIVE,
        extra_function=_browser_menu,
        extra={'buttons': ['⬅', '➡'], 'userlock': False},
        state_data=state_data)


async def character_forceremove(bot, context):
    """Forcibly removes the character of the given user."""
    owner = context.arguments[0]
    if data.is_mod(bot, member=owner):
        if not data.is_admin(bot, context.guild, context.author.id):
            raise CBException("Cannot remove characters of other bot moderators.")
    character_search = utilities.clean_text(context.arguments[1])
    search_result = _user_character_search(bot, context.author, owner, character_search)
    character = search_result[1][search_result[0]]

    data.db_delete(
        bot, 'characters', where_arg='clean_name=%s AND owner_id=%s',
        input_args=(character.clean_name, owner.id))

    return Response(content="Character forcefully deleted.")


async def character_display(bot, context):
    """Shows the character entry menu."""
    owner = context.options.get('user')
    if context.arguments[0]:
        character_search = utilities.clean_text(context.arguments[0])
    else:
        character_search = None
    state_data = _user_character_search(bot, context.author, owner, character_search) + [0]
    return Response(
        embed=_build_profile(bot, discord.Embed(), *state_data),
        message_type=MessageTypes.INTERACTIVE,
        extra_function=_character_entry_browser,
        extra={'buttons': ['⏮', '⬅', '➡', '⏭'], 'userlock': False},
        state_data=state_data)


def _build_profile(bot, embed, character_index, characters, image_index):
    """Edits the given embed for the given character."""
    character = characters[character_index]
    owner = data.get_member(bot, character.owner_id, safe=True, attribute='mention')
    owner = owner if owner else 'Unknown'
    version = character.data['version']
    embed.clear_fields()
    if character.data['embed_color'] is not None:
        embed.color = discord.Color(character.data['embed_color'])
    else:
        embed.color = discord.Embed.Empty

    # Owner description
    owner_text = '{} by {}'.format(CHARACTER_TYPES[character.data['type']][0], owner)
    embed.add_field(
        name=character.name, inline=False, value='Character [ {} / {} ] | {}'.format(
            character_index + 1, len(characters), owner_text))
    attributes = character.data['attributes']
    common_attributes = []
    for key in COMMON_ATTRIBUTES:
        if key in attributes:
            common_attributes.append('{}: {}'.format(key, attributes[key]))
    if common_attributes:
        embed.add_field(name='Common attributes', value='\n'.join(common_attributes))

    # Version 2: Use attribute order
    attribute_order = character.data['attribute_order']
    for key in [it for it in attribute_order if it not in COMMON_ATTRIBUTES]:
        embed.add_field(name=key, value=attributes[key])

    # Image/caption and thumbnail
    if character.data['images']:
        image, source, caption = character.data['images'][image_index]
        image_text = '[Image [ {} / {} ]]({}){}'.format(
            image_index + 1, len(character.data['images']), source or image,
            ('\n' + caption) if caption else '')
        embed.add_field(name='\u200b', value=image_text, inline=False)
        embed.set_image(url=image)
    else:
        embed.set_image(url='')
    embed.set_thumbnail(url=character.data['thumbnail'] if character.data['thumbnail'] else '')

    # Version 2: Show tags
    embed.set_footer(text='Tags: #{} | Last updated'.format(' #'.join(character.tags)))

    embed.timestamp = datetime.datetime.utcfromtimestamp(character.data['created'])
    return embed


async def _character_entry_browser(bot, context, response, result, timed_out):
    if timed_out or not result:
        return
    selection = ['⏮', '⬅', '➡', '⏭'].index(result[0].emoji)
    if selection in (1, 2):  # Image selection
        total = len(response.state_data[1][response.state_data[0]].data['images'])
        if total:
            delta = 1 if selection == 2 else -1
            response.state_data[2] = (response.state_data[2] + delta) % total
    elif selection in (0, 3):  # Character selection
        total = len(response.state_data[1])
        if total:
            delta = 1 if selection == 3 else -1
            response.state_data[0] = (response.state_data[0] + delta) % total
            response.state_data[2] = 0
    _build_profile(bot, response.embed, *response.state_data)
    await response.message.edit(embed=response.embed)


async def _clear_webhook(bot, webhook_id):
    """Clears the webhook from volatile data."""
    logger.debug("Removing webhook: %s", webhook_id)
    utilities.remove_schedule_entries(bot, __name__, search=str(webhook_id))
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


@plugins.listen_for('on_message')
async def check_webhook_messages(bot, message):
    """Intercepts webhook messages to the data channel.
    
    There are 3 separate stages:
    0 - Starting stage (webhook exists)
    1 - User has submitted the file, edit webhook name with return code
    2 - User acknowledges result, requests that the webhook be deleted
    """
    if message.channel != DATA_CHANNEL:
        return

    # Check for valid webhook messages
    webhook_id = message.author.id
    if webhook_id not in DATA_CHANNEL_WEBHOOK_IDS:
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
        webhooks = await DATA_CHANNEL.webhooks()
        for webhook in webhooks:  # In case the webhook ID was invalid
            if webhook.id == webhook_id:
                await webhook.delete()
                break


@plugins.listen_for('bot_on_ready_boot')
async def setup_globals(bot):
    """Sets up the DATA_CHANNEL global"""
    global DATA_CHANNEL
    DATA_CHANNEL = data.get_channel(bot, configurations.get(bot, __name__, key='data_channel'))
    if not DATA_CHANNEL:
        logger.warn("Failed to find the data channel. Defaulting to the upload channel.")
        DATA_CHANNEL = data.get_channel(bot, configurations.get(bot, 'core', key='upload_channel'))

    # Clear any webhooks (debug)
    webhooks = await DATA_CHANNEL.webhooks()
    for webhook in webhooks:
        logger.debug("Deleting webhook %s", webhook)
        await webhook.delete()
