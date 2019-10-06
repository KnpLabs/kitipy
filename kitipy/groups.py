import click
import functools
import os
import subprocess
from typing import Callable, Dict, Optional
from . import filters
from .context import Context, pass_context, get_current_context
from .dispatcher import Dispatcher
from .executor import Executor
from .utils import load_config_file, normalize_config, set_up_file_transfer_listeners


def _fake_click_ctx() -> click.Context:
    """This internal function is used to create a fake click Context. It's 
    used by Group.merge() to list Tasks from source Groups.
    """
    return click.Context.__new__(click.Context)


class Task(click.Command):
    """Task is like regular :class:`click.Command` but it can be dynamically
    disabled through a filter function. Such filters can be used to
    conditionally enable a task for a specific stage or to limit it to remote
    stages for instance.

    Note that only :class:`kitipy.Group` can filter out Tasks; using
    :class:`kitipy.Task` with regular :class:`click.Group` will have no effect.

    kitipy provides some filters in :mod:`kitipy.filters` and :mod:`kitipy.docker.filters`
    but you can also write your own filters if you have more advanced use-cases.
    """
    def __init__(self,
                 name: str,
                 filter: Optional[Callable[[click.Context], bool]] = None,
                 **kwargs):
        """
        Args:
            name (str):
                Name of the task.
            filter (Optional[Callable[[click.Context], bool]]):
                Filter function used to filter out the task.
                When it's not provided, it defaults to a lambda returning
                ``True``.
                
                :class:`click.Context` is passed to the filter function when
                it's called, as it's the most generic object available (eg. 
                everything is accessible from there).
                
                Check native filters to know how to retrieve :class:`kitipy.Context`
                from :class:`click.Context`.
            **kwargs:
                Accept any other parameters also supported by :class:`click.Command`
                constructor.
        """
        super().__init__(name, **kwargs)
        self.filter = filter or (lambda _: True)

    def is_enabled(self, click_ctx: click.Context) -> bool:
        """Check if that Task should be filtered out based on the given 
        :class:`click.Context`.

        Most generally, you shouldn't have to worry about this method, it's 
        automatically called by :class:`kitipy.Group`.

        Args:
            click_ctx (click.Context):
                The click Context passed to the underlying filter.
        
        Returns:
            bool: Either this task should be filtered in (True) or
            filtered out (False).
        """
        return self.filter(click_ctx)

    def invoke(self, click_ctx: click.Context):
        """Given a context, this invokes the attached callback (if it exists)
        in the right way.

        This is a :class:`click.Command` method overriden to implement task
        filtering.

        You generally don't need to call this method by yourself.

        Raises:
            click.ClickException: When this task is filtered out.
        """
        if not self.is_enabled(click_ctx):
            click_ctx.fail('Task "%s" is filtered out.' % self.name)

        return super().invoke(click_ctx)

    def get_help_option(self, click_ctx: click.Context):
        """This is a :class:`click.Command` method overriden to implement task
        filtering.

        You generally don't need to call this method by yourself.
        """
        help_options = self.get_help_option_names(click_ctx)
        if not help_options or not self.add_help_option:
            return

        def show_help(click_ctx, param, value):
            """Returns the help option object when the task is not
            filtered out, or raise an Error.
            """
            if not self.is_enabled(click_ctx):
                click_ctx.fail('Task "%s" not found.' % self.name)

            if value and not click_ctx.resilient_parsing:
                click.echo(click_ctx.get_help(), color=click_ctx.color)
                click_ctx.exit()

        return click.Option(  # type: ignore
            help_options,
            is_flag=True,
            is_eager=True,
            expose_value=False,
            callback=show_help,
            help='Show this message and exit.')


