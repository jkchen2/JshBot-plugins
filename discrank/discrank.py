import requests
import time
import random
import math

# Debugging
import logging

from riotwatcher import RiotWatcher, RateLimit
from riotwatcher import LoLException, error_429, error_404

from jshbot import data, configurations, utilities
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import ErrorTypes, BotException
from jshbot.utilities import future

__version__ = '0.1.9'
EXCEPTION = 'Riot API plugin'
uses_configuration = True


def get_commands():
    """Sets up new commands and shortcuts in the proper syntax."""
    commands = []

    commands.append(Command(
        'lol', SubCommands(
            ('summoner ^', 'summoner <summoner name>', 'Gets some basic '
             'information about the given summoner.'),
            ('?ranked match prev: ^', '(ranked) match prev <number> <summoner '
             'name>', 'Shows the given previous match of the given summoner. '
             'You can see a list of previous matches with the `match history` '
             'command.'),
            ('?ranked match ?history ^', '(ranked) match (history) <summoner '
             'name>', 'Gets the current or last match of the given summoner. '
             'If \'history\' is given, this will list the last 10 games that '
             'can be seen with the `match prev` command.'),
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
            ('match', 'match {}', '^', 'match <arguments>', '<arguments>'),
            ('mastery', 'mastery {}', '^', 'mastery <summoner name>',
             '<summoner name>'),
            ('challenge', 'challenge {} {} {} {}', '::::', 'challenge '
             '<"summoner 1"> <"summoner 2"> <"champion 1"> <"champion 2">',
             '<"summoner 1"> <"summoner 2"> <"champion 1"> <"champion 2">'),
            ('blitz', '{}', '^', '<arguments>', '<arguments>')),
        description='Get League of Legends information from the API.',
        other='You can specify the region for a summoner by adding '
              '`:<region>` after the name. For example, try\n`{invoker}lol '
              'summoner hide on bush:kr`', group='game data'))

    return commands


def api_cooldown():
    """Raises the cooldown exception if the API is being used too often."""
    raise BotException(
        EXCEPTION,
        "API is being used too often right now. Please try again later.")


async def _get_summoner(static, name, region, summoner_id=None):
    """Gets the summoner information. Returns None if not found."""
    watcher = static[0]
    regions = static[4]
    name = str(name)
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
            if type(summoner_id) in (list, tuple):
                summoner = await future(
                    watcher.get_summoners, ids=summoner_id, region=region)
            else:
                summoner = await future(
                    watcher.get_summoner, _id=summoner_id, region=region)

        else:
            summoner = await future(
                watcher.get_summoner, name=name, region=region)

        return (summoner, region)

    except Exception as e:
        if e == error_429:
            print("Get summoner is hitting cooldown")
            api_cooldown()
        elif e == error_404:
            if summoner_id is None and name.isdigit():  # Possibly given an ID
                return await _get_summoner(
                    static, name, region, summoner_id=name)
            else:
                raise BotException(
                    EXCEPTION, "Summoner {0} not found in region {1}.".format(
                        name, region.upper()))
        else:
            raise BotException(
                EXCEPTION, "Failed to get summoner information.", e=e)


async def _get_league(static, summoner_ids, region):
    """Gets the league. Returns an empty dictionary if not found."""
    watcher = static[0]
    try:
        return await future(
            watcher.get_league_entry, summoner_ids=summoner_ids, region=region)
    except Exception as e:
        if e == error_429:
            print("Get league is hitting cooldown")
            api_cooldown()
        elif e == error_404:
            return {}
        else:
            raise BotException(
                EXCEPTION, "Failed to get summoner league.", e=e)


def _check_ranked(static, match_data, current):
    """Returns whether or not the given match is ranked."""
    ranked_modes = ["4", "41", "42", "410"]
    if current:
        return match_data['gameQueueConfigId'] in ranked_modes
    else:
        return static[3].get(match_data['subType'], "0") in ranked_modes


