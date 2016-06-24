import requests
import time
import random
import math

# Debugging
import logging

from riotwatcher import RiotWatcher
from riotwatcher import LoLException, error_429, error_404

from jshbot import data, configurations
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import ErrorTypes, BotException
from jshbot.utilities import future

__version__ = '0.1.6'
EXCEPTION = 'Riot API plugin'
uses_configuration = True


def get_commands():
    """Sets up new commands and shortcuts in the proper syntax."""
    commands = []

    commands.append(Command(
        'lol', SubCommands(
            ('summoner ^', 'summoner <summoner name>', 'Gets some basic '
             'information about the given summoner.'),
            ('match ^', 'match <summoner name>', 'Gets the current or last '
             'ranked match of the given summoner.'),
            ('mastery ?champion: ^', 'mastery (champion <"champion name">) '
             '<summoner name>', 'Gets a list of the top 10 champions of the '
             'summoner based off of mastery. If a champion is specified, it '
             'will return information for that champion only.'),
            ('chests ^', 'chests <summoner name>', ''),
            ('challenge ::::', 'challenge <"summoner 1"> <"summoner 2"> '
             '<"champion 1"> <"champion 2">', 'This command compares two '
             'summoner\'s mastery points, mastery levels, and number of '
             'games played (ranked) data against each other.'),
            ('setregion &', 'setregion (region)', 'Sets the default region '
             'for the server. Valid region codes are NA (default), BR, EUNE, '
             'EUW, JP, KR, LAN, LAS, OCE, RU, and TR.'),
            ('region', 'region', 'Gets the current default region for this '
             'server.')),
        shortcuts=Shortcuts(
            ('summoner', 'summoner {}', '^', 'summoner <summoner name>',
             '<summoner name>'),
            ('match', 'match {}', '^', 'match <summoner name>',
             '<summoner name>'),
            ('mastery', 'mastery {}', '^', 'mastery <summoner name>',
             '<summoner name>'),
            ('challenge', 'challenge {} {} {} {}', '::::', 'challenge '
             '<"summoner 1"> <"summoner 2"> <"champion 1"> <"champion 2">',
             '<"summoner 1"> <"summoner 2"> <"champion 1"> <"champion 2">'),
            ('blitz', '{}', '^', '<arguments>', '<arguments>')),
        description='Get League of Legends information from the API.',
        other='You can specify the region for a summoner by adding '
              '`:<region>` after the name. For example, try\n`{invoker}lol '
              'summoner hide on bush:kr`'))

    return commands


def api_cooldown():
    """Raises the cooldown exception if the API is being used too often."""
    raise BotException(
        EXCEPTION,
        "API is being used too often right now. Please try again later.")


async def get_summoner_wrapper(static, name, region, summoner_id=None):
    """Gets the summoner information. Returns None if not found."""
    watcher = static[0]
    regions = static[4]
    try:
        if ':' in name:  # Custom region
            name, region = name.split(':', 1)
            region = region.lower()
            if region not in regions.values():
                if region in regions:
                    region = regions[region]
                else:
                    raise BotException(
                        EXCEPTION, "That is not a defined region.")

        if summoner_id:
            summoner = await future(
                watcher.get_summoner, _id=summoner_id, region=region)

        else:
            summoner = await future(
                watcher.get_summoner, name=name, region=region)

        return (summoner, region)

    except Exception as e:
        if e == error_429:
            api_cooldown()
        elif e == error_404:
            if summoner_id is None and name.isdigit():  # Possibly given an ID
                return await get_summoner_wrapper(
                    static, name, region, summoner_id=name)
            else:
                raise BotException(
                    EXCEPTION, "Summoner {0} not found in region {1}.".format(
                        name, region.upper()))
        else:
            raise BotException(
                EXCEPTION, "Failed to get summoner information.", e=e)


async def get_league_wrapper(watcher, summoner_ids, region):
    """Gets the league. Returns an empty dictionary if not found."""
    try:
        return await future(
            watcher.get_league_entry, summoner_ids=summoner_ids, region=region)
    except Exception as e:
        if e == error_429:
            api_cooldown()
        elif e == error_404:
            logging.warn("Summoner has not played ranked.")
            return {}
        else:
            raise BotException(
                EXCEPTION, "Failed to get summoner league.", e=e)


