import io
import json
import discord

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from jshbot import utilities, plugins, configurations, data, logger, parser
from jshbot.exceptions import ConfiguredBotException, BotException
from jshbot.commands import (
        Command, SubCommand, Shortcut, ArgTypes,
        Arg, Opt, Response, Attachment, MessageTypes)

__version__ = '0.1.1'
CBException = ConfiguredBotException('Pride flag creator')
uses_configuration = False

PRIDE_FLAGS = None


class FlagConverter():
    def __init__(self, include_flag_list=True, allow_urls=True, check_attachments=False):
        self.include_flag_list = include_flag_list
        self.allow_urls = allow_urls
        self.check_attachments = check_attachments
    def __call__(self, bot, message, value, *a):
        cleaned = utilities.clean_text(value, level=4).replace('flag', '').replace('pride', '')
        for flag_key, flag_data in PRIDE_FLAGS.items():
            if cleaned == flag_key or cleaned in flag_data.get('substitutions', []):
                return (flag_key, flag_data)
        if self.allow_urls:
            if value and utilities.valid_url(value):
                return ('url', {'image': value, 'title': 'Custom'})
            elif self.check_attachments and message.attachments:
                return ('url', {'image': message.attachments[0].url, 'title': 'Custom'})
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
                Opt('flags'),
                doc='Shows which pride flags are available.',
                function=pride_flags),
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
                    convert=utilities.PercentageConverter(), always_include=True, group='style',
                    quotes_recommended=False, check=lambda b, m, v, *a: 0.1 <= v <= 1.0,
                    check_error='Overlay opacity must be between 10% and 100% inclusive.'),
                Opt('mask', optional=True, group='style',
                    doc='Composites the pride flag and the opaque pixels in the given image.'),
                Opt('rotation', attached='angle', optional=True, group='style',
                    always_include=True, default=0, quotes_recommended=False,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 360,
                    check_error='Rotation angle must be between 0 and 360 inclusive.'),
                Arg('flag type', convert=FlagConverter()),
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Overlays a semi-transparent pride flag over the given image.',
                function=pride_overlay),
            SubCommand(
                Opt('circle'),
                Opt('size', attached='level', optional=True, default=1, group='style',
                    quotes_recommended=False, convert=int, always_include=True,
                    check=lambda b, m, v, *a: 1 <= v <= 3,
                    check_error='Circle size level must be between 1 and 3 inclusive.',
                    doc='Thickness of the circle between 1 and 3 inclusive (defaults to 1).'),
                Opt('resize', optional=True, group='style',
                    doc='Resizes the image so that the vertical and horizontal extremes '
                        'are not cut off by the drawn circle.'),
                Opt('full', optional=True, group='style',
                    doc='Fills in any transparency of the given image with the pride flag.'),
                Opt('rotation', attached='angle', optional=True, group='style',
                    always_include=True, default=0, quotes_recommended=False,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 360,
                    check_error='Rotation angle must be between 0 and 360 inclusive.'),
                Arg('flag type', convert=FlagConverter()),
                Arg('user or URL', argtype=ArgTypes.MERGED_OPTIONAL, group='user or image'),
                Attachment('Image', optional=True, group='user or image'),
                doc='Draws a pride flag circle around the given image.',
                function=pride_circle),
            SubCommand(
                Opt('fill'),
                Opt('rotation', attached='angle', optional=True,
                    always_include=True, default=0, quotes_recommended=False,
                    convert=int, check=lambda b, m, v, *a: 0 <= v <= 360,
                    check_error='Rotation angle must be between 0 and 360 inclusive.'),
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
        allow_direct=True, category='tools')]


def _fp_from_image(image):
    """Returns a BytesIO fp from the given image."""
    fp = io.BytesIO()
    image.save(fp, format='png')
    fp.seek(0)
    return fp


def _file_from_image(image):
    """Returns a discord.File from the given image."""
    return discord.File(_fp_from_image(image), filename='result.png')


