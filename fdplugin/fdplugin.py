import datetime
import time
import praw
import discord

from prawcore import NotFound

from jshbot import utilities, plugins, configurations, data, logger, parser
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import Command, SubCommand, Shortcut, ArgTypes, Arg, Opt, Response

__version__ = '0.1.4'
CBException = ConfiguredBotException('/r/Furry Discord plugin')
CBException_vc = ConfiguredBotException('Verification checker')
uses_configuration = True

REDDIT_CLIENT = None


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'verification', subcommands=[
            SubCommand(
                Opt('check'),
                Arg('user', argtype=ArgTypes.MERGED_OPTIONAL,
                    quotes_recommended=False, convert=utilities.MemberConverter()),
                doc='Checks if the given user qualifies for verification.',
                function=verification_check),
            SubCommand(
                Opt('karma'),
                Arg('user', argtype=ArgTypes.MERGED, quotes_recommended=False),
                doc='Checks for karma on the r/furry subreddit.',
                elevated_level=1, function=verification_karma),
            SubCommand(
                Opt('set'),
                Arg('role', argtype=ArgTypes.MERGED, quotes_recommended=False,
                    convert=utilities.RoleConverter()),
                doc='Sets the verification role.',
                elevated_level=1, function=verification_set)],
        shortcuts=[
            Shortcut('vc', 'check {user}', Arg('user', argtype=ArgTypes.MERGED_OPTIONAL)),
            Shortcut('vk', 'karma {user}', Arg('user', argtype=ArgTypes.MERGED))],
        description='A set of tools for the r/furry Discord server.',
        allow_direct=False, category='tools')]


async def _check_reddit_karma(bot, username):
    """Returns the amount of karma on the r/furry the given user has."""

    verification_period = configurations.get(bot, __name__, 'verification_period')
    time_threshold = time.time() - (verification_period * 86400)

    async def _get_next(generator):
        def _f():
            try:
                while True:
                    check = next(generator)
                    if check.created_utc <= time_threshold:
                        return check
                return next(generator)
            except StopIteration:
                return None
            except NotFound:
                raise CBException_vc("User u/{} not found.".format(username))
            except Exception as e:
                raise CBException_vc("Failed to retrieve data.", e=e)
        return await utilities.future(_f)

    # Submission karma using built-in search
    submission_limit = configurations.get(bot, __name__, 'submission_karma_limit')
    submissions = REDDIT_CLIENT.subreddit('furry').search('author:{}'.format(username))
    submission_karma = 0
    while True:
        submission = await _get_next(submissions)
        submission_karma += submission.score if submission else 0
        if not submission or submission_karma > submission_limit:
            break

    # Comment karma
    comment_limit = configurations.get(bot, __name__, 'comment_karma_limit')
    comments = REDDIT_CLIENT.redditor(username).comments.new(limit=None)
    comment_karma = 0
    while True:
        comment = await _get_next(comments)
        if comment and comment.subreddit.display_name == 'furry':
            comment_karma += comment.score if comment else 0
        if not comment or comment_karma > comment_limit:
            break

    return (submission_karma, comment_karma)


def _member_age(member):
    """Returns a string with the number of days a member has been on a server."""
    age = (datetime.datetime.now() - member.joined_at).days
    plural = '' if age == 1 else 's'
    return "{} day{}".format(age, plural)


def _get_verified_role(bot, guild, member=None):
    """Checks for the verified role and returns it unless the member has the role."""
    role_id = data.get(bot, __name__, 'verification_role', guild_id=guild.id)
    verified_role = data.get_role(bot, role_id, guild=guild, safe=True)
    if not (role_id or verified_role):
        raise CBException_vc("The verified role has not been set.")
    if member and verified_role in member.roles:
        raise CBException_vc(
            "{} already has the role {} (member for {}).".format(
                member.mention, verified_role.mention, _member_age(member)))
    return verified_role