async def get_match_list_wrapper(watcher, summoner_id, region):
    """Gets the match list. Returns None if no matches are found."""
    try:  # TODO: Convert to recent game instead, but the API is so different
        match_list = await future(
            watcher.get_match_list, summoner_id, region=region)
        if 'matches' not in match_list:
            logging.warn("Summoner has no matches.")
            return []
        return match_list['matches']
    except Exception as e:
        if e == error_429:
            api_cooldown()
        elif e == error_404:
            logging.warn("Summoner has no match list.")
            return []
        else:
            raise BotException(EXCEPTION, "Failed to get the match list.", e=e)


def get_recent_match(match_list, no_team=False):
    """Gets the most recent match from the match list."""
    if not match_list:
        return None
    elif not no_team:  # Just get first match
        return match_list[0]['matchId']
    else:
        for match in match_list:
            if match['queue'].startswith('RANKED_TEAM_'):
                continue
            else:
                return match['matchId']
        return None  # No suitable match was found


async def get_match_wrapper(watcher, match_id, region):
    """Gets the match given match_id. Includes exception handling."""
    try:
        return await future(watcher.get_match, match_id, region=region)
    except Exception as e:
        if e == error_429:
            api_cooldown()
        elif e == error_404:
            return None
        else:
            raise BotException(
                EXCEPTION, "Failed to get match information.", e=e)


async def get_current_match_wrapper(static, summoner_id, region):
    """Returns the current match if there is one, otherwise returns None."""
    watcher = static[0]
    platform = static[5][region]
    try:
        return await future(
            watcher.get_current_game, summoner_id,
            platform_id=platform, region=region)
    except Exception as e:
        if e == error_429:
            api_cooldown()
        elif e == error_404:
            return None
        else:
            raise BotException(
                EXCEPTION, "Failed to get the current match.", e=e)


async def get_mastery_wrapper(
        bot, static, summoner_id, region, top=True, champion_id=None):
    """Gets the player's champion mastery, otherwise returns None.

    Keyword arguments:
    top -- gets the champions organized by most mastery points
    champion_id -- if specified, gets the given champion's mastery
    """
    platform = static[5][region]
    api_key = bot.configurations['discrank.py']['token']
    if champion_id:
        champion = '/{}'.format(champion_id)
        top = False
    else:
        champion = 's'
    get_top = 'top' if top else ''
    url = ('https://{0}.api.pvp.net/championmastery/location/{1}/player/{2}/'
           '{3}champion{4}?api_key={5}').format(
               region, platform, summoner_id, get_top, champion, api_key)

    response = await future(requests.get, url)
    try:
        result = response.json()
    except:
        return None
    if 'status' in result:
        error_code = result['status']['status_code']
        if error_code == 429:
            api_cooldown()
        else:
            logging.error("This is the requests result: " + str(result))
            raise BotException(
                EXCEPTION, "Failed to retrieve mastery data.", error_code)
    else:
        return result


def get_top_champions(static, mastery):
    """Gets the top 3 champions based on mastery."""
    if not mastery:
        return None
    champions = []
    if len(mastery) == 0:
        return None
    elif len(mastery) < 3:
        max_range = len(mastery)
    else:
        max_range = 3
    for x in range(max_range):
        champion_id = str(mastery[x]['championId'])
        champions.append(static[1][champion_id]['name'])
    return ', '.join(champions)


def get_mastery_details(static, mastery):
    """Returns a string of details for the given mastery."""
    champion_id = str(mastery['championId'])
    champion_name = static[1][champion_id]['name']
    return ('{0}:\n'
            '\tPoints: {1[championPoints]}\n'
            '\tLevel: {1[championLevel]}\n'
            '\tHighest Grade: {1[highestGrade]}\n').format(
                champion_name, mastery)