async def get_match(
        bot, static, summoner_id, region,
        match_index=None, safe=False, ranked=False):
    """Gets a match.

    The match may or may not be a game, but is formatted like a match to be
    used with _get_formatted_match_table(). If a game_id is given, this will
    look for that game with the given summoner_id. match_index starts from 1.
    """
    if match_index is None:
        match_data = await _get_current_match(static, summoner_id, region)
        match_type = 0
        if ranked and match_data and not _check_ranked(
                static, match_data, True):
            match_data = None
    else:
        try:
            match_index = int(match_index)
        except:
            raise BotException(EXCEPTION, "Invalid match number.")
        match_data = None

    if not match_data:  # Get most recent finished game/match
        if ranked:
            match_type = 2
            games = await _get_recent_matches(static, summoner_id, region)
        else:
            match_type = 1
            games = await _get_recent_games(static, summoner_id, region)
        if games:
            if match_index is None:
                match_data = games[0]
            elif match_index < 1 or match_index > len(games):
                if safe:
                    return None
                else:
                    raise BotException(EXCEPTION, "Invalid match number.")
            else:
                match_data = games[match_index - 1]
        else:
            if safe:
                return None
            else:
                raise BotException(EXCEPTION, "No recent match found.")

    # Use match data and match type to get properly formatted match
    try:
        return await _format_match_data(
            bot, static, summoner_id, region, match_data, match_type)
    except Exception as e:
        if safe:
            return None
        else:
            raise e


