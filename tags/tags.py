import asyncio
import discord
import random
import pprint
import yaml
import time
import re

from collections import OrderedDict
from discord.abc import PrivateChannel
from psycopg2.extras import Json
from youtube_dl import YoutubeDL
from tinytag import TinyTag

from jshbot import data, utilities, configurations, logger, plugins, parser
from jshbot.exceptions import BotException, ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
uses_configuration = True
CBException = ConfiguredBotException('Tags')

# NOTE: Do not change the order of flags
flag_list = ['Sound', 'Private', 'NSFW', 'Complex', 'Random']
simple_flag_list = list(map(str.lower, flag_list))
use_global_tags, replace_commands = False, False  # Set by on_ready


# Converts the input (value) into a tag tuple
class TagConverter():

    def __init__(self, apply_checks=False, tag_owner=False, skip_sound=False):
        self.apply_checks = apply_checks
        self.tag_owner = tag_owner
        self.skip_sound = skip_sound
        self.pass_error = True

    def __call__(self, bot, message, value, *a):
        tag = _get_tag(bot, value, message.guild.id)

        if self.tag_owner:
            is_mod = data.is_mod(bot, message.guild, message.author.id)
            if tag.author != message.author.id and not is_mod:
                tag_author = data.get_member(
                    bot, tag.author, guild=message.guild, safe=True, strict=True)
                if tag_author:
                    raise CBException("You are not the tag owner, {}.".format(tag_author.mention))
                else:
                    raise CBException(
                        "You are not a bot moderator. (Tag author is no longer on the server)")

        if self.apply_checks:
            flags = _get_flags(tag.flags, simple=True)
            is_mod = data.is_mod(bot, message.guild, message.author.id)
            if not is_mod:
                server_filter = data.get(
                    bot, __name__, 'filter', guild_id=message.guild.id, default=[])
                channel_filter = data.get(
                    bot, __name__, 'filter', guild_id=message.guild.id,
                    channel_id=message.channel.id, default=[])
                if 'all' in server_filter:
                    raise CBException("Tags are disabled on this server.")
                elif 'all' in channel_filter:
                    raise CBException("Tags are disabled in this channel.")
                elif 'private' in flags and message.author.id != tag.author:
                    raise CBException("This tag is private.")
                for restriction in server_filter:
                    if restriction in flags:
                        flag_name = flag_list[simple_flag_list.index(restriction)]
                        raise CBException(
                            "{} tags are disabled on this server.".format(flag_name))
                for restriction in channel_filter:
                    if restriction in flags:
                        flag_name = flag_list[simple_flag_list.index(restriction)]
                        raise CBException(
                            "{} tags are disabled in this channel.".format(flag_name))

            if not self.skip_sound and 'sound' in flags:
                if message.author.voice is None:  # Check channel mute filters
                    raise CBException(
                        "This is a sound tag - you are not in a voice channel.", value)
                voice_channel = message.author.voice.channel
                voice_filter = data.get(
                    bot, __name__, 'filter', guild_id=message.guild.id,
                    channel_id=voice_channel.id, default=[])
                if not is_mod:
                    if 'all' in voice_filter:
                        raise CBException("Sound tags are disabled in this voice channel.")
                    for restriction in voice_filter:
                        if restriction in flags:
                            flag_name = flag_list[simple_flag_list.index(restriction)]
                            raise CBException(
                                "{} sound tags are disabled in this voice channel.".format(
                                    flag_name))

        return tag


