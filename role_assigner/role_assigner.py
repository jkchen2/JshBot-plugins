import discord

from jshbot import utilities, plugins, data, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import Command, SubCommand, ArgTypes, Arg, Opt, Response

__version__ = '0.1.2'
CBException = ConfiguredBotException('Role assigner')
uses_configuration = False


@plugins.command_spawner
def get_commands(bot):
    return [Command(
        'role', subcommands=[
            SubCommand(
                Opt('join'),
                Arg('role name', argtype=ArgTypes.SPLIT, convert=utilities.RoleConverter()),
                doc='Join a role (or multiple).',
                function=role_joinleave, id='join'),
            SubCommand(
                Opt('leave'),
                Arg('role name', argtype=ArgTypes.SPLIT, convert=utilities.RoleConverter()),
                doc='Leave a role (or multiple).',
                function=role_joinleave, id='leave'),
            SubCommand(
                Opt('toggle'),
                Arg('role name', argtype=ArgTypes.SPLIT, convert=utilities.RoleConverter()),
                doc='Toggle self-assignable roles.',
                elevated_level=1, function=role_toggle),
            SubCommand(
                Opt('create'),
                Opt('mentionable', optional=True, group='options'),
                Opt('hoisted', optional=True, group='options'),
                Opt('color', optional=True, attached='hex color', group='options',
                    convert=utilities.HexColorConverter()),
                Arg('role name', argtype=ArgTypes.MERGED),
                doc='Creates roles and allows them to be self-assignable.',
                elevated_level=1, function=role_create),
            SubCommand(
                Opt('delete'),
                Arg('role name', argtype=ArgTypes.SPLIT, convert=utilities.RoleConverter()),
                doc='Deletes the given roles.',
                elevated_level=1, function=role_delete),
            SubCommand(
                Opt('verification'),
                Arg('role name', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.RoleConverter()),
                doc='If self-assignable roles requires a role itself, '
                    'it can be set or cleared with this command.',
                elevated_level=1, function=role_verification),
            SubCommand(
                Opt('list'),
                Arg('role name', argtype=ArgTypes.MERGED_OPTIONAL,
                    convert=utilities.RoleConverter()),
                doc='Lists available self-assignable roles, or members with the given role.',
                function=role_list)],
        allow_direct=False,
        description='Assigns or removes roles.', category='user tools')]


def _check_roles(bot, guild):
    """Checks/ensures the validity of available self-assignable roles in the guild."""
    available_role_ids = data.get(bot, __name__, 'roles', guild_id=guild.id, default=[])
    guild_roles = dict((it.id, it) for it in guild.roles)
    top_role = guild.me.top_role
    remaining_roles = []
    for role_id in available_role_ids:
        if role_id not in guild_roles or guild_roles[role_id] > top_role:
            data.list_data_remove(bot, __name__, 'roles', role_id, guild_id=guild.id)
        else:
            remaining_roles.append(guild_roles[role_id])
    return remaining_roles


async def role_toggle(bot, context):
    """Toggles the list of given roles as available for self-assignment."""
    top_role = context.guild.me.top_role
    for role in context.arguments:
        if role > top_role:
            raise CBException(
                "The role {} is not below the bot's role in the hierarchy.".format(role.mention))
        if role.is_default():
            raise CBException("The default role cannot be self-assignable.")
    changes = []
    for role in context.arguments:
        added = data.list_data_toggle(bot, __name__, 'roles', role.id, guild_id=context.guild.id)
        changes.append('{}ed role {}'.format('Add' if added else 'Remov', role.mention))

    embed = discord.Embed(title='Self-assignable role changes:', description='\n'.join(changes))
    return Response(embed=embed)


async def role_joinleave(bot, context):
    """Adds/removes the given role(s) to/from the member."""

    # Check for a verified role
    verified_role = data.get_custom_role(bot, __name__, 'verified', context.guild)
    if verified_role:
        if not data.has_custom_role(bot, __name__, 'verified', member=context.author):
            raise CBException("You must have the role {} in order to self-assign roles.".format(
                verified_role.mention))

    joining = context.id == 'join'
    available_role_ids = data.get(bot, __name__, 'roles', guild_id=context.guild.id, default=[])
    for role in context.arguments:
        if role.id not in available_role_ids:
            raise CBException("The role {} is not self-assignable.".format(role.mention))
    try:
        if joining:
            await context.author.add_roles(*context.arguments, reason="Self-assignable role")
        else:
            await context.author.remove_roles(*context.arguments, reason="Self-assignable role")
    except discord.Forbidden:
        if not context.guild.me.guild_permissions.manage_roles:
            raise CBException("The bot is missing the `Manage Roles` permission.")
        _check_roles(bot, context.guild)
        action = 'assign' if joining else 'remov'
        raise CBException("The role(s) could not be {}ed due to a hierarchy issue.".format(action))

    embed = discord.Embed(
        title='You have {} the role{}:'.format(
            'joined' if joining else 'left', '' if len(context.arguments) == 1 else 's'),
        description='\n'.join(it.mention for it in context.arguments))
    return Response(embed=embed)


