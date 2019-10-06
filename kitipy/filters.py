"""Common kitipy tasks and task groups filters.

These filters have to be used with kitipy.Task and kitipy.Group class, or 
through kitipy.task() and kitipy.group() decorators.
"""

import click
from .context import Context
from typing import Callable, Union


def local_only(click_ctx: click.Context) -> bool:
    """Check if the current kitipy Executor is in local mode.

    Args:
        click_ctx (click.Context): Current click context.
    
    Returns:
        bool: Either the executor is running in local mode. It returns False if
            the're no kitipy Context available right now.
    """

    kctx = click_ctx.find_object(Context)
    if kctx is None:
        return False

    return kctx.is_local


def remote_only(click_ctx: click.Context):
    """Check if the current kitipy Executor is in remote mode.

    Args:
        click_ctx (click.Context): Current click context.
    
    Returns:
        bool: Either the executor is running in remote mode. It returns False
            if the're no kitipy Context available right now.
    """

    kctx = click_ctx.find_object(Context)
    if kctx is None:
        return False
    return kctx.is_remote


def stage_named(expected: str) -> Callable:
    def only(click_ctx: click.Context) -> bool:
        kctx = click_ctx.find_object(Context)  # type: Union[None, Context]
        if kctx is None:
            return False
        return kctx.stage is not None and kctx.stage['name'] == expected

    return only
