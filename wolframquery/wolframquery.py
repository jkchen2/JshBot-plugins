import urllib
import random
import io
import discord

import wap

from PIL import Image, ImageDraw, ImageFont
from xml.etree import ElementTree

from jshbot import utilities, data, configurations, plugins, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
CBException = ConfiguredBotException('Wolfram|Alpha API plugin')
uses_configuration = True


@plugins.command_spawner
def get_commands(bot):
    new_commands = []

    new_commands.append(Command(
        'wolfram', subcommands=[
            SubCommand(
                Opt('pro'),
                doc='Learn about Wolfram|Alpha Pro and what it can help you accomplish.',
                function=get_pro_info),
            SubCommand(
                Opt('ip'), Arg('IP address', quotes_recommended=False),
                doc='Sets the default IP address used for queries.',
                allow_direct=False, function=set_ip_address),
            SubCommand(
                Opt('units'), doc='Toggles between metric and US standard units.',
                allow_direct=False, function=set_units),
            SubCommand(
                Opt('text', optional=True, doc='Only get textual results.'),
                Opt('results', attached='number of results', optional=True, default=3,
                    convert=int, check=lambda b, m, v, *a: 1 <= v <= 8,
                    check_error='Must be between 1 and 8 inclusive.',
                    quotes_recommended=False, doc='The number of results to display.'),
                Arg('query', argtype=ArgTypes.MERGED),
                doc='Uses Wolfram|Alpha to parse the given query.', function=run_query)],
            shortcuts=[
                Shortcut('wa', '{arguments}', Arg('arguments', argtype=ArgTypes.MERGED)),
                Shortcut('swa', 'text results 1 {query}', Arg('query', argtype=ArgTypes.MERGED))],
            description='Wolfram|Alpha integration.',
            other='API calls are limited. Please use responsibly!', category='tools'))

    return new_commands


async def get_pro_info(bot, context):
    embed = discord.Embed(
        title='Wolfram|Alpha Pro', url='https://www.wolframalpha.com/pro/',
        colour=discord.Colour(0xfa8114), description=(
            "Wolfram|Alpha Pro lets you do more with Wolfram|Alpha. Whether "
            "you are a business, professional, or student, Wolfram|Alpha Pro "
            "can cater to your needs."))
    embed.add_field(name="For businesses:", value=(
        "Wolfram|Alpha Pro allows you to analyze your own custom data by uploading "
        "images and files. With access to the powerful Wolfram|Alpha "
        "engine, your data can be analyzed quickly and easily. "
        "Furthermore, Wolfram|Alpha Pro enables you to build charts, "
        "graphs, and other beautifully formatted visualizations with your "
        "uploaded data. [See which files you can upload and analyze.]"
        "(https://www.wolframalpha.com/input/pro/uploadexamples/)"))
    embed.add_field(name="For educators:", value=(
        "Get accurate and up-to-date data on nearly "
        "anything, ranging from population histories to chemical "
        "compounds, and everything in between. Build engaging visuals, "
        "such as detailed charts, perfect 3D graphs, and more. Use the "
        "Wolfram Problem Generator to create unique practice problems for "
        "every student, covering a wide range of math subjects, including "
        "number theory and statistics. [Try the Wolfram Problem Generator.]"
        "(https://www.wolframalpha.com/problem-generator/)"))
    embed.add_field(name="For students:", value=(
        "Receive help on those difficult math "
        "problems using Wolfram|Alpha Pro with step-by-step solutions to "
        "many topics, including calculus and differential equations. "
        "Customize your Wolfram|Alpha experience using Wolfram|Alpha "
        "powered web apps to make recurring calculations a breeze. "
        "Wolfram|Alpha Pro also makes it easier than ever to input math "
        "into a query with the included extended keyboard for math and "
        "physics. [View a demo of the solver.](https://"
        "www.wolframalpha.com/pro/step-by-step-math-solver.html)"))
    embed.add_field(name='\u200b', value=(
        "No matter what type of organization or individual you are, "
        "Wolfram|Alpha has what it takes to increase your productivity "
        "and help you make the most out of your time. Designed for "
        "ease-of-use, but also great computational power, Wolfram|Alpha "
        "Pro gives you the prime Wolfram|Alpha experience you need.\n\n"
        "For more details, visit https://www.wolframalpha.com/pro/"))
    return Response(embed=embed)