async def _format_match_data(
        bot, static, summoner_id, region, match_data, match_type):
    """Returns the match in a nice format

    Match types:
    0 -- current
    1 -- universal (match or game ID)
    2 -- match only (match has separate data)
    """
    match = {}
    blue_team, red_team, blue_players, red_players = {}, {}, [], []
    divisions = {"V": "5", "IV": "4", "III": "3", "II": "2", "I": "1"}
    spectate_url = 'http://{0}.op.gg/match/new/batch/id={1}'
    summoner_name = (await _get_summoner(
        static, summoner_id, region, summoner_id=summoner_id))[0]['name']
    match['invoker_summoner'] = summoner_name

    if match_type == 0:  # Current match
        print("Found a current match.")
        match['spectate'] = spectate_url.format(region, match_data['gameId'])
        match['finished'] = False
        match['game_mode'] = static[3].get(
            str(match_data.get('gameQueueConfigId', 0)), 'Unknown')
        match['timestamp'] = match_data['gameStartTime']
        length = match_data['gameLength'] + 180
        match['game_time'] = '{0}:{1:02d}'.format(
            int(length / 60), length % 60)

        # Get tier and division data
        participants = match_data['participants']
        player_ids = [player['summonerId'] for player in participants]
        player_data = await _get_league(static, player_ids, region)

        for index, player in enumerate(participants):
            players = blue_players if player['teamId'] == 100 else red_players
            spell_ids = [str(player['spell1Id']), str(player['spell2Id'])]
            player_id = player['summonerId']
            if str(player_id) in player_data:
                league = player_data[str(player_id)][0]
                rank = '({0}{1})'.format(
                    league['tier'][0],
                    divisions[league['entries'][0]['division']])
            else:
                rank = ''
            if str(player_id) == str(summoner_id):
                if player['teamId'] == 100:
                    match['invoker_summoner_team'] = 'Blue'
                else:
                    match['invoker_summoner_team'] = 'Red'
                mastery = await _get_mastery_data(
                    bot, static, summoner_id, region,
                    champion_id=player['championId'])
                if mastery:
                    mastery = (
                        '({0[championPoints]}|{0[championLevel]})').format(
                            mastery)
                else:
                    mastery = '(0|0)'
            else:
                mastery = None
            kda = await _get_champion_kda(
                static, player['summonerId'], player['championId'], region)
            players.append({
                'summoner': player['summonerName'],
                'summoner_id': player['summonerId'],
                'spells': [
                    static[2].get(spell, {}).get('name', 'Unknown')
                    for spell in spell_ids],
                'champion': static[1].get(str(player['championId']), {}).get(
                    'name', 'Unknown'),
                'rank': rank,
                'kda': kda,
                'kda_values': [0, 0, 0],
                'mastery': mastery})

        # Get bans
        match['teams'] = {
            'red': {'players': red_players}, 'blue': {'players': blue_players}}
        match['teams']['red']['bans'] = []
        match['teams']['blue']['bans'] = []
        for ban_data in match_data['bannedChampions']:
            team = match['teams'][
                'blue' if ban_data['teamId'] == 100 else 'red']
            team['bans'].append(static[1].get(
                str(ban_data['championId']), {}).get('name', 'Unknown'))

    elif match_type == 1:  # Universal
        match['teams'] = {'red': red_team, 'blue': blue_team}
        match_id = match_data['gameId']
        match_details = await _get_match_data(static, match_id, region)
        match['finished'] = True
        match['game_mode'] = static[3].get(
            static[3].get(match_details['queueType'], '-1'), 'Unknown')
        match['timestamp'] = match_data['createDate']
        stats = match_data['stats']
        length = stats['timePlayed']
        match['game_time'] = '{0}:{1:02d}'.format(
            int(length / 60), length % 60)
        red_team['bans'], blue_team['bans'] = [], []
        for team in match_details['teams']:
            for ban in team.get('bans', []):
                champion_name = static[1].get(
                    str(ban['championId']), {}).get('name', 'Unknown')
                if team['teamId'] == 100:
                    blue_team['bans'].append(champion_name)
                else:
                    red_team['bans'].append(champion_name)
        won = stats['win']
        if stats['team'] == 100:
            match['invoker_summoner_team'] = 'Blue'
            blue_team['winner'], red_team['winner'] = won, not won
        else:
            match['invoker_summoner_team'] = 'Red'
            blue_team['winner'], red_team['winner'] = not won, won

        # Get player data list and champion names
        player_ids = [int(summoner_id)]
        player_names = [summoner_name]
        champion_ids = [match_data['championId']]
        champions = [static[1].get(str(
            match_data['championId']), {}).get('name', 'Unknown')]
        for player in match_data.get('fellowPlayers', []):
            player_ids.append(player['summonerId'])
            champion_ids.append(player['championId'])
            champions.append(static[1].get(
                str(player['championId']), {}).get('name', 'Unknown'))
        player_data = await _get_league(static, player_ids, region)
        player_name_dictionary = (await _get_summoner(
            static, None, region, summoner_id=player_ids))[0]
        for player_id in player_ids[1:]:
            player_names.append(player_name_dictionary[str(player_id)]['name'])

        identities = {}
        for index, champion_id in enumerate(champion_ids):
            player_id = player_ids[index]
            if str(player_id) in player_data:
                league = player_data[str(player_id)][0]
                rank = '({0}{1})'.format(
                    league['tier'][0],
                    divisions[league['entries'][0]['division']])
            else:
                rank = ''
            identities[str(champion_id)] = [
                player_names[index], player_id, champions[index], rank]

        # Pull information from each entry of player_game_data
        for player in match_details['participants']:

            # Get information without using game data
            players = blue_players if player['teamId'] == 100 else red_players
            identity_key = str(player['championId'])
            identity = identities.get(identity_key, [
                '[Bot]', 0,
                static[1].get(identity_key, {}).get('name', 'Unknown'), ''])

            # Get spells and KDA with match details
            spell_ids = [str(player['spell1Id']), str(player['spell2Id'])]
            spells = [static[2].get(spell, {}).get('name', 'Unknown')
                      for spell in spell_ids]
            stats = player['stats']
            kills, deaths, assists = (
                stats['kills'], stats['deaths'], stats['assists'])
            value = (kills + assists) / (1 if deaths == 0 else deaths)
            kda_values = [kills, deaths, assists]
            kda = '{0}/{1}/{2} ({3:.2f})'.format(kills, deaths, assists, value)

            # Get mastery if this is the target player
            if identity[0] == match['invoker_summoner']:
                mastery = await _get_mastery_data(
                    bot, static, summoner_id, region,
                    champion_id=player['championId'])
                if mastery:
                    mastery = (
                        '({0[championPoints]}|{0[championLevel]})').format(
                            mastery)
                else:
                    mastery = '(0|0)'
            else:
                mastery = None

            players.append({
                'summoner': identity[0],
                'summoner_id': identity[1],
                'spells': spells,
                'champion': identity[2],
                'rank': identity[3],
                'kda': kda,
                'kda_values': kda_values,
                'mastery': mastery})

        red_team['players'] = red_players
        blue_team['players'] = blue_players

    elif match_type == 2:  # Match only
        match['teams'] = {'red': red_team, 'blue': blue_team}
        match_details = await _get_match_data(
            static, match_data['matchId'], region)
        match['finished'] = True
        match['game_mode'] = static[3].get(
            static[3].get(match_details['queueType'], '-1'), 'Unknown')
        match['timestamp'] = match_data['timestamp']
        length = match_details['matchDuration']
        match['game_time'] = '{0}:{1:02d}'.format(
            int(length / 60), length % 60)
        blue_won = match_details['teams'][0]['winner']
        blue_team['winner'], red_team['winner'] = blue_won, not blue_won
        red_team['bans'], blue_team['bans'] = [], []
        for team in match_details['teams']:
            for ban in team.get('bans', []):
                champion_name = static[1].get(
                    str(ban['championId']), {}).get('name', 'Unknown')
                if team['teamId'] == 100:
                    blue_team['bans'].append(champion_name)
                else:
                    red_team['bans'].append(champion_name)

        # Get player data list and ranks
        players = [player for player in match_details['participantIdentities']]
        player_ids = [player['player']['summonerId'] for player in players]
        player_names = [player['player']['summonerName'] for player in players]
        player_data = await _get_league(static, player_ids, region)
        player_ranks = []
        for player_id in player_ids:
            if str(player_id) in player_data:
                league = player_data[str(player_id)][0]
                rank = '({0}{1})'.format(
                    league['tier'][0],
                    divisions[league['entries'][0]['division']])
            else:
                rank = ''
            player_ranks.append(rank)

        # Pull information from each entry of player_game_data
        for index, player in enumerate(match_details['participants']):

            # Get spells and KDA with match details
            spell_ids = [str(player['spell1Id']), str(player['spell2Id'])]
            spells = [static[2].get(spell, {}).get('name', 'Unknown')
                      for spell in spell_ids]
            stats = player['stats']
            kills, deaths, assists = (
                stats['kills'], stats['deaths'], stats['assists'])
            value = (kills + assists) / (1 if deaths == 0 else deaths)
            kda_values = [kills, deaths, assists]
            kda = '{0}/{1}/{2} ({3:.2f})'.format(kills, deaths, assists, value)

            # Get mastery if this is the target player
            if player_names[index] == match['invoker_summoner']:
                team_name = 'Blue' if player['teamId'] == 100 else 'Red'
                match['invoker_summoner_team'] = team_name
                mastery = await _get_mastery_data(
                    bot, static, summoner_id, region,
                    champion_id=player['championId'])
                if mastery:
                    mastery = (
                        '({0[championPoints]}|{0[championLevel]})').format(
                            mastery)
                else:
                    mastery = '(0|0)'
            else:
                mastery = None

            players = blue_players if player['teamId'] == 100 else red_players
            players.append({
                'summoner': player_names[index],
                'summoner_id': player_ids[index],
                'spells': spells,
                'champion': static[1].get(
                    str(player['championId']), {}).get('name', 'Unknown'),
                'rank': player_ranks[index],
                'kda': kda,
                'kda_values': kda_values,
                'mastery': mastery})

        red_team['players'] = red_players
        blue_team['players'] = blue_players

    return match


