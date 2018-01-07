import asyncio
import discord
import json
import requests
import random
import datetime
import time

from collections import OrderedDict
from bs4 import BeautifulSoup

from jshbot import utilities, data, configurations, plugins, logger
from jshbot.exceptions import BotException, ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
uses_configuration = True
CBException = ConfiguredBotException('GDQ plugin')


@plugins.command_spawner
def get_commands(bot):
    new_commands = []

    new_commands.append(Command(
        'gdq', subcommands=[
            SubCommand(doc='Shows the GDQ menu.'),
            SubCommand(Opt('about'), doc='Shows some basic information about GDQ.'),
            SubCommand(
                Opt('status'),
                doc='Shows the stream status and total amount amount of money raised.'),
            SubCommand(Opt('current'), doc='Shows the current game being played.'),
            SubCommand(
                Opt('next'),
                Arg('number', convert=int, check=lambda b, m, v, *a: 1 <= v <= 5,
                    check_error='Must be between 1 and 5 inclusive.', default=1,
                    argtype=ArgTypes.OPTIONAL, quotes_recommended=False),
                doc='Shows the next game(s). If a number is given (between 1 and 5 '
                    'inclusive), it will show the next number of games.'),
            SubCommand(
                Opt('search'), Arg('title', argtype=ArgTypes.MERGED),
                doc='Searches for the given game.'),
            SubCommand(
                Opt('notify'),
                Opt('channel', optional=True,
                    check=lambda b, m, v, *a: data.is_mod(b, m.guild, m.author.id),
                    check_error='Only bot moderators can notify the channel.'),
                Arg('title', argtype=ArgTypes.MERGED),
                doc='Sends a message to either the user or the channel for the given '
                    'game when it is about to be streamed (approximately 5-10 minutes '
                    'beforehand).\nOnly bot moderators can use the channel option.')],
        description='Games Done Quick for Discord.', category='service'))

    return new_commands


async def _notify(bot, scheduled_time, payload, search, destination, late):
    messageable = utilities.get_messageable(bot, destination)
    if 'error' in payload:
        text = payload['error']
    elif time.time() > payload['end']:
        text = (
            "Sorry! I missed the notification for {}. You can "
            "watch the VOD in a few days.").format(payload['text'])
    else:
        stream_url = configurations.get(bot, __name__, 'stream_url')
        notify_message = [
            "Heads up,", "Get ready,", "Good news!", "It's time!",
            "Just so you know,", "Just letting you know,", "Attention,", "Ping!", "Hey,"]
        text = "{} {} is about to be played soon. Watch the speedrun live at {}".format(
            random.choice(notify_message), payload['text'], stream_url)
    await messageable.send(content=text)


def _toggle_notification(bot, game, context, use_channel=False):
    """Adds the user or channel to the notifications list."""
    if use_channel:
        destination = 'c{}'.format(context.channel.id)
        destination_text = "This channel"
    else:
        destination = 'u{}'.format(context.author.id)
        destination_text = "You"
    key = game['key']
    game_text = '{} ({})'.format(game['game'], game['type'])
    pending_notification = utilities.get_schedule_entries(
        bot, __name__, search=key, destination=destination)
    if pending_notification:  # Remove from schedule
        utilities.remove_schedule_entries(bot, __name__, search=key, destination=destination)
        return "{} will no longer be notified when {} is about to be streamed.".format(
            destination_text, game_text)
    else:  # Add to schedule
        stream_url = configurations.get(bot, __name__, 'stream_url')
        current_time = datetime.datetime.utcnow()
        start_time, end_time = game['scheduled'], game['end']
        setup_delta = datetime.timedelta(seconds=game['setup_seconds'])

        if current_time < start_time:
            scheduled_seconds = start_time.replace(tzinfo=datetime.timezone.utc).timestamp()
            delta = utilities.get_time_string(scheduled_seconds - time.time(), text=True)
            info = 'GDQ game notification: {}'.format(game_text)
            payload = {
                'text': game_text,
                'end': scheduled_seconds + game['seconds']}
            utilities.schedule(
                bot, __name__, scheduled_seconds, _notify, payload=payload,
                search=key, destination=destination, info=info)
            return (
                "{} will be notified when {} is about to be streamed!\n"
                "(In approximately {})".format(destination_text, game_text, delta))

        elif current_time < start_time + setup_delta:
            return "The game is scheduled to start soon!\nWatch it at {}".format(stream_url)
        elif current_time < end_time:
            return "The game has already started!\nWatch it at {}".format(stream_url)
        else:
            return "Sorry, this game has already been finished."


