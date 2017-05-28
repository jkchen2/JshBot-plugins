import discord
import logging
import asyncio
import functools
import time

from enum import Enum
from youtube_dl import YoutubeDL
from tinytag import TinyTag

from jshbot import utilities, configurations, data
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '0.2.0'
EXCEPTION = 'Music playlist'
uses_configuration = True

class States(Enum):
    PLAYING, PAUSED, STOPPED, LOADING = range(4)

class Modes(Enum):
    PLAYLIST, QUEUE = range(2)


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'playlist', SubCommands(
            ('?play', '(play)', 'Opens the playlist interface. Begins '
             'playback if specified.'),
            ('tracks', 'tracks', 'View the entire playlist.'),
            ('info ^', 'info <index>', 'Retrieves the song information of '
             'the given entry.'),
            ('add ^', 'add <url>', 'Adds a song to the playlist.'),
            ('remove ^', 'remove <index>', 'Removes a song from the '
             'playlist.'),
            ('volume ^', 'volume <percent>', 'Sets the player volume to the '
             'given percentage. Must be between 10% and 200%.'),
            ('configure ?threshold: ?cutoff:', 'configure '
             '(threshold <seconds>) (cutoff <seconds>)',
             'Configures the track duration threshold, and '
             'the fallback song cutoff. For example with a threshold of 300 '
             'seconds and cutoff of 200 seconds, if a song is over 300 '
             'seconds long, only the first 200 seconds will play.')),
        shortcuts=Shortcuts(
            ('p', '{}', '&', '(<arguments>)', '(<arguments>)')),
        description='Play YouTube links.', group='music', allow_direct=False))

    return new_commands