async def _get_recent_matches(static, summoner_id, region):
    """Gets the match list. Returns empty list if no matches are found."""
    watcher = static[0]
    try:
        match_list = await future(
            watcher.get_match_list, summoner_id, region=region)
        if 'matches' not in match_list:
            logging.warn("Summoner has no matches.")
            return []
        return match_list['matches']
    except Exception as e:
        if e == error_429:
            print("Recent matches is hitting cooldown")
            api_cooldown()
        elif e == error_404:
            logging.warn("Summoner has no match list.")
            return []
        else:
            raise BotException(EXCEPTION, "Failed to get the match list.", e=e)


async def _get_recent_games(static, summoner_id, region):
    watcher = static[0]
    try:
        game_list = await future(
            watcher.get_recent_games, summoner_id, region=region)
        if 'games' not in game_list:
            logging.warn("Summoner has no games.")
            return []
        return game_list['games']
    except Exception as e:
        if e == error_429:
            print("Recent games is hitting cooldown")
            api_cooldown()
        elif e == error_404:
            logging.warn("Summoner has no games.")
            return []
        else:
            raise BotException(EXCEPTION, "Failed to get the game list.", e=e)


async def _get_match_data(static, match_id, region):
    """Gets the match given match_id. Includes exception handling."""
    watcher = static[0]
    try:
        return await future(watcher.get_match, match_id, region=region)
    except Exception as e:
        if e == error_429:
            print("Match data is hitting cooldown")
            api_cooldown()
        elif e == error_404:
            raise BotException(EXCEPTION, "Match does not exist.")
        else:
            raise BotException(
                EXCEPTION, "Failed to get match information.", e=e)


