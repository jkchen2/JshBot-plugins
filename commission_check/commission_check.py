import time
import discord

from datetime import timezone as tz

from jshbot import utilities, plugins, configurations, data, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import Command, SubCommand, Shortcut, ArgTypes, Arg, Opt, Response

__version__ = '0.1.1'
CBException = ConfiguredBotException('Commission channel checker')
uses_configuration = True


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'commission', subcommands=[
            SubCommand(
                Opt('configure'),
                Opt('channel', attached='channel name', optional=True,
                    convert=utilities.ChannelConverter(
                        constraint=discord.TextChannel, attribute='id')),
                Opt('cooldown', attached='seconds', optional=True,
                    convert=int, check=lambda b, m, v, *a: v > 0,
                    check_error='Must be greater than 0 seconds.',
                    quotes_recommended=False),
                doc='Configures the commission channel rules.',
                elevated_level=1, function=commission_configure),
            SubCommand(
                Opt('whitelist'),
                Arg('user', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.MemberConverter(attribute='id')),
                doc='Whitelists a user from the commission channel limits.',
                elevated_level=1, function=commission_whitelist),
            SubCommand(
                Opt('reset'),
                Arg('user', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.MemberConverter()),
                doc='Resets a user\'s post cooldown.',
                elevated_level=1, function=commission_reset),
            SubCommand(
                Opt('list'),
                doc='Lists users that have advertised in the commission channel.',
                elevated_level=1, function=commission_list)],
        description='Imposes the limits on the commission channel.',
        allow_direct=False)]


async def _notify_advertisement_available(
        bot, scheduled_time, payload, search, destination, late, *args):
    """Notifies the user that they can advertise again."""
    messageable = utilities.get_messageable(bot, destination)
    await messageable.send(embed=discord.Embed(
        color=discord.Color(0x77b255), description=(
            'You are now eligible to post another advertisement in the commission channel.\n'
            'Note that doing so will automatically delete your previous advertisement.')))


async def _get_advertisement_data(bot, guild, ignore_user_id=None):
    """Gets a dictionary of advertisements in the guild, or builds one if necessary.

    If ignore_user_id is provided, this will ignore the first message by that user.
    """
    rules = data.get(bot, __name__, 'rules', guild_id=guild.id)
    if not rules:
        raise CBException("Commission channel rules are not configured on this server.")
    advertisement_data = data.get(
        bot, __name__, 'advertisements', guild_id=guild.id, volatile=True)

    if advertisement_data:
        return advertisement_data

    # No data found. Fetch it manually
    channel = data.get_channel(bot, rules['channel'], safe=True)
    if not channel:
        raise CBException("The commission channel was not found.")

    # TODO: Add permission checks for channel access and deleting messages
    advertisement_data = {}
    whitelist = data.get(bot, __name__, 'whitelist', guild_id=guild.id, default=[])
    async for message in channel.history(limit=100):
        author_id = message.author.id
        if (not message.author.bot and
                message.type is discord.MessageType.default and
                not message.pinned and
                author_id not in whitelist):
            if author_id in advertisement_data:
                logger.warn('Deleting previously undetected message %s', message.id)
                await message.delete()
            else:
                if ignore_user_id == author_id:
                    ignore_user_id = None
                else:
                    advertisement_data[author_id] = message

    data.add(bot, __name__, 'advertisements', advertisement_data, guild_id=guild.id, volatile=True)
    return advertisement_data


async def commission_whitelist(bot, context):
    """Whitelists the given user from the commission channel limits."""
    if context.arguments[0]:  # Toggle user
        added = data.list_data_toggle(
            bot, __name__, 'whitelist', context.arguments[0], guild_id=context.guild.id)
        return Response('{}ed user {} the commission channel rules whitelist.'.format(
            *(('Add', 'to') if added else ('Remov', 'from'))))
    else:  # List users
        users = data.get(bot, __name__, 'whitelist', guild_id=context.guild.id)
        if not users:
            raise CBException("No users in the commission channel rules whitelist to list.")
        return Response(embed=discord.Embed(
            title='Whitelisted users:', description=', '.join('<@{}>'.format(it) for it in users)))


async def commission_reset(bot, context):
    """Resets a given user's post cooldown manually."""
    advertisement_data = await _get_advertisement_data(bot, context.guild)
    deleted_persistence = data.get(
        bot, __name__, 'recently_deleted', guild_id=context.guild.id, default={})
    user_id = context.arguments[0].id
    if user_id in advertisement_data:
        del advertisement_data[user_id]
    if str(user_id) in deleted_persistence:
        del deleted_persistence[str(user_id)]
    return Response(
        "Reset that user's advertisement cooldown. Their last advertisement post "
        "will need to be removed manually if necessary.")


async def commission_configure(bot, context):
    """Configures the channel and cooldown for the commission channel rules."""
    rules = data.get(bot, __name__, 'rules', guild_id=context.guild.id, default={})
    default_cooldown = configurations.get(bot, __name__, 'default_cooldown')
    replace_channel = context.options.get('channel', rules.get('channel'))
    replace_cooldown = context.options.get('cooldown', rules.get('cooldown', default_cooldown))
    if not replace_channel:
        raise CBException("No commission channel configured.")

    # Reset advertisement data
    rules = { 'channel': replace_channel, 'cooldown': replace_cooldown }
    data.add(bot, __name__, 'rules', rules, guild_id=context.guild.id)
    data.remove(
        bot, __name__, 'advertisements', guild_id=context.guild.id, volatile=True, safe=True)
    await _get_advertisement_data(bot, context.guild)

    description = 'Channel: {0.mention}\nCooldown: {1}'.format(
        data.get_channel(bot, replace_channel),
        utilities.get_time_string(replace_cooldown, text=True, full=True))
    embed = discord.Embed(title='Commission channel configuration:', description=description)
    return Response(embed=embed)


