import click
import contextlib
import functools
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from . import filters
from .context import Context, pass_context, get_current_context
from .dispatcher import Dispatcher
from .exceptions import TaskError
from .executor import Executor, _create_executor
from .utils import load_config_file, normalize_config, set_up_file_transfer_listeners


class Task(click.Command):
    """Task is like regular click.Command but it can be dynamically
    disabled through a filter function. Such functions can be used to
    conditionally enable a task for a specific stage/stack or to limit it to
    remote stages for instance.

    Note that only kitipy Group can filter out Task; using Task with regular
    click Group will have no effect.

    kitipy provides some filters in kitipy.filters and kitipy.docker.filters
    but you can also write your own filters if you have more advanced use-cases.
    """
    def __init__(
            self,
            name: str,
            filters: Optional[List[Callable[[click.Context], bool]]] = None,
            cwd: Optional[str] = None,
            **kwargs):
        """
        Args:
            name (str):
                Name of the task.
            filters (List[Callable[[click.Context], bool]]):
                Filter functions used to filter out the task based on click
                Context. When it's not provided, the task or group is always 
                enabled.
                Click Context is passed as argument as it's the most generic
                object available (eg. everything is accessible from there).
                Check native filters to know how to retrieve kitipy Context
                from click Context.
            cwd (str):
                Base directory where the commands used by this task shoud be
                executed.

                It's recommended to use this parameter instead of calling
                kctx.cd() directly as the Task cwd can be easily changed, thus
                increasing the Task reusability.
            **kwargs:
                Accept any other parameters also supported by click.Command()
                constructor.
        """
        super().__init__(name, **kwargs)
        self.filters = filters if filters else []
        self.cwd = cwd

    def is_enabled(self, click_ctx: click.Context) -> bool:
        """Check if the that Task should be filtered out based on click Context.
        Most generally, you shouldn't have to worry about this method, it's 
        automatically called by kitipy Group.

        Args:
            click_ctx (click.Context):
                The click Context passed to the underlying filter.
        
        Returns:
            bool: Either this task should be filtered in (True) or
            filtered out (False).
        """
        for filter in self.filters:
            if not filter(click_ctx):
                return False
        return self.hidden != True

    def invoke(self, click_ctx: click.Context):
        """Given a context, this invokes the attached callback (if it exists)
        in the right way.

        Raises:
            click.ClickException: When this task is filtered out.
        """
        if not self.is_enabled(click_ctx):
            raise TaskError('Task "%s" is filtered out.' % self.name)

        cm = contextlib.nullcontext()
        if self.cwd:
            kctx = get_current_context(click_ctx)
            cm = kctx.cd(self.cwd)

        with cm:
            return super().invoke(click_ctx)