async def _get_current_match(static, summoner_id, region):
    """Returns the current match if there is one, otherwise returns None."""
    watcher = static[0]
    platform = static[5][region]
    try:
        return await future(
            watcher.get_current_game, summoner_id,
            platform_id=platform, region=region)
    except Exception as e:
        if e == error_429:
            print("Current match is hitting cooldown")
            api_cooldown()
        elif e == error_404:
            return None
        else:
            raise BotException(
                EXCEPTION, "Failed to get the current match.", e=e)


async def _get_mastery_data(
        bot, static, summoner_id, region, top=True, champion_id=None):
    """Gets the player's champion mastery, otherwise returns None.

    Keyword arguments:
    top -- gets the champions organized by most mastery points
    champion_id -- if specified, gets the given champion's mastery
    """
    platform = static[5][region]
    api_key = configurations.get(bot, __name__, key='token')
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
            print("Mastery is hitting cooldown")
            api_cooldown()
        else:
            logging.error("This is the requests result: " + str(result))
            raise BotException(
                EXCEPTION, "Failed to retrieve mastery data.", error_code)
    else:
        return result


def _get_top_champions(static, mastery):
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


def _get_mastery_details(static, mastery):
    """Returns a string of details for the given mastery."""
    champion_id = str(mastery['championId'])
    champion_name = static[1][champion_id]['name']
    return ('{0}:\n'
            '\tPoints: {1[championPoints]}\n'
            '\tLevel: {1[championLevel]}\n'
            '\tHighest Grade: {1[highestGrade]}\n').format(
                champion_name, mastery)


async def _get_champion_kda(static, summoner_id, champion_id, region):
    """Gets a nicely formatted string of the summoner's champion KDA."""
    watcher = static[0]
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


def _get_kill_participation(team_players, player):
    """Gets a string of the kill participation of the summoner."""
    kills, deaths, assists = player['kda_values']
    participant_kills = kills + assists
    total_kills = 0
    for player in team_players:
        total_kills += player['kda_values'][0]
    total_kills = 1 if total_kills <= 0 else total_kills
    return '{0:.1f}%'.format(100*participant_kills/total_kills)


