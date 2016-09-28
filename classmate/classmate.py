import logging
import asyncio

import xml.etree.ElementTree as ElementTree

from jshbot import utilities, configurations, data
from jshbot.commands import Command, SubCommands
from jshbot.exceptions import BotException

__version__ = '0.1.2'
EXCEPTION = 'Course Checker'
course_url_template = (
    "http://courses.illinois.edu/cisapp/explorer/schedule/{year}/{semester}/"
    "{{department}}{{course_number}}{{crn}}.xml{{detail}}")


def get_commands():
    new_commands = []

    new_commands.append(Command(
        'crn', SubCommands(
            ('pending', 'pending', 'Lists courses you are waiting on.'),
            ('watch :::', 'watch <department code> <course number> <CRN>',
             'Monitors a CRN and lets you know if it opens up.'),
            ('course ::', 'course <department code> <course number>',
             'Shows details on the given course.'),
            ('+', '<department code> (course number) (CRN)', 'Gets '
             'information about the given department, course number, and/or '
             'CRN if included.')),
        description='UIUC course tool.', group='tools'))

    return new_commands


def _get_watching_courses(bot, author):
    """Returns a list of courses the author """
    course_dictionary = data.get(bot, __name__, 'courses', default={})
    watching = []
    for course_crn, course_values in course_dictionary.items():
        if author.id in course_values['notify_list']:
            watching.append(
                course_values['course_title'] + ' ({})'.format(course_crn))
    return watching


def list_watching_courses(bot, author):
    """Shows the courses that the author is watching as a string."""
    watching = _get_watching_courses(bot, author)
    if watching:
        return "You are watching:\n{}".format('\n'.join(watching))
    else:
        return "You are not watching any courses right now."


async def watch_course(bot, author, *args):
    """Adds the given user and course to the notification loop."""
    course_data = await _get_data(bot, *args)
    course_title = _get_course_title(course_data)
    if 'Open' in course_data.find('enrollmentStatus').text:
        raise BotException(EXCEPTION, "CRN is currently open.")
    course_dictionary = data.get(
        bot, __name__, 'courses', create=True, default={})
    crn = course_data.get('id')
    if crn in course_dictionary:  # Course already exists
        if author.id in course_dictionary[crn]['notify_list']:
            course_dictionary[crn]['notify_list'].remove(author.id)
            if not course_dictionary[crn]['notify_list']:
                del course_dictionary[crn]
            return "Removed course from the watch list."
        else:
            if len(_get_watching_courses(bot, author)) >= configurations.get(
                    bot, __name__, 'course_limit'):
                raise BotException(
                    EXCEPTION, "You are watching too many courses.")
            course_dictionary[crn]['notify_list'].append(author.id)
    else:  # Course does not exist
        if len(_get_watching_courses(bot, author)) >= configurations.get(
                bot, __name__, 'course_limit'):
            raise BotException(
                EXCEPTION, "You are watching too many courses.")
        course_dictionary[crn] = {
            "notify_list": [author.id],
            "course_title": course_title,
            "identity": args
        }
    return "Course '{}' added to the watch list.".format(course_title)


async def list_search(bot, *args):
    """Searches for course information given the number of arguments."""
    if len(args) > 3:
        raise BotException(EXCEPTION, "Too many arguments.")
    elif len(args) == 3:
        return await get_crn_info(bot, *args)

    course_data = await _get_data(bot, *args)
    code, label = course_data.get('id'), course_data.find('label').text
    response_list = ['***`{0}: {1}`***'.format(code, label)]
    if len(args) == 2:  # Course number (list CRNs)
        section_list = course_data.find(
            'detailedSections').findall('detailedSection')
        if not section_list:
            raise BotException(EXCEPTION, "Course has no sections.")
        for section_data in section_list:
            section_details = _get_section_details(section_data)
            response_list.append(
                '**{crn}:** {section} ({type}), {start}-{end} {days}'.format(
                    **section_details))

    else:  # Department (list courses)
        course_list = course_data.find('courses').findall('course')
        if not course_list:
            raise BotException(EXCEPTION, "Department has no courses.")
        for course in course_list:
            response_list.append('**{0}:** {1}'.format(
                course.get('id'), course.text))

    return '\r\n'.join(response_list)


async def _get_data(bot, department, course_number='', crn=''):
    department = department.upper()
    if course_number:
        detail = '?mode=detail'
        try:
            course_number = str(int(course_number))
        except:
            raise BotException(EXCEPTION, "Course number is not a number.")
        if crn:
            try:
                crn = str(int(crn))
            except:
                raise BotException(EXCEPTION, "CRN is not a number.")
    else:
        detail = ''
    complete_url = course_url_template.format(
        department=department,
        course_number='/'+course_number if course_number else '',
        crn='/'+crn if crn else '', detail=detail)

    status, text = await utilities.get_url(bot, complete_url)
    if status == 404:  # TODO: Suggest what wasn't found
        raise BotException(EXCEPTION, "Course not found.")
    elif status != 200:
        raise BotException(EXCEPTION, "Something bad happened.", status)
    try:
        return ElementTree.fromstring(text)
    except Exception as e:
        raise BotException(EXCEPTION, "The XML could not be parsed.", e)


