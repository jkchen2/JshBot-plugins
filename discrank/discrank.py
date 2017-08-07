import requests
import asyncio
import datetime
import time
import random
import math
import urllib
import json
import io

import discord

from enum import Enum

from requests import HTTPError
from psycopg2.extras import Json
from collections import namedtuple

# from riotwatchermod import RiotWatcher, RateLimit
# from riotwatchermod import HTTPError, error_429, error_404, error_403
#from riotwatchermod import RiotWatcher
from riotwatcher import RiotWatcher

from jshbot.utilities import future
from jshbot import data, utilities, configurations, logger, plugins, parser
from jshbot.exceptions import BotException, ConfiguredBotException, ErrorTypes
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
CBException = ConfiguredBotException('Riot API plugin')
uses_configuration = True


class MatchTypes(Enum):
    NORMAL, RANKED, CURRENT = range(3)


def handle_lol_exception(e):
    response_code = e.response.status_code
    if response_code == 429:
        raise CBException("API is being used too often right now. Please try again later.")
    elif e.response.status_code == 403:
        raise CBException("The API key is blacklisted!")
    else:
        raise CBException("An API error occurred: {}".format(e))


class SummonerConverter():
    async def __call__(self, bot, message, value, *a):
        if not value:
            raise CBException("Summoner name must not be blank.")
        region = data.get(bot, __name__, 'region', guild_id=message.guild.id, default='na')
        return await _get_summoner(bot, value, region)


class ChampionConverter():
    def __call__(self, bot, message, value, *a):
        cleaned = value.replace(' ', '').replace('_', '').replace('-', '').lower()
        try:
            return CHAMPIONS[cleaned]
        except:
            raise CBException("Champion `{}` not found.".format(cleaned))


Summoner = namedtuple(
    'Summoner',
    [
        'account_id', 'summoner_id', 'summoner_name', 'search_name', 'region',
        'level', 'revision_date', 'icon', 'last_updated', 'tier', 'shorthand_tier',
        'missing_data', 'wins', 'losses', 'rank', 'lp', 'inactive', 'top_champions',
        'other_positions', 'total_games'
    ]
)


@plugins.command_spawner
def get_commands(bot):
    """Sets up new commands and shortcuts in the proper syntax."""
    new_commands = []

    new_commands.append(Command(
        'lol', subcommands=[
            SubCommand(
                Opt('summoner'),
                Arg('name', argtype=ArgTypes.MERGED, convert=SummonerConverter()),
                doc='Gets some basic information about the given summoner.',
                function=format_summoner),
            SubCommand(
                Opt('ranked', optional=True),
                Opt('match'),
                Opt('history'),
                Arg('summoner name', convert=SummonerConverter(), argtype=ArgTypes.MERGED),
                doc='Shows a list of the last 10 matches of the given summoner.',
                function=format_matchlist),
            SubCommand(
                Opt('ranked', optional=True),
                Opt('match'),
                Opt('prev', attached='number', convert=int, optional=True,
                    quotes_recommended=False, check=lambda b, m, v, *a: v >= 1,
                    check_error="Must be 1 or greater."),
                Arg('summoner name', convert=SummonerConverter(), argtype=ArgTypes.MERGED),
                doc='Shows the given previous match of the given summoner. '
                    'You can see a list of previous matches with the `match history` '
                    'command.',
                function=format_match),
            SubCommand(
                Opt('challenge'),
                Arg('summoner 1', convert=SummonerConverter()),
                Arg('summoner 2', convert=SummonerConverter()),
                Arg('champion 1', convert=ChampionConverter()),
                Arg('champion 2', convert=ChampionConverter()),
                doc='Compares two summoners\' mastery points, mastery levels, and number '
                    'of games played (ranked) data against each other.',
                function=challenge),
            SubCommand(
                Opt('setregion'),
                Arg('region', quotes_recommended=False, argtype=ArgTypes.MERGED_OPTIONAL,
                    doc='Valid regions are `NA` (default), `BR`, `EUNE`, `EUW`, `JP`, '
                        '`KR`, `LAN`, `LAS`, `OCE`, `RU`, and `TR`.'),
                doc='Sets the default region for the server.',
                function=set_region)],
        shortcuts=[
            Shortcut('summoner', 'summoner {name}', Arg('name', argtype=ArgTypes.MERGED)),
            Shortcut('match', 'match {name}', Arg('name', argtype=ArgTypes.MERGED)),
            Shortcut('mastery', 'mastery {arguments}', Arg('arguments', argtype=ArgTypes.MERGED))],
        description='Get League of Legends information from the API.',
        other='You can specify the region for a summoner by adding '
              '`:region` after the name. For example, try\n`{invoker}lol '
              'summoner hide on bush:kr`',
        category='game data'))

    return new_commands

'''
SubCommand(
    Opt('mastery'),
    Opt('champion', attached='champion name',
        optional=True, convert=ChampionConverter(),
        doc='Get information for this champion only.'),
    Arg('summoner name', argtype=ArgTypes.MERGED, convert=SummonerConverter()),
    doc='Gets a list of the top 10 champions of the summer based off of mastery.'),
SubCommand(
    Opt('chests'),
    Arg('summoner name', argtype=ArgTypes.MERGED, convert=SummonerConverter()),
    doc='Shows the unobtained chests for the given summoner.'),
'''


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'lol_summoner_template': (
            "account_id         bigint PRIMARY KEY,"
            "summoner_id        bigint,"
            "search_name        text,"
            "region             lol_region,"
            "data               json,"
            "last_updated       bigint"),

        'lol_match_template': (
            "match_id           bigint PRIMARY KEY,"
            "region             lol_region,"
            "data               json,"
            "last_accessed      bigint"),

        'lol_raw_match_template': (
            "match_id           bigint,"
            "account_id         bigint,"
            "ranked             bool,"
            "region             lol_region,"
            "data               json,"
            "last_accessed      bigint")
    }