def _get_formatted_match_table(match, verbose=False):
    """Returns a scoreboard view of the given match."""
    response = ''

    # Very detailed table
    if verbose:
        response = '```diff\n'  # Use + and - to highlight

        # Add current game time and game type
        response += "{0} Game Time: {1}\n".format(
                'Finished' if match['finished'] else 'Current',
                match['game_time'])
        response += 'Game Type: {}\n\n'.format(match['game_mode'])
        team_responses = []  # Built up for each team
        use_full_width = False
        table_template = (
            '{{0:{}<16}} {{1: >4}} | {{2: <13}}| {{3: <22}}| '
            '{{4: <9}}| {{5: <9}}|\n')
        guide_template = (
            '  {} Rank | Champion     | '
            'KDA                   | Spell 1  | Spell 2  |\n'
            ' -{}------|--------------|-'
            '----------------------|----------|----------|\n')

        # Loop through each team
        for team_key, team in match['teams'].items():
            team_response = []  # Current team response

            # Team
            winner, finished = team.get('winner', ''), match['finished']
            team_bans = ' -- Bans [{}]'.format(
                ', '.join(team['bans'])) if team['bans'] else ''
            team_win = ' [{}]'.format(
                'WON' if winner else 'LOST') if finished else ''
            team_response.append('{0} Team{1}{2}\n'.format(
                team_key.capitalize(), team_bans, team_win))

            # Loop through each participant on the team
            for player in team['players']:

                # Check for full width
                try:
                    player['summoner'].encode('ascii')
                except UnicodeEncodeError:  # Non-ascii detected
                    use_full_width = True

                # Highlight summoner if this is the one we're looking for
                is_target = player['summoner'] == match['invoker_summoner']
                spells = player['spells']
                keys = ('summoner', 'rank', 'champion', 'kda')

                # Add champion name, kda, and spells
                team_response.append([
                    is_target, *(player[key] for key in keys), *spells])

            team_responses.append(team_response)

        # Append to response
        if use_full_width:
            space, hyphen = ('　', '－')
            guide_text = 'Ｓｕｍｍｏｎｅｒ'
        else:
            space, hyphen = (' ', '-')
            guide_text = 'Summoner'

        guide_template = guide_template.format(guide_text + space*8, hyphen*16)
        table_template = table_template.format(space)

        for team_response in team_responses:
            response += team_response[0] + guide_template
            for player_data in team_response[1:]:
                response += '+ ' if player_data[0] else '  '  # highlight

                if use_full_width:  # Convert to ideographic width
                    new_name = ''
                    for character in player_data[1]:
                        if character.isalnum() and ord(character) < 128:
                            new_name += chr(ord(character) + 65248)
                        elif character == ' ':
                            new_name += space
                        else:
                            new_name += character
                    player_data[1] = new_name

                response += table_template.format(*player_data[1:])
            response += '\n'

        response += '\n```\n'
        if not match['finished']:
            response += 'Spectate: <{}>'.format(match['spectate'])

    # Simple 3-4 line game info
    else:

        # Get team and summoner
        team = match['teams'][
            match['invoker_summoner_team'].lower()]['players']
        try:
            player = [
                player for player in team
                if player['summoner'] == match['invoker_summoner']][0]
        except:
            return 'Failed to retrieve game information.'

        # Get extra information
        if match['finished']:
            kill_participation = ' - Kill Participation {}'.format(
                _get_kill_participation(team, player))
            player_team = match['invoker_summoner_team'].lower()
            won_status = match['teams'][player_team]['winner']
            extra_information = (
                'Status: {}').format('Won' if won_status else 'Lost')
        else:
            kill_participation = ''
            extra_information = (
                'Side: {0[invoker_summoner_team]}\n'
                'Time: {0[game_time]}').format(match)

        response += (
            "**Game Type:** {0[game_mode]}\n"
            "{1[champion]} - {1[kda]} {1[mastery]}{2} - {3} - {4}\n"
            "{5}").format(match, player, kill_participation,
                          *player['spells'], extra_information)

    return response


async def get_match_table_wrapper(
        bot, static, name, region,
        match_index=None, verbose=False, ranked=False):
    """Gets the match table. Makes the calling method easier to look at."""
    summoner, region = await _get_summoner(static, name, region)

    # Get last match or current match information
    match = await get_match(
        bot, static, summoner['id'], region,
        match_index=match_index, ranked=ranked)
    return _get_formatted_match_table(match, verbose=verbose)


