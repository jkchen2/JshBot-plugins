import io
import json
import discord

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from jshbot import utilities, plugins, configurations, data, logger, parser
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import (
        Command, SubCommand, Shortcut, ArgTypes,
        Arg, Opt, Response, Attachment, MessageTypes)

__version__ = '0.1.0'
CBException = ConfiguredBotException('Pride flag creator')
uses_configuration = False

PRIDE_FLAGS = None


class FlagConverter():
    def __init__(self, include_flag_list=True):
        self.include_flag_list = include_flag_list
    def __call__(self, bot, message, value, *a):
        cleaned = utilities.clean_text(value, level=4).replace('flag', '')
        for flag_key, flag_data in PRIDE_FLAGS.items():
            if cleaned == flag_key or cleaned in flag_data.get('substitutions', []):
                return (flag_key, flag_data)
        if self.include_flag_list:
            end = " List of available flags:\n{}".format(', '.join(_get_available_flags()))
        else:
            end = ''
        raise CBException("Flag `{}` not found.{}".format(cleaned, end))


@plugins.command_spawner
def get_commands(bot):

    return [Command(
        'pride', subcommands=[
            SubCommand(
                Opt('types'),
                doc='Shows which pride flags are available.',
                function=pride_types),
            SubCommand(
                Opt('analyze'),
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Analyzes an image or profile picture of the given user '
                    'to show pride flag coverage.',
                function=pride_analyze),
            SubCommand(
                Opt('overlay'),
                Opt('opacity', attached='percentage', optional=True, default="50%",
                    convert=utilities.PercentageConverter(), always_include=True,
                    quotes_recommended=False, check=lambda b, m, v, *a: 0.1 <= v <= 1.0,
                    check_error='Overlay opacity must be between 10% and 100% inclusive.'),
                Opt('mask', optional=True,
                    doc='Composites the pride flag and the opaque pixels in the given image.'),
                Arg('flag type', convert=FlagConverter()),
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Overlays a semi-transparent pride flag over the given image.',
                function=pride_overlay),
            SubCommand(
                Opt('circle'),
                Opt('size', attached='level', optional=True, default=1, quotes_recommended=False,
                    convert=int, always_include=True, check=lambda b, m, v, *a: 1 <= v <= 3,
                    check_error='Circle size level must be between 1 and 3 inclusive.',
                    doc='Thickness of the circle between 1 and 3 inclusive (defaults to 1).'),
                Opt('resize', optional=True,
                    doc='Resizes the image so that the vertical and horizontal extremes '
                        'are not cut off by the drawn circle.'),
                Opt('full', optional=True,
                    doc='Fills in any transparency of the given image with the pride flag.'),
                Arg('flag type', convert=FlagConverter()),
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Draws a pride flag circle around the given image.',
                function=pride_circle),
            SubCommand(
                Opt('fill'),
                Arg('flag type', convert=FlagConverter()),
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Fills in the background of the given image with the pride flag',
                function=pride_fill),
            SubCommand(
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Interactive pride flag image generator.',
                function=pride_interactive)],
        description='Add pride flair to images.',
        allow_direct=False, category='tools')]


def _fp_from_image(image):
    """Returns a BytesIO fp from the given image."""
    fp = io.BytesIO()
    image.save(fp, format='png')
    fp.seek(0)
    return fp


def _file_from_image(image):
    """Returns a discord.File from the given image."""
    return discord.File(_fp_from_image(image), filename='result.png')


async def _get_image(bot, context):
    """Takes the last argument and attachment in the context and gets an Image."""
    text, attachments = context.arguments[-1], context.message.attachments
    if text and attachments:
        raise CBException("Must provide only one user, URL, or image.")

    # Get URL
    url = None
    if text:
        if not context.direct:
            test = data.get_member(bot, text, guild=context.guild, strict=True, safe=True)
            if test:
                url = test.avatar_url_as(format='png')
        if not url:
            if not utilities.valid_url(text):
                raise CBException("User not found, or an invalid image URL was given.")
            url = text
    elif attachments:
        url = attachments[0].url
    else:
        url = context.author.avatar_url_as(format='png')

    # Save URL
    fp = await utilities.download_url(bot, url, use_fp=True)
    image = Image.open(fp).convert('RGBA')

    # Check image size and resize if necessary
    if min(image.size) < 32:
        raise CBException("Minimum dimension too small (<32 pixels).")
    elif max(image.size) > 3000:
        raise CBException("Maximum dimension too large (>3000 pixels).")
    elif image.size[0] != image.size[1]:
        if max(image.size) / min(image.size) > 2.0:
            raise CBException("Aspect ratio of image is too extreme (2:1).")
        image = ImageOps.fit(image, (min(image.size),)*2)

    return image