@plugins.command_spawner
def get_commands(bot):
    new_commands = []

    valid_filters = '`{}`'.format('`, `'.join(it for it in simple_flag_list))
    use_global_tags = configurations.get(bot, __name__, 'global_tags')
    global_tag_elevation = 3 if use_global_tags else 1
    new_commands.append(Command(
        'tag', subcommands=[
            SubCommand(
                Opt('create'),
                Opt('random', optional=True, group='flags',
                    doc='Random tags can have up to 100 entries. Entries are '
                        'separated by spaces and quotes upon creation.'),
                Opt('private', optional=True, group='flags',
                    doc='Tags can only be called by the creator or a moderator.'),
                Opt('sound', optional=True, group='flags',
                    doc='Plays the given URL to the voice channel the caller is in.'),
                Opt('nsfw', optional=True, group='flags',
                    doc='Mark tag as NSFW. Cannot be called in channels that block '
                        'NSFW tags.'),
                Arg('tag name'),
                Arg('content', argtype=ArgTypes.MERGED),
                doc='Creates a tag with the given options.',
                allow_direct=use_global_tags, function=tag_create),
            SubCommand(
                Opt('remove'),
                Arg('tag name', argtype=ArgTypes.MERGED, convert=TagConverter(tag_owner=True)),
                doc='Removes the specified tag. You must be the tag author or a moderator',
                allow_direct=use_global_tags, function=tag_remove),
            SubCommand(
                Opt('raw'), Opt('file', optional=True, doc='Send contents as a file.'),
                Arg('tag name', argtype=ArgTypes.MERGED,
                    convert=TagConverter(apply_checks=True, skip_sound=True)),
                doc='Gets the raw tag data. Useful for figuring out what is inside '
                    'a random tag.',
                allow_direct=use_global_tags, function=tag_raw),
            SubCommand(
                Opt('info'),
                Arg('tag name', argtype=ArgTypes.MERGED_OPTIONAL, convert=TagConverter()),
                doc='Gets tag information for the server. If a tag is given, '
                    'this gets basic tag information instead, including the author, '
                    'creation date, number of uses, length, etc.',
                allow_direct=use_global_tags, function=tag_info),
            SubCommand(
                Opt('edit', attached='tag name', convert=TagConverter(tag_owner=True)),
                Opt('set', attached='content', optional=True),
                Opt('rename', attached='new name', optional=True, group='extra'),
                Opt('add', attached='entry', optional=True, group='extra'),
                Opt('remove', attached='entry', optional=True, group='extra'),
                Opt('volume', attached='percent', optional=True, group='extra',
                    convert=utilities.PercentageConverter(),
                    check=lambda b, m, v, *a: 0.1 <= v <= 2.0,
                    check_error='Must be between 10% and 200% inclusive.'),
                Opt('private', optional=True, group='extra'),
                Opt('nsfw', optional=True, group='extra'),
                doc='Modifies the given tag with the given options.',
                allow_direct=use_global_tags, function=tag_edit),
            SubCommand(
                Opt('list'),
                Opt('author', attached='user', optional=True,
                    convert=utilities.MemberConverter(live_check=lambda b, m ,v, *a:
                        not (use_global_tags or isisntance(m.channel, PrivateChannel)))),
                Arg('filter', additional='more filters',
                    argtype=ArgTypes.SPLIT_OPTIONAL, quotes_recommended=False,
                    doc='Valid filters are: {}.'.format(valid_filters)),
                doc='Lists tags for the server, constrained by the given options.',
                function=tag_list),
            SubCommand(
                Opt('search'), Arg('terms', argtype=ArgTypes.MERGED),
                doc='Searches for tag names and content with the given terms.',
                function=tag_search),
            SubCommand(
                Opt('toggle', doc='Valid flags are: `all`, {}.'.format(valid_filters)),
                Arg('flag', quotes_recommended=False),
                Arg('channel', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.ChannelConverter()),
                doc='Toggles the given flag for either the specified channel or the '
                    'entire server. ',
                allow_direct=False, elevated_level=1, function=tag_toggle),
            SubCommand(
                Opt('export'),
                Opt('private', optional=True, doc='Includes private tags in the export.'),
                Arg('tag name', argtype=ArgTypes.MERGED_OPTIONAL, convert=TagConverter()),
                doc='Exports the tag(s) as a CSV file.',
                allow_direct=use_global_tags, elevated_level=global_tag_elevation,
                function=tag_export),
            SubCommand(
                Opt('import'),
                Opt('replace', optional=True, doc='Overwrites tags if there is a name conflict.'),
                Attachment('tag database'),
                doc='Imports the attached tag database file.',
                allow_direct=use_global_tags, elevated_level=global_tag_elevation,
                function=tag_import),
            SubCommand(
                Arg('tag name', argtype=ArgTypes.MERGED, convert=TagConverter(apply_checks=True)),
                allow_direct=use_global_tags, doc='Retrieves the given tag.',
                confidence_threshold=3, function=tag_retrieve)],
        shortcuts=[
            Shortcut('t', '{arguments}', Arg('arguments', argtype=ArgTypes.MERGED)),
            Shortcut(
                'tc', 'create {tag name} {arguments}',
                Arg('tag name'), Arg('arguments', argtype=ArgTypes.MERGED)),
            Shortcut(
                'stc', 'create sound {tag name} {arguments}',
                Arg('tag name'), Arg('arguments', argtype=ArgTypes.MERGED)),
            Shortcut(
                'tl', 'list {arguments}', Arg('arguments', argtype=ArgTypes.MERGED_OPTIONAL)),
            Shortcut('ts', 'search {arguments}', Arg('arguments', argtype=ArgTypes.MERGED))],
        description='Create and recall macros of text and sound.', category='chat tools'))

    return new_commands


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'tags_template': ("key               text PRIMARY KEY,"  # Cleaned key
                          "value             text ARRAY,"
                          "length            integer ARRAY,"
                          "volume            double precision,"
                          "name              text,"  # Full name upon creation
                          "flags             integer,"
                          "author            bigint,"
                          "hits              integer,"
                          "created           bigint,"
                          "last_used         bigint,"
                          "last_used_by      bigint,"
                          "complex           json,"  # Unfinished
                          "extra             json,"
                          "CHECK (hits >= 0)")
    }

@plugins.on_load
def setup_global_tag_table(bot):
    data.db_create_table(bot, 'tags', table_suffix='global', template='tags_template')


async def tag_create(bot, context):
    settings = configurations.get(bot, __name__)
    length_limit = settings['max_tag_name_length']
    default_max_tags = settings['max_tags_per_server']
    server_settings = data.get(bot, __name__, 'settings', guild_id=context.guild.id, default={})
    tag_limit = server_settings.get('max_tags', default_max_tags)
    if tag_limit > default_max_tags:  # Safety tag limit
        tag_limit = default_max_tags

    # Check for issues
    tag_name = context.arguments[0]
    cleaned_tag_name = _cleaned_tag_name(tag_name)
    if cleaned_tag_name in context.keywords:
        raise CBException("That tag name is reserved as a keyword.")
    elif cleaned_tag_name.startswith(tuple(context.keywords)):
        raise CBException("That tag name starts with a reserved keyword.")
    elif len(tag_name) > length_limit:
        raise CBException(
            "The tag name cannot be longer than {} characters long".format(length_limit))
    elif len(cleaned_tag_name) == 0:
        raise CBException("No.")
    test = _get_tag(bot, cleaned_tag_name, context.guild.id, safe=True)
    if test:
        raise CBException("Tag `{}` already exists.".format(_format_tag(test)))
    cursor = data.db_select(
        bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id))
    if cursor is not None and cursor.fetchone().count >= tag_limit:
        raise CBException("The tag limit of {} has been reached.".format(tag_limit))

    if 'random' in context.options:
        content = parser.split_parameters(context.arguments[1])[::2]
    else:
        content = [context.arguments[1]]
    flag_list = list(context.options.keys())
    flag_list.remove('create')
    flag_bits = _get_flag_bits(flag_list)
    tag_data = [
        cleaned_tag_name,               # key
        content,                        # value
        [len(it) for it in content],    # length
        1.0,                            # volume
        tag_name,                       # name
        flag_bits,                      # flags
        context.author.id,              # author
        0,                              # hits
        int(time.time()),               # created
        None,                           # last_used
        None,                           # last_used_by
        {},                             # complex
        {}                              # extra
    ]
    if 'sound' in context.options:  # Check audio length for sound tags
        return Response(
            content='Checking the length of the audio...',
            message_type=MessageTypes.ACTIVE,
            extra=tag_data,
            extra_function=_check_sound_length)
    else:  # Add tag and finish
        _add_tag(bot, tag_data, context.guild.id, replace=False)
        return Response("Tag `{}` created. (Stored as `{}`)".format(tag_name, cleaned_tag_name))