def _embed_games_information(bot, games, guild_id):
    """Formats the given games into an embedded format."""
    result = []
    current_time = datetime.datetime.utcnow()
    for game in games:
        start_time, end_time = game['scheduled'], game['end']
        setup_delta = datetime.timedelta(seconds=game['setup_seconds'])
        setup_time = start_time + setup_delta

        extra = ''
        if current_time < start_time:  # Upcoming
            title = "Upcoming: "
            seconds = (start_time - current_time).total_seconds()
            if seconds > 60:
                begins_in = utilities.get_time_string(seconds, text=True, full=False)
            else:
                begins_in = 'a few moments!'
            offset, adjusted_time = utilities.get_timezone_offset(
                bot, guild_id, utc_dt=start_time, as_string=True)
            scheduled = '{} [{}]'.format(adjusted_time.strftime('%a %I:%M %p'), offset)
            extra = '\n\tStarts {} ({})'.format(scheduled, begins_in)

        elif current_time <= setup_time:  # In setup
            title = "Setting up: "

        elif current_time < end_time:  # Current
            title = "Current: "
            current_seconds = (current_time - start_time - setup_delta).total_seconds()
            extra = '\n\tCurrently at {}'.format(utilities.get_time_string(current_seconds))

        else:  # Finished
            title = "Finished: "

        title += '{}{}'.format(game['game'], ' ({})'.format(game['type']) if game['type'] else '')
        value = '\u200b\tRun by {} in {}'.format(game['runners'], game['estimation']) + extra
        result.append((title, value))

    return result


def _update_current_game(bot, safe=False, include_setup_status=False):
    """Updates the index of the latest/current game."""
    schedule_data = data.get(bot, __name__, 'schedule', volatile=True, default=[])
    current_time = datetime.datetime.utcnow()
    for index, game in enumerate(schedule_data):
        start_time, end_time = game['scheduled'], game['end']
        if start_time <= current_time < end_time:  # Update latest index
            data.add(bot, __name__, 'current_index', index, volatile=True)
            data.add(bot, __name__, 'current_game', game, volatile=True)
            if include_setup_status:
                setup_time = datetime.timedelta(seconds=game['setup_seconds'])
                return index, (current_time < start_time + setup_time)
            else:
                return index
        elif current_time < start_time:
            logger.debug("The current time is less than the start time. Index: %s", index)
            break
    else:  # GDQ over, or past schedule
        game, index = None, 999
    if safe:
        data.add(bot, __name__, 'current_index', index, volatile=True)
        data.add(bot, __name__, 'current_game', game, volatile=True)
        if include_setup_status:
            return index, True
        else:
            return index
    raise CBException("No current game was found.")


def _get_current_game(bot, guild_id):
    latest_index = _update_current_game(bot)
    schedule_data = data.get(bot, __name__, 'schedule', volatile=True)
    return _embed_games_information(bot, [schedule_data[latest_index]], guild_id)


def _get_next_games(bot, retrieve, guild_id):
    """Gets the current/next game(s) and the defined number of extra games."""
    latest_index, in_setup = _update_current_game(bot, safe=True, include_setup_status=True)
    schedule_data = data.get(bot, __name__, 'schedule', volatile=True, default=[])
    if not in_setup:
        latest_index += 1
    games_list = schedule_data[latest_index:latest_index + retrieve]
    embed_data = _embed_games_information(bot, games_list, guild_id)
    if embed_data:
        return embed_data
    else:
        raise CBException("Game information not found.")


def _search_games(bot, search, guild_id=None, return_game=False):
    """Searches the schedule for the given game and gets the information."""
    cleaned_search = utilities.get_cleaned_filename(search, cleaner=True)
    schedule_data = data.get(bot, __name__, 'schedule', volatile=True)
    found_games = []
    for index, game in enumerate(schedule_data):
        if cleaned_search == game['key']:
            found_games = [game]
            break
        elif cleaned_search in game['key']:
            found_games.append(game)
    if not found_games:
        raise CBException("No games found with that name.")
    elif len(found_games) > 10:
        raise CBException("Too many games found with that name.")
    elif len(found_games) != 1:
        raise CBException(
            "Multiple games found:", '\n'.join(
                ['{} ({})'.format(game['game'], game['type'])  for game in found_games]))
    elif return_game:
        return found_games[0]
    else:
        return _embed_games_information(bot, found_games, guild_id)[0]