def get_participant(match, summoner_id, finished):
    """Gets the summoner from the given match."""
    if finished:  # Add summoner name and match URI to final return
        for participant_entry in match['participantIdentities']:
            if participant_entry['player']['summonerId'] == summoner_id:
                index = participant_entry['participantId'] - 1
                break
        participant = match['participants'][index]
        participant.update(participant_entry['player'])
        return participant

    else:  # Just the match given should be sufficient
        for index, participant in enumerate(match['participants']):
            if participant['summonerId'] == summoner_id:
                participant['participantId'] = index + 1
                return participant

    raise BotException(EXCEPTION, "Summoner not found in match participants.")


async def get_champion_kda(watcher, summoner_id, champion_id, region):
    """Gets a nicely formatted string of the summoner's champion KDA."""
    try:
        stats = await future(
            watcher.get_ranked_stats, summoner_id, region=region)
    except LoLException as e:
        if e == error_429:
            return 'API Limit'
        else:  # Champion data not found
            return '0/0/0 (0)'
    for champion in stats['champions']:
        if champion['id'] == champion_id:
            break
    stats = champion['stats']
    sessions = stats['totalSessionsPlayed']
    if sessions == 0:
        return '0/0/0 (0)'
    kills = stats['totalChampionKills'] / sessions
    deaths = stats['totalDeathsPerSession'] / sessions
    assists = stats['totalAssists'] / sessions
    value = (kills + assists) / (1 if deaths == 0 else deaths)
    return "{0:.1f}/{1:.1f}/{2:.1f} ({3:.1f})".format(
        kills, deaths, assists, value)


def get_kill_participation(match, participant_id, side):
    """Gets a string of the kill participation of the summoner."""
    total_kills = 0
    for participant in match['participants']:
        stats = participant['stats']
        if participant['teamId'] == side:
            if participant['participantId'] == participant_id:
                participant_kills = stats['kills']
                participant_kills += stats['assists']
            total_kills += stats['kills']
    total_kills = 1 if total_kills <= 0 else total_kills
    return '{0:.1f}%'.format(100*participant_kills/total_kills)


def get_bans(static, match, team, finished=True):
    """Gets the 3 bans for the given team in the given match."""
    bans = []
    if finished:
        ban_list = match['teams'][int((team/100) - 1)]['bans']
        for it in range(3):
            bans.append(static[1][str(ban_list[it]['championId'])]['name'])
    else:
        for ban in match['bannedChampions']:
            if ban['teamId'] == team:
                bans.append(static[1][str(ban['championId'])]['name'])
    return bans