async def set_ip_address(bot, context):
    if context.arguments[0]:
        data.add(bot, __name__, 'server_ip', context.arguments[0], guild_id=context.guild.id)
        response = "IP address set!"
    else:  # Get current IP
        default_ip = configurations.get(bot, __name__, key='default_ip')
        response = "The current IP address is: {}".format(
            data.get(bot, __name__, 'server_ip', guild_id=context.guild.id, default=default_ip))
    return Response(content=response)


async def set_units(bot, context):
    default_units = configurations.get(bot, __name__, key='default_units')
    units = data.get(
        bot, __name__, 'server_units', guild_id=context.guild.id, default=default_units)
    if units == 'metric':
        new_units = 'nonmetric'
        response = 'US standard'
    else:
        new_units = 'metric'
        response = 'Metric'
    data.add(bot, __name__, 'server_units', new_units, guild_id=context.guild.id)
    return Response(content=response + ' units are now set as the default.')


async def run_query(bot, context):
    results = context.options['results']
    text_result = 'text' in context.options
    query_url, result, warning = await get_query_result(
        bot, context.guild, context.arguments[0], text_result=text_result, result_lines=results)
    if configurations.get(bot, __name__, 'ads'):
        location = context.channel if context.direct else context.guild
        advertisement = get_advertisement(bot, location)
    else:
        advertisement = None

    embed = discord.Embed(
        description=advertisement if advertisement else discord.Embed.Empty,
        colour=discord.Colour(0xdd1100))
    embed.set_author(name='Query', url=query_url, icon_url='https://i.imgur.com/mFfV1zk.png')

    if text_result:
        for name, value in result:
            embed.add_field(name=name, value=value, inline=False)
    else:
        embed.set_image(url=result)

    if warning:
        embed.set_footer(text=warning, icon_url='https://i.imgur.com/CGl7njZ.png')

    return Response(embed=embed)


def get_advertisement(bot, location):
    """Retrieves an advertisement if one is scheduled."""
    all_uses = data.get(bot, __name__, 'uses', volatile=True)
    ad_uses = configurations.get(bot, __name__, 'ad_uses')
    ad_uses = ad_uses if ad_uses > 0 else 30
    current_uses = all_uses.get(location.id, 0)
    if current_uses >= ad_uses - 1:  # Show advertisement
        if location.id in all_uses:
            del all_uses[location.id]
        content = random.choice((
            "Consider supporting Wolfram|Alpha by trying out Wolfram|Alpha "
            "Pro! It helps keep Wolfram|Alpha free, and provides you with "
            "a much more complete knowledge database experience.",
            "Do you work/study in a STEM field? Wolfram|Alpha Pro can help!",
            "Need help with STEM homework? Wolfram|Alpha Pro has you covered "
            "with step-by-step instructions on how to solve almost any "
            "calculus problems.",
            "Experience professional-grade computational knowledge with "
            "Wolfram|Alpha Pro.",
            "Student or educator in STEM? Wolfram|Alpha brings you the "
            "professional features you need to excel.",
            "Love Wolfram|Alpha? Get more out of your Wolfram|Alpha "
            "experience by going pro!",
            "Need beautifully crafted interactive data visuals? Wolfram|Alpha "
            "Pro can do that for you!",
            "Professional-grade data analysis and visualization can "
            "greatly expedite completing projects and presentations.",
            "Need help with math homework? Get step-by-step solutions for "
            "complexity ranging from arithmetic to calculus and beyind!",
            "Having trouble with learning mathematics? It doesn't matter "
            "if it's algebra or differential equations, Wolfram|Alpha Pro "
            "gives you step-by-step solutions.",
            "Need extra math practice? Wolfram|Alpha Pro can generate an "
            "infinite number of practice problems with step-by-step "
            "solutions to help you ace your exams.",
            "Frequent Wolfram|Alpha user? Tailor your experience for your own "
            "needs with Wolfram|Alpha Pro!",
            "Are your queries timing out? Wolfram|Alpha Pro extends "
            "computation times.",
            "Need powerful visualization and analysis tools for your data? "
            "Wolfram|Alpha Pro is for you!",
            "Directly interact with and download computed data with "
            "Wolfram|Alpha Pro."
            ))
        link = random.choice((
            'See more at', 'For more information, visit',
            'See what upgrading can do at', 'Interested? Check out',
            'Click here for more:', 'Ready to upgrade? See',
            'Curious? Learn more at',
            'Check it out at')) + ' <https://www.wolframalpha.com/pro/>'
        return content + ' ' + link
    else:
        all_uses[location.id] = current_uses + 1


