import asyncio

from urllib.request import urlopen

from jshbot import configurations
from jshbot.exceptions import BotException
from jshbot.utilities import future

__version__ = '0.1.0'
EXCEPTION = 'Carbonitex Data Pusher'
uses_configuration = True


def get_commands():
    return []


async def bot_on_ready_boot(bot):
    """Periodically sends a POST request to Carbonitex."""
    carbonitex_key = configurations.get(bot, __name__, key='key')
    use_loop = configurations.get(bot, __name__, key='enabled')
    while use_loop:
        print("In Carbonitex loop")
        await asyncio.sleep(60*60*2)  # 2 hour delay
        try:
            await future(
                urlopen, 'https://www.carbonitex.net/discord/data/botdata.php',
                data={'key': carbonitex_key, 'servercount': len(bot.servers)})
        except Exception as e:
            raise BotException(
                EXCEPTION, "Failed to update Carbonitex data:", e)
