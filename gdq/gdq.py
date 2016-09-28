import asyncio
import json
import datetime
import requests
import random

from bs4 import BeautifulSoup

from jshbot import utilities, data, configurations
from jshbot.commands import Command, SubCommands
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'GDQ plugin'
uses_configuration = True


def get_commands():
    commands = []

    commands.append(Command(
        'gdq', SubCommands(
            ('', ' ', 'Gets some general information about GDQ.'),
            ('schedule', 'schedule', 'Gets the link to the schedule.'),
            ('donate', 'donate', 'Gets the link to the donation page.'),
            ('next &', 'next (<number>)', 'Shows the next game in the '
             'schedule. Up to 5 games can be displayed.'),
            ('current', 'current', 'Shows the current game on the schedule.'),
            ('search ^', 'search <game>', 'Searches for the given game.'),
            ('status', 'status', 'Gets the stream status and total amount '
             'of money raised.'),
            ('notify ?channel ^', 'notify (channel) <game title>', 'Sends a '
             'message to either the user or the channel for the given game '
             'when it is about to be streamed (approximately 10 minutes).')),
        description='Games Done Quick for Discord.', group='service'))

    return commands


def toggle_notify(bot, search, message, use_channel=False):
    """Adds the user or channel to the notifications list."""
    if use_channel:
        location_id = message.channel.id
    else:
        location_id = message.author.id
    game = search_games(bot, search, return_game=True)
    clean_title = utilities.get_cleaned_filename(game['game'], cleaner=True)
    stream_url = configurations.get(bot, __name__, 'stream_url')
    current_time = datetime.datetime.utcnow()
    end_time = game['time'] + datetime.timedelta(seconds=game['seconds'])
    notify_games = data.get(bot, __name__, 'notify_games', default={})
    notification_pair = [location_id, use_channel]

    if (clean_title in notify_games and
            notification_pair in notify_games[clean_title]):
        notify_games[clean_title].remove(notification_pair)
        if not notify_games[clean_title]:
            del notify_games[clean_title]
        return (
            "{0} will be no longer be notified when {1} is about to be "
            "streamed.").format(
                'This channel' if use_channel else 'You', game['game'])

    if current_time < game['time'] - datetime.timedelta(minutes=10):

        if not notify_games:
            notify_games = {clean_title: [notification_pair]}
            data.add(bot, __name__, 'notify_games', notify_games)
        else:
            if clean_title in notify_games:
                notify_games[clean_title].append(notification_pair)
            else:
                notify_games[clean_title] = [notification_pair]

        return (
            "{0} will be notified when {1} is about to be streamed!").format(
                'This channel' if use_channel else 'You', game['game'])

    elif current_time < game['time']:
        return ("The game is scheduled to start soon!\n"
                "Tune in now at {}".format(stream_url))
    elif current_time < end_time:
        return ("The game has already started!\n"
                "You can watch it now at {}".format(stream_url))
    else:
        return "Sorry, this game has already been finished."


def get_game_information(games):
    """Formats the given games into a nice looking string."""
    responses = []
    current_time = datetime.datetime.utcnow()
    for game in games:
        end_time = game['time'] + datetime.timedelta(seconds=game['seconds'])

        if current_time < game['time']:  # Upcoming
            play_title = "Upcoming"
            begin_delta = game['time'] - current_time
            begins_in = ''
            delta_hours = int((begin_delta.seconds/3600) % 24)
            delta_minutes = int((begin_delta.seconds/60) % 60)
            if begin_delta.days:
                begins_in += '{} day(s)'.format(begin_delta.days)
            if delta_hours:
                begins_in += '{0}{1} hour(s)'.format(
                    ', ' if begins_in else '', delta_hours)
            if delta_minutes:
                begins_in += '{0}{1} minute(s)'.format(
                    ', and ' if begins_in else '', delta_minutes)
            if not begins_in:
                begins_in = "a few moments!"
            extra = (
                '\n**Setup time:** {0[setup]}\n'
                '**Scheduled:** {1.month}/{1.day} '
                '{1.hour:02d}:{1.minute:02d}:{1.second:02d} UTC\n'
                '**Begins in:** {2}').format(game, game['time'], begins_in)

        elif current_time < end_time:  # Current
            play_title = "Current"
            remaining_delta = end_time - current_time
            remaining_time_detailed = [
                int((remaining_delta.seconds/3600) % 24),
                int((remaining_delta.seconds/60) % 60),
                int(remaining_delta.seconds % 60)]
            current_delta = current_time - game['time']
            current_time_detailed = [
                int((current_delta.seconds/3600) % 24),
                int((current_delta.seconds/60) % 60),
                int(current_delta.seconds % 60)]
            extra = (
                '\n**Remaining time:** {0[0]}:{0[1]:02d}:{0[2]:02d}\n'
                '**Current time:** {1[0]}:{1[1]:02d}:{1[2]:02d}').format(
                    remaining_time_detailed, current_time_detailed)

        else:  # Finished
            play_title = "Finished"
            extra = ''

        responses.append((
            '**{0} game:** {1[game]}\n'
            '**Speedrun type:** {1[type]}\n'
            '**Runner(s):** {1[runners]}\n'
            '**Estimated time:** {1[estimation]}'
            '{2}').format(play_title, game, extra))

    return '\n\n'.join(responses)