class Group(click.Group):
    """Group is like regular click.Group but it implements some ktipy-specific
    features like: support for stage/stack-scoped task groups and task
    filtering.
    """
    def __init__(
            self,
            name=None,
            commands=None,
            tasks: Optional[List[Task]] = None,
            stage: Optional[str] = None,
            stack: Optional[str] = None,
            filters: Optional[List[Callable[[click.Context], bool]]] = None,
            cwd: Optional[str] = None,
            invoke_on_help: bool = False,
            transparents: List[click.MultiCommand] = [],
            **attrs):
        """
        Args:
            name (str):
                Name of the task Group.
            commands:
                List of commands to attach to this group.
            filters (List[Callable]):
                A group of functions to filter this task group.
            cwd (str):
                Base directory where the commands used by this task shoud be
                executed.

                It's recommended to use this parameter instead of calling
                kctx.cd() directly as the Task cwd can be easily changed, thus
                increasing the Task reusability.
            invoke_on_help (bool):
                Whehter this group function should be calle before generatng
                help message.
            **attrs:
                Any other constructor parameters accepted by click.Group.
        """
        super().__init__(name, commands, **attrs)
        self.tasks = tasks if tasks else []
        self.stage = stage
        self.stack = stack
        self.filters = filters if filters else []
        self.cwd = cwd
        self.invoke_on_help = invoke_on_help
        self._transparents = {}  # type: Dict[str, click.MultiCommand]
        self._resolved = {
        }  # type: Dict[str, Tuple[click.Command, click.MultiCommand]]

        for group in transparents:
            self.add_transparent_group(group)

    @property
    def transparent_groups(self) -> List[click.MultiCommand]:
        return list(self._transparents.values())

    def add_command(self, cmd, name=None):
        if len(self._resolved) > 0:
            raise RuntimeError(
                "This task group structure has already been resolved, you can't merge or add new tasks or commands at this point."
            )

        if isinstance(cmd, (Task, Group)):
            self.tasks.append(cmd)
        else:
            super().add_command(cmd, name)

    def add_transparent_group(self, group: click.MultiCommand):
        if len(self._resolved) > 0:
            raise RuntimeError(
                "This task group structure has already been resolved, you can't add new transparent groups at this point."
            )

        if group.name in self._transparents:
            raise KeyError(
                "There's already a transparent group named %s attached to %s."
                % (group.name, self.name))

        self._transparents[group.name] = group

    def merge(self, *args: click.Group):
        """This method can be used to merge click.Group(s), including kitipy
        Groups and RootCommand, into another Group. In this way, you can
        combine Groups coming from other projects/kitipy taskfiles.

        Args:
            *args (click.Group):
                One or many source click.Groups you want to merge in the
                current Group.
        """
        for src in args:
            for cmd in src.commands.values():
                self.add_command(cmd)

            if isinstance(src, Group):
                for task in src.tasks:
                    self.add_command(task)

                for group in src.transparent_groups:
                    self.add_transparent_group(group)

    def is_enabled(self, click_ctx: click.Context) -> bool:
        """Check if the that Task should be filtered out based on click Context.
        Most generally, you shouldn't have to worry about this method, it's 
        automatically called by kitipy Group.

        Args:
            click_ctx (click.Context):
                The click Context passed to the underlying filter.
        
        Returns:
            bool: Either this task should be filtered in (True) or
            filtered out (False).
        """
        for filter in self.filters:
            if not filter(click_ctx):
                return False
        return self.hidden != True

    def _resolve_commands(self, click_ctx: click.Context):
        if len(self._resolved) > 0:
            return self._resolved

        commands = self._filter_command_list(click_ctx, self)
        if len(commands) > 0:
            names, origs, cmds = zip(*commands)
        else:
            names, origs, cmds = ((), (), ())

        origins = dict(zip(names,
                           origs))  # type: Dict[str, click.MultiCommand]
        resolved = dict(
            zip(names, zip(cmds, origs)
                ))  # type: Dict[str, Tuple[click.Command, click.MultiCommand]]

        for group_name, group in self._transparents.items():
            subcommands = self._filter_command_list(click_ctx, group)

            if len(subcommands) > 0:
                sub_names, sub_origs, sub_cmds = zip(*subcommands)
            else:
                sub_names, sub_origs, sub_cmds = ((), (), ())

            # @TODO: check if the subcommand
            colliding = list(set(origins.keys()) & set(sub_names))
            if len(colliding) > 0:
                error = ', '.join([
                    '"%s" from "%s"' % (cmd_name, origins[cmd_name].name)
                    for cmd_name in colliding
                ])
                raise RuntimeError(
                    'The transparent group "%s" adds command(s) colliding with: %s.'
                    % (group.name, error))

            resolved.update(dict(zip(sub_names, zip(sub_cmds, sub_origs))))
            origins.update(dict(zip(sub_names, sub_origs)))

        self._resolved = resolved
        return self._resolved

    def _filter_command_list(
            self, click_ctx: click.Context, group: click.MultiCommand
    ) -> List[Tuple[str, click.MultiCommand, click.Command]]:
        cmd_group = super() if group is self else group
        filtered = [
        ]  # type: List[Tuple[str, click.MultiCommand, click.Command]]

        commands = cmd_group.list_commands(click_ctx)  # type: ignore
        for cmd_name in commands:
            cmd = cmd_group.get_command(click_ctx, cmd_name)  # type: ignore
            if cmd is None:
                continue

            if not cmd.hidden:
                filtered.append((cmd_name, cmd_group, cmd))  # type: ignore

        tasks = group.tasks if isinstance(group, Group) else []
        for task in tasks:
            if not task.is_enabled(click_ctx):
                continue

            if task.name in filtered:
                raise RuntimeError(
                    'Two tasks/commands with the same name and successful filters are attached to the group named "%s".'
                    % (self.name))

            filtered.append((task.name, group, task))

        return filtered

    def get_command(self, click_ctx: click.Context, cmd_name: str):
        """This is a click.Group method overriden to implement
        stage/stack-scoped task groups.

        Commands aren't filtered out by this method because format_command()
        method calls it to display the help message.

        You generally don't need to call it by yourself.
        """
        resolved = self._resolve_commands(click_ctx)
        if cmd_name not in resolved:
            return None
        return resolved[cmd_name][0]

    def list_commands(self, click_ctx: click.Context):
        """This is a click.Group method overriden to implement
        stage/stack-scoped task groups and task filtering behaviors.

        You generally don't need to call it by yourself.
        """
        commands = self._resolve_commands(click_ctx).keys()
        return sorted(commands)

    def get_help(self, click_ctx: click.Context):
        if self.invoke_on_help:
            self.invoke_without_command = True
            self.invoke(click_ctx)

        kctx = get_current_context(click_ctx)

        basedir_cm = contextlib.nullcontext()
        if self.cwd is not None:
            basedir_cm = kctx.cd(self.cwd)

        stage_cm = contextlib.nullcontext()
        if self.stage is not None:
            stage_cm = kctx.using_stage(self.stage)

        stack_cm = contextlib.nullcontext()
        if self.stack is not None:
            stack_cm = kctx.using_stack(self.stack)

        with basedir_cm, stage_cm, stack_cm:
            return super().get_help(click_ctx)

    def format_commands(self, click_ctx: click.Context, formatter):
        """Format Commands section for the help message."""
        for group_name, group in self._transparents.items():
            subcommands = group.list_commands(click_ctx)
            self._print_group_help_section(click_ctx, group_name, group,
                                           subcommands, formatter)

        resolved = self._resolve_commands(click_ctx).values()
        subcommands = [cmd.name for cmd, group in resolved if group is self]
        self._print_group_help_section(click_ctx, 'Commands', self,
                                       sorted(subcommands), formatter)

    def _print_group_help_section(self, click_ctx: click.Context,
                                  section_name: str, group, subcommands,
                                  formatter):
        # This code comes from click.MultiCommand.format_commands()
        commands = []
        for subcommand in subcommands:
            cmd = group.get_command(click_ctx, subcommand)
            if cmd is not None:
                commands.append((subcommand, cmd))

        # allow for 3 times the default spacing
        if len(commands):
            limit = formatter.width - 6 - max(len(cmd[0]) for cmd in commands)

            rows = []
            for subcommand, cmd in commands:
                help = cmd.get_short_help_str(limit)
                rows.append((subcommand, help))

            if rows:
                with formatter.section(section_name):
                    formatter.write_dl(rows)

    def command(self, *args, **kwargs):
        raise DeprecationWarning(
            "kitipy task groups don\'t support command() helper.\n\n" +
            "You either have to call kitipy.task() or if you really prefer " +
            "using a click Command, you can use click.command() decorator " +
            "and add the command to this group using group.add_command().")

    def invoke(self, click_ctx: click.Context):
        """Given a context, this invokes the attached callback (if it exists)
        in the right way.

        Raises:
            click.ClickException: When this group is filtered out.
        """
        if not self.is_enabled(click_ctx):
            raise TaskError('Task "%s" is filtered out.' % self.name)

        kctx = get_current_context(click_ctx)

        basedir_cm = contextlib.nullcontext()
        if self.cwd is not None:
            basedir_cm = kctx.cd(self.cwd)

        stage_cm = contextlib.nullcontext()
        if self.stage is not None:
            stage_cm = kctx.using_stage(self.stage)

        stack_cm = contextlib.nullcontext()
        if self.stack is not None:
            stack_cm = kctx.using_stack(self.stack)

        # The code below is directly copied from click.MultiCommand.invoke().
        # There're however 3 major changes:
        #   * Chain mode has been removed
        #   * The callback of the current group is called before resolving the
        #     subcommand. As such click_ctx.invoked_subcommand is not set,
        #     unlike parent implementation). In this way, the subcommand filter
        #     can use values dynamically set by the parent callback.
        #   * Wrap the callback execution with a context manager returned by cd
        #     (or a null one) to easily restore the Executor state.
        def _process_result(value):
            if self.result_callback is not None:
                value = click_ctx.invoke(self.result_callback, value,
                                         **click_ctx.params)
            return value

        # Ensure that the cwd set on this group is rewinded once the children
        # commands got executed.
        with basedir_cm, stage_cm, stack_cm:
            if not click_ctx.protected_args:
                if self.invoke_without_command:
                    return click.Command.invoke(self, click_ctx)
                click_ctx.fail('Missing command.')

            # Fetch args back out
            args = click_ctx.protected_args + click_ctx.args
            click_ctx.args = []
            click_ctx.protected_args = []
            # Make sure the context is entered so we do not clean up
            # resources until the result processor has worked.
            with click_ctx:  # type: ignore
                click.Command.invoke(self, click_ctx)
                cmd_name, cmd, args = self.resolve_command(click_ctx, args)
                sub_ctx = cmd.make_context(cmd_name, args, parent=click_ctx)
                with sub_ctx:  # type: ignore
                    return _process_result(sub_ctx.command.invoke(sub_ctx))

    def task(self, *args, **kwargs):
        """This decorator creates a new kitipy task and adds it to the current
        Group. See kitipy.Task() for more details about the
        differences between kitipy.Task and click.Command.

        See kitipy.task() signature for more details about accepted
        parameters.

        Also note that the task function that receives this decorator will 
        get the current kitipy.Context as 

        Returns
            Callable: The decorator to apply to the group function.
        """
        def decorator(f):
            kwargs.setdefault('cls', Task)
            cmd = task(*args, **kwargs)(_prepend_kctx_wrapper(f))
            self.add_command(cmd)
            return cmd

        return decorator

    def group(self, *args, **kwargs):
        """This decorator creates a new kitipy Group and adds it to the current
        Group. See kitipy.Group() for more details about the differences
        between kitipy.Group and click.Group.

        See kitipy.group() signature for more details about accepted
        parameters.
        
        Returns
            Callable: The decorator to apply to the group function.
        """
        def decorator(f):
            kwargs.setdefault('cls', Group)
            cmd = group(*args, **kwargs)(f)
            self.add_command(cmd)
            return cmd

        return decorator

    def stage_group(self, **attrs):
        """This decorator creates a new kitipy.Group and registers it as a
        transparent stage-scoped group on all the stacks in this StackGroup.

        Args:
            **attrs: Any options accepted by StageGroup constructor.
        """
        def decorator(f):
            # @TODO: add config
            attrs.setdefault('cls', StageGroup)
            group = click.group(**attrs)(lambda _: ())
            self.add_transparent_group(group)
            return group

        return decorator

    def stack_group(self, **attrs):
        """This decorator creates a new kitipy.Group and registers it as a
        transparent stage-scoped group on all the stacks in this StackGroup.

        Args:
            **attrs: Any options accepted by StageGroup constructor.
        """
        def decorator(f):
            attrs.setdefault('cls', StackGroup)
            group = click.group(**attrs)(lambda _: ())
            self.add_transparent_group(group)
            return group

        return decorator


