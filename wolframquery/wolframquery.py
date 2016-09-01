import urllib
import io

import wap

from PIL import Image, ImageDraw, ImageFont
from xml.etree import ElementTree

from jshbot import utilities, data, configurations
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '0.1.1'
EXCEPTION = 'Wolfram|Alpha API plugin'
uses_configuration = True


def get_commands():
    commands = []

    commands.append(Command(
        'wolfram', SubCommands(
            ('ip &', 'ip (<IP address>)', 'Sets the default IP address used '
             'for queries.'),
            ('?text ?results: ^', '(text) (reslts <number of results>) '
             '<query>', 'Uses Wolfram|Alpha to parse the given query. The '
             'number of results can be up to 8, and defaults to 3. Adding the '
             'text option gives you plaintext results that you can copy.')),
        shortcuts=Shortcuts(
            ('wa', '{}', '^', '<arguments>', '<arguments>'),
            ('swa', 'text results 1 {}', '^', 'text results 1 <query>',
             '<query>')),
        description='Wolfram|Alpha integration.',
        other='API calls are limited. Please use responsibly!', group='tools'))

    return commands


async def async_query(client, client_query):
    """Wraps a query to make it non-blocking."""
    try:
        result = await utilities.future(client.PerformQuery, client_query)
    except Exception as e:
        raise BotException(
            EXCEPTION, "The query could not be processed.", e=e)
    return result


async def wolfram_alpha_query(bot, query, user_ip):
    """Returns a query result from Wolfram|Alpha."""
    client = data.get(bot, __name__, 'client', volatile=True)
    client_query = client.CreateQuery(query, ip=user_ip)
    query_result = await async_query(client, client_query)
    result = wap.WolframAlphaQueryResult(query_result)
    element = ElementTree.fromstring(result.XmlResult)
    return ElementTree.ElementTree(element=element).getroot()


async def get_query_result(
        bot, server, query, text_result=False, extra_results=0):
    """Gets a query result and formats it."""
    default_ip = configurations.get(bot, __name__, key='default_ip')
    if server is None:
        server_ip = default_ip
    else:
        server_ip = data.get(
            bot, __name__, 'server_ip',
            server_id=server.id, default=default_ip)
    root = await wolfram_alpha_query(bot, query, server_ip)
    pods = root.findall('pod')
    response = ''
    query_url = '<http://www.wolframalpha.com/input/?i={}>'.format(
        urllib.parse.quote_plus(query))

    # Error handling
    if root.get('success') == 'false':

        suggestions = root.find('didyoumeans')
        if suggestions:
            suggestions = suggestions.findall('didyoumean')
            suggestion_text = [suggestion.text for suggestion in suggestions]
        raise BotException(
            EXCEPTION,
            "Wolfram|Alpha could not interpret your query.{}".format(
                '' if suggestions is None else ' Suggestion(s): {}'.format(
                    ', '.join(suggestion_text[:3]))))
    elif root.get('timedout'):
        if len(pods) == 0:
            bot.extra = root
            raise BotException(EXCEPTION, "Query timed out.", query_url)
        elif len(pods) < extra_results:
            response += "`Query timed out but returned some results:`\n"
    elif len(pods) == 0:
        raise BotException(
            EXCEPTION, "No result given (general error).", query_url)

    if root.find('sources') is not None:
        query_url = 'Sources: ' + query_url

    # Format answer
    result_list = []
    if root.find('pod').get('id') != 'Input':
        extra_results -= 1
    if root.find('warnings') is not None:
        spellchecks = root.find('warnings').findall('spellcheck')
        for spellcheck in spellchecks:
            result_list.append(('spellcheck', None, spellcheck.get('text')))
    for pod in root.findall('pod')[:1 + extra_results]:
        for index, sub_pod in enumerate(pod.findall('subpod')):
            image = sub_pod.find('img')
            image_url = '' if image is None else image.get('src')
            text = sub_pod.find('plaintext').text
            title = pod.get('title')
            if index > 0:
                title = None
            result_list.append(
                (title, image_url, text))

    if text_result:
        for result in result_list:
            text = result[2] if result[2] else result[1]
            if result[0] == 'spellcheck':
                response += '***`{}`***\n'.format(text)
            elif result[0]:
                response += '\n***`{0}`***\n{1}\n'.format(result[0], text)
            else:
                response += '{}\n'.format(text)
    else:  # Get the image
        response += await get_result_as_image(bot, result_list)
    response += '\n' + query_url

    return response


async def get_result_as_image(bot, result_list):
    """Takes image URLs from the result list and creates an image."""
    titles, urls, raw_texts = list(zip(*result_list))

    response_codes, image_bytes = list(zip(
        *await utilities.get_url(bot, urls, get_bytes=True)))
    image_pairs = []
    max_width, total_height = 0, 0
    font = ImageFont.truetype(
        '{}/plugins/plugin_data/DejaVuSans.ttf'.format(bot.path), 12)

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

    image_bytes = await utilities.future(
        combine_images, bot, image_pairs, max_width, total_height)

    return await utilities.upload_to_discord(
        bot, image_bytes, filename='result.png', close=True)


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


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if blueprint_index == 0:  # set IP
        if message.server is None:
            raise BotException(
                EXCEPTION, "Cannot set IP address in a direct message.")
        if arguments[0]:  # Set IP address
            data.add(
                bot, __name__, 'server_ip', arguments[0],
                server_id=message.server.id)
            response = "IP address set!"
        else:  # Get current IP
            default_ip = configurations.get(bot, __name__, key='default_ip')
            response = "The current IP address is: {}".format(
                data.get(
                    bot, __name__, 'server_ip',
                    server_id=message.server.id, default=default_ip))

    elif blueprint_index == 1:  # regular query
        if 'results' in options:
            try:
                extra_results = int(options['results'])
            except:
                raise BotException(
                    EXCEPTION,
                    "{} is not a valid integer.".format(options['results']))
            if extra_results > 8 or extra_results < 1:
                raise BotException(EXCEPTION, "Results must be between [1-8].")
        else:
            extra_results = 3
        text_result = 'text' in options
        response = await get_query_result(
            bot, message.server, arguments[0],
            text_result=text_result, extra_results=extra_results)

    return (response, tts, message_type, extra)


async def on_ready_boot(bot):
    """Create a new wolframalpha client object and store in volatile data."""
    config = configurations.get(bot, __name__)
    client = wap.WolframAlphaEngine(config['api_key'], config['server'])
    client.ScanTimeout = config['scan_timeout']
    client.PodTimeout = config['pod_timeout']
    client.FormatTimeout = config['format_timeout']
    data.add(bot, __name__, 'client', client, volatile=True)
