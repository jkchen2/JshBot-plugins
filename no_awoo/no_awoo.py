import time
import random
import unicodedata
import re

import discord

from jshbot import utilities, configurations, plugins, data, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.1.0'
CBException = ConfiguredBotException('Awoo police')
uses_configuration = True

statements = None
fine = None
BASIC_MATCH = re.compile('a+w+oo+')
PLEA_MATCH = re.compile('legali[zs]e *a+w+oo+')


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'awoo', subcommands=[
            SubCommand(doc='Get fined.', allow_direct=False, function=awoo),
            SubCommand(
                Opt('stats'),
                Arg('user', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.MemberConverter(server_only=False)),
                doc='See how much money you or the given user owes.',
                function=awoo_stats),
            SubCommand(
                Opt('leaderboards'),
                doc='See the list of worst offenders.',
                function=awoo_leaderboards)],
        shortcuts=[
            Shortcut(
                'astats', 'stats {arguments}',
                Arg('arguments', argtype=ArgTypes.MERGED_OPTIONAL)),
            Shortcut('aleaderboards', 'leaderboards')
        ],
        description='Consult the criminal database.'),

    Command(
        'modawoo', subcommands=[
            SubCommand(
                Opt('toggle'),
                Arg('channel', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.ChannelConverter(constraint=discord.TextChannel),
                    doc='Toggles detection in this channel.'),
                doc='Toggles awoo detection.',
                function=awoo_toggle),
            SubCommand(
                Opt('whitelist'),
                Arg('user', argtype=ArgTypes.MERGED, convert=utilities.MemberConverter()),
                doc='Whitelist certain users from detection.',
                function=awoo_whitelist)
        ],
        description='Awoo mod tools.', elevated_level=1),
    
    Command(
        'ownerawoo', subcommands=[
            SubCommand(
                Opt('reset'),
                Arg('user', argtype=ArgTypes.MERGED, convert=utilities.MemberConverter()),
                function=awoo_reset)],
        description='Awoo bot owner tools.', elevated_level=3, hidden=True)]


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'awoo_template': (
            "user_id            bigint UNIQUE,"
            "debt               decimal,"
            "violations         integer,"
            "sneaky             integer")
    }


@plugins.on_load
def setup_awoo_table(bot):
    data.db_create_table(bot, 'awoo', template='awoo_template')
    user_index = 'IX_awoo_order'
    if not data.db_exists(bot, user_index):
        data.db_execute(bot, 'CREATE INDEX {} ON awoo (debt DESC)'.format(user_index))


async def awoo(bot, context):
    if not _awoo_check(bot, context.message):  # User has been whitelisted. Force a violation
        await _violation_notification(bot, context.message, 1)
    return


async def awoo_stats(bot, context):
    """Pulls stats on the given user."""
    user = context.arguments[0] or context.author
    cursor = data.db_select(bot, from_arg='awoo', where_arg='user_id=%s', input_args=[user.id])
    entry = cursor.fetchone() if cursor else None
    if not entry:
        raise CBException(
            '{} has not made a single awoo violation. What a good cookie.'.format(user.mention))
    embed = discord.Embed(title=':scales: Awoo violation statistics', description=user.mention)
    embed.add_field(name='Debt', value='${}'.format(entry.debt))
    embed.add_field(name='Violations', value='{}'.format(entry.violations))
    return Response(embed=embed)


async def awoo_leaderboards(bot, context):
    """Displays the top 10 violators."""
    cursor = data.db_select(bot, from_arg='awoo', additional='ORDER BY debt DESC', limit=10)
    entries = cursor.fetchall() if cursor else []
    if not entries:
        raise CBException("Nobody has made any awoo violations yet!")

    stats = [[], []]  # debt/violations, user
    for index, entry in enumerate(entries):
        stats[0].append('`{0}.` ${1.debt} | {1.violations}'.format(index + 1, entry))
        user = data.get_member(bot, entry.user_id, safe=True, attribute='mention')
        user = user or 'Unknown ({})'.format(entry.user_id)
        stats[1].append('{}'.format(user))

    embed = discord.Embed(title=':scales: Awoo violation leaderboards')
    embed.add_field(name='Debt | Violations', value='\n'.join(stats[0]))
    embed.add_field(name='User', value='\n'.join(stats[1]))
    return Response(embed=embed)