async def get_match_table(
        static, match, mastery, summoner_id, region,
        finished=True, verbose=False):
    """Returns a scoreboard view of the given match."""
    watcher = static[0]
    divisions = {
        "V": "5",
        "IV": "4",
        "III": "3",
        "II": "2",
        "I": "1"
    }
    participant = get_participant(match, summoner_id, finished)
    response = ''

    # Get game type and also time if the game is not finished
    if finished:
        queue_id = static[3][match['queueType']]
        game_length_key = 'matchDuration'
    else:
        try:
            queue_id = str(match['gameQueueConfigId'])
        except KeyError:
            queue_id = '0'
        game_length_key = 'gameLength'
    total_length = int(match[game_length_key]) + 180
    minutes = str(int(total_length/60))
    seconds = "{0:02d}".format(total_length % 60)
    game = static[3][queue_id]

    # Get ranking for each player
    summoners = []
    for index, member in enumerate(match['participants']):
        if finished:
            summoner = match['participantIdentities'][index]
            summoners.append(summoner['player']['summonerId'])
        else:
            summoners.append(member['summonerId'])
    league_data = await get_league_wrapper(static[0], summoners, region)

    # Very detailed table
    if verbose:
        response = '```diff\n'  # Use + and - to highlight

        # Get winning team number
        if finished:
            if participant['stats']['winner']:
                winning_team = participant['teamId']
            else:
                winning_team = 100 if participant['teamId'] == 200 else 200

        # Add current game time
        response += "{2}Game Time: {0}:{1}\n".format(
            minutes, seconds, '' if finished else 'Current ')
        # Game type
        response += 'Game Type: {}\n\n'.format(game)

        # Loop through each team
        for team in (100, 200):

            # Team
            response += '{} Team'.format(
                    'Blue' if team == 100 else 'Red')

            # Get bans
            try:
                bans = "{0}, {1}, {2}".format(
                        *get_bans(static, match, team, finished))
                response += ' -- Bans [{}]'.format(bans)
            except:
                logging.warn("No bans.")

            # Add game won or lost
            if finished:
                status = 'WON' if team == winning_team else 'LOST'
                response += ' [{}]\n'.format(status)
            else:
                response += '\n'

            # Loop through each participant on the team
            response += ('  Summoner         Rank | Champion     | '
                         'KDA                   | Spell 1  | Spell 2  |\n'
                         '------------------------|--------------|-'
                         '----------------------|----------|----------|\n')
            for index, member in enumerate(match['participants']):
                if member['teamId'] != team:  # Continue
                    continue

                # Get summoner name
                if finished:
                    summoner = match['participantIdentities'][index]
                    summoner_name = summoner['player']['summonerName']
                    summoner_id = str(summoner['player']['summonerId'])
                else:
                    summoner_name = member['summonerName']
                    summoner_id = str(member['summonerId'])

                # Get summoner rank
                if summoner_id in league_data:
                    league = league_data[summoner_id][0]
                    rank = '({0}{1})'.format(
                        league['tier'][0],
                        divisions[league['entries'][0]['division']])
                else:
                    rank = ''

                # Get champion name and spell names
                champion = static[1][str(member['championId'])]['name']
                spell1 = static[2][str(member['spell1Id'])]['name']
                spell2 = static[2][str(member['spell2Id'])]['name']

                # Get KDA
                if finished:  # Pull from participant data
                    stats = member['stats']
                    kills, deaths = stats['kills'], stats['deaths']
                    assists = stats['assists']
                    value = "({0:.1f})".format(
                        ((kills + assists) / (1 if deaths == 0 else deaths)))
                    kda = "{0[kills]}/{0[deaths]}/{0[assists]} {1}".format(
                        stats, value)
                else:
                    kda = await get_champion_kda(
                        watcher, member['summonerId'], member['championId'],
                        region)

                # Highlight summoner if this is the one we're looking for
                if index == participant['participantId'] - 1:
                    response += '+ '
                else:
                    response += '  '

                # Add champion name, kda, and spells
                response += ('{0: <17}{1: >4} | {2: <13}| {3: <22}| {4: <9}| '
                             '{5: <9}|\n').format(
                                 summoner_name, rank, champion,
                                 kda, spell1, spell2)

            response += '\n'

        response += '\n```\n'

    # Simple 3-4 line game info
    else:

        # Get KDA
        champion_id = participant['championId']
        if finished:  # Pull from participant data
            stats = participant['stats']
            kills = stats['kills']
            assists = stats['assists']
            deaths = stats['deaths'] if stats['deaths'] else 1
            value = "({0:.1f})".format((kills + assists) / deaths)
            kda = "{0[kills]}/{0[deaths]}/{0[assists]} {1}".format(
                stats, value)
        else:  # Pull from league data
            kda = await get_champion_kda(
                watcher, summoner_id, champion_id, region)

        # Get spell names
        spell1 = static[2][str(participant['spell1Id'])]['name']
        spell2 = static[2][str(participant['spell2Id'])]['name']
        champion = static[1][str(champion_id)]['name']

        # Get mastery data
        if mastery:
            for champion_mastery in mastery:
                if champion_mastery['championId'] == champion_id:
                    break
            mastery_data = "({0[championPoints]}|{0[championLevel]})".format(
                    champion_mastery)
        else:
            mastery_data = "(No mastery)"

        # Format response
        if finished:
            status = 'Won' if participant['stats']['winner'] else 'Lost'
            kill_participation = get_kill_participation(
                    match, participant['participantId'], participant['teamId'])
            response += (
                "**Game Type:** {0}\n"
                "{1} - {2} {3} - Kill Participation {4} - {5} - {6}\n"
                "Status: {7}").format(
                    game, champion, kda, mastery_data, kill_participation,
                    spell1, spell2, status)
        else:
            side = 'Blue' if participant['teamId'] == 100 else 'Red'
            response += ("**Game Type:** {0}\n"
                         "{1} - {2} {3} - {4} - {5}\n"
                         "Side: {6}\n"
                         "Time: {7}:{8}").format(
                             game, champion, kda, mastery_data, spell1, spell2,
                             side, minutes, seconds)

    return response