class StageGroup(click.MultiCommand):
    def __init__(self,
                 name: str,
                 subgroups_params: Optional[Dict[str, Any]] = None,
                 **attrs):
        super().__init__(name, **attrs)
        self.subgroups_params = subgroups_params if subgroups_params else {}

        self._stages = {}  # type: Dict[str, Group]
        self._all = self._create_stage('all')
        self._resolved = {}  # type: Dict[str, click.Command]

    @property
    def all(self):
        return self._all

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(
                "Stage group names can't start with an underscore.")
        if name not in self._stages:
            self._stages[name] = self._create_stage(name)
        return self._stages[name]

    def _create_stage(self, stage_name: str, callback=None) -> Group:
        if callback is None:
            callback = lambda _: ()
        callback = _prepend_kctx_wrapper(callback)

        args = self.subgroups_params.copy()
        args['stage'] = stage_name
        args.setdefault('cls', Group)

        return group(stage_name, **args)(callback)

    def _resolve_commands(self, click_ctx: click.Context):
        if len(self._resolved) > 0:
            return self._resolved

        kctx = get_current_context(click_ctx)
        stages_cfg = kctx.config.get('stages', {})
        stages = {}

        for name in stages_cfg.keys():
            group = self.__getattr__(name)
            group.merge(self._all)

            stages[name] = (group, self)

        self._resolved = stages  # type: ignore
        return self._resolved

    def stage(self, name, **attrs):
        def decorator(f):
            attrs.setdefault('cls', Group)
            attrs['stage'] = name
            group = click.group(**attrs)(f)
            self._stages[name] = group
            return group

        return decorator

    def list_commands(self, click_ctx: click.Context):
        return self._resolve_commands(click_ctx).keys()

    def get_command(self, click_ctx: click.Context, cmd_name: str):
        resolved = self._resolve_commands(click_ctx)
        return resolved[cmd_name][0] if cmd_name in resolved else None

    def format_help(self, click_ctx, formatter):
        raise RuntimeError("StackGroups don't have any specific help message.")

    def invoke(self, click_ctx):
        raise RuntimeError(
            "You can't directly invoke a StackGroup, you should instead invoke one of its member."
        )


