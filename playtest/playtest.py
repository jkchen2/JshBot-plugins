import discord
from youtube_dl import YoutubeDL

from jshbot import utilities, configurations, plugins, data, logger
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.1.0'
CBException = ConfiguredBotException('Play test')
uses_configuration = False


@plugins.command_spawner
def get_commands(bot):
    return [Command('playtest', subcommands=[SubCommand(Arg('url'))])]


async def get_response(bot, context):
    response = Response()
    if context.author.voice:
        voice_channel = context.author.voice.channel
        voice_client = await utilities.join_and_ready(bot, voice_channel)

        options = {'format': 'bestaudio/best', 'noplaylist': True}
        downloader = YoutubeDL(options)
        url = context.arguments[0]
        try:
            file_location = data.get_from_cache(bot, None, url=url)
            if not file_location:
                logger.info("Not found in cache. Downloading...")
                info = await utilities.future(downloader.extract_info, url, download=False)
                download_url = info['formats'][0]['url']
                file_location = await data.add_to_cache(bot, download_url, name=url)
            ffmpeg_options = '-protocol_whitelist "file,http,https,tcp,tls"'
            voice_client.play(discord.FFmpegPCMAudio(file_location, before_options=ffmpeg_options))
        except BotException as e:
            raise e  # Pass up
        except Exception as e:
            raise CBException("Something bad happened.", e=e)
        response.content = "Playing your stuff."
    else:
        raise CBException("You're not in a voice channel.")

    return response