async def role_create(bot, context):
    """Creates the given role."""
    role_name = context.arguments[0]
    guild_role_names = list(it.name.lower() for it in context.guild.roles)
    if role_name.lower() in guild_role_names:
        raise CBException("A similarly named role already exists.")
    color = context.options.get('color', discord.Color.default())
    hoisted, mentionable = 'hoisted' in context.options, 'mentionable' in context.options
    try:
        new_role = await context.guild.create_role(
            name=role_name, color=color, hoist=hoisted, mentionable=mentionable,
            reason='Created self-assigned role')
    except discord.Forbidden:
        raise CBException("The bot is missing the `Manage Roles` permission.")
    data.list_data_append(
        bot, __name__, 'roles', new_role.id, guild_id=context.guild.id, duplicates=False)

    embed = discord.Embed(
        title='Created a new self-assignable role:',
        description='{}\n{}\n{}'.format(
            new_role.mention,
            'Hoisted' if hoisted else '',
            'Mentionable' if mentionable else '').strip(),
        color=color if 'color' in context.options else discord.Embed.Empty)
    return Response(embed=embed)


async def role_delete(bot, context):
    """Deletes the given roles."""
    available_role_ids = data.get(bot, __name__, 'roles', guild_id=context.guild.id, default=[])
    for role in context.arguments:
        if role.id not in available_role_ids:
            raise CBException("The role {} is not self-assignable.".format(role.mention))
    try:
        for role in context.arguments:
            await role.delete(reason='Deleted by {0} ({0.id})'.format(context.author))
    except discord.Forbidden:
        raise CBException("The bot is missing the `Manage Roles` permission.")

    return Response(embed=discord.Embed(description='Roles deleted.'))


async def role_verification(bot, context):
    """Sets or clears a verification role to allow self-assignment."""
    role = context.arguments[0]
    if role:
        data.add_custom_role(bot, __name__, 'verified', role)
        return Response(embed=discord.Embed(
            description='Only users with the {} role can self-assign roles.'.format(role.mention)))
    else:
        data.remove_custom_role(bot, __name__, 'verified', context.guild)
        return Response(embed=discord.Embed(description='Anybody can now self-assign roles.'))


async def role_list(bot, context):
    """Lists the available roles for self-assignment."""
    if context.arguments[0]:  # List members that have this role
        role = context.arguments[0]
        if role.is_default():
            raise CBException("Cannot list members with the @everyone role.")
        if not role.members:
            raise CBException("No members have the role {}.".format(role.mention))
        elif len(role.members) > 80:
            name_file = discord.File(
                utilities.get_text_as_file('\n'.join(str(it) for it in role.members)),
                filename='members.txt')
            embed = discord.Embed(
                description='{} members have this role.'.format(len(role.members)))
            return Response(file=name_file, embed=embed)
        else:
            plural = len(role.members) > 1
            return Response(embed=discord.Embed(
                title='{} member{} {} this role:'.format(
                    len(role.members), 's' if plural else '', 'have' if plural else 'has'),
                description=', '.join(it.mention for it in role.members)))

    else:  # List self-assignable roles
        available_roles = _check_roles(bot, context.guild)
        if not available_roles:
            raise CBException("There are no self-assignable roles available.")

        embed = discord.Embed(
            title='Self-assignable roles:',
            description='\n'.join(it.mention for it in available_roles))
        embed.set_footer(text='To join a role, use {}role join'.format(
            utilities.get_invoker(bot, guild=context.guild)))
        return Response(embed=embed)


async def bot_on_ready_boot(bot):
    permissions = { 'manage_roles': 'Allows for self-assignable roles.' }
    utilities.add_bot_permissions(bot, __name__, **permissions)