class Group(click.Group, Task):
    """Group is like regular :class:`click.Group` but it implements some
    ktipy-specific features like: support for stage/stack-scoped task groups
    and task filtering.
    """
    def __init__(self,
                 name=None,
                 commands=None,
                 filter=None,
                 invoke_on_help: bool = False,
                 **attrs):
        """
        Args:
            name (str):
                Name of the task Group.
            commands:
                List of commands to attach to this group.
            filter (Callable):
                A function to filter in/out this task group.
            invoke_on_help (bool):
                Whehter this group function should be called before generating
                help message.

                This is needed when the group callback has some side-effects
                used by some filters of the commands attached to this Group.
            **attrs:
                Any other constructor parameters accepted by :class:`click.Group`.
        """
        super().__init__(name, commands, **attrs)
        self._stage_group = None
        self._stack_group = None
        self.filter = filter or (lambda _: True)
        self.invoke_on_help = invoke_on_help

    def merge(self, *args: click.Group):
        """This method can be used to merge multiple :class:`click.Group`,
        including :class:`kitipy.Group` and :class:`kitipy.RootCommand`, into
        another Group. In this way, you can combine Groups coming from other
        projects or kitipy taskfiles.

        Args:
            *args (click.Group):
                One or many source :class:`click.Group` you want to merge in
                the current Group.
        """
        click_ctx = _fake_click_ctx()
        for src in args:
            for cmdname in src.list_commands(click_ctx):
                cmd = src.get_command(click_ctx, cmdname)
                self.add_command(cmd)  # type: ignore

    def get_command(self, click_ctx: click.Context, cmd_name: str):
        """This is a :class:`click.Group` method overriden to implement
        stage/stack-scoped task groups.

        Commands aren't filtered out by this method because :meth:`click.MultiCommand.format_commands`
        calls it to display the help message.

        You generally don't need to call it by yourself.

        Raises:
            KeyError: When the task is not found.
        """
        kctx = click_ctx.find_object(Context)

        if cmd_name in kctx.get_stage_names():
            kctx.meta['stage'] = cmd_name
            return self._stage_group
        if cmd_name in kctx.get_stack_names():
            kctx.meta['stack'] = cmd_name
            return self._stack_group

        cmd = super().get_command(kctx, cmd_name)

        return cmd

    def list_commands(self, click_ctx: click.Context):
        """This is a :class:`click.Group` method overriden to implement
        stage/stack-scoped task groups and task filtering features.

        You generally don't need to call it by yourself.
        """
        commands = self.commands
        root = click_ctx.find_root().command

        kctx = get_current_context()
        stage_names = kctx.get_stage_names()
        if len(stage_names) > 0 and self._stage_group is not None:
            stage_vals = (self._stage_group for i in range(len(stage_names)))
            stage_tasks = dict(zip(stage_names, stage_vals))

            commands = dict(commands, **stage_tasks)

        stack_names = kctx.get_stack_names()
        if len(stack_names) > 0 and self._stack_group is not None:
            stack_vals = (self._stack_group for i in range(len(stack_names)))
            stack_tasks = dict(zip(stack_names, stack_vals))

            commands = dict(commands, **stack_tasks)

        filtered = {}  # type: Dict[str, click.Command]
        for cmd_name, cmd in commands.items():
            if isinstance(cmd, Task) or isinstance(cmd, Group):
                if cmd.is_enabled(click_ctx):
                    filtered[cmd_name] = cmd
            else:
                filtered[cmd_name] = cmd

        return sorted(filtered)

    def get_help(self, click_ctx: click.Context):
        """Format the help into a string and return it.

        This is a :class:`click.Group` method overriden to call the group
        callback when :py:attr:`invoke_on_help` is ``True``.
        
        You generally don't need to call this method by yourself.
        """
        if self.invoke_on_help:
            self.invoke_without_command = True
            self.invoke(click_ctx)
        return super().get_help(click_ctx)

    def command(self, *args, **kwargs):
        """This decorator is inherited from :class:`click.Group` but is
        disabled. You should use the decorator :meth:`Group.task` instead.

        Raises:
            DeprecationWarning
        """
        raise DeprecationWarning(
            "kitipy task groups don\'t support command() helper.\n\n" +
            "You either have to call kitipy.task() or if you really prefer " +
            "using a click Command, you can use click.command() decorator " +
            "and add the command to this group using group.add_command().")

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
        """This decorator creates a new :class:`kitipy.Group` and adds it to
        the current Group. See :class:`kitipy.Group` for more details about the
        differences between :class:`kitipy.Group` and :class:`click.Group`.

        See :func:`kitipy.group` decorator signature for more details about
        accepted parameters.
        
        Returns
            Callable: The decorator to apply to the group function.
        """
        def decorator(f):
            kwargs.setdefault('cls', Group)
            cmd = group(*args, **kwargs)(f)
            self.add_command(cmd)
            return cmd

        return decorator

    def stage_group(self, use_default_group=False, **attrs):
        """This decorator creates a new kitipy.Group and registers it as a
        stage-scoped group on the current Group.
        
        As stage-scoped task groups are regular task groups, this
        decorator is the only way to create a stage-scoped group.

        Args:
            **attrs: Any options accepted by click.group() decorator.
        """
        def decorator(f):
            attrs.setdefault('cls', Group)
            cmd = click.group(**attrs)(_init_stage_group_wrapper(f))
            self._stage_group = cmd
            return cmd

        return decorator

    def stack_group(self, **attrs):
        """This decorator creates a new kitipy.Group and registers it as a
        stack-scoped group on the current Group.
        
        As stack-scoped task groups are regular task groups, this
        decorator is the only way to create a stack-scope group.

        Args:
            **attrs: Any options accepted by click.group() decorator.
        """
        def decorator(f):
            attrs.setdefault('cls', Group)
            cmd = click.group(**attrs)(_init_stack_group_wrapper(f))
            self._stack_group = cmd
            return cmd

        return decorator


