import random
import asyncio
import time
import math

import yaml
import discord

from urllib.parse import urlparse
from collections import OrderedDict, deque
from psycopg2.extras import Json
from datetime import datetime

from enum import Enum, IntEnum
from youtube_dl import YoutubeDL
from tinytag import TinyTag

from jshbot import utilities, configurations, data, plugins, logger
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.3.11'
CBException = ConfiguredBotException('Music playlist')
uses_configuration = True

TITLE_LIMIT = 50  # Track title character limit in the track explorer
URL_LIMIT = 140  # Track URL limit to be displayed in the track explorer
MIRROR_TIMER = 60  # Chat mirror timer in seconds

class States(IntEnum):
    PLAYING, PAUSED, STOPPED, LOADING = range(4)

class Modes(IntEnum):
    PLAYLIST, QUEUE = range(2)

class Control(IntEnum):
    ALL, PARTIAL, DJS = range(3)


@plugins.command_spawner
def get_commands(bot):

    max_threshold = configurations.get(bot, __name__, key='max_threshold')
    max_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    max_user_track_limit = configurations.get(bot, __name__, key='max_user_track_limit')
    max_total_track_limit = configurations.get(bot, __name__, key='max_total_track_limit')

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
                Opt('youtube', attached='url', optional=True, quotes_recommended=False),
                Attachment('tracklist file', optional=True),
                doc='Adds the tracks in the attached tracklist file, '
                    'or from the YouTube playlist link. Only DJs can import '
                    'tracks to prevent abuse.',
                function=import_tracklist),
            SubCommand(
                Opt('info'),
                Arg('track number', quotes_recommended=False, convert=int),
                doc='Retrieves the song information of the given track number.',
                function=get_info),
            SubCommand(
                Opt('add'),
                Arg('query', argtype=ArgTypes.MERGED),
                doc='Adds a song to the playlist. Can either be a URL to a supported site '
                    '(YouTube, Bandcamp, SoundCloud, etc.) or a YouTube search query',
                function=add_track),
            SubCommand(
                Opt('remove'),
                Arg('track number', quotes_recommended=False, convert=int),
                doc='Removes the given track number from the playlist.',
                function=remove_track),
            SubCommand(
                Opt('volume'),
                Arg('percent', quotes_recommended=False,
                    convert=utilities.PercentageConverter(),
                    check=lambda b, m, v, *a: 0.01 <= v <= 1.0,
                    check_error='Must be between 1% and 100% inclusive.'),
                doc='Sets the player volume to the given percentage.',
                function=set_volume),
            SubCommand(
                Opt('configure'),
                Opt('threshold', attached='seconds', optional=True, group='options',
                    quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 10 <= v <= max_threshold,
                    check_error='Must be between 10 and {} seconds.'.format(max_threshold)),
                Opt('cutoff', attached='seconds', optional=True, group='options',
                    quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 10 <= v <= max_cutoff,
                    check_error='Must be between 10 and {} seconds.'.format(max_cutoff)),
                Opt('usertracks', attached='limit', optional=True, group='options',
                    quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 0 <= v <= max_user_track_limit,
                    check_error='Must be between 0 and {}.'.format(max_user_track_limit),
                    doc='Limits the number of tracks users can add to the player. 0 for no limit'),
                Opt('totaltracks', attached='limit', optional=True, group='options',
                    quotes_recommended=False, convert=int,
                    check=lambda b, m, v, *a: 0 <= v <= max_total_track_limit,
                    check_error='Must be between 0 and {}.'.format(max_total_track_limit),
                    doc='Limits the total number of tracks for the player. 0 for no limit'),
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
                Opt('mirrorchat', optional=True, group='options',
                    doc='Mirrors the last few chat messages to a message above the player.'),
                doc='Configures the music player properties.',
                function=configure_player),
            SubCommand(Opt('clear'), doc='Clears the playlist.', function=clear_playlist),
            SubCommand(
                Opt('page'),
                Arg('number', convert=int, quotes_recommended=False),
                doc='Displays the given page.', function=skip_to_page),
            SubCommand(
                Opt('swap'),
                Arg('track 1', convert=int, quotes_recommended=False),
                Arg('track 2', convert=int, quotes_recommended=False),
                doc='Swaps the position of the given tracks.', function=swap_tracks),
            SubCommand(
                Opt('control'),
                Opt('pause', optional=True, group='action'),
                Opt('resume', optional=True, group='action'),
                Opt('stop', optional=True, group='action'),
                Opt('next', optional=True, group='action'),
                Opt('skip', optional=True, group='action'),
                Opt('previous', optional=True, group='action'),
                doc='Basic controls for the player. Only one option can be provided at a time.',
                confidence_threshold=10, function=control_player),
            SubCommand(
                Opt('play'),
                Opt('track', attached='track number', optional=True,
                    quotes_recommended=False, convert=int,
                    doc='Plays the given track number.'),
                Arg('query', argtype=ArgTypes.MERGED_OPTIONAL,
                    doc='Either a URL to a supported site (YouTube, Bandcamp, '
                    'SoundCloud, etc.), or a YouTube search query.'),
                confidence_threshold=5, doc='Plays (or adds) the given track.',
                function=setup_player, id='play'),
            SubCommand(doc='Shows the music player interface.', function=setup_player, id='show'),
        ],
        shortcuts=[
            Shortcut('p', '{arguments}', Arg('arguments', argtype=ArgTypes.MERGED_OPTIONAL)),
            Shortcut('add', 'add {query}', Arg('query', argtype=ArgTypes.MERGED)),
            Shortcut('remove', 'remove {number}', Arg('number', argtype=ArgTypes.MERGED)),
            Shortcut('volume', 'volume {percent}', Arg('percent', argtype=ArgTypes.MERGED)),
            Shortcut(
                'play', 'play {arguments}',
                Arg('arguments', argtype=ArgTypes.MERGED_OPTIONAL)),
            Shortcut('pause', 'control pause'),
            Shortcut('resume', 'control resume'),
            Shortcut('skip', 'control skip'),
            Shortcut('next', 'control next'),
            Shortcut('previous', 'control previous')],
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
        self.embed = None
        self.message = None  # Set later
        self.satellite_message = None
        self.satellite_data = None
        self.mirror_message = None
        self.mirror_chats = None
        self.mirror_last_notification = None
        self.mirror_notifications = deque(maxlen=5)
        self.mirror_chats = deque(maxlen=12)

        # Update/internal tasks
        self.timer_task = None  # Player timer
        self.command_task = None  # Waits for reaction commands
        self.progress_task = None  # Refreshes the progress bar
        self.state_check_task = None  # Checks voice state changes
        self.chat_mirror_task = None  # Mirrors chat every 10 seconds
        self.autoplay_task = None  # Short-lived task for autostarting the player

        # Player information
        self.state = States.LOADING
        self.loading_interface = False
        self.first_time_startup = True
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
        self.tracklist = None
        self.tracklist_url = ''
        self.tracklist_time = 0
        self.tracklist_update_time = 0
        self.update_tracklist()
        self.update_config()

        if self.mode == Modes.QUEUE:
            self.track_index = 0  # Track index in queue mode doesn't change
        else:
            if self.shuffle and self.tracklist:
                self.track_index = random.randint(0, len(self.tracklist) - 1)
            else:
                self.track_index = data.get(
                    self.bot, __name__, 'last_index', guild_id=self.guild.id, default=0)
                if not 0 <= self.track_index < len(self.tracklist):
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
        self.mirror_chat = data.get(
            self.bot, __name__, 'mirror_chat', guild_id=guild_id, default=False)

        self.volume = data.get(self.bot, __name__, 'volume', guild_id=guild_id, default=1.0)
        if self.source:
            self.source.volume = self.volume

        # Actively update threshold/cutoff timer
        if self.timer_task and self.state == States.PLAYING:
            self.timer_task.cancel()
            self.timer_task = asyncio.ensure_future(
                self._track_timer(*self._get_delay(config_update=True)))

    async def _connect(self, autoplay=False, track_index=None):
        is_mod = data.is_mod(self.bot, member=self.author)
        try:
            self.voice_client = await utilities.join_and_ready(
                self.bot, self.voice_channel, is_mod=is_mod, reconnect=True)
        except Exception as e:
            self.state = States.STOPPED
            error = CBException("Failed to start the player interface.", e=e)
            await self.channel.send(embed=error.embed)
        else:
            await asyncio.sleep(1)  # Safety sleep
            await self._build_interface()
            # Start playback if necessary
            if autoplay:
                self.autoplay_task = asyncio.ensure_future(
                    self._autoplay(track_index=track_index))

    async def _autoplay(self, track_index=None):
        safety_timeout = 0
        while self.state == States.LOADING:
            if safety_timeout > 30:
                raise CBException("Autoplay failed.")
            await asyncio.sleep(0.5)
            safety_timeout += 0.5
        asyncio.ensure_future(self.play(track_index=track_index, author=self.author))

    def update_tracklist(self):
        self.tracklist_update_time = time.time()
        self.tracklist = _get_tracklist(self.bot, self.guild)

    async def update_state(self):
        if self.state == States.STOPPED:
            return
        if not (self.voice_client and self.voice_channel):
            logger.warn("update_state detected that the bot disconnected. Stopping now.")
            await self.stop(
                text="The player has been stopped due to an undetected disconnection.")
        elif (
                (self.voice_client.is_playing() and self.voice_client.source != self.source) or
                self.guild.me not in self.voice_channel.members):
            logger.warn("update_state detected an unstopped instance. Stopping now.")
            await self.stop(
                text="The player has been stopped due to a different audio source being in use.")

    async def reset_player_messages(self):
        """Rebuilds the set of 3 messages if one is somehow deleted."""
        await self.set_new_message(self.message)
        self.mirror_last_notification = ""
        self.notification = "A message was unexpectedly deleted."

    async def set_new_message(self, message, autoplay=False, track_index=None):
        """Bumps up the player interface to the bottom of the channel."""

        # Prevent issues with trying to set a new message too quickly
        if self.loading_interface:
            logger.warn("Ignoring interface refresh reques as the interface is still loading")
            if autoplay:
                self.autoplay_task = asyncio.ensure_future(
                    self._autoplay(track_index=track_index))
            return
        self.loading_interface = True

        if self.command_task:
            self.command_task.cancel()
        if self.progress_task:
            self.progress_task.cancel()
        if self.state_check_task:
            self.state_check_task.cancel()
        if self.chat_mirror_task:
            self.chat_mirror_task.cancel()
        if self.message:
            for old_message in (self.message, self.satellite_message, self.mirror_message):
                try:
                    await old_message.delete()
                except Exception as e:
                    logger.warn("Couldn't delete original messages: %s", e)

        self.channel = message.channel
        self.author = message.author
        self.satellite_data = None  # Force update
        asyncio.ensure_future(self._build_interface(resume=self.state == States.PLAYING))
        if autoplay:
            self.autoplay_task = asyncio.ensure_future(
                self._autoplay(track_index=track_index))

    async def _build_interface(self, resume=False):
        """Sets up player messages and the main interface structure."""
        self.state = States.LOADING
        self.loading_interface = True
        self.satellite_message = await self.channel.send(embed=discord.Embed(title="\u200b"))
        self.mirror_message = await self.channel.send(embed=discord.Embed(title="\u200b"))
        embed = discord.Embed(colour=discord.Colour(0xffab00))
        embed.add_field(  # Title
            name=':arrows_counterclockwise: **[]**',
            value='**`[{}]` [ `0:00` / `0:00` ]**'.format('-' * 50), inline=False)
        embed.add_field(name='---', value='---', inline=False)  # Info
        embed.add_field(name='---', value='---', inline=False)  # Listeners
        embed.add_field(name='---', value='---\n' * 6, inline=False)  # Tracklist
        embed.add_field(name='---', value='---')  # Notification
        self.embed = embed
        self.message = await self.channel.send(embed=embed)
        self.command_task = asyncio.ensure_future(self._command_listener(resume=resume))

    async def _progress_loop(self):
        """Refreshes the progress bar."""
        await asyncio.sleep(5)
        while True:
            await self.update_state()
            if self.state == States.PLAYING:
                self.update_listeners(update_interface=False)
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

    async def _chat_mirror_loop(self):
        """Mirrors chat messages after 10 seconds."""

        async def _delete_and_update(message):
            await asyncio.sleep(MIRROR_TIMER)
            if self.state == States.STOPPED or not self.mirror_chat:
                return
            try:
                await message.delete()
            except Exception as e:
                pass
            else:
                await self.update_mirror(new_chat=message)

        while True:
            message = await self.bot.wait_for('message')
            if (not self.mirror_chat or
                    not message or
                    self.state == States.STOPPED or
                    message.channel != self.channel):
                continue

            # Don't log player messages by the bot or non-standard messages (like pins)
            player_messages = (self.message.id, self.satellite_message.id, self.mirror_message.id)
            if message.type is discord.MessageType.default and message.id not in player_messages:
                asyncio.ensure_future(_delete_and_update(message))

    async def _listener_loop(self):
        """Checks the state of members in the voice channel."""

        class VoiceChange(Enum):
            NORMAL, LEFT, JOINED = range(3)

        def check(member, before, after):
            if member.guild != self.guild:
                return VoiceChange.NORMAL
            elif not member == self.bot.user and (member.bot or not (before or after)):
                return VoiceChange.NORMAL
            elif after and after.channel == self.voice_channel:
                if not before or before.channel != self.voice_channel:
                    return VoiceChange.JOINED
            elif before and before.channel == self.voice_channel:
                if not after or after.channel != self.voice_channel:
                    return VoiceChange.LEFT
            return VoiceChange.NORMAL

        # Preliminary check
        self.listeners = len([it for it in self.voice_channel.members if not it.bot])

        # Wait on voice state updates to determine users entering/leaving
        while True:
            result = await self.bot.wait_for('voice_state_update')
            if not result:
                continue
            elif self.state == States.STOPPED:
                return
            member, before, after = result

            # Check for self changes
            if member == self.bot.user and member.guild == self.guild:
                if not after:  # Disconnected
                    # TODO: Consider adding failsafe stop
                    logger.warn("Voice disconnected, detected from _listener_loop.")
                    return
                if before != after:
                    logger.debug("Bot was dragged to a new voice channel.")
                    if after.channel == self.guild.afk_channel:  # TODO: Act on AFK channel
                        logger.warn("Moved to the AFK channel. Failsafe stopping.")
                    self.voice_channel = after.channel
                    self.voice_client = self.guild.voice_client

            # Update listener count
            self.listeners = len([it for it in self.voice_channel.members if not it.bot])
            logger.debug("Voice state updated. Listeners: %s", self.listeners)
            self.update_listeners(update_interface=False)

            voice_change = check(*result)
            if voice_change is VoiceChange.LEFT:
                if member.id in self.skip_voters:
                    self.skip_voters.remove(member.id)
                asyncio.ensure_future(self.update_interface(ignore_ratelimit=True))
            elif voice_change is VoiceChange.JOINED:
                asyncio.ensure_future(self.update_interface(ignore_ratelimit=True))

            if self.listeners == 0:
                self.autopaused = True
                self.notification = "The player has been automatically paused"
                asyncio.ensure_future(self.pause())

    def update_listeners(self, update_interface=True):
        """Updates the number of listeners and skips the song if enough people have voted."""

        current_listeners = [it.id for it in self.voice_channel.members]
        for member_id in self.skip_voters[:]:
            if member_id not in current_listeners:
                self.skip_voters.remove(member_id)

        # Skip if enough votes
        needed_votes = math.ceil(self.listeners * self.skip_threshold)
        if needed_votes and len(self.skip_voters) >= needed_votes:
            index_string = '[[Track{}]{}]'.format(
                ' {}'.format(self.track_index + 1) if self.mode == Modes.PLAYLIST else '',
                _build_shortlink(self.bot, self.now_playing))
            self.notification = "{} was voteskipped ({} vote{})".format(
                index_string, len(self.skip_voters), '' if len(self.skip_voters) == 1 else 's')
            del self.skip_voters[:]
            self._skip_track()
        elif update_interface:
            asyncio.ensure_future(self.update_interface(ignore_ratelimit=True))

    async def update_interface(self, notification_text='', ignore_ratelimit=False):
        """Calls the other functions to update the main interface."""
        await self.update_notification(text=notification_text)
        await self.update_title()
        await self.update_info()
        await self.update_footer()
        if not ignore_ratelimit and time.time() - self.last_interface_update < 1:
            return
        try:
            await self.message.edit(content=None, embed=self.embed)
            self.last_interface_update = time.time()
        except discord.NotFound:
            await self.reset_player_messages()

    async def update_satellite(self):
        """Updates the satellite with track data."""

        if not self.now_playing and self.satellite_data:  # Player stopped
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
            chunks = [description[it:it + 1000] for it in range(0, len(description), 1000)]
            if len(chunks) > 3:
                chunks = chunks[:3]
                chunks[-1] += '…'
            for index, chunk in enumerate(chunks):
                embed.add_field(name='Description' if index == 0 else '\u200b', value=chunk)

        if 'thumbnail' in extra:
            embed.set_image(url=extra['thumbnail'])

        if 'artist_thumbnail' in extra:
            embed.set_thumbnail(url=extra['artist_thumbnail'])

        try:
            await self.satellite_message.edit(embed=embed)
        except discord.NotFound:
            await self.reset_player_messages()

    async def update_mirror(self, new_notification=None, new_chat=None):
        """Updates the mirror message with notification or chat data."""

        if new_notification:
            if new_notification != self.mirror_last_notification:
                self.mirror_last_notification = new_notification
                self.mirror_notifications.append(new_notification)
        if new_chat:
            self.mirror_chats.append(new_chat)

        embed = discord.Embed()
        while sum(len(it) for it in self.mirror_notifications) > 1000:
            self.mirror_notifications.popleft()
        notifications = '\u200b' + '\n'.join(self.mirror_notifications)
        embed.add_field(name='Recent notifications:', value=notifications, inline=False)

        if self.mirror_chat:

            for _ in range(3):
                embed.add_field(name='\u200b', value='\u200b', inline=False)
            formatted_chats = []

            def _length_check(segment_index):
                """Checks the length of a set of 4 messages given the segment."""
                segment = formatted_chats[4 * segment_index:4 * segment_index + 4]
                return sum(len(it) for it in segment) < 1000

            # Format messages
            for message in self.mirror_chats:
                if message.attachments:
                    attachment = ' [(Attachment)]({})'.format(message.attachments[0].url)
                else:
                    attachment = ''
                if message.content:
                    content = message.content
                elif message.embeds:
                    title, description = message.embeds[0].title, message.embeds[0].description
                    title_text = '{}: '.format(title) if title else ''
                    description_text = description if description else '[No description]'
                    content = '{}{}'.format(title_text, description_text)
                else:
                    content = '[Empty message]'
                if len(content) > 500:
                    content = content[:500] + '…'
                content = content.replace('```', '\`\`\`')
                formatted_chats.append('[{}{}]: {}'.format(
                    message.author.mention, attachment, content))

            # Remove messages if one is too long
            for it in range(2, -1, -1):
                while not _length_check(it):
                    del formatted_chats[0]

            # Set embeds
            segments = [formatted_chats[it:it + 4] for it in range(0, 12, 4)]
            for index, segment in enumerate(segments):
                embed.set_field_at(
                    index + 1, name='Recent chat messages:' if index == 0 else '\u200b',
                    value='\u200b' + '\n'.join(segment), inline=False)

        try:
            await self.mirror_message.edit(embed=embed)
        except discord.NotFound:
            await self.reset_player_messages()

    async def update_footer(self):
        """Updates volume display, control type, and player mode in the footer."""
        if self.volume < 0.3:
            volume_indicator = '\U0001F508'
        elif self.volume < 0.6:
            volume_indicator = '\U0001F509'
        else:
            volume_indicator = '\U0001F50A'
        footer_text = '{}: {}% | {} | {}{}{} | Click \u2753 for help'.format(
            volume_indicator,
            int(self.volume * 100),
            ('Public', 'Partially public', 'DJs only')[self.control],
            '\U0001F500 ' if self.mode == Modes.PLAYLIST and self.shuffle else '',
            ('Playlist', 'Queue')[self.mode],
            ' | Mirroring chat' if self.mirror_chat else '')
        self.embed.set_footer(text=footer_text)

    async def update_title(self):
        """Updates the now playing title and progress bar"""
        # Calculate progress and set embed color
        if self.state == States.PLAYING:
            progress = self.progress + (time.time() - self.start_time)
            status_icon = ':arrow_forward:'
            color = discord.Color(0x3b88c3)
        elif self.state == States.PAUSED:
            progress = self.progress
            status_icon = ':pause_button:'
            color = discord.Color(0xccd6dd)
        else:
            progress = 0
            status_icon = ':arrows_counterclockwise:'
            color = discord.Color(0xffab00)
        self.embed.color = color

        # Set title and progress
        if self.now_playing:
            title = _truncate_title(self.now_playing.title, limit=60)
            duration = self.now_playing.duration
        else:
            title = '---'
            duration = 0
        new_name = '{} **[{}]**'.format(status_icon, title)
        percentage = 0 if duration == 0 else progress / duration
        progress_bar = '\u2588' * int(50 * percentage)
        new_value = '**`[{:-<50}]` [ `{}` / `{}` ]**'.format(
            progress_bar, utilities.get_time_string(progress),
            utilities.get_time_string(duration))

        self.embed.set_field_at(0, name=new_name, value=new_value, inline=False)

    async def update_info(self):
        """Updates the info, listeners, and track list explorer display."""
        # Listeners
        new_name = '{} listener{}'.format(self.listeners, '' if self.listeners == 1 else 's')
        new_value = '[ {} / {} ] :eject: votes needed to skip'.format(
            len(self.skip_voters), math.ceil(self.listeners * self.skip_threshold))
        self.embed.set_field_at(2, name=new_name, value=new_value, inline=False)

        # Tracklist slice
        total_tracks = len(self.tracklist)
        total_duration = sum(it.duration for it in self.tracklist)
        total_pages = max(int((total_tracks + 4) / 5), 1)
        self.page %= total_pages
        displayed_tracks = self.tracklist[self.page * 5:(self.page * 5) + 5]

        # Build individual track entries from slice
        info = ['---'] * 5 + ['Page [ {} / {} ]'.format(self.page + 1, total_pages)]
        for index, entry in enumerate(displayed_tracks):
            duration = utilities.get_time_string(entry.duration)
            entry_index = (self.page * 5) + index + 1
            full_title = entry.title.replace('`', '').replace('*', '')
            title = _truncate_title(full_title)
            use_indicator = entry_index == self.track_index + 1 and self.mode == Modes.PLAYLIST
            info[index] = ('**[`{}{}`]{}**: ({}) *{}*'.format(
                '▶ ' if use_indicator else '', entry_index,
                _build_shortlink(self.bot, entry), duration, title))
        new_value = '\n'.join(info)

        # Total tracks and runtime
        player_mode = 'queued' if self.mode == Modes.QUEUE else 'in the playlist'
        if total_tracks > 0:
            new_name = '{} track{} {} (runtime of {}):'.format(
                total_tracks, '' if total_tracks == 1 else 's', player_mode,
                utilities.get_time_string(total_duration, text=True))
        else:
            new_name = 'No tracks {}'.format(player_mode)

        self.embed.set_field_at(3, name=new_name, value=new_value, inline=False)

        # Info
        if self.now_playing:
            new_name = 'Info:'
            time_ago = time.time() - self.now_playing.timestamp
            index_string = '[[Track{}]{}]'.format(
                ' {}'.format(self.track_index + 1) if self.mode == Modes.PLAYLIST else '',
                _build_shortlink(self.bot, self.now_playing))
            new_value = 'Playing: {} Added by <@{}> {} ago'.format(
                index_string, self.now_playing.userid,
                utilities.get_time_string(time_ago, text=True))
        else:
            new_name = '---'
            new_value = '---'

        # Determine next track
        if len(self.tracklist) == 0:
            next_index = -1
            new_value += '\n---'
        elif self.now_playing is None:
            next_index = 0 if self.mode == Modes.QUEUE else self.track_index
        elif self.track_index + 1 >= len(self.tracklist):
            next_index = 0
        else:
            if self.mode == Modes.PLAYLIST:
                next_index = self.track_index + 1
            else:
                next_index = 0

        # Show next track if available
        if next_index != -1:
            next_track = self.tracklist[next_index]
            if next_index >= 0:
                if self.mode == Modes.PLAYLIST and self.shuffle:
                    new_value += '\nUp next: [Track ?]'
                else:
                    new_value += '\nUp next: {}'.format(
                        _build_track_details(self.bot, next_track, next_index))

        self.embed.set_field_at(1, name=new_name, value=new_value, inline=False)

    async def update_notification(self, text=''):
        if text:
            self.notification = text
        elif not self.notification:
            self.notification = 'No notification.'
        if self.notification != self.mirror_last_notification:
            asyncio.ensure_future(self.update_mirror(new_notification=self.notification))
        self.embed.set_field_at(4, name='Notification:', value=self.notification)

    def _skip_track(self):
        """Skips the current track (even if paused)."""
        delta = 1 if self.mode == Modes.PLAYLIST else 0
        if self.mode == Modes.PLAYLIST and self.shuffle:
            if self.now_playing:
                self.shuffle_stack.append(self.now_playing.id)
            if len(self.tracklist) > 1:
                new_track_index = random.randint(0, len(self.tracklist) - 2)
                if new_track_index >= self.track_index:
                    new_track_index += 1
            else:
                new_track_index = 0
        else:
            new_track_index = self.track_index + delta
        asyncio.ensure_future(self.play(track_index=new_track_index))

    async def _track_timer(self, sleeptime, use_skip=False):
        """Sleeps until the end of the song or cutoff. Plays the next track afterwards."""
        logger.debug("Sleeping for %s seconds. Time: %s", sleeptime, time.time())
        track_check = self.now_playing
        await asyncio.sleep(sleeptime)
        logger.debug("Finished sleeping for %s seconds. Time: %s", sleeptime, time.time())
        await self.update_state()
        if self.state == States.STOPPED or track_check != self.now_playing:
            logger.debug("The track timer resumed?")
            return
        while self.state == States.LOADING:
            logger.warn("Player was moved while the track was loading.")
            await asyncio.sleep(1)
        if self.mode == Modes.PLAYLIST and self.shuffle:
            logger.debug("Adding track %s to the shuffle stack", track_check.title)
            self.shuffle_stack.append(track_check.id)
            if len(self.tracklist) > 1:
                new_track_index = random.randint(0, len(self.tracklist) - 2)
                if new_track_index >= self.track_index:
                    new_track_index += 1
            else:
                new_track_index = 0
            asyncio.ensure_future(self.play(track_index=new_track_index, skipped=use_skip))
        else:
            logger.debug('_track_timer is moving on: %s', use_skip)
            asyncio.ensure_future(self.play(skipped=use_skip))

    def _get_delay(self, config_update=False):  # Gets track delay with cutoff
        if self.now_playing.duration > self.threshold:
            duration = self.cutoff
            use_skip = self.now_playing
        else:
            duration = self.now_playing.duration
            use_skip = False
        if config_update:
            current_progress = self.progress + time.time() - self.start_time
        else:
            current_progress = self.progress
        return (max(duration - current_progress, 0), use_skip)

    async def play(self, track_index=None, skipped=False, wrap_track_numbers=True, author=None):
        """Plays (the given track).

        Keyword arguments:
        track_index -- The specific track to play.
            In queue mode, -1 indicates to repeat the current track.
        skipped -- Whether or not the last track was skipped due to a length constraint.
        wrap_track_numbers -- Wraps out-of-bounds track indices to the nearest edge.
        author -- If provided, displays a notification on who started the player.
        """
        # Ignore loading player
        if self.state in (States.LOADING, States.STOPPED):
            return

        # Resume player if paused
        if (self.state == States.PAUSED and
                self.now_playing and self.progress and track_index is None):
            self.state = States.PLAYING
            self.voice_client.resume()
            self.start_time = time.time()
            self.timer_task = asyncio.ensure_future(self._track_timer(*self._get_delay()))
            author_text = '{} resumed the player'.format(author.mention) if author else ''
            asyncio.ensure_future(self.update_interface(notification_text=author_text))
            self.autopaused = False  # Reset single-time resume state
            return

        # No more tracks left to play
        if len(self.tracklist) == 0 and not (track_index == -1 and self.state == States.PLAYING):
            self.notification = "There are no more tracks in the queue"
            if self.voice_client.is_playing():
                self.voice_client.stop()
            self.source = None
            self.now_playing = None
            self.first_time_startup = True  # Reset so non-DJs can start the player again
            self.progress = 0
            self.state = States.PAUSED
            asyncio.ensure_future(self.update_interface(ignore_ratelimit=True))
            asyncio.ensure_future(self.update_satellite())
            return

        # No track index was given - act as a skip
        if track_index is None and self.now_playing:
            if self.mode == Modes.PLAYLIST:
                self.track_index = (self.track_index + 1) % len(self.tracklist)

        # A specific track index was given
        elif track_index is not None:
            if track_index != -1 and not 0 <= track_index < len(self.tracklist):
                if wrap_track_numbers:
                    if track_index >= len(self.tracklist):
                        track_index = 0
                    elif track_index < 0:
                        track_index = -1
                else:
                    self.notification = (
                        'Index must be between 1 and {} inclusive'.format(len(self.tracklist)))
                    asyncio.ensure_future(self.update_interface())
                    return

            # Wrap a backwards skip to the end of the playlist in playlist mode
            if self.mode == Modes.PLAYLIST:
                if track_index == -1:
                    track_index = len(self.tracklist) - 1
                self.track_index = track_index

        # Track from playlist
        if self.mode == Modes.PLAYLIST:
            track = self.tracklist[self.track_index]

        # Track from queue
        else:

            # Repeat current track
            if track_index == -1:
                if self.now_playing:
                    track = self.now_playing
                else:
                    return

            # Skip to specific track by removing it from the database first
            else:
                if track_index is None:
                    track_index = 0
                track = self.tracklist[0 if track_index == -1 else track_index]
                data.db_delete(
                    self.bot, 'playlist', table_suffix=self.guild.id,
                    where_arg='id=%s', input_args=[track.id])
                self.update_tracklist()

        self.autopaused = False  # Reset single-time resume state

        # Setup the player
        logger.debug("Preparing to play the next track.")
        self.page = int(self.track_index / 5)
        del self.skip_voters[:]
        if self.state == States.PLAYING:
            if self.voice_client.is_playing():
                self.voice_client.stop()
        if self.timer_task:
            self.timer_task.cancel()
        self.first_time_startup = not bool(self.now_playing)
        self.state = States.LOADING
        self.now_playing = track
        sound_file = data.get_from_cache(self.bot, None, url=track.url)

        # Audio not found in cache, download now instead
        if not sound_file:
            asyncio.ensure_future(self.update_interface())
            logger.debug("Not found in cache. Downloading...")

            try:
                options = {'format': 'bestaudio/best', 'noplaylist': True}
                downloader = YoutubeDL(options)
                sound_file = await data.add_to_cache_ydl(self.bot, downloader, track.url)
            except Exception as e:  # Attempt to redownload from base url
                logger.warn("Failed to download track %s\n%s", track.url, e)
                self.notification = "Failed to download {}. Failsafe skipping...".format(
                    track.title)
                self.state = States.PAUSED
                self._skip_track()
                return

        # TODO: Add exception handling
        # TODO: Change ffmpeg_options for docker version
        #ffmpeg_options = '-protocol_whitelist "file,http,https,tcp,tls"'
        #audio_source = discord.FFmpegPCMAudio(sound_file, before_options=ffmpeg_options)
        audio_source = discord.FFmpegPCMAudio(sound_file)

        # Set volume and play audio
        audio_source = discord.PCMVolumeTransformer(audio_source, volume=self.volume)
        self.voice_client.play(audio_source)
        self.source = audio_source

        # Record progress time
        self.progress = 0
        self.start_time = time.time()
        self.state = States.PLAYING
        self.timer_task = asyncio.ensure_future(self._track_timer(*self._get_delay()))
        if skipped:
            self.notification = (
                'The track *{}* was cut short because it exceeded '
                'the song length threshold of {} seconds.'.format(
                    _build_hyperlink(self.bot, skipped), self.threshold))
        elif self.first_time_startup and author:
            self.notification = '{} started the player'.format(author.mention)

        asyncio.ensure_future(self.update_interface(ignore_ratelimit=True))
        data.add(self.bot, __name__, 'last_index', self.track_index, guild_id=self.guild.id)

    async def pause(self, author=None):
        if (self.state in (States.PAUSED, States.LOADING, States.STOPPED) or
                self.voice_client is None or not self.voice_client.is_playing()):
            return
        if self.timer_task:
            self.timer_task.cancel()
        self.voice_client.pause()
        self.state = States.PAUSED
        self.progress += time.time() - self.start_time
        author_text = '{} paused the player'.format(author.mention) if author else ''
        asyncio.ensure_future(self.update_interface(
            notification_text=author_text, ignore_ratelimit=True))

    async def stop(self, text="The player has been stopped."):
        logger.debug("Stopping the player!")
        await utilities.stop_audio(self.bot, self.guild)
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
            if self.chat_mirror_task:
                self.chat_mirror_task.cancel()
        except Exception as e:
            logger.warn("Failed to stop some task. %s", e)
        try:
            asyncio.ensure_future(self.satellite_message.delete())
            asyncio.ensure_future(self.mirror_message.delete())
            asyncio.ensure_future(self.message.clear_reactions())
            asyncio.ensure_future(self.message.edit(content=text, embed=None))
        except Exception as e:
            logger.warn("Failed to modify the original message %s", e)
            pass

    async def track_navigate(self, use_skip, member):
        """Navigates the track (next, previous, or repeat). Returns True if successful."""
        is_dj = data.has_custom_role(self.bot, __name__, 'dj', member=member)

        # Build skip text
        use_repeat = time.time() - self.start_time >= 10 and self.now_playing
        self_skip = False
        if use_skip:
            skip_format = '{} skipped {}'
            if self.now_playing and self.now_playing.userid == member.id:
                self_skip = True
            elif not self.now_playing:
                skip_format = '{} played the queued track'
        else:
            if self.now_playing and (use_repeat or self.mode == Modes.QUEUE):
                skip_format = '{} repeated {}'
            elif self.now_playing:
                skip_format = '{} skipped back from {}'
            else:
                skip_format = '{} skipped back a track'

        # Skip track only if the user is a DJ or was the one that added it
        if not self_skip and not is_dj and not self.control == Control.ALL:
            return False

        if self.now_playing:
            track_details = _build_track_details(
                self.bot, self.now_playing, self.track_index)
        else:
            track_details = ''
        self.notification = skip_format.format(member.mention, track_details)

        # Determine track delta
        if self.mode == Modes.PLAYLIST:
            # Repeat track if more than 10 seconds have elapsed
            start_delta = 1 if self.now_playing else 0
            delta = start_delta if use_skip else (0 if use_repeat else -1)
        else:
            delta = 0 if use_skip else -1

        if self.mode == Modes.PLAYLIST and self.shuffle and delta != 0:
            last_track = None
            if not use_skip and self.shuffle_stack:  # Check shuffle stack first
                last_track_id = self.shuffle_stack.pop()
                for new_track_index, track in enumerate(self.tracklist):
                    if track.id == last_track_id:
                        last_track = track
                        break
            if last_track is None:
                if self.now_playing:
                    self.shuffle_stack.append(self.now_playing.id)
                if len(self.tracklist) > 1:
                    new_track_index = random.randint(0, len(self.tracklist) - 2)
                    if new_track_index >= self.track_index:
                        new_track_index += 1
                else:
                    new_track_index = 0
        else:
            new_track_index = self.track_index + delta
        asyncio.ensure_future(self.play(track_index=new_track_index))
        return True

    async def _command_listener(self, resume=False):
        valid_commands = ('⏮', '⏯', '⏭', '⏹', '🔀', '🎵', '⬅', '⏺', '➡', '⏏', '❓')

        async def _add_buttons():
            """Adds the buttons in the background to show interface immediately."""
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

            # Safety interface update
            asyncio.ensure_future(self.update_interface())
            await asyncio.sleep(1)
            self.loading_interface = False

        self.progress_task = asyncio.ensure_future(self._progress_loop())
        self.state_check_task = asyncio.ensure_future(self._listener_loop())
        self.chat_mirror_task = asyncio.ensure_future(self._chat_mirror_loop())
        self.page = int(self.track_index / 5)
        asyncio.ensure_future(self.update_interface())
        asyncio.ensure_future(_add_buttons())

        # Startup - finished loading basics
        if self.state == States.LOADING:
            self.state = States.PLAYING if resume else States.PAUSED

        try:  # TODO: Remove try/except block
            while True:
                # Wait on reaction command
                kwargs = {'check': lambda r, u: r.message.id == self.message.id and not u.bot}
                logger.debug("Waiting on command...")
                result = await self.bot.wait_for('reaction_add', **kwargs)
                if result is None or self.state == States.STOPPED:
                    return
                elif result[1] == self.bot.user:
                    continue

                # Check validity of reaction
                command, member = result[0].emoji, result[1]
                logger.debug("Player interaction: %s: %s", member, command)
                is_dj = data.has_custom_role(self.bot, __name__, 'dj', member=member)
                if not await utilities.can_interact(self.bot, member, channel_id=self.channel.id):
                    continue
                asyncio.ensure_future(self.message.remove_reaction(command, member))
                if not is_dj and (member not in self.voice_channel.members or
                        self.state == States.LOADING or
                        command not in valid_commands):
                    continue

                # Check player control type
                restricted_commands = [
                    set(),  # Public
                    (valid_commands[0],) + valid_commands[3:5],  # Partially public
                    valid_commands[:10]  # DJ Only
                ][self.control]
                if command in restricted_commands and not is_dj:
                    logger.debug("Ignoring command (insufficient permissions)")
                    continue

                # Play/pause and skip
                if command in valid_commands[:3]:
                    logger.debug("Play|pause and skip selected")

                    # Play/pause
                    if command == valid_commands[1]:
                        permissions = self.control == Control.ALL or is_dj
                        if self.state == States.PLAYING and permissions:
                            asyncio.ensure_future(self.pause(author=member))
                        elif self.state == States.PAUSED:
                            if permissions or self.autopaused or self.first_time_startup:
                                asyncio.ensure_future(self.play(author=member))

                    # Skip
                    elif self.state != States.LOADING:
                        use_skip = command == valid_commands[2]
                        asyncio.ensure_future(self.track_navigate(use_skip, member))

                # Stop player
                elif command == valid_commands[3]:
                    await self.stop(
                        text="The player has been stopped by {}.".format(member.mention))
                    return

                # Shuffle mode
                elif command == valid_commands[4]:
                    if self.mode == Modes.PLAYLIST:
                        self.shuffle = not self.shuffle
                        data.add(
                            self.bot, __name__, 'shuffle', self.shuffle, guild_id=self.guild.id)
                    asyncio.ensure_future(self.update_interface())

                # Generate tracklist
                elif command == valid_commands[5]:
                    logger.debug("Tracklist selected")
                    if self.tracklist:
                        if self.tracklist_time != self.tracklist_update_time:
                            self.tracklist_time = self.tracklist_update_time
                            tracklist_string = await _build_tracklist(
                                self.bot, self.guild, self.tracklist)
                            tracklist_file = utilities.get_text_as_file(tracklist_string)
                            url = await utilities.upload_to_discord(
                                self.bot, tracklist_file, filename='tracklist.txt')
                            self.tracklist_url = url

                        text = '[Click here]({}) to download the tracklist'.format(
                            self.tracklist_url)
                        asyncio.ensure_future(self.update_interface(notification_text=text))

                # Track list navigation
                elif command in valid_commands[6:9]:
                    logger.debug("Track list navigation selected")
                    if command == valid_commands[7]:  # Reset to the current page
                        self.page = int(self.track_index / 5)
                    else:
                        self.page += -1 if command == valid_commands[6] else 1
                    asyncio.ensure_future(self.update_interface(ignore_ratelimit=True))

                # Voteskip
                elif command == valid_commands[9]:
                    logger.debug("Vote skip selected")
                    if self.state != States.PLAYING or member.bot:
                        continue
                    elif member.id in self.skip_voters:
                        self.skip_voters.remove(member.id)
                        logger.debug("Vote by %s was removed.", member)
                    elif member in self.voice_channel.members:
                        self.skip_voters.append(member.id)
                        logger.debug("Vote by %s was added.", member)
                    else:
                        continue
                    self.update_listeners()

                # Help
                elif command == valid_commands[10]:
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
                        '**DJs only:** Only DJs can manage the player.\n'
                        '**Partially public:** Everybody can '
                        'add tracks, change track pages, and voteskip. '
                        'You can skip your own tracks as well.\n'
                        '**Public:** Everybody has full control '
                        '(except removing other people\'s '
                        'tracks and importing tracklists).'
                    )
                    status_help = (
                        ':arrow_forward: (Blue): Playing a track\n'
                        ':pause_button: (White): Paused\n'
                        ':arrows_counterclockwise: (Orange): Loading'
                    )
                    command_help = (
                        'To add tracks:\n`{0}`\u200b{1[3].help_string}\n'
                        'To remove tracks:\n`{0}`\u200b{1[4].help_string}\n'
                        'To add tracks and/or skip to a track:\n'
                        '`{0}`\u200b{1[11].help_string}\n\n'
                        'Examples (using the shortcut):\n'
                        '`{0}add Erasure Always`\n'
                        '`{0}remove 1`\n'
                        '`{0}play Toto Africa`\n'
                        '`{0}play track 7`\n'
                        'For more, type: `help playlist`'
                    ).format(
                        utilities.get_invoker(self.bot, guild=self.guild),
                        self.bot.commands['playlist'].subcommands)
                    help_embed = discord.Embed(title=':question: Music player help')
                    help_embed.add_field(name='Basic usage:', value=command_help)
                    help_embed.add_field(name='Buttons:', value=button_help)
                    help_embed.add_field(name='Control types:', value=permissions_help)
                    help_embed.add_field(name='Status icons:', value=status_help)
                    asyncio.ensure_future(member.send(embed=help_embed))

        except Exception as e:
            self.bot.extra = e
            logger.warn("Something bad happened (%s). %s", type(e), e)


