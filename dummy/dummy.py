###############################################################################
#
# NOTE: If you don't need this file, feel free to delete it! It only serves to
#   be a demo of how plugins should be made.
#
#
# Hey there!
#
# This is dummy.py, or the whirlwind tour of what JshBot 0.4.x is about, and
#   how it can be used.
#
# The file demonsrates how to create new commands with standard syntax,
#   as well as shortcuts and help (manual entry).
#
# There is also a demonstration on how get_response() should be used, and how
#   to read a parsed result.
#
# Lastly, there is a small amount of demo code to show off that plugins can
#   define standard events from discord.Client, and they will be called
#   appropriately.
#
###############################################################################

import asyncio

from jshbot import utilities, configurations, plugins, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import (
    Command, SubCommand, Shortcut, ArgTypes, Attachment, Arg, Opt, MessageTypes, Response)

__version__ = '0.2.0'
CBException = ConfiguredBotException('Dummy')
uses_configuration = True


# Apply this decorator to every function that returns a list of new commands
@plugins.command_spawner
def get_commands(bot):
    """Returns a list of commands associated with this plugin."""
    new_commands = []

    new_commands.append(Command(
        'mycommand', subcommands=[
            SubCommand(
                Opt('myoption'),
                doc='This is a simple command with a single required option.'),
            SubCommand(
                Opt('custom', optional=True),
                Opt('attached', optional=True, attached='attached argument'),
                doc='This has two different optional options, one without an attached '
                    'parameter, and the other requiring an attached parameter.'),
            SubCommand(
                Opt('trailing'),
                Arg('arg 1'),
                Arg('arg 2'),
                Arg('arg 3', argtype=ArgTypes.SPLIT, additional='more args'),
                doc='This command requires a lot of trailing arguments.'),
            SubCommand(
                Opt('grouped'),
                Arg('grouped arguments', argtype=ArgTypes.MERGED),
                doc='This will group all given arguments as a single string.'),
            SubCommand(
                Opt('complex', attached='attached'),
                Opt('other', optional=True, attached='also required'),
                Arg('arg 1'),
                Arg('arg 2', argtype=ArgTypes.SPLIT_OPTIONAL, additional='more args'),
                doc='The complex option has a required attached parameter, and the '
                    '\'other\' option also has a required attached parameter if '
                    '\'other\' is included. Additionally, there will be a requirement '
                    'of at least 1 trailing argument.'),
            SubCommand(
                Opt('marquee'),
                Arg('text', argtype=ArgTypes.MERGED,
                    check=lambda b, m, v, *a: len(v) <= 100,
                    check_error="Marquee message must be less than 100 characters long."),
                doc='Creates a marquee that loops 3 times.')],
        shortcuts=[
            Shortcut(
                'complex', 'complex {attached} other {other} {arg 1} {arg 2}',
                Arg('attached'), Arg('other'), Arg('arg 1'),
                Arg('arg 2', argtype=ArgTypes.SPLIT_OPTIONAL)),
            Shortcut(
                'marquee', 'marquee {text}', Arg('text', argtype=ArgTypes.MERGED))],
        description='Your command description here.',
        other='This text is optional - it just shows up after everything '
              'else. Quick note, all of the commands here can only be used by '
              'bot moderators or above, as indicated by elevated_level. A '
              'level of 2 would mean only server owners or above can use the '
              'command, and a level of 3 would restrict the command to only '
              'the bot owners.',
        elevated_level=1, category='demo'))

    new_commands.append(Command(
        'myothercommand', subcommands=[
            SubCommand(
                Arg('text', argtype=ArgTypes.MERGED_OPTIONAL),
                doc='This traps all further commands from being executed.'),
            SubCommand(
                Opt('order'), Opt('matters'),
                doc='It is impossible to access this command because the first '
                    'subcommand will always be satisfied first. Order of the '
                    'subcommand matters!'),
            SubCommand(
                Opt('sample'), Opt('foo'), Opt('bar'),
                doc='Also impossible to access. This subcommand just adds some '
                    'keywords to the command.')],
        description='Only bot owners can see this text!',
        other='Note that no shortcuts were defined. They, too, are optional. '
              'Also, this command is hidden, which means that only the bot '
              'owners can see this command listed from the help command. '
              'However, unless the command is configured with an elevated '
              'permissions level, any user can still execute the command. '
              'Users still will not be able to see the specific help for this '
              'command, though. Lastly, this command is disabled in DMs.',
        hidden=True, allow_direct=False, category='demo'))

    new_commands.append(Command(
        'notify', subcommands=[
            SubCommand(
                Arg('text', argtype=ArgTypes.MERGED),
                doc='Notify the owners with some text!')],
        other='This command uses a custom function. It is called with the '
              'same arguments as get_response. The command will show up to '
              'all users in the help command, but can only be used by server '
              'owners, as it is disallowed in direct messages.',
        elevated_level=2, allow_direct=False, function=custom_notify,
        category='demo'))

    new_commands.append(Command(
        'wait', other='Use this command to demo the wait_for functionality', category='demo'))

    return new_commands


