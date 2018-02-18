import random
import logging
import asyncio
import functools
import time
import math

import yaml
import discord

from urllib.parse import urlparse
from collections import OrderedDict
from psycopg2.extras import Json

from enum import Enum, IntEnum
from youtube_dl import YoutubeDL
from tinytag import TinyTag

from jshbot import utilities, configurations, data, plugins, logger
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.3.2'
CBException = ConfiguredBotException('Music playlist')
uses_configuration = True

class States(IntEnum):
    PLAYING, PAUSED, STOPPED, LOADING = range(4)

class Modes(IntEnum):
    PLAYLIST, QUEUE = range(2)

class Control(IntEnum):
    ALL, PARTIAL, DJS = range(3)


@plugins.command_spawner
def get_commands(bot):

    default_threshold = configurations.get(bot, __name__, key='max_threshold')
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')

    async def check_whitelist(bot, context):
        config = configurations.get(bot, __name__)
        if config['use_whitelist'] and context.guild.id not in config['whitelist']:
            raise CBException("This server is not in the music player whitelist.")

    new_commands = []

    new_commands.append(Command(
        'playlist', subcommands=[
            SubCommand(
                Opt('tracks'), doc='View the entire playlist', function=format_tracklist),
            SubCommand(
                Opt('import'),
                Attachment('tracklist file'),
                doc='Adds the tracks in the attached tracklist file.',
                function=import_tracklist),
            SubCommand(
                Opt('info'),
                Arg('track number', quotes_recommended=False, convert=int),
                doc='Retrieves the song information of the given track number.',
                function=get_info),
            SubCommand(
                Opt('add'),
                Arg('url', argtype=ArgTypes.MERGED),
                doc='Adds a song to the playlist. Can either be a URL or a YouTube search query',
                function=add_track),
            SubCommand(
                Opt('remove'),
                Arg('track number', quotes_recommended=False, convert=int),
                doc='Removes a song from the playlist.',
                function=remove_track),
            SubCommand(
                Opt('volume'),
                Arg('percent', quotes_recommended=False,
                    convert=utilities.PercentageConverter(),
                    check=lambda b, m, v, *a: 0.1 <= v <= 2.0,
                    check_error='Must be between 10% and 200% inclusive.'),
                doc='Sets the player volume to the given percentage.',
                function=set_volume),
            SubCommand(
                Opt('configure'),
                Opt('threshold', attached='seconds', optional=True, group='options',
                    quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 10 <= v <= default_threshold,
                    check_error='Must be between 10 and {} seconds.'.format(default_threshold)),
                Opt('cutoff', attached='seconds', optional=True, group='options',
                    quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 10 <= v <= default_cutoff,
                    check_error='Must be between 10 and {} seconds.'.format(default_cutoff)),
                Opt('djrole', attached='role', optional=True, group='options',
                    convert=utilities.RoleConverter()),
                Opt('channel', attached='text channel', optional=True, group='options',
                    quotes_recommended=False,
                    convert=utilities.ChannelConverter(constraint=discord.TextChannel),
                    doc='Sets the text channel the player will use for the interface.'),
                Opt('switchcontrol', optional=True, group='options',
                    doc='Switches between DJ only, partial, and public control types.'),
                Opt('switchmode', optional=True, group='options',
                    doc='Switches between repeating playlist and single play queue mode.'),
                doc='Configures the music player properties.',
                function=configure_player),
            SubCommand(Opt('clear'), doc='Clears the playlist.', function=clear_playlist),
            SubCommand(
                Opt('play', optional=True, doc='Automatically starts the player.'),
                Arg('track number', argtype=ArgTypes.OPTIONAL, convert=int,
                    quotes_recommended=False, doc='Selects the given track number'),
                doc='Opens the music player interface.',
                function=setup_player)],
        shortcuts=[
            Shortcut('p', '{arguments}', Arg('arguments', argtype=ArgTypes.MERGED_OPTIONAL))],
        allow_direct=False, category='music',
        pre_check=check_whitelist, description='Play music.'))

    return new_commands


@plugins.db_template_spawner
def get_templates(bot):
    return {
        'playlist_template': (
            "url                text,"
            "downloadurl        text,"
            "title              text,"
            "duration           integer,"
            "userid             bigint,"
            "timestamp          bigint,"
            "extra              json,"
            "id                 serial UNIQUE"
        )
    }