class StackGroup(click.MultiCommand):
    def __init__(self,
                 name,
                 subgroups_params: Optional[Dict[str, Any]] = None,
                 **attrs):
        super().__init__(name, **attrs)
        self.subgroups_params = subgroups_params if subgroups_params else {}

        self._stacks = {}  # type: Dict[str, Group]
        self._all = self._create_stack('all')
        self._resolved = {}  # type: Dict[str, click.Command]

    @property
    def all(self):
        return self._all

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(
                "Stack group names can't start with an underscore.")
        if name not in self._stacks:
            self._stacks[name] = self._create_stack(name)
        return self._stacks[name]

    def _create_stack(self, stack_name: str, callback=None) -> Group:
        if callback is None:
            callback = lambda _: ()
        callback = _prepend_kctx_wrapper(callback)

        args = self.subgroups_params.copy()
        args.setdefault('cls', Group)
        args['stack'] = stack_name

        return group(stack_name, **args)(callback)

    def _resolve_commands(self, click_ctx: click.Context):
        if len(self._resolved) > 0:
            return self._resolved

        kctx = get_current_context(click_ctx)
        stacks_cfg = kctx.config.get('stacks', {})
        stacks = {}  # type: Dict[str, Group]

        for name in stacks_cfg.keys():
            group = self.__getattr__(name)
            group.merge(self._all)

            stacks[name] = (group, self)

        self._resolved = stacks  # type: ignore
        return self._resolved

    def stack(self, name, **attrs):
        def decorator(f):
            attrs.setdefault('cls', Group)
            attrs['stack'] = name
            cmd = group(**attrs)(f)
            self._stacks[name] = cmd
            return cmd

        return decorator

    def list_commands(self, click_ctx: click.Context):
        return self._resolve_commands(click_ctx).keys()

    def get_command(self, click_ctx: click.Context, cmd_name: str):
        resolved = self._resolve_commands(click_ctx)
        return resolved[cmd_name][0] if cmd_name in resolved else None

    def format_help(self, click_ctx, formatter):
        raise RuntimeError("StackGroups don't have any specific help message.")

    def invoke(self, click_ctx):
        raise RuntimeError(
            "You can't directly invoke a StackGroup, you should instead invoke one of its member."
        )