async def get_course_description(bot, *args):
    """Gets course description from the given arguments."""
    course_data = await _get_data(bot, *args)
    title = _get_course_title(course_data, course_list=True)
    description = {}
    attributes = [
        ('description', 'description'),
        ('sectionDegreeAttributes', 'type'),
        ('courseSectionInformation', 'restrictions'),
        ('classScheduleInformation', 'notes')]
    for attribute, key in attributes:
        current = course_data.find(attribute)
        description[key] = 'n/a' if current is None else current.text
    return (
        '***`{0}`***\n**Description:** {description}\n**Type:** {type}\n'
        '**Restrictions:** {restrictions}\n**Notes:** {notes}').format(
            title, **description)


def _get_course_title(course_data, course_list=False):
    if course_list:
        return '{0}: {1}'.format(
            course_data.get('id'), course_data.find('label').text)
    else:
        parent_data = course_data.find('parents')
        return '{0} {1}: {2}'.format(
            parent_data.find('subject').get('id'),
            parent_data.find('course').get('id'),
            parent_data.find('course').text)


def _get_section_details(section_data):
    course_details = {}
    meeting_data = section_data.find('meetings').find('meeting')
    attributes = [
        ('type', 'type'), ('start', 'start'), ('end', 'end'),
        ('daysOfTheWeek', 'days'), ('roomNumber', 'room'),
        ('buildingName', 'building')]
    for attribute, key in attributes:
        current = meeting_data.find(attribute)
        course_details[key] = 'n/a' if current is None else current.text
    course_details['section'] = (
        section_data.find('sectionNumber').text
        if section_data.find('sectionNumber') is not None else 'n/a')
    course_details['status'] = (
        section_data.find('enrollmentStatus').text
        if section_data.find('enrollmentStatus') is not None else 'n/a')
    instructors = meeting_data.find('instructors').findall('instructor')
    course_details['instructors'] = ', '.join(
        '"{}"'.format(instructor.text) for instructor in instructors)
    notes = section_data.find('sectionNotes')
    if notes is None:
        notes = section_data.find('sectionText')
    if notes is None:
        notes = "None provided."
    elif section_data.find('sectionText'):  # Additional notes
        notes += ' ({})'.format(section_data.find('sectionText').text)
    else:
        notes = notes.text
    course_details['notes'] = notes
    course_details['crn'] = section_data.get('id')

    return course_details


async def get_crn_info(bot, *args):
    """Gets course information given a CRN."""
    course_data = await _get_data(bot, *args)
    course_title = _get_course_title(course_data)
    section_details = _get_section_details(course_data)

    return (
        '***`{0}`***\n**Section:** {section}\n**Type:** {type}\n**Meets:** '
        '{days} {start} to {end} in {building} {room}\n**Instructors:** '
        '{instructors}\n**Status:** {status}\n**Notes:** {notes}').format(
            course_title, **section_details)


async def get_response(
        bot, message, base, blueprint_index, options, arguments,
        keywords, cleaned_content):
    response, tts, message_type, extra = ('', False, 0, None)

    if blueprint_index == 0:  # pending
        response = list_watching_courses(bot, message.author)
    elif blueprint_index == 1:  # watch
        response = await watch_course(bot, message.author, *arguments)
    elif blueprint_index == 2:  # course
        response = await get_course_description(bot, *arguments)
    elif blueprint_index == 3:  # info
        response = await list_search(bot, *arguments)

    return (response, tts, message_type, extra)


async def bot_on_ready_boot(bot):
    """Notifies user when a course opens up every few minutes."""
    global course_url_template
    course_url_template = course_url_template.format(
        **configurations.get(bot, __name__))
    while True:
        course_dictionary = data.get(bot, __name__, 'courses', default={})

        crns_to_remove = []
        for course_crn, course_values in course_dictionary.items():
            try:
                course_data = await _get_data(
                    bot, *course_values['identity'])
            except Exception as e:
                logging.error("Failed to retrieve the course: " + str(e))
                await asyncio.sleep(30)
                continue
            try:
                status = course_data.find('enrollmentStatus').text
            except:
                status = ''
                logging.error(
                    "There is no enrollment status for {}.".format(course_crn))
            if 'Open' in status:
                crns_to_remove.append(course_crn)
                if 'Restricted' in status:  # Open, but restricted
                    restriction = course_data.find('sectionNotes')
                    if restriction is None:
                        restriction = course_data.find('sectionText')
                    if restriction is None:
                        restriction = "None provided."
                    else:
                        restriction = restriction.text
                    notification = " (Restriction: {})".format(restriction)
                else:  # Open
                    notification = " (No listed restrictions)"
                for user_id in course_values['notify_list']:
                    user = data.get_member(bot, user_id)
                    await bot.send_message(
                        user, "{0[course_title]} ({1}) is now open{2}".format(
                            course_values, course_crn, notification))
                    for it in range(5):
                        await bot.send_message(user, ":warning:")
                        await asyncio.sleep(1)
            await asyncio.sleep(1)

        for crn in crns_to_remove:
            del course_dictionary[crn]

        await asyncio.sleep(5*60)