class MusicPlayer():

    def __init__(self, bot, message, autoplay=False, track_index=None):

        # Discord information
        self.bot = bot
        self.channel = message.channel
        self.author = message.author
        self.voice_channel = message.author.voice.channel
        self.guild = message.guild
        self.voice_client = None
        self.source = None
        self.message = None  # Set later
        self.satellite_message = None
        self.satellite_data = None

        # Update tasks
        self.timer_task = None  # Player timer
        self.command_task = None  # Waits for reaction commands
        self.progress_task = None  # Refreshes the progress bar
        self.state_check_task = None  # Checks voice state changes

        # Player information
        self.state = States.LOADING
        self.now_playing = None
        self.notification = None
        self.page = 0
        self.progress = 0
        self.start_time = 0
        self.last_interface_update = 0
        self.listeners = 0
        self.skip_voters = []
        self.skip_threshold = 0.5
        self.shuffle_stack = []
        self.autopaused = False
        self.update_config()
        if self.mode == Modes.QUEUE:
            self.track_index = 0
        else:
            self.track_index = data.get(
                self.bot, __name__, 'last_index', guild_id=self.guild.id, default=0)
            test_tracklist = _get_tracklist(self.bot, self.guild)
            if not 0 <= self.track_index < len(test_tracklist):
                self.track_index = 0

        # Build interface
        asyncio.ensure_future(self._connect(autoplay=autoplay, track_index=track_index))

    def update_config(self):
        guild_id = self.guild.id
        default_threshold = configurations.get(self.bot, __name__, key='max_threshold')
        default_cutoff = configurations.get(self.bot, __name__, key='max_cutoff')

        self.threshold = data.get(
            self.bot, __name__, 'threshold', guild_id=guild_id, default=default_threshold)
        self.cutoff = data.get(
            self.bot, __name__, 'cutoff', guild_id=guild_id, default=default_cutoff)
        self.control = data.get(
            self.bot, __name__, 'control', guild_id=guild_id, default=Control.PARTIAL)
        self.mode = data.get(
            self.bot, __name__, 'mode', guild_id=guild_id, default=Modes.QUEUE)
        self.shuffle = data.get(
            self.bot, __name__, 'shuffle', guild_id=guild_id, default=Modes.QUEUE)

        self.volume = data.get(self.bot, __name__, 'volume', guild_id=guild_id, default=1.0)
        if self.source:
            self.source.volume = self.volume

        # Actively update threshold/cutoff timer
        if self.timer_task and self.state == States.PLAYING:
            self.timer_task.cancel()
            self.timer_task = asyncio.ensure_future(self._track_timer(*self._get_delay()))

    async def _connect(self, autoplay=False, track_index=None):
        is_mod = data.is_mod(self.bot, self.guild, self.author.id)
        try:
            self.voice_client = await utilities.join_and_ready(
                self.bot, self.voice_channel, is_mod=is_mod, reconnect=True)
        except Exception as e:
            self.state = States.STOPPED
            error = CBException("Failed to start the player interface.", e=e)
            await self.channel.send(embed=error.embed)
        else:
            await self._build_interface()
            # Start playback if necessary
            if autoplay:
                self.autoplay_task = asyncio.ensure_future(
                    self._autoplay(track_index=track_index))

    async def _autoplay(self, track_index=None):
        logger.debug("Using autoplay")
        safety_timeout = 0
        while self.state == States.LOADING:
            if safety_timeout > 30:
                raise CBException("Autoplay failed.")
            await asyncio.sleep(0.5)
            safety_timeout += 0.5
        asyncio.ensure_future(self.play(track_index=track_index))
        logger.debug("Autoplay finished")

    async def update_state(self):
        if self.state == States.STOPPED:
            return
        if ((self.voice_client.is_playing() and self.voice_client.source != self.source)
                or self.guild.me not in self.voice_channel.members):
            logger.debug("update_state detected an unstopped instance. Stopping now.")
            await self.stop(
                text="The player has been stopped due to a different audio source being in use.")

    async def set_new_message(self, channel, autoplay=False, track_index=None):
        if self.command_task:
            self.command_task.cancel()
        if self.progress_task:
            self.progress_task.cancel()
        if self.state_check_task:
            self.state_check_task.cancel()
        if self.message:
            try:
                await self.message.delete()
                await self.satellite_message.delete()
            except Exception as e:
                logger.warn("Couldn't delete original messages: %s", e)
        
        self.channel = channel
        self.satellite_data = None  # Force update
        asyncio.ensure_future(self._build_interface(resume=self.state == States.PLAYING))
        if autoplay:
            self.autoplay_task = asyncio.ensure_future(
                self._autoplay(track_index=track_index))

    async def _build_interface(self, resume=False):
        self.state = States.LOADING
        self.satellite_message = await self.channel.send(embed=discord.Embed())
        embed = discord.Embed(colour=discord.Colour(0xffab00))
        embed.add_field(  # Title
            name=':arrows_counterclockwise: **[]**',
            value='**`[{}]` [ `0:00` / `0:00` ]**'.format('\u25aa'*30), inline=False)
        embed.add_field(name='---', value='---', inline=False)  # Info
        embed.add_field(name='---', value='---', inline=False)  # Listeners
        embed.add_field(name='---', value='---\n'*6, inline=False)  # Tracklist
        embed.add_field(name='---', value='---')  # Notification
        self.embed = embed
        self.message = await self.channel.send(embed=embed)
        self.command_task = asyncio.ensure_future(self._command_listener(resume=resume))
        #self.state_listener_task = asyncio.ensure_future(self._listener_loop())

    def _build_hyperlink(self, track):
        title = track.title
        if len(title) > 40:
            title = title[:40] + ' **...**'
        return '[{}]({} "{} (added by {})")'.format(
            title, track.url, track.title, data.get_member(self.bot, track.userid))

    # Refreshes the progress bar
    async def _progress_loop(self):
        await asyncio.sleep(5)
        while True:
            await self.update_state()
            if self.state == States.PLAYING:
                self.update_listeners()
                if time.time() - self.last_interface_update >= 4:
                    asyncio.ensure_future(self.update_interface())
                asyncio.ensure_future(self.update_satellite())
                await asyncio.sleep(5)
            elif self.state in (States.PAUSED, States.LOADING):
                # TODO: Implement idle timeout
                await asyncio.sleep(1)
            else:  # Stopped
                logger.warn("Progress loop wasn't cancelled for some reason. Stopping loop...")
                return

    # Checks the state of members in the voice channel
    async def _listener_loop(self):
        class VoiceChange(Enum):
            NORMAL, LEFT, JOINED = range(3)
        def check(member, before, after):
            if not member == self.bot.user and (member.bot or not (before or after)):
                return VoiceChange.NORMAL
            elif after and after.channel == self.voice_channel:
                if not before or before.channel != self.voice_channel:
                    return VoiceChange.JOINED
            elif before and before.channel == self.voice_channel:
                if not after or after.channel != self.voice_channel:
                    return VoiceChange.LEFT
            else:
                logger.warn(
                    "Listener check did not account for: Before: %s, After: %s",
                    before, after)
                return VoiceChange.NORMAL

        # Preliminary check
        self.listeners = len([it for it in self.voice_channel.members if not it.bot])

        # Wait on voice state updates to determine users entering/leaving
        logger.debug("Listener loop (voice state checker) started.")
        while True:
            result = await self.bot.wait_for('voice_state_update')
            logger.debug("Voice state change detected!")
            if not result:
                logger.warn("Voice state update returned empty!")
                continue
            elif self.state == States.STOPPED:
                logger.warn("Listener loop wasn't cancelled for some reason. Stopping loop.")
                return

            # Check for self changes
            if result[0] == self.bot.user:
                if not result[2]:  # Disconnected
                    # TODO: Consider adding failsafe stop
                    logger.warn("Voice disconnected, detected from _listener_loop.")
                    return
                if result[1] != result[2]:
                    logger.debug("Bot was dragged to a new voice channel.")
                    if result[2].channel == self.guild.afk_channel:  # TODO: Act on AFK channel
                        logger.warn("Moved to the AFK channel. Failsafe stopping.")
                    self.voice_channel = result[2].channel
                    self.voice_client = self.guild.voice_client

            # Update listener count
            self.listeners = len([it for it in self.voice_channel.members if not it.bot])
            logger.debug("Listeners: %s", self.listeners)
            self.update_listeners()

            voice_change = check(*result)
            if voice_change is VoiceChange.NORMAL:
                logger.debug("Ignoring voice state change")
                continue
            elif voice_change is VoiceChange.LEFT:
                logger.debug("User left the voice channel")
                if result[0].id in self.skip_voters:
                    self.skip_voters.remove(result[0].id)
                    logger.debug("Removing a skip voter...")
            elif voice_change is VoiceChange.JOINED:
                logger.debug("User joined the voice channel")
                pass

            if self.listeners == 0:
                logger.debug("Automatically pausing.")
                self.autopaused = True
                self.notification = "The player has been automatically paused."
                asyncio.ensure_future(self.pause())
            elif self.listeners == 1 and self.state == States.PAUSED and self.autopaused:
                logger.debug("Automatically resuming.")
                self.autopaused=False
                self.notification = "The player has been automatically resumed."
                asyncio.ensure_future(self.play())
            # TODO: Consider setting autopause to false by default

    # Updates the number of active listeners and skips the song if enough people have voted
    def update_listeners(self):

        current_listeners = [it.id for it in self.voice_channel.members]
        for member_id in self.skip_voters[::]:
            if member_id in current_listeners:
                self.skip_voters.remove(member_id)

    # Updates the main interface
    async def update_interface(self, notification_text='', ratelimit_ignore=False):
        if ratelimit_ignore and time.time() - self.last_interface_update < 1:
            return
        await self.update_title()
        await self.update_info()
        await self.update_notification(text=notification_text)
        await self.update_footer()
        await self.message.edit(content=None, embed=self.embed)
        self.last_interface_update = time.time()

    # Updates the satellite message
    async def update_satellite(self):
        if not self.now_playing and self.satellite_data:
            self.satellite_data = None
            await self.satellite_message.edit(embed=discord.Embed())
            return
        elif not self.now_playing or self.now_playing.extra == self.satellite_data:
            return
        self.satellite_data = extra = self.now_playing.extra

        embed = discord.Embed()
        keys = ('uploader', 'views', 'likes', 'dislikes', 'uploaded')
        if any(key in extra for key in keys):
            info_list = ['{}: {}'.format(key.title(), extra[key]) for key in keys if key in extra]
            embed.add_field(name='Info', value='\n'.join(info_list))

        if 'description' in extra:
            description = extra['description']
            chunks = [description[it:it+1000] for it in range(0, len(description), 1000)]
            if len(chunks) > 3:
                chunks = chunks[:3]
                chunks[-1] += '**...**'
            for index, chunk in enumerate(chunks):
                embed.add_field(name='Description' if index == 0 else '\u200b', value=chunk)

        if 'thumbnail' in extra:
            embed.set_image(url=extra['thumbnail'])

        if 'artist_thumbnail' in extra:
            embed.set_thumbnail(url=extra['artist_thumbnail'])

        await self.satellite_message.edit(embed=embed)
        
    async def update_footer(self):
        if self.mode == Modes.PLAYLIST:
            shuffle_text = ' | Shuffle: {}'.format('On' if self.shuffle else 'Off')
        else:
            shuffle_text = ''
        footer_text = 'Volume: {}% | Mode: {}{} | Control: {}'.format(
            int(self.volume * 100),
            ('Playlist', 'Queue')[self.mode],
            shuffle_text,
            ('Public', 'Partially public', 'DJs only')[self.control])
        self.embed.set_footer(text=footer_text)

    async def update_title(self):
        if self.state == States.PLAYING:
            progress = self.progress + (time.time() - self.start_time)
            status_icon = ':arrow_forward:'
        elif self.state == States.PAUSED:
            progress = self.progress
            status_icon = ':pause_button:'
        else:
            progress = 0
            status_icon = ':arrows_counterclockwise:'
        if self.now_playing:
            title = self.now_playing.title
            if len(title) > 60:
                title = title[:60] + ' **...**'
            duration = self.now_playing.duration
        else:
            title = '---'
            duration = 0
        new_name = '{} **[{}]**'.format(status_icon, title)
        percentage = 0 if duration == 0 else progress/duration
        progress_bar = '\u2588' * int(30 * percentage)
        new_value = '**`[{:\u25aa<30}]` [ `{}` / `{}` ]**'.format(
            progress_bar, utilities.get_time_string(progress),
            utilities.get_time_string(duration))

        if self.state == States.PLAYING:
            color = discord.Color(0x3b88c3)
        elif self.state == States.PAUSED:
            color = discord.Color(0xccd6dd)
        else:  # Loading
            color = discord.Color(0xffab00)
        self.embed.color = color

        self.embed.set_field_at(0, name=new_name, value=new_value, inline=False)

    async def update_info(self):
        # Listeners
        new_name = '{} listener{}'.format(self.listeners, '' if self.listeners == 1 else 's')
        new_value = '[ {} / {} ] needed to skip'.format(
            len(self.skip_voters), math.ceil(self.listeners * self.skip_threshold))
        self.embed.set_field_at(2, name=new_name, value=new_value, inline=False)

        # Tracklist
        tracklist = _get_tracklist(self.bot, self.guild)
        total_tracks = len(tracklist)
        total_duration = sum(it.duration for it in tracklist)
        total_pages = max(int((total_tracks + 4) / 5), 1)
        #self.page = min(max(self.page, 0), total_pages - 1)
        if self.page < 0:
            self.page = total_pages - 1
        elif self.page >= total_pages:
            self.page = 0
        displayed_tracks = tracklist[self.page * 5:(self.page * 5) + 5]

        info = []
        for index, entry in enumerate(displayed_tracks):
            duration = utilities.get_time_string(entry.duration)
            entry_index = (self.page * 5) + index + 1
            info.append('**`{}{}`**: ({}) *{}*'.format(
                '▶ ' if entry_index == self.track_index + 1 else '',
                entry_index, duration, self._build_hyperlink(entry)))
        for index in range(5 - len(displayed_tracks)):
            info.append('---')
        info.append('Page [ {} / {} ]'.format(self.page+1, total_pages))
        new_value = '\n'.join(info)
        player_mode = 'queue' if self.mode == Modes.QUEUE else 'playlist'
        if total_tracks > 0:
            new_name = '{} track{} in {} (runtime of {}):'.format(
                total_tracks, '' if total_tracks == 1 else 's', player_mode,
                utilities.get_time_string(total_duration, text=True))
        else:
            new_name = 'No tracks in {}'.format(player_mode)

        self.embed.set_field_at(3, name=new_name, value=new_value, inline=False)

        # Info
        if self.now_playing:
            # TODO: Possible remove redundant link portion?
            new_name = 'Info:'
            time_ago = time.time() - self.now_playing.timestamp
            new_value = 'Added by <@{}> {} ago [(Link)]({} "{}")'.format(
                self.now_playing.userid,
                utilities.get_time_string(time_ago, text=True),
                self.now_playing.url, self.now_playing.title)
        else:
            new_name = '---'
            new_value = '---'

        if len(tracklist) == 0:
            next_index = -1
            new_value += '\n---'
        elif self.now_playing is None:
            next_index = 0 if self.mode == Modes.QUEUE else self.track_index
        elif self.track_index + 1 >= len(tracklist):
            next_index = 0
        else:
            if self.mode == Modes.PLAYLIST:
                next_index = self.track_index + 1
            else:
                next_index = 0

        if next_index != -1:
            next_track = tracklist[next_index]
            if self.mode == Modes.PLAYLIST:
                index_string = '[Track {}] '.format(next_index + 1)
            else:
                index_string = ''
            if next_index >= 0:
                if self.mode == Modes.PLAYLIST and self.shuffle:
                    new_value += '\nUp next: [Track ?]'
                else:
                    duration = utilities.get_time_string(next_track.duration)
                    new_value += '\nUp next: {}({}) *{}*'.format(
                        index_string, duration, self._build_hyperlink(next_track))

        self.embed.set_field_at(1, name=new_name, value=new_value, inline=False)

    async def update_notification(self, text=''):
        if text:
            self.notification = text
        elif not self.notification:
            self.notification = 'No notification.'
        self.embed.set_field_at(4, name='Notification:', value=self.notification)

    # Skips the current track (even if paused)
    def _skip_track(self):
        delta = 1 if self.mode == Modes.PLAYLIST else 0
        if self.mode == Modes.PLAYLIST and self.shuffle and delta != 0:
            tracklist = _get_tracklist(self.bot, self.guild)
            if self.now_playing:
                self.shuffle_stack.append(self.now_playing.id)
            new_track_index = random.randint(0, len(tracklist) - 1)
        else:
            new_track_index = self.track_index + delta
        asyncio.ensure_future(self.play(track_index=new_track_index))
        logger.debug("_skip_track finished")

    async def _track_timer(self, sleeptime, use_skip=False):
        logger.debug("Sleeping for %s seconds. Time: %s", sleeptime, time.time())
        track_check = self.now_playing
        await asyncio.sleep(sleeptime)
        logger.debug("Finished sleeping for %s seconds. Time: %s", sleeptime, time.time())
        await self.update_state()
        if self.state == States.STOPPED or track_check != self.now_playing:
            logger.debug("The track timer resumed????????????????????????????????")
            return
        while self.state == States.LOADING:
            logger.warn("Player was moved while the track was loading.")
            await asyncio.sleep(1)
        if self.mode == Modes.PLAYLIST and self.shuffle:
            logger.debug("Adding track %s to the shuffle stack", track_check.title)
            tracklist = _get_tracklist(self.bot, self.guild)
            new_track_index = random.randint(0, len(tracklist) - 1)
            self.shuffle_stack.append(track_check.id)
            asyncio.ensure_future(self.play(track_index=new_track_index, skipped=use_skip))
        else:
            logger.debug('_track_timer is moving on: %s', use_skip)
            asyncio.ensure_future(self.play(skipped=use_skip))

    def _get_delay(self):  # Gets track delay with cutoff
        if self.now_playing.duration > self.threshold:
            duration = self.cutoff
            use_skip = self.now_playing
        else:
            duration = self.now_playing.duration
            use_skip = False
        temp = (max(duration - self.progress, 0), use_skip)
        logger.debug('_get_delay got: %s', temp)
        return temp
        #return (max(duration - self.progress, 0), use_skip)

    async def play(self, track_index=None, skipped=False, wrap_track_numbers=True):
        if self.state in (States.LOADING, States.STOPPED):
            return
        if (self.state == States.PAUSED and
                self.now_playing and self.progress and track_index is None):
            logger.debug("Resuming player...")
            self.state = States.PLAYING
            self.voice_client.resume()
            self.start_time = time.time()
            self.timer_task = asyncio.ensure_future(self._track_timer(*self._get_delay()))
            asyncio.ensure_future(self.update_interface())
            logger.debug("Player resumed!")
            return

        tracklist = _get_tracklist(self.bot, self.guild)
        if len(tracklist) == 0 and not (track_index == -1 and self.state == States.PLAYING):
            self.notification = "There are no more tracks in the queue."
            if self.voice_client.is_playing():
                self.voice_client.stop()
            self.source = None
            self.now_playing = None
            self.progress = 0
            self.state = States.PAUSED
            asyncio.ensure_future(self.update_interface())
            asyncio.ensure_future(self.update_satellite())
            return

        if self.now_playing is None and track_index is None:  # First time startup
            pass  # Probably just leave this here?

        elif track_index is None:

            if self.mode == Modes.PLAYLIST:
                if self.track_index + 1 >= len(tracklist):
                    self.track_index = 0
                else:
                    self.track_index += 1

        else:  # Given track_index
            if track_index != -1 and not 0 <= track_index < len(tracklist):
                if wrap_track_numbers:
                    logger.debug("Wrapping track number.")
                    if track_index >= len(tracklist):
                        track_index = 0
                    elif track_index < 0:
                        track_index = -1
                else:
                    self.notification = (
                        "Index must be between 1 and {} inclusive.".format(len(tracklist)))
                    asyncio.ensure_future(self.update_interface())
                    return
            if track_index == -1 and self.mode == Modes.PLAYLIST:
                track_index = len(tracklist) - 1
            if self.mode == Modes.PLAYLIST:
                self.track_index = track_index

        if self.mode == Modes.PLAYLIST:
            track = tracklist[self.track_index]
        else:
            if track_index == -1:
                if self.now_playing:
                    track = self.now_playing
                else:
                    return
            else:
                if isinstance(track_index, int) and track_index != 0:
                    track = tracklist[0 if track_index == -1 else track_index]
                else:
                    track = tracklist[0]
                if track_index != -1:
                    logger.debug("Removing track from tracklist")
                    data.db_delete(
                        self.bot, 'playlist', table_suffix=self.guild.id,
                        where_arg='id=%s', input_args=[track.id])

        # Setup the player
        logger.debug("Preparing to play the next track.")
        if self.state == States.PLAYING:
            if self.voice_client.is_playing():
                self.voice_client.stop()
        if self.timer_task:
            self.timer_task.cancel()
        self.state = States.LOADING
        self.now_playing = track
        sound_file = data.get_from_cache(self.bot, None, url=track.url)
        if not sound_file:
            asyncio.ensure_future(self.update_interface())
            logger.debug("Not found in cache. Downloading...")

            try:
                options = {'format': 'bestaudio/best', 'noplaylist': True}
                downloader = YoutubeDL(options)
                sound_file = await data.add_to_cache_ydl(self.bot, downloader, track.url)
            except Exception as e:  # Attempt to redownload from base url
                logger.debug("Failed to download the track. Failsafe skipping... %s", e)
                self.notification = "Failed to download {}. Failsafe skipping...".format(
                    track.title)
                self.state = States.PAUSED
                self._skip_track()
                return

            logger.debug("Download finished.")
        # TODO: Add exception handling
        # TODO: Change ffmpeg_options for docker version
        #ffmpeg_options = '-protocol_whitelist "file,http,https,tcp,tls"'
        #audio_source = discord.FFmpegPCMAudio(sound_file, before_options=ffmpeg_options)
        audio_source = discord.FFmpegPCMAudio(sound_file)

        audio_source = discord.PCMVolumeTransformer(audio_source, volume=self.volume)
        self.voice_client.play(audio_source)
        self.source = audio_source

        self.progress = 0
        self.start_time = time.time()
        self.state = States.PLAYING
        self.timer_task = asyncio.ensure_future(self._track_timer(*self._get_delay()))
        if skipped:
            self.notification = (
                "The track *{}* was cut short because it exceeded "
                "the song length threshold of {} seconds.".format(
                    self._build_hyperlink(skipped), self.threshold))
        asyncio.ensure_future(self.update_interface())
        data.add(self.bot, __name__, 'last_index', self.track_index, guild_id=self.guild.id)

    async def pause(self):
        if (self.state in (States.PAUSED, States.LOADING, States.STOPPED) or
                self.voice_client is None or not self.voice_client.is_playing()):
            return
        logger.debug("Pausing the player...")
        if self.timer_task:
            self.timer_task.cancel()
        self.voice_client.pause()
        self.state = States.PAUSED
        self.progress += time.time() - self.start_time
        asyncio.ensure_future(self.update_interface())
        logger.debug("Player paused!")

    async def stop(self, text="The player has been stopped."):
        logger.debug("Stopping the player!")
        self.state = States.STOPPED
        self.now_playing = None
        try:
            if self.voice_client:
                self.voice_client.stop()
            if self.timer_task:
                self.timer_task.cancel()
            if self.command_task:
                self.command_task.cancel()
            if self.progress_task:
                self.progress_task.cancel()
            if self.state_check_task:
                self.state_check_task.cancel()
        except Exception as e:
            logger.debug("Failed to stop some task. %s", e)
        try:
            asyncio.ensure_future(self.satellite_message.delete())
            asyncio.ensure_future(self.message.clear_reactions())
            asyncio.ensure_future(self.message.edit(content=text, embed=None))
        except Exception as e:
            logger.warn("Failed to modify the original message %s", e)
            pass

    async def _command_listener(self, resume=False):
        valid_commands = ('⏮', '⏯', '⏭', '⏹', '🔀', '🎵', '⬅', '⏺', '➡', '⏏', '❓')
        for reaction in valid_commands:
            try:
                await self.message.add_reaction(reaction)
            except Exception as e:
                logger.warn("Failed to add reaction: %s", e)

        # Check reactions are proper
        for reaction in self.message.reactions:
            users = await self.bot.get_reaction_users(reaction)
            for user in users:
                if user != self.bot.user:
                    await self.message.remove_reaction(reaction.emoji, user)

        if self.state == States.LOADING:  # Startup - finished loading basics
            self.state = States.PLAYING if resume else States.PAUSED
        asyncio.ensure_future(self.update_interface())
        self.progress_task = asyncio.ensure_future(self._progress_loop())
        self.state_check_task = asyncio.ensure_future(self._listener_loop())

        try:  # TODO: Remove try/except block
            while True:
                # Wait on reaction command
                kwargs = {'check': lambda r, u: r.message.id == self.message.id and not u.bot}
                logger.debug("Waiting on command...")
                result = await self.bot.wait_for('reaction_add', **kwargs)
                if result is None or self.state == States.STOPPED:
                    logger.debug("Command listener stopping.")
                    return
                elif result[1] == self.bot.user:
                    logger.warn("Ignoring own command. Is this possible?")
                    continue
                logger.debug("Command listener finished waiting once.")

                command = result[0].emoji
                asyncio.ensure_future(self.message.remove_reaction(command, result[1]))
                if self.state == States.LOADING:
                    logger.debug("Ignoring command: player is still loading.")
                    continue
                if command not in valid_commands:
                    logger.debug("Ignoring invalid command: %s", command)
                    continue

                # Check player control type
                is_dj = data.has_custom_role(self.bot, __name__, 'dj', member=result[1])
                restriction_width = [0, 5, 10][self.control]
                if command in valid_commands[:restriction_width] and not is_dj:
                    logger.debug("Ignoring command (insufficient permissions)")
                    continue

                if command in valid_commands[:3]:  # Play/pause and skip
                    logger.debug("Play|pause and skip selected")
                    if command == valid_commands[1]:  # Play/pause
                        if self.state == States.PLAYING:
                            asyncio.ensure_future(self.pause())
                        elif self.state == States.PAUSED:
                            asyncio.ensure_future(self.play())
                    else:  # skip
                        if self.mode == Modes.PLAYLIST:
                            use_repeat = time.time() - self.start_time >= 10
                            if use_repeat:
                                delta = 0 if command == valid_commands[0] else 1
                            else:
                                delta = -1 if command == valid_commands[0] else 1
                        else:
                            delta = -1 if command == valid_commands[0] else 0
                        if self.mode == Modes.PLAYLIST and self.shuffle and delta != 0:
                            tracklist = _get_tracklist(self.bot, self.guild)
                            last_track = None
                            if delta == -1 and self.shuffle_stack:  # Check shuffle stack first
                                last_track_id = self.shuffle_stack.pop()
                                for new_track_index, track in enumerate(tracklist):
                                    if track.id == last_track_id:
                                        last_track = track
                                        break
                            if last_track is None:
                                if self.now_playing:
                                    self.shuffle_stack.append(self.now_playing.id)
                                new_track_index = random.randint(0, len(tracklist) - 1)
                        else:
                            new_track_index = self.track_index + delta
                        asyncio.ensure_future(self.play(track_index=new_track_index))

                elif command == valid_commands[3]:  # Stop player
                    await self.stop(
                        text="The player has been stopped by {}.".format(result[1].mention))
                    return

                elif command == valid_commands[4]:  # Shuffle mode
                    if self.mode == Modes.PLAYLIST:
                        self.shuffle = not self.shuffle
                        data.add(
                            self.bot, __name__, 'shuffle', self.shuffle, guild_id=self.guild.id)
                    asyncio.ensure_future(self.update_interface())

                elif command == valid_commands[5]:  # Generate tracklist
                    logger.debug("Tracklist selected")
                    tracklist = _get_tracklist(self.bot, self.guild)
                    if tracklist:
                        tracklist_string = _build_tracklist(self.bot, self.guild, tracklist)
                        tracklist_file = utilities.get_text_as_file(tracklist_string)
                        url = await utilities.upload_to_discord(
                            self.bot, tracklist_file, filename='tracklist.txt')
                        asyncio.ensure_future(self.update_interface(
                            notification_text=(
                                '[Click here]({}) to download the current tracklist.'.format(
                                    url))))

                elif command in valid_commands[6:9]:  # Track listing navigation
                    logger.debug("Track listing navigation selected")
                    if command == valid_commands[7]:  # Reset to page 1
                        self.page = int(self.track_index / 5)
                    else:
                        self.page += -1 if command == valid_commands[6] else 1
                    asyncio.ensure_future(self.update_interface())

                elif command == valid_commands[9]:  # Voteskip
                    logger.debug("Vote skip selected")
                    voice_members = self.voice_channel.members

                    if result[1].bot:
                        continue
                    elif result[1].id in self.skip_voters:
                        self.skip_voters.remove(result[1].id)
                    elif result[1] in voice_members:
                        self.skip_voters.append(result[1].id)
                    else:
                        continue

                    needed_votes = math.ceil(int(self.listeners * self.skip_threshold))

                    if len(self.skip_voters) >= needed_votes:
                        logger.debug("Skip threshold met")
                        self.notification = "The track {} was voteskipped ({} vote{}).".format(
                            self._build_hyperlink(self.now_playing), len(self.skip_voters),
                            '' if len(self.skip_voters) == 1 else 's')
                        del self.skip_voters[:]

                        # Skimmed down skip code
                        self._skip_track()

                    else:
                        asyncio.ensure_future(self.update_interface(ratelimit_ignore=True))

                    # asyncio.ensure_future(self.update_listeners())
                    # asyncio.ensure_future(self.update_interface(ratelimit_ignore=True))

                elif command == valid_commands[10]:  # Help
                    logger.debug("Help selected")
                    button_help = (
                        '⏮, ⏯, ⏭, ⏹: Back, Play|Pause, Next, Stop\n'
                        '🔀: Shuffle (playlist mode only)\n'
                        '🎵: Generate tracklist\n'
                        '⬅, ➡: Track page navigation\n'
                        '⏺: Reset track page to current playing track\n'
                        '⏏: Voteskip (must be listening)\n'
                        '❓: This help page'
                    )
                    permissions_help = (
                        '**DJs only:** Only DJs can manage the player\n'
                        '**Partially public:** Everybody can add tracks, '
                        'change track pages, and voteskip\n'
                        '**Public:** Everybody has full control '
                        '(except removing other people\'s '
                        'tracks and importing tracklists)'
                    )
                    status_help = (
                        ':arrow_forward: (Blue): Playing a track\n'
                        ':pause_button: (White): Paused\n'
                        ':arrows_counterclockwise: (Orange): Loading'
                    )
                    command_help = (
                        'To add tracks: {0[3].help_string}\n'
                        'To remove tracks: {0[4].help_string}\n'
                        'For more, type `help playlist`'
                    ).format(self.bot.commands['playlist'].subcommands)
                    help_embed = discord.Embed(title=':question: Music player help')
                    help_embed.add_field(name='Basic usage', value=command_help)
                    help_embed.add_field(name='Buttons', value=button_help)
                    help_embed.add_field(name='Control types', value=permissions_help)
                    help_embed.add_field(name='Status icons', value=status_help)
                    asyncio.ensure_future(result[1].send(embed=help_embed))

        except Exception as e:
            self.bot.extra = e
            logger.warn("Something bad happened. %s", e)