def task(name: Optional[str] = None,
         filters: List[Callable[[click.Context], bool]] = [],
         cwd: Optional[str] = None,
         **attrs):
    """This decorator creates a new kitipy Task. It automatically sets
    the requested filter depending on local_only/remote_only kwargs.

    Args:
        name (Optional[str]):
            The name of the task. The function name is used by default.
        filters (List[Callable[[click.Context], bool]]):
            List of filters passed to the Task constructor. The filter is 
            added to the list when both are provided.
        cwd (Optional[str]):
            Base directory where the commands used by this task should be
            executed.

            It's recommended to use this parameter instead of calling
            kctx.cd() directly as the Task cwd can be easily changed, thus
            increasing the Task reusability.
        **attrs:
            Any other parameters supported by click.Command is also supported.
            In addition, it also supports local_only and remote_only
            parameters. Both are booleans and automatically set the appropriate
            filter on the task.
    
    Returns
        Callable: The decorator to apply to the task function.
    """
    if cwd:
        attrs['cwd'] = cwd
    attrs['filters'] = filters
    attrs.setdefault('cls', Task)
    return click.command(name, **attrs)


def group(name: Optional[str] = None, **attrs):
    """This decorator creates a new kitipy Group. See kitipy.Group() for more
    details about the differences between kitipy.Group and click.Group.

    Args:
        name (Optional[str]):
            The name of the group. The name of the decorated function is used
            by default.
        local_only (bool):
            This group should be enabled only when the current kitipy Executor
            is running in local mode.
        remote_only (bool):
            This group should be enabled only when the current kitipy Executor
            is running in remote mode.
        filter (Callable):
            A function to filter in/out this task group.
        cwd (str):
            Base directory where the commands used by this task group should be
            executed.

            It's recommended to use this parameter instead of calling
            kctx.cd() directly as the Group cwd can be easily changed, thus
            increasing the Group reusability.
        **attrs:
            Any other parameter accepted by click.command().

    Returns
        Callable: The decorator to apply to the group function.
    """
    attrs.setdefault('cls', Group)
    return task(name, **attrs)


