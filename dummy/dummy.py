###############################################################################
#
# NOTE: If you don't need this file, feel free to delete it! It only serves to
#   be a demo of how plugins should be made.
#
#
# Hey there!
#
# This is dummy.py, or the whirlwind tour of what JshBot 0.3.x is about, and
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

from jshbot import utilities, configurations
from jshbot.commands import Command, SubCommands, Shortcuts
from jshbot.exceptions import BotException

__version__ = '0.1.0'
EXCEPTION = 'Dummy'
uses_configuration = False


def get_commands():
    """Returns a list of commands associated with this plugin."""
    commands = []

    commands.append(Command(
        'mycommand', SubCommands(
            ('myoption', 'myoption', 'This is a simple command with a single '
             'required option.'),
            ('?custom ?attached:', '(custom) (attached <"attached argument">)',
             'This has two different optional options, one without an '
             'attached argument, and the other requiring an attached argument '
             'if the option \'attached\' is specified.'),
            ('trailing ::+', 'trailing <"arg 1"> <"arg 2"> <"arg 3"> '
             '("arg 4") ("arg 5") (...)', 'This command requires lot of '
             'trailing arguments.'),
            ('grouped ^', 'grouped <grouped arguments>', 'This will group all '
             'given arguments as a single string.'),
            ('complex: ?other: :#', 'complex <"attached"> (other <"also '
             'required">) <"arg 1"> ("arg 2") ("arg 3") (...)', 'The complex '
             'option has a required positional argument, and the \'other\' '
             'option has one too if it is specified. Additionally, there will '
             'be a requirement of at least 1 trailing arguent.'),
            ('marquee ^', 'marquee <text>', 'Create a marquee that loops '
             'three times.')),
        shortcuts=Shortcuts(
            ('complex', 'complex {} other {} {} {}', ':::#',
             '<"attached"> other <"other"> <"arg 1"> ("arg 2") (...)',
             '<"attached"> <"other"> <"arg 1"> ("arg 2") (...)'),
            ('marquee', 'marquee {}', '^',
             'marquee <text>', '<text>')),
        description='Your command description here.',
        other='This text is optional - it just shows up after everything '
              'else. Quick note, all of the commands here can only be used by '
              'bot moderators or above, as indicated by elevated_level. A '
              'level of 2 would mean only server owners or above can use the '
              'command, and a level of 3 would restrict the command to only '
              'the bot owners.',
        elevated_level=1, group='demo'))

    commands.append(Command(
        'myothercommand', SubCommands(
            ('&', '<text>', ''),
            ('order matters', 'order matters', 'It is impossible to access '
             'this command because the first subcommand will always be '
             'satisfied first. Order of subcommands matters!'),
            ('sample foo bar', 'sample foo bar', 'Also impossible to access. '
             'This subcommand just adds some keywords to the command.')),
        description='Only bot owners can see this text!',
        other='Note that no shortcuts were defined. They, too, are optional. '
              'Also, this command is hidden, which means that only the bot '
              'owners can see this command listed from the help command. '
              'However, unless the command is configured with an elevated '
              'permissions level, any user can still execute the command. '
              'Users still will not be able to see the specific help for this '
              'command, though. Lastly, this command is disabled in DMs.',
        hidden=True, allow_direct=False, group='demo'))

    commands.append(Command(
        'notify', SubCommands(('^', '<text>', 'Notify the owners!')),
        other='This command uses a custom function. It is called with the '
              'same arguments as get_response. The command will show up to '
              'all users in the help command, but can only be used by server '
              'owners, as it is disallowed in direct messages.',
        elevated_level=2, allow_direct=False, function=custom_notify,
        group='demo'))

    commands.append(Command(
        'interact', SubCommands(('', '', '')),
        other='Use this command to demo the wait_for_message functionality.',
        group='demo'))

    return commands


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    """Gets a response given the parsed input.

    Arguments:
    bot -- A reference to the bot itself.
    message -- The discord.message object obtained from on_message.
    base -- The base command name that immediately follows the invoker.
    blueprint_index -- The index of the subcommand of the given base command.
    options -- A dictionary representing the options and potential positional
        arguments that are attached to them.
    arguments -- A list of strings that follow the syntax of the blueprint
        index for arguments following the options.
    keywords -- Another list of strings that holds all option keywords. These
        can be used to prevent database conflicts with user commands.
    cleaned_content -- Simply the message content without the invoker.
    """

    # This is what the bot will say when it returns from this function.
    response = ''

    # Set to True if you want your message read with /tts (not recommended).
    tts = False

    # The message type dictates how the bot handles your returned message.
    #
    #   0 - Regular message. This message can be edited, and the bot will
    #       attempt to get a new response and replace the given message.
    #       This is the default recommended behavior.
    #
    #   1 - Permanent message. This message cannot be edited by a user changing
    #       their command. All types past this point are also non-editable.
    #
    #   2 - Terminal message. This message will self destruct in a defined
    #       number of seconds based on the 'extra' variable. For example, if
    #       message_type is set to 2, and extra is set to 10, the message will
    #       be displayed for 10 seconds, then be deleted. If the 'extra'
    #       variable is not set, it will default to 10 seconds.
    #
    #   3 - Active message. This message will be passed back to the plugin for
    #       extra processing and editing. The function it will call is
    #       handle_active_message(). See the comments for
    #       handle_active_message() for more information.
    #
    message_type = 0

    # The extra variable is used for the second and third message types.
    extra = None

    # Initially, check to make sure that you've matched the proper command.
    # If there is only one command specified, this may not be necessary.
    if base == 'mycommand':

        # Then, the blueprint_index will tell you which command syntax was
        #   satisfied. The order is the same as was specified initially.
        if blueprint_index == 0:  # myoption
            response = "You called the first subcommand!"
            # Do other stuff...

        elif blueprint_index == 1:  # custom/attached
            # To see if an optional option was included in the command, use:
            if 'custom' in options:
                response += "You included the \"custom\" flag!\n"
                # Do stuff relevant to this flag here...

            # To get the argument attached to an option, simply access it from
            #   the options dictionary.
            if 'attached' in options:
                response += "The attached argument: {}\n".format(
                    options['attached'])

            # In case somebody was looking for the help...
            if len(options) == 0:
                invoker = utilities.get_invoker(bot, server=message.server)
                response += "You didn't use either flag...\n"
                response += "For help, try `{}help mycommand`".format(invoker)

        elif blueprint_index == 2:  # trailing arguments
            # If arguments are specified as trailing, they will be in a list.
            response += "The list of trailing arguments: {}".format(arguments)

        elif blueprint_index == 3:  # grouped arguments
            # All arguments are grouped together as the first element
            message_type = 1
            response = ("You can't edit your command here.\n"
                        "Single grouped argument: {}").format(arguments[0])

        elif blueprint_index == 4:  # complex
            # This mixes elements of both examples seen above.
            response = ("The argument attached to the complex "
                        "option: {}\n").format(options['complex'])
            if 'other' in options:
                response += "The other option has attached: {}\n".format(
                    options['other'])
            response += "Lastly, the trailing arguments: {}".format(arguments)

        elif blueprint_index == 5:  # (Very slow) marquee
            # This demonstrates the active message type. Check
            #   handle_active_message to see how it works.
            text = arguments[0]
            if not text or len(text) > 100 or '\n' in text:
                response = ("Must have text 1-100 characters long, and must "
                            "not have any new lines.")
            else:
                message_type = 3  # active
                extra = ('marquee', text)
                response = "Setting up marquee..."  # This will be shown first

    # Here's another command base.
    elif base == 'myothercommand':

        if blueprint_index == 0:  # keyword checker
            text = arguments[0]
            if not text:
                response = "You didn't say anything...\n"
            else:
                response = "This is your input: {}\n".format(text)
                if text in keywords:
                    response += "Your input was in the list of keywords!\n"
                else:
                    response += ("Your input was not in the list of keywords. "
                                 "They are: {}\n").format(keywords)

            message_type = 2  # Self-destruct
            extra = 15  # 15 seconds
            response += "This message will self destruct in 15 seconds."

        else:  # impossible command???
            raise BotException(
                EXCEPTION, "This is a bug! You should never see this message.")

    elif base == 'interact':
        message_type = 6  # Use wait_for_message
        # The extra argument should consist of a 2 element tuple with the first
        #   element being the callback function, and the second being the
        #   keyword arguments passed into wait_for_message
        extra = (
            custom_interaction,
            {'timeout': 10, 'author': message.author},
            None
        )
        response = "Say something, {}".format(message.author)

    return (response, tts, message_type, extra)