class MusicPlayer():

    def __init__(self, bot, message, author, voice_channel, autoplay=False):

        # Discord information
        self.bot = bot
        self.message = message
        self.channel = message.channel
        self.voice_channel = voice_channel
        self.server = message.server
        self.author = author
        self.voice_client = None
        self.player = None

        # Player information
        self.state = States.LOADING
        self.now_playing = None
        self.notification = None
        self.page = 0
        self.progress = 0
        self.start_time = 0
        self.mode = data.get(
            self.bot, __name__, 'mode',
            server_id=self.server.id, default=Modes.QUEUE)
        if self.mode is Modes.QUEUE:
            self.track_index = 0
        else:
            self.track_index = data.get(
                self.bot, __name__, 'last_index',
                server_id=self.server.id, default=0)
        self.update_config()


        # Update tasks
        self.timer_task = None  # Player timer
        self.command_task = None  # Waits for reaction commands
        self.progress_task = None  # Refreshes the progress bar

        # Build interface
        asyncio.ensure_future(self._connect(autoplay=autoplay))

    def update_config(self):
        default_threshold = configurations.get(
            self.bot, __name__, key='max_threshold')
        default_cutoff = configurations.get(
            self.bot, __name__, key='max_cutoff')
        self.threshold = data.get(
            self.bot, __name__, 'threshold',
            server_id=self.server.id, default=default_threshold)
        self.cutoff = data.get(
            self.bot, __name__, 'cutoff',
            server_id=self.server.id, default=default_cutoff)
        self.volume = data.get(
            self.bot, __name__, 'volume',
            server_id=self.server.id, default=1.0)

    async def _connect(self, autoplay=False):
        is_mod = data.is_mod(self.bot, self.server, self.author.id)
        try:
            self.voice_client = await utilities.join_and_ready(
                self.bot, self.voice_channel, is_mod=is_mod, reconnect=True)
        except Exception as e:
            try:
                await self.bot.edit_message(
                    self.message,
                    new_content=(
                        'Failed to start the player interface.'
                        '\n\n{}'.format(e)))
            except:
                print("Error edit failed.")
            self.state = States.STOPPED
            return
        await self._build_interface()
        # Start playback if necessary
        if autoplay:
            self.autoplay_task = asyncio.ensure_future(self._autoplay())

    async def _autoplay(self):
        print("Using autoplay")
        safety_timeout = 0
        while self.state is States.LOADING:
            if safety_timeout > 30:
                raise BotException(EXCEPTION, "Autoplay failed.")
            await asyncio.sleep(0.5)
            safety_timeout += 0.5
        asyncio.ensure_future(self.play())
        print("Autoplay finished")

    def _get_tracklist(self):
        return data.get(
            self.bot, __name__, 'tracklist', server_id=self.server.id,
            default=[], create=True, save=True)

    async def update_state(self):
        if self.state is States.STOPPED:
            return
        voice_client = self.bot.voice_client_in(self.server)
        if self.now_playing:
            player_check = utilities.get_player(self.bot, self.server.id)
        else:
            player_check = None
        if (voice_client is None or
                voice_client.channel != self.voice_channel or
                self.player != player_check):
            print("update_state detected an unstopped instance. Stopping now.")
            await self.stop()

    async def set_new_message(self, message):
        if self.command_task:
            self.command_task.cancel()
        if self.progress_task:
            self.progress_task.cancel()
        try:
            await self.bot.delete_message(self.message)
        except Exception as e:
            print("Couldn't delete original message:", e)
        self.message = message
        asyncio.ensure_future(self._build_interface())

    async def _build_interface(self):
        embed = discord.Embed()
        embed.add_field(  # Title
            name=':arrows_counterclockwise: **[]**',
            value='**`[{}]` [ 0:00 / 0:00 ]**'.format('-'*30),
            inline=False)
        embed.add_field(name='---', value='---', inline=False)  # Info
        embed.add_field(name='---', value='---', inline=False)  # Listeners
        embed.add_field(name='---', value='---\n'*6, inline=False)  # Tracklist
        embed.add_field(name='---', value='---')  # Notification
        self.embed = embed
        await self.bot.edit_message(self.message, embed=embed)
        self.command_task = asyncio.ensure_future(self._command_listener())

    def _build_hyperlink(self, track):
        title = track['title']
        if len(title) > 40:
            title = title[:40] + ' **...**'
        return '[{}]({} "{} (added by {})")'.format(
            title, track['url'], track['title'],
            data.get_member(self.bot, track['userid']))

    async def _progress_loop(self):  # Refreshes the progress bar
        await asyncio.sleep(5)
        while True:
            await self.update_state()
            if self.state is States.PLAYING:
                asyncio.ensure_future(self.display_title())
                await asyncio.sleep(5)
            elif self.state in (States.PAUSED, States.LOADING):
                # TODO: Implement idle timeout
                await asyncio.sleep(1)
            else:  # Stopped
                print(
                    "Progress loop wasn't canceled for "
                    "some reason. Stopping loop...")
                return

    async def display_title(self):
        if self.state is States.PLAYING:
            progress = self.progress + (time.time() - self.start_time)
            status_icon = ':arrow_forward:'
        elif self.state is States.PAUSED:
            progress = self.progress
            status_icon = ':pause_button:'
        else:
            progress = 0
            status_icon = ':arrows_counterclockwise:'
        if self.now_playing:
            title = self.now_playing['title']
            if len(title) > 40:
                title = title[:40] + ' **...**'
            duration = self.now_playing['duration']
        else:
            title = '---'
            duration = 0
        new_name = '{} **[{}]**'.format(status_icon, title)
        percentage = 0 if duration == 0 else progress/duration
        progress_bar = '|' * int(30 * percentage)
        new_value = '**`[{:-<30}]` [ {} / {} ]**'.format(
            progress_bar, utilities.get_time_string(progress),
            utilities.get_time_string(duration))

        if self.state is States.PLAYING:
            color = discord.Color(0x3b88c3)
        elif self.state is States.PAUSED:
            color = discord.Color(0xccd6dd)
        else:
            color = discord.Color(0xffab00)
        self.embed.color = color

        # TODO: Seek better thumbnail solution (consider thumbnail message)
        '''
        if self.now_playing:
            thumbnail_test = self.now_playing['thumbnail']
            if self.embed.thumbnail.url != thumbnail_test:
                self.embed.set_thumbnail(url=thumbnail_test)
        else:
            self.embed.set_thumbnail(url='')
        '''
        '''
        if self.now_playing:
            thumbnail_test = self.now_playing['thumbnail']
            if self.embed.image.url != thumbnail_test:
                self.embed.set_image(url=thumbnail_test)
        else:
            self.embed.set_image(url='')
        '''

        self.embed.set_field_at(
            0, name=new_name, value=new_value, inline=False)
        await self.bot.edit_message(
            self.message, new_content=' ', embed=self.embed)

    async def display_info(self):
        # Listeners


        # Tracklist
        tracklist = self._get_tracklist()
        total_tracks = len(tracklist)
        total_duration = sum(it['duration'] for it in tracklist)
        total_pages = max(int((total_tracks + 4) / 5), 1)
        self.page = min(max(self.page, 0), total_pages - 1)
        displayed_tracks = tracklist[self.page * 5:(self.page * 5) + 5]

        info = []
        for index, entry in enumerate(displayed_tracks):
            duration = utilities.get_time_string(entry['duration'])
            entry_index = (self.page * 5) + index + 1
            info.append('**`{}`**: ({}) *{}*'.format(
                entry_index, duration, self._build_hyperlink(entry)))
        for index in range(5 - len(displayed_tracks)):
            info.append('---')
        info.append('Page [ {} / {} ]'.format(self.page+1, total_pages))
        new_value = '\n'.join(info)
        if total_tracks > 0:
            new_name = '{} track{} in queue (runtime of {}):'.format(
                total_tracks, '' if total_tracks == 1 else 's',
                utilities.get_time_string(total_duration, text=True))
        else:
            new_name = 'No tracks in queue'

        self.embed.set_field_at(
            3, name=new_name, value=new_value, inline=False)

        # Info
        if self.now_playing:
            # TODO: Possible remove redundant link portion?
            new_name = 'Info'
            time_ago = time.time() - self.now_playing['timestamp']
            new_value = 'Added by <@{}> {} ago [(Link)]({} "{}")'.format(
                self.now_playing['userid'],
                utilities.get_time_string(time_ago, text=True),
                self.now_playing['url'], self.now_playing['title'])
        else:
            new_name = '---'
            new_value = '---'

        if len(tracklist) == 0:
            next_index = -1
            new_value += '\n---'
        elif self.now_playing is None:
            next_index = 0
        elif self.track_index + 1 >= len(tracklist):
            next_index = 0
        else:
            if self.mode is Modes.PLAYLIST:
                next_index = self.track_index + 1
            else:
                next_index = 0
        
        if next_index != -1:
            next_track = tracklist[next_index]
            if self.mode is Modes.PLAYLIST:
                index_string = '[track {}] '.format(next_index + 1)
            else:
                index_string = ''
            if next_index >= 0:
                duration = utilities.get_time_string(
                    next_track['duration'])
                new_value += '\nUp next: {}({}) *{}*'.format(
                    index_string, duration, self._build_hyperlink(next_track))
        self.embed.set_field_at(
            1, name=new_name, value=new_value, inline=False)

        await self.bot.edit_message(self.message, embed=self.embed)

    async def display_notification(self, text=''):
        if text:
            self.notification = text
        elif not self.notification:
            self.notification = 'No notification.'
        self.embed.set_field_at(
            4, name='Notification:', value=self.notification)
        await self.bot.edit_message(self.message, embed=self.embed)

    async def _track_timer(self, sleeptime, use_skip=False):
        print("Sleeping for", sleeptime, "seconds. Time:", time.time())
        track_check = self.now_playing
        await asyncio.sleep(sleeptime)
        print("Finished sleeping for", sleeptime, "seconds. Time:", time.time())
        await self.update_state()
        if self.state is States.STOPPED or track_check != self.now_playing:
            print("The track timer resumed????????????????????????????????")
            return
        asyncio.ensure_future(self.play(skipped=use_skip))

    async def play(self, track_index=None, skipped=False):

        def _get_delay():  # Gets track delay with cutoff
            if self.now_playing['duration'] > self.threshold:
                duration = self.cutoff
                use_skip = self.now_playing
            else:
                duration = self.now_playing['duration']
                use_skip = False
            return (max(duration - self.progress, 0), use_skip)

        if self.state in (States.LOADING, States.STOPPED):
            return
        if (self.state is States.PAUSED and
                self.now_playing and self.progress and
                track_index is None):
            print("Resuming player...")
            self.state = States.PLAYING
            self.player.resume()
            self.start_time = time.time()
            self.timer_task = asyncio.ensure_future(
                self._track_timer(*_get_delay()))
            asyncio.ensure_future(self.display_title())
            print("Player resumed!")
            return

        tracklist = self._get_tracklist()
        if (len(tracklist) == 0 and
                not (track_index == -1 and self.state is States.PLAYING)):
            self.notification = "There are no more tracks in the queue."
            if self.player:
                self.player.stop()
            self.player = None
            self.now_playing = None
            self.progress = 0
            utilities.set_player(self.bot, self.server.id, self.player)
            self.state = States.PAUSED
            asyncio.ensure_future(self.display_title())
            asyncio.ensure_future(self.display_info())
            asyncio.ensure_future(self.display_notification())
            return
        if self.now_playing is None:  # First time startup
            pass  # Probably just leave this here?
        elif track_index is None:

            if self.mode is Modes.PLAYLIST:
                if self.track_index + 1 >= len(tracklist):
                    self.track_index = 0
                else:
                    self.track_index += 1

        else:  # Given track_index
            if (not (self.mode is Modes.QUEUE and track_index == -1) and
                    not 0 <= track_index < len(tracklist)):
                self.notification = (
                    "Index must be between 1 and {} inclusive.".format(
                        len(tracklist)))
                asyncio.ensure_future(self.display_notification())
                return
            if self.mode is Modes.PLAYLIST:
                self.track_index = track_index
            else:
                if track_index == -1:  # Repeat current
                    tracklist.insert(0, self.now_playing)
                elif track_index != 0:
                    track = tracklist.pop(track_index)
                    tracklist.insert(0, track)

        if self.mode is Modes.PLAYLIST:
            track = tracklist[self.track_index]
        else:
            track = tracklist.pop(0)

        # Setup the player
        print("Preparing to play the next track.")
        if self.state is States.PLAYING:
            if self.player:
                self.player.stop()
        if self.timer_task:
            self.timer_task.cancel()
        self.state = States.LOADING
        self.now_playing = track
        file_location = data.get_from_cache(
            self.bot, None, url=track['downloadurl'])
        if not file_location:
            asyncio.ensure_future(self.display_title())
            asyncio.ensure_future(self.display_info())
            print("Not found in cache. Downloading...")
            file_location = await data.add_to_cache(
                self.bot, track['downloadurl'])
            print("Download finished.")
        # TODO: Add exception handling
        self.player = self.voice_client.create_ffmpeg_player(file_location)
        self.player.volume = self.volume
        self.player.start()
        utilities.set_player(self.bot, self.server.id, self.player)
        self.progress = 0
        self.start_time = time.time()
        self.state = States.PLAYING
        self.timer_task = asyncio.ensure_future(
            self._track_timer(*_get_delay()))
        asyncio.ensure_future(self.display_title())
        asyncio.ensure_future(self.display_info())
        if skipped:
            self.notification = (
                "The last track (*{}*) was cut short because it exceeded "
                "the song length threshold of {} seconds.".format(
                    self._build_hyperlink(skipped), self.threshold))
            asyncio.ensure_future(self.display_notification())
        data.add(
            self.bot, __name__, 'last_index',
            self.track_index, server_id=self.server.id)

    async def pause(self):
        if (self.state in (States.PAUSED, States.LOADING, States.STOPPED) or
                self.player is None):
            return
        print("Pausing the player...")
        if self.timer_task:
            self.timer_task.cancel()
        self.player.pause()
        self.state = States.PAUSED
        self.progress += time.time() - self.start_time
        asyncio.ensure_future(self.display_title())
        print("Player paused!")

    async def stop(self):
        print("Stopping the player!")
        self.state = States.STOPPED
        self.now_playing = None
        try:
            if self.player:
                player.stop()
            if self.timer_task:
                self.timer_task.cancel()
            if self.command_task:
                self.command_task.cancel()
            if self.progress_task:
                self.progress_task.cancel()
        except Exception as e:
            self.bot.extra = e
            print("Failed to stop some task.", e)
        try:
            print("Attempting to delete things")
            asyncio.ensure_future(self.bot.clear_reactions(self.message))
            asyncio.ensure_future(self.bot.edit_message(
                self.message, new_content='The player has stopped.',
                embed=discord.Embed()))
            print("Things deleted!")
        except Exception as e:
            self.bot.extra = e
            print("Failed to delete the original message", e)
            pass

    async def _command_listener(self):
        valid_commands = ('⏮', '⏯', '⏭', '➖', '⬅', '➡', '⏏')
        for reaction in valid_commands:
            await self.bot.add_reaction(self.message, reaction)

        # Check reactions are proper
        for reaction in self.message.reactions:
            users = await self.bot.get_reaction_users(reaction)
            for user in users:
                if user.id != self.bot.user.id:
                    await self.bot.remove_reaction(
                        self.message, reaction.emoji, user)
        
        if self.state is States.LOADING:  # Startup - finished loading basics
            self.state = States.PAUSED
        asyncio.ensure_future(self.display_title())
        asyncio.ensure_future(self.display_info())
        asyncio.ensure_future(self.display_notification())
        self.progress_task = asyncio.ensure_future(self._progress_loop())

        while True:
            # Wait on reaction command
            print("Waiting on command...")
            result = await self.bot.wait_for_reaction(message=self.message)
            # result.user   and   result.reaction
            if result is None or self.state is States.STOPPED:
                print("Command listener stopping.")
                return
            elif result.user.id == self.bot.user.id:
                print("Ignoring own command. Is this possible?")
                continue

            command = result.reaction.emoji
            if command != valid_commands[6]:
                print("Removing reaction...")
                try:
                    await self.bot.remove_reaction(
                        self.message, command, result.user)
                except Exception as e:
                    print("Failed to remove reaction:", e)
            if self.state is States.LOADING:
                print("Ignoring command: player is still loading.")
                continue

            if command in valid_commands[:3]:  # play|pause and skip
                print("Play|pause and skip selected")
                # User must be a moderator
                is_dj = await _is_dj(self.bot, self.server, result.user.id)
                if not is_dj:
                    continue
                if command == valid_commands[1]:  # play|pause
                    if self.state is States.PLAYING:
                        asyncio.ensure_future(self.pause())
                    elif self.state is States.PAUSED:
                        asyncio.ensure_future(self.play())
                else:  # skip
                    if self.mode is Modes.PLAYLIST:
                        delta = -1 if command == valid_commands[0] else 1
                    else:
                        delta = -1 if command == valid_commands[0] else 0
                    asyncio.ensure_future(
                        self.play(track_index=self.track_index + delta))
            elif command in valid_commands[3:6]:  # Track listing navigation
                print("Track listing navigation selected")
                if command == valid_commands[3]:  # Reset to page 1
                    self.page = int(self.track_index / 5)
                else:
                    self.page += -1 if command == valid_commands[4] else 1
                asyncio.ensure_future(self.display_info())
            elif command == valid_commands[6]:  # Voteskip
                print("Vote skip selected")
                # Check user is in voice channel
                # TODO: Implement
                pass
            else:
                print("THIS SHOULD NEVER HAPPEN WHAT:", command)


        # self.progress_task = asyncio.ensure_future(self._progress_updater())


