import click
import subprocess
from typing import Any, Dict, List, Optional
from .dispatcher import Dispatcher
from .executor import Executor


class Context(object):
    """kitipy Context is the global object carrying the kitipy :class:`Executor` used to
    ubiquitously run commands on local and remote targets, as well as the stack
    and stage objects loaded by task groups and the dispatcher used to decouple
    command execution from other concerns.

    It's acting as a global Facade, such that you generally don't need to
    interact with other kitipy or click objects.

    As both kitipy and click exposes their own ``Context`` object, you might wonder
    what's the fundamental difference between them. Here it is:

      * As said above, kitipy ``Context`` carry everything about how and where to
        execute shell commands, on either local or remote targets. As such, it
        has a central place in kitipy and is what you interact with within 
        kitipy tasks.
      * In the other hand, the click ``Context`` is here to carry details about CLI
        commands and options, and to actually parse and navigate the command
        tree made of kitipy tasks or regular click commands. As kitipy is a 
        super-set of click features, :class:`click.Context` actually embeds the
        :class:`kitipy.Context` object.

    You generally don't need to instantiate it by yourself, as this is
    handled by :class:`RootCommand` which can be created through the :func:`kitipy.root`
    decorator.
    """
    def __init__(self,
                 config: Dict,
                 executor: Executor,
                 dispatcher: Dispatcher,
                 stage: Optional[Dict[Any, Any]] = None,
                 stack=None):
        """
        Args:
            config (Dict):
                Normalized kitipy config (see :meth:`kitipy.normalize_config`).
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
        self.config = config
        self.stage = stage
        self.stack = stack
        self.executor = executor
        self.dispatcher = dispatcher

    def run(self, cmd: str, **kwargs) -> subprocess.CompletedProcess:
        """This method is the way to ubiquitously run commands on either local
        or remote targets, depending on how the executor was set.

        Args:
            cmd (str): The command to run.
            **kwargs: See :meth:`Executor.run` options for more details.

        Raises:
            paramiko.ssh_exception.SSHException:
                When the SSH client fail to run the command. Note that this
                won't be raised when the command could not be found or it
                exits with code > 0 though, but only when something fails at
                the SSH client/server lower level.
                
            subprocess.SubprocessError:
                When check mode is enabled and the processs exits with return
                code > 0.
        
        Returns:
            :class:`subprocess.CompletedProcess`
        """
        return self.executor.run(cmd, **kwargs)

    def local(self, cmd: str, **kwargs) -> subprocess.CompletedProcess:
        """Run a command on local host.
        
        This method is particularly useful when you want to run some commands
        on local host whereas the Executor is running in remote mode. 
        
        For instance, you might want to check if a given git tag or some Docker
        images exists on a remote repository/registry before deploying them, 
        or you might want to fetch the local git author name to log deployment
        events somewhere. Such checks are generally better run locally.

        Args:
            cmd (str): The command to run.
            **kwargs: See :meth:`Executor.local` options for more details.

        Raises:
            subprocess.SubprocessError: When check mode is enabled and the
                processs exits with return code > 0.
        
        Returns:
            :class:`subprocess.CompletedProcess`
        """
        return self.executor.local(cmd, **kwargs)

    def copy(self, src: str, dest: str):
        """Copy a local file to a given path. If the underlying :class:`Executor` has
        been configured to work in remote mode, the given source path will
        be copied over network. Otherwise, nothing happens.
        
        See :meth:`Executor.copy` for more details."""
        self.executor.copy(src, dest)

    def get_stage_names(self):
        """Get the name of all stages in the configuration."""
        return self.config['stages'].keys()

    def get_stack_names(self):
        """Get the name of all stacks in the configuration."""
        return self.config['stacks'].keys()

    @property
    def is_local(self):
        """Check if current kitipy Executor is in local mode."""
        return self.executor.is_local

    @property
    def is_remote(self):
        """Check if current kitipy Executor is in remote mode."""
        return self.executor.is_remote

    @property
    def meta(self):
        """Meta properties from current :class:`click.Context`."""
        return click.get_current_context().meta

    def invoke(self, *args, **kwargs):
        """Call :meth:`click.Context.invoke` method on current
        :class:`click.Context`.
        
        This is particularly useful if you want to invoke another task/command.
        In the example below, when task ``foo`` is invoked, it starts by
        invoking task ``bar``.
        
        .. code-block:: python

            @root.task()
            def foo(kctx: kitipy.Context):
                kctx.invoke(bar)
                # Some more actions

        To know more about other composition patterns, see :ref:`composition-patterns`.
        """
        return click.get_current_context().invoke(*args, **kwargs)

    def echo(self, *args, **kwargs):
        """Call :func:`click.echo`."""
        return click.echo(*args, **kwargs)

    def info(self, message: str):
        """Output a colored info message (black on cyan) on stderr using :func:`click.secho`."""
        return click.secho('WARNING: ' + message,
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
        """Raise a :class:`click.ClickException`."""
        raise click.ClickException(message)


pass_context = click.make_pass_decorator(Context)
pass_context.__doc__ = """This decorator prepends the function arguments with
the current :class:`kitipy.Context`.

This is particularly useful for group functions, when you've to load/set some
values on the Context. However this is an advanced usage of kitipy, stage and
stack-scoped groups are actually doing all the hard work to load and set the 
stage/stack on the the Context.
"""


def get_current_context() -> Context:
    """
    Get the current kitipy context or raise an error.

    Raises:
        RuntimeError: When no kitipy context is available.
    
    Returns:
        Context: The current kitipy Context.
    """

    click_ctx = click.get_current_context()
    kctx = click_ctx.find_object(Context)
    if kctx is None:
        raise RuntimeError('No kitipy context found.')
    return kctx


def get_current_executor() -> Executor:
    """
    Get the executor from the current kitipy Context or raise an error.

    Raises:
        RuntimeError: When no kitipy context is available.
    
    Returns:
        Executor: The executor of the current kitipy context.
    """

    kctx = get_current_context()
    return kctx.executor