async def _check_sound_length(bot, context, response):
    """Checks the length of the sound tag to ensure that it is within limits."""
    response.extra[2] = await _get_checked_durations(bot, response.extra[1])
    _add_tag(bot, response.extra, context.guild.id, replace=False)
    await response.message.edit(
        content="Tag `{0[4]}` created. (Stored as `{0[0]}`)".format(response.extra))


async def tag_remove(bot, context):
    _remove_tag(bot, context.arguments[0].key, context.guild.id)
    return Response(content='Tag removed.')


async def tag_raw(bot, context):
    tag = context.arguments[0]
    values = pprint.pformat(tag.value)
    if 'file' in context.options or len(values) > 1900:
        content_file = utilities.get_text_as_file(values)
        discord_file = discord.File(content_file, filename='raw.txt')
        return Response(content='\u200b', file=discord_file)
    else:
        if '```' in values:
            warning = (
                'Triple backticks found in tag. Inserting a zero-width space to keep formatting.')
            values = values.replace('```', '`\u200b``')
        else:
            warning = ''
        return Response(content='{}```\n{}```'.format(warning, values))


async def tag_info(bot, context):
    tag = context.arguments[0]

    if tag:
        embed = discord.Embed(
            title=':information_source: Tag: {}'.format(tag.name), colour=discord.Color(0x3b88c3))
        embed.add_field(name='Database name', value=tag.key)
        author = data.get_member(
            bot, tag.author, guild=context.guild, attribute='mention', safe=True, strict=True)
        if author is None:
            author = '[Not found]'
        embed.add_field(name='Author', value=author)
        flags = ', '.join(_get_flags(tag.flags))
        embed.add_field(name='Flags', value=flags if flags else '[None]')
        embed.add_field(name='Hits', value=str(tag.hits))
        if tag.last_used_by:
            last_used_by = data.get_member(
                bot, tag.last_used_by, guild=context.guild,
                attribute='mention', safe=True, strict=True)
            if last_used_by is None:
                last_used_by = '[Not found]'
            embed.add_field(name='Last used by', value=last_used_by)
        if 'Sound' in flags:
            length_type = 'second'
            volume = '{}%'.format(int(tag.volume * 100))
            embed.add_field(name='Volume', value=volume)
        else:
            length_type = 'character'
        length = '{} {}(s)'.format(', '.join(str(it) for it in tag.length), length_type)
        embed.add_field(name='Length', value=length)
        offset, created = utilities.get_timezone_offset(
            bot, guild_id=context.guild.id, utc_seconds=tag.created, as_string=True)
        created = time.asctime(time.gmtime(created))
        embed.add_field(name='Created', value='{} [{}]'.format(created, offset))
        if tag.last_used:
            _, last_used = utilities.get_timezone_offset(
                bot, guild_id=context.guild.id, utc_seconds=tag.last_used)
            last_used = time.asctime(time.gmtime(last_used))
            embed.add_field(name='Last used', value='{} [{}]'.format(last_used, offset))
    else:
        cursor = data.db_select(
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id))
        tag_count = cursor.fetchone().count if cursor else 0
        if tag_count == 0:
            raise CBException("This server has no tags.")
        embed = discord.Embed(
            title=':information_source: {} tag statistics'.format(context.guild),
            colour=discord.Color(0x3b88c3))
        embed.add_field(name='Total tags', value=str(tag_count))
        total_hits = data.db_select(  # Total tag uses
            bot, select_arg='SUM(hits)', from_arg='tags',
            table_suffix=str(context.guild.id)).fetchone().sum
        embed.add_field(name='Total usages', value=str(total_hits))
        tag_count = data.db_select(  # Sound tags
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id),
            where_arg='flags & 1 = 1').fetchone().count
        embed.add_field(name='Sound tags', value=str(tag_count))
        tag_count = data.db_select(  # Private tags
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id),
            where_arg='flags & 2 = 2').fetchone().count
        embed.add_field(name='Private tags', value=str(tag_count))
        tag_count = data.db_select(  # NSFW tags
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id),
            where_arg='flags & 4 = 4').fetchone().count
        embed.add_field(name='NSFW tags', value=str(tag_count))
        tag_count = data.db_select(  # Random tags
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id),
            where_arg='flags & 16 = 16').fetchone().count
        embed.add_field(name='Random tags', value=str(tag_count))
        cursor = data.db_select(
            bot, from_arg='tags', table_suffix=str(context.guild.id),
            additional='ORDER BY hits DESC', limit=3, safe=False)
        top_tags = cursor.fetchall()
        top_tags_formatted = []
        for tag in top_tags:
            top_tags_formatted.append('`{}` ({} hits)'.format(_format_tag(tag), tag.hits))
        embed.add_field(name='Top tags', value='\n'.join(top_tags_formatted))

    return Response(embed=embed)