async def _is_dj(bot, server, user_id):
    """
    Placeholder for determining if somebody can use special playlist features.
    """
    return data.is_mod(bot, server, user_id)


async def _remove_track(bot, entry_index, message, music_player):
    response, message_type, extra, autodelete = '', 0, None, 0
    if music_player:
        await music_player.update_state()
        if music_player.state is not States.STOPPED:
            message_type, extra, autodelete = 2, (5, message), 5

    tracklist = data.get(
        bot, __name__, 'tracklist',
        server_id=message.server.id, save=True, default=[])
    if not tracklist:
        raise BotException(
            EXCEPTION, "Playlist is empty.", autodelete=autodelete)
    try:
        index = int(entry_index) - 1
        if not 0 <= index <= len(tracklist) - 1:
            raise ValueError
    except ValueError:
        raise BotException(
            EXCEPTION,
            "Invalid index. Must be between 1 and {} inclusive.".format(
                len(tracklist)), autodelete=autodelete)

    is_dj = await _is_dj(bot, message.server, message.author.id)
    track_info = tracklist[index]
    if track_info['userid'] != message.author.id and not is_dj:
        raise BotException(
            EXCEPTION,
            "You must be the user who added the entry, or a DJ.",
            autodelete=autodelete)
    tracklist.pop(index)
    response = "Removed {} from the queue".format(track_info['title'])

    if message_type == 2:  # Add notification
        await music_player.display_notification(
            text='<@{}> removed *{}* (track {}) from the queue.'.format(
                message.author.id, music_player._build_hyperlink(track_info),
                index + 1))
        await music_player.display_info()

    return response, message_type, extra


