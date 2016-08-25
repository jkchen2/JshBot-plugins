import asyncio

import xml.etree.ElementTree as ElementTree

from jshbot import utilities, configurations, data
from jshbot.commands import Command, SubCommands
from jshbot.exceptions import BotException

__version__ = '0.1.1'
EXCEPTION = 'Class Checker'
course_url_template = (
    "http://courses.illinois.edu/cisapp/explorer/schedule/{year}/{semester}/"
    "{{department}}{{course_number}}{{crn}}.xml")


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'crn', SubCommands(
            ('pending', 'pending', 'Lists classes you are waiting on.'),
            ('watch :::', 'watch <department code> <course number> <CRN>',
             'Monitors a CRN and lets you know if it opens up.'),
            (':::', '<department code> <course number> <CRN>', 'Gets '
             'information about the given class.')),
        description='UIUC class tool', group='tools'))

    return new_commands


def _get_watching_classes(bot, author):
    """Returns a list of classes the author """
    class_dictionary = data.get(bot, __name__, 'classes', default={})
    watching = []
    for class_crn, class_values in class_dictionary.items():
        if author.id in class_values['notify_list']:
            watching.append(
                class_values['class_title'] + ' ({})'.format(class_crn))
    return watching


def list_watching_classes(bot, author):
    """Shows the classes that the author is watching as a string."""
    watching = _get_watching_classes(bot, author)
    if watching:
        return "You are watching:\n{}".format('\n'.join(watching))
    else:
        return "You are not watching any classes right now."


async def watch_class(bot, author, *args):
    """Adds the given user and class to the notification loop."""
    class_data = await get_class_data(bot, *args)
    class_title = get_class_title(class_data)
    if 'Open' in class_data.find('enrollmentStatus').text:
        raise BotException(EXCEPTION, "CRN is currently open.")
    class_dictionary = data.get(
        bot, __name__, 'classes', create=True, default={})
    crn = class_data.get('id')
    if crn in class_dictionary:  # Class already exists
        if author.id in class_dictionary[crn]['notify_list']:
            class_dictionary[crn]['notify_list'].remove(author.id)
            return "Removed class from the watch list."
        else:
            if len(_get_watching_classes(bot, author)) >= configurations.get(
                    bot, __name__, 'class_limit'):
                raise BotException(
                    EXCEPTION, "You are watching too many classes.")
            class_dictionary[crn]['notify_list'].append(author.id)
    else:  # Class does not exist
        if len(_get_watching_classes(bot, author)) >= configurations.get(
                bot, __name__, 'class_limit'):
            raise BotException(
                EXCEPTION, "You are watching too many classes.")
        class_dictionary[crn] = {
            "notify_list": [author.id],
            "class_title": class_title,
            "identity": args
        }
    return "Class '{}' added to the watch list.".format(class_title)


async def get_class_data(bot, department, course_number=None, crn=None):
    department = department.upper()
    if course_number:
        try:
            course_number = str(int(course_number))
        except:
            raise BotException(EXCEPTION, "Course number is not a number.")
        if crn:
            try:
                crn = str(int(crn))
            except:
                raise BotException(EXCEPTION, "CRN is not a number.")
    complete_url = course_url_template.format(
        department=department,
        course_number='/'+course_number if course_number else '',
        crn='/'+crn if crn else '')

    status, text = await utilities.get_url(bot, complete_url)
    if status == 404:  # TODO: Suggest what wasn't found
        raise BotException(EXCEPTION, "Class not found.")
    elif status != 200:
        raise BotException(EXCEPTION, "Something bad happened.", status)
    try:
        return ElementTree.fromstring(text)
    except Exception as e:
        raise BotException(EXCEPTION, "The XML could not be parsed.", e)


def get_class_title(class_data):
    parent_data = class_data.find('parents')
    return '{0} {1}: {2}'.format(
        parent_data.find('subject').get('id'),
        parent_data.find('course').get('id'),
        parent_data.find('course').text)


async def get_class_info(bot, *args):
    class_data = await get_class_data(bot, *args)
    class_title = get_class_title(class_data)
    meeting_data = class_data.find('meetings').find('meeting')
    instructors = meeting_data.find('instructors').findall('instructor')
    instructor_names = ', '.join(
        '"{}"'.format(instructor.text) for instructor in instructors)
    notes = class_data.find('sectionNotes')
    if notes is None:
        notes = class_data.find('sectionText')
    if notes is None:
        notes = "None provided."
    else:
        notes = notes.text
    return (
        '***`{0}`***\n**Section:** {1}\n**Type:** {2}\n**Meets:** {5} {3} to '
        '{4} in {6} {7}\n**Instructors:** {8}\n**Status:** {9}\n**Notes:** '
        '{10}'.format(
            class_title, class_data.find('sectionNumber').text,
            meeting_data.find('type').text, meeting_data.find('start').text,
            meeting_data.find('end').text,
            meeting_data.find('daysOfTheWeek').text,
            meeting_data.find('roomNumber').text,
            meeting_data.find('buildingName').text,
            instructor_names, class_data.find('enrollmentStatus').text, notes))


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if blueprint_index == 0:  # pending
        response = list_watching_classes(bot, message.author)
    elif blueprint_index == 1:  # watch
        response = await watch_class(bot, message.author, *arguments)
    elif blueprint_index == 2:  # info
        response = await get_class_info(bot, *arguments)

    return (response, tts, message_type, extra)


async def notify_loop(bot):
    """Notifies user when a class opens up every few minutes."""
    while True:
        class_dictionary = data.get(bot, __name__, 'classes', default={})

        crns_to_remove = []
        for class_crn, class_values in class_dictionary.items():
            try:
                class_data = await get_class_data(
                    bot, *class_values['identity'])
            except Exception as e:
                logging.error("Failed to retrieve the class: " + str(e))
                await asyncio.sleep(30)
                continue
            status = class_data.find('enrollmentStatus').text
            if 'Open' in status:
                crns_to_remove.append(class_crn)
                if 'Restricted' in status:  # Open, but restricted
                    restriction = class_data.find('sectionNotes')
                    if restriction is None:
                        restriction = class_data.find('sectionText')
                    if restriction is None:
                        restriction = "None provided."
                    else:
                        restriction = restriction.text
                    notification = " (Restriction: {})".format(restriction)
                else:  # Open
                    notification = " (No listed restrictions)"
                for user_id in class_values['notify_list']:
                    user = data.get_member(bot, user_id)
                    await bot.send_message(
                        user, "{0[class_title]} ({1}) is now open{2}".format(
                            class_values, class_crn, notification))
                    for it in range(5):
                        await bot.send_message(user, ":warning:")
                        await asyncio.sleep(1)
            await asyncio.sleep(1)

        for crn in crns_to_remove:
            del class_dictionary[crn]

        await asyncio.sleep(5*60)


async def on_ready(bot):
    if bot.fresh_boot:
        global course_url_template
        course_url_template = course_url_template.format(
            **configurations.get(bot, __name__))
        await notify_loop(bot)
