import googletrans
import discord

from jshbot import utilities, plugins, configurations, data, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import Command, SubCommand, Shortcut, ArgTypes, Arg, Opt, Response

__version__ = '0.1.0'
CBException = ConfiguredBotException('Translator')
uses_configuration = False
TRANSLATOR = googletrans.Translator()
LANGUAGE_LINK = (
    '[Click here for a list of supported languages.]'
    '(https://en.wikipedia.org/wiki/Google_Translate#Supported_languages)')


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'translate', subcommands=[
            SubCommand(
                Opt('default'),
                Arg('language', argtype=ArgTypes.MERGED_OPTIONAL),
                doc='Sets the default translation language. (Default is `en`)',
                elevated_level=1, function=translate_default),
            SubCommand(
                Opt('languages'),
                doc='Gets a list of valid language codes.',
                function=translate_languages),
            SubCommand(
                Opt('from', attached='language', optional=True),
                Opt('to', attached='language', optional=True),
                Arg('text', argtype=ArgTypes.MERGED),
                doc='Translates text.',
                function=translate)],
        shortcuts=[Shortcut('ft', 'from igbo {text}', Arg('text', argtype=ArgTypes.MERGED))],
        description='Translates text.', other=LANGUAGE_LINK, category='tools')]


async def translate_default(bot, context):
    """Sets the default translation language."""
    language = context.arguments[0]
    if language:
        data.add(bot, __name__, 'default', language, guild_id=context.guild.id)
    else:
        data.remove(bot, __name__, 'default', guild_id=context.guild.id)
    return Response(
        content='Default language set to {}.'.format(language if language else 'English'))


async def translate_languages(bot, context):
    """Gets a list of valid language codes."""
    codes = ['{} [`{}`]'.format(v.title(), k) for k, v in googletrans.constants.LANGUAGES.items()]
    code_break = int(len(codes)/3) + 1
    code_split = [codes[it:it + code_break] for it in range(0, len(codes), code_break)]
    embed = discord.Embed(title='Language list')
    for split in code_split:
        embed.add_field(name='\u200b', value='\n'.join(split))
    return Response(embed=embed)


async def translate(bot, context):
    """Translates the given text."""
    source = context.options.get('from', 'auto')
    if 'to' in context.options:
        destination = context.options['to']
    else:
        if context.direct:
            destination = 'en'
        else:
            destination = data.get(
                bot, __name__, 'default', guild_id=context.guild.id, default='en')

    try:
        result = await utilities.future(
            TRANSLATOR.translate, context.arguments[0], src=source, dest=destination)
    except ValueError as e:
        if 'source' in e.args[0]:
            issue, language = 'source', source
        else:
            issue, language = 'destination', destination
        raise CBException("Invalid {} language (`{}`).\n{}".format(issue, language, LANGUAGE_LINK))
    except Exception as e:
        raise CBException("Failed to translate the text.", e)

    full_source = googletrans.constants.LANGUAGES[result.src].title()
    full_destination = googletrans.constants.LANGUAGES[result.dest].title()
    embed = discord.Embed(
        title=':arrows_counterclockwise: Google Translate', color=discord.Color(0x3b88c3))
    embed.add_field(name=full_source, value=context.arguments[0], inline=False)
    embed.add_field(name=full_destination, value=result.text, inline=False)
    return Response(embed=embed)