def update_latest_index(bot, include_setup_status=False):
    """Updates the index of the latest/current game."""
    schedule_data = data.get(bot, __name__, 'schedule_data', volatile=True)
    latest_index = data.get(bot, __name__, 'current', volatile=True, default=0)
    current_time = datetime.datetime.utcnow()
    for index, game in enumerate(schedule_data[latest_index:]):

        # Update the latest index
        end_time = game['time'] + datetime.timedelta(seconds=game['seconds'])
        if current_time < end_time:
            latest_index += index
            data.add(bot, __name__, 'current', latest_index, volatile=True)
            if include_setup_status:
                return (latest_index, current_time < game['time'])
            else:
                return latest_index

    raise BotException(EXCEPTION, "No current game was found.")


async def get_games(bot, next_game=False, extra=0):
    """Gets the current/next game(s) and the defined number of extra games."""
    if extra not in (0, ''):
        try:
            extra = int(extra)
        except:
            raise BotException(EXCEPTION, "That is not a valid integer.")
        if extra < 1 or extra > 5:
            raise BotException(
                EXCEPTION, "Can only list between [1-5] inclusive.")
        extra -= 1
    else:
        extra = 0
    latest_index, setup = update_latest_index(bot, include_setup_status=True)
    schedule_data = data.get(bot, __name__, 'schedule_data', volatile=True)
    if next_game and not setup:
        latest_index += 1
    games_list = schedule_data[latest_index:latest_index + extra + 1]
    games_information = get_game_information(games_list)
    if games_information:
        return games_information
    else:
        raise BotException(
            EXCEPTION, "{} game information not found.".format(
                'Upcoming' if setup else 'Current'))


def search_games(bot, search, return_game=False):
    """Searches the schedule for the given game and gets the information."""
    cleaned_search = utilities.get_cleaned_filename(search, cleaner=True)
    schedule_data = data.get(bot, __name__, 'schedule_data', volatile=True)
    games = data.get(bot, __name__, 'game_list', volatile=True, default=[])
    found_games = []
    for index, game in enumerate(games):
        if cleaned_search in game:
            found_games.append(schedule_data[index])
    if not found_games:
        raise BotException(EXCEPTION, "No games found with that name.")
    elif len(found_games) > 10:
        raise BotException(EXCEPTION, "Too many games found with that name.")
    elif len(found_games) != 1:
        raise BotException(
            EXCEPTION, "Multiple games found:",
            '\n'.join([game['game'] for game in found_games]))
    elif return_game:
        return found_games[0]
    else:
        return get_game_information(found_games)


async def get_donation_data(bot):
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
        raise BotException(EXCEPTION, "Failed to retrieve donation data.", e=e)
    return (total_raised, total_donations, max_average)


async def get_status(bot):
    """Gets the stream status and information."""
    api_url = configurations.get(bot, __name__, 'api_url')
    try:
        stream_json = (await utilities.future(requests.get, api_url)).text
        stream_dictionary = json.loads(stream_json)
    except Exception as e:
        raise BotException(EXCEPTION, "Failed to retrieve stream data.", e=e)
    stream_data = stream_dictionary['stream']
    status = "Online" if stream_data else "Offline"
    viewers = stream_data['viewers'] if stream_data else 0
    donation_data = await get_donation_data(bot)
    return (
        "**Stream status:** {0}\n"
        "**Viewers:** {1}\n"
        "**Total raised:** {2}\n"
        "**Total donations:** {3}\n"
        "**Max / Average donation:** {4}").format(
            status, viewers, *donation_data)


