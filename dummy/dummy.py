################################################################################
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
#   as well as commands and help (manual entry).
#
# There is also a demonstration on how get_response() should be used, and how
#   to read a parsed result.
#
# Lastly, there is a small amount of demo code to show off that plugins can
#   define standard events from discord.Client, and they will be called
#   appropriately.
#
################################################################################

import discord
import asyncio

from jshbot.exceptions import ErrorTypes, BotException

__version__ = '0.1.0'
EXCEPTION = 'Dummy'
uses_configuration = False

def get_commands():

    commands = {}
    shortcuts = {}
    manual = {}

    # Commands
    # (For more help on these symbols, refer to command_reference.txt)
    commands['mycommand'] = ([
        'myoption',
        '?custom ?attached:',
        'trailing ::+',
        'grouped ^',
        'complex: ?other: :#'],[
        ('myoption', 'option', 'o'),
        ('trailing', 'trail'),
        ('grouped', 'blocked')])
    commands['myothercommand'] = (['&'], [])
        
    # Shortcuts
    shortcuts['complex'] = ('mycommand -complex {} -other {} {} {}', ':::#')
    shortcuts['group'] = ('mycommand -grouped {}', '^')
    shortcuts['myshortcut'] = ('myothercommand', '')

    # Manual
    manual['mycommand'] = {
        'description': 'Your command description here.',
        'usage': [
            ('-myoption', 'This is a simple command with a single required '
                'option.'),
            ('(-custom) (-attached <attached argument>)', 'This has two '
                'different optional options, one without a flag, and the other '
                'requring an attached argument if it the flag is specified.'),
            ('-trailing <arg 1> <arg 2> <arg 3> (arg 4) (arg 5) (...)', 'This '
                'command has a lot of trailing arguments as a requirement.'),
            ('-grouped <grouped arguments>', 'This will group all given '
                'arguments as a single string.'),
            ('-complex <attached> (-other <also required>) <arg 1> (arg 2) '
                '(arg 3) (...)', 'The complex flag has a required associated '
                'positional argument, and the other flag has one too if it is '
                'specified. Additionally, there will be a requirement of at '
                'least 1 trailing argument.')],
        'shortcuts': [
            ('complex <attached> <other> <arg1> (arg2) (...)',
            '-complex <attached> -other <other> <arg1> (arg2) (...)'),
            ('group <argument>', '-group <argument>')],
        'other': 'All of this text is actually not required! You can choose '
            'to not include any of these sections, and they will simply not '
            'show up. Obviously, it\'s best if at least the description and '
            'usage portions were provided.'}
    manual['myothercommand'] = {
        'description': 'Just another basic command.'}

    # Returned as a touple - the core will handle the rest
    return (commands, shortcuts, manual)

async def get_response(bot, message, parsed_command, direct):

    response = ''

    # Set to True if you want your message read with /tts (not recommended)
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
    #       handle_active_message(message_reference, extra). See the comments
    #       for handle_active_message for more information.
    #       
    message_type = 0

    # The extra variable is used for the second and third message types.
    extra = None

    # This is what the parser returns that you can use.
    #   base - The base command that immediately follows the command invoker.
    #   plan_index - The index of the plan as specified by the commands
    #       dictionary in get_commands.
    #   options - The dictionary of the given options, and their arguments if
    #       they have any.
    #   arguments - The list or string of arguments found after the command.
    base, plan_index, options, arguments = parsed_command

    # Initially, check to make sure that you've matched the proper command.
    # If there is only one command specified, this may not be necessary.
    if base == 'mycommand':

        # Then, the plan_index will tell you which command syntax was
        #   satisfied. The order is the same as was specified initially.
        if plan_index == 0: # myoption
            response = "You called the first command!"
            if direct:
                response += "This is called in a direct message!"
            # Do other stuff...

        elif plan_index == 1: # custom/attached

            # To see if an optional option was included in the command, use:
            if 'custom' in options:
                response += "You included the \"custom\" flag!\n"
                # Do stuff relevant to this flag...

            # To get the argument attached to an option, simply access it from
            #   the options dictionary.
            if 'attached' in options:
                response += ("The attached argument: " + options['attached'] + 
                    '\n') # Can't believe this is on a line by itself.

            # In case somebody was looking for the help...
            if len(options) == 0:
                response += "You didn't use either flag...\n"
                response += "For help, try `{}help mycommand`".format(
                        bot.configurations['core']['command_invokers'][0])

        elif plan_index == 2: # trailing arguments

            # If arguments are specified as trailing, they will be in a list.
            response += "The list of trailing arguments: " + str(arguments)

        elif plan_index == 3: # grouped arguments

            # If arguments are specified as grouped, it will be a single string.
            response += "Here is the grouped argument: " + arguments

        elif plan_index == 4: # complex

            # This mixes elements of both examples seen above.
            response = ("The argument attached to the complex option: " +
                    options['complex'] + '\n')
            if 'other' in options:
                response += "The other option has: " + options['other'] + '\n'
            response += "Lastly, the trailing arguments: " + str(arguments)

    # Here's another command base.
    elif base == 'myothercommand':
        
        # We only have one command, checking for plan_index isn't necessary.
        if arguments:
            response = ("You called the other command and gave this "
                    "argument: " + arguments)
        else:
            response = "You called the other command with no arguments."

    return (response, tts, message_type, extra)

# If necessary, discord.Client events can be defined here, and they will be
#   called appropriately. Be sure to include the bot argument first!

async def on_ready(bot):
    print("on_ready was called from dummy.py!")

async def on_message_edit(bot, before, after):
    print("Somebody edited their message from '{}' to '{}'.".format(
        before.content, after.content))