async def get_match_table_wrapper(bot, static, name, region, verbose=False):
    """Gets the match table. Makes the calling method easier to look at."""
    watcher = static[0]
    summoner, region = await get_summoner_wrapper(static, name, region)
    mastery = await get_mastery_wrapper(
        bot, static, summoner['id'], region, top=False)

    # Get last match or current match information
    match = await get_current_match_wrapper(static, summoner['id'], region)
    currently_playing = bool(match)
    if not currently_playing:  # Get most recent match
        match_list = await get_match_list_wrapper(
            watcher, summoner['id'], region)
        recent_match = get_recent_match(match_list, no_team=True)
        match = await get_match_wrapper(watcher, recent_match, region)

    if match:
        return await get_match_table(
            static, match, mastery, summoner['id'], region,
            finished=(not currently_playing), verbose=verbose)
    else:
        return "A most recent match was not found..."


async def get_summoner_information(bot, static, name, region, verbose=False):
    """Get a nicely formatted string of summoner data."""
    watcher = static[0]
    summoner, region = await get_summoner_wrapper(static, name, region)
    mastery = await get_mastery_wrapper(
        bot, static, summoner['id'], region, top=False)
    response = ("***`{0[name]}`***\n"
                "**Summoner ID:** {0[id]}\n"
                "**Level:** {0[summonerLevel]}\n"
                "**Top Champions:** {1}\n\n").format(
                    summoner, get_top_champions(static, mastery))

    # Get league information
    summoner_id = str(summoner['id'])
    league = await get_league_wrapper(watcher, [summoner_id], region)
    if league:
        league = league[summoner_id][0]

        # Extra champion mastery data if we want extra information
        if verbose:
            mastery_details = []
            for it in range(3):
                mastery_details.append(
                    get_mastery_details(static, mastery[it]))
            response += ("***`Champion Mastery`***\n"
                         "**First:** {0}"
                         "**Second:** {1}"
                         "**Third:** {2}\n").format(*mastery_details)

        # Ranked statistics
        entries = league['entries'][0]
        division = league['tier'].capitalize() + ' ' + entries['division']
        wlr = 100 * entries['wins'] / (entries['wins'] + entries['losses'])
        response += ("***`Ranked Statistics`***\n"
                     "**Rank:** {0}\n"
                     "**League Points:** {1[leaguePoints]}\n"
                     "**Wins/Losses:** {1[wins]}/{1[losses]}\n"
                     "**W/L Percent:** {2:.2f}%\n\n").format(
                         division, entries, wlr)
    else:
        response += "This summoner has not played ranked yet this season...\n"

    # Get last match or current match information
    match = await get_current_match_wrapper(static, summoner['id'], region)
    currently_playing = bool(match)
    if not currently_playing:  # Get most recent match
        match_list = await get_match_list_wrapper(
            watcher, summoner['id'], region)
        recent_match = get_recent_match(match_list, no_team=True)
        match = await get_match_wrapper(watcher, recent_match, region)

    # If a suitable match was found, get the information
    if match:
        response += "***`{} Match`***\n".format(
                'Current' if currently_playing else 'Last')
        response += await get_match_table(
            static, match, mastery, summoner['id'], region,
            finished=(not currently_playing), verbose=False)
    else:
        response += "A most recent match was not found...\n"

    return response


def get_formatted_mastery_data(static, champion_data):
    """Gets a nicely formatted line of mastery data."""
    print(champion_data)
    champion_name = static[1][str(champion_data['championId'])]['name']
    chest = 'Yes' if champion_data['chestGranted'] else 'No'
    if 'lastPlayTime' in champion_data:
        last_played = time.time() - champion_data['lastPlayTime']/1000
        last_played = '{0:.1f} d'.format(last_played/86400)
    else:  # No data
        last_played = 'Unknown'
    if 'highestGrade' in champion_data:
        highest_grade = champion_data['highestGrade']
    else:
        highest_grade = 'n/a'
    return ('{0: <14}| {1[championPoints]: <10}| {1[championLevel]: <4}| '
            '{2: <4}| {3: <6}| {4}\n').format(
                champion_name, champion_data, chest,
                highest_grade, last_played)


