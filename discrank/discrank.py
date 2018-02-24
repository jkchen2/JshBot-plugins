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
    def __init__(self, force_update=False):
        self.force_update = force_update
    async def __call__(self, bot, message, value, *a):
        if not value:
            raise CBException("Summoner name must not be blank.")
        if isinstance(message.channel, discord.DMChannel):
            region = 'na'
        else:
            region = data.get(bot, __name__, 'region', guild_id=message.guild.id, default='na')
        return await _get_summoner(bot, value, region, force_update=self.force_update)


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
                Arg('summoner 1', convert=SummonerConverter(force_update=True)),
                Arg('summoner 2', convert=SummonerConverter(force_update=True)),
                Arg('champion 1', convert=ChampionConverter()),
                Arg('champion 2', convert=ChampionConverter()),
                doc='Compares two summoners\' mastery points, mastery levels, and number '
                    'of games played (ranked) data against each other.',
                function=challenge),
            SubCommand(
                Opt('setregion'),
                Arg('region', argtype=ArgTypes.MERGED_OPTIONAL,
                    doc='Valid regions are `NA` (default), `BR`, `EUNE`, `EUW`, `JP`, '
                        '`KR`, `LAN`, `LAS`, `OCE`, `RU`, and `TR`.'),
                doc='Sets the default region for the server.',
                allow_direct=False, function=set_region)],
        shortcuts=[
            Shortcut('summoner', 'summoner {name}', Arg('name', argtype=ArgTypes.MERGED)),
            Shortcut('match', 'match {name}', Arg('name', argtype=ArgTypes.MERGED)),
            Shortcut(
                'challenge', 'challenge {summoner1} {summoner2} {champion1} {champion2}',
                Arg('summoner1'), Arg('summoner2'), Arg('champion1'), Arg('champion2'))],
        description='Get League of Legends information from the API.',
        other='You can specify the region for a summoner by adding '
              '`:region` after the name. For example, try\n`{invoker}lol '
              'summoner hide on bush:kr`',
        category='game data'))

    return new_commands


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
    if not data.db_exists(bot, 'lol_region', check_type=True):
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

    # Add cleverly disguised padding field
    embed.add_field(
        name='{} total ranked game{}'.format(
            summoner.total_games, '' if summoner.total_games == 1 else 's'),
        value='\u200b' + '　'*35 + '\u200b', inline=False)

    if 'mastery' not in summoner.missing_data:
        top_champions_text = ' | '.join([
            '[{}](# "Level: {} | Points: {}")'.format(
                CHAMPION_EMOJIS.get(it[0], UNKNOWN_EMOJI), *it[1:])
            for it in summoner.top_champions
        ])
        embed.add_field(name="Top champions", value=top_champions_text)

    newest_match = await _get_newest_match(bot, summoner, safe=True)
    if newest_match:
        if str(summoner.summoner_id) not in newest_match['quickstatus']:
            status = 'Last' if newest_match['finished'] else 'Current'
            line = '[{}] Match information unavailable'.format(UNKNOWN_EMOJI)
        elif newest_match['finished']:
            status = 'Last'
            quickstatus = newest_match['quickstatus'][str(summoner.summoner_id)]
            team = newest_match['teams'][quickstatus[0]]
            player = team['players'][quickstatus[1]]
            win_text = 'Won' if team['winner'] else 'Lost'
            time_delta = time.time() - newest_match['timestamp'] + newest_match['game_time']
            line = '[{}](# "{}") | {} `{: <2}\u200b` | {}{} | KDA: {}'.format(
                '🇼' if team['winner'] else '🇱',
                "{} {} ago".format(win_text, utilities.get_time_string(time_delta, text=True)),
                CHAMPION_EMOJIS.get(player['champion'][0], UNKNOWN_EMOJI),
                player['champion'][1],
                SPELL_EMOJIS.get(player['spells'][0], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][1], UNKNOWN_EMOJI),
                player['kda']
            )
        else:
            status = 'Current'
            quickstatus = newest_match['quickstatus'][str(summoner.summoner_id)]
            player = newest_match['teams'][quickstatus[0]]['players'][quickstatus[1]]
            line = '{} | {} | {}{} | [Time: {}]({} "op.gg spectate batch file")'.format(
                ':large_blue_circle:' if quickstatus[0] == 'blue' else ':red_circle:',
                CHAMPION_EMOJIS.get(player['champion'][0], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][0], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(player['spells'][1], UNKNOWN_EMOJI),
                utilities.get_time_string(newest_match['game_time']),
                newest_match['spectate']
            )
        game_mode = MODES.get(newest_match['game_mode'], 'Unknown')
        embed.add_field(name='{} match [{}]'.format(status, game_mode), value=line)

    time_delta = time.time() - summoner.last_updated
    if time_delta > 10:
        time_ago = utilities.get_time_string(time_delta, text=True, resolution=1) + ' ago'
    else:
        time_ago = 'just now'
    embed.set_footer(
        text="{} | ID {} | AID {} | Updated {}".format(
            summoner.region.upper(), summoner.summoner_id, summoner.account_id, time_ago),
        icon_url=REGION_IMAGES.get(summoner.region, UNKNOWN_EMOJI_URL))
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

    # Check if the entry exists and needs to be refreshed, or has expired
    if not result or (time.time() - result.last_updated > 24*60*60) or force_update:
        logger.debug("Summoner NOT found in cache (or expired or forced).")
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
            info = await utilities.parallelize([league_future, mastery_future], pass_error=True)
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

        # Parse mastery data:
        top_champions = [
            [it['championId'], it['championLevel'], it['championPoints']] for it in info[1][:3]]
        if not top_champions:
            json_data['missing_data'].append('mastery')
        else:
            json_data.update({ 'top_champions': top_champions })
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
        elif entry_count > 10000:
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
        matchlist, match_type = await _get_matchlist(bot, summoner, force_ranked=force_ranked)
        try:
            chosen_match = matchlist[context.options['prev'] - 1]
        except IndexError:
            raise CBException(
                "Match index must be between 1 and {} inclusive.".format(len(matchlist)))

        match_id = chosen_match['gameId']
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
    embed = _build_match_embed(match_data)
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
            'game_mode': current_match_data.get('gameQueueConfigId', 0),
            'timestamp': int(current_match_data['gameStartTime']/1000),
            'game_time': current_match_data['gameLength'] + 180,
            'finished': False,
            'region': region,
            'invoker_account_id': invoker.account_id,
            'invoker_name': invoker.summoner_name,
            'teams': { 'red': red_team, 'blue': blue_team },
            'quickstatus': {},
            'obfuscated': False  # Always false for current games
        }

        blue_team['winner'], red_team['winner'] = None, None
        red_team['bans'], blue_team['bans'], all_bans = [], [], []
        logger.debug("Banned champions: %s", current_match_data['bannedChampions'])
        for banned_champion in current_match_data['bannedChampions']:
            champion_id = banned_champion['championId']
            if champion_id != -1 and champion_id not in all_bans:
                all_bans.append(champion_id)
                player_team = blue_team if banned_champion['teamId'] == 100 else red_team
                player_team['bans'].append(banned_champion['championId'])

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
                'champion': [participant['championId']],
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
            if str(invoker.summoner_id) in cached_match['quickstatus']:
                logger.debug("Returning cached match...")
                data.db_update(
                    bot, 'lol_match_cache', set_arg='last_accessed=%s',
                    where_arg='match_id=%s', input_args=[time.time(), match_id])
                return cached_match
            else:  # Update quickstatus data
                logger.debug("Found match, but missing quickstatus data.")
                match_data = await _get_raw_match(bot, match_id, invoker)
                for index, identity in enumerate(match_data['participantIdentities']):
                    if ('player' in identity and
                            identity['player']['accountId'] == invoker.account_id):
                        player_position = identity['participantId']
                        if match_data['participants'][index]['teamId'] == 100:
                            team_name = 'blue'
                        else:
                            team_name = 'red'
                        team = cached_match['teams'][team_name]
                        for player_index, player_data in enumerate(team['players']):
                            if player_data['position'] == player_position:
                                player_data.update({
                                    'summoner_name': invoker.summoner_name,
                                    'summoner_id': invoker.summoner_id,
                                    'account_id': invoker.account_id,
                                    'rank': invoker.shorthand_tier
                                })
                                logger.debug("This is player the data: %s", player_data)
                                cached_match['quickstatus'].update({
                                    str(invoker.summoner_id): [team_name, player_index]
                                })
                                break
                        else:
                            raise CBException("Player not found in team...?")
                        break
                else:
                    logger.warn("Summoner not found in match...?")
                    #raise CBException("Summoner not found in match...?")

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
            'timestamp': int(match_data['gameCreation']/1000),
            'game_time': match_data['gameDuration'],
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
        red_team['bans'], blue_team['bans'], all_bans = [], [], []
        for team in match_data['teams']:
            player_team = blue_team if team['teamId'] == 100 else red_team
            player_team['bdt'] = [
                team.get(it, 0) for it in ('baronKills', 'dragonKills', 'towerKills')]
            for ban in team.get('bans', []):
                champion_id = ban['championId']
                if champion_id != -1 and champion_id not in all_bans:
                    player_team['bans'].append(champion_id)
                    all_bans.append(champion_id)

        # Get player data list and ranks
        players = []
        player_ranks = []
        rank_indices = []
        rank_futures = []
        for index, identity in enumerate(match_data['participantIdentities']):
            summoner_tier = match_data['participants'][index].get('highestAchievedSeasonTier', 'U')
            player_ranks.append(summoner_tier[0])
            if 'player' in identity and 'summonerId' in identity['player']:
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

        # Get total kills in match
        total_kills = 0
        blue_kills, red_kills = 0, 0
        for player in match_data['participants']:
            player_kills = player['stats']['kills']
            total_kills += player_kills
            if player['teamId'] == 100:
                blue_kills += player_kills
            else:
                red_kills += player_kills
        total_kills = 1 if total_kills <= 0 else total_kills
        blue_kills, red_kills = max(1, blue_kills), max(1, red_kills)

        # Pull information from each entry of player_game_data
        for index, player in enumerate(match_data['participants']):

            if player['teamId'] == 100:
                player_team, team_kills = blue_players, blue_kills
            else:
                player_team, team_kills = red_players, red_kills

            # Get spells and KDA with match details
            stats = player['stats']
            kills, deaths, assists = (stats['kills'], stats['deaths'], stats['assists'])
            value = (kills + assists) / (1 if deaths == 0 else deaths)
            kda_values = [kills, deaths, assists]
            participation = '{:.1f}%'.format(100 * (kills + assists) / team_kills)
            kda = '{}/{}/{} ({:.2f} | {})'.format(kills, deaths, assists, value, participation)

            # Get kill tier
            kill_tier = 0
            kill_tier_frequency = 0
            kill_tiers = ['double', 'triple', 'quadra', 'penta', 'unreal']
            for tier_index, tier in enumerate(kill_tiers):
                tier_test = stats[tier + 'Kills']
                if tier_test:
                    kill_tier = tier_index + 1
                    kill_tier_frequency = tier_test

            player_team.append({
                'summoner_name': players[index].get('summoner_name', '[Hidden]'),
                'summoner_id': players[index].get('summoner_id', ''),
                'account_id': players[index].get('account_id', ''),
                'position': players[index]['position'],
                'damage': stats.get('totalDamageDealtToChampions', 0),
                'gold': stats.get('goldEarned', 0),
                'cs': stats.get('totalMinionsKilled', 0) + stats.get('neutralMinionsKilled', 0),
                'spells': [player['spell1Id'], player['spell2Id']],
                'champion': [player['championId'], stats['champLevel']],
                'rank': player_ranks[index],
                'kda': kda,
                'kda_values': kda_values,
                'kill_tier': [kill_tier, kill_tier_frequency]
            })

            if players[index].get('summoner_id'):
                team = blue_team if player['teamId'] == 100 else red_team
                team_name = 'blue' if player['teamId'] == 100 else 'red'
                cleaned_match['quickstatus'].update({
                    str(players[index]['summoner_id']): [team_name, len(player_team) - 1]
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

        if entry_count > 10000:
            logger.debug("Replacing oldest match in cache with: %s", cleaned_match['id'])
            oldest_id = data.db_select(
                bot, from_arg='lol_match_cache', additional='ORDER BY last_accessed ASC',
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
        if isinstance(e, HTTPError):
            if e.response.status_code == 404:
                raise CBException("Match does not exist.")
            else:
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
    if entry_count > 1000:
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
    truncated_list = matchlist[:20]
    clean_matchlist = [None] * len(truncated_list)
    match_futures = []
    match_indices = []
    unknown_match_blurbs = []
    for index, match_blurb in enumerate(truncated_list):
        result = data.db_select(
            bot, from_arg='lol_match_cache', where_arg='match_id=%s AND region=%s',
            input_args=[match_blurb['gameId'], summoner.region]).fetchone()
        if result and str(summoner.summoner_id) in result.data['quickstatus']:
            quickstatus = result.data['quickstatus'][str(summoner.summoner_id)]
            team = result.data['teams'][quickstatus[0]]
            player = team['players'][quickstatus[1]]
            clean_matchlist[index] = {
                'game_mode': result.data['game_mode'],
                'champion': player['champion'],
                'kda': player['kda'],
                'status': team['winner'],
                'spells': player['spells'],
                'end_time': int(match_blurb['timestamp']/1000 + result.data['game_time'])
            }
        else:
            match_futures.append(_get_raw_match(bot, match_blurb['gameId'], summoner))
            unknown_match_blurbs.append(match_blurb)
            match_indices.append(index)
    results = await utilities.parallelize(match_futures, return_exceptions=True)
    clean_results = _clean_matchlist(bot, unknown_match_blurbs, results, summoner)
    for index, result in zip(match_indices, clean_results):
        clean_matchlist[index] = result

    entries = _get_matchlist_entries(bot, clean_matchlist)
    embed, _ = _build_matchlist_embed(bot, summoner, entries, 0)
    logger.debug("Finished format match list")

    return Response(
        message_type=MessageTypes.INTERACTIVE,
        extra_function=_matchlist_menu,
        extra={'buttons': ['⬅', '➡']},
        embed=embed,
        summoner=summoner,
        entries=entries,
        page_index=0)


async def _matchlist_menu(bot, context, response, result, timed_out):
    if timed_out or not result:
        return
    selection = ['⬅', '➡'].index(result[0].emoji)
    page_index = response.page_index + (-1 if selection == 0 else 1)
    embed, page_index = _build_matchlist_embed(
        bot, response.summoner, response.entries, page_index)
    response.page_index = page_index
    await response.message.edit(embed=embed)
    

def _build_matchlist_embed(bot, summoner, entries, page_index):
    """Builds the matchlist embed for the given entries and page"""
    split_entries = [entries[it:it+5] for it in range(0, len(entries), 5)]
    max_index = len(split_entries) - 1
    page_index = max(min(page_index, max_index), 0)
    columns = list(zip(*split_entries[page_index]))

    embed = discord.Embed(
        title="{}'s match history".format(summoner.summoner_name), description='\u200b')
    embed.add_field(name='Match', value='\n'.join(columns[0]))
    embed.add_field(name='Champion | Spells | KDA', value='\n'.join(columns[1]))
    embed.add_field(
        name='Page [ {} / {} ]'.format(page_index + 1, max_index + 1),
        value='\u200b', inline=False)
    embed.set_footer(
        text="{} | ID {} | AID {}".format(
            summoner.region.upper(), summoner.summoner_id, summoner.account_id),
        icon_url=REGION_IMAGES.get(summoner.region, UNKNOWN_EMOJI_URL))
    return embed, page_index


def _clean_matchlist(bot, matchlist, matchlist_data, invoker):
    """Formats the given list of raw matches for the matchlist table."""
    cleaned_matches = []
    for match_blurb, match_data in zip(matchlist, matchlist_data):

        if isinstance(match_data, BotException):
            logger.debug("Ratelimited!")
            cleaned_matches.append({
                'game_mode': -1,
                'champion': [match_blurb['champion'], 0],
                'kda': '?',
                'status': None,
                'spells': [-1, -1],
                'end_time': 0,
            })
            continue

        total_kills = 0
        for player in match_data['participants']:
            total_kills += player['stats']['kills']
        total_kills = 1 if total_kills <= 0 else total_kills
        champion_data = [match_blurb['champion'], 0]

        for participant in match_data['participantIdentities']:
            if ('player' in participant and
                    participant['player']['currentAccountId'] == invoker.account_id):
                player = match_data['participants'][participant['participantId']-1]
                stats = player['stats']
                win = stats['win']
                spells = [player['spell1Id'], player['spell2Id']]
                kills, deaths, assists = (stats['kills'], stats['deaths'], stats['assists'])
                value = (kills + assists) / (1 if deaths == 0 else deaths)
                participation = '{:.1f}%'.format(100 * (kills + assists) / total_kills)
                kda = '{}/{}/{} ({:.2f} | {})'.format(kills, deaths, assists, value, participation)
                champion_data[1] = stats['champLevel']
                break
        else:
            kda, spells, win = '?', [-1, -1], True  # Benefit of the doubt

        cleaned_matches.append({
            'game_mode': match_data['queueId'],
            'champion': champion_data,
            'kda': kda,
            'status': win,
            'spells': spells,
            'end_time': int(match_blurb['timestamp']/1000 + match_data['gameDuration'])
        })

    return cleaned_matches


def _get_matchlist_entries(bot, clean_matchlist):
    """Returns a list of 2-column rows for the fields of a cleaned matchlist embed."""
    # Number | Selection | Win | Type ||| Champion | Spells | KDA
    rows = []
    
    for index, match in enumerate(clean_matchlist):
        entry = []

        if match['status'] is None:  # Ratelimited result
            entry.append('`[{: <2}]` | {} | {}'.format(
                index + 1, UNKNOWN_EMOJI, "**`[Ratelimited!]`**"))
            entry.append('| {0} `? \u200b` | {0}{0} | {1}'.format(UNKNOWN_EMOJI, match['kda']))

        else:
            win_text = 'Won' if match['status'] else 'Lost'
            time_delta = utilities.get_time_string(time.time() - match['end_time'], text=True)

            entry.append('`[{: <2}]` | [{}](# "{}") | {}'.format(
                index + 1,
                '🇼' if match['status'] else '🇱',
                "{} {} ago".format(win_text, time_delta),
                MODES.get(match['game_mode'], 'Unknown')
            ))
            entry.append('| {} `{: <2}\u200b` | {}{} | {}'.format(
                CHAMPION_EMOJIS.get(match['champion'][0], UNKNOWN_EMOJI),
                match['champion'][1],
                SPELL_EMOJIS.get(match['spells'][0], UNKNOWN_EMOJI),
                SPELL_EMOJIS.get(match['spells'][1], UNKNOWN_EMOJI),
                match['kda']
            ))

        rows.append(entry)

    return rows


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
        if isinstance(e, HTTPError):
            if e.response.status_code == 404:
                raise CBException("No matches available.")
            else:
                handle_lol_exception(e)
        else:
            raise e
    return match_list, match_type


async def challenge(bot, context):
    summoners = context.arguments[:2]
    champions = context.arguments[2:]

    # Calculate score
    rank_points = []
    mastery_futures = []
    for summoner, champion in zip(summoners, champions):
        if summoner.tier == 'Unranked':
            rank_points.append(CHALLENGE_POINTS['Unranked'])
        elif summoner.tier in ('Master', 'Challenger'):
            rank_points.append(CHALLENGE_POINTS['Master/Challenger'] + summoner.lp/87)
        else:
            rank_points.append(CHALLENGE_POINTS[summoner.tier][summoner.rank])
        mastery_futures.append(future(
            WATCHER.champion_mastery.by_summoner_by_champion,
            PLATFORMS[summoner.region], summoner.summoner_id, champion['id']))

    total = 0
    scores = []
    masteries = []
    results = await utilities.parallelize(mastery_futures, return_exceptions=True)
    for result, points in zip(results, rank_points):
        if isinstance(result, HTTPError):
            if result.response.status_code == 404:
                mastery_data = (1, 1)
            else:
                handle_lol_exception(result)
        else:
            mastery_data = (result['championLevel'], result['championPoints'])
        score = points * mastery_data[0] * math.log1p(mastery_data[1])
        total += score
        scores.append(score)
        masteries.append(mastery_data)

    # Edit embed
    embed = discord.Embed(title="Challenge")
    embed.add_field(name='', value='')
    embed.add_field(name='\u200b', value='\u200b\u3000\u3000\u3000:vs:')
    embed.add_field(name='', value='')
    for summoner, champion, mastery, score in zip(summoners, champions, masteries, scores):
        rows = []
        opgg_link = 'https://{}.op.gg/summoner/userName={}'.format(
            summoner.region, urllib.parse.quote_plus(summoner.summoner_name))
        rows.append('`[{: <2}]` | [{}]({})'.format(
            summoner.shorthand_tier, summoner.summoner_name, opgg_link))
        rows.append('[{0}](# "Level: {1} | Points: {2}") ( {1} | {2} )'.format(
            CHAMPION_EMOJIS.get(champion['id'], UNKNOWN_EMOJI), *mastery))
        rows.append('{0}Win chance: {1:.2f}%{0}'.format(
            '**' if score == max(scores) else '', 100 * score / total))

        edit_index = 0 if summoners.index(summoner) == 0 else 2
        embed.set_field_at(edit_index, name='\u200b', value='\n'.join(rows))

    # Calculate summary
    random_value = random.random() * total
    winner_name = (summoners[0] if random_value < scores[0] else summoners[1]).summoner_name
    embed.add_field(
        name='\u200b', value='The RNG gods rolled: {:.1f}\nThe winner is **{}**!'.format(
            random_value, winner_name))

    return Response(embed=embed)


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
        return Response(content="Region set to {} {}.{}".format(
            region.upper(), REGION_EMOJIS.get(region, UNKNOWN_EMOJI), last_region_message))
    else:
        data.remove(bot, __name__, 'region', guild_id=context.guild.id, safe=True)
        return Response(content="Region reset to NA {}.{}".format(
            REGION_EMOJIS.get('na', UNKNOWN_EMOJI), last_region_message))


def _build_match_embed(match):
    """Builds an embed from the given match"""
    opgg_template = 'https://{}.op.gg/summoner/userName={{}}'.format(match['region'])
    finished = match['finished']
    obfuscated = match['obfuscated']

    # Setup team templates
    if finished:
        blue_won = match['teams']['blue']['winner']
        blue_status, red_status = (' [WON]', ' [LOST]') if blue_won else (' [LOST]', ' [WON]')
        colour = discord.Colour(0x55acee if blue_won else 0xdd2e44)
        embed_padding = '\u3000'*50 + '\u200b'
    else:
        blue_status, red_status, embed_padding = '', '', ''
        colour = discord.Colour(0x77b255)

    embed = discord.Embed(
        colour=colour,
        timestamp=datetime.datetime.utcfromtimestamp(match['timestamp']),
        title='Match' if finished else 'Ongoing Match (spectate)',
        description='{}: {}\nType: {}\n\u200b{}'.format(
            'Duration' if finished else 'Current Time',
            utilities.get_time_string(match['game_time']),
            MODES.get(match['game_mode'], 'Unknown'),
            embed_padding))

    if not finished:
        embed.url = match['spectate']

    embed.add_field(name='', value='', inline=False)
    embed.add_field(name='Summoner', value='')
    embed.add_field(name='Champion | Spells | KDA', value='')
    if finished:
        embed.add_field(name='Multi | Damage | CS | Gold', value='')
    embed.add_field(name='', value='', inline=False)
    embed.add_field(name='Summoner', value='')
    embed.add_field(name='Champion | Spells | KDA', value='')
    if finished:
        embed.add_field(name='Multi | Damage | CS | Gold', value='')

    embed.set_footer(
        text="{} | ID {} | Started".format(match['region'].upper(), match['id']),
        icon_url=REGION_IMAGES.get(match['region'], UNKNOWN_EMOJI_URL))

    for team_name, team in match['teams'].items():
        # Summoner ||| Champion | Spells | KDA ||| Multi | Damage | CS | Gold
        columns = [[], [], []]
        team_kda = [0, 0, 0]
        for player in team['players']:

            for index in range(3):
                team_kda[index] += player['kda_values'][index]

            # Summoner column
            is_target = player['summoner_name'] == match['invoker_name']
            indicator = 'white' if is_target else 'black'
            if obfuscated and not is_target:
                player_name = '`[Hidden]`'
                player_rank = player['rank'][0] + '?'
            else:
                opgg_link = opgg_template.format(urllib.parse.quote_plus(player['summoner_name']))
                player_name = '[{}]({})'.format(player['summoner_name'], opgg_link)
                player_rank = '{: <2}'.format(player['rank'])
            columns[0].append(':{}_small_square:`[{}]` | {}'.format(
                indicator, player_rank, player_name))

            # Champion and KDA column
            champion = CHAMPION_EMOJIS.get(player['champion'][0], UNKNOWN_EMOJI)
            spells = [SPELL_EMOJIS.get(it, UNKNOWN_EMOJI) for it in player['spells']]

            # If the match is finished, add the third column for Damage, CS, and Gold
            if finished:
                columns[1].append('| {} `{: <2}\u200b` | {}{} | {}'.format(
                    champion, player['champion'][1], *spells, player['kda']))
                kill_tier, frequency = player['kill_tier']
                if frequency:
                    tier_name = ['double', 'triple', 'quadra', 'penta', 'unreal'][kill_tier - 1]
                    tier_text = "{} {}-kill{}".format(
                        frequency, tier_name, '' if frequency == 1 else 's')
                    tier_emoji = NUMBER_EMOJIS[kill_tier + 1]
                else:
                    tier_text = "No multi-kills"
                    tier_emoji = ':stop_button:'
                multi_kill = '[{}](# "{}")'.format(tier_emoji, tier_text)
                columns[2].append(
                    '| {} | `{: <6}\u200b` | `{: <3}\u200b` | `{: <5}\u200b`'.format(
                        multi_kill, player['damage'], player['cs'], player['gold']))
            else:
                columns[1].append('| {} | {}{}'.format(champion, *spells))

        # Build team information (bans, team KDA)
        team_blurb = []
        if team_name == 'blue':
            field_index = 0
            team_title = ':large_blue_circle: | Blue Team{}'.format(blue_status)
        else:
            field_index = 4 if finished else 3
            team_title = ':red_circle: | Red Team{}'.format(red_status)
        if team['bans']:
            team_blurb.append('Bans: [{}]'.format(
                ''.join(CHAMPION_EMOJIS.get(it, UNKNOWN_EMOJI) for it in team['bans'])))

        # Add additional team details if the match is finished
        if finished:
            # Get BDT
            team_bdt_emojis = [BDT_EMOJIS[team_name[0] + it] for it in ('b', 'd', 't')]
            team_blurb.append('[{0[0]}{1[0]} | {0[1]}{1[1]} | {0[2]}{1[2]}]'.format(
                team_bdt_emojis, team['bdt']))
            team_blurb.append('Team KDA: [{0[0]}/{0[1]}/{0[2]}]'.format(team_kda))
            embed.set_field_at(
                field_index+3, name='Multi | Damage | CS | Gold',
                value='\u200b' + '\n'.join(columns[2]))
            column_2_name = 'Champion | Spells | KDA'
        else:
            column_2_name = 'Champion | Spells'

        # Apply changes
        embed.set_field_at(
            field_index, name=team_title, inline=False,
            value='\u200b{}'.format(' | '.join(team_blurb)))
        embed.set_field_at(
            field_index+1, name='Summoner', value='\u200b' + '\n'.join(columns[0]))
        embed.set_field_at(
            field_index+2, name=column_2_name, value='\u200b' + '\n'.join(columns[1]))

    return embed


async def _get_static_data(bot):
    """Get static data returned as a tuple."""
    try:
        #assert False  # TODO: Remove debug
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


@plugins.listen_for('bot_on_ready_boot')
async def setup_client(bot):
    """Sets up the client and gets the LoL emojis."""
    # Load champion/spell emojis
    global CHAMPION_EMOJIS, SPELL_EMOJIS, BDT_EMOJIS, WATCHER, CHAMPIONS, SPELLS
    emoji_file_location = utilities.get_plugin_file(bot, 'lol_emojis.json', safe=False)
    with open(emoji_file_location, 'r') as emoji_file:
        emoji_data = json.load(emoji_file)
    for key, value in emoji_data['champions']['id'].items():
        CHAMPION_EMOJIS[int(key)] = value
    for key, value in emoji_data['spells']['id'].items():
        SPELL_EMOJIS[int(key)] = value
    for color, symbols in emoji_data['bdt'].items():
        for symbol, value in symbols.items():
            BDT_EMOJIS[color[0] + symbol[0]] = value

    # Obtain all static data required
    watcher = RiotWatcher(configurations.get(bot, __name__, key='token'))
    configurations.redact(bot, __name__, 'token')

    # Get static data
    WATCHER = watcher
    CHAMPIONS, SPELLS = await _get_static_data(bot)

    # Add external emojis permission
    permissions = {'external_emojis': "Shows champion and spell icons."}
    utilities.add_bot_permissions(bot, __name__, **permissions)

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
UNKNOWN_EMOJI_URL = "https://i.imgur.com/UF2cwhX.png"

CHAMPION_EMOJIS, SPELL_EMOJIS, BDT_EMOJIS = {}, {}, {}

REGION_IMAGES = {
    'br':   'https://i.imgur.com/FJ6ahZ0.png',
    'eune': 'https://i.imgur.com/5gVIRDD.png',
    'euw':  'https://i.imgur.com/5gVIRDD.png',
    'kr':   'https://i.imgur.com/3y1Ytbh.png',
    'lan':  'https://i.imgur.com/C3NOAPU.png',
    'las':  'https://i.imgur.com/5eMM4X6.png',
    'na':   'https://i.imgur.com/YjkbwMB.png',
    'oce':  'https://i.imgur.com/qh2a85S.png',
    'ru':   'https://i.imgur.com/tgHNo8A.png',
    'tr':   'https://i.imgur.com/YnTJHXT.png',
    'jp':   'https://i.imgur.com/kNeRbMn.png'
}

REGION_EMOJIS = {
    'br':   ':flag_br:',
    'eune': ':flag_eu:',
    'euw':  ':flag_eu:',
    'kr':   ':flag_kr:',
    'lan':  ':flag_mx:',
    'las':  ':flag_co:',
    'na':   ':flag_us:',
    'oce':  ':flag_au:',
    'ru':   ':flag_ru:',
    'tr':   ':flag_tr:',
    'jp':   ':flag_jp:'
}

NUMBER_EMOJIS = [
    ':zero:', ':one:', ':two:', ':three:', ':four:', ':five:',
    ':six:', ':seven:', ':eight:', ':nine:', ':keycap_ten:'
]

CHALLENGE_POINTS = {
    'Unranked': 2.66,

    'Bronze': {
        'V':    1.00,
        'IV':   1.33,
        'III':  1.66,
        'II':   2.00,
        'I':    2.33
    },
    'Silver': {
        'V':    2.66,
        'IV':   3.00,
        'III':  3.33,
        'II':   3.66,
        'I':    4.00
    },
    'Gold': {
        'V':    4.33,
        'IV':   4.66,
        'III':  5.00,
        'II':   5.33,
        'I':    5.66
    },
    'Platinum': {
        'V':    6.0,
        'IV':   6.33,
        'III':  6.66,
        'II':   7.00,
        'I':    7.33
    },
    'Diamond': {
        'V':    7.5,
        'IV':   8,
        'III':  8.5,
        'II':   9,
        'I':    9.5
    },
    'Master/Challenger': 10.2
}