async def _get_info(bot, entry_index, message, music_player):
    response, message_type, extra, autodelete = '', 0, None, 0
    if music_player:
        await music_player.update_state()
        if music_player.state is not States.STOPPED:
            message_type, extra, autodelete = 2, (1, message), 5

    tracklist = data.get(
        bot, __name__, 'tracklist',
        server_id=message.server.id, save=True, default=[])
    if not tracklist:
        raise BotException(
            EXCEPTION, "Playlist is empty.", autodelete=autodelete)
    try:
        index = int(entry_index) - 1
        if not 0 <= index <= len(tracklist) - 1:
            raise ValueError
    except ValueError:
        raise BotException(
            EXCEPTION,
            "Invalid index. Must be between 1 and {} inclusive.".format(
                len(tracklist)), autodelete=autodelete)

    track_info = tracklist[index]
    track_member = data.get_member(bot, track_info['userid'])
    title = track_info['title']
    if len(title) > 40:
        title = title[:40] + ' **...**'
    time_ago = time.time() - track_info['timestamp']
    added_by_text = "Added by <@{}> {} ago.".format(
        track_member.id, utilities.get_time_string(time_ago, text=True))
    duration_text = "Duration: ({})".format(
        utilities.get_time_string(track_info['duration']))
    response = "Info for track {}:".format(index + 1)

    if message_type == 2:  # Add notification
        track_link = '[{}]({} "{} (added by {})")'.format(
            title, track_info['url'], track_info['title'], track_member)
        info_text = "{} {}\n{}\n{}".format(
            response, track_link, duration_text, added_by_text)
        await music_player.display_notification(text=info_text)
        response = ''
    else:
        response += " {} ({})\n{}\n{}".format(
            title, track_info['url'], duration_text, added_by_text)

    return response, message_type, extra