# Link builders
def _build_hyperlink(bot, track):
    full_title = track.title.replace('`', '').replace('*', '')
    title = _truncate_title(full_title)
    return '[{0}]({1} "{2} (added by <@{3}>)")'.format(title, track.url, full_title, track.userid)


def _build_shortlink(bot, track):
    """Like _build_hyperlink, but for the URL portion only."""
    display_url = 'http://dis.gd' if len(track.url) > URL_LIMIT else track.url
    display_title = _truncate_title(track.title.replace('`', ''))
    return '({} "{} (added by <@{}>)")'.format(display_url, display_title, track.userid)


def _build_track_details(bot, track, index):
    """Creates a string that shows a one liner of the track"""
    full_title = track.title.replace('`', '').replace('*', '')
    title = _truncate_title(full_title)
    return '[[Track {}]({} "{} (added by <@{}>)")] ({}) *{}*'.format(
        index + 1, track.url, full_title, track.userid,
        utilities.get_time_string(track.duration), title)


def _truncate_title(text, limit=TITLE_LIMIT):
    """Truncates the text to the given limit if it is too long."""
    return (text[:limit] + '…') if len(text) > limit else text


def _get_tracklist(bot, guild):
    cursor = data.db_select(
        bot, from_arg='playlist', additional='ORDER BY id ASC', table_suffix=guild.id)
    return cursor.fetchall() if cursor else ()