def _get_available_flags():
    return ['[{0[title]}]({0[image]})'.format(it) for it in PRIDE_FLAGS.values()]


def _generate_pride_flag(flag_type, size):
    """Generates a pride flag given the structure."""
    flag = Image.new('RGBA', size)
    flag_draw = ImageDraw.Draw(flag)

    # Iterate over each structure part in the given flag
    for part in PRIDE_FLAGS[flag_type]['structure']:
        part_type = part['type']
        if part_type == 'rectangle':
            flag_draw.rectangle(
                [
                    tuple(int(it * size[0]) for it in part['start']),
                    tuple(int(it * size[1]) for it in part['end'])
                ],
                fill=tuple(part['color']))
        else:
            raise CBException('Invalid structure type: {}'.format(part_type))

    return flag


async def pride_types(bot, context):
    """Shows pride flag types."""
    return Response(embed=discord.Embed(
        title=':gay_pride_flag: Pride flag list', description=', '.join(_get_available_flags())))


# TODO: Implement
async def pride_analyze(bot, context):
    """Analyzes the given image for coverage percentage."""
    return Response('WIP')


def _process_overlay(image, flag, opacity=0.5, mask=False):
    """Processes the overlay method."""
    combined = image.copy()
    combined.paste(flag, mask=Image.new('L', image.size, int(255*opacity)))
    if mask:
        transparent = Image.new('RGBA', image.size)
        transparent.paste(combined, mask=image)
        combined = transparent
    return combined


async def pride_overlay(bot, context):
    """Overlays or composites the flag over the given image."""
    options, arguments = context.options, context.arguments
    image = await _get_image(bot, context)
    flag = _generate_pride_flag(arguments[0][0], image.size)
    result = _process_overlay(image, flag, opacity=options['opacity'], mask='mask' in options)
    discord_file = _file_from_image(result)
    embed = discord.Embed(
        title='{} pride flag overlay | {:.2f}% transparency | {}masking'.format(
            arguments[0][1]['title'],
            options['opacity'] * 100,
            '' if 'mask' in options else 'no '))
    embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed)


def _process_circle(image, flag, size=1, resize=False, full=False):
    """Processes the circle (ring) method"""
    original = image.copy()

    # Upsample image to ~1000x1000 for better circle/ring definition
    upsample_scale = max(1, 1000/image.size[0])
    dimensions = (int(original.size[0] * upsample_scale),) * 2
    original = original.resize(dimensions, resample=Image.BICUBIC)
    upsampled_flag = flag.resize(dimensions, resample=Image.BICUBIC)

    # Mask away the image
    mask = Image.new('L', dimensions, 0)
    mask_draw = ImageDraw.Draw(mask)
    offset = int(0.04 * dimensions[0] * size)
    mask_draw.ellipse([(offset,)*2, (dimensions[0] - offset - 1,)*2], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.8))

    # Resize image if necessary
    if resize:
        resized = original.resize((dimensions[0] - 2*offset,)*2, resample=Image.BICUBIC)
        original = Image.new('RGBA', dimensions)
        original.paste(resized, (offset,)*2)

    # Make a full circle in the background if necessary
    if full:
        masked_original = Image.composite(original, Image.new('RGBA', dimensions), mask)
        upsampled_flag.paste(masked_original, mask=masked_original.convert('RGBA'))
    else:  # Otherwise, just paste the original
        upsampled_flag.paste(original, mask=mask)

    # Mask away the rest
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse([(0, 0), (dimensions[0] - 1,)*2], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=0.8))
    combined = Image.new('RGBA', dimensions)
    combined.paste(upsampled_flag, mask=mask)

    # Scale back down to the original size
    combined = combined.resize((int(dimensions[0]/upsample_scale),)*2, resample=Image.BICUBIC)
    return combined