async def update_schedule(bot):
    """Reads the GDQ schedule and updates the information in the database."""
    schedule_url = configurations.get(bot, __name__, 'schedule_url')
    html_data = (await utilities.future(requests.get, schedule_url)).text
    soup = BeautifulSoup(html_data, 'html.parser')
    run_table = soup.find('table', {'id': 'runTable'})
    schedule_data = []
    game_list = []

    for entry in run_table.find_all('tr'):
        entry_class = entry.get('class', [''])[0]

        if entry_class == 'day-split':
            continue

        subentries = [subentry.text for subentry in entry.find_all('td')]
        if entry_class == 'second-row':  # Extra data for the last game
            estimation, run_type = subentries
            split_estimate = estimation.split(':')
            estimation_seconds = (int(split_estimate[0])*3600 +
                                  int(split_estimate[1])*60 +
                                  int(split_estimate[2]))
            schedule_data[-1].update({
                'estimation': estimation.strip(),
                'seconds': estimation_seconds,
                'type': run_type
            })

        else:
            while len(subentries) < 4:
                subentries.append('')
            start_time_string, game, runners, setup_time = subentries
            start_time = datetime.datetime.strptime(
                start_time_string, '%Y-%m-%dT%H:%M:%SZ')
            schedule_data.append({
                'time': start_time,
                'game': game,
                'runners': runners,
                'setup': setup_time.strip()
            })
            game_list.append(
                utilities.get_cleaned_filename(game, cleaner=True))

    # Add finale entry
    schedule_data[-1].update({
        'estimation': '2:00:00',
        'seconds': 60*120,
        'type': 'Party%',
        'setup': 'n/a'
    })

    # Save data
    data.add(bot, __name__, 'schedule_data', schedule_data, volatile=True)
    data.add(bot, __name__, 'game_list', game_list, volatile=True)


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    use_plugin = configurations.get(bot, __name__, 'enable')
    if not use_plugin:
        response = (
            "The GDQ plugin is currently disabled. (GDQ is probably "
            "over or hasn't started yet)")
    elif blueprint_index == 1:  # schedule
        response = configurations.get(bot, __name__, 'schedule_url')
    elif blueprint_index == 2:  # donate
        response = configurations.get(bot, __name__, 'donate_url')
    elif blueprint_index == 3:  # next
        response = await get_games(bot, next_game=True, extra=arguments[0])
    elif blueprint_index == 4:  # current
        response = await get_games(bot)
    elif blueprint_index == 5:  # search
        response = search_games(bot, arguments[0])
    elif blueprint_index == 6:  # status
        response = await get_status(bot)
    elif blueprint_index == 7:  # notify
        response = toggle_notify(
            bot, arguments[0], message,
            use_channel='channel' in options)
    elif blueprint_index == 0:  # general info
        response = (
            "GDQ (Games Done Quick) is a charity gaming marathon that brings "
            "together speedrunners from around the globe to raise money on a "
            "livestream.\nThey are currently supporting Doctors Without "
            "Borders, and all donations go directly to the charity.\nCheck "
            "out GDQ at https://gamesdonequick.com and the Twitch stream at "
            "{}").format(configurations.get(bot, __name__, 'stream_url'))

    return (response, tts, message_type, extra)


async def bot_on_ready_boot(bot):
    """Notifies users that a game is about to be played."""
    use_plugin = configurations.get(bot, __name__, key='enable')

    if use_plugin:
        stream_url = configurations.get(bot, __name__, 'stream_url')
        update_counter = 0
        update_time = 10  # 10 minute update interval default
        time_leeway = datetime.timedelta(minutes=10)  # 10 minute default
        notify_message = [
            "Heads up,",
            "Just so you know,",
            "Just letting you know,",
            "Attention,",
            "Ping!",
            "Hey,"
        ]
        await update_schedule(bot)

    while use_plugin:
        current_time = datetime.datetime.utcnow()
        if update_counter >= update_time:
            await update_schedule(bot)
            update_counter = 0
        notify_games = data.get(bot, __name__, 'notify_games', default={})
        schedule_data = data.get(bot, __name__, 'schedule_data', volatile=True)
        game_list = data.get(
            bot, __name__, 'game_list', volatile=True, default=[])

        to_remove = []
        for game_key, notification_list in notify_games.items():
            game_index = game_list.index(game_key)  # May throw an exception
            game = schedule_data[game_index]

            if current_time > game['time'] - time_leeway:
                to_remove.append(game_key)
                game_length = datetime.timedelta(seconds=game['seconds'])
                if game['time'] < current_time < game['time'] + game_length:
                    response = (
                        "Uh oh. Either the schedule was shifted drastically "
                        "or I missed the timer - sorry! {} is live right "
                        "now.").format(game['game'])
                elif current_time >= game['time'] + game_length:
                    response = (
                        "Sorry! I missed the notification for {}. You can "
                        "watch the VOD in a few days.").format(game['game'])
                else:
                    response = (
                        "{0} {1} is about to be played soon. Tune in to "
                        "watch the speedrun live!").format(
                            random.choice(notify_message), game['game'])
                response += '\n{}'.format(stream_url)

                for location_id, use_channel in notification_list:
                    if use_channel:
                        location = bot.get_channel(location_id)
                    else:
                        location = data.get_member(bot, location_id)
                    if location:
                        asyncio.ensure_future(
                            bot.send_message(location, response))
                        await asyncio.sleep(0.05)  # Probably not necessary

        for expired in to_remove:
            del notify_games[expired]

        await asyncio.sleep(60)
        update_counter += 1
