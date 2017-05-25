import discord
import logging
import asyncio
import functools

from enum import Enum
from time import time
from youtube_dl import YoutubeDL
from tinytag import TinyTag

from jshbot import utilities, configurations, data
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Music playlist'
uses_configuration = True

class States(Enum):
    PLAYING, PAUSED, STOPPED, LOADING = range(4)


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'playlist', SubCommands(
            ('?all', '(all)', 'Check the current playlist. Only the most '
             'recent 10 entries will be shown unless \'all\' is included.'),
            ('info ^', 'info <index>', 'Retrieves the song information of '
             'the given entry.'),
            ('add ^', 'add <url>', 'Adds a song to the playlist.'),
            ('remove ^', 'remove <index>', 'Removes a song from the '
             'playlist.'),
            ('play', 'play', 'Starts playing the playlist in your channel.'),
            ('pause', 'pause', 'Stops playback.'),
            ('skip', 'skip', 'Skips current song.'),
            ('configure ?volume: ?cutoff: ?duration:', 'configure '
             '(volume <percent>) (cutoff <seconds>) (duration <seconds>)',
             'Configures the cutoff threshold, and '
             'the fallback song duration. For example with a cutoff of 300 '
             'seconds and duration of 200 seconds, if a song is over 300 '
             'seconds long, only the first 200 seconds will play.')),
        shortcuts=Shortcuts(
            ('p', '{}', '&', '(<arguments>)', '(<arguments>)')),
        description='Play YouTube links.', group='music', allow_direct=False))

    return new_commands


def _get_playlist_listing(bot, state, now_playing, options, server_id):
    response = ''
    if state is not States.STOPPED:
        if state is States.PAUSED:
            state_text = "Paused"
            progress = now_playing['progress']
        else:
            state_text = "Playing"
            progress = (
                now_playing['progress'] + time() - now_playing['start'])
        progress_text = '{} / {}'.format(
            utilities.get_time_string(progress),
            utilities.get_time_string(now_playing['duration']))
        response = "**`[ {} ]`** {}: {}\n\n".format(
            progress_text, state_text, now_playing['title'])
    playlist_info = data.get(
        bot, __name__, 'playlist', server_id=server_id)

    if playlist_info:
        total_tracks = len(playlist_info)
        total_time = sum(it['duration'] for it in playlist_info)
        display_maxed = total_tracks > 10
        response += '{} track{} (runtime of {}):\n'.format(
            total_tracks, '' if total_tracks == 1 else 's',
            utilities.get_time_string(total_time, text=True))
        if 'all' not in options:
            playlist_info = playlist_info[:10]
        info = []
        for index, entry in enumerate(playlist_info):
            duration = utilities.get_time_string(entry['duration'])
            title = entry['title']
            if len(title) > 50:
                title = title[:50] + ' **...**'
            info.append('**`{}`**: ({}) *{}*'.format(
                index + 1, duration, title))
        if 'all' not in options and display_maxed:
            info.append('\n***...***')
        response += '\n'.join(info)
    else:
        response += "The playlist is empty."

    return response