@plugins.on_load
def create_lol_cache(bot):
    if not data.db_exists(bot, 'lol_region', check_type=True):  # Create time index
        data.db_execute(
            bot, 'CREATE TYPE lol_region AS ENUM (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            input_args=['br', 'eune', 'euw', 'kr', 'lan', 'las', 'na', 'oce', 'ru', 'tr', 'jp'])
    # If necessary, alter to add new regions
    #data.db_execute(
    #    bot, 'ALTER TYPE lol_region ADD VALUE IF NOT EXISTS %s', input_args=['pbe'])

    data.db_create_table(bot, 'lol_summoner_cache', template='lol_summoner_template')
    data.db_dump_exclude(bot, 'lol_summoner_cache')
    summoner_index = 'IX_lol_summoner_cache_order'
    if not data.db_exists(bot, summoner_index):
        data.db_execute(
            bot, 'CREATE INDEX {} ON lol_summoner_cache (last_updated ASC)'.format(summoner_index))

    data.db_create_table(bot, 'lol_match_cache', template='lol_match_template')
    data.db_dump_exclude(bot, 'lol_match_cache')
    match_index = 'IX_lol_match_cache_order'
    if not data.db_exists(bot, match_index):
        data.db_execute(
            bot, 'CREATE INDEX {} ON lol_match_cache (last_accessed ASC)'.format(match_index))

    data.db_create_table(bot, 'lol_raw_match_cache', template='lol_raw_match_template')
    data.db_dump_exclude(bot, 'lol_raw_match_cache')
    match_index = 'IX_lol_match_cache_order'
    if not data.db_exists(bot, match_index):
        data.db_execute(
            bot, 'CREATE INDEX {} ON lol_raw_match_cache (last_accessed ASC)'.format(match_index))


async def format_summoner(bot, context):
    current_time = int(time.time())
    summoner = context.arguments[0]
    if current_time - summoner.last_updated <= 60:
        message_type = MessageTypes.NORMAL
    else:
        message_type = MessageTypes.INTERACTIVE

    response = Response(
        message_type=message_type,
        extra_function=_show_summoner_information,
        extra={'buttons': ['🔄']})
    response.embed = await _build_summoner_embed(bot, summoner)
    response.summoner = summoner
    return response


async def _build_summoner_embed(bot, summoner):
    timestamp = datetime.datetime.utcfromtimestamp(summoner.last_updated)
    embed = discord.Embed(timestamp=timestamp, colour=RANK_COLORS[summoner.tier])
    opgg_link = 'https://{}.op.gg/summoner/userName={}'.format(
        summoner.region, urllib.parse.quote_plus(summoner.summoner_name))
    profile_icon = 'https://ddragon.leagueoflegends.com/cdn/7.15.1/img/profileicon/{}.png'.format(
        summoner.icon)
    embed.set_author(name=summoner.summoner_name, url=opgg_link, icon_url=profile_icon)
    embed.set_thumbnail(url=RANK_ICONS[summoner.tier])

    if 'league' in summoner.missing_data:
        ranked_info_display = 'Unranked\nLevel: {}'.format(summoner.level)
    else:
        template = '{0.tier} {0.rank}\nLP: {0.lp}\nW/L: {0.wins}/{0.losses} ({1:.01f}%)'
        wins, losses = summoner.wins, summoner.losses
        ranked_info_display = template.format(summoner, (wins / max(wins + losses, 1)) * 100)
    embed.add_field(name="Solo Queue", value=ranked_info_display)

    template = '{0[tier]} {0[rank]}\nLP: {0[lp]}\nW/L: {0[wins]}/{0[losses]} ({1:.01f}%)'
    for index, position_name in enumerate(('Flex 5v5', 'Flex 3v3')):
        position = summoner.other_positions[index]
        if position:
            wins, losses = position['wins'], position['losses']
            ranked_info_display = template.format(position, (wins / max(wins + losses, 1)) * 100)
        else:
            ranked_info_display = 'Unranked'
        embed.add_field(name=position_name, value=ranked_info_display)

    if 'mastery' not in summoner.missing_data:
        embed.add_field(name="Top champions", value=summoner.top_champions)

    newest_match = await _get_newest_match(bot, summoner, safe=True)
    if newest_match:
        if newest_match['finished']:
            status = 'Last'
            quickstatus = newest_match['quickstatus'][str(summoner.account_id)]
            team = newest_match['teams'][quickstatus[0]]
            player = team['players'][quickstatus[1]]
            line = '{} | {}{}{} | KDA: {}'.format(
                'Won' if team['winner'] else 'Lost',
                CHAMPION_EMOJIS.get(player['champion'], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][0], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][1], UNKNOWN_EMOJI),
                player['kda']
            )
        else:
            status = 'Current'
            quickstatus = newest_match['quickstatus'][str(summoner.summoner_id)]
            player = newest_match['teams'][quickstatus[0]]['players'][quickstatus[1]]
            line = '{} | {}{}{} | [Time: {}]({} "op.gg spectate batch file")'.format(
                ':large_blue_circle:' if quickstatus[0] == 'blue' else ':red_circle:',
                CHAMPION_EMOJIS.get(player['champion'], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][0], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][1], UNKNOWN_EMOJI),
                newest_match['game_time'],
                newest_match['spectate']
            )
        game_mode = MODES.get(newest_match['game_mode'], 'Unknown')
        embed.add_field(name='{} match [{}]'.format(status, game_mode), value=line)

    # Add cleverly disguised padding field
    embed.add_field(
        name='{} total ranked game{}'.format(
            summoner.total_games, '' if summoner.total_games == 1 else 's'),
        value='\u200b' + '　'*35 + '\u200b', inline=False)

    embed.set_footer(text="{} | ID {} | AID {} | Updated".format(  # TODO: Remove AID
        summoner.region.upper(), summoner.summoner_id, summoner.account_id))
    return embed


async def _show_summoner_information(bot, context, response, result, timed_out):
    if timed_out or not result:
        return
    await response.message.edit(content='Refreshing information...')
    # Get summoner information and update display
    test_name = response.summoner.summoner_name
    test_region = response.summoner.region
    try:
        summoner = await _get_summoner(bot, test_name, test_region, force_update=True)
    except Exception as e:
        raise CBException("Failed to update summoner information.", e=e)
    embed = await _build_summoner_embed(bot, summoner)
    await response.message.edit(content= '', embed=embed)
    return False