async def commission_list(bot, context):
    """Lists the users that have advertised in the commission channel."""
    advertisement_data = await _get_advertisement_data(bot, context.guild)
    return Response(embed=discord.Embed(
        title='List of advertisers:',
        description=', '.join(it.author.mention for it in advertisement_data.values())))


@plugins.listen_for('on_message')
async def check_commission_advertisement(bot, message):
    """Checks new messages in the commissions channel."""
    if isinstance(message.channel, discord.abc.PrivateChannel):
        return
    guild_data = data.get(bot, __name__, None, guild_id=message.guild.id, default={})
    if (not guild_data.get('rules') or
            message.channel.id != guild_data['rules']['channel'] or
            message.author.id in guild_data.get('whitelist', []) or
            message.author.bot):
        return

    cooldown = guild_data['rules']['cooldown']
    advertisement_data = await _get_advertisement_data(
        bot, message.guild, ignore_user_id=message.author.id)
    deleted_persistence = data.get(
        bot, __name__, 'recently_deleted', guild_id=message.guild.id, default={})
    time_delta = cooldown  # Assume cooldown has been passed
    author_id = message.author.id

    # Check the last advertisement's creation time (if it exists)
    if str(author_id) in deleted_persistence:
        time_delta = time.time() - deleted_persistence[str(author_id)]
    if author_id in advertisement_data:
        last_message = advertisement_data[author_id]
        time_delta = time.time() - last_message.created_at.replace(tzinfo=tz.utc).timestamp()

    # Not enough time has passed
    if time_delta < cooldown:
        # content_backup = message.content  # TODO: Consider sending the user a content backup?
        await message.delete()
        wait_for = utilities.get_time_string(cooldown - time_delta, text=True, full=True)
        warning = (
            'You cannot send another advertisement at this time. '
            'You must wait {}.').format(wait_for)
        await message.author.send(embed=discord.Embed(
            colour=discord.Colour(0xffcc4d), description=warning))
        return

    # Enough time has passed - delete the last message
    elif author_id in advertisement_data:
        try:
            await advertisement_data[author_id].delete()
        except:  # User deleted their advertisement already
            logger.warn("Failed to delete the last advertisement.")

    # Schedule a notification for when a new advertisement post is eligible
    utilities.schedule(
        bot, __name__, time.time() + cooldown, _notify_advertisement_available,
        search='c_ad_{}'.format(message.guild.id), destination='u{}'.format(author_id),
        info='Commission advertisement post eligibility.')

    advertisement_data[author_id] = message
    notification = (
        'Hello! Your advertisement post in the commissions channel has been recorded. '
        '**Please remember that there can only be one message per advertisement**.\n\n'
        'If you want to revise your advertisement [(like adding an image)]'
        '(https://imgur.com/a/qXB2v "Click here for a guide on how to add an image '
        'with a message"), you can delete your advertisement and submit it again, '
        'although this only works within the next 10 minutes and if nobody else has '
        'posted another advertisement after yours.\n\nYou are eligible to post a '
        'new advertisement after the waiting period of {}. When you post a new '
        'advertisement, your previous one will be automatically deleted.\n\n'
        'For convenience, you will be notified when you are eligible to make '
        'a new post.').format(
            utilities.get_time_string(cooldown, text=True, full=True))
    await message.author.send(embed=discord.Embed(
        colour=discord.Colour(0x77b255), description=notification))


@plugins.listen_for('on_message_delete')
async def check_recently_deleted(bot, message):
    """Checks if a user wants to revise their last advertisement."""
    if isinstance(message.channel, discord.abc.PrivateChannel):
        return
    guild_data = data.get(bot, __name__, None, guild_id=message.guild.id, default={})
    if (not guild_data.get('rules') or
            message.channel.id != guild_data['rules']['channel'] or
            message.author.id in guild_data.get('whitelist', []) or
            message.author.bot):
        return

    cooldown = guild_data['rules']['cooldown']
    message_time = message.created_at.replace(tzinfo=tz.utc).timestamp()
    advertisement_data = await _get_advertisement_data(bot, message.guild)
    author_id = message.author.id

    # Message mismatch. Ignore deletion
    if advertisement_data.get(author_id) != message:
        return

    # User wants to replace their last message (limit within 10 minutes)
    future_message = await message.channel.history(limit=1, after=message).flatten()
    time_delta = time.time() - message_time
    if not future_message and time_delta < 60:
        del advertisement_data[author_id]
        utilities.remove_schedule_entries(
            bot, __name__, search='c_ad_{}'.format(message.guild.id),
            destination='u{}'.format(author_id))
        notification = (
            'Heads up, you have deleted your last advertisement within 10 minutes of posting it '
            '(and nobody else posted an advertisement during that time).\n\n'
            'You can submit a revised advertisement now if you wish.')
        await message.author.send(embed=discord.Embed(description=notification))

    # User deleted their advertisement for some reason?
    # Keep message creation time to prevent users from circumventing the cooldown
    elif time_delta < cooldown:
        deleted_persistence = data.get(
            bot, __name__, 'recently_deleted', guild_id=message.guild.id, default={})

        # Clear any expired entries
        to_remove = [k for k, v in deleted_persistence.items() if time.time() - v > cooldown]
        for remove_id in to_remove:
            del deleted_persistence[str(remove_id)]

        # Add persistence entry
        deleted_persistence[str(author_id)] = message_time
        data.add(bot, __name__, 'recently_deleted', deleted_persistence, guild_id=message.guild.id)