async def _get_image(bot, context=None, message=None, url=None):
    """Takes the last argument and attachment in the context and gets an Image.
    
    If a message is given, this will instead look for a link or attachment in there.
    If a url is given, this directly attempts to download that
    """
    # Get URL
    if not url:

        if message:
            text, attachments = message.content, message.attachments
        else:
            text, attachments = context.arguments[-1], context.message.attachments
        if text and attachments:
            raise CBException("Must provide only one user, URL, or image.")

        if text:
            if not message and not context.direct:
                test = data.get_member(bot, text, guild=context.guild, strict=True, safe=True)
                if test:
                    url = test.avatar_url_as(format='png')
            if not url:
                if not utilities.valid_url(text):
                    if message:
                        raise CBException("An invalid image URL was given.")
                    else:
                        raise CBException("User not found, or an invalid image URL was given.")
                url = text
        elif attachments:
            url = attachments[0].url
        else:
            url = (context or message).author.avatar_url_as(format='png')

    # Save URL
    fp = await utilities.download_url(bot, url, use_fp=True)
    try:
        image = Image.open(fp).convert('RGBA')
    except Exception as e:
        raise CBException("Invalid image given.", e=e)

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


async def _generate_pride_flag(bot, flag_data, size):
    """Generates a pride flag given the structure."""
    # Iterate over each structure part in the given flag
    if 'structure' in flag_data:
        flag = Image.new('RGBA', size)
        flag_draw = ImageDraw.Draw(flag)
        for part in flag_data['structure']:
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

    # No structure defined, download image
    elif 'image' in flag_data:
        flag = await _get_image(bot, url=flag_data['image'])
        flag = ImageOps.fit(flag, size, method=Image.BICUBIC)

    return flag


async def pride_flags(bot, context):
    """Shows pride flag types."""
    return Response(embed=discord.Embed(
        title=':gay_pride_flag: Pride flag list', description=', '.join(_get_available_flags())))


async def pride_analyze(bot, context):
    """Analyzes the given image for coverage percentage."""
    # Convert to a reasonable resolution
    resolution = 200
    image = await _get_image(bot, context=context)
    image = image.convert('RGBA').resize((resolution,)*2).getchannel('A').convert('1')

    # Make method suggestions
    percentage = image.histogram()[0] / resolution**2
    no_transparency = percentage < 0.0001
    if percentage < 0.2:
        result = 'no transparency' if no_transparency else 'low transparency'
        suggestions = [
            'Overlay with no masking',
            'Circle with no fill']
    elif percentage < 0.4:
        result = 'transparency'
        suggestions = [
            'Overlay with masking',
            'Circle with no fill',
            'Fill']
    elif percentage < 0.8:
        result = 'high transparency'
        suggestions = [
            'Overlay with masking',
            'Circle with a filled background',
            'Fill']
    else:
        result = 'very high transparency'
        suggestions = ['Fill']

    # Attach image only if there is some level of transparency
    embed = discord.Embed(
        title='Transparency analyzer',
        description='Image has {} ({}%)'.format(result, int(percentage*100)))
    embed.add_field(name='Suggested method(s):', value='\n'.join(suggestions))
    if no_transparency:
        discord_file = None
    else:
        discord_file = _file_from_image(image)
        embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed, message_type=MessageTypes.PERMANENT)


def _rotate_flag(flag, rotation):
    """Rotates the square flag by the given number of degrees. Preserves size."""
    original_size = flag.size
    offset = int(((flag.size[0] * (2**0.5)) - flag.size[0]) / 2)
    flag = flag.resize((int(flag.size[0]*(2**0.5)) + 1,)*2, resample=Image.BICUBIC)
    flag = flag.rotate(rotation, resample=Image.BICUBIC)
    if rotation % 90 == 0:  # TODO: Actually calculate crop amount needed
        return ImageOps.fit(flag, original_size)
    return flag.crop([offset, offset, offset + original_size[0], offset + original_size[0]])