def _get_music_player(bot, guild):
    return data.get(bot, __name__, 'music_player', guild_id=guild.id, volatile=True)


async def _check_active_player(bot, guild, autodelete_time=5):
    """Tries to get the active music player and whether or not the interface is active."""
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


def _check_total_tracks_limits(bot, author):
    """Ensures that limits of the track list are respected. Returns tracklist."""

    # Limits
    user_track_limit = data.get(
        bot, __name__, key='user_track_limit', guild_id=author.guild.id,
        default=configurations.get(bot, __name__, key='max_user_track_limit'))
    total_track_limit = data.get(
        bot, __name__, key='total_track_limit', guild_id=author.guild.id,
        default=configurations.get(bot, __name__, key='max_total_track_limit'))

    # Checks
    tracklist = _get_tracklist(bot, author.guild)
    if data.has_custom_role(bot, __name__, 'dj', member=author):  # DJs ignore limits
        return tracklist
    if total_track_limit and len(tracklist) >= total_track_limit:
        raise CBException("The track limit of {} has been reached.".format(total_track_limit))
    user_tracks = [it for it in tracklist if it.userid == author.id]
    if user_track_limit and len(user_tracks) >= user_track_limit:
        raise CBException(
            "You cannot add any more songs right now (limit {}).".format(user_track_limit))
    return tracklist