async def wolfram_alpha_query(
        bot, query, user_ip, indices='', format_param='plaintext,image', units='metric'):
    """Returns a query result from Wolfram|Alpha."""
    client = data.get(bot, __name__, 'client', volatile=True)
    client_query = client.GetQuery(query=query)
    client_query.ToURL()
    if indices:
        client_query.AddPodIndex(podindex=indices)
    # client_query.AddFormat(format_param=format_param)  # need both
    client_query.AddIp(ip=user_ip)
    client_query.AddUnits(units=units)
    #query_result = await async_query(client, client_query.Query)
    try:
        query_result = await utilities.future(client.PerformQuery, client_query.Query)
    except Exception as e:
        raise CBException("The query could not be processed.", e=e)
    result = wap.WolframAlphaQueryResult(query_result)
    element = ElementTree.fromstring(result.XmlResult)
    return ElementTree.ElementTree(element=element).getroot()


async def get_query_result(
        bot, guild, query, text_result=False, result_lines=0):
    """Gets a query result and formats it."""
    default_ip = configurations.get(bot, __name__, key='default_ip')
    default_units = configurations.get(bot, __name__, key='default_units')
    if guild is None:
        server_ip = default_ip
        units = default_units
    else:
        server_ip = data.get(
            bot, __name__, 'server_ip',
            guild_id=guild.id, default=default_ip)
        units = data.get(
            bot, __name__, 'server_units',
            guild_id=guild.id, default=default_units)

    indices = ','.join((str(index) for index in range(1, result_lines + 2)))
    format_param = 'plaintext' + ('' if text_result else ',image')
    root = await wolfram_alpha_query(
        bot, query, server_ip, indices=indices, format_param=format_param, units=units)
    pods = root.findall('pod')
    warning = None
    query_url = 'http://www.wolframalpha.com/input/?i={}'.format(urllib.parse.quote_plus(query))

    # Error handling
    if root.get('success') == 'false':

        suggestions = root.find('didyoumeans')
        if suggestions:
            suggestions = suggestions.findall('didyoumean')
            suggestion_text = [suggestion.text for suggestion in suggestions]
        raise CBException(
            "Wolfram|Alpha could not interpret your query.{}".format(
                '' if suggestions is None else ' Suggestion(s): {}'.format(
                    ', '.join(suggestion_text[:3]))))
    elif root.get('timedout'):
        if len(pods) == 0:
            raise CBException("Query timed out.", query_url)
        elif len(pods) < result_lines:
            warning = "Query timed out but returned some results"
    elif len(pods) == 0:
        raise CBException("No result given (general error).", query_url)

    # Format answer
    result_list = []
    if root.find('pod').get('id') != 'Input':
        result_lines -= 1
    if root.find('warnings') is not None:
        spellchecks = root.find('warnings').findall('spellcheck')
        for spellcheck in spellchecks:
            result_list.append(('spellcheck', None, spellcheck.get('text')))
    for pod in root.findall('pod')[:1 + result_lines]:
        for index, sub_pod in enumerate(pod.findall('subpod')):
            image = sub_pod.find('img')
            image_url = '' if image is None else image.get('src')
            text = sub_pod.find('plaintext').text
            title = pod.get('title')
            if index > 0:
                title = None
            result_list.append((title, image_url, text))

    if text_result:
        result = []
        for query_result in result_list:
            text = query_result[2]
            if text:
                if query_result[0] == 'spellcheck':
                    result.append(('Spell check', text))
                elif query_result[0]:
                    result.append((query_result[0], text))
                else:
                    result.append(('\u200b', text))
            else:
                result.append((query_result[0], '[`Image`]({})'.format(query_result[1])))
    else:  # Get the image
        result = await get_result_as_image(bot, result_list)

    return query_url, result, warning