async def get_mastery_table(bot, static, name, region, champion=None):
    """Gets mastery information for the given summoner.

    If the champion argument is specified, it will find the details of that
    champion only. The table generated will be the top 10 champions of the
    summoner.
    """
    summoner, region = await get_summoner_wrapper(static, name, region)
    if champion:
        try:
            champion_id = static[1][champion.replace(' ', '').lower()]['id']
            champion_data = await get_mastery_wrapper(
                bot, static, summoner['id'], region, champion_id=champion_id)
        except KeyError:
            raise BotException(EXCEPTION, "Champion not found.")
        if champion_data is None:
            raise BotException(
                EXCEPTION, "This summoner has no mastery data for the given "
                "champion.")
    else:
        champion_data = await get_mastery_wrapper(
            bot, static, summoner['id'], region, top=False)

    labels = '#  | Champion      | Points    | Lvl | Box | Grade | Last Played'
    line = '---|---------------|-----------|-----|-----|-------|-------------'

    if champion:
        labels = labels[5:]
        line = line[5:]

    response = '```\n{}\n{}\n'.format(labels, line)

    if not champion_data:
        raise BotException(EXCEPTION, "This summoner has no mastery data.")

    if champion:
        response += get_formatted_mastery_data(static, champion_data)
    else:
        for it in range(10):
            if it < len(champion_data):
                data = get_formatted_mastery_data(static, champion_data[it])
                response += '{0: <3}| {1}'.format(it + 1, data)
    return response + '```'


async def get_ranked_stats_wrapper(watcher, summoner_id, region):
    """Gets ranked stats. Return None if not found."""
    try:
        return await future(
            watcher.get_ranked_stats,
            summoner_id,
            region=region)
    except Exception as e:
        if e == error_429:
            api_cooldown()
        elif e == error_404:
            return None
        else:
            raise BotException(EXCEPTION, "Failed to get ranked stats.", e=e)


async def get_challenge_result(bot, static, arguments, region):
    """Gets a result of the challenge minigame.

    The minigame consists of pitting two summoners' champions' mastery values
    against each other.
    """

    watcher = static[0]
    summoners = [arguments[0], arguments[1]]
    champions = [arguments[2], arguments[3]]
    games = [0, 0]
    names = ['', '']
    ids = [0, 0]

    for it in range(2):

        # Get summoner data and champion ID
        summoners[it], summoner_region = await get_summoner_wrapper(
            static, summoners[it], region)
        names[it] = summoners[it]['name']
        try:  # In case the champion isn't valid
            champions[it] = static[1][champions[it].replace(' ', '').lower()]
            champions[it] = champions[it]['id']
        except KeyError:
            return "Could not find the champion {}.".format(champions[it])

        # Get ranked stats for total games played on each champion
        ids[it] = summoners[it]['id']
        summoners[it] = await get_ranked_stats_wrapper(
            watcher, ids[it], summoner_region)

        if summoners[it]:
            for champion in summoners[it]['champions']:
                if champion['id'] == champions[it]:
                    games[it] = champion['stats']['totalSessionsPlayed']
        if not games[it] or games[it] == 1:
            games[it] = math.e

        # Get champion mastery data for each champion
        data = await get_mastery_wrapper(
            bot, static, ids[it], summoner_region, champion_id=champions[it])
        if data:
            champions[it] = (data['championPoints'], data['championLevel'])
        else:  # No mastery data on this champion
            champions[it] = (math.e, 1)

    # Do the calculation
    if champions[0][1] and champions[1][1] and games[0] and games[1]:

        # Do calculation stuff
        scores = [0, 0]
        for it in range(2):
            scores[it] = (champions[it][1] *
                          math.log1p(games[it]) *
                          math.log1p(champions[it][0]))
        total = scores[0] + scores[1]
        response = ("Chance of {0} winning: {1:.2f}%\n"
                    "Chance of {2} winning: {3:.2f}%\n").format(
                        names[0], 100 * scores[0] / total,
                        names[1], 100 * scores[1] / total)

        # Calculate winner
        random_value = random.random() * total
        response += 'The RNG gods rolled: {0:.1f}\n'.format(random_value)
        response += 'The winner is **{}**!'.format(
            names[0] if random_value < scores[0] else names[1])

        return response

    else:
        return "Something bad happened. Please report!"