async def _get_tracklist(bot, message, music_player):
    response, message_type, extra, autodelete = '', 0, None, 0
    if music_player:
        await music_player.update_state()
        if music_player.state is not States.STOPPED:
            message_type, extra, autodelete = 2, (1, message), 5

    tracklist = data.get(
            bot, __name__, 'tracklist', server_id=message.server.id,
            default=[])
    if len(tracklist) == 0:
        raise BotException(
            EXCEPTION, 'The playlist queue is empty.', autodelete=autodelete)
    entries = []
    for index, track in enumerate(tracklist):
        entries.append(
            '{}: {}\r\n\tURL: {}\r\n\tAdded by {} ({}) at {} UTC'.format(
                index + 1, track['title'], track['url'],
                data.get_member(bot, track['userid']),
                track['userid'], time.strftime(
                    '%H:%M %d/%m/%Y', time.gmtime(track['timestamp'])
                )
            )
        )
    tracklist_string = '\r\n\r\n'.join(entries)
    tracklist_file = utilities.get_text_as_file(bot, tracklist_string)

    if message_type == 2:  # Add notification
        url = await utilities.upload_to_discord(
            bot, tracklist_file, filename='tracklist.txt')
        await music_player.display_notification(
            text='[Click here]({}) to download the current tracklist.'.format(
                url))
    else:
        response, message_type, extra = tracklist_file, 5, 'tracklist.txt'

    return response, message_type, extra


