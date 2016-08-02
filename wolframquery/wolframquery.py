import wolframalpha
import urllib

from jshbot import utilities, data, configurations
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Wolfram|Alpha API plugin'
uses_configuration = True


def get_commands():
    commands = []

    commands.append(Command(
        'wolfram', SubCommands(
            ('group ^', 'group <query>', 'Groups all results into a single '
             'image.'),
            ('?image ?results: ^', '(results <number of results>) <query>',
             'Uses Wolfram|Alpha to parse the given query. The number of '
             'results can be up to 8.')),
        shortcuts=Shortcuts(
            ('wa', '{}', '^', '<arguments>', '<arguments>')),
        description='Wolfram|Alpha integration.',
        other='API calls are limited. Please use responsibly!', group='tools'))

    return commands


async def async_query(client, query):
    """Wraps a query to make it non-blocking."""
    try:
        result = await utilities.future(client.query, query)
    except Exception as e:
        if 'Error 1:' in str(e):
            raise BotException(
                EXCEPTION,
                "This plugin is not set up properly (invalid appid).")
        elif 'Error 1000:' in str(e):
            raise BotException(
                EXCEPTION, "There was no input for the query.")
        else:
            raise BotException(
                EXCEPTION, "The query could not be processed.", e=e)
    return result


async def wolfram_alpha_query(client, query, simple=True, extra_results=0):
    """Returns a query result from Wolfram|Alpha."""

    if extra_results:
        simple = False
    response = ''
    query_url = "Query URL: http://www.wolframalpha.com/input/?i={}\n".format(
        urllib.parse.quote_plus(query))
    query_result = await async_query(client, query)
    root = query_result.tree.getroot()

    # Error handling
    if root.get('success') == 'false':
        try:
            suggestion = root.find('didyoumeans').find('didyoumean').text
        except Exception as e:  # TODO: Get proper exception type
            print("Something bad happened to the query:\n" + str(e))  # DEBUG
            raise BotException(
                EXCEPTION, "Wolfram|Alpha could not interpret your query.")
        raise BotException(
            EXCEPTION, "Wolfram|Alpha could not interpret your query. "
            "Suggestions:", suggestion)
    elif root.get('timedout'):
        if len(query_result.pods) == 0:
            raise BotException(EXCEPTION, "Query timed out.", query_url)
        elif not simple:
            response += "`Query timed out but returned some results:`\n"
    elif len(query_result.pods) == 0:
        raise BotException(
            EXCEPTION, "No result given (general error).", query_url)

    # Format answer
    if simple:  # Return a straight, single answer
        result_list = list(query_result.results)
        if len(result_list) > 0:
            response = result_list[0].text
            if not response:
                response = result_list[0].img
        else:  # No explicit 'result' was found
            try:
                pod_index = 1 if len(query_result.pods) > 0 else 0
                pod = query_result.pods[pod_index]
                pod_text = pod.text if pod.text else pod.img
                response = "Closest result:\n{}".format(pod_text)
            except Exception as e:  # This shouldn't happen, really
                raise BotException(EXCEPTION, "Something awful happened.", e=e)

    else:  # Full answer, up to 1800 characters long
        number_of_results = 0
        for pod in query_result.pods:
            for sub_pod in list(pod.node):
                image = sub_pod.find('img')
                if image is not None:
                    response += "{pod_title}: {image_url}\n".format(
                        pod_title=pod.__dict__['title'],
                        image_url=image.get('src'))
                    number_of_results += 1
                    if len(response) > 1800:
                        response += "`Truncating long result`"
                        break
            if number_of_results > extra_results + 1:
                break
        response += query_url

    # Check response length
    if len(response) > 1900:
        response = '`Truncating long result`\n{} `...`'.format(response[:1900])

    return response


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    client = data.get(bot, __name__, 'client', volatile=True)

    if blueprint_index == 0:  # group
        response = "Not in quite yet..."
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
            extra_results = 0
        simple = 'image' not in options
        response = await wolfram_alpha_query(
            client, arguments[0], simple=simple, extra_results=extra_results)

    return (response, tts, message_type, extra)


async def on_ready(bot):
    """Create a new wolframalpha client object and store in volatile data."""
    client = wolframalpha.Client(
        configurations.get(bot, __name__, 'api_key'))
    data.add(bot, __name__, 'client', client, volatile=True)
    # await async_query(client, 'python')  # Test appid