async def get_response(bot, context):
    """Gets a response given the parsed input.

    context attributes:
    bot -- A reference to the bot itself.
    message -- The discord.message object obtained from on_message.
    base -- The base command name that immediately follows the invoker.
    subcommand -- The subcommand that matched the parameters.
    index -- The index of the found subcommand.
    options -- A dictionary representing the options and potential positional
        arguments that are attached to them.
    arguments -- A list of strings that follow the syntax of the blueprint
        index for arguments following the options.
    keywords -- Another list of strings that holds all option keywords. These
        can be used to prevent database conflicts with user commands.
    cleaned_content -- Simply the message content without the invoker.
    """

    # This is what the bot will say when it returns from this function.
    # The response object can be manipulated in many ways. The attributes of
    #   the response will be passed into the send function.
    response = Response()
    response.content = ''  # Default

    # Set to True if you want your message read with /tts (not recommended).
    response.tts = False  # Default

    # The message type dictates how the bot handles your returned message.
    #
    # NORMAL - Normal. The issuing command can be edited.
    # PERMANENT - Message is not added to the edit dictionary.
    # REPLACE - Deletes the issuing command after 'extra' seconds. Defaults
    #   to 0 seconds if 'extra' is not given.
    # ACTIVE - The message reference is passed back to the function defined
    #   with 'extra_function'. If 'extra_function' is not defined, it will call
    #   plugin.handle_active_message.
    # INTERACTIVE - Assembles reaction buttons given by extra['buttons'] and
    #   calls 'extra_function' whenever one is pressed.
    # WAIT - Wait for event. Calls 'extra_function' with the result, or None
    #   if the wait timed out.
    #
    # Only the NORMAL message type can be edited.
    response.message_type = MessageTypes.NORMAL  # Default

    # The extra variable is used for some message types.
    response.extra = None  # Default

    # Initially, check to make sure that you've matched the proper command.
    # If there is only one command specified, this may not be necessary.
    index, options, arguments = context.index, context.options, context.arguments
    if context.base == 'mycommand':

        # Then, the subcommand index will tell you which command syntax was
        #   satisfied. The order is the same as was specified initially.
        if index == 0:  # myoption
            response.content = "You called the first subcommand!"
            # Do other stuff...

        elif index == 1:  # custom/attached
            # To see if an optional option was included in the command, use:
            if 'custom' in options:
                response.content += "You included the \"custom\" flag!\n"
                # Do stuff relevant to this flag here...

            # To get the parameter attached to an option, simply access it from
            #   the options dictionary.
            if 'attached' in options:
                response.content += "The attached parmeter: {}\n".format(options['attached'])

            # In case somebody was looking for the help...
            if len(options) == 0:
                invoker = utilities.get_invoker(bot, guild=context.guild)
                response.content += ("You didn't use either flag...\n"
                                     "For help, try `{}help mycommand`".format(invoker))

        elif index == 2:  # trailing arguments
            # If arguments are specified as trailing, they will be in a list.
            response.content += "The list of trailing arguments: {}".format(arguments)

        elif index == 3:  # grouped arguments
            # All arguments are grouped together as the first element
            response.message_type = MessageTypes.PERMANENT
            response.content = ("You can't edit your command here.\n"
                                "Single grouped argument: {}").format(arguments[0])

        elif index == 4:  # complex
            # This mixes elements of both examples seen above.
            response.content = ("The argument attached to the complex "
                                "option: {}").format(options['complex'])
            if 'other' in options:
                response.content += "\nThe other option has attached: {}".format(options['other'])
            response.content += "\nLastly, the trailing arguments: {}".format(arguments)

        elif index == 5:  # (Very slow) marquee
            # This demonstrates the active message type.
            # Check active_marquee to see how it works.
            response.message_type = MessageTypes.ACTIVE
            response.extra_function = active_marquee  # The function to call
            response.extra = arguments[0]  # The text to use
            response.content = "Setting up marquee..."  # This will be shown first

    # Here's another command base.
    elif context.base == 'myothercommand':

        if index == 0:  # keyword checker
            text = arguments[0]
            if not text:
                response.content = "You didn't say anything...\n"
            else:
                response.content = "This is your input: {}\n".format(text)
                if text in context.keywords:
                    response.content += "Your input was in the list of keywords!\n"
                else:
                    response.content += ("Your input was not in the list of keywords. "
                                         "They are: {}\n").format(context.keywords)
            response.message_type = MessageTypes.PERMANENT
            response.delete_after = 15
            response.content += "This message will self destruct in 15 seconds."

        else:  # impossible command???
            raise CBException("This is a bug! You should never see this message.")

    elif context.base == 'wait':
        response.message_type = MessageTypes.WAIT
        # The extra attribute should consist of a dictionary containing the
        #   event and any other kwargs. Most notably, you will likely want to
        #   define the check used in wait_for.
        response.extra_function = custom_interaction
        response.extra = {
            'event': 'message',
            'kwargs': {
                'timeout': 30,  # Default 300
                'check': lambda m: m.author == context.author,
            }
        }
        response.content = "Say something, {}.".format(context.author)

    return response