async def _add_track_with_url(bot, guild, check_url, user_id=0, timestamp=0):
    """Checks the given url and adds it to the database."""
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

    # Get information about the track
    try:
        info = await utilities.future(downloader.extract_info, check_url, download=False)
        if not is_url:  # Select first result on search
            info = info['entries'][0]
            check_url = info['webpage_url']
    except BotException as e:
        raise e  # Pass up
    except Exception as e:
        raise CBException("Failed to fetch information from the URL.", e=e)
    return await _add_track_to_db(
        bot, guild, check_url, info, user_id=user_id, timestamp=timestamp)


async def _add_track_to_db(bot, guild, check_url, info, user_id=0, timestamp=0):
    """Adds the given track info to the database."""
    hard_threshold = configurations.get(bot, __name__, key='hard_threshold')
    bot.extra = info
    try:
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

    # Prepare data for insertion
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

    return data.db_insert(
        bot, 'playlist', table_suffix=guild.id, input_args=entry_data,
        create='playlist_template')


async def add_track(bot, context):
    """Adds a track to the playlist (via command)."""
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    # Check channel restriction
    channel_id = data.get(bot, __name__, 'channel', guild_id=context.guild.id)
    if not channel_id:
        raise CBException("No channel configured for the music player.")
    channel_restriction = data.get_channel(bot, channel_id)
    is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
    if context.channel.id != channel_id and not is_dj:
        raise CBException("You can only add tracks in {}".format(channel_restriction.mention))

    # Check control restriction
    control = data.get(
        bot, __name__, 'control', guild_id=context.guild.id, default=Control.PARTIAL)
    if not is_dj and control == Control.DJS:
        raise CBException("You must be a DJ to add tracks.", autodelete=autodelete)

    default_threshold = configurations.get(bot, __name__, key='max_threshold')
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    guild_id = context.guild.id
    threshold = data.get(bot, __name__, 'threshold', guild_id=guild_id, default=default_threshold)
    cutoff = data.get(bot, __name__, 'cutoff', guild_id=guild_id, default=default_cutoff)

    # Add track to the playlist
    check_url = context.arguments[0]
    try:
        tracklist = _check_total_tracks_limits(bot, context.author)
        cursor = await _add_track_with_url(
            bot, context.guild, check_url, user_id=context.author.id)
        track = cursor.fetchone()
    except BotException as e:
        e.autodelete = autodelete
        raise e

    response = '{} added {}'.format(
        context.author.mention, _build_track_details(bot, track, len(tracklist)))
    if track.duration > threshold:
        response += (
            "\nTrack is longer than the threshold length ({} seconds), so "
            "only the first {} seconds will be played".format(threshold, cutoff))

    # Check the music player again, as it may have stopped while we were download the url
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    if use_player_interface:
        music_player.update_tracklist()
        await music_player.update_interface(notification_text=response)

    return Response(
        embed=discord.Embed(description=response),
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
    track = tracklist[index]
    if control == Control.DJS and not is_dj:
        raise CBException("You must be a DJ to remove entries.", autodelete=autodelete)
    elif track.userid != context.author.id and not is_dj:
        raise CBException(
            "You must be the user who added the entry, or a DJ.", autodelete=autodelete)

    data.db_delete(
        bot, 'playlist', table_suffix=context.guild.id,
        where_arg='id=%s', input_args=[track.id])
    response = '{} removed {}'.format(
        context.author.mention, _build_track_details(bot, track, index))

    # Change current index if necessary
    if use_player_interface:
        music_player.update_tracklist()
        if music_player.mode == Modes.PLAYLIST:
            use_skip = index == music_player.track_index
            if index <= music_player.track_index:  # Shift track index down
                music_player.track_index -= 1
            if use_skip:  # Skip track due to removing the current track
                music_player._skip_track()
        await music_player.update_interface(notification_text=response)

    return Response(
        embed=discord.Embed(description=response),
        message_type=MessageTypes.REPLACE if use_player_interface else MessageTypes.NORMAL,
        delete_after=autodelete if use_player_interface else None,
        extra=autodelete if use_player_interface else None)


async def _build_tracklist(bot, guild, tracklist):
    header = (
        '# Tracklist generated: {3[1]} {3[0]}\r\n'
        '# Guild: {0}\r\n'
        '# Total tracks: {1}\r\n'
        '# Runtime: {2}\r\n'
    ).format(
        guild.name, len(tracklist),
        utilities.get_time_string(sum(it.duration for it in tracklist), text=True, full=True),
        utilities.get_timezone_offset(
            bot, guild_id=guild.id, utc_dt=datetime.utcnow(), as_string=True))
    tracklist_text_list = [header]
    template = (
        '{}: |\r\n'
        '  {}\r\n'  # Title
        '  {}\r\n'  # URL
        '  Added by {} at {} {}\r\n'  # Info
        '  Duration: {} ID|Timestamp: {}|{}\r\n'  # Duration, internal info
    )
    all_guild_members = await guild.fetch_members(limit=None).flatten()
    for index, track in enumerate(tracklist):
        track_author = (
            (await data.fetch_member(bot, track.userid, safe=True, search=all_guild_members)) or
            'Unknown')
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

    tracklist_string = await _build_tracklist(bot, context.guild, tracklist)
    tracklist_file = utilities.get_text_as_file(tracklist_string)

    if use_player_interface:
        url = await utilities.upload_to_discord(bot, tracklist_file, filename='tracklist.txt')
        await music_player.update_interface(
            notification_text='[Click here]({}) to download the current tracklist'.format(url))
        return Response(content='Tracklist file updated.', delete_after=5)
    else:
        return Response(
            content='Tracks:', file=discord.File(tracklist_file, filename='tracklist.txt'))


async def import_tracklist(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    use_youtube_playlist = 'youtube' in context.options
    if not (bool(context.message.attachments) ^ use_youtube_playlist):
        raise CBException(
            "Must include an attachment or a YouTube playlist URL.", autodelete=autodelete)
    if not data.has_custom_role(bot, __name__, 'dj', member=context.author):
        raise CBException("You must be a DJ to import tracks.")
    if use_player_interface:
        raise CBException(
            'The player must be stopped before importing tracks.', autodelete=autodelete)

    data.add(bot, __name__, 'import_lock', True, guild_id=context.guild.id, volatile=True)
    try:

        # Get tracklist data from playlist URL
        if use_youtube_playlist:
            downloader = YoutubeDL()
            info = await utilities.future(
                downloader.extract_info, context.options['youtube'], download=False)
            # tracklist_data = list(it['webpage_url'] for it in info['entries'])
            tracklist_data = info['entries']

        # Get tracklist data from file
        else:
            use_youtube_playlist = False
            file_url = context.message.attachments[0].url
            tracklist_file = await utilities.download_url(bot, file_url, use_fp=True)

            tracklist_data = yaml.safe_load(tracklist_file)
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
        extra=(tracklist_data, use_youtube_playlist),
        extra_function=_import_tracklist_status)


async def _import_tracklist_status(bot, context, response):
    last_update_time = time.time()
    total_imported = 0
    tracklist_data, use_youtube_playlist = response.extra

    async def _update_notification(last_update_time):
        if time.time() - last_update_time > 5:
            await response.message.edit(content="Importing tracks... [ {} / {} ]".format(
                total_imported, len(tracklist_data)))
            return time.time()
        return last_update_time

    try:

        if use_youtube_playlist:
            for info in tracklist_data:
                await _add_track_to_db(
                    bot, context.guild, info['webpage_url'], info,
                    context.author.id, int(time.time()))
                total_imported += 1
                last_update_time = await _update_notification(last_update_time)

        else:
            if isinstance(tracklist_data, list):
                tracklist_data = OrderedDict((it[0], it[1]) for it in enumerate(tracklist_data))
            for _, track_blob in sorted(tracklist_data.items()):
                cleaned = track_blob.strip()
                if not cleaned:
                    continue
                elif '\n' in cleaned:
                    title, url, _, info, _ = track_blob.split('\n')
                    user_id, _, timestamp = info.split()[3].partition('|')
                else:
                    title = url = track_blob
                    user_id, timestamp = context.author.id, time.time()

                _check_total_tracks_limits(bot, context.author)
                await _add_track_with_url(bot, context.guild, url, int(user_id), int(timestamp))
                total_imported += 1
                last_update_time = await _update_notification(last_update_time)

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
    title = _truncate_title(track_info.title)

    time_ago = time.time() - track_info.timestamp
    added_by_text = "Added by <@{}> {} ago.".format(
        track_info.userid, utilities.get_time_string(time_ago, text=True))
    duration_text = "Duration: ({})".format(utilities.get_time_string(track_info.duration))
    response = "Info for track {}:".format(index + 1)

    if use_player_interface:  # Add notification
        track_link = _build_hyperlink(bot, track_info)
        info_text = "{}\n{}\n{}\n{}".format(response, track_link, duration_text, added_by_text)
        music_player.page = int(index / 5)
        await music_player.update_interface(notification_text=info_text, ignore_ratelimit=True)
        return Response(message_type=MessageTypes.REPLACE, extra=autodelete)
    else:
        response += "\n{}\n{}\n{}\n{}".format(title, track_info.url, duration_text, added_by_text)
        return Response(content=response)


async def set_volume(bot, context):
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    # Check control restriction
    is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
    control = data.get(
        bot, __name__, 'control', guild_id=context.guild.id, default=Control.PARTIAL)
    if not is_dj and control != Control.ALL:
        raise CBException("You must be a DJ to change the volume.", autodelete=autodelete)

    volume = context.arguments[0]
    data.add(bot, __name__, 'volume', volume, guild_id=context.guild.id)
    if use_player_interface:
        music_player.update_config()
        await music_player.update_interface(
            notification_text='<@{}> set the volume to {:.2f}%'.format(
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

    guild_id = context.guild.id
    changes = []
    is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
    is_mod = context.elevation > 0

    if 'threshold' in options:
        if not is_dj:
            raise CBException("You must be a DJ in order to change the length threshold.")
        threshold = options['threshold']
        data.add(bot, __name__, 'threshold', threshold, guild_id=guild_id)
        changes.append('Duration threshold set to {} seconds.'.format(threshold))

    if 'cutoff' in options:
        if not is_dj:
            raise CBException("You must be a DJ in order to change the length cutoff.")
        cutoff = options['cutoff']
        data.add(bot, __name__, 'cutoff', cutoff, guild_id=guild_id)
        changes.append('Cutoff set to {} seconds.'.format(cutoff))

    if 'usertracks' in options:
        if not is_dj:
            raise CBException("You must be a DJ in order to change the user track limit.")
        limit = options['usertracks']
        data.add(bot, __name__, 'user_track_limit', limit, guild_id=guild_id)
        changes.append('User track limit set to {} track(s).'.format(limit))

    if 'totaltracks' in options:
        if not is_dj:
            raise CBException("You must be a DJ in order to change the total track limit.")
        limit = options['totaltracks']
        data.add(bot, __name__, 'total_track_limit', limit, guild_id=guild_id)
        changes.append('Total track limit set to {} track(s).'.format(limit))

    if 'djrole' in options:
        if not is_mod:
            raise CBException("You must be a bot moderator in order to change the DJ role.")
        dj_role = options['djrole']
        data.add_custom_role(bot, __name__, 'dj', dj_role)
        changes.append('Set the DJ role to {}.'.format(dj_role.mention))

    if 'channel' in options:
        if not is_mod:
            raise CBException("You must be a bot moderator in order to change the player channel.")
        text_channel = options['channel']
        data.add(bot, __name__, 'channel', text_channel.id, guild_id=guild_id)
        changes.append('Set the text channel restriction to {}.'.format(text_channel.mention))

    if 'switchcontrol' in options:
        if not is_mod:
            raise CBException("You must be a bot moderator in order to cycle control modes.")
        control = data.get(bot, __name__, 'control', guild_id=guild_id, default=Control.PARTIAL)
        control = 0 if control == len(Control) - 1 else control + 1
        data.add(bot, __name__, 'control', control, guild_id=guild_id)
        changes.append('Cycled the playlist permissions control mode to: {}'.format(
            ('Public', 'Partially public', 'DJs only')[control]))

    if 'switchmode' in options:
        if not is_mod:
            raise CBException("You must be a bot moderator in order to cycle player modes.")
        mode = data.get(bot, __name__, 'mode', guild_id=guild_id, default=Modes.QUEUE)
        mode = 0 if mode == len(Modes) - 1 else mode + 1
        data.add(bot, __name__, 'mode', mode, guild_id=guild_id)
        changes.append('Cycled the playlist mode to: {}'.format(('Playlist', 'Queue')[mode]))

    if 'mirrorchat' in options:
        if not is_mod:
            raise CBException("You must be a bot moderator in order to toggle chat mirroring.")
        mirror = not data.get(bot, __name__, 'mirror_chat', guild_id=guild_id, default=False)
        data.add(bot, __name__, 'mirror_chat', mirror, guild_id=guild_id)
        changes.append('{}abled chat mirroring.'.format('En' if mirror else 'Dis'))

    # Defaults
    default_threshold = configurations.get(bot, __name__, key='max_threshold')
    default_cutoff = configurations.get(bot, __name__, key='max_cutoff')
    default_total_track_limit = configurations.get(bot, __name__, key='max_total_track_limit')
    default_user_track_limit = configurations.get(bot, __name__, key='max_user_track_limit')

    # Format and display all settings
    threshold = data.get(bot, __name__, 'threshold', guild_id=guild_id, default=default_threshold)
    cutoff = data.get(bot, __name__, 'cutoff', guild_id=guild_id, default=default_cutoff)
    total_track_limit = data.get(
        bot, __name__, key='total_track_limit',
        guild_id=guild_id, default=default_total_track_limit)
    user_track_limit = data.get(
        bot, __name__, key='user_track_limit',
        guild_id=guild_id, default=default_user_track_limit)
    dj_role = data.get_custom_role(bot, __name__, 'dj', context.guild)
    control = data.get(bot, __name__, 'control', guild_id=guild_id, default=Control.PARTIAL)
    mode = data.get(bot, __name__, 'mode', guild_id=guild_id, default=Modes.QUEUE)
    chat_mirroring = data.get(bot, __name__, 'mirror_chat', guild_id=guild_id, default=False)
    text_channel_id = data.get(bot, __name__, 'channel', guild_id=guild_id)
    text_channel = context.guild.get_channel(text_channel_id)

    embed = discord.Embed(
        title='Player configuration', description=(
            'Text channel: {}\nTotal track limit: {}\n'
            'User track limit: {}\nThreshold: {}\nCutoff: {}\n'
            'DJ Role: {}\nControl: {}\nPlayer mode: {}\nChat mirroring: {}'.format(
                text_channel.mention if text_channel else 'None',
                '{} tracks'.format(total_track_limit),
                '{} tracks'.format(user_track_limit),
                '{} seconds'.format(threshold),
                '{} seconds'.format(cutoff),
                dj_role.mention if dj_role else 'None',
                ('Public', 'Partially public', 'DJs only')[control],
                ('Repeating playlist', 'Single play queue')[mode],
                chat_mirroring)
        )
    )

    if changes:
        embed.add_field(name="Changes", value='\n'.join(changes))
        if use_player_interface:
            music_player.update_config()
            await music_player.update_interface('{}:\n{}'.format(
                context.author.mention, '\n'.join(changes)))

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
    """Menu for confirming a playlist clear."""
    if result is None:  # Timed out
        edit = 'Playlist clear timed out.'

    elif result.content.lower() == 'yes':
        # music_player = _get_music_player(bot, context.guild)
        _, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
        if use_player_interface:
            raise CBException(
                "Cannot clear playlist tracks when the player is active.", autodelete=autodelete)
        data.db_drop_table(bot, 'playlist', table_suffix=context.guild.id, safe=True)
        edit = 'Playlist has been cleared.'

    else:
        edit = 'Playlist clear cancelled.'

    await response.message.edit(content=edit)


async def skip_to_page(bot, context):
    """Skips to a certain page of the tracklist in the player interface."""
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    if not use_player_interface:
        raise CBException("The player interface must be active.")

    # Check page number
    tracklist = music_player.tracklist
    page_number = context.arguments[0] - 1
    total_pages = max(int((len(tracklist) + 4) / 5), 1)
    if not 0 <= page_number <= total_pages - 1:
        raise CBException(
            "Invalid page number. Must be between 1 and {} inclusive.".format(total_pages),
            autodelete=autodelete)

    music_player.page = page_number
    await music_player.update_interface(ignore_ratelimit=True)
    return Response(message_type=MessageTypes.REPLACE, extra=1)


async def swap_tracks(bot, context):
    """Swaps the given two tracks in the playlist."""
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    # Check control restriction
    control = data.get(
        bot, __name__, 'control', guild_id=context.guild.id, default=Control.PARTIAL)
    is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
    if not is_dj and control != Control.ALL:
        raise CBException("You must be a DJ to swap tracks.", autodelete=autodelete)
    
    # Check index validity
    tracklist = _get_tracklist(bot, context.guild)
    swap = []
    for index in context.arguments:
        if not 1 <= index <= len(tracklist):
            raise CBException(
                "Index must be between 1 and {}".format(len(tracklist)),
                autodelete=autodelete)
        swap.append(tracklist[index - 1])

    # Swap tracks
    set_arg = (
        '(url, downloadurl, title, duration, userid, timestamp, extra) = '
        '(%s, %s, %s, %s, %s, %s, %s)')
    for index, track in enumerate(swap):
        data.db_update(
            bot, 'playlist', table_suffix=context.guild.id,
            set_arg=set_arg, where_arg='id=%s', input_args=[
                track.url, track.downloadurl, track.title, track.duration, track.userid,
                track.timestamp, Json(track.extra), swap[index - 1].id])

    # Add notification and skip track if necessary
    response = '{} swapped tracks {} and {}'.format(context.author.mention, *context.arguments)
    if use_player_interface:
        music_player.update_tracklist()
        if music_player.track_index + 1 in context.arguments:
            asyncio.ensure_future(music_player.play(track_index=music_player.track_index))
        await music_player.update_interface(notification_text=response, ignore_ratelimit=True)
        return Response(message_type=MessageTypes.REPLACE, extra=autodelete)
    else:
        return Response(content=response)


async def _check_player_restrictions(
        bot, context, music_player, use_player_interface, autodelete):
    """Ensures that the user in the context can interact with the player."""

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
            "The music player must be used in the assigned text channel, {}.".format(
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


async def control_player(bot, context):
    """Basic control of the player (like pausing/stopping/skipping etc."""
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)

    if len(context.options) != 2:
        raise CBException("Only one action must be provided.", autodelete=autodelete)
    if not use_player_interface:
        raise CBException("The music player is not active.")
    await _check_player_restrictions(bot, context, music_player, use_player_interface, autodelete)
    is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
    permissions = music_player.control == Control.ALL or is_dj

    try:
        action = "[Unknown]"
        if 'next' in context.options or 'skip' in context.options:
            action = 'skip the current track'
            assert permissions or music_player.control == Control.PARTIAL
            result = await music_player.track_navigate(True, context.author)
            if not result:  # Add to vote skip list instead
                if (music_player.state == States.PLAYING and
                        context.author.id not in music_player.skip_voters):
                    action += '. Voting to skip instead'
                    music_player.skip_voters.append(context.author.id)
                    music_player.update_listeners()
                assert False
        elif 'resume' in context.options:
            action = 'resume the player'
            assert permissions or music_player.autopaused or music_player.first_time_startup
            asyncio.ensure_future(music_player.play(author=context.author))
        else:
            if 'pause' in context.options:
                action = 'pause the player'
                assert permissions
                asyncio.ensure_future(music_player.pause(author=context.author))
            elif 'stop' in context.options:
                action = 'stop the player'
                assert permissions
                asyncio.ensure_future(music_player.stop(
                    text="The player has been stopped by {}.".format(context.author.mention)))
            elif 'previous' in context.options:
                action = 'skip to the previous track'
                assert permissions
                asyncio.ensure_future(music_player.track_navigate(False, context.author))
    except AssertionError:
        raise CBException(
            "You have insufficient permissions to {}.".format(action),
            autodelete=autodelete)

    # Delete message
    return Response(message_type=MessageTypes.REPLACE, extra=1)


async def setup_player(bot, context):
    """Starts the player interface and starts playing a track if selected."""
    music_player, use_player_interface, autodelete = await _check_active_player(bot, context.guild)
    await _check_player_restrictions(bot, context, music_player, use_player_interface, autodelete)

    use_play_command = context.subcommand.id == 'play'
    if use_play_command and (context.arguments[0] and 'track' in context.options):
        raise CBException(
            "Cannot supply the track and query paramters at the same time.",
            autodelete=autodelete)

    # Check given track index if given
    # Get mode from persistent data because the player may not exist yet
    track_index = None
    track = None
    adding_track = False
    if use_play_command:
        if 'track' in context.options:  # Play track index
            track_index = context.options['track']
            tracklist = _get_tracklist(bot, context.guild)
            if not 0 < track_index <= len(tracklist):
                raise CBException(
                    "Track index must be between 1 and {} inclusive.".format(len(tracklist)),
                    autodelete=autodelete)
            track_index -= 1
            track = tracklist[track_index]
        elif context.arguments[0]:  # Query given (add track)
            adding_track = True
            add_track_response = await add_track(bot, context)
            # add_track_response.message_type = MessageTypes.PERMANENT
            await bot.handle_response(context.message, add_track_response, context=context)

    # Check autoplay permissions
    use_autoplay = False
    if use_play_command:
        is_dj = data.has_custom_role(bot, __name__, 'dj', member=context.author)
        control_type = data.get(
            bot, __name__, 'control', guild_id=context.guild.id, default=Control.PARTIAL)
        use_autoplay = (
            control_type == Control.ALL or is_dj or
            (control_type == Control.PARTIAL and
                (not music_player or music_player.first_time_startup)))

    # Setup new player
    if music_player is None or music_player.state == States.STOPPED:
        logger.debug("Creating new music player.")
        music_player = MusicPlayer(
            bot, context.message, autoplay=use_autoplay, track_index=track_index)
        data.add(
            bot, __name__, 'music_player', music_player, guild_id=context.guild.id, volatile=True)

    # Update player message or change tracks
    else:
        if use_autoplay and track_index is not None:
            music_player.notification = '{} skipped to {}'.format(
                context.author.mention, _build_track_details(bot, track, track_index))

        play_track = bool(
            use_autoplay and (music_player.state == States.PAUSED or track_index is not None))

        # Check if messages can just be replaced
        message_history = await context.channel.history(limit=3).flatten()
        message_ids = list(it.id for it in message_history)
        if (len(message_history) > 2 and music_player.message.id in message_ids and
                not context.subcommand.id == 'show'):
            if play_track:
                asyncio.ensure_future(music_player.play(
                    track_index=track_index, author=context.author))

        else:
            await music_player.set_new_message(
                context.message, autoplay=use_autoplay if play_track else None,
                track_index=track_index)

        # Delete any immediate play/skip commands, but keep track add messages.
        if not adding_track:
            return Response(message_type=MessageTypes.REPLACE)