async def _get_summoner(bot, name, region, force_update=False):
    """Gets the cached summoner information. Returns None if not found."""
    if ':' in name:  # Custom region
        name, region = name.rsplit(':', 1)
        region = region.lower()
        if region in REGIONS:
            region = REGIONS[region]
        elif region not in REGIONS.values():
            raise CBException(
                "That is not a defined region. Available regions:",
                ', '.join(r.upper() for r in REGIONS.values()))

    search_name = utilities.clean_text(name)
    logger.debug("Looking for name: %s and region: %s", search_name, region)
    result = data.db_select(
        bot, from_arg='lol_summoner_cache', where_arg='search_name=%s AND region=%s',
        input_args=[search_name, region]).fetchone()
    # logger.debug("This is the cache result: %s", result)
    if not result or (time.time() - result.last_updated > 24*60*60) or force_update:
        logger.debug("Summoner NOT found in cache (or expired).")
        # Get summoner information and update
        # call summoner-v3 (get summoner ID)
        try:
            summoner_info = await future(WATCHER.summoner.by_name, PLATFORMS[region], name)
        except HTTPError as e:
            if e.response.status_code == 404:
                raise CBException(
                    "Summoner `{}` not found in region {}.".format(name, region.upper()))
            handle_lol_exception(e)
        account_id = summoner_info['accountId']
        summoner_id = summoner_info['id']
        summoner_name = summoner_info['name']
        search_name = utilities.clean_text(summoner_name)
        current_time = int(time.time())
        all_data = [
            account_id,
            summoner_id,
            search_name,
            region
        ]
        json_data = {
            'account_id': account_id,
            'summoner_id': summoner_id,
            'summoner_name': summoner_name,
            'search_name': search_name,
            'region': region,
            'level': summoner_info['summonerLevel'],
            'revision_date': summoner_info['revisionDate'],
            'icon': summoner_info['profileIconId'],
            'last_updated': current_time,
            'tier': 'Unranked',
            'shorthand_tier': 'U',
            'total_games': 0,
            'wins': None,
            'losses': None,
            'rank': None,
            'lp': None,
            'inactive': None,
            'top_champions': None,
            'other_positions': [{}, {}],  # Flex 5:5, Flex 3:3
            'missing_data': []
        }

        # call league-v3 (use summoner ID)
        league_future = future(
            WATCHER.league.positions_by_summoner, PLATFORMS[region], summoner_id)
        mastery_future = future(
            WATCHER.champion_mastery.by_summoner, PLATFORMS[region], summoner_id)
        try:
            info = await utilities.parallelize([league_future, mastery_future])
        except HTTPError as e:
            handle_lol_exception(e)

        # Parse league data:
        for league in info[0]:
            if league['queueType'] == 'RANKED_SOLO_5x5':
                to_update = json_data
            elif league['queueType'] == 'RANKED_FLEX_SR':  # Flex 5:5
                to_update = json_data['other_positions'][0]
            elif league['queueType'] == 'RANKED_FLEX_TT':  # Flex 3:3
                to_update = json_data['other_positions'][1]
            to_update.update({
                'wins': league['wins'],
                'losses': league['losses'],
                'tier': league['tier'].title(),
                'rank': league['rank'],
                'lp': league['leaguePoints'],
                'inactive': league['inactive'],
                'shorthand_tier': league['tier'][0] + DIVISIONS[league['rank']]
            })
            json_data['total_games'] += league['wins'] + league['losses']
        if not json_data['rank']:  # Solo ranked data missing
            json_data['missing_data'].append('league')
            logger.warn("No solo ranked data available.")  # TODO: Remove

        # Parse mastery data:
        top_champions = []
        for mastery in info[1][:3]:
            top_champions.append(
                CHAMPIONS.get(str(mastery['championId']), {'name': 'Unknown'})['name'])
        if not top_champions:
            json_data['missing_data'].append('mastery')
            logger.warn("No mastery data available.")  # TODO: Remove
        json_data.update({
            'top_champions': ' | '.join(top_champions) if top_champions else 'None'
        })
        all_data += [Json(json_data), current_time]

        result = json_data

        # Add to or update cache
        entry_count = data.db_select(
            bot, select_arg='COUNT(*)', from_arg='lol_summoner_cache').fetchone().count
        logger.debug("Looking for account_id: %s", account_id)
        exist_test = data.db_select(
            bot, from_arg='lol_summoner_cache', where_arg='account_id=%s',
            input_args=[account_id]).fetchone()
        names = '(account_id, summoner_id, search_name, region, data, last_updated)'
        values = '(%s, %s, %s, %s, %s, %s)'
        if exist_test:
            logger.debug("Updating cache entry for account: %s", account_id)
            data.db_update(
                bot, 'lol_summoner_cache',
                set_arg='{} = {}'.format(names, values), where_arg='account_id=%s',
                input_args=all_data + [account_id])
        elif entry_count > 20:  # TODO: Change back to 10000
            logger.debug("Replacing oldest entry in cache with: %s", account_id)
            oldest_id = data.db_select(
                bot, from_arg='lol_summoner_cache', additional='ORDER BY last_updated ASC',
                limit=1, safe=False).fetchone().account_id
            data.db_update(
                bot, 'lol_summoner_cache',
                set_arg='{} = {}'.format(names, values), where_arg='account_id=%s',
                input_args=all_data + [oldest_id])
        else:
            logger.debug("Adding entry to cache: %s", account_id)
            data.db_insert(bot, table='lol_summoner_cache', input_args=all_data)

    else:
        # result is the summoner json data
        logger.debug("Summoner found in cache.")
        result = result.data  # Only retrieve data

    result = Summoner(**result)
    return result


async def format_match(bot, context):
    logger.debug("Starting format_match...")
    force_ranked = 'ranked' in context.options
    summoner = context.arguments[0]
    current_match_data = None
    if 'prev' in context.options:  # Check previous match
        # match_data = await _get_previous_match(
        #     bot, summoner, context.options['prev'], force_ranked=force_ranked)
        matchlist, match_type = await _get_matchlist(bot, summoner, force_ranked=force_ranked)
        try:
            chosen_match = matchlist[context.options['prev'] - 1]
        except IndexError:
            raise CBException("Match index must be between 1 and {}.".format(len(matchlist)))

        match_id = chosen_match['gameId']
        # match_data = await _clean_match(bot, match_id, match_type, summoner)
    else:  # Check for current match
        match_id = None
        try:
            current_match_data = await future(
                WATCHER.spectator.by_summoner, PLATFORMS[summoner.region], summoner.summoner_id)
            match_type = MatchTypes.CURRENT
        except HTTPError as e:
            if e.response.status_code != 404:
                handle_lol_exception(e)
        if not current_match_data:
            matchlist, match_type = await _get_matchlist(bot, summoner, force_ranked=force_ranked)
            chosen_match = matchlist[0]
            match_id = chosen_match['gameId']

    match_data = await _clean_match(
        bot, match_id, match_type, summoner, current_match_data=current_match_data)
    match_table = _get_formatted_match_table(match_data, verbose=True)
    embed = discord.Embed(
        timestamp=datetime.datetime.utcfromtimestamp(match_data['timestamp']),
        title="Match", description='\u200b' + '　'*60 + '\n' + match_table)
    embed.set_footer(text="{} | ID {} | Started".format(summoner.region.upper(), match_data['id']))
    logger.debug("Finished format match")
    return Response(embed=embed)


async def _get_newest_match(bot, summoner, force_ranked=False, safe=False):
    """Returns the current or last cleaned match the summoner was in.

    If safe, returns None if no matches found for the given summoner. It will still
    throw exceptions if there was an HTTPError though.
    """
    # Check if there is a current match
    match_id = None
    try:
        current_match_data = await future(
            WATCHER.spectator.by_summoner, PLATFORMS[summoner.region], summoner.summoner_id)
        match_type = MatchTypes.CURRENT
    except HTTPError as e:
        if e.response.status_code == 404:  # No current match
            current_match_data = None
        else:
            handle_lol_exception(e)

    if not current_match_data:  # Get last game instead
        if force_ranked:
            match_type = MatchTypes.RANKED
            match_list_future = future(
                WATCHER.match.matchlist_by_account,
                PLATFORMS[summoner.region], summoner.account_id)
        else:
            match_type = MatchTypes.NORMAL
            match_list_future = future(
                WATCHER.match.matchlist_by_account_recent,
                PLATFORMS[summoner.region], summoner.account_id)

        try:
            matchlist = await match_list_future
            assert len(matchlist['matches'])
        except Exception as e:
            if e.response.status_code == 404 or isinstance(e, AssertionError):
                if safe:
                    return
                raise CBException("No matches available.")
            elif isinstance(e, HTTPError):
                handle_lol_exception(e)
            else:
                raise e

        latest_match = matchlist['matches'][0]
        match_id = latest_match['gameId']

    return await _clean_match(
        bot, match_id, match_type, summoner, current_match_data=current_match_data)


async def _get_previous_match(bot, summoner, index, force_ranked=False):
    """Returns the cleaned match at the specified index."""
    if force_ranked:
        match_type = MatchTypes.RANKED
        match_list_future = future(
            WATCHER.match.matchlist_by_account,
            PLATFORMS[summoner.region], summoner.account_id)
    else:
        match_type = MatchTypes.NORMAL
        match_list_future = future(
            WATCHER.match.matchlist_by_account_recent,
            PLATFORMS[summoner.region], summoner.account_id)

    try:
        matchlist = (await match_list_future)['matches']
        assert len(matchlist)
    except Exception as e:
        if isinstance(e, (HTTPError, AssertionError)):
            if isinstance(e, AssertionError) or e.response.status_code == 404:
                raise CBException("No matches available.")
            else:
                handle_lol_exception(e)
        else:
            raise e

    try:
        chosen_match = matchlist[index - 1]
    except IndexError:
        raise CBException("Match index must be between 1 and {}.".format(len(matchlist)))

    match_id = chosen_match['gameId']
    return await _clean_match(bot, match_id, match_type, summoner)