async def awoo_toggle(bot, context):
    """Toggles awoo detection for either the guild or the given channel."""
    guild_awoo_data = data.get(
        bot, __name__, None, guild_id=context.guild.id, default={}, create=True)

    # Channel
    if context.arguments[0]:
        channel = context.arguments[0]
        if channel.id in guild_awoo_data.get('disabled_channels', []):
            action = 'is now '
            data.list_data_remove(
                bot, __name__, 'disabled_channels', value=channel.id, guild_id=context.guild.id)
        else:
            action = 'is no longer'
            data.list_data_append(
                bot, __name__, 'disabled_channels', channel.id, guild_id=context.guild.id)
        return Response(content='{} {} being monitored for awoos.'.format(channel.mention, action))

    # Guild
    else:
        guild_awoo_data['enabled'] = not guild_awoo_data.get('enabled', False)
        return Response(content='Detection is now {}abled'.format(
            'en' if guild_awoo_data['enabled'] else 'dis'))


async def awoo_whitelist(bot, context):
    """(De)whitelists the given user."""
    user = context.arguments[0]
    whitelist = data.get(bot, __name__, 'whitelist', guild_id=context.guild.id, default=[])
    if user.id in whitelist:
        action = 'removed from'
        data.list_data_remove(bot, __name__, 'whitelist', value=user.id, guild_id=context.guild.id)
    else:
        action = 'added to'
        data.list_data_append(bot, __name__, 'whitelist', user.id, guild_id=context.guild.id)
    return Response(content="User {} the whitelist.".format(action))



async def awoo_reset(bot, context):
    """Removes the given user from the database."""
    user = context.arguments[0]
    removed = data.db_delete(bot, 'awoo', where_arg='user_id=%s', input_args=[user.id])
    if not removed:
        raise CBException("User not in violation database.")
    return Response(content="User removed from the database.")


def _awoo_check(bot, message, show_filtered=False):
    """
    Checks for awoo violations.

    Tier 1: Standard match
    Tier 2: Bypass attempt match
    Tier 3: Legalization plea
    """

    # Initial content check
    content = message.content.lower() if message.content else ''
    author, channel = message.author, message.channel
    if not content or author.bot or isinstance(channel, discord.abc.PrivateChannel):
        return

    # Ignore muted guilds, channels, and users
    guild_data = data.get(bot, 'core', None, message.guild.id, default={})
    if (guild_data.get('muted', False) or
            channel.id in guild_data.get('muted_channels', []) or
            author.id in guild_data.get('blocked', [])):
        return

    # Ignore disabled guilds, disabled channels and whitelisted users
    guild_awoo_data = data.get(bot, __name__, None, guild_id=message.guild.id, default={})
    if (not guild_awoo_data.get('enabled', False) or
            channel.id in guild_awoo_data.get('disabled_channels', []) or
            author.id in guild_awoo_data.get('whitelist', [])):
        return

    # Tier 3: Legalization plea
    if PLEA_MATCH.search(content):
        return 3

    # Tier 1: Basic check
    if BASIC_MATCH.search(content):
        return 1

    # Tier 2: Advanced check
    # I'm sorry.
    substitutions = [
        ['a', [
            '/-\\\\', '4', 'Ⱥ', '🇦', '🅰', 'ል', 'ɐ', 'ᴀ',
            'ค', 'ﾑ', 'а', 'Д', 'ค', 'α']],
        ['w', [
            'vv', '\\\\/\\\\/', '🇼', '🆆', 'ሠ', 'ʍ', 'ᴡ',
            'ฝ', 'ш', 'Щ', 'ฬ', 'ω']],
        ['o', [
            '<>', '{}', '[]', '()', '0', 'ø', '🇴', '🅾',
            'ዐ', 'ѻ', 'о', 'Ф', 'ф', '๏', 'σ']],
        ['', ['`', '[', ']', '(', ')']]
    ]
    unicode_filters = ['Cf', 'Cn', 'Zs', 'Zl', 'Zp', 'Mn']
    filtered = ''.join(c for c in content if not unicodedata.category(c).startswith('Z'))
    for key, values in substitutions:
        for value in values:
            filtered = filtered.replace(value, key)
    filtered = unicodedata.normalize('NFKD', filtered)
    filtered = ''.join(c for c in filtered if unicodedata.category(c) not in unicode_filters)
    for value in substitutions[3][1]:
        filtered = filtered.replace(value, '')
    if BASIC_MATCH.search(filtered.lower()):
        return 2

    # Debug
    if show_filtered:
        return filtered.lower()