async def get_chests(bot, static, name, region):
    """Gets a list of champions for which a chest has not yet been obtained."""
    # Get mastery data
    summoner, region = await get_summoner_wrapper(static, name, region)
    mastery = await get_mastery_wrapper(
        bot, static, summoner['id'], region, top=False)
    response = ("Here is a list of champions that {} has not received a chest "
                "for:\n").format(summoner['name'])
    champions = []
    for entry in mastery:  # Look for chests that can be obtained
        if not entry['chestGranted']:
            champion_name = static[1][str(entry['championId'])]['name']
            champions.append(champion_name)
    champions.sort()

    if not champions:
        return "This summoner has no mastery data."

    # Format the result
    for it in range(len(champions) % 6):
        champions.append('')  # Fill out the rest with empty strings
    total_length = len(champions)

    response += '```\n'
    for it in range(int(total_length/6)):
        for it2 in range(6):
            response += '{}'.format(champions[6*it + it2]).ljust(14)
        response += '\n'
    response += '```'

    return response


def set_region(bot, static, server_id, region):
    """Sets the server's default region."""
    regions = static[4]
    region = region.lower().replace(' ', '').replace('_', '').replace('-', '')
    response = ''
    if not region:
        region = 'na'
        response = "Reset region to NA (default)."
    elif region not in regions.values():
        if region not in regions:
            raise BotException(
                EXCEPTION, "That is not a defined region. See "
                "{}help lol setregion".format(bot.command_invokers[0]))
        else:
            region = regions[region]
    if not response:
        response = "Region set!"
    data.add(bot, __name__, 'region', region, server_id=server_id)
    return response


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if message.channel.is_private:
        region = 'na'
    else:
        region = data.get(
            bot, __name__, 'region',
            server_id=message.server.id, default='na')
    static = data.get(bot, __name__, 'static_data', volatile=True)
    if static is None:
        raise BotException(
            EXCEPTION, "Discrank is not ready yet, please try again later.")

    if blueprint_index == 0:  # Get basic summoner information
        response = await get_summoner_information(
            bot, static, arguments[0], region, verbose=('extra' in options))

    elif blueprint_index == 1:  # Get match information
        response = await get_match_table_wrapper(
            bot, static, arguments[0], region,
            verbose=('basic' not in options))

    elif blueprint_index == 2:  # Get mastery table
        champion = options['champion'] if 'champion' in options else None
        response = await get_mastery_table(
            bot, static, arguments[0], region, champion=champion)

    elif blueprint_index == 3:  # Chests
        response = await get_chests(bot, static, arguments[0], region)

    elif blueprint_index == 4:  # Challenge
        response = await get_challenge_result(
            bot, static, arguments, region)

    elif blueprint_index == 5:  # Set region
        if message.channel.is_private:
            response = "Can't set region in a direct message, sorry."
        else:
            response = set_region(bot, static, message.server.id, arguments[0])

    elif blueprint_index == 6:  # Get region
        response = "The current region is {}.".format(region.upper())

    return (response, tts, message_type, extra)


def get_static_data(watcher):
    """Get static data returned as a tuple."""
    try:
        champions = watcher.static_get_champion_list(data_by_id=True)['data']
        champions_named = watcher.static_get_champion_list()['data']
        spells = watcher.static_get_summoner_spell_list(
            data_by_id=True)['data']
    except LoLException as e:
        raise BotException(
            EXCEPTION, "Failed to retrieve static data. Your Riot API "
            "token may be invalid or blacklisted - please check to make "
            "sure you copied your key correctly!", e=e,
            error_type=ErrorTypes.STARTUP)
    champions_named = dict(
        (key.lower(), value) for key, value in champions_named.items())
    champions.update(champions_named)
    return (champions, spells)