async def custom_notify(bot, context):
    """This is only called with the notify command.

    This function is called with the same arguments as get_response.
    """
    await utilities.notify_owners(
            bot, '{0.author} from {0.guild}: {0.arguments[0]}'.format(context))
    return Response(content="Notified the owners with your message!")


async def custom_interaction(bot, context, response, result):
    """This is the function defined for the wait command.

    'context' and 'response' are familiar parameters used before. The only
    difference is that the response message can now be obtained via
    response.message
    """
    if result is None:  # Timed out
        edit = 'You took too long to respond...'
    elif result.content:
        edit = 'You replied with "{}"'.format(result.content[:100])
    else:
        edit = 'You did not reply with any content text!'
    await response.message.edit(content=edit)


async def active_marquee(bot, context, response):
    """Handle the marquee active message."""

    # Setup text with whitespace padding
    total_length = 40 + len(response.extra)
    text = '{0: ^{1}}'.format(response.extra, total_length)
    for it in range(3):
        for move in range(total_length - 20):
            moving_text = '`|{:.20}|`'.format(text[move:])
            await asyncio.sleep(1)  # Evenly distribute ratelimit
            await response.message.edit(content=moving_text)

    # When the marquee is done, just display the text
    await asyncio.sleep(1)
    await response.message.edit(content=response.extra)


@plugins.permissions_spawner
def setup_permissions(bot):
    """Use this decorator to return a dictionary of required permissions."""
    return {
        'read_messages': "This is a dummy additional permission.",
        'change_nickname': "This allows the bot to change its own nickname."
    }


# If necessary, events can be listened for using the plugins.listen_for decorator.
#   These events include everything discord.py provides (see the event reference in the docs).
#
#   Additionally, the bot provides a few extra events:
#   - bot_on_command (context):
#       A command is about to be called (the context has been built)
#   - bot_on_response (response, context):
#       A command has been executed, and a response was created
#   - bot_on_user_ratelimit (author):
#       The author has issued too many commands (global command ratelimit exceeded)
#   - bot_on_exception (error, message):
#       A BotException was caught
#   - bot_on_discord_exception (error, message):
#       A discord.py exception caught (likely messages were too long)
#   - bot_on_uncaught_exception (error, message):
#       An uncaught exception was raised (internal error)
#   - bot_on_ready_boot:
#       The bot has started up for the first time (or the plugin was reloaded)
#
#   Be sure to include the bot argument first for these event functions!


@plugins.listen_for('on_message_edit')
async def show_edits(bot, before, after):
    if (before.author != bot.user and
            configurations.get(bot, __name__, key='show_edited_messages')):
        logger.info("Somebody edited their message from '{0}' to '{1}'.".format(
            before.content, after.content))


@plugins.listen_for('bot_on_ready_boot')
async def demo_on_boot(bot):
    """This is called only once every time the bot is started (or reloaded)."""
    logger.info("demo_on_boot was called from dummy.py!")