async def _get_donation_data(bot):
    """Gets the current donation information, like total raised."""
    tracker_url = configurations.get(bot, __name__, 'tracker_url')
    try:
        donate_html = (await utilities.future(requests.get, tracker_url)).text
        soup = BeautifulSoup(donate_html, 'html.parser')
        donation_text = soup.find('small').text.splitlines()[1:]
        total_raised, total_donations, _unused = donation_text[1].split()
        total_donations = total_donations.strip('()')
        max_average = donation_text[3]
    except Exception as e:
        raise CBException("Failed to retrieve donation data.", e=e)
    return (total_raised, total_donations, max_average)


async def _get_status(bot, raised_only=False):
    """Gets the stream status and information."""
    api_url = configurations.get(bot, __name__, 'api_url')
    client_id = configurations.get(bot, __name__, 'client_id')
    if not raised_only:
        try:
            stream_json = (await utilities.future(
                requests.get, api_url, headers={'Client-ID': client_id})).text
            stream_dictionary = json.loads(stream_json)
        except Exception as e:
            raise CBException("Failed to retrieve stream data.", e=e)
        stream_data = stream_dictionary['stream']
        status = "Online" if stream_data else "Offline"
        viewers = stream_data['viewers'] if stream_data else 0
    donation_stats = await _get_buffered_donation_stats(bot)
    if raised_only:
        return "**Total raised:** {}".format(total_raised)
    else:
        return (
            "**Stream:** {0}\n"
            "**Viewers:** {1}\n"
            "**Total raised:** {2}\n"
            "**Total donations:** {3}\n"
            "**Max / Average donation:** {4}").format(status, viewers, *donation_stats)


async def _set_debug_weeks(bot, weeks):
    """Change the week delta programmatically for testing purposes."""
    data.add(bot, __name__, 'debug_weeks', int(weeks), volatile=True)
    await _update_schedule(bot)
    return "Schedule updated."


