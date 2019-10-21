import click
import contextlib
import subprocess
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from .dispatcher import Dispatcher
from .exceptions import TaskError
from .executor import BaseExecutor, ProxyExecutor, _create_executor


class Context(ProxyExecutor):
    """Kitipy context is the global object carrying the kitipy Executor used to
    ubiquitously run commands on local and remote targets, as well as the stack
    and stage objects loaded by task groups and the dispatcher used to update
    the CLI based on executor events.

    It's acting as a global Facade, such that you generally don't need to
    interact with other kitipy or click objects.

    As both kitipy and click exposes their own Context object, you might wonder
    what's the fundamental difference between them, here it is:

      * As said above, kitipy Context carry everything about how and where to
        execute shell commands, on either local or remote targets. As such, it
        has a central place in kitipy and is what you interact with within 
        kitipy tasks.
      * In the other hand, the click Context is here to carry details about CLI
        commands and options, and to actually parse and navigate the command
        tree made of kitipy tasks or regular click commands. As kitipy is a 
        super-set of click features, click.Context actually embeds the
        kitipy.Context object.

    You generally don't need to instantiate it by yourself, as this is
    handled by RootCommand which can be created through the kitipy.root()
    decorator.
    """
    def __init__(self,
                 config: Dict,
                 executor: BaseExecutor,
                 dispatcher: Dispatcher,
                 stage: Optional[Dict[Any, Any]] = None,
                 stack=None):
        """
        Args:
            config (Dict):
                Normalized kitipy config (see normalize_config()).
            executor (kitipy.Executor):
                The command executor used to ubiquitously run commands on local
                and remote targets.
            dispatcher (kitipy.Dispatcher):
                The event dispatcher used by the executor to signal events
                about file transfers and any other event that shall produce
                something on the CLI. This is used to decouple SSH matters 
                from the CLI.
            stage (Optional[Dict[Any, Any]]):
                This is the config for the stage in use.
                There might be no stage available when the Context is built. In
                such case, it can be set afterwards. The stage can be loaded
                through kitipy.load_stage(), but this is handled
                automatically by creating a stack-scoped task group through
                kitipy.task() or kctx.task() decorators.
            stack (Optional[kitipy.docker.BaseStack]):
                This is the stack object representing the Compose/Swarm stack
                in use.
                There might be no stack available when the Context is built. In
                such case, it can be set afterwards. The stack can be loaded
                through kitipy.docker.load_stack(), but this is handled
                automatically by creating a stack-scoped task group through
                kitipy.task() or kctx.task() decorators.
        """
        super().__init__(executor)
        self.config = config
        self._stage = stage
        self._stack = stack
        self.dispatcher = dispatcher

    @property
    def stack(self):
        return self._stack

    @property
    def stage(self):
        return self._stage

    @property
    def executor(self):
        return self._executor

    @contextmanager
    def using_executor(self, executor: BaseExecutor):
        previous = self.executor
        try:
            self._executor = executor
            yield None
        finally:
            self._executor = previous

    @contextmanager
    def using_stage(self, stage_name: str):
        exec = _create_executor(self.config, stage_name, self.dispatcher)
        stage = self.config['stages'][stage_name]
        previous = self._stage

        with self.using_executor(exec):
            try:
                self._stage = stage
                yield None
            finally:
                self._stage = previous

    @contextmanager
    def using_stack(self,
                    stack_name,
                    filename_params: Optional[Dict[str, str]] = None):
        from .docker.stack import load_stack

        filename_params = filename_params if filename_params else {}
        stack = load_stack(self, stack_name, filename_params)
        stack_cfg = self.config['stacks'][stack_name]
        basedir = stack_cfg.get('basedir')

        cm = contextlib.nullcontext()
        if basedir:
            cm = self.cd(basedir)

        previous = self._stack
        with cm:
            try:
                self._stack = stack
                yield None
            finally:
                self._stack = previous

    def invoke(self, cmd: click.Command, *args, **kwargs):
        """Call invoke() method on current click.Context"""
        parent = click.get_current_context()
        ctx = click.Context(cmd, info_name=cmd.name, parent=parent)

        for param in cmd.params:
            if not param.expose_value or param.name in kwargs:
                continue

            default = param.get_default(ctx)
            if param.name in parent.params:
                default = parent.params[param.name]

            kwargs[param.name] = default

        callback = cmd.callback
        if callback is None:
            raise TaskError(
                'Could not invoke command "%s" as it has no callback attached.'
                % (cmd.name))

        with click.core.augment_usage_errors(parent):
            with ctx:  # type: ignore
                return callback(*args, **kwargs)  # type: ignore

    def echo(self, *args, **kwargs):
        """Call echo() method on current click.Context"""
        return click.echo(*args, **kwargs)

    def info(self, message: str):
        """Output a colored info message (black on cyan) on stderr using :func:`click.secho`."""
        return click.secho('INFO: ' + message,
                           bg='cyan',
                           fg='black',
                           bold=True,
                           err=True)

    def warning(self, message: str):
        """Output a colored warning message (black on yellow) on stderr, using :func:`click.secho`."""
        return click.secho('WARNING: ' + message,
                           bg='yellow',
                           fg='black',
                           bold=True,
                           err=True)

    def error(self, message: str):
        """Output a colored error message (white on red) on stderr using :func:`click.secho`."""
        return click.secho('ERROR: ' + message,
                           bg='red',
                           fg='bright_white',
                           bold=True,
                           err=True)

    def fail(self, message):
        """Call fail() method on current click.Context"""
        raise click.ClickException(message)


pass_context = click.make_pass_decorator(Context)


def get_current_context(click_ctx: Optional[click.Context] = None) -> Context:
    """
    Find the current kitipy context or raise an error.

    Args:
        click_ctx (click.Context):
            The click.Context where this function should look for a
            kitipy.Context.

    Raises:
        RuntimeError: When no kitipy context has been found.
    
    Returns:
        Context: The current kitipy context.
    """

    if click_ctx is None:
        click_ctx = click.get_current_context()

    kctx = click_ctx.find_object(Context)
    if kctx is None:
        raise RuntimeError('No kitipy context found.')
    return kctx


def get_current_executor() -> BaseExecutor:
    """
    Get the executor from the current kitipy context or raise an error.

    Raises:
        RuntimeError: When no kitipy context has been found.
    
    Returns:
        BaseExecutor: The executor of the current kitipy context.
    """

    kctx = get_current_context()
    return kctx.executor