def _get_tracklist(bot, guild):
    cursor = data.db_select(
        bot, from_arg='playlist', additional='ORDER BY id ASC', table_suffix=guild.id)
    return cursor.fetchall() if cursor else ()


def _get_music_player(bot, guild):
    return data.get(bot, __name__, 'music_player', guild_id=guild.id, volatile=True)


async def _check_active_player(bot, guild, autodelete_time=5):
    import_lock = data.get(bot, __name__, 'import_lock', guild_id=guild.id, volatile=True)
    if import_lock:
        raise CBException("A track import is in progress. Please wait for it to finish.")
    music_player = _get_music_player(bot, guild)
    if music_player:
        await music_player.update_state()
        use_player_interface = music_player.state is not States.STOPPED
    else:
        use_player_interface = False
    autodelete = autodelete_time if use_player_interface else 0
    return music_player, use_player_interface, autodelete


async def _add_track_to_db(bot, guild, check_url, user_id=0, timestamp=0):
    hard_threshold = configurations.get(bot, __name__, key='hard_threshold')
    options = {'format': 'bestaudio/best', 'noplaylist': True, 'default-search': 'ytsearch'}
    downloader = YoutubeDL(options)

    # Check for a direct URL (SO: 7160737)
    try:
        test = urlparse(check_url)
        is_url = test.scheme and test.netloc and test.path
    except:
        is_url = False

    if not is_url and not check_url.lower().startswith('ytsearch:'):
        check_url = 'ytsearch:' + check_url.strip()

    try:
        info = await utilities.future(downloader.extract_info, check_url, download=False)
        if not is_url:  # Select first result on search
            info = info['entries'][0]
            check_url = info['webpage_url']
        chosen_format = info['formats'][0]
        download_url = chosen_format['url']
        title = info.get('title', 'Unknown')
        thumbnail = info.get('thumbnail', None)
        likes = info.get('like_count', None)
        dislikes = info.get('dislike_count', None)
        views = info.get('view_count', None)
        description = info.get('description', None)
        upload_date = info.get('upload_date', None)
        uploader = info.get('uploader', None)
        if 'duration' in info:
            duration = int(info['duration'])
        else:  # Manual download and check
            extension = chosen_format['ext']
            sound_file, filename = await utilities.download_url(
                bot, download_url, extension=extension, include_name=True)
            duration = int(TinyTag.get(sound_file).duration)
            utilities.delete_temporary_file(bot, filename)
    except BotException as e:
        raise e  # Pass up
    except Exception as e:
        raise CBException("Failed to get duration from the URL.", e=e)

    if duration > hard_threshold:
        raise CBException(
            "Song is longer than the hard threshold of {} seconds.".format(hard_threshold))

    extra_data = {}
    if thumbnail is not None:
        extra_data['thumbnail'] = thumbnail
    if likes is not None:
        extra_data['likes'] = likes
    if dislikes is not None:
        extra_data['dislikes'] = dislikes
    if views is not None:
        extra_data['views'] = views
    if description is not None:
        extra_data['description'] = description
    if upload_date is not None:
        extra_data['uploaded'] = '{}/{}/{}'.format(
            upload_date[4:6], upload_date[6:8], upload_date[:4])
    if uploader is not None:
        extra_data['uploader'] = uploader
    entry_data = [
        check_url,
        download_url,
        title,
        duration,
        user_id,
        timestamp if timestamp else time.time(),
        Json(extra_data)
    ]

    data.db_insert(
        bot, 'playlist', table_suffix=guild.id, input_args=entry_data, create='playlist_template')

    return entry_data