async def _clean_match(
        bot, match_id, match_type, invoker, current_match_data=None, force_update=False):

    region = invoker.region
    blue_team, red_team, blue_players, red_players = {}, {}, [], []

    if match_type == MatchTypes.CURRENT:
        spectate_url = 'http://{0}.op.gg/match/new/batch/id={1}'
        cleaned_match = {
            'spectate': spectate_url.format(region, current_match_data['gameId']),
            'id': current_match_data['gameId'],
            'map': current_match_data['mapId'],
            'game_mode': current_match_data['gameQueueConfigId'],
            'timestamp': int(current_match_data['gameStartTime']/1000),
            'game_time': utilities.get_time_string(current_match_data['gameLength'] + 180),
            'finished': False,
            'region': region,
            'invoker_account_id': invoker.account_id,
            'invoker_name': invoker.summoner_name,
            'teams': { 'red': red_team, 'blue': blue_team },
            'quickstatus': {},
            'obfuscated': False  # Always false for current games
        }

        blue_team['winner'], red_team['winner'] = None, None
        red_team['bans'], blue_team['bans'] = [], []
        all_bans = []
        logger.debug("Banned champions: %s", current_match_data['bannedChampions'])
        for banned_champion in current_match_data['bannedChampions']:
            champion_id = banned_champion['championId']
            if champion_id == -1 or champion_id in all_bans:
                continue
            all_bans.append(champion_id)
            champion_name = CHAMPIONS.get(
                str(banned_champion['championId']), {}).get('name', 'Unknown')
            if banned_champion['teamId'] == 100:
                blue_team['bans'].append(champion_name)
            else:
                red_team['bans'].append(champion_name)

        participants = current_match_data['participants']
        summoner_futures = [_get_summoner(bot, it['summonerName'], region) for it in participants]
        summoner_results = await utilities.parallelize(summoner_futures, return_exceptions=True)
        player_ranks = []
        for result in summoner_results:
            if isinstance(result, Exception):
                player_ranks.append('?')
            else:
                player_ranks.append(result.shorthand_tier)

        for index, participant in enumerate(participants):
            player_team = blue_players if participant['teamId'] == 100 else red_players
            team_name = 'blue' if participant['teamId'] == 100 else 'red'
            player_team.append({
                'summoner_name': participant['summonerName'],
                'summoner_id': participant['summonerId'],
                'account_id': None,
                'spells': [participant['spell1Id'], participant['spell2Id']],
                'champion': participant['championId'],
                'rank': player_ranks[index],
                'kda': '',
                'kda_values': [0, 0, 0]
            })
            cleaned_match['quickstatus'].update({  # Have to use summoner ID because thanks Riot
                str(participant['summonerId']): [team_name, len(player_team) - 1]
            })

    else:

        result = data.db_select(
            bot, from_arg='lol_match_cache', where_arg='match_id=%s AND region=%s',
            input_args=[match_id, region]).fetchone()
        if result:  # Update match access time and return result
            # Check if summoner ID is in the quickstatus
            cached_match = result.data
            cached_match['invoker_account_id'] = invoker.account_id
            cached_match['invoker_name'] = invoker.summoner_name
            if str(invoker.account_id) in cached_match['quickstatus']:
                logger.debug("Returning cached match...")
                data.db_update(
                    bot, 'lol_match_cache', set_arg='last_accessed=%s',
                    where_arg='match_id=%s', input_args=[time.time(), match_id])
                bot.extra = cached_match  # TODO: Remove
                return cached_match
            else:
                logger.debug("Found match, but missing quickstatus data.")
                # Call raw match with invoker data
                match_data = await _get_raw_match(bot, match_id, invoker)
                # Loop through participants
                for index, identity in enumerate(match_data['participantIdentities']):
                    if ('player' in identity and
                            identity['player']['accountId'] == invoker.account_id):
                        player_position = identity['participantId']
                        # player = identity['player']
                        if match_data['participants'][index]['teamId'] == 100:
                            team_name = 'blue'
                        else:
                            team_name = 'red'
                        team = cached_match['teams'][team_name]
                        for player_data in team['players']:
                            if player_data['position'] == player_position:
                                player_data.update({
                                    'summoner_name': invoker.summoner_name,
                                    'summoner_id': invoker.summoner_id,
                                    'account_id': invoker.account_id,
                                    'rank': invoker.shorthand_tier
                                })
                                logger.debug("This is player the data: %s", player_data)
                                cached_match['quickstatus'].update({
                                    str(invoker.account_id): [team_name, index]
                                })
                                break
                        else:
                            raise CBException("Player not found in team...?")
                        break
                else:
                    raise CBException("Summoner not found in match...?")
                # Once invoker is matched, get values, set to updated_player_data
                # Loop through summoners in invoker team in cached_match
                # Once invoker is matched, update data
                bot.extra = cached_match

                data.db_update(
                    bot, 'lol_match_cache', set_arg='(data, last_accessed) = (%s, %s)',
                    where_arg='match_id=%s',
                    input_args=[Json(cached_match), time.time(), match_id])
                return cached_match

        match_data = await _get_raw_match(bot, match_id, invoker)
        cleaned_match = {
            'id': match_data['gameId'],
            'map': match_data['mapId'],
            'game_mode': match_data['queueId'],
            #'game_mode': MODES.get(match_data['queueId'], 'Unknown'),
            'timestamp': int(match_data['gameCreation']/1000),
            'game_time': utilities.get_time_string(match_data['gameDuration']),
            'finished': True,
            'region': region,
            'invoker_account_id': invoker.account_id,
            'invoker_name': invoker.summoner_name,
            'teams': { 'red': red_team, 'blue': blue_team },
            'obfuscated': False,  # Can change later
            'quickstatus': {}
        }

        blue_won = match_data['teams'][0]['win'] == "Win"  # dear god why
        blue_team['winner'], red_team['winner'] = blue_won, not blue_won
        red_team['bans'], blue_team['bans'] = [], []
        for team in match_data['teams']:
            for ban in team.get('bans', []):
                champion_name = CHAMPIONS.get(str(ban['championId']), {}).get('name', 'Unknown')
                if team['teamId'] == 100:
                    blue_team['bans'].append(champion_name)
                else:
                    red_team['bans'].append(champion_name)

        # Get player data list and ranks
        players = []
        player_ranks = []
        rank_indices = []
        rank_futures = []
        bot.extra = match_data['participantIdentities']
        for index, identity in enumerate(match_data['participantIdentities']):
            summoner_tier = match_data['participants'][index].get('highestAchievedSeasonTier', 'U')
            player_ranks.append(summoner_tier[0])
            if 'player' in identity:
                summoner_name = identity['player']['summonerName']
                logger.debug("Found player in match: %s", summoner_name)
                rank_indices.append(index)
                rank_futures.append(_get_summoner(bot, summoner_name, region))
                players.append({
                    'summoner_name': summoner_name,
                    'summoner_id': identity['player']['summonerId'],
                    'account_id': identity['player']['accountId'],
                    'position': identity['participantId']
                })
            else:
                cleaned_match['obfuscated'] = True
                players.append({'position': identity['participantId']})

        rank_results = await utilities.parallelize(rank_futures, return_exceptions=True)
        for index, result in enumerate(rank_results):
            if isinstance(result, Exception):
                logger.warn("A result was given as an error! %s", result)
            else:
                player_ranks[rank_indices[index]] = result.shorthand_tier

        # Pull information from each entry of player_game_data
        for index, player in enumerate(match_data['participants']):

            # Get spells and KDA with match details
            spell_ids = [str(player['spell1Id']), str(player['spell2Id'])]
            # spells = [SPELLS.get(spell, {}).get('name', 'Unknown') for spell in spell_ids]
            stats = player['stats']
            kills, deaths, assists = (stats['kills'], stats['deaths'], stats['assists'])
            value = (kills + assists) / (1 if deaths == 0 else deaths)
            kda_values = [kills, deaths, assists]
            kda = '{0}/{1}/{2} ({3:.2f})'.format(kills, deaths, assists, value)

            player_team = blue_players if player['teamId'] == 100 else red_players
            player_team.append({
                'summoner_name': players[index].get('summoner_name', '[Hidden]'),
                'summoner_id': players[index].get('summoner_id', ''),
                'account_id': players[index].get('account_id', ''),
                'position': players[index]['position'],
                'spells': [player['spell1Id'], player['spell2Id']],
                'champion': player['championId'],
                'rank': player_ranks[index],
                'kda': kda,
                'kda_values': kda_values
            })

            if players[index].get('summoner_id'):
                team = blue_team if player['teamId'] == 100 else red_team
                team_name = 'blue' if player['teamId'] == 100 else 'red'
                cleaned_match['quickstatus'].update({
                    str(players[index]['account_id']): [team_name, len(player_team) - 1]
                })

    red_team['players'] = red_players
    blue_team['players'] = blue_players
    if cleaned_match['finished']:  # Only cache finished matches
        _cache_match(bot, cleaned_match)
    return cleaned_match