async def get_match_history_wrapper(bot, static, name, region, ranked=False):
    """Gets the match history of the given summoner."""
    summoner, region = await _get_summoner(static, name, region)
    if ranked:
        games = await _get_recent_matches(static, summoner['id'], region)
        games = games[:20]  # Limit to 20 games

        # Retrieve data for 20 games
        match_ids = [str(game['matchId']) for game in games]
        coroutines = [
            _get_match_data(static, match_id, region)
            for match_id in match_ids]
        match_results = await utilities.parallelize(
            coroutines, return_exceptions=True)
    else:
        games = await _get_recent_games(static, summoner['id'], region)
    if not games:
        raise BotException(
            EXCEPTION, "No recent {}games found.".format(
                'ranked ' if ranked else ''))

    guide_template = (
        '#  | Game Type               | Champion      | '
        'KDA              | Status |\n'
        '---|-------------------------|---------------|-'
        '-----------------|--------|\n')
    formatted_games = []
    for index, game in enumerate(games):
        if ranked:
            game_type = static[3].get(
                static[3].get(game['queue'], '-1'), 'Unknown')
            champion = static[1].get(
                str(game['champion']), {}).get('name', 'Unknown')
            current = match_results[index]
            if isinstance(current, Exception):
                kills, deaths, assists, status = 0, 0, 0, 'n/a'
            else:
                participants = current['participants']
                for player in current['participantIdentities']:
                    if player['player']['summonerId'] == summoner['id']:
                        participant_index = player['participantId'] - 1
                        team_id = participants[participant_index]['teamId']
                        break
                stats = participants[participant_index]['stats']
                kills, deaths, assists = (
                    stats['kills'], stats['deaths'], stats['assists'])
                if (team_id == 100) != current['teams'][0]['winner']:
                    status = 'Lost'
                else:
                    status = 'Won'
        else:
            game_type = static[3].get(
                static[3].get(game['subType'], '-1'), 'Unknown')
            champion = static[1].get(
                str(game['championId']), {}).get('name', 'Unknown')
            stats = game['stats']
            kills, deaths, assists = (
                stats.get('championsKilled', 0),
                stats.get('numDeaths', 0), stats.get('assists', 0))
            status = 'Won' if stats['win'] else 'Lost'

        value = (kills + assists) / (1 if deaths == 0 else deaths)
        kda = '{0}/{1}/{2} ({3:.2f})'.format(kills, deaths, assists, value)
        formatted_games.append((
            '{0: <3}| {1: <24}| {2: <14}| {3: <17}| {4: <7}|').format(
                index + 1, game_type, champion, kda, status))

    return '```\n{0}{1}```'.format(guide_template, '\n'.join(formatted_games))


async def get_summoner_information(bot, static, name, region, verbose=False):
    """Get a nicely formatted string of summoner data."""
    summoner, region = await _get_summoner(static, name, region)
    mastery = await _get_mastery_data(
        bot, static, summoner['id'], region, top=False)
    response = ("***`{0[name]}`***\n"
                "**Summoner ID:** {0[id]}\n"
                "**Level:** {0[summonerLevel]}\n"
                "**Top Champions:** {1}\n\n").format(
                    summoner, _get_top_champions(static, mastery))

    # Get league information
    summoner_id = str(summoner['id'])
    league = await _get_league(static, [summoner_id], region)
    if league:
        league = league[summoner_id][0]

        # Extra champion mastery data if we want extra information
        if verbose:
            mastery_details = []
            for it in range(3):
                mastery_details.append(
                    _get_mastery_details(static, mastery[it]))
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
        response += "This summoner has not played ranked yet this season.\n\n"

    # Get last match or current match information
    match = await get_match(bot, static, summoner_id, region, safe=True)
    if match:
        response += "***`{} Match`***\n".format(
                'Last' if match['finished'] else 'Current')
        response += _get_formatted_match_table(match)
    else:
        response += (
            "A most recent match was not found or could not be obtained.")

    return response