def _prepend_kctx_wrapper(f):
    """This internal function creates a wrapper function automatically applied
    to task function to inject the kitipy.Context as first argument.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        # Don't add kctx if it's already in *args. This might happen when a
        # task is invoked from another one.
        if len(args) == 0 or not isinstance(args[0], Context):
            kctx = get_current_context()
            args = (kctx, ) + args
        return f(*args, **kwargs)

    return wrapper


class RootCommand(Group):
    """The RootCommand is used to mark the root of kitipy task tree. It's
    mostly a kitipy task group but without filter support. It's a central
    piece of kitipy as it's responsible for creating the kitipy Context and the
    Executor used to run local and remote commands seamlessly.

    If there's a single stage defined, it'll be used by default. If there're
    multiple stages, one have to be marked as default or an error got raised.
    The config have to be provided with at least one stage or an error is
    raised. The normalize_config() function takes care of adding a default
    local stage if none is present.

    In the same way, if there's a single stack defined, it'll be used by
    default. However, if there're multiple stacks, no default stacks will be
    loaded.
    """
    def __init__(self, config: Dict, basedir: str = '', **kwargs):
        """
        Args:
            config (Dict):
                Kitipy config. The constructor takes care of normalizing the
                config format (see normalize_config()).
            basedir (str):
                This is the base directory where kitipy commands will be executed.
                You generally want to use the current working directory
                (eg. os.getcwd()), but in some cases you might want to run all
                or a subset of your tasks in a specific subdirectory of your
                project (for instance if your project is composed of multiple
                components/services).
            **kwargs:
                Accept any valid argument for click.Group().

        Raises:
            RuntimeError:
                If there're multiple stages defined and there're no default stage.
        """
        # RootCommand can't be filtered out, that'd make no sense.
        kwargs['filters'] = []
        super().__init__(**kwargs)

        self._config = normalize_config(config)
        self._dispatcher = set_up_file_transfer_listeners(Dispatcher())
        self.click_ctx = None

        stages = config['stages'].values()
        if len(stages) == 1:
            stage = list(stages)[0]
            self.stage = stage['name']
        if len(stages) > 1:
            stage = next((stage for stage in stages if stage.get('default')),
                         None)
            if stage is None:
                raise RuntimeError(
                    'Mutiple stages are defined but none is marked as default.'
                )
            self.stage = stage['name']

        stacks = config['stacks'].values()
        if len(stacks) == 1:
            stack_cfg = list(stacks)[0]
            self.stack = stack_cfg['name']

    def make_context(self, info_name, args, parent=None, **extra):
        """Create a click.Context and parse remaining CLI args.

        See make_context() method from click.Group. This method does pretty
        much the same job but attaches kitipy.Context to click.Context 
        before parsing remaning CLI args. This is needed as subcommands might
        be stage/stack-dedicated task groups, in which case stages/stacks
        names have to be accessed through kitipy.Context during parsing.

        You don't need to call this method by yourself.
        """
        for key, value in self.context_settings.items():
            if key not in extra:
                extra[key] = value

        # Attach kitipy Context to the click Context right after it's created
        # to have it available when parsing remaining CLI args.
        self.click_ctx = click.Context(self,
                                       info_name=info_name,
                                       parent=parent,
                                       **extra)
        executor = Executor(os.getcwd(), '', self._dispatcher)
        self.click_ctx.obj = Context(self._config, executor, self._dispatcher)

        with self.click_ctx.scope(cleanup=False):
            self.parse_args(self.click_ctx, args)

        return self.click_ctx

    def invoke(self, click_ctx: click.Context):
        try:
            super().invoke(click_ctx)
        except TaskError as err:
            if err.click_ctx is None:
                err.click_ctx = self.click_ctx
            raise err
        except subprocess.CalledProcessError as err:
            raise TaskError(str(err), self.click_ctx, err.returncode)


def root(config: Optional[Dict] = None,
         config_file: Optional[str] = None,
         basedir=None,
         **kwargs):
    """This decorator is used to create the kitipy RootCommand group. It loads
    the given config_file if provided or uses the given config parameter. The
    config_file parameter takes precedence over config. If no config is
    provided, it defaults to an empty config dict.

    This is generally what you want to call to declare the root of your task
    tree and use all of the kitipy features.

    Args:
        config (Optional[Dict]):
            Config used by kitipy.
        config_file (Optional[str]): 
            File containing kitipy config.
        basedir (Optional[str]):
            The basedir where kitipy commands should be executed. If not provided,
            the current working directory will be used.
        **kwargs:
            Any other argument supported by click.group() decorator.

    Returns:
        Callable: The decorator to apply to the task function.
    """
    if config_file is not None:
        config = load_config_file(config_file)
    if basedir is None:
        basedir = os.getcwd()

    if config_file is None and config is None:
        config = {}

    return click.group('root',
                       cls=RootCommand,
                       config=config,
                       basedir=basedir,
                       **kwargs)