def _configure(bot, options, server_id):
    response = ''
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    default_duration_limit = configurations.get(
        bot, __name__, key='max_duration')
    cutoff = data.get(
        bot, __name__, 'cutoff',
        server_id=server_id, default=default_cutoff)
    duration = data.get(
        bot, __name__, 'duration',
        server_id=server_id, default=default_duration_limit)

    if len(options) == 1:
        cutoff = default_cutoff
        duration = default_duration_limit
        response = (
            "Cutoff and duration limit reset to {} and {} seconds "
            "respectively.".format(cutoff, duration))

    if 'volume' in options:
        try:
            volume = float(options['volume'].strip('%')) / 100
            if not 0.1 <= volume <= 2:
                raise ValueError
        except ValueError:
            raise BotException(
                EXCEPTION, "Volume must be between 10% and 200% inclusive.")
        data.add(
            bot, __name__, 'volume', volume, server_id=server_id)
        player = utilities.get_player(bot, server_id)
        if player:
            player.volume = volume
        response += "Volume set to {}%.\n".format(volume * 100)

    if 'cutoff' in options:
        try:
            cutoff = int(options['cutoff'])
            if not 10 <= cutoff <= default_cutoff:
                raise ValueError
        except ValueError:
            raise BotException(
                EXCEPTION,
                "Cutoff must be between 10 and {} inclusive.".format(
                    default_cutoff))
        response += "Cutoff set to {} seconds.\n".format(cutoff)

    if 'duration' in options:
        try:
            duration = int(options['duration'])
            if not 10 <= duration <= default_duration_limit:
                raise ValueError
        except ValueError:
            raise BotException(
                EXCEPTION,
                "Duration must be between 10 and {} inclusive.".format(
                    default_duration_limit))
        response += "Duration limit set to {} seconds.".format(duration)

    data.add(bot, __name__, 'cutoff', cutoff, server_id=server_id)
    data.add(
        bot, __name__, 'duration', duration, server_id=server_id)
    return response


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    now_playing = data.get(
        bot, __name__, 'now_playing', server_id=message.server.id,
        volatile=True, default={'state': States.STOPPED})

    voice_client = bot.voice_client_in(message.server)
    if voice_client is None:
        now_playing['state'] = States.STOPPED
    state = now_playing['state']
    print("The state is:", state)

    if blueprint_index == 0:  # Playlist listing
        response = _get_playlist_listing(
            bot, state, now_playing, options, message.server.id)

    elif blueprint_index == 1:  # Info
        # TODO: Implement
        response = "Not in quite yet..."

    elif blueprint_index == 2:  # Add
        # TODO: Add playlist length limit
        response = "Checking the length of the audio..."
        message_type = 3
        extra = ('duration_check', arguments[0], message.author.id)

    elif blueprint_index == 3:  # Remove
        playlist_info = data.get(
            bot, __name__, 'playlist',
            server_id=message.server.id, save=True, default=[])
        if not playlist_info:
            raise BotException(EXCEPTION, "Playlist is empty.")
        try:
            index = int(arguments[0]) - 1
            if not 0 <= index <= len(playlist_info) - 1:
                raise ValueError
        except ValueError:
            raise BotException(
                EXCEPTION,
                "Invalid index. Must be between 1 and {} inclusive.".format(
                    len(playlist_info)))
        is_mod = data.is_mod(bot, message.server, message.author.id)
        track_info = playlist_info[index]
        if track_info['userid'] != message.author.id and not is_mod:
            raise BotException(
                EXCEPTION,
                "You must be the user who added "
                "the entry, or a bot moderator.")
        playlist_info.pop(index)
        response = "Removed {} from the queue".format(track_info['title'])

    elif blueprint_index == 4:  # Play
        if state in (States.PLAYING, States.LOADING):
            raise BotException(EXCEPTION, "Playlist is already playing.")
        else:
            response = await _begin_playback(
                bot, message.author, resume=state is States.PAUSED)

    elif blueprint_index == 5:  # Pause
        if state is States.PLAYING:
            playlist_task = data.get(
                bot, __name__, 'playlist_task',
                server_id=message.server.id, volatile=True)
            playlist_task.cancel()
            player = utilities.get_player(bot, message.server.id)
            player.pause()
            now_playing['progress'] += time() - now_playing['start']
            now_playing['state'] = States.PAUSED
            response = "Paused."
        else:
            if state is States.PAUSED:
                exception_message = "Playlist is already paused."
            elif state is States.LOADING:
                exception_message = "Playlist is loading the next track."
            else:
                exception_message = "Playlist is stopped."
            raise BotException(EXCEPTION, exception_message)

    elif blueprint_index == 6:  # Skip
        if state in (States.PLAYING, States.PAUSED):
            if state is States.PLAYING:  # Cancel task first
                playlist_task = data.get(
                    bot, __name__, 'playlist_task',
                    server_id=message.server.id, volatile=True)
                playlist_task.cancel()
            response = await _next_track(bot, message.server, 0)
        else:
            if state is States.STOPPED:
                exception_message = "Playlist is stopped."
            else:
                exception_message = "Playlist is loading the next track."
            raise BotException(EXCEPTION, exception_message)

    elif blueprint_index == 7:  # Configure
        response = _configure(bot, options, message.server.id)

    return (response, tts, message_type, extra)


async def _begin_playback(bot, member, resume=False):
    playlist_info = data.get(
        bot, __name__, 'playlist', server_id=member.server.id)
    if not playlist_info:
        raise BotException(EXCEPTION, "Playlist is empty.")
    if not resume:
        is_mod = data.is_mod(bot, member.server, member.id)
        voice_channel = member.voice_channel
        if voice_channel is None:
            raise BotException(
                EXCEPTION, "You must be in a voice channel first.")
        voice_client = await utilities.join_and_ready(
            bot, voice_channel, is_mod=is_mod)

    delay = 0
    if resume:
        now_playing = data.get(
            bot, __name__, 'now_playing', server_id=member.server.id,
            volatile=True, default={'state': States.STOPPED})
        player = utilities.get_player(bot, member.server.id)

        default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
        default_duration_limit = configurations.get(
            bot, __name__, key='max_duration')
        server_id = member.server.id
        cutoff = data.get(
            bot, __name__, 'cutoff',
            server_id=server_id, default=default_cutoff)
        duration_limit = data.get(
            bot, __name__, 'duration',
            server_id=server_id, default=default_duration_limit)

        if now_playing['duration'] > cutoff:
            duration = duration_limit
        else:
            duration = now_playing['duration']
        now_playing['start'] = time()
        delay = duration - now_playing['progress']
        player.resume()
        now_playing['state'] = States.PLAYING

    return await _next_track(bot, member.server, delay)


