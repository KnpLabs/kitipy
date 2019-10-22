import click
import kitipy
import pytest
import subprocess
from unittest import mock
from kitipy import *


def test_get_current_context():
    with mock.patch('click.get_current_context') as mock_get_current_click_ctx:
        click_ctx = mock.Mock(spec=click.Context)
        kctx = mock.Mock(spec=kitipy.Context)

        mock_get_current_click_ctx.return_value = click_ctx
        click_ctx.find_object.return_value = kctx

        returned = get_current_context()

        assert returned is kctx
        click_ctx.find_object.assert_called_with(Context)


def test_get_current_context_fails():
    with mock.patch('click.get_current_context') as mock_get_current_click_ctx:
        click_ctx = mock.Mock(spec=click.Context)
        kctx = mock.Mock(spec=kitipy.Context)

        mock_get_current_click_ctx.return_value = click_ctx
        click_ctx.find_object.return_value = None

        with pytest.raises(RuntimeError):
            get_current_context()


def test_get_current_executor():
    with mock.patch('click.get_current_context') as mock_get_current_click_ctx:
        click_ctx = mock.Mock(spec=click.Context)
        kctx = mock.Mock(spec=kitipy.Context)
        kctx.executor = mock.Mock(spec=kitipy.Executor)

        mock_get_current_click_ctx.return_value = click_ctx
        click_ctx.find_object.return_value = kctx

        returned = get_current_executor()

        assert returned is kctx.executor
        click_ctx.find_object.assert_called_with(Context)


def test_get_current_executor_fails():
    with mock.patch('click.get_current_context') as mock_get_current_click_ctx:
        click_ctx = mock.Mock(spec=click.Context)

        mock_get_current_click_ctx.return_value = click_ctx
        click_ctx.find_object.return_value = None

        with pytest.raises(RuntimeError):
            get_current_executor()


def test_context_run():
    dispatcher = mock.Mock(spec=kitipy.Dispatcher)
    executor = mock.Mock(spec=kitipy.Executor)
    kctx = kitipy.Context({}, executor, dispatcher)

    kctx.run("some cmd", env={"FOO": "bar"}, pipe=True, check=False)

    executor.run.assert_called_once_with('some cmd', {"FOO": "bar"}, None,
                                         True, None, True, None, True, False)
