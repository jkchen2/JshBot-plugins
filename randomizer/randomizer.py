import random
import re
import discord

from jshbot import plugins, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
CBException = ConfiguredBotException('Randomizer')


class RollConverter():
    def __call__(self, bot, message, value, *a):
        value = value.lower().strip().replace(' ', '')
        if not value:
            raise CBException("The specifier cannot be blank.")

        bulk, addition_check, bonus = value.partition('+')
        if bulk and addition_check and not bonus:
            raise CBException("Invalid specifier (must be in *x* d *y* + *z* syntax).")
        if bonus:
            try:
                bonus = int(bonus)
            except:
                raise CBException("Invalid static modifier.")
            if bonus < 1:
                raise CBException("Static modifier must be positive.")
        else:
            bonus = 0

        rolls, format_check, sides = bulk.partition('d')
        if rolls and format_check and not sides:
            raise CBException("Invalid specifier (must be in *x* d *y* + *z* syntax).")
        if rolls and not format_check:
            sides = rolls
            rolls = ''
        if sides:
            try:
                sides = int(sides)
            except:
                raise CBException("Invalid number of sides.")
            if not 2 <= sides <= 100:
                raise CBException("Number of sides must be between 2 and 100 inclusive.")
        else:
            sides = 6
        if rolls:
            try:
                rolls = int(rolls)
            except:
                raise CBException("Invalid rolls.")
            if not 1 <= rolls <= 100:
                raise CBException("Rolls must be between 1 and 100 inclusive.")
        else:
            rolls = 1

        return rolls, sides, bonus


class BoundsConverter():
    def __call__(self, bot, message, value, *a):
        result = re.findall(r'-?\d+', value.strip())
        if len(result) != 2:
            raise CBException("Invalid bounds.")
        converted = []
        try:
            for test in result:
                converted.append(int(test))
        except:
            raise CBException("`{}` is an invalid number.".format(test))
        return sorted(converted)


@plugins.command_spawner
def get_commands(bot):
    new_commands = []

    new_commands.append(Command(
        'random', subcommands=[
            SubCommand(doc='Gets a random float value [0.0-1.0)', function=get_random_float),
            SubCommand(
                Opt('roll'),
                Arg('specifier', argtype=ArgTypes.MERGED_OPTIONAL, default='d6',
                    convert=RollConverter()),
                doc='Rolls a die based on the specifier following the D&D dice '
                    'rolling syntax (*x*d*y* + *z*). If no specifier is given, '
                    'this rolls a regular 6-sided die.',
                function=get_roll),
            SubCommand(
                Opt('pick'),
                Arg('option', additional='more options', argtype=ArgTypes.SPLIT),
                doc='Picks an option. Items must be wrapped in quotes if they '
                    'have spaces in them.',
                function=get_pick),
            SubCommand(
                Opt('number'),
                Arg('bounds', argtype=ArgTypes.MERGED_OPTIONAL, default='1 100',
                    convert=BoundsConverter()),
                doc='Gets a random number from 1 to 100, or from the given bounds.',
                function=get_number),
            SubCommand(
                Opt('flip'),
                Arg('times', argtype=ArgTypes.OPTIONAL, quotes_recommended=False,
                    convert=int, check=lambda b, m, v, *a: 1 <= v <= 100, default='1',
                    check_error='Must be between 1 and 100 inclusive.'),
                doc='Flips a virtual coin.', function=get_flip)],
        shortcuts=[
            Shortcut(
                'roll', 'roll {specifier}',
                Arg('specifier', argtype=ArgTypes.MERGED_OPTIONAL)),
            Shortcut(
                'pick', 'pick {arguments}', Arg('arguments', argtype=ArgTypes.MERGED)),
            Shortcut(
                'flip', 'flip {times}', Arg('times', argtype=ArgTypes.MERGED_OPTIONAL))],
        description='Get random stuff.', category='tools'))

    return new_commands


async def get_random_float(bot, context):
    return Response(content=random.random())


async def get_roll(bot, context):
    """Gets a random roll in the D&D syntax style."""
    rolls, sides, bonus = context.arguments[0]
    max_characters = len(str(sides))

    results = [random.randint(1, sides) for it in range(rolls)]
    text_results = ['`{: <{}}\u200b`'.format(it, max_characters) for it in results]
    split_results = [text_results[it:it+10] for it in range(0, len(text_results), 10)]
    result_text = '\n'.join(', '.join(it) for it in split_results)

    embed = discord.Embed(title=':game_die: Dice roll', description=result_text)
    total = sum(results)
    if rolls > 1:
        embed.add_field(name='Sum', value=str(total))
        embed.add_field(name='Mean', value='{:.2f}'.format(total / len(results)))
    if bonus:
        embed.add_field(name='Final', value=str(total + bonus))

    return Response(embed=embed)


async def get_pick(bot, context):
    embed = discord.Embed(
        title=':game_die: Option chooser', description=random.choice(context.arguments))
    return Response(embed=embed)

async def get_number(bot, context):
    embed = discord.Embed(
        title=':game_die: Random number', description=random.randint(*context.arguments[0]))
    return Response(embed=embed)

async def get_flip(bot, context):
    flips = context.arguments[0]
    results = [random.choice(('ⓗ', 'ⓣ')) for number in range(flips)]
    split_results = [results[it:it+10] for it in range(0, len(results), 10)]
    result_text = '\n'.join(', '.join(it) for it in split_results)
    if flips == 1:
        full_name = 'Heads' if results[0] == 'ⓗ' else 'Tails'
        result_text = 'Flipped **{}!** {}'.format(full_name, result_text)
    embed = discord.Embed(title=':game_die: Coin flip', description=result_text)

    if flips > 1:
        total = len(results)
        heads_count = results.count('ⓗ')
        tails_count = total - heads_count
        embed.add_field(
            name='Heads', value='{} ({:.2f}%)'.format(heads_count, 100*heads_count/total))
        embed.add_field(
            name='Tails', value='{} ({:.2f}%)'.format(tails_count, 100*tails_count/total))

    return Response(embed=embed)