async def _next_track(bot, server, duration):
    print("Sleeping for", duration, "seconds until next song.", time())
    await asyncio.sleep(duration)
    print("Done sleeping for", duration, "seconds.", time())
    player = utilities.get_player(bot, server.id)

    # TODO: Remove testing
    now_playing = data.get(
        bot, __name__, 'now_playing', server_id=server.id,
        volatile=True, default={'state': States.STOPPED})
    state = now_playing['state']
    print("_next_track state:", state)
    if state is States.PAUSED:
        print("THIS SHOULD NEVER HAPPEN. WHY DID THIS HAPPEN.")
        print("RETURNING FOR SAFETY")
        return

    if player:
        print("STOPPING THE PLAYER RIGHT THE FUCK NOW")  # TODO: Remove debug
        player.stop()
    playlist_info = data.get(bot, __name__, 'playlist', server_id=server.id)
    voice_client = bot.voice_client_in(server)
    if not playlist_info or voice_client is None:  # Stop playlist
        print("The playlist is stopping!")
        now_playing = data.get(
            bot, __name__, 'now_playing', server_id=server.id, volatile=True)
        now_playing['state'] = States.STOPPED
        utilities.set_player(bot, server.id, None)
        return "The playlist has stopped."

    track_info = playlist_info.pop(0)
    track_info['state'] = States.LOADING
    track_info['progress'] = 0
    await _play_and_continue(bot, server, voice_client, track_info)
    duration = utilities.get_time_string(track_info['duration'])
    return "Now playing {0[title]} ({1})".format(track_info, duration)


async def _play_and_continue(bot, server, voice_client, track_info):
    """Plays the track and sets up the next track."""
    track_info['start'] = time()
    data.add(
        bot, __name__, 'now_playing', track_info,
        server_id=server.id, volatile=True)
    ytdl_options = {'noplaylist': True}
    player = await voice_client.create_ytdl_player(
        track_info['url'], ytdl_options=ytdl_options)
    # TODO: Caching
    # download_url = player.download_url
    # file_directory = await data.add_to_cache(bot, download_url)
    # player = voice_client.create_ffmpeg_player(file_directory)
    player.volume = data.get(
        bot, __name__, 'volume', server_id=server.id, default=1.0)
    player.start()
    track_info['state'] = States.PLAYING
    utilities.set_player(bot, server.id, player)

    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    default_duration_limit = configurations.get(
        bot, __name__, key='max_duration')
    cutoff = data.get(
        bot, __name__, 'cutoff', server_id=server.id, default=default_cutoff)
    duration_limit = data.get(
        bot, __name__, 'duration',
        server_id=server.id, default=default_duration_limit)
    if track_info['duration'] > cutoff:
        final_duration = duration_limit
    else:
        final_duration = track_info['duration']
    playlist_task = asyncio.ensure_future(
        _next_track(bot, server, final_duration))
    bot.extra = playlist_task
    data.add(
        bot, __name__, 'playlist_task', playlist_task,
        server_id=server.id, volatile=True)


async def handle_active_message(bot, message_reference, extra):
    if extra[0] == 'duration_check':
        hard_cutoff = configurations.get(bot, __name__, key='hard_cutoff')
        default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
        default_duration_limit = configurations.get(
            bot, __name__, key='max_duration')
        server_id = message_reference.server.id
        cutoff = data.get(
            bot, __name__, 'cutoff',
            server_id=server_id, default=default_cutoff)
        duration_limit = data.get(
            bot, __name__, 'duration',
            server_id=server_id, default=default_duration_limit)
        options = {'format': 'worstaudio/worst', 'noplaylist': True}
        downloader = YoutubeDL(options)
        try:
            info = await utilities.future(
                downloader.extract_info, extra[1], download=False)
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
            raise BotException(
                EXCEPTION, "Failed to get duration from the URL.", e=e)

        if duration > hard_cutoff:
            raise BotException(
                EXCEPTION,
                "Song is longer than the hard cutoff of {} seconds.".format(
                    hard_cutoff))

        response = "Song added to playlist."
        if duration > cutoff:
            response = (
                "\nSong is longer than the cutoff length ({} seconds), so "
                "only the first {} seconds will be played.".format(
                    cutoff, duration_limit))

        playlist_info = data.get(
            bot, __name__, 'playlist', server_id=server_id,
            default=[], create=True, save=True)
        playlist_info.append({
            'url': extra[1],
            'title': info.get('title', 'Unknown'),
            'duration': duration,
            'timestamp': time(),
            'userid': extra[2]
        })

        await bot.edit_message(message_reference, new_content=response)