async def pride_circle(bot, context):
    """Draws a flag ring or filled circle around the given image."""
    options, arguments = context.options, context.arguments
    image = await _get_image(bot, context)
    flag = _generate_pride_flag(arguments[0][0], image.size)
    result = _process_circle(
        image, flag, size=options['size'],
        resize='resize' in options, full='full' in options)
    discord_file = _file_from_image(result)
    embed = discord.Embed(
        title='{} pride flag circle | size {} | {} | {} background'.format(
            arguments[0][1]['title'], options['size'],
            'resized' if 'resize' in options else 'no resizing',
            'filled-in' if 'full' in options else 'transparent'))
    embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed)


def _process_fill(image, flag):
    """Processes the fill method."""
    combined = flag.copy()
    combined.paste(image, mask=image)
    return combined


async def pride_fill(bot, context):
    """Fills the given image's background with the flag."""
    image = await _get_image(bot, context)
    flag = _generate_pride_flag(context.arguments[0][0], image.size)
    result = _process_fill(image, flag)
    discord_file = _file_from_image(result)
    embed = discord.Embed(title='{} pride flag fill'.format(context.arguments[0][1]['title']))
    embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed)


def _generate_image_options(*images, size=(150, 150)):
    """Creates a collage of the given images to be used as a showcase of options."""
    spacing = 20
    image_copies = [it.resize(size, resample=Image.BICUBIC) for it in images]
    combined = Image.new('RGBA', (size[0]*len(images) + spacing*(len(images)-1), size[1]))

    for index, image in enumerate(image_copies):
        combined.paste(image, ((spacing + size[0]) * index, 0), mask=image)

    return combined