async def tag_edit(bot, context):
    options = context.options
    if len(options) == 1:
        raise CBException("Nothing was changed!")
    tag = options['edit']
    flags = _get_flags(tag.flags, simple=True)
    new_tag = list(tag)
    additions = []

    if 'set' in options:
        new_content = options['set']
        if not new_content:
            raise CBException("Can't set empty text.")
        elif 'random' in flags:
            raise CBException("Cannot set text for a random tag.")
        elif 'sound' in flags:  # Check audio length
            length = await _get_checked_durations(bot, [new_content])[0]
            new_tag[2][0] = length
            additions.append("Set tag URL.")
        else:
            new_tag[2][0] = len(new_content)
            additions.append("Set tag text.")
        new_tag[1][0] = new_content

    if 'rename' in options:
        cleaned_tag_name = _cleaned_tag_name(options['rename'])
        tag_name = options['rename']
        test = _get_tag(bot, cleaned_tag_name, context.guild.id, safe=True)
        if test:
            raise CBException("Tag `{}` already exists.".format(_format_tag(test)))
        new_tag[0] = cleaned_tag_name
        new_tag[4] = tag_name
        additions.append("Renamed to `{}`. (Stored as `{}`)".format(tag_name, cleaned_tag_name))

    if 'nsfw' in options:
        if 'nsfw' in flags:
            flags.remove('nsfw')
            additions.append("Tag is no longer marked as NSFW")
        else:
            flags.append('nsfw')
            additions.append("Tag is now marked as NSFW")

    if 'private' in options:
        if 'private' in flags:
            flags.remove('private')
            additions.append("Tag is now public.")
        else:
            flags.append('private')
            additions.append("Tag is now private.")

    if 'volume' in options:
        if 'sound' not in flags:
            raise CBException("Cannot change the volume of a text only tag.")
        new_volume = options['volume']
        new_tag[3] = new_volume
        additions.append("Volume changed to {}%.".format(new_volume * 100))

    if 'add' in options or 'remove' in options:
        if 'set' in options:
            raise CBException("Cannot set tag value while also adding/removing entries.")
        if 'add' in options:
            to_add = options['add']
            total_entries = len(tag.value)
            if total_entries >= 100:
                raise CBException("Random tags can have no more than 100 entries.")
            if 'sound' in flags:  # Check audio length
                length = await _get_checked_durations(bot, [to_add])[0]
            else:
                length = len(to_add)
            new_tag[1].append(to_add)
            new_tag[2].append(length)
            if len(new_tag[1]) > 1 and 'random' not in flags:
                flags.append('random')
                additions.append("Added an entry. Tag is now random.")
            else:
                additions.append("Added an entry.")
        if 'remove' in options:
            to_remove = options['remove']
            if to_remove not in new_tag[1]:
                raise CBException("Entry '{}' not found in the tag.".format(to_remove))
            else:
                value_index = new_tag[1].index(to_remove)
                new_tag[1].pop(value_index)
                new_tag[2].pop(value_index)
            entries_length = len(new_tag[1])
            if entries_length == 0:
                _remove_tag(bot, new_tag[0], context.guild.id)
                additions = ["Tag removed (last entry removed)."]
            elif entries_length == 1 and 'random' in flags:
                flags.remove('random')
                additions.append("Removed an entry. Tag is no longer random.")
            else:
                additions.append("Removed an entry.")

    if 'rename' in options:
        _remove_tag(bot, tag[0], context.guild.id)
    if len(new_tag[1]):
        new_tag[5] = _get_flag_bits(flags)
        _add_tag(bot, new_tag, context.guild.id, replace=True)
    return Response(content='\n'.join(additions))


async def tag_list(bot, context):
    where_arg = ''
    input_args = []
    if 'author' in context.options:
        where_arg += 'author = %s'
        input_args.append(context.options['author'].id)
    if context.arguments[0]:
        flag_strip = []
        for flag in context.arguments:
            if flag.lower() not in simple_flag_list:
                raise CBException("`{}` is not a valid flag.".format(flag))
            flag_strip.append(flag.lower())
        flag_restriction = _get_flag_bits(context.arguments)
        if where_arg:
            where_arg += ' AND '
        where_arg += 'flags & %s = %s'
        input_args.extend((flag_restriction, flag_restriction))

    if context.direct:  # List tags from all guilds
        buttons = ['⏮', '⬅', '➡', '⏭']
        guilds = [it.guild for it in bot.get_all_members() if it == context.author]
        if not guilds:
            raise CBException("You are not on any servers shared by the bot.")
    else:
        buttons = ['⬅', '➡']
        cursor = data.db_select(
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id))
        tag_count = cursor.fetchone().count if cursor else 0
        if tag_count == 0:
            raise CBException("This server has no tags.")
        guilds = [context.guild]

    guild_tags, tag_blob = _get_guild_tags(bot, guilds, where_arg, input_args)
    if not guild_tags:
        raise CBException('No tags on any of your servers!')

    if context.arguments[0]:
        filter_text = 'Filtering by: {}'.format(', '.join(_get_flags(flag_restriction)))
    else:
        filter_text = ''
    return _build_tag_list_response(context, buttons, guild_tags, tag_blob, filter_text)