def task(name: Optional[str] = None,
         local_only: bool = False,
         remote_only: bool = False,
         **attrs):
    """This decorator creates a new kitipy Task. It automatically sets
    the requested filter depending on local_only/remote_only kwargs.

    Args:
        name (Optional[str]):
            The name of the task. The function name is used by default.
        local_only (bool):
            This task should be enabled only when the current kitipy Executor
            is running in local mode.
        remote_only (bool):
            This task should be enabled only when the current kitipy Executor
            is running in remote mode.
        **attrs:
            Any other parameters supported by click.Command is also supported.
            In addition, it also supports local_only and remote_only
            parameters. Both are booleans and automatically set the appropriate
            filter on the task.
    
    Returns
        Callable: The decorator to apply to the task function.
    """
    if local_only:
        attrs['filter'] = filters.local_only

    if remote_only:
        attrs['filter'] = filters.remote_only
        del attrs['remote_only']

    attrs.setdefault('cls', Task)
    return click.command(name, **attrs)


def group(name: Optional[str] = None, **attrs):
    """This decorator creates a new kitipy Group. See kitipy.Group() for more
    details about the differences between kitipy.Group and click.Group.

    Args:
        name (Optional[str]):
            The name of the group. The function name is used by default.
        **attrs:
            Any other parameter accepted by click.command().
    
    Returns
        Callable: The decorator to apply to the group function.
    """
    attrs.setdefault('cls', Group)
    return click.command(name, **attrs)


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