async def _set_volume(bot, volume_level, message, music_player):
    response, message_type, extra, autodelete = '', 0, None, 0
    if music_player:
        await music_player.update_state()
        if music_player.state is not States.STOPPED:
            message_type, extra, autodelete = 2, (5, message), 5
    try:
        volume = float(volume_level.strip('%')) / 100
        if not 0.1 <= volume <= 2:
            raise ValueError
    except ValueError:
        raise BotException(
            EXCEPTION, "Volume must be between 10% and 200% inclusive.",
            autodelete=autodelete)
    data.add(
        bot, __name__, 'volume', volume, server_id=message.server.id)
    if music_player:
        if music_player.player:
            music_player.player.volume = volume
    response += "Volume set to {}%.\n".format(volume * 100)

    if message_type == 2:  # Add notification
        music_player.update_config()
        await music_player.display_notification(
            text='<@{}> set the volume to {}%.'.format(
                message.author.id, volume * 100))

    return response, message_type, extra


async def _configure(bot, options, message, music_player):
    response, message_type, extra, autodelete = '', 0, None, 0
    if music_player:
        await music_player.update_state()
        if music_player.state is not States.STOPPED:
            message_type, extra, autodelete = 2, (5, message), 5

    default_threshold = configurations.get(bot, __name__, key='max_threshold')
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    threshold = data.get(
        bot, __name__, 'threshold',
        server_id=message.server.id, default=default_threshold)
    cutoff = data.get(
        bot, __name__, 'cutoff',
        server_id=message.server.id, default=default_cutoff)

    if len(options) == 1:
        threshold = default_threshold
        cutoff = default_cutoff
        response = (
            "Threshold and cutoff reset to {} and {} seconds "
            "respectively.".format(threshold, cutoff))

    if 'threshold' in options:
        try:
            threshold = int(options['threshold'])
            if not 10 <= threshold <= default_threshold:
                raise ValueError
        except ValueError:
            raise BotException(
                EXCEPTION,
                "Threshold must be between 10 and {} inclusive.".format(
                    default_threshold), autodelete=autodelete)
        response += "Threshold set to {} seconds.\n".format(threshold)

    if 'cutoff' in options:
        try:
            cutoff = int(options['cutoff'])
            if not 10 <= cutoff <= default_cutoff:
                raise ValueError
        except ValueError:
            raise BotException(
                EXCEPTION,
                "Cutoff must be between 10 and {} inclusive.".format(
                    default_cutoff), autodelete=autodelete)
        response += "Cutoff limit set to {} seconds.".format(cutoff)

    data.add(
        bot, __name__, 'threshold', threshold, server_id=message.server.id)
    data.add(bot, __name__, 'cutoff', cutoff, server_id=message.server.id)

    if message_type == 2:  # Add notification
        music_player.update_config()
        await music_player.display_notification(
            text='<@{}> updated the duration configuration:\n{}'.format(
                message.author.id, response))

    return response, message_type, extra


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    music_player = data.get(
        bot, __name__, 'music_player',
        server_id=message.server.id, volatile=True)

    if blueprint_index == 0:  # Playlist interface
        response = 'Setting up the player interface...'
        message_type = 3
        extra = ('setup_interface', message.author, 'play' in options)

    elif blueprint_index == 1:  # Tracklist
        response, message_type, extra = await _get_tracklist(
            bot, message, music_player)

    elif blueprint_index == 2:  # Info
        response, message_type, extra = await _get_info(
            bot, arguments[0], message, music_player)

    elif blueprint_index == 3:  # Add
        # TODO: Add playlist length limit
        response = "Checking the length of the audio..."
        message_type = 3
        extra = ('duration_check', arguments[0], message.author.id, message)

    elif blueprint_index == 4:  # Remove
        response, message_type, extra = await _remove_track(
            bot, arguments[0], message, music_player)

    elif blueprint_index == 5:  # Volume
        response, message_type, extra = await _set_volume(
            bot, arguments[0], message, music_player)

    elif blueprint_index == 6:  # Configure
        response, message_type, extra = await _configure(
            bot, options, message, music_player)

    return (response, tts, message_type, extra)