async def custom_notify(bot, message, *args):
    """This is only called with the notify command.

    This function is called with the same arguments as get_response.
    """
    response, tts, message_type, extra = ('', False, 0, None)

    notify_message = '{0} from server {1} is sending you: {2}'.format(
        message.author.display_name, message.server.name, args[3][0])
    await bot.notify_owners(notify_message)
    response = "Notified the owners with your message!"

    return (response, tts, message_type, extra)


async def custom_interaction(bot, message_reference, reply, extra):
    """This is called when the message_type is 6.

    message_reference is the original message that can be edited.
    The reply argument is the return value of bot.wait_for_message.
    If the reply argument is None, the wait timed out.
    """
    if reply is None:
        edit = 'You took too long to respond...'
    elif reply.content:
        edit = 'You replied with "{}"'.format(reply.content[:100])
    else:
        edit = 'You did not reply with any text!'
    await bot.edit_message(message_reference, edit)


async def handle_active_message(bot, message_reference, extra):
    """This is called if the given message was marked as active.

    (message_type of 3).
    """
    if extra[0] == 'marquee':  # Handle the marquee active message

        # Set text expanded with whitespace
        total_length = 40 + len(extra[1])
        text = '{0: ^{1}}'.format(extra[1], total_length)

        # Loop through the text three times
        for it in range(3):
            for move in range(total_length - 20):
                moving_text = '`|{:.20}|`'.format(text[move:])
                await asyncio.sleep(1)  # Don't get rate limited!
                await bot.edit_message(message_reference, moving_text)

        # When the marquee is done, just display the text
        await asyncio.sleep(1)
        await bot.edit_message(message_reference, extra[1])


# If necessary, discord.Client events can be defined here, and they will be
#   called appropriately. Be sure to include the bot argument first!

async def on_ready(bot):
    print("on_ready was called from dummy.py!")


async def on_message_edit(bot, before, after):
    if (before.author != bot.user and
            configurations.get(bot, __name__, key='show_edited_messages')):
        print("Somebody edited their message from '{0}' to '{1}'.".format(
            before.content, after.content))


async def bot_on_ready_boot(bot):
    """This is called only once every time the bot is started (or reloaded)."""
    # Use this to set up additonal permissions for the plugin
    permissions = {
        'read_messages': "This is a dummy additional permission.",
        'change_nickname': "This allows the bot to change its own nickname."
    }
    utilities.add_bot_permissions(bot, __name__, **permissions)