async def _violation_notification(bot, message, awoo_tier, send_message=True):
    """
    Logs the violation and (optionally) sends the user a notification.
    
    Standard notification: once per violation, up to 2 times
    None: 3 violations
    Silence notification: 1 violation

    Reset period for notifications is 1 minute.

    Stress indicates a number of users making a violation within a 60 second period.
    Tier 1: 3 members
    Tier 2: 5 members
    Tier 3: 10 members
    """
    
    author, channel = message.author, message.channel
    current_time = time.time()
    violation_data = data.get(
        bot, __name__, 'user_violation', user_id=author.id, volatile=True)
    channel_violation_data = data.get(
        bot, __name__, 'channel_violation', channel_id=channel.id, volatile=True)
    if not violation_data or current_time - violation_data['time'] >= 60:
        violation_data = {'time': 0, 'violations': 0}
        data.add(bot, __name__, 'user_violation', violation_data, user_id=author.id, volatile=True)
    if not channel_violation_data or current_time - channel_violation_data['time'] >= 60:
        channel_violation_data = {'time': 0, 'violators': set(), 'sent_tier': 0}
        data.add(
            bot, __name__, 'channel_violation', channel_violation_data,
            channel_id=channel.id, volatile=True)
    violation_data['violations'] += 1
    violation_data['time'] = current_time
    channel_violation_data['violators'].add(author.id)
    channel_violation_data['time'] = current_time

    # Update table
    set_arg = 'debt = debt+%s, violations = violations+1'
    if awoo_tier == 2:
        set_arg += ', sneaky = sneaky+1'
    cursor = data.db_select(bot, from_arg='awoo', where_arg='user_id=%s', input_args=[author.id])
    entry = cursor.fetchone() if cursor else None
    if entry:
        data.db_update(
            bot, 'awoo', set_arg=set_arg, where_arg='user_id=%s', input_args=[fine, author.id])
    else:
        data.db_insert(bot, 'awoo', input_args=[author.id, fine, 1, 1 if awoo_tier == 2 else 0])

    # Add a snarky message depending on the tier
    if awoo_tier == 2:  # Attempted bypass
        snark = random.choice(statements['bypass']) + '\n'
    elif awoo_tier == 3:  # Legalization plea
        snark = random.choice(statements['legalize']) + '\n'
    else:
        snark = ''

    # Notify user
    logger.debug("Violations: %s", violation_data['violations'])
    text = ''
    if violation_data['violations'] <= 2:
        text = "{}{} has been fined ${} for an awoo violation.".format(snark, author.mention, fine)
    elif violation_data['violations'] == 6:
        text = "{} {}".format(author.mention, random.choice(statements['silence']))
    elif awoo_tier == 3 and violation_data['violations'] <= 3:  # Legalization plea, but silent
        text = snark
    if send_message and text:
        await channel.send(content=text)

    # Stress
    violators, sent_tier = channel_violation_data['violators'], channel_violation_data['sent_tier']
    if (len(violators) == 3 and sent_tier == 0 or
            len(violators) == 5 and sent_tier == 1 or
            len(violators) == 10 and sent_tier == 2):
        if send_message:
            await message.channel.send(random.choice(statements['stress'][sent_tier]))
        channel_violation_data['sent_tier'] += 1


async def on_message(bot, message):
    awoo_tier = _awoo_check(bot, message)
    if awoo_tier:  # Awoo detected
        await _violation_notification(bot, message, awoo_tier)


async def on_message_edit(bot, message_before, message_after):
    awoo_tier = _awoo_check(bot, message_after)
    if awoo_tier:
        await _violation_notification(bot, message_after, awoo_tier, send_message=False)


async def bot_on_ready_boot(bot):
    global statements, fine
    statements = configurations.get(bot, __name__, extra='statements', extension='json')
    fine = configurations.get(bot, __name__, 'fine')