def _cache_match(bot, cleaned_match):
    """Adds the match to the database as a cache. If the match exists, it will be replaced."""

    result = data.db_select(
        bot, from_arg='lol_match_cache', where_arg='match_id=%s AND region=%s',
        input_args=[cleaned_match['id'], cleaned_match['region']]).fetchone()

    # update args
    set_arg = '(match_id, region, data, last_accessed) = (%s, %s, %s, %s)'
    input_args = [cleaned_match['id'], cleaned_match['region'], Json(cleaned_match), time.time()]

    if result:  # Replace
        logger.debug("Replacing found match in cache: %s", cleaned_match['id'])
        data.db_update(
            bot, 'lol_match_cache', set_arg=set_arg,
            where_arg='match_id=%s', input_args=input_args + [cleaned_match['id']])

    else:  # Insert (or kick the oldest entry out)
        entry_count = data.db_select(
            bot, select_arg='COUNT(*)', from_arg='lol_match_cache').fetchone().count

        if entry_count > 20:  # TODO: Change back to 10000
            logger.debug("Replacing oldest match in cache with: %s", cleaned_match['id'])
            oldest_id = data.db_select(
                bot, from_arg='lol_match_cache', additional='ORDER BY last_updated ASC',
                limit=1, safe=False).fetchone().match_id
            data.db_update(
                bot, 'lol_match_cache', set_arg=set_arg,
                where_arg='match_id=%s', input_args=input_args + [oldest_id])

        else:  # Insert new match entry
            logger.debug("Adding new match entry: %s", cleaned_match['id'])
            data.db_insert(bot, 'lol_match_cache', input_args=input_args, mark=False)


async def _get_raw_match(bot, match_id, summoner):
    """Gets match data by the specified ID and summoner."""
    try:

        # Check raw cache
        result = data.db_select(
            bot, from_arg='lol_raw_match_cache', where_arg='match_id=%s AND region=%s',
            input_args=[match_id, summoner.region]).fetchall()

        for entry in result:
            if entry.ranked:
                logger.debug("Found ranked cached raw match")
                match_data = entry.data
                break
            elif entry.account_id == summoner.account_id:
                logger.debug("Found normal cached raw match")
                match_data = entry.data
                break
        else:  # Not found - get match and cache it
            match_data = await future(
                WATCHER.match.by_id, PLATFORMS[summoner.region],
                match_id, for_account_id=summoner.account_id)
            _cache_raw_match(bot, match_data, summoner)

        return match_data

    except Exception as e:
        if e.response.status_code == 404:
            raise CBException("Match does not exist.")
        elif isinstance(e, HTTPError):
            handle_lol_exception(e)
        else:
            raise CBException("Failed to get match information.", e=e)


def _cache_raw_match(bot, raw_data, summoner):
    """Adds the given finished match to the database as a cache."""
    # Determine if data contains all participant information (ranked)
    for participant in raw_data['participantIdentities']:
        if 'player' not in participant:
            ranked = False
            break
    else:  # Ranked match
        ranked = True

    set_arg = (
        '(match_id, account_id, ranked, region, data, last_accessed) = (%s, %s, %s, %s, %s, %s)')
    input_args = [
        raw_data['gameId'], summoner.account_id, ranked,
        summoner.region, Json(raw_data), time.time()]

    entry_count = data.db_select(
        bot, select_arg='COUNT(*)', from_arg='lol_raw_match_cache').fetchone().count
    if entry_count > 20:  # TODO: Change back to 1000
        logger.debug("Replacing oldest raw match in cache")
        oldest_entry = data.db_select(
            bot, from_arg='lol_raw_match_cache', additional='ORDER BY last_accessed ASC',
            limit=1, safe=False).fetchone()
        data.db_update(
            bot, 'lol_raw_match_cache', set_arg=set_arg, where_arg='match_id=%s AND account_id=%s',
            input_args=input_args + [oldest_entry.match_id, oldest_entry.account_id])
    else:
        logger.debug("Adding new raw match entry")
        data.db_insert(bot, 'lol_raw_match_cache', input_args=input_args, mark=False)


async def format_matchlist(bot, context):
    summoner = context.arguments[0]
    matchlist, match_type = await _get_matchlist(
        bot, summoner, force_ranked='ranked' in context.options)
    truncated_list = matchlist[:10]
    clean_matchlist = [None] * len(truncated_list)
    match_futures = []
    match_indices = []
    unknown_match_blurbs = []
    for index, match_blurb in enumerate(truncated_list):
        result = data.db_select(
            bot, from_arg='lol_match_cache', where_arg='match_id=%s AND region=%s',
            input_args=[match_blurb['gameId'], summoner.region]).fetchone()
        if result and str(summoner.account_id) in result.data['quickstatus']:
            quickstatus = result.data['quickstatus'][str(summoner.account_id)]
            team = result.data['teams'][quickstatus[0]]
            clean_matchlist[index] = {
                'game_mode': MODES.get(result.data['game_mode'], 'Unknown'),
                'champion': CHAMPIONS.get(str(match_blurb['champion']), {}).get('name', 'Unknown'),
                'kda': team['players'][quickstatus[1]]['kda'],
                'status': 'Won' if team['winner'] else 'Lost'
            }
        else:
            match_futures.append(_get_raw_match(bot, match_blurb['gameId'], summoner))
            unknown_match_blurbs.append(match_blurb)
            match_indices.append(index)
    results = await utilities.parallelize(match_futures, return_exceptions=True)
    clean_results = _clean_matchlist(bot, unknown_match_blurbs, results, summoner)
    for index, result in zip(match_indices, clean_results):
        clean_matchlist[index] = result
    bot.extra = clean_matchlist
    matchlist_table = _get_matchlist_table(bot, clean_matchlist)
    embed = discord.Embed(
        title="Match history", description='\u200b' + '　'*50 + '\n' + matchlist_table)
    embed.set_footer(text="{} | ID {}".format(summoner.region.upper(), summoner.summoner_id))
    logger.debug("Finished format match list")
    return Response(embed=embed)