def get_formatted_mastery_data(static, champion_data):
    """Gets a nicely formatted line of mastery data."""
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
    summoner, region = await _get_summoner(static, name, region)
    if champion:
        try:
            champion_id = static[1][champion.replace(' ', '').lower()]['id']
            champion_data = await _get_mastery_data(
                bot, static, summoner['id'], region, champion_id=champion_id)
        except KeyError:
            raise BotException(EXCEPTION, "Champion not found.")
        if champion_data is None:
            raise BotException(
                EXCEPTION, "This summoner has no mastery data for the given "
                "champion.")
    else:
        champion_data = await _get_mastery_data(
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
            print("Get ranked stats is hitting cooldown")
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
        summoners[it], summoner_region = await _get_summoner(
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
        data = await _get_mastery_data(
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
    summoner, region = await _get_summoner(static, name, region)
    mastery = await _get_mastery_data(
        bot, static, summoner['id'], region, top=False)
    response = ("Here is a list of champions that {} has not received a chest "
                "for:\n").format(summoner['name'])
    champions = []
    for entry in mastery:  # Look for chests that can be obtained
        if not entry['chestGranted']:
            champion_name = static[1].get(
                str(entry['championId']), {}).get('name', 'Unknown')
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

    elif blueprint_index == 1:  # Show old match
        ranked = 'ranked' in options
        response = await get_match_table_wrapper(
            bot, static, arguments[0], region,
            match_index=options['prev'], verbose=True, ranked=ranked)

    elif blueprint_index == 2:  # Get match information
        ranked = 'ranked' in options
        if 'history' in options:
            response = await get_match_history_wrapper(
                bot, static, arguments[0], region, ranked=ranked)
        else:
            response = await get_match_table_wrapper(
                bot, static, arguments[0], region, verbose=True, ranked=ranked)

    elif blueprint_index == 3:  # Get mastery table
        champion = options['champion'] if 'champion' in options else None
        response = await get_mastery_table(
            bot, static, arguments[0], region, champion=champion)

    elif blueprint_index == 4:  # Chests
        response = await get_chests(bot, static, arguments[0], region)

    elif blueprint_index == 5:  # Challenge
        response = await get_challenge_result(
            bot, static, arguments, region)

    elif blueprint_index == 6:  # Set region
        if message.channel.is_private:
            response = "Can't set region in a direct message, sorry."
        else:
            response = set_region(bot, static, message.server.id, arguments[0])

    elif blueprint_index == 7:  # Get region
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

async def bot_on_ready_boot(bot):
    # Obtain all static data required
    if configurations.get(bot, __name__, 'production_key'):
        limits = (RateLimit(3000, 10), RateLimit(180000, 600))
    else:
        limits = (RateLimit(10, 10), RateLimit(500, 600))
    watcher = RiotWatcher(
        configurations.get(bot, __name__, key='token'), limits=limits)
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
        "9": "Ranked Flex 3v3",
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
        "61": "Dynamic Queue",
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
        "315": "Nexus Siege",
        "317": "Definitely Not Dominion",
        "400": "Normal (Draft)",
        "410": "Dynamic Queue",
        "440": "Ranked Flex 5v5",
        "CUSTOM": "0",
        "NONE": "0",
        "NORMAL": "2",
        "NORMAL_3x3": "8",
        "NORMAL_5x5_BLIND": "2",
        "NORMAL_5x5_DRAFT": "14",
        "RANKED_PREMADE_5x5*": "6",
        "RANKED_PREMADE_3x3*": "9",
        "RANKED_TEAM_3x3": "41",
        "RANKED_TEAM_5x5": "42",
        "ODIN_UNRANKED": "16",
        "ODIN_5x5_BLIND": "16",
        "ODIN_5x5_DRAFT": "17",
        "BOT_5x5*": "7",
        "BOT_ODIN_5x5": "25",
        "BOT": "31",
        "BOT_5x5_INTRO": "31",
        "BOT_5x5_BEGINNER": "32",
        "BOT_5x5_INTERMEDIATE": "33",
        "BOT_3x3": "52",
        "BOT_TT_3x3": "52",
        "GROUP_FINDER_5x5": "61",
        "ARAM_UNRANKED_5x5": "65",
        "ARAM_5x5": "65",
        "ONEFORALL_5x5": "70",
        "FIRSTBLOOD_1x1": "72",
        "FIRSTBLOOD_2x2": "73",
        "SR_6x6": "75",
        "URF": "76",
        "URF_5x5": "76",
        "URF_BOT": "83",
        "BOT_URF_5x5": "83",
        "NIGHTMARE_BOT": "91",
        "NIGHTMARE_BOT_5x5_RANK1": "91",
        "NIGHTMARE_BOT_5x5_RANK2": "92",
        "NIGHTMARE_BOT_5x5_RANK5": "93",
        "ASCENSION": "96",
        "ASCENSION_5x5": "96",
        "HEXAKILL": "98",
        "BILGEWATER_ARAM_5x5": "100",
        "KING_PORO": "300",
        "KING_PORO_5x5": "300",
        "COUNTER_PICK": "310",
        "BILGEWATER": "313",
        "BILGEWATER_5x5": "313",
        "SIEGE": "315",
        "DEFINITELY_NOT_DOMINION_5x5": "317",
        "TEAM_BUILDER_DRAFT_UNRANKED_5x5": "400",
        "CAP_5x5": "400",
        "TEAM_BUILDER_DRAFT_RANKED_5x5": "410",
        "RANKED_SOLO_5x5": "410",
        "RANKED_FLEX_TT": "9",
        "RANKED_FLEX_SR": "440"
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

    platforms = {  # RiotWatcher doesn't believe in Japan
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
    print("discrank.py is ready!")