async def add_track(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    hard_threshold = configurations.get(bot, __name__, key='hard_threshold')
    default_threshold = configurations.get(bot, __name__, key='max_threshold')
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    guild_id = context.guild.id
    threshold = data.get(bot, __name__, 'threshold', guild_id=guild_id, default=default_threshold)
    cutoff = data.get(bot, __name__, 'cutoff', guild_id=guild_id, default=default_cutoff)

    check_url = context.arguments[0]
    try:
        track_data = await _add_track_to_db(
            bot, context.guild, check_url, user_id=context.author.id)
    except BotException as e:
        e.autodelete = autodelete
        raise e

    response = "Song `{}` was added to the playlist.".format(track_data[2])
    title, duration = track_data[2], track_data[3]
    if duration > threshold:
        response = (
            "\nSong is longer than the threshold length ({} seconds), so "
            "only the first {} seconds will be played.".format(threshold, cutoff))
    
    # Check the music player again, as it may have stopped while we were download the url
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    if use_player_interface:
        await music_player.update_interface(
            notification_text='<@{}> added *[{}]({})* ({}) to the queue.'.format(
                context.author.id, title, check_url, utilities.get_time_string(duration)))
    
    return Response(
        content=response,
        message_type=MessageTypes.REPLACE if use_player_interface else MessageTypes.NORMAL,
        delete_after=autodelete if use_player_interface else None,
        extra=autodelete if use_player_interface else None)


async def remove_track(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    # Check track index
    tracklist = _get_tracklist(bot, context.guild)
    if not tracklist:
        raise CBException("The playlist queue is empty.", autodelete=autodelete)
    index = context.arguments[0] - 1
    if not 0 <= index < len(tracklist):
        raise CBException("Invalid index. Must be between 1 and {} inclusive.".format(
            len(tracklist)), autodelete=autodelete)

    # Check permissions
    is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
    control = data.get(
        bot, __name__, 'control', guild_id=context.guild.id, default=Control.PARTIAL)
    track_info = tracklist[index]
    if control == Control.DJS and not is_dj:
        raise CBException("You must be a DJ to remove entries.", autodelete=autodelete)
    elif track_info.userid != context.author.id and not is_dj:
        raise CBException(
            "You must be the user who added the entry, or a DJ.", autodelete=autodelete)

    # Change current index if necessary

    data.db_delete(
        bot, 'playlist', table_suffix=context.guild.id,
        where_arg='id=%s', input_args=[track_info.id])

    if use_player_interface:
        logger.debug("Removed index: [%s] Current index: [%s]", index, music_player.track_index)
        if index <= music_player.track_index:  # Shift track index down
            music_player.track_index -= 1
        if index == music_player.track_index + 1:  # Skip track
            music_player._skip_track()
        await music_player.update_interface(
            notification_text='<@{}> removed *{}* (track {}) from the queue.'.format(
                context.author.id, music_player._build_hyperlink(track_info), index + 1))

    return Response(
        content="Removed `{}` from the queue.".format(track_info.title),
        message_type=MessageTypes.REPLACE if use_player_interface else MessageTypes.NORMAL,
        delete_after=autodelete if use_player_interface else None,
        extra=autodelete if use_player_interface else None)


def _build_tracklist(bot, guild, tracklist):
    template = (
        '{}: |\r\n'
        '  {}\r\n'  # Title
        '  {}\r\n'  # URL
        '  Added by {} at {} {}\r\n'  # Info
        '  Duration: {} ID|Timestamp: {}|{}\r\n'  # Duration, internal info
    )
    tracklist_text_list = []
    for index, track in enumerate(tracklist):
        track_author = data.get_member(bot, track.userid)
        track_author = str(track_author) if track_author else 'Unknown'
        offset, upload_time = utilities.get_timezone_offset(
            bot, guild_id=guild.id, utc_seconds=track.timestamp, as_string=True)
        upload_time_text = time.strftime('%H:%M %m/%d/%Y', time.gmtime(upload_time))
        tracklist_text_list.append(template.format(
            index + 1, track.title, track.url, track_author, upload_time_text, offset,
            utilities.get_time_string(track.duration), track.userid, track.timestamp))

    return '\r\n'.join(tracklist_text_list)


async def format_tracklist(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    # Format tracklist into user-friendly yaml
    tracklist = _get_tracklist(bot, context.guild)
    if not tracklist:
        raise CBException("The playlist queue is empty.", autodelete=autodelete)

    tracklist_string = _build_tracklist(bot, context.guild, tracklist)
    tracklist_file = utilities.get_text_as_file(tracklist_string)

    if use_player_interface:
        url = await utilities.upload_to_discord(bot, tracklist_file, filename='tracklist.txt')
        await music_player.update_interface(
            notification_text='[Click here]({}) to download the current tracklist.'.format(url))
        return Response(content='Tracklist file updated.', delete_after=5)
    else:
        return Response(
            content='Tracks:', file=discord.File(tracklist_file, filename='tracklist.txt'))


async def import_tracklist(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    if not data.has_custom_role(bot, __name__, 'dj', member=context.author):
        raise CBException("You must be a DJ to import tracks.")
    if use_player_interface:
        raise CBException(
            'The player must be stopped before importing tracks.', autodelete=autodelete)

    data.add(bot, __name__, 'import_lock', True, guild_id=context.guild.id, volatile=True)
    try:
        file_url = context.message.attachments[0].url
        tracklist_file = await utilities.download_url(bot, file_url, use_fp=True)

        tracklist_data = yaml.load(tracklist_file)
        if isinstance(tracklist_data, str):  # Read lines instead
            tracklist_file.seek(0)
            tracklist_blob = tracklist_file.read().decode('utf8').replace('\r\n', '\n').strip()
            tracklist_data = tracklist_blob.split('\n')
        logger.debug("Tracklist data: %s", tracklist_data)

        if not tracklist_data or len(tracklist_data) == 0:
            raise CBException("The tracklist file is empty.")
        elif len(tracklist_data) > 100:
            raise CBException("Cannot import more than 100 tracks at a time.")
    except Exception as e:
        data.remove(bot, __name__, 'import_lock', guild_id=context.guild.id, volatile=True)
        if isinstance(e, BotException):
            raise e
        else:
            raise CBException("Failed to load the tracklist file.", e=e)

    return Response(
        content="Importing tracks...",
        message_type=MessageTypes.ACTIVE,
        extra=tracklist_data,
        extra_function=_import_tracklist_status)


async def _import_tracklist_status(bot, context, response):
    try:
        if isinstance(response.extra, list):
            response.extra = OrderedDict((it[0], it[1]) for it in enumerate(response.extra))
        last_update_time = time.time()
        total_imported = 0
        for _, track_blob in sorted(response.extra.items()):
            bot.extra = track_blob  # TODO: remove debug
            cleaned = track_blob.strip()
            if not cleaned:
                continue
            elif '\n' in cleaned:
                title, url, _, info, _ = track_blob.split('\n')
                user_id, _, timestamp = info.split()[3].partition('|')
            else:
                title = url = track_blob
                user_id, timestamp = context.author.id, time.time()

            entry_data = await _add_track_to_db(
                bot, context.guild, url, int(user_id), int(timestamp))
            total_imported += 1

            if time.time() - last_update_time > 5:
                await response.message.edit(content="Importing tracks... [ {} / {} ]".format(
                    total_imported, len(response.extra)))
                last_update_time = time.time()

    except Exception as e:
        data.remove(bot, __name__, 'import_lock', guild_id=context.guild.id, volatile=True)
        try:
            raise CBException("Failed to import track {}".format(title), e=e)
        except NameError:
            raise CBException("Failed to import tracks.", e=e)

    data.remove(bot, __name__, 'import_lock', guild_id=context.guild.id, volatile=True)
    await response.message.edit(content="Imported {} track{}.".format(
        total_imported, '' if total_imported == 1 else 's'))


async def get_info(bot, context):
    """Gets the information for the given track in the playlist."""
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    tracklist = _get_tracklist(bot, context.guild)
    if not tracklist:
        raise CBException("The playlist queue is empty.", autodelete=autodelete)

    index = context.arguments[0] - 1
    if not 0 <= index < len(tracklist):
        raise CBException("Invalid index. Must be between 1 and {} inclusive.".format(
            len(tracklist)), autodelete=autodelete)

    track_info = tracklist[index]
    track_member = data.get_member(bot, track_info.userid)
    title = track_info.title
    if len(title) > 40:
        title = title[:40] + ' **...**'

    time_ago = time.time() - track_info.timestamp
    added_by_text = "Added by <@{}> {} ago.".format(
        track_member.id, utilities.get_time_string(time_ago, text=True))
    duration_text = "Duration: ({})".format(utilities.get_time_string(track_info.duration))
    response = "Info for track {}:".format(index + 1)

    if use_player_interface:  # Add notification
        track_link = '[{}]({} "{} (added by {})")'.format(
            title, track_info.url, track_info.title, track_member)
        info_text = "{} {}\n{}\n{}".format(response, track_link, duration_text, added_by_text)
        music_player.page = int(index / 5)
        await music_player.update_interface(notification_text=info_text)
        return Response(message_type=MessageTypes.REPLACE, extra=autodelete)
    else:
        response += " {} ({})\n{}\n{}".format(title, track_info.url, duration_text, added_by_text)
        return Response(content=response)


async def set_volume(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    volume = context.arguments[0]
    data.add(bot, __name__, 'volume', volume, guild_id=context.guild.id)
    if use_player_interface:
        music_player.update_config()
        await music_player.update_interface(
            notification_text='<@{}> set the volume to {:.2f}%.'.format(
                context.author.id, volume * 100))
    
    return Response(
        content="Volume set to {:.2f}%.".format(volume * 100),
        message_type=MessageTypes.REPLACE if use_player_interface else MessageTypes.NORMAL,
        delete_after=autodelete if use_player_interface else None,
        extra=autodelete if use_player_interface else None)


async def configure_player(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(
        bot, context.guild, autodelete_time=10)
    options = context.options

    if use_player_interface:
        if 'switchmode' in options:
            raise CBException(
                "Cannot switch player modes while it is active.", autodelete=autodelete)
        elif 'channel' in options:
            raise CBException(
                "Cannot set text channel while the player is active.", autodelete=autodelete)

    default_threshold = configurations.get(bot, __name__, key='max_threshold')
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    guild_id = context.guild.id
    changes = []

    if 'threshold' in options:
        threshold = options['threshold']
        data.add(bot, __name__, 'threshold', threshold, guild_id=guild_id)
        changes.append('Duration threshold set to {} seconds.'.format(threshold))

    if 'cutoff' in options:
        cutoff = options['cutoff']
        data.add(bot, __name__, 'cutoff', cutoff, guild_id=guild_id)
        changes.append('Cutoff set to {} seconds.'.format(cutoff))

    if 'djrole' in options:
        dj_role = options['djrole']
        data.add_custom_role(bot, __name__, 'dj', dj_role)
        changes.append('Set the DJ role to {}.'.format(dj_role.mention))

    if 'channel' in options:
        text_channel = options['channel']
        data.add(bot, __name__, 'channel', text_channel.id, guild_id=guild_id)
        changes.append('Set the text channel restriction to {}.'.format(text_channel.mention))

    if 'switchcontrol' in options:
        control = data.get(bot, __name__, 'control', guild_id=guild_id, default=Control.PARTIAL)
        control = 0 if control == len(Control) - 1 else control + 1
        data.add(bot, __name__, 'control', control, guild_id=guild_id)
        changes.append('Cycled the playlist permissions control mode.')

    if 'switchmode' in options:
        mode = data.get(bot, __name__, 'mode', guild_id=guild_id, default=Modes.QUEUE)
        mode = 0 if mode == len(Modes) - 1 else mode + 1
        data.add(bot, __name__, 'mode', mode, guild_id=guild_id)
        changes.append('Cycled the playlist mode.')

    # Format and display all settings
    threshold = data.get(bot, __name__, 'threshold', guild_id=guild_id, default=default_threshold)
    cutoff = data.get(bot, __name__, 'cutoff', guild_id=guild_id, default=default_cutoff)
    dj_role = data.get_custom_role(bot, __name__, 'dj', context.guild)
    control = data.get(bot, __name__, 'control', guild_id=guild_id, default=Control.PARTIAL)
    mode = data.get(bot, __name__, 'mode', guild_id=guild_id, default=Modes.QUEUE)
    text_channel_id = data.get(bot, __name__, 'channel', guild_id=guild_id)
    text_channel = context.guild.get_channel(text_channel_id)

    embed = discord.Embed(
        title='Player configuration', description=(
            'Text channel: {}\nThreshold: {}\nCutoff: {}\n'
            'DJ Role: {}\nControl: {}\nPlayer mode: {}\n'.format(
                text_channel.mention if text_channel else 'None',
                '{} seconds'.format(threshold),
                '{} seconds'.format(cutoff),
                dj_role.mention if dj_role else 'None',
                ('Public', 'Partially public', 'DJs only')[control],
                ('Repeating playlist', 'Single play queue')[mode])
        )
    )

    if changes:
        embed.add_field(name="Changes", value='\n'.join(changes))
        if use_player_interface:
            music_player.update_config()
            await music_player.update_interface('\n'.join(changes))

    return Response(
        embed=embed,
        message_type=MessageTypes.REPLACE if use_player_interface else MessageTypes.NORMAL,
        delete_after=autodelete if use_player_interface else None,
        extra=autodelete if use_player_interface else None)


async def clear_playlist(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    if use_player_interface:
        raise CBException(
            "Cannot clear playlist tracks when the player is active.", autodelete=autodelete)
    
    return Response(
        content="Say 'yes' to confirm clearning the playlist.",
        message_type=MessageTypes.WAIT,
        extra_function=_confirm_clear_playlist,
        extra={
            'event': 'message',
            'kwargs': {
                'timeout': 30,  # Default 300
                'check': lambda m: m.author == context.author,
            }
        }
    )


async def _confirm_clear_playlist(bot, context, response, result):
    if result is None:  # Timed out
        edit = 'Playlist clear timed out.'

    elif result.content.lower() == 'yes':
        music_player = _get_music_player(bot, context.guild)
        if music_player:
            await music_player.update_state()
            use_player_interface = music_player.state is not States.STOPPED
        else:
            use_player_interface = False
        if use_player_interface:
            raise CBException(
                "Cannot clear playlist tracks when the player is active.", autodelete=autodelete)
        data.db_drop_table(bot, 'playlist', table_suffix=context.guild.id, safe=True)
        edit = 'Playlist has been cleared.'

    else:
        edit = 'Playlist clear cancelled.'

    await response.message.edit(content=edit)


async def setup_player(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    # Channel restriction checks
    channel_restriction_id = data.get(bot, __name__, 'channel', guild_id=context.guild.id)
    if channel_restriction_id not in [it.id for it in context.guild.channels]:
        raise CBException(
            "The music player does not have an assigned text channel. Please see "
            "`{}help playlist configure` for more information.".format(
                utilities.get_invoker(bot, guild=context.guild)))
    if channel_restriction_id != context.channel.id:
        channel_restriction = data.get_channel(bot, channel_restriction_id, guild=context.guild)
        raise CBException(
            "The music player must used in the assigned text channel, {}.".format(
                channel_restriction.mention))

    # Voice channel checks
    if not context.author.voice:
        raise CBException(
            "You must be in a voice channel to use the player.", autodelete=autodelete)
    voice_channel = context.author.voice.channel
    if use_player_interface and music_player.voice_channel != voice_channel:
        raise CBException(
            "You must be in the same voice channel as the bot.", autodelete=autodelete)
    elif use_player_interface and music_player.state == States.LOADING:
        raise CBException("Playlist is loading, please wait.", autodelete=autodelete)

    # Check given track index if given
    mode_test = data.get(bot, __name__, 'mode', guild_id=context.guild.id, default=Modes.QUEUE)
    if context.arguments[0] is not None and mode_test == Modes.PLAYLIST:
        new_track_index = context.arguments[0]
        tracklist = _get_tracklist(bot, context.guild)
        if not (0 < new_track_index <= len(tracklist)):
            raise CBException(
                "Track index must be between 1 and {} inclusive.".format(len(tracklist)),
                autodelete=autodelete)
        new_track_index -= 1
        new_track = tracklist[new_track_index]
    else:
        new_track_index = None

    # Check autoplay permissions
    use_autoplay = False
    if 'play' in context.options:
        is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
        control_type = data.get(
            bot, __name__, 'control', guild_id=context.guild.id, default=Control.PARTIAL)
        use_autoplay = control_type == Control.ALL or is_dj

    # Setup new player
    if music_player is None or music_player.state == States.STOPPED:
        logger.debug("Creating new music player.")
        music_player = MusicPlayer(
            bot, context.message, autoplay=use_autoplay, track_index=new_track_index)
        data.add(
            bot, __name__, 'music_player', music_player,
            guild_id=context.guild.id, volatile=True)

    # Update player message or change tracks
    else:
        message_history = await context.channel.history(limit=2).flatten()
        if use_autoplay and new_track_index is not None:
            music_player.notification = '{} skipped to {} (track {}).'.format(
                context.author.mention, music_player._build_hyperlink(new_track),
                new_track_index + 1)

        play_track = bool(
            use_autoplay and (music_player.state == States.PAUSED or new_track_index is not None))

        if len(message_history) > 1 and message_history[1].id == music_player.message.id:
            logger.debug("Music player already detected in place.")
            # Only play if the player is paused or we're requesting a specific track
            if play_track:
                asyncio.ensure_future(music_player.play(track_index=new_track_index))
            return Response(message_type=MessageTypes.REPLACE)

        else:
            logger.debug("Setting new message. Here's the state: %s", music_player.state)
            await music_player.set_new_message(
                context.channel, autoplay=use_autoplay if play_track else None,
                track_index=new_track_index)