def _process_overlay(image, flag, opacity=0.5, mask=False, rotation=0):
    """Processes the overlay method."""
    if rotation:
        flag = _rotate_flag(flag, rotation)
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
    image = await _get_image(bot, context=context)
    flag = await _generate_pride_flag(bot, arguments[0][1], image.size)
    result = _process_overlay(
        image, flag, opacity=options['opacity'],
        mask='mask' in options, rotation=options['rotation'])
    discord_file = _file_from_image(result)
    embed = discord.Embed(
        title='{} pride flag overlay | {:.2f}% transparency | {}masking'.format(
            arguments[0][1]['title'],
            options['opacity'] * 100,
            '' if 'mask' in options else 'no '))
    embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed, message_type=MessageTypes.PERMANENT)


def _process_circle(image, flag, size=1, resize=False, full=False, rotation=0):
    """Processes the circle (ring) method"""
    if rotation:
        flag = _rotate_flag(flag, rotation)
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
    image = await _get_image(bot, context=context)
    flag = await _generate_pride_flag(bot, arguments[0][1], image.size)
    result = _process_circle(
        image, flag, size=options['size'], resize='resize' in options,
        full='full' in options, rotation=options['rotation'])
    discord_file = _file_from_image(result)
    embed = discord.Embed(
        title='{} pride flag circle | size {} | {} | {} background'.format(
            arguments[0][1]['title'], options['size'],
            'resized' if 'resize' in options else 'no resizing',
            'filled-in' if 'full' in options else 'transparent'))
    embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed, message_type=MessageTypes.PERMANENT)


def _process_fill(image, flag, rotation=0):
    """Processes the fill method."""
    if rotation:
        flag = _rotate_flag(flag, rotation)
    combined = flag.copy()
    combined.paste(image, mask=image)
    return combined