async def on_ready(bot):
    # Obtain all static data required
    watcher = RiotWatcher(configurations.get(bot, __name__, key='token'))
    if not watcher.can_make_request():
        raise BotException(
            EXCEPTION, "The given Riot API token cannot get requests.",
            error_type=ErrorTypes.STARTUP)

    # Get static data
    champions, spells = await future(get_static_data, watcher)
    modes = {
        "0": "Custom",
        "8": "Normal 3v3",
        "2": "Normal",
        "14": "Normal Draft",
        "4": "Dynamic Queue",
        "6": "Dynamic Queue",
        "9": "Ranked 3v3",
        "41": "Ranked 3v3",
        "42": "Ranked 5v5",
        "16": "This Gamemode doesn't even exist anymore",
        "17": "Same with this one",
        "7": "Co-op vs AI",
        "25": "Co-op vs AI",
        "31": "Co-op vs AI",
        "32": "Co-op vs AI",
        "33": "Co-op vs AI",
        "52": "Co-op vs AI (3v3)",
        "61": "Team Builder",
        "65": "ARAM",
        "70": "One For All",
        "72": "Magma Chamber 1v1",
        "73": "Magma Chamber 2v2",
        "75": "Hexakill",
        "76": "URF",
        "83": "Co-op vs AI (URF)",
        "91": "Doom Bots Lv 1",
        "92": "Doom Bots Lv 2",
        "93": "Doom Bots Lv 3",
        "96": "Ascension",
        "98": "Hexakill",
        "100": "Bilgewater",
        "300": "Legend of the Poro King",
        "313": "Bilgewater ARAM",
        "400": "Team Builder",
        "410": "Dynamic Queue",
        "CUSTOM": "0",
        "NORMAL_3x3": "8",
        "NORMAL_5x5_BLIND": "2",
        "NORMAL_5x5_DRAFT": "14",
        "RANKED_SOLO_5x5": "4",
        "RANKED_PREMADE_5x5*": "6",
        "RANKED_PREMADE_3x3*": "9",
        "RANKED_TEAM_3x3": "41",
        "RANKED_TEAM_5x5": "42",
        "ODIN_5x5_BLIND": "16",
        "ODIN_5x5_DRAFT": "17",
        "BOT_5x5*": "7",
        "BOT_ODIN_5x5": "25",
        "BOT_5x5_INTRO": "31",
        "BOT_5x5_BEGINNER": "32",
        "BOT_5x5_INTERMEDIATE": "33",
        "BOT_TT_3x3": "52",
        "GROUP_FINDER_5x5": "61",
        "ARAM_5x5": "65",
        "ONEFORALL_5x5": "70",
        "FIRSTBLOOD_1x1": "72",
        "FIRSTBLOOD_2x2": "73",
        "SR_6x6": "75",
        "URF_5x5": "76",
        "BOT_URF_5x5": "83",
        "NIGHTMARE_BOT_5x5_RANK1": "91",
        "NIGHTMARE_BOT_5x5_RANK2": "92",
        "NIGHTMARE_BOT_5x5_RANK5": "93",
        "ASCENSION_5x5": "96",
        "HEXAKILL": "98",
        "BILGEWATER_ARAM_5x5": "100",
        "KING_PORO_5x5": "300",
        "COUNTER_PICK": "310",
        "BILGEWATER_5x5": "313",
        "TEAM_BUILDER_DRAFT_UNRANKED_5x5": "400",
        "TEAM_BUILDER_DRAFT_RANKED_5x5": "410"
    }

    regions = {
        'brazil': 'br',
        'europeeast': 'eune',
        'europewest': 'euw',
        'korea': 'kr',
        'latinamericanorth': 'lan',
        'latinamericasouth': 'las',
        'northamerica': 'na',
        'oceania': 'oce',
        'russia': 'ru',
        'turkey': 'tr',
        'japan': 'jp'
    }

    platforms = {  # RiotWatcher doesn't have Japan
        'br': 'BR1',
        'eune': 'EUN1',
        'euw': 'EUW1',
        'kr': 'KR',
        'lan': 'LA1',
        'las': 'LA2',
        'na': 'NA1',
        'oce': 'OC1',
        'ru': 'RU',
        'tr': 'TR1',
        'jp': 'JP1'
    }

    static_data = [watcher, champions, spells, modes, regions, platforms]
    data.add(bot, __name__, 'static_data', static_data, volatile=True)
    print("Discrank is ready!")
