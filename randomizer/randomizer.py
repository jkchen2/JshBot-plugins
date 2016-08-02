import random

from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Randomizer'


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'random', SubCommands(
            ('roll &', 'roll (<specifier>)', 'Rolls a die based on the '
             'specifier, which follows the D&D dice rolling syntax (xdy). If '
             'no specifier is given, this rolls a regular 6-sided die.'),
            ('pick :+', 'pick <"option 1"> <"option 2"> (<"option 3">) (...) ',
             'Picks an option. Items must be wrapped in quotes if they have '
             'spaces in them.'),
            ('number :&', 'number <lower bound> (upper bound)', 'Gets a '
             'random number from 0 to the lower bound, or between the bounds '
             'if two are given.'),
            ('number', 'number', 'Gets a random number from 1 to 100.'),
            ('flip &', 'flip (<number of flips>)', 'Flips the coin'),
            (' ', ' ', 'Gets a random float [0.0-1.0)')),
        # ('?sound tag', '(sound) tag', 'Retrieves a random tag.')),
        shortcuts=Shortcuts(
            ('roll', 'roll {}', '&', 'roll (<specifier>)', '(<specifier>)'),
            ('pick', 'pick {}', '^', 'pick <arguments>', '<arguments>'),
            ('flip', 'flip {}', '&', 'flip (<number of flips>)',
             '(<number of flips>)')),
        description='Get random stuff.', group='tools'))

    return new_commands


def get_roll(specifier):
    """Gets a random roll in the D&D syntax style."""
    if not specifier:  # Roll a 6 sided die
        specifier = 'd6'
    specifier = specifier.replace(' ', '')
    rolls, d, sides = specifier.partition('d')
    offset = 0
    if '+' in sides or '-' in sides:
        operator = '+' if '+' in sides else '-'
        sides, operation, offset = sides.partition(operator)
        try:
            offset = int(offset)
            if operation == '+':
                assert offset > 0
            else:
                offset = -offset
                assert offset < 0
        except:
            raise BotException(EXCEPTION, "Invalid offset (must match sign).")

    if d != 'd':
        raise BotException(EXCEPTION, "Invalid roll specification.")

    if rolls != '':
        try:
            rolls = int(rolls)
            assert 1 <= rolls <= 100
        except:
            raise BotException(
                EXCEPTION, "Invalid roll number. [1-100]")
    else:
        rolls = 1

    if sides != '':
        try:
            sides = int(sides)
            assert 2 <= sides <= 100
        except:
            raise BotException(
                EXCEPTION, "Invalid sides. [2-100]")
    else:
        raise BotException(EXCEPTION, "No die sides specified. [2-100]")

    results = [random.randint(1, sides) for roll in range(rolls)]
    response = '**Rolled:** {}\n'.format(
        ', '.join([str(result) for result in results]))
    if rolls > 1:
        response += '**Summed:** {}\n'.format(sum(results))
    if offset != 0:
        response += '**Final result:** {}\n'.format(sum(results) + offset)
    return response


def pick_choice(choices):
    """Picks a choice from the choices."""
    return random.choice(choices)


def get_number(bound, other_bound):
    """Gets a random number between the bounds."""
    try:
        bound1 = int(bound)
    except:
        raise BotException(EXCEPTION, '{} is an invalid number'.format(bound))
    if other_bound != '':
        try:
            bound2 = int(other_bound)
        except:
            raise BotException(
                EXCEPTION, '{} is an invalid number'.format(other_bound))
    else:
        other_bound = None

    if other_bound is None:
        result = random.randint(min(bound1, 0), max(bound1, 0))
    else:
        result = random.randint(min(bound1, bound2), max(bound1, bound2))

    return '**Result:** {}'.format(result)


def get_flip(flips):
    """Flips a coin."""
    if flips == '':
        return 'Flipped **{0}!** {1}'.format(
            *random.choice((('Heads', 'ⓗ'), ('Tails', 'ⓣ'))))
    else:
        try:
            flips = int(flips)
            assert 2 <= flips <= 100
        except:
            raise BotException(EXCEPTION, "Invalid number of flips. [2-100]")
        results = [random.choice(('ⓗ', 'ⓣ')) for number in range(flips)]
        heads_count = len([result for result in results if result == 'ⓗ'])
        tails_count = flips - heads_count
        heads_percent = 100 * heads_count / flips
        tails_percent = 100 - heads_percent
        return (
            '**Results:** {0}\n**Heads:** {1} ({2:.2f}%)\n'
            '**Tails:** {3} ({4:.2f}%)'.format(
                ', '.join(results), heads_count, heads_percent,
                tails_count, tails_percent))


async def get_random_tag(bot, message):
    """Gets a random tag if the tag plugin is available."""
    if 'tags.py' not in bot.plugins:
        raise BotException(EXCEPTION, "The tags plugin is not installed.")
    raise BotException(
        EXCEPTION, "I'm not sure how you did this, but congratulations.")
    return "Not yet..."


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if blueprint_index == 0:  # Roll
        response = get_roll(arguments[0])
    elif blueprint_index == 1:  # Pick
        response = pick_choice(arguments)
    elif blueprint_index == 2:  # Number with bounds
        response = get_number(*arguments)
    elif blueprint_index == 3:  # 1-100 number
        response = get_number(1, 100)
    elif blueprint_index == 4:  # Coin flip
        response = get_flip(arguments[0])
    elif blueprint_index == 5:  # random.random()
        response = random.random()
    elif blueprint_index == 5:  # Tag (not finished yet)
        response = await get_random_tag(bot)

    return (response, tts, message_type, extra)