def _clean_matchlist(bot, matchlist, matchlist_data, invoker):
    """Formats the given list of raw matches for the matchlist table."""
    cleaned_matches = []
    for match_blurb, match_data in zip(matchlist, matchlist_data):
        for participant in match_data['participantIdentities']:
            if ('player' in participant and
                    participant['player']['accountId'] == invoker.account_id):
                stats = match_data['participants'][participant['participantId']-1]['stats']
                win = 'Won' if stats['win'] else 'Lost'
                kills, deaths, assists = (stats['kills'], stats['deaths'], stats['assists'])
                value = (kills + assists) / (1 if deaths == 0 else deaths)
                kda_values = [kills, deaths, assists]
                kda = '{0}/{1}/{2} ({3:.2f})'.format(kills, deaths, assists, value)
                break
        else:
            kda, win = '?', '?'

        cleaned_matches.append({
            'game_mode': MODES.get(match_data['queueId'], 'Unknown'),
            'champion': CHAMPIONS.get(str(match_blurb['champion']), {}).get('name', 'Unknown'),
            'kda': kda,
            'status': win
        })

    return cleaned_matches


def _get_matchlist_table(bot, clean_matchlist):
    """Returns a nicely formatted matchlist table."""
    guide_template = (
        '#  | Game Type               | Champion      | KDA              | Status |\n'
        '---|-------------------------|---------------|------------------|--------|\n')
    formatted_matches = []
    for index, match in enumerate(clean_matchlist):
        formatted_matches.append((
            '{0: <3}| {1[game_mode]: <24}| {1[champion]: <14}| '
            '{1[kda]: <17}| {1[status]: <7}|').format(index + 1, match))

    return '```\n{0}{1}```'.format(guide_template, '\n'.join(formatted_matches))


async def _get_matchlist(bot, summoner, force_ranked=False):
    if force_ranked:
        match_type = MatchTypes.RANKED
        match_list_future = future(
            WATCHER.match.matchlist_by_account,
            PLATFORMS[summoner.region], summoner.account_id)
    else:
        match_type = MatchTypes.NORMAL
        match_list_future = future(
            WATCHER.match.matchlist_by_account_recent,
            PLATFORMS[summoner.region], summoner.account_id)

    try:
        match_list = (await match_list_future)['matches']
        assert len(match_list)
    except Exception as e:
        if e.response.status_code == 404 or isinstance(e, AssertionError):
            raise CBException("No matches available.")
        elif isinstance(e, HTTPError):
            handle_lol_exception(e)
        else:
            raise e
    return match_list, match_type


async def challenge(bot, context):
    pass


async def set_region(bot, context):
    last_region = data.get(bot, __name__, 'region', guild_id=context.guild.id)
    last_region_message = ' (Previously {})'.format(last_region.upper()) if last_region else ''
    if context.arguments[0]:
        region = context.arguments[0].replace(' ', '').lower()
        if region in REGIONS:
            region = REGIONS[region]
        elif region not in REGIONS.values():
            raise CBException(
                "That is not a defined region. Available regions:",
                ', '.join(r.upper() for r in REGIONS.values()))
        data.add(bot, __name__, 'region', region, guild_id=context.guild.id)
        return Response(content="Region set.{}".format(last_region_message))
    else:
        data.remove(bot, __name__, 'region', guild_id=context.guild.id, safe=True)
        return Response(content="Region reset to NA.{}".format(last_region_message))


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
        response += 'Game Type: {}\n\n'.format(MODES.get(match['game_mode'], 'Unknown'))
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

        obfuscated = match['obfuscated']
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
                    player['summoner_name'].encode('ascii')
                except UnicodeEncodeError:  # Non-ascii detected
                    use_full_width = True

                # Highlight summoner if this is the one we're looking for
                is_target = player['summoner_name'] == match['invoker_name']

                # Add champion name, kda, and spells
                if obfuscated and not is_target:
                    summoner_name = '[Hidden]'
                    summoner_rank = player['rank'][0] + '?'
                else:
                    summoner_name = player['summoner_name']
                    summoner_rank = player['rank']
                champion = CHAMPIONS.get(str(player['champion']), {}).get('name', 'Unknown')
                spells = [
                    SPELLS.get(str(it), {}).get('name', 'Unknown') for it in player['spells']
                ]
                team_response.append([
                    is_target, summoner_name,
                    summoner_rank, champion,
                    player['kda'], *spells
                ])

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
                if player['account_id'] == match['invoker_account_id']][0]
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


'''
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
'''


async def _get_static_data(bot):
    """Get static data returned as a tuple."""
    try:
        # assert False  # Debug
        champions = (await future(
            WATCHER.static_data.champions, 'na1', data_by_id='true'))['data']
        spells = (await future(
            WATCHER.static_data.summoner_spells, 'na1', data_by_id='true'))['data']
        cache_dictionary = { 'champions': champions, 'spells': spells }
        cache_bytes = io.StringIO()
        json.dump(cache_dictionary, cache_bytes, indent=4)
        utilities.add_temporary_file(bot, cache_bytes, 'discrank_static_cache.json')
    except (HTTPError, AssertionError) as e:
        if isinstance(e, HTTPError):
            logger.warn(
                "Failed to retrieve static data. Your Riot API "
                "token may be invalid or blacklisted - please check to make "
                "sure you copied your key correctly! %s\n%s", e.response.content, e)
        else:
            logger.warn("Skipping static data update. Don't forget to remove this!")
        file_path = utilities.get_temporary_file(bot, 'discrank_static_cache.json')
        if file_path:
            with open(file_path, 'r') as cache_file:
                static_cache = json.load(cache_file)
            champions = static_cache['champions']
            spells = static_cache['spells']
            logger.warn("Using fallback static data cache.")
        else:
            raise CBException(
                "Fallback static data cache not found.", error_type=ErrorTypes.STARTUP)

    champions_named = dict((v['key'].lower(), v) for _, v in champions.items())
    champions.update(champions_named)
    spells_named = dict((v['name'].lower(), v) for _, v in spells.items())
    spells.update(spells_named)
    return (champions, spells)


async def bot_on_ready_boot(bot):
    # Obtain all static data required
    watcher = RiotWatcher(configurations.get(bot, __name__, key='token'))
    configurations.redact(bot, __name__, 'token')

    # Get static data
    global WATCHER, CHAMPIONS, SPELLS
    WATCHER = watcher
    CHAMPIONS, SPELLS = await _get_static_data(bot)

    logger.info('discrank.py is ready!')


