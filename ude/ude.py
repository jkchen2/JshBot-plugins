import json
import datetime

import discord

from jshbot import utilities, configurations, plugins, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import Command, Response

__version__ = '0.1.0'
CBException = ConfiguredBotException('Emoji updater')
uses_configuration = True


@plugins.command_spawner
def get_commands(bot):
    return [Command('ude', elevated_level=3, hidden=True)]


async def get_response(bot, context):
    if 'discrank.py' not in bot.plugins:
        raise CBException("Discrank plugin not detected.")
    discrank_plugin = bot.plugins['discrank.py']
    champions, spells = discrank_plugin.CHAMPIONS, discrank_plugin.SPELLS

    chunks = [bot.get_guild(it).emojis for it in configurations.get(bot, __name__, 'guilds')]
    emojis = [it for chunk in chunks for it in chunk]

    final = {
        'champions': {'id': {}, 'name': {}},
        'spells': {'id': {}, 'name': {}},
        'bdt': {'blue': {}, 'red': {}}
    }
    for emoji in emojis:

        if emoji.name.startswith('Champion'):
            clean_name = emoji.name.split('_')[1].lower()
            if clean_name not in champions:
                raise CBException("Champion {} not found.".format(clean_name))
            item_id = champions[clean_name]['id']
            final['champions']['id'][str(item_id)] = str(emoji)
            final['champions']['name'][clean_name] = str(emoji)

        elif emoji.name.startswith('Spell'):
            clean_name = emoji.name.split('_')[1].lower()
            if clean_name not in spells:
                raise CBException("Spell {} not found.".format(clean_name))
            item_id = spells[clean_name]['id']
            final['spells']['id'][str(item_id)] = str(emoji)
            final['spells']['name'][clean_name] = str(emoji)

        elif emoji.name.startswith(('Red', 'Blue')):
            color, name = emoji.name.split('_')
            final['bdt'][color.lower()][name.lower()] = str(emoji)

        else:
            raise CBException("Invalid emoji detected: {}".format(emoji.name))

    final_json = json.dumps(final, sort_keys=True, indent=4)
    json_file = utilities.get_text_as_file(final_json)

    file_url = await utilities.upload_to_discord(
        bot, json_file, filename='lol_emojis.json', close=True)
    embed = discord.Embed(
        description='[Click here to download]({})'.format(file_url),
        colour=discord.Colour(0x4CAF50),
        timestamp=datetime.datetime.utcnow())
    embed.set_footer(text="Updated")

    try:
        update_channel = bot.get_channel(configurations.get(bot, __name__, 'update_channel'))
        message_id = configurations.get(bot, __name__, 'update_message')
        update_message = await update_channel.fetch_message(message_id)
        await update_message.edit(content='', embed=embed)
    except Exception as e:
        raise CBException("Failed to edit the update message.", e=e)

    return Response(content="Updated!")