async def verification_check(bot, context):
    """Checks if the given user qualifies for verification by date alone."""
    member = context.arguments[0] or context.author
    verified_role = _get_verified_role(bot, context.guild, member=member)
    verification_period = configurations.get(bot, __name__, 'verification_period')

    # Check that the user has been here for at least the verification period
    age = (datetime.datetime.now() - member.joined_at).days
    if age >= verification_period:
        indicator = ':white_check_mark:'
        qualifies = 'meets the minimum time qualification'
        color = discord.Color(0x77b255)
    else:
        indicator = ':x:'
        qualifies = 'needs to be a member for at least {} days'.format(verification_period)
        color = discord.Color(0xdd2e44)

    after_datetime = member.joined_at - datetime.timedelta(days=1)
    hint = (
        "\n\n:warning: Activity qualifications must also be met :warning:\n"
        "To check for message activity qualifications, use the following search "
        "parameters to see messages since the member has last joined the server:\n\n"
        "```\nfrom: {} after: {}\n```"
    ).format(member, after_datetime.strftime('%Y-%m-%d'))

    description = '{} Member for {}\n{} {} for {}{}'.format(
        indicator, _member_age(member), member.mention,
        qualifies, verified_role.mention, hint)
    return Response(embed=discord.Embed(description=description, color=color))


async def verification_set(bot, context):
    """Sets the verification role."""
    role = context.arguments[0]
    data.add(bot, __name__, 'verification_role', role.id, guild_id=context.guild.id)
    return Response(embed=discord.Embed(
        description='Verification role set to {}'.format(role.mention)))


async def verification_karma(bot, context):
    """Checks if the given user has karma in the r/furry subreddit."""
    verified_role = _get_verified_role(bot, context.guild)
    karma = await _check_reddit_karma(bot, context.arguments[0])
    threshold = configurations.get(bot, __name__, 'karma_threshold')

    # Replace karma amount with limits if it is past that
    limits = [
        configurations.get(bot, __name__, 'submission_karma_limit'),
        configurations.get(bot, __name__, 'comment_karma_limit')
    ]
    zipped = zip(karma, limits)
    karma_strings = [(str(it) if it <= ts else (str(ts) + '+')) for it, ts in zipped]

    if sum(karma) >= threshold:
        response = ':white_check_mark: {} submission karma, {} comment karma'
        qualifies = 'qualifies'
        color = discord.Color(0x77b255)
    else:
        response = ':x: {} submission karma, {} comment karma'
        qualifies = 'needs at least {} combined karma'.format(threshold)
        color = discord.Color(0xdd2e44)

    description = '{0}\n[u/{1}](https://www.reddit.com/user/{1}) {2} for {3}'.format(
        response.format(*karma_strings), context.arguments[0], qualifies, verified_role.mention)
    return Response(embed=discord.Embed(description=description, color=color))


@plugins.listen_for('bot_on_ready_boot')
async def create_reddit_client(bot):
    global REDDIT_CLIENT
    credential_data = configurations.get(bot, __name__)
    REDDIT_CLIENT = praw.Reddit(
        client_id=credential_data['reddit_client_id'],
        client_secret=credential_data['reddit_client_secret'],
        user_agent=credential_data['reddit_user_agent'])
    configurations.redact(bot, __name__, 'reddit_client_id')
    configurations.redact(bot, __name__, 'reddit_client_secret')
    configurations.redact(bot, __name__, 'reddit_user_agent')


@plugins.listen_for('on_message')
async def check_warns(bot, message):
    """Checks for issued warnings."""
    if (not message.guild or
            message.guild.id != configurations.get(bot, __name__, 'guild_id') or
            not message.content.lower().startswith('!warn ') or
            not data.is_mod(bot, member=message.author) or
            'autolog.py' not in bot.plugins):
        return

    split, quotes = parser.split_parameters(message.content, include_quotes=True, quote_list=True)
    if len(split) < 3:  # Not enough arguments
        return
    name = split[2][1:-1] if 2 in quotes else split[2]
    member = data.get_member(bot, name, guild=message.guild, safe=True, strict=True)
    if not member or data.is_mod(bot, member=member):  # Member not found or is another mod
        return

    details = '{0} (<@{0.id}>) was warned by {1} (<@{1.id}>): {2}'.format(
        member, message.author, ''.join(split[4:]) or "No warn reason given")
    await bot.plugins['autolog.py'].automated_dump_message(
        bot, message.guild, details, query=member.id, moderator_id=message.author.id)


@plugins.listen_for('on_message')
async def death_response(bot, message):
    """Adds an emoji reaction to a Minecraft death."""
    configuration = configurations.get(bot, __name__)
    if (message.channel.id == configuration['minecraft_channel_id'] and
            message.author.id == configuration['admin_bot_id'] and
            message.content.startswith('**') and
            message.content.endswith('**')):
        await message.add_reaction(bot.get_emoji(configuration['death_reaction_id']))