# Static data
MODES = {
    0:          "Custom",
    2:          "Normal",
    4:          "Ranked Solo/Duo",
    6:          "Ranked Solo/Duo",
    7:          "Co-op vs AI",
    8:          "Normal 3v3",
    9:          "Ranked Flex 3v3",
    14:         "Normal Draft",
    16:         "Dominion Blind",
    17:         "Dominion Draft",
    25:         "Co-op vs AI",
    31:         "Co-op vs AI",
    32:         "Co-op vs AI",
    33:         "Co-op vs AI",
    41:         "Ranked 3v3",
    42:         "Ranked 5v5",
    52:         "Co-op vs AI (3v3)",
    61:         "Team Builder",
    65:         "ARAM",
    70:         "One For All",
    72:         "Magma Chamber 1v1",
    73:         "Magma Chamber 2v2",
    75:         "Hexakill",
    76:         "URF",
    78:         "One For All (Mirror)",
    83:         "Co-op vs AI (URF)",
    91:         "Doom Bots Lv 1",
    92:         "Doom Bots Lv 2",
    93:         "Doom Bots Lv 3",
    96:         "Ascension",
    98:         "Hexakill",
    100:        "Bilgewater",
    300:        "Legend of the Poro King",
    310:        "Nemesis",
    313:        "Bilgewater ARAM",
    315:        "Nexus Siege",
    317:        "Definitely Not Dominion",
    318:        "All Random URF",
    325:        "All Random",
    400:        "Normal (Draft)",
    410:        "Ranked Solo/Duo",
    420:        "Ranked Solo/Duo",
    430:        "Normal 5v5 (Blind)",
    440:        "Ranked Flex 5v5",
    600:        "Blood Hunt (Assassin Mode)",
    610:        "Dark Star",

    "CUSTOM":                                0,
    "NONE":                                  0,
    "NORMAL":                                2,
    "NORMAL_3x3":                            8,
    "NORMAL_5x5_BLIND":                      2,
    "NORMAL_5x5_DRAFT":                      14,
    "RANKED_PREMADE_5x5*":                   6,
    "RANKED_PREMADE_3x3*":                   9,
    "RANKED_TEAM_3x3":                       41,
    "RANKED_TEAM_5x5":                       42,
    "ODIN_UNRANKED":                         16,
    "ODIN_5x5_BLIND":                        16,
    "ODIN_5x5_DRAFT":                        17,
    "BOT_5x5*":                              7,
    "BOT_ODIN_5x5":                          25,
    "BOT":                                   31,
    "BOT_5x5_INTRO":                         31,
    "BOT_5x5_BEGINNER":                      32,
    "BOT_5x5_INTERMEDIATE":                  33,
    "BOT_3x3":                               52,
    "BOT_TT_3x3":                            52,
    "GROUP_FINDER_5x5":                      61,
    "ARAM_UNRANKED_5x5":                     65,
    "ARAM_5x5":                              65,
    "ONEFORALL_5x5":                         70,
    "FIRSTBLOOD_1x1":                        72,
    "FIRSTBLOOD_2x2":                        73,
    "SR_6x6":                                75,
    "URF":                                   76,
    "URF_5x5":                               76,
    "URF_BOT":                               83,
    "BOT_URF_5x5":                           83,
    "NIGHTMARE_BOT":                         91,
    "NIGHTMARE_BOT_5x5_RANK1":               91,
    "NIGHTMARE_BOT_5x5_RANK2":               92,
    "NIGHTMARE_BOT_5x5_RANK5":               93,
    "ASCENSION":                             96,
    "ASCENSION_5x5":                         96,
    "HEXAKILL":                              98,
    "BILGEWATER_ARAM_5x5":                   100,
    "KING_PORO":                             300,
    "KING_PORO_5x5":                         300,
    "COUNTER_PICK":                          310,
    "BILGEWATER":                            313,
    "BILGEWATER_5x5":                        313,
    "SIEGE":                                 315,
    "DEFINITELY_NOT_DOMINION_5x5":           317,
    "TEAM_BUILDER_DRAFT_UNRANKED_5x5":       400,
    "CAP_5x5":                               400,
    "TEAM_BUILDER_DRAFT_RANKED_5x5":         410,
    "RANKED_SOLO_5x5":                       410,
    "RANKED_FLEX_TT":                        9,
    "RANKED_FLEX_SR":                        440,
    "ONEFORALL_MIRRORMODE_5x5":              78,
    "COUNTER_PICK":                          310,
    "ARURF_5X5":                             318,
    "ARSR_5x5":                              325,
    "TEAM_BUILDER_RANKED_SOLO":              420, # lol
    "TB_BLIND_SUMMONERS_RIFT_5x5":           430,
    "ASSASSINATE_5x5":                       600,
    "DARKSTAR_3x3":                          610
}


# NOTE: If any additional regions need to be added,
#   modify create_lol_region to alter the lol_region enum type
REGIONS = {
    'brazil':               'br',
    'europeeast':           'eune',
    'europewest':           'euw',
    'korea':                'kr',
    'latinamericanorth':    'lan',
    'latinamericasouth':    'las',
    'northamerica':         'na',
    'oceania':              'oce',
    'russia':               'ru',
    'turkey':               'tr',
    'japan':                'jp'
}

PLATFORMS = {
    'br':   'br1',
    'eune': 'eun1',
    'euw':  'euw1',
    'kr':   'kr',
    'lan':  'la1',
    'las':  'la2',
    'na':   'na1',
    'oce':  'oc1',
    'ru':   'ru',
    'tr':   'tr1',
    'jp':   'jp1'
}

RANK_ICONS = {
    'Challenger':   'https://i.imgur.com/war3JkZ.png',
    'Master':       'https://i.imgur.com/SftcjBK.png',
    'Diamond':      'https://i.imgur.com/tE4UXbe.png',
    'Platinum':     'https://i.imgur.com/EYAWQ2P.png',
    'Gold':         'https://i.imgur.com/y4CgEY8.png',
    'Silver':       'https://i.imgur.com/KzsC03C.png',
    'Bronze':       'https://i.imgur.com/lfT1lfr.png',
    'Unranked':     'https://i.imgur.com/9ENx4rB.png'
}

# TODO: Finish
RANK_COLORS = {
    'Challenger':   discord.Color(0x2aa3d8),
    'Master':       discord.Color(0x4ae4d5),
    'Diamond':      discord.Color(0x1d66b2),
    'Platinum':     discord.Color(0xcbdde4),
    'Gold':         discord.Color(0xe8d270),
    'Silver':       discord.Color(0x8ca099),
    'Bronze':       discord.Color(0xad7b4a),
    'Unranked':     discord.Embed.Empty
}

DIVISIONS = {"V": "5", "IV": "4", "III": "3", "II": "2", "I": "1"}

WATCHER, CHAMPIONS, SPELLS = None, None, None  # Set on startup

UNKNOWN_EMOJI = ":grey_question:"