async def tag_search(bot, context):
    terms = context.arguments[0]
    if not terms:
        raise CBException("Try some text next time, knucklehead")
    if context.direct:  # Search tags from all guilds
        buttons = ['⏮', '⬅', '➡', '⏭']
        guilds = [it.guild for it in bot.get_all_members() if it == context.author]
        if not guilds:
            raise CBException("You are not on any servers shared by the bot.")
    else:
        buttons = ['⬅', '➡']
        cursor = data.db_select(
            bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id))
        tag_count = cursor.fetchone().count if cursor else 0
        if tag_count == 0:
            raise CBException("This server has no tags.")
        guilds = [context.guild]
    guild_tags, tag_blob = _get_guild_tags(bot, guilds, 'key LIKE %s', ['%'+terms+'%'])
    if not guild_tags:
        raise CBException("No tags found.")
    filter_text = "Tags with `{}` in it:".format(terms)
    return _build_tag_list_response(context, buttons, guild_tags, tag_blob, filter_text)


def _build_tag_list_response(context, buttons, guild_tags, tag_blob, filter_text):
    response = Response(
        message_type=MessageTypes.INTERACTIVE,
        extra_function=_tag_list_browser,
        extra={'buttons': buttons})
    response.guild_tags = guild_tags
    response.tag_blob = tag_blob
    response.page = 0
    response.search = False
    response.filter_text = filter_text
    if context.direct:
        response.current_guild = next(iter(guild_tags)).name
    else:
        response.current_guild = context.guild.name
    guild_tag_data = guild_tags[response.current_guild]
    tag_page_listing = guild_tag_data['listing']
    response.embed = discord.Embed(
        title='{} tags for {}'.format(guild_tag_data['total'], response.current_guild),
        description='{}```md\n{}```'.format(response.filter_text, tag_page_listing[0]))
    if len(guild_tags) == 1:
        guild_page_value = ''
    else:
        guild_page_value = 'Server [ 1 / {} ]\n'.format(1, len(guild_tags))
    page_value = '{}Page [ 1 / {} ]'.format(guild_page_value, len(tag_page_listing))
    response.embed.add_field(name='\u200b', value=page_value, inline=False)
    return response


async def tag_toggle(bot, context):
    flag = context.arguments[0].lower()
    if flag not in simple_flag_list and flag != 'all':
        raise CBException("`{}` is not a valid flag.".format(flag))
    flag_full_name = flag_list[simple_flag_list.index(flag)] if flag != 'all' else 'All'

    channel = context.arguments[1]
    if channel:
        pass_in = {'guild_id': context.guild.id, 'channel_id': channel.id}
        if isinstance(channel, discord.VoiceChannel):
            location = '{} voice channel'.format(channel.name)
        else:
            location = '{} channel'.format(channel.mention)
    else:
        pass_in = {'guild_id': context.guild.id}
        location = 'entire server'

    current_filter = data.get(bot, __name__, 'filter', default=[], create=True, **pass_in)
    if flag in current_filter:
        data.list_data_remove(bot, __name__, 'filter', value=flag, **pass_in)
        action = "lifted"
    else:
        data.list_data_append(bot, __name__, 'filter', flag, **pass_in)
        action = "added"
    if flag == 'all':
        status = "All flag restriction {}.\n".format(action)
    else:
        status = "`{}` tag restriction {}.\n".format(flag_full_name, action)
    response = Response(content=status)
    if 'all' in current_filter:
        response.content += "All tags are disabled for the {}.".format(location)
    elif current_filter:
        flag_full_names = [flag_list[simple_flag_list.index(it)] for it in current_filter]
        response.content += "Disallowed tag types for the {}: `{}`".format(
            location, '`, `'.join(flag_full_names))
    else:
        response.content += "All tags are now allowed for the {}.".format(location)
    return response


async def tag_export(bot, context):
    if context.arguments[0]:
        tags = [context.arguments[0]]
    else:
        try:
            cursor = data.db_select(bot, from_arg='tags', table_suffix=context.guild.id)
            tags = cursor.fetchall()
            assert len(tags)
        except:
            raise CBException("This server has no tags.")
    if 'private' in context.options:
        destination = context.author
    else:
        destination = None

    tag_data = {}
    for tag in tags:
        if tag.flags & 2 == 2 and not destination:  # Skip private tags
            continue
        author_name = data.get_member(bot, tag.author, guild=context.guild, safe=True)
        if not author_name:
            author_name = '[Not found]'
        if tag.last_used_by:
            last_used_by_name = data.get_member(
                bot, tag.last_used_by, guild=context.guild, safe=True)
            if not last_used_by_name:
                last_used_by_name = '[Not found]'
        else:
            last_used_by_name = 'None'
        flag_names = ', '.join(_get_flags(tag.flags))
        if not flag_names:
            flag_names = 'None'
        offset, created_readable = utilities.get_timezone_offset(
            bot, guild_id=context.guild.id, utc_seconds=tag.created, as_string=True)
        created_readable = '{} [{}]'.format(
            time.asctime(time.gmtime(created_readable)), offset)
        if tag.last_used:
            _, last_used_readable = utilities.get_timezone_offset(
                bot, guild_id=context.guild.id, utc_seconds=tag.last_used)
            last_used_readable = '{} [{}]'.format(
                time.asctime(time.gmtime(last_used_readable)), offset)
        else:
            last_used_readable = 'None'
        tag_data[tag.key] = {
            "database_name": tag.key,
            "full_name": tag.name,
            "author": tag.author,
            "author_name": str(author_name),
            "flags": tag.flags,
            "flag_names": flag_names,
            "content": tag.value,
            "length": tag.length,
            "volume": tag.volume,
            "hits": tag.hits,
            "created": tag.created,
            "created_readable": created_readable,
            "last_used": tag.last_used,
            "last_used_readable": last_used_readable,
            "last_used_by": tag.last_used_by,
            "last_used_by_name": str(last_used_by_name),
            "complex": tag.complex,
            "extra": tag.extra
        }

    if not tag_data:
        raise CBException("No non-private tags exported.")
    yaml_text = yaml.dump(tag_data, default_flow_style=False, indent=4)
    return Response(
        content='Exported {} tag{}.'.format(len(tag_data), '' if len(tag_data) == 1 else 's'),
        file=discord.File(utilities.get_text_as_file(yaml_text), filename='database.txt'),
        destination=destination)