async def _update_schedule(bot):
    """Reads the GDQ schedule and updates the information in the database."""
    schedule_url = configurations.get(bot, __name__, 'schedule_url')
    html_data = (await utilities.future(requests.get, schedule_url)).text
    soup = BeautifulSoup(html_data, 'html.parser')
    run_table = soup.find('table', {'id': 'runTable'})
    schedule_data = []
    game_list = []

    if run_table is None:
        raise CBException('Run table not found!')

    debug_weeks = data.get(bot, __name__, 'debug_weeks', default=0, volatile=True)
    current_data = {}
    for entry in run_table.find_all('tr'):
        entry_class = entry.get('class', [''])[0]

        if entry_class == 'day-split':
            continue

        subentries = [subentry.text for subentry in entry.find_all('td')][:2]
        if entry_class == 'second-row':  # Extra data for the last game
            estimation, run_type = subentries
            split_estimate = estimation.split(':')
            estimation_seconds = (int(split_estimate[0])*3600 +
                                  int(split_estimate[1])*60 +
                                  int(split_estimate[2]))
            end_time = (
                current_data['scheduled'] + datetime.timedelta(
                    seconds=(estimation_seconds + current_data['setup_seconds'])))
            key_name = utilities.get_cleaned_filename(
                current_data['game'] + run_type, cleaner=True)
            current_data.update({
                'estimation': estimation.strip(),
                'seconds': estimation_seconds,
                'type': run_type,
                'end': end_time,
                'key': key_name
            })
            game_list.append(key_name)
            schedule_data.append(current_data)

        else:  # Happens first
            while len(subentries) < 4:
                subentries.append('')
            start_time_string, game, runners, setup_time = subentries
            start_time = datetime.datetime.strptime(start_time_string, '%Y-%m-%dT%H:%M:%SZ')
            setup_time = setup_time.strip()
            split_setup = setup_time.split(':')
            if len(split_setup) > 1:
                setup_seconds = (int(split_setup[0])*3600 +
                                 int(split_setup[1])*60 +
                                 int(split_setup[2]))
            else:
                setup_seconds = 0
            current_data = {
                'scheduled': start_time - datetime.timedelta(weeks=debug_weeks),
                'game': game,
                'runners': runners,
                'setup': setup_time,
                'setup_seconds': setup_seconds
            }

    # Add finale entry
    run_type = 'Party%'
    end_time = current_data['scheduled'] + datetime.timedelta(minutes=30)
    key_name = utilities.get_cleaned_filename(current_data['game'] + run_type, cleaner=True)
    current_data.update({
        'estimation': '0:30:00',
        'seconds': 60*30,
        'end': end_time,
        'type': run_type,
        'key': key_name
    })
    game_list.append(key_name)
    schedule_data.append(current_data)

    # Update scheduled notifications
    entries = utilities.get_schedule_entries(bot, __name__)
    for entry in entries:
        payload, key = entry[3:5]
        if key not in game_list:  # Not found error
            error_message = (
                ":warning: Warning: The game {} has been removed, renamed, or "
                "recategorized. You have been removed from the notification list "
                "for this game. Please check the schedule at {}.".format(
                    payload['text'], configurations.get(bot, __name__, 'schedule_url')))
            utilities.update_schedule_entries(
                bot, __name__, search=key, payload={'error': error_message}, time=time.time())
        else:
            game = schedule_data[game_list.index(key)]
            start_time, end_time = game['scheduled'], game['end']
            setup_delta = datetime.timedelta(seconds=game['setup_seconds'])
            scheduled = start_time.replace(tzinfo=datetime.timezone.utc).timestamp()
            current_time = datetime.datetime.utcnow()
            if start_time + setup_delta < current_time < end_time:
                stream_url = configurations.get(bot, __name__, 'stream_url')
                payload = {'error': (
                        "Uh oh. The schedule shifted drastically and I didn't notice "
                        "fast enough - sorry! {} is live right now at {}").format(
                            payload['text'], stream_url)}
            else:
                payload.update({'end': scheduled + game['seconds']})
            utilities.update_schedule_entries(
                bot, __name__, search=key, payload=payload, time=scheduled)

    # Save data
    data.add(bot, __name__, 'schedule', schedule_data, volatile=True)
    try:
        _update_current_game(bot)
    except:
        pass


async def _update_menu(bot, response):
    while response.update_stats:
        donation_stats = await _get_buffered_donation_stats(bot)
        value = (
            "Total raised: {}\n"
            "Total donations: {}\n"
            "Max / Average donation: {}").format(*donation_stats)
        response.embed.set_field_at(0, name='Donation stats', value=value, inline=False)
        try:
            await response.message.edit(embed=response.embed)
        except:
            return
        await asyncio.sleep(60)


async def gdq_menu(bot, context, response, result, timed_out):
    if timed_out:
        response.update_stats = False
        if response.update_task:
            response.update_task.cancel()
        return
    if not result and not response.update_task:
        response.update_task = asyncio.ensure_future(_update_menu(bot, response))
        return
    selection = ['⬅', '⏺', '➡'].index(result[0].emoji)
    schedule_data = data.get(bot, __name__, 'schedule', volatile=True)
    guild_id = context.guild.id if context.guild else None

    if selection in (0, 2):  # Page navigation
        offset = -5 if selection == 0 else 5
        response.game_index = max(min(response.game_index + offset, len(schedule_data) - 5), 0)
    else:
        response.game_index = data.get(bot, __name__, 'current_index', volatile=True, default=0)
    games_list = schedule_data[response.game_index:response.game_index + 5]
    game_data = _embed_games_information(bot, games_list, guild_id)
    value = '\n\n'.join(
        '**[{}] {}**\n{}'.format(it+response.game_index+1, *c) for it, c in enumerate(game_data))
    response.embed.set_field_at(1, name='Schedule', value=value, inline=False)
    await response.message.edit(embed=response.embed)