CHAMPION_EMOJIS = {
    266:    '<:Champion_Aatrox:341777426537512962>',
    12:     '<:Champion_Alistar:341777427196018691>',
    40:     '<:Champion_Janna:341777427233505292>',
    51:     '<:Champion_Caitlyn:341777427233505302>',
    63:     '<:Champion_Brand:341777427258802187>',
    126:    '<:Champion_Jayce:341777427292225537>',
    86:     '<:Champion_Garen:341777427292225547>',
    1:      '<:Champion_Annie:341777427384631317>',
    136:    '<:Champion_AurelionSol:341777427418316801>',
    24:     '<:Champion_Jax:341777427418316811>',
    105:    '<:Champion_Fizz:341777427443220480>',
    42:     '<:Champion_Corki:341777427447676928>',
    84:     '<:Champion_Akali:341777427451740160>',
    268:    '<:Champion_Azir:341777427464323074>',
    245:    '<:Champion_Ekko:341777427468517377>',
    222:    '<:Champion_Jinx:341777427497746434>',
    9:      '<:Champion_Fiddlesticks:341777427552403457>',
    36:     '<:Champion_DrMundo:341777427560923147>',
    131:    '<:Champion_Diana:341777427590283264>',
    32:     '<:Champion_Amumu:341777427602604052>',
    53:     '<:Champion_Blitzcrank:341777427619643403>',
    201:    '<:Champion_Braum:341777427623575552>',
    202:    '<:Champion_Jhin:341777427628032011>',
    30:     '<:Champion_Karthus:341777427636158465>',
    119:    '<:Champion_Draven:341777427636420608>',
    69:     '<:Champion_Cassiopeia:341777427636420618>',
    59:     '<:Champion_JarvanIV:341777427640352778>',
    150:    '<:Champion_Gnar:341777427648741376>',
    22:     '<:Champion_Ashe:341777427648872448>',
    79:     '<:Champion_Gragas:341777427657261066>',
    432:    '<:Champion_Bard:341777427665780736>',
    39:     '<:Champion_Irelia:341777427678363659>',
    74:     '<:Champion_Heimerdinger:341777427682295808>',
    60:     '<:Champion_Elise:341777427682295818>',
    43:     '<:Champion_Karma:341777427682557952>',
    31:     '<:Champion_Chogath:341777427686621184>',
    81:     '<:Champion_Ezreal:341777427690946560>',
    114:    '<:Champion_Fiora:341777427699204097>',
    104:    '<:Champion_Graves:341777427703398400>',
    429:    '<:Champion_Kalista:341777427724369920>',
    103:    '<:Champion_Ahri:341777427762249738>',
    427:    '<:Champion_Ivern:341777427812581386>',
    28:     '<:Champion_Evelynn:341777427812581396>',
    420:    '<:Champion_Illaoi:341777427816644608>',
    41:     '<:Champion_Gangplank:341777427816775680>',
    122:    '<:Champion_Darius:341777427829227530>',
    164:    '<:Champion_Camille:341777427900661790>',
    120:    '<:Champion_Hecarim:341777428076822548>',
    3:      '<:Champion_Galio:341777428202651648>',
    34:     '<:Champion_Anivia:341777428710162443>',
    121:    '<:Champion_Khazix:341777651008274433>',
    7:      '<:Champion_Leblanc:341777651045892107>',
    203:    '<:Champion_Kindred:341777651155075073>',
    96:     '<:Champion_KogMaw:341777651167395841>',
    20:     '<:Champion_Nunu:341777651251412993>',
    80:     '<:Champion_Pantheon:341777651293487106>',
    72:     '<:Champion_Skarner:341777651297550339>',
    117:    '<:Champion_Lulu:341777651310002177>',
    236:    '<:Champion_Lucian:341777651322585110>',
    15:     '<:Champion_Sivir:341777651335430146>',
    10:     '<:Champion_Kayle:341777651347750913>',
    62:     '<:Champion_MonkeyKing:341777651356139523>',
    56:     '<:Champion_Nocturne:341777651385630721>',
    38:     '<:Champion_Kassadin:341777651423248384>',
    64:     '<:Champion_LeeSin:341777651440025600>',
    25:     '<:Champion_Morgana:341777651440025611>',
    99:     '<:Champion_Lux:341777651444219922>',
    127:    '<:Champion_Lissandra:341777651460997130>',
    14:     '<:Champion_Sion:341777651460997131>',
    89:     '<:Champion_Leona:341777651465322496>',
    11:     '<:Champion_MasterYi:341777651469647872>',
    90:     '<:Champion_Malzahar:341777651482230787>',
    21:     '<:Champion_MissFortune:341777651490488330>',
    76:     '<:Champion_Nidalee:341777651507134474>',
    267:    '<:Champion_Nami:341777651507134475>',
    82:     '<:Champion_Mordekaiser:341777651507265537>',
    57:     '<:Champion_Maokai:341777651515654144>',
    54:     '<:Champion_Malphite:341777651515785216>',
    55:     '<:Champion_Katarina:341777651528237057>',
    61:     '<:Champion_Orianna:341777651536625664>',
    33:     '<:Champion_Rammus:341777651561660419>',
    133:    '<:Champion_Quinn:341777651561660426>',
    68:     '<:Champion_Rumble:341777651587088384>',
    102:    '<:Champion_Shyvana:341777651595214858>',
    85:     '<:Champion_Kennen:341777651607928843>',
    240:    '<:Champion_Kled:341777651620642817>',
    35:     '<:Champion_Shaco:341777651649740800>',
    2:      '<:Champion_Olaf:341777651666780160>',
    111:    '<:Champion_Nautilus:341777651674906634>',
    27:     '<:Champion_Singed:341777651675168778>',
    421:    '<:Champion_RekSai:341777651683295233>',
    92:     '<:Champion_Riven:341777651683295242>',
    113:    '<:Champion_Sejuani:341777651683426306>',
    75:     '<:Champion_Nasus:341777651696009216>',
    107:    '<:Champion_Rengar:341777651700334592>',
    78:     '<:Champion_Poppy:341777651704397835>',
    58:     '<:Champion_Renekton:341777651763118090>',
    13:     '<:Champion_Ryze:341777651804930049>',
    497:    '<:Champion_Rakan:341777652077821963>',
    98:     '<:Champion_Shen:341777652086079498>',
    6:      '<:Champion_Urgot:341777778921701387>',
    110:    '<:Champion_Varus:341777778925895692>',
    17:     '<:Champion_Teemo:341777778972295170>',
    77:     '<:Champion_Udyr:341777779009912836>',
    45:     '<:Champion_Veigar:341777779030753281>',
    48:     '<:Champion_Trundle:341777779035078677>',
    254:    '<:Champion_Vi:341777779035078691>',
    37:     '<:Champion_Sona:341777779043598337>',
    67:     '<:Champion_Vayne:341777779056181270>',
    112:    '<:Champion_Viktor:341777779098124290>',
    106:    '<:Champion_Volibear:341777779118964739>',
    134:    '<:Champion_Syndra:341777779119095818>',
    163:    '<:Champion_Taliyah:341777779140067329>',
    16:     '<:Champion_Soraka:341777779143999498>',
    412:    '<:Champion_Thresh:341777779169296384>',
    26:     '<:Champion_Zilean:341777779173359617>',
    91:     '<:Champion_Talon:341777779186073600>',
    29:     '<:Champion_Twitch:341777779186073603>',
    4:      '<:Champion_TwistedFate:341777779215302656>',
    23:     '<:Champion_Tryndamere:341777779244924928>',
    8:      '<:Champion_Vladimir:341777779265765376>',
    154:    '<:Champion_Zac:341777779269828608>',
    101:    '<:Champion_Xerath:341777779270090762>',
    157:    '<:Champion_Yasuo:341777779282673673>',
    161:    '<:Champion_VelKoz:341777779290931209>',
    44:     '<:Champion_Taric:341777779290931210>',
    50:     '<:Champion_Swain:341777779312033792>',
    143:    '<:Champion_Zyra:341777779332874250>',
    238:    '<:Champion_Zed:341777779337068544>',
    19:     '<:Champion_Warwick:341777779374948353>',
    115:    '<:Champion_Ziggs:341777779479543808>',
    18:     '<:Champion_Tristana:341777779483738122>',
    223:    '<:Champion_TahmKench:341777779509035008>',
    498:    '<:Champion_Xayah:341777779894779904>',
    5:      '<:Champion_XinZhao:341777779924402179>',
    83:     '<:Champion_Yorick:341777780343832577>',
    141:    '<:Champion_Kayne:343597241367265281>'
}

SPELL_EMOJIS = {
    4:      '<:Spell_Flash:341780677164793858>',
    21:     '<:Spell_Barrier:341780677168988172>',
    1:      '<:Spell_Cleanse:341780677244747777>',
    6:      '<:Spell_Ghost:341780677253005314>',
    13:     '<:Spell_Clarity:341780677324177420>',
    14:     '<:Spell_Ignite:341780677416583169>',
    11:     '<:Spell_Smite:341780677487755265>',
    7:      '<:Spell_Heal:341780677517377538>',
    3:      '<:Spell_Exhaust:341780677651464192>',
    12:     '<:Spell_Teleport:341780677697470464>',
    32:     '<:Spell_Mark:341780677890539521>'
}