async def tag_import(bot, context):
    file_url = context.message.attachments[0].url
    database_file = await utilities.download_url(bot, file_url, use_fp=True)
    try:
        tag_data = yaml.load(database_file)
    except Exception as e:
        raise CBException("Failed to parse the database file.", e=e)

    default_max_tags = configurations.get(bot, __name__, 'max_tags_per_server')
    server_settings = data.get(bot, __name__, 'settings', guild_id=context.guild.id, default={})
    tag_limit = server_settings.get('max_tags', default_max_tags)
    if tag_limit > default_max_tags:  # Safety tag limit
        tag_limit = default_max_tags
    if len(tag_data) > tag_limit:
        raise CBException("Too many tags to import (limit {}).".format(tag_limit))

    return Response(
        content="Importing tags...",
        message_type=MessageTypes.ACTIVE,
        extra=tag_data,
        extra_function=_import_tag_status,
        tag_limit=tag_limit)


async def _import_tag_status(bot, context, response):
    last_update_time = time.time()

    tag_limit = response.tag_limit
    length_limit = configurations.get(bot, __name__, 'max_tag_name_length')
    replace_tags = 'replace' in context.options
    overwrites = 0
    new_tags = []
    required = (
        ('full_name', str), ('flags', int), ('content', (list, str)),
        ('author', int), ('created', int), ('hits', int), ('last_used', (None, int)),
        ('last_used_by', (None, int)), ('volume', float))
    bot.extra = response.extra
    for index, tag_pair in enumerate(response.extra.items()):
        _, tag = tag_pair

        # Check for issues
        try:

            for test, type_check in required:
                if test not in tag:
                    raise CBException("Missing field `{}`".format(test))
                try:
                    if isinstance(type_check, tuple):
                        if type_check[0] is list:
                            assert isinstance(tag[test], type_check[0])
                            for entry in tag[test]:
                                assert isinstance(entry, type_check[1])
                        else:  # Possibly None
                            assert tag[test] is None or isinstance(tag[test], type_check[1])
                    else:
                        assert isinstance(tag[test], type_check)
                except:
                    raise CBException("Field `{}` has an invalid type.".format(test))
            tag_name = tag['full_name']
            cleaned_tag_name = _cleaned_tag_name(tag_name)
            if cleaned_tag_name in context.keywords:
                raise CBException("Name reserved as a keyword.".format(tag_name))
            elif cleaned_tag_name.startswith(tuple(context.keywords)):
                raise CBException("Name starts with a reserved keyword.".format(tag_name))
            elif len(tag_name) > length_limit:
                raise CBException("Name is too long.".format(tag_name))
            elif len(cleaned_tag_name) == 0:
                raise CBException("Name has no valid characters.".format(tag_name))
            elif len(tag['content']) == 0:
                raise CBException("")
            test = _get_tag(bot, cleaned_tag_name, context.guild.id, safe=True)
            if test and replace_tags:
                overwrites += 1
            elif test:  # Don't replace
                continue

            flags = _get_flags(tag['flags'], simple=True)
            if bool(len(tag['content']) > 1) != bool('random' in flags):
                raise CBException("`Random` flag missing or incorrect.")
            lengths = []
            if 'sound' in flags:
                lengths = await _get_checked_durations(bot, tag['content'])
            else:
                for entry in tag['content']:
                    if len(entry) > 1998:
                        raise CBException("Entry is too long.")
                    lengths.append(len(entry))
            if not 0.1 <= tag['volume'] <= 2.0:
                raise CBException("Invalid volume range.")
            elif not 0 <= tag['hits'] <= 999999:
                raise CBException("Invalid hits range.")

            new_tags.append([
                cleaned_tag_name,           # key
                tag['content'],             # value
                lengths,                    # length
                tag['volume'],              # volume
                tag_name,                   # name
                tag['flags'],               # flags
                tag['author'],              # author
                tag['hits'],                # hits
                tag['created'],             # created
                tag['last_used'],           # last_used
                tag['last_used_by'],        # last_used_by
                {},                         # complex
                {}                          # extra
            ])

            if time.time() - last_update_time > 5:
                await response.message.edit(content="Importing tags... [ {} / {} ]".format(
                    index + 1, len(response.extra)))
                last_update_time = time.time()
            logger.debug("Tag added to new_tags: %s", cleaned_tag_name)

        except Exception as e:
            try:
                raise CBException("Failed to import tag `{}`".format(tag_name), e=e)
            except NameError:
                raise CBException("Failed to import tags", e=e)

    cursor = data.db_select(
        bot, select_arg='COUNT(*)', from_arg='tags', table_suffix=str(context.guild.id))
    if cursor is not None and cursor.fetchone().count - overwrites + len(new_tags) > tag_limit:
        raise CBException(
            "Total tags (original and imported) exceed tag limit ({}).".format(tag_limit))

    for tag in new_tags:
        _add_tag(bot, tag, context.guild.id, replace=True)
    await response.message.edit(content="Imported {} tags. ({} new, {} replaced)".format(
        len(new_tags), len(new_tags) - overwrites, overwrites))