async def get_result_as_image(bot, result_list):
    """Takes image URLs from the result list and creates an image."""
    titles, urls, raw_texts = list(zip(*result_list))

    response_codes, image_bytes = list(zip(*await utilities.get_url(bot, urls, get_bytes=True)))
    image_pairs = []
    max_width, total_height = 0, 0
    font = ImageFont.truetype('{}/plugins/plugin_data/DejaVuSans.ttf'.format(bot.path), 12)

    for title, image, raw_text in zip(titles, image_bytes, raw_texts):
        result_bytes = io.BytesIO(image)
        result_bytes.seek(0)
        if not image:
            result_image = Image.new('RGB', (0, 0))
        else:
            result_image = Image.open(result_bytes)
        result_size = result_image.size

        if title and title != 'spellcheck':
            raw_text = title + ':'
            color = (119, 165, 182)
        else:
            color = (85,)*3

        if title:
            text_dummy = ImageDraw.Draw(result_image)
            text_width = text_dummy.textsize(raw_text, font=font)[0]
            text_image = Image.new('RGB', (text_width, 20), color=(255,)*3)
            draw_image = ImageDraw.Draw(text_image)
            text_size = text_image.size
            draw_image.text((0, 4), raw_text, font=font, fill=color)
        else:
            text_image = None
            text_size = (0, 0)

        max_width = max(result_size[0] + 15, text_size[0], max_width)
        total_height += result_size[1] + 10 + text_size[1]
        image_pairs.append((text_image, result_image))

    image_bytes = combine_images(bot, image_pairs, max_width, total_height)
    return await utilities.upload_to_discord(bot, image_bytes, filename='result.png', close=True)


def combine_images(bot, image_pairs, width, height):
    """Creates an image that combines all of the image pairs. Returns bytes."""
    base_image = Image.new('RGB', (width + 20, height + 20), color=(255,)*3)
    current_y = 10
    for title, image in image_pairs:
        if title is not None:
            base_image.paste(title, box=(10, current_y))
            current_y += 20
        base_image.paste(image, box=(25, current_y))
        current_y += image.size[1] + 10

    image_bytes = io.BytesIO()
    base_image.save(image_bytes, 'png')
    image_bytes.seek(0)
    return image_bytes


@plugins.listen_for('bot_on_ready_boot')
async def create_client(bot):
    """Create a new wolframalpha client object and store in volatile data."""
    config = configurations.get(bot, __name__)
    client = wap.WolframAlphaEngine(config['api_key'], config['server'])
    client.ScanTimeout = config['scan_timeout']
    client.PodTimeout = config['pod_timeout']
    client.FormatTimeout = config['format_timeout']
    data.add(bot, __name__, 'client', client, volatile=True)
    data.add(bot, __name__, 'uses', {}, volatile=True)