async def get_response(bot, context):
    response = Response()
    use_plugin = configurations.get(bot, __name__, key='enable')
    if not use_plugin:
        response.content = (
            "The GDQ plugin is currently disabled. (GDQ is probably over or hasn't started yet)")
        return response

    embed_template = discord.Embed(
        title='Games Done Quick', url='https://gamesdonequick.com/',
        colour=discord.Colour(0x00aff0),
        description='\[ [Stream]({}) ] \[ [Schedule]({}) ] \[ [Donate]({}) ]'.format(
            configurations.get(bot, __name__, 'stream_url'),
            configurations.get(bot, __name__, 'schedule_url'),
            configurations.get(bot, __name__, 'donate_url')))
    embed_template.set_thumbnail(url='http://i.imgur.com/GcdqhUR.png')
    guild_id = context.guild.id if context.guild else None
    if context.index == 0:
        embed_template.add_field(name='Donation stats', value='Loading...', inline=False)
        response.game_index = data.get(bot, __name__, 'current_index', volatile=True, default=0)
        schedule_data = data.get(bot, __name__, 'schedule', volatile=True)
        games_list = schedule_data[response.game_index:response.game_index + 5]
        game_data = _embed_games_information(bot, games_list, guild_id)
        value = '\n\n'.join('**{}**\n{}'.format(*it) for it in game_data)
        embed_template.add_field(name='Schedule', value=value, inline=False)
        response.update_stats = True
        response.update_task = None
        response.message_type = MessageTypes.INTERACTIVE
        response.extra_function = gdq_menu
        response.extra = {'buttons': ['⬅', '⏺', '➡']}

    elif context.index == 1:  # About
        embed_template.add_field(name='About', value=(
            "Games Done Quick (GDQ) is a week-long charity gaming marathon that "
            "brings together speedrunners from around the globe to raise money on a "
            "livestream. They are currently supporting {0}, and all donations go "
            "directly to the charity.\n\nCheck out the links above for the Twitch "
            "stream, games schedule, and the donation portal!").format(
                configurations.get(bot, __name__, 'charity')))

    elif context.index == 2:  # Status
        status_text = await _get_status(bot)
        embed_template.add_field(name='Status', value=status_text, inline=False)

    elif context.index == 3:  # Current game
        embed_data = _get_current_game(bot, guild_id)[0]
        embed_template.add_field(name=embed_data[0], value=embed_data[1], inline=False)

    elif context.index == 4:  # Next game(s)
        embed_data = _get_next_games(bot, context.arguments[0], guild_id)
        for name, value in embed_data:
            embed_template.add_field(name=name, value=value, inline=False)

    elif context.index == 5:  # Search
        embed_data = _search_games(bot, context.arguments[0], guild_id=guild_id)
        embed_template.add_field(name=embed_data[0], value=embed_data[1], inline=False)

    elif context.index == 6:  # Notify
        game = _search_games(bot, context.arguments[0], return_game=True)
        response.content = _toggle_notification(
            bot, game, context, use_channel='channel' in context.options)
        embed_template = None

    response.embed = embed_template
    return response


async def _get_buffered_donation_stats(bot):
    """Pulls buffered donation information if it is 1 minute old or less."""
    last_pull = data.get(bot, __name__, 'last_pull', volatile=True, default=0)
    buffer_time = configurations.get(bot, __name__, 'stats_buffer_time')
    if time.time() - last_pull > buffer_time:  # Pull information
        data.add(bot, __name__, 'last_pull', time.time(), volatile=True)
        tracker_url = configurations.get(bot, __name__, 'tracker_url')
        try:
            donate_html = (await utilities.future(requests.get, tracker_url)).text
            soup = BeautifulSoup(donate_html, 'html.parser')
            donation_text = soup.find('small').text.splitlines()[1:]
            total_raised, total_donations, _unused = donation_text[1].split()
            total_donations = total_donations.strip('()')
            max_average = donation_text[3]
        except Exception as e:
            raise CBException("Failed to retrieve donation data.", e=e)
        donation_stats = [total_raised, total_donations, max_average]
        data.add(bot, __name__, 'donation_stats', donation_stats, volatile=True)
    else:
        donation_stats = data.get(bot, __name__, 'donation_stats', volatile=True)
    return donation_stats


async def bot_on_ready_boot(bot):
    """Constantly updates the schedule data."""
    use_plugin = configurations.get(bot, __name__, key='enable')
    while use_plugin:
        try:
            await _update_schedule(bot)
        except Exception as e:
            logger.warn("Failed to update the GDQ schedule. %s", e)
            await asyncio.sleep(20*60)
        await asyncio.sleep(10*60)