async def tag_retrieve(bot, context):
    tag = context.arguments[0]
    flags = _get_flags(tag.flags, simple=True)
    _update_hits(bot, tag.key, context.author.id, context.guild.id)
    if len(tag.value) > 1:
        content = random.choice(tag.value)
    else:
        content = tag.value[0]

    if 'sound' in flags:
        voice_channel = context.author.voice.channel
        voice_client = await utilities.join_and_ready(
            bot, voice_channel, is_mod=context.elevation >= 1)

        sound_file = data.get_from_cache(bot, None, url=content)
        if not sound_file:  # Can't reuse URLs unfortunately
            if content.startswith('https://my.mixtape.moe/'):
                download_url = content
            else:
                try:
                    ytdl_options = {'format': 'bestaudio/best', 'noplaylist': True}
                    downloader = YoutubeDL(ytdl_options)
                    info = await utilities.future(downloader.extract_info, content, download=False)
                    download_url = info['formats'][0]['url']
                except Exception as e:
                    logger.warn("youtube_dl failed to download file.")
                    logger.warn("Exception information: {}".format(e))
                    download_url = value
            sound_file = await data.add_to_cache(bot, download_url, name=content)

        # TODO: Check ffmpeg options?
        #ffmpeg_options = '-protocol_whitelist "file,http,https,tcp,tls"'
        #audio_source = discord.FFmpegPCMAudio(sound_file, before_options=ffmpeg_options)
        audio_source = discord.FFmpegPCMAudio(sound_file)
        audio_source = discord.PCMVolumeTransformer(audio_source, volume=tag.volume)
        voice_client.play(audio_source)
    else:
        return Response(content=content)


def _cleaned_tag_name(name):
    """Get the cleaned up version of the given name.

    The returned tag name only has standard ascii alphanumerical characters.
    """
    cleaned_list = []
    for char in name.lower():  # I /could/ do list comprehension, but nah.
        num = ord(char)
        if 48 <= num <= 57 or 97 <= num <= 122:
            cleaned_list.append(char)
    return ''.join(cleaned_list)


def _format_tag(tag, stripped=[], clean=True):
    """Formats the given tag in the proper markdown syntax."""
    special = [flag[0] for flag in _get_flags(tag.flags) if flag.lower() not in stripped]
    if clean:
        tag_name = re.sub('[[\]()<>#/",`\\\\]', '', tag.name)
    else:
        tag_name = tag.name
    if special:
        return '[{0}]({1})'.format(tag_name, '/'.join(special))
    else:
        return tag_name


def _get_flags(flag_bits, simple=False):
    """Gets a list of strings representing the flags of the tag.

    If simple is set to True, this will use the simple_flag_list instead.
    """
    found_flags = []
    specified_flag_list = simple_flag_list if simple else flag_list
    for it, flag in enumerate(specified_flag_list):
        if (flag_bits >> it) & 1:
            found_flags.append(flag)
    return found_flags


def _get_flag_bits(given_flags):
    """Gets the flag bits given the flags."""
    flag_value = 0
    for flag in given_flags:
        try:
            flag_index = simple_flag_list.index(flag.lower())
            flag_value += 1 << flag_index
        except ValueError:
            pass
    return flag_value


def _get_tag(bot, tag_name, guild_id, safe=False):
    """Obtains the tag from the database."""
    if not tag_name:
        raise CBException("Nice try, guy.")
    if configurations.get(bot, __name__, 'global_tags'):
        table_suffix = 'global'
    else:
        table_suffix = str(guild_id)
    key = _cleaned_tag_name(tag_name)
    cursor = data.db_select(
        bot, from_arg='tags', table_suffix=table_suffix, where_arg='key=%s', input_args=[key])
    if cursor is None:
        if safe:
            return None
        raise CBException("This server has no tags.")
    tag = cursor.fetchone()
    if not tag:  # Look for similar names
        if safe:
            return None
        cursor = data.db_select(
            bot, from_arg='tags', table_suffix=table_suffix, limit=3,
            where_arg='key LIKE %s', input_args=['%' + key + '%'])
        matches = cursor.fetchall()
        if matches:
            suggestion = "Did you mean: `{}`".format(
                '`, `'.join(_format_tag(tag) for tag in matches))
            raise CBException('Tag `{}` not found.\n{}'.format(key, suggestion))
        else:
            raise CBException('Tag `{}` not found.'.format(key))
    else:
        return tag


def _add_tag(bot, tag_data, guild_id, replace=False):
    if not isinstance(tag_data[11], Json):
        tag_data[11] = Json(tag_data[11])
    if not isinstance(tag_data[12], Json):
        tag_data[12] = Json(tag_data[12])
    if replace:  # Delete the original tag first
        try:
            _remove_tag(bot, tag_data[0], guild_id)
        except:
            pass
    data.db_insert(
        bot, 'tags', input_args=tag_data, table_suffix=guild_id,
        safe=False, create='tags_template')


def _remove_tag(bot, tag_name, guild_id):
    data.db_delete(
        bot, 'tags', table_suffix=guild_id, where_arg='key=%s', input_args=[tag_name], safe=False)


def _update_hits(bot, cleaned_tag_name, user_id, guild_id):
    """Increments the hit counter on the given tag."""
    data.db_update(
        bot, 'tags', table_suffix=guild_id, set_arg='hits=hits+1, last_used=%s, last_used_by=%s',
        where_arg='key=%s', input_args=[int(time.time()), user_id, cleaned_tag_name])