async def handle_active_message(bot, message_reference, extra):

    if extra[0] == 'duration_check':
        music_player = data.get(
            bot, __name__, 'music_player',
            server_id=message_reference.server.id, volatile=True)
        hard_threshold = configurations.get(
            bot, __name__, key='hard_threshold')
        default_threshold = configurations.get(
            bot, __name__, key='max_threshold')
        default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
        server_id = message_reference.server.id
        threshold = data.get(
            bot, __name__, 'threshold',
            server_id=server_id, default=default_threshold)
        cutoff = data.get(
            bot, __name__, 'cutoff',
            server_id=server_id, default=default_cutoff)
        options = {'format': 'worstaudio/worst', 'noplaylist': True}
        downloader = YoutubeDL(options)
        try:
            info = await utilities.future(
                downloader.extract_info, extra[1], download=False)
            '''  # TODO: Remove debug
            from pprint import pprint
            pprint(info)
            '''
            chosen_format = info['formats'][0]
            download_url = chosen_format['url']
            title = info.get('title', 'Unknown')
            thumbnail = info.get('thumbnail', '')
            if 'duration' in info:
                duration = int(info['duration'])
            else:  # Manual download and check
                extension = chosen_format['ext']
                file_location, filename = await utilities.download_url(
                    bot, download_url, extension=extension, include_name=True)
                duration = int(TinyTag.get(file_location).duration)
                utilities.delete_temporary_file(bot, filename)
        except BotException as e:
            raise e  # Pass up
        except Exception as e:
            raise BotException(
                EXCEPTION, "Failed to get duration from the URL.", e=e)

        if duration > hard_threshold:
            raise BotException(
                EXCEPTION,
                "Song is longer than the hard threshold of {} seconds.".format(
                    hard_cutoff))

        response = "Song added to playlist."
        if duration > threshold:
            response = (
                "\nSong is longer than the threshold length ({} seconds), so "
                "only the first {} seconds will be played.".format(
                    threshold, cutoff))

        tracklist = data.get(
            bot, __name__, 'tracklist', server_id=server_id,
            default=[], create=True, save=True)
        tracklist.append({
            'url': extra[1],
            'downloadurl': download_url,
            'title': title,
            'thumbnail': thumbnail,
            'duration': duration,
            'timestamp': time.time(),
            'userid': extra[2]
        })

        await bot.edit_message(message_reference, new_content=response)
        if music_player:
            await music_player.update_state()
            if music_player.state is not States.STOPPED:
                await music_player.display_notification(
                    text='<@{}> added *[{}]({})* ({}) to the queue.'.format(
                        extra[2], title, extra[1],
                        utilities.get_time_string(duration)))
                await music_player.display_info()
                await asyncio.sleep(5)
                try:
                    await bot.delete_message(extra[3])
                    await bot.delete_message(message_reference)
                except:
                    pass


    elif extra[0] == 'setup_interface':
        music_player = data.get(
            bot, __name__, 'music_player',
            server_id=message_reference.server.id, volatile=True)
        voice_channel = extra[1].voice.voice_channel
        if music_player and music_player.state is States.STOPPED:
            music_player = None
        elif music_player:
            await music_player.update_state()
        if voice_channel is None:
            raise BotException(
                EXCEPTION,
                "You must be in a voice channel to use the player.")
        elif (music_player and music_player.state is not States.STOPPED and
                music_player.voice_channel != voice_channel):
            raise BotException(
                EXCEPTION,
                "You must be in the same voice channel as the bot.")
        elif music_player and music_player.state is States.LOADING:
            raise BotException(
                EXCEPTION,
                "Playlist is loading, please wait.", autodelete=5)
        elif music_player is None or music_player.state is States.STOPPED:
            print("Creating new music player.")
            music_player = MusicPlayer(
                bot, message_reference, extra[1], voice_channel,
                autoplay=extra[2])
            data.add(
                bot, __name__, 'music_player', music_player,
                server_id=message_reference.server.id, volatile=True)
        else:
            print("Setting new message. Here's the state:", music_player.state)
            await music_player.set_new_message(message_reference)