# @TODO: This won't work as expected if the stage-scoped group is declared
# inside of a stack-scoped group as the stack object has already a copy of the
# Executor.
def _init_stage_group_wrapper(f):
    """This internal function creates a wrapper function run when a
    stage-scoped group is invoked to change the command Executor set on the
    kitipy Context.

    Like _prepend_kctx_wrapper(), the wrapper injects kitipy.Context as first
    argument.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        kctx = get_current_context()
        stage_name = kctx.meta['stage']
        kctx.executor = _create_executor(kctx.config, stage_name,
                                         kctx.dispatcher)
        return f(kctx, *args, **kwargs)

    return wrapper


def _create_executor(config: Dict, stage_name: str,
                     dispatcher: Dispatcher) -> Executor:
    """Instantiate a new executor for the given stage.

    Args:
        config (Dict):
            The whole kitipy config.
        stage_name (str):
            The name of the stage to instantiate an Executor for.
        dispatcher (Dispatcher):
            The dispatcher later used by the instantied Executor.
    """

    stage = config['stages'][stage_name]

    if stage.get('type', None) not in ('remote', 'local'):
        raise click.BadParameter(
            'Stage "%s" has no "type" field or its value is invalid (should be either: local or remote).'
            % (stage_name))

    if stage['type'] == 'local':
        # @TODO: local executor base path should be configurable through stage params
        return Executor(os.getcwd(), dispatcher)

    if 'hostname' not in stage:
        raise click.BadParameter(
            'Remote stage "%s" has no hostname field defined.' % (stage))

    # @TODO: verify and explain better all the mess around basedir/cwd
    basedir = stage.get('basedir', '~/')
    params = {
        'hostname': stage['hostname'],
    }

    if 'ssh_config' in config:
        params['ssh_config_file'] = config['ssh_config']
    if 'paramiko_config' in config:
        # @TODO: we shouldn't be that much permissive with paramiko config
        params['paramiko_config'] = config['paramiko_config']

    return Executor(basedir, dispatcher, **params)


def _init_stack_group_wrapper(f):
    """This internal function creates a wrapper function run when a
    stack-scoped group is invoked to load the requested stack and set it on 
    current kitipy Context.

    Like _prepend_kctx_wrapper(), the wrapper injects kitipy.Context as first
    argument.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        # This has to be imported here to avoid circular dependencies
        from .docker.stack import load_stack

        kctx = get_current_context()
        stack_name = kctx.meta['stack']
        kctx.stack = load_stack(kctx, stack_name)
        return f(kctx, *args, **kwargs)

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
        kwargs['filter'] = (lambda _: True)
        super().__init__(**kwargs)

        config = normalize_config(config)
        dispatcher = set_up_file_transfer_listeners(Dispatcher())

        stages = config['stages'].values()
        if len(stages) == 1:
            stage = list(stages)[0]
        if len(stages) > 1:
            stage = next((stage for stage in stages if stage['default']), None)
            if stage is None:
                raise RuntimeError(
                    'Mutiple stages are defined but none is marked as default.'
                )
        if len(stages) == 0:
            raise RuntimeError(
                'You have to provide a config with at least one stage.')

        executor = _create_executor(config, stage['name'], dispatcher)
        self.kctx = Context(config, executor, dispatcher)
        self.kctx.stage = stage
        self.click_ctx = None

        stacks = config['stacks'].values()
        if len(stacks) == 1:
            from .docker.stack import load_stack
            stack_cfg = list(stacks)[0]
            self.kctx.stack = load_stack(self.kctx, stack_cfg['name'])

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
        self.click_ctx.obj = self.kctx

        with self.click_ctx.scope(cleanup=False):
            self.parse_args(self.click_ctx, args)

        return self.click_ctx

    def invoke(self, click_ctx: click.Context):
        try:
            super().invoke(click_ctx)
        except subprocess.CalledProcessError as e:
            raise TaskError(str(e), self.click_ctx, e.returncode)


class TaskError(click.ClickException):
    def __init__(self,
                 message: str,
                 click_ctx: Optional[click.Context] = None,
                 exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code
        self.click_ctx = click_ctx

    def show(self, file=None):
        color = self.click_ctx.color if self.click_ctx else None
        msg = click.style('Error: %s' % self.format_message(),
                          fg='bright_white',
                          bg='red')
        click.echo(msg, file=file, color=color)


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