async def pride_fill(bot, context):
    """Fills the given image's background with the flag."""
    image = await _get_image(bot, context=context)
    flag = await _generate_pride_flag(bot, context.arguments[0][1], image.size)
    result = _process_fill(image, flag, rotation=context.options['rotation'])
    discord_file = _file_from_image(result)
    embed = discord.Embed(title='{} pride flag fill'.format(context.arguments[0][1]['title']))
    embed.set_image(url='attachment://result.png')
    return Response(file=discord_file, embed=embed, message_type=MessageTypes.PERMANENT)


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
    image = await _get_image(bot, context=context)

    rotations = [0, 90, 45, 315]
    def _generate_rotated_images(response):
        """Generates rotated images from the response's args and kwargs."""
        response.state['path'] = 'rotation'
        response.embed.set_field_at(
            0, name='Rotation style options',
            value='Angle in degrees:\n{}'.format(' | '.join(
                '{} {}\u00b0'.format(utilities.NUMBER_EMOJIS[index + 1], it)
                for index, it in enumerate(rotations))))
        return [
            response.path_process(*response.process_args, **response.process_kwargs, rotation=it)
            for it in rotations]

    async def _menu(bot, context, response, result, timed_out):
        if timed_out:
            response.embed.description = "The menu timed out."
            response.embed.remove_field(0)
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
        if not path and stage == 0:
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
            elif result_index == 2:  # Fill selected. Now selecting rotation
                path = 'rotation'
                response.path_process = _process_fill
                images = _generate_rotated_images(response)
            else:  # Ignore option 4
                return

            response.state['path'] = path
            image = _generate_image_options(*images)

        elif path == 'overlay':
            response.state['stage'] += 1
            opacity_list = [0.25, 0.5, 0.75, 1]

            # Finished selecting mask. Now selecting opacity
            if stage == 0:
                response.process_kwargs['mask'] = result_index == 1
                if result_index == 0:
                    del opacity_list[-1]
                images = [
                    _process_overlay(
                        *response.process_args, opacity=it, **response.process_kwargs)
                    for it in opacity_list]
                style_description = 'Opacity:\n{}'.format(' | '.join(
                    ':{}: {}%'.format(utilities.NUMBER_EMOJIS[index + 1], it*100)
                    for index, it in enumerate(opacity_list)))
                response.embed.set_field_at(
                    0, name='Opacity style options', value=style_description)

            # Finished selecting opacity. Now selecting rotation
            else:
                response.process_kwargs['opacity'] = opacity_list[result_index]
                response.path_process = _process_overlay
                images = _generate_rotated_images(response)

            image = _generate_image_options(*images)

        elif path == 'circle':
            response.state['stage'] += 1

            # Finished selecting fill. Now selecting resize
            if stage == 0:
                response.process_kwargs['full'] = result_index == 1
                images = [
                    _process_circle(
                        *response.process_args, resize=False, **response.process_kwargs),
                    _process_circle(
                        *response.process_args, resize=True, **response.process_kwargs)]
                response.embed.set_field_at(
                    0, name='Resize style options', value=':one: No resizing | :two: Resize')

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

            # Finished selecting size. Now selecting rotation
            else:
                response.process_kwargs['size'] = result_index + 1
                response.path_process = _process_circle
                images = _generate_rotated_images(response)

            image = _generate_image_options(*images)

        elif path == 'rotation':
            path = 'done'
            response.process_kwargs['rotation'] = rotations[result_index]
            image = response.path_process(*response.process_args, **response.process_kwargs)
            response.embed.remove_field(0)
            response.embed.description = "Here is the final result:"

        url = await utilities.upload_to_discord(bot, _fp_from_image(image), filename='result.png')
        response.embed.set_image(url=url)
        await response.message.edit(embed=response.embed)
        return path != 'done'

    async def _flag_input(bot, context, response, result):
        """Collects the author's response to inputting a pride flag."""
        if not result:  # Timed out
            response.embed.description = "The menu timed out."
            response.embed.remove_field(0)
            await response.message.edit(embed=response.embed)
        else:
            converter = FlagConverter(
                include_flag_list=False, check_attachments=True)
            flag_key, flag_data = converter(bot, result, result.content)
            if flag_key == 'url':
                response.embed.description = "Please wait..."
                response.embed.set_field_at(0, name='\u200b', value='\u200b')
                await response.message.edit(embed=response.embed)
            response.flag_image = await _generate_pride_flag(bot, flag_data, response.image.size)
            response.process_args = (response.image, response.flag_image)
            response.process_kwargs = {}

            # Create collage of available methods (overlay, circle, fill)
            args = (response.image, response.flag_image)
            images = [it(*args) for it in [_process_overlay, _process_circle, _process_fill]]
            combined = _generate_image_options(*images)
            url = await utilities.upload_to_discord(
                bot, _fp_from_image(combined), filename='result.png')
            response.embed.description = "Select a style using the reaction buttons below."
            response.embed.set_image(url=url)
            response.embed.set_field_at(
                0, name='Style options', value=':one: Overlay | :two: Circle | :three: Fill')
            response.message_type = MessageTypes.INTERACTIVE
            response.extra_function = _menu
            response.extra = { 'buttons': ['1⃣', '2⃣', '3⃣', '4⃣'] }
            response.state = { 'path': None, 'stage': 0 }
            try:
                await result.delete()
            except:  # Ignore permissions errors
                pass
            await response.message.edit(embed=response.embed)
            await bot.handle_response(
                context.message, response, message_reference=response.message, context=context)

    description = 'Please type the flag you want to use. Choose from one below:\n{}'.format(
        ', '.join(_get_available_flags()))
    embed = discord.Embed(
        title=':gay_pride_flag: Pride flag avatar creator', description=description)
    embed.add_field(
        name='\u200b', value="To use a custom flag, paste the image URL below or upload it.")
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
