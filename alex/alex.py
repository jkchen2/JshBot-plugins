import subprocess

from PIL import Image, ImageDraw, ImageFont
from jshbot import utilities
from jshbot.commands import Command, SubCommands
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'AlexJS'
uses_configuration = False

PAD_SIZE = 5
left_red, right_red, left_green, right_green = None, None, None, None
text_a, text_b, text_c, text_d = None, None, None, None
alex_parent, font = None, None


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'alex', SubCommands(
            ('^', '<input>', 'Criticizes your input.')),
        description='AlexJS for Discord.', group='memes'))
    new_commands.append(Command(
        'alexify', SubCommands(
            (':&', '<first> (second)', 'Makes a suggestion.')),
        description='Alexify a word.', group='memes'))

    return new_commands


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if base == 'alex':
        response = await utilities.future(alex_parse, bot, arguments[0])

    elif base == 'alexify':
        for text in arguments:
            if len(text) > 32:
                raise BotException(EXCEPTION, "32 character limit.")
        if arguments[1]:
            await utilities.future(build_dual_text, bot, *arguments)
        else:
            await utilities.future(build_single_text, bot, arguments[0])

        message_type = 5
        response = open(
            '{}/temp/alexout.png'.format(bot.path), 'rb')

    return (response, tts, message_type, extra)


def alex_parse(bot, text):
    with open(bot.path + '/temp/alexinput.txt', 'w') as alex_file:
        alex_file.write(text)
    try:
        alex_out = subprocess.check_output(
            'cat "{}/temp/alexinput.txt" | alex'.format(bot.path),
            stderr=subprocess.STDOUT, shell=True)
    except subprocess.CalledProcessError as e:
        response = e.output.decode('utf-8')
    else:
        response = alex_out.decode('utf-8')
    return '```\n{}```'.format(response)


def build_dual_text(bot, text_1, text_2):
    text_dummy = ImageDraw.Draw(alex_parent)
    text_1_size = text_dummy.textsize(text_1, font=font)[0]
    text_1_image = Image.new('RGB', (text_1_size, 32), (206, 64, 55))
    text_1_draw = ImageDraw.Draw(text_1_image)
    text_1_draw.text((0, 2), text_1, font=font)
    text_2_size = text_dummy.textsize(text_2, font=font)[0]
    text_2_image = Image.new('RGB', (text_2_size, 32), (41, 168, 83))
    text_2_draw = ImageDraw.Draw(text_2_image)
    text_2_draw.text((0, 2), text_2, font=font)

    total_size = 20 + 275 + 95 + text_1_size + text_2_size + (2 * PAD_SIZE)
    current_x = PAD_SIZE

    base_image = Image.new(
        'RGB', (total_size, 32 + (2 * PAD_SIZE)), (255, 255, 255))

    # Red block of text 1
    base_image.paste(left_red, box=(current_x, PAD_SIZE))
    current_x += 5
    base_image.paste(text_1_image, box=(current_x, PAD_SIZE))
    current_x += text_1_size
    base_image.paste(right_red, box=(current_x, PAD_SIZE))
    current_x += 5

    # Text A
    base_image.paste(text_a, box=(current_x, PAD_SIZE))
    current_x += 275

    # Green block of text 2
    base_image.paste(left_green, box=(current_x, PAD_SIZE))
    current_x += 5
    base_image.paste(text_2_image, box=(current_x, PAD_SIZE))
    current_x += text_2_size
    base_image.paste(right_green, box=(current_x, PAD_SIZE))
    current_x += 5

    # Text B
    base_image.paste(text_b, box=(current_x, PAD_SIZE))

    base_image.save(
        '{}/temp/alexout.png'.format(bot.path), 'png')


def build_single_text(bot, text_1):
    text_dummy = ImageDraw.Draw(alex_parent)
    text_1_size = text_dummy.textsize(text_1, font=font)[0]
    text_1_image = Image.new('RGB', (text_1_size, 32), (206, 64, 55))
    text_1_draw = ImageDraw.Draw(text_1_image)
    text_1_draw.text((0, 2), text_1, font=font)

    total_size = 10 + 113 + 144 + text_1_size + (2 * PAD_SIZE)
    current_x = PAD_SIZE

    base_image = Image.new(
        'RGB', (total_size, 32 + (2 * PAD_SIZE)), (255, 255, 255))

    # Text C
    base_image.paste(text_c, box=(current_x, PAD_SIZE))
    current_x += 113

    # Red block of text 1
    base_image.paste(left_red, box=(current_x, PAD_SIZE))
    current_x += 5
    base_image.paste(text_1_image, box=(current_x, PAD_SIZE))
    current_x += text_1_size
    base_image.paste(right_red, box=(current_x, PAD_SIZE))
    current_x += 5

    # Text D
    base_image.paste(text_d, box=(current_x, PAD_SIZE))

    base_image.save(
        '{}/temp/alexout.png'.format(bot.path), 'png')


async def on_ready(bot):
    global left_red, right_red, left_green, right_green
    global text_a, text_b, text_c, text_d, font, alex_parent

    alex_parent = Image.open(
        '{}/plugins/plugin_data/alexify.png'.format(bot.path))
    left_red = alex_parent.crop((0, 0, 5, 32))
    right_red = alex_parent.crop((6, 0, 11, 32))
    left_green = alex_parent.crop((12, 0, 17, 32))
    right_green = alex_parent.crop((18, 0, 23, 32))
    text_a = alex_parent.crop((24, 0, 299, 32))
    text_b = alex_parent.crop((300, 0, 395, 32))
    text_c = alex_parent.crop((396, 0, 509, 32))
    text_d = alex_parent.crop((510, 0, 654, 32))
    font = ImageFont.truetype(
        '{}/plugins/plugin_data/VeraMono.ttf'.format(bot.path), 24)