async def _get_checked_durations(bot, urls):
    length_limit = configurations.get(bot, __name__, 'max_sound_tag_length')
    downloader = YoutubeDL({'format': 'worstaudio/worst', 'noplaylist': True})
    lengths = []
    over_limit = []
    for url in urls:
        try:
            info = await utilities.future(downloader.extract_info, url, download=False)
            if 'duration' in info:
                duration = int(info['duration'])
            else:  # Manual download and check
                chosen_format = info['formats'][0]
                extension = chosen_format['ext']
                download_url = chosen_format['url']
                file_location, filename = await utilities.download_url(
                    bot, download_url, extension=extension, include_name=True)
                duration = int(TinyTag.get(file_location).duration)
                utilities.delete_temporary_file(bot, filename)
        except BotException as e:
            raise e  # Pass up
        except Exception as e:
            raise CBException("Failed to get duration from a URL.", url, e=e)
        lengths.append(duration)
        if duration > length_limit:
            over_limit.append(url)

    if over_limit:
        raise CBException(
            "The following URL(s) have audio over the "
            "length limit of {} seconds.".format(length_limit),
            '\n'.join(over_limit))
    else:  # Update lengths and add to db
        return lengths


def _get_guild_tags(bot, guilds, where_arg='', input_args='', flag_strip=[]):
    guild_tags = OrderedDict()
    for guild in guilds:
        cursor = data.db_select(
            bot, from_arg='tags', table_suffix=guild.id, where_arg=where_arg,
            input_args=input_args, additional='ORDER BY key ASC')
        if cursor is None:
            continue
        found_tags = cursor.fetchall()
        if len(found_tags) == 0:
            continue
        guild_tags[guild.name] = {'total': len(found_tags), 'listing': []}

        # Organise tags into respective characters
        tag_listing = OrderedDict()
        for tag in found_tags:
            if tag.key[0] not in tag_listing:
                tag_listing[tag.key[0]] = []
            tag_listing[tag.key[0]].append('{}'.format(_format_tag(tag, stripped=flag_strip)))

        # Separate into pages less than 950 characters long
        cumulative_listing = []
        cumulative_length = 0
        for letter, formatted_tags in tag_listing.items():
            category = '# {} #\n{}'.format(letter.upper(), ', '.join(formatted_tags))
            if len(category) + cumulative_length > 1500:
                guild_tags[guild.name]['listing'].append('\n\n'.join(cumulative_listing))
                cumulative_listing = []
                cumulative_length = 0
            cumulative_listing.append(category)
            cumulative_length += len(category)
        if cumulative_listing:
            guild_tags[guild.name]['listing'].append('\n\n'.join(cumulative_listing))

    tag_blob_list = []
    for guild_name, guild_tag_data in guild_tags.items():
        if guild_tag_data:
            guild_tag_listing = guild_tag_data['listing']
        else:
            guild_tag_listing = ['[No tags found]']

        tag_blob_list.append(
            '### Tags for {} ###\n\n{}'.format(guild_name, '\n\n'.join(guild_tag_listing)))
    tag_blob = '\n\n\n'.join(tag_blob_list)
    return guild_tags, tag_blob


async def _add_download_link(bot, response):
    tag_blob_file = utilities.get_text_as_file(response.tag_blob.replace('\n', '\r\n'))
    url = await utilities.upload_to_discord(bot, tag_blob_file, filename='tag_list.txt')
    try:
        response.embed.url = url
        await response.message.edit(embed=response.embed)
    except Exception as e:
        logger.warn("Failed to add download link to embed: %s", e)


async def _tag_list_browser(bot, context, response, result, timed_out):
    if timed_out:  # TODO: Add timed out notification
        return

    if not result:
        asyncio.ensure_future(_add_download_link(bot, response))
        return
    else:
        selection = ['⏮', '⬅', '➡', '⏭'].index(result[0].emoji)
    current_guild = response.current_guild
    guild_tag_data = response.guild_tags[current_guild]
    tag_page_listing = guild_tag_data['listing']
    guild_name_list = list(response.guild_tags)
    guild_name_index = guild_name_list.index(current_guild)
    if selection in (1, 2):  # Page selection
        response.page = response.page + (1 if selection == 2 else -1)
        if response.page >= len(tag_page_listing):
            response.page = 0
        elif response.page < 0:
            response.page = len(tag_page_listing) - 1
    elif selection in (0, 3):  # Guild selection
        guild_name_index = guild_name_index + (1 if selection == 3 else -1)
        if guild_name_index >= len(guild_name_list):
            guild_name_index = 0
        elif guild_name_index < 0:
            guild_name_index = len(guild_name_list) - 1
        response.current_guild = guild_name_list[guild_name_index]

    # Edit embed
    response.embed.title = '{} tags for {}'.format(guild_tag_data['total'], response.current_guild)
    response.embed.description='{}```md\n{}```'.format(
        response.filter_text, tag_page_listing[response.page])
    if len(response.guild_tags) == 1:
        guild_page_value = '\u200b'
    else:
        guild_page_value = 'Server [ {} / {} ]'.format(guild_name_index+1, len(guild_name_list))
    page_value = 'Page [ {} / {} ]'.format(response.page+1, len(tag_page_listing))
    response.embed.set_field_at(0, name=guild_page_value, value=page_value, inline=False)
    await response.message.edit(embed=response.embed)


async def bot_on_ready_boot(bot):
    """Sets up the configuration globals"""
    global use_global_tags, replace_commands
    use_global_tags = configurations.get(bot, __name__, 'global_tags')
    replace_commands = configurations.get(bot, __name__, 'replace_commands')

    permissions = {
        'attach_files': "Allows the tag list to be uploaded as a text file.",
        'connect': "Allows the bot to connect to voice channels.",
        'speak': "Allows the usage of sound tags."
    }
    utilities.add_bot_permissions(bot, __name__, **permissions)
