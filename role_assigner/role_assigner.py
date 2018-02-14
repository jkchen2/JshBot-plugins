import discord

from jshbot import utilities, plugins, data, logger
from jshbot.exceptions import ConfiguredBotException
from jshbot.commands import Command, SubCommand, ArgTypes, Arg, Opt, Response

__version__ = '0.1.0'
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
                Opt('list'),
                doc='Lists available self-assignable roles.',
                function=role_list)
        ],
        allow_direct=False,
        description='Assigns or removes roles.')]


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

    embed = discord.Embed(title='Self-assignable role changes', description='\n'.join(changes))
    return Response(embed=embed)


async def role_joinleave(bot, context):
    """Adds/removes the given role(s) to/from the member."""
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


async def role_list(bot, context):
    """Lists the available roles for self-assignment."""
    available_roles = _check_roles(bot, context.guild)
    if not available_roles:
        raise CBException("There are no self-assignable roles available.")
    embed = discord.Embed(
        title='Self-assignable roles:',
        description='\n'.join(it.mention for it in available_roles))
    return Response(embed=embed)


async def bot_on_ready_boot(bot):
    permissions = { 'manage_roles': 'Allows for self-assignable roles.' }
    utilities.add_bot_permissions(bot, __name__, **permissions)