async def pride_interactive(bot, context):
    """Interactive pride flag image creator."""
    image = await _get_image(bot, context)

    async def _menu(bot, context, response, result, timed_out):
        if timed_out:
            response.embed.description = "The menu timed out."
            response.embed.set_image(url='')
            await response.message.edit(embed=response.embed)
            return False
        if not result:
            return

        path, stage = response.state['path'], response.state['stage']
        try:
            result_index = response.extra['buttons'].index(result[0].emoji)
        except ValueError:  # Invalid reaction button - ignore
            return

        # Select method
        if not path:
            images = []
            if result_index == 0:
                path = 'overlay'
                images = [
                    _process_overlay(*response.process_args, mask=False),
                    _process_overlay(*response.process_args, mask=True)]
                response.embed.set_field_at(
                    0, name='Overlay style options', value=':one: No masking | :two: Masking')
            elif result_index == 1:
                path = 'circle'
                images = [
                    _process_circle(*response.process_args, full=False),
                    _process_circle(*response.process_args, full=True)]
                response.embed.set_field_at(
                    0, name='Circle style options',
                    value=':one: Transparent background | :two: Filled background')
            elif result_index == 2:
                path = 'fill'
                images = [_process_fill(*response.process_args)]
                response.embed.remove_field(0)
                response.embed.description = "Here is the final result:"
            else:  # Ignore option 4
                return
            response.state['path'] = path

            if path != 'fill':
                combined = _generate_image_options(*images)
                url = await utilities.upload_to_discord(
                    bot, _fp_from_image(combined), filename='result.png')
            else:
                url = await utilities.upload_to_discord(
                    bot, _fp_from_image(images[0]), filename='result.png')
            response.embed.set_image(url=url)
            await response.message.edit(embed=response.embed)
            return path != 'fill'

        elif path == 'overlay':
            response.state['stage'] += 1
            opacity_list = [0.25, 0.5, 0.75, 1]

            # Finished selecting mask. Now selecting opacity
            if stage == 0:
                response.process_kwargs = { 'mask': result_index == 1 }
                if result_index == 0:
                    del opacity_list[-1]
                images = [
                    _process_overlay(
                        *response.process_args, opacity=it, **response.process_kwargs)
                    for it in opacity_list]
                button_names = ['one', 'two', 'three', 'four']
                style_description = ' | '.join(
                    ':{}: {}% Opacity'.format(button_names[index], it*100)
                    for index, it in enumerate(opacity_list))
                response.embed.set_field_at(
                    0, name='Opacity style options', value=style_description)
                image = _generate_image_options(*images)

            # Finished selecting opacity. Generate final image
            else:
                response.process_kwargs['opacity'] = opacity_list[result_index]
                image = _process_overlay(*response.process_args, **response.process_kwargs)
                response.embed.remove_field(0)
                response.embed.description = "Here is the final result:"

            url = await utilities.upload_to_discord(
                bot, _fp_from_image(image), filename='result.png')
            response.embed.set_image(url=url)
            await response.message.edit(embed=response.embed)
            return response.state['stage'] != 2

        elif path == 'circle':
            response.state['stage'] += 1

            # Finished selecting fill. Now selecting resize
            if stage == 0:
                response.process_kwargs = { 'full': result_index == 1 }
                images = [
                    _process_circle(
                        *response.process_args, resize=False, **response.process_kwargs),
                    _process_circle(
                        *response.process_args, resize=True, **response.process_kwargs)]
                response.embed.set_field_at(
                    0, name='Resize style options', value=':one: No resizing | :two: Resize')
                image = _generate_image_options(*images)

            # Finished selecting resize. Now selecting size
            elif stage == 1:
                response.process_kwargs['resize'] = result_index == 1
                images = [
                    _process_circle(
                        *response.process_args, size=it, **response.process_kwargs)
                    for it in range(1, 4)]
                response.embed.set_field_at(
                    0, name='Size style options',
                    value='Width in pixels:\n:one: 1 | :two: 2 | :three: 3')
                image = _generate_image_options(*images)

            # Finished selecting size. Generate final image
            else:
                response.process_kwargs['size'] = result_index + 1
                image = _process_circle(*response.process_args, **response.process_kwargs)
                response.embed.remove_field(0)
                response.embed.description = "Here is the final result:"

            url = await utilities.upload_to_discord(
                bot, _fp_from_image(image), filename='result.png')
            response.embed.set_image(url=url)
            await response.message.edit(embed=response.embed)
            return response.state['stage'] != 3

    async def _flag_input(bot, context, response, result):
        """Collects the author's response to inputting a pride flag."""
        if result is None:  # Timed out
            response.embed.description = "The menu timed out."
            await response.message.edit(embed=response.embed)
        else:
            converter = FlagConverter(include_flag_list=False)
            flag_key = converter(bot, context.message, result.content)[0]
            response.flag_image = _generate_pride_flag(flag_key, response.image.size)
            response.process_args = (response.image, response.flag_image)

            # Create collage of available methods (overlay, circle, fill)
            args = (response.image, response.flag_image)
            images = [it(*args) for it in [_process_overlay, _process_circle, _process_fill]]
            combined = _generate_image_options(*images)
            url = await utilities.upload_to_discord(
                bot, _fp_from_image(combined), filename='result.png')
            response.embed.description = "Select a style using the reaction buttons below."
            response.embed.set_image(url=url)
            response.embed.add_field(
                name='Style options', value=':one: Overlay | :two: Circle | :three: Fill')
            response.message_type = MessageTypes.INTERACTIVE
            response.extra_function = _menu
            response.extra = {'buttons': ['1⃣', '2⃣', '3⃣', '4⃣']}
            response.state = { 'path': None, 'stage': 0 }
            await result.delete()
            await response.message.edit(embed=response.embed)
            await bot.handle_response(
                context.message, response, message_reference=response.message, context=context)

    description = 'Please type the flag you want to use. Choose from one below:\n{}'.format(
        ', '.join(_get_available_flags()))
    embed = discord.Embed(
        title=':gay_pride_flag: Pride flag avatar creator', description=description)
    extra = { 'event': 'message', 'kwargs': {'check': lambda m: m.author == context.author} }
    return Response(
        embed=embed, message_type=MessageTypes.WAIT,
        extra=extra, extra_function=_flag_input,
        image=image)


@plugins.listen_for('bot_on_ready_boot')
async def setup_pride_flags(bot):
    """Loads the pride flags from pride_flags.json."""
    global PRIDE_FLAGS
    pride_flags_file = utilities.get_plugin_file(bot, 'pride_flags.json', safe=False)
    with open(pride_flags_file, 'r') as flags_file:
        PRIDE_FLAGS = json.load(flags_file)
