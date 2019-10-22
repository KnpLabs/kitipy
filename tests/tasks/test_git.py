import click
import kitipy
import os
import pytest
import tempfile
import shutil
import subprocess
from kitipy import git_actions
from unittest import mock


@pytest.fixture(scope='module')
def dispatcher() -> kitipy.Dispatcher:
    return kitipy.Dispatcher()


@pytest.fixture(scope='module')
def executor(dispatcher: kitipy.Dispatcher) -> kitipy.Executor:
    baredir = tempfile.mkdtemp()
    clonedir = tempfile.mkdtemp()

    basedir = os.path.dirname(os.path.abspath(__file__))
    tgz_path = os.path.join(basedir, 'testdata', 'git-repo.tgz')
    subprocess.run('tar -C %s -zxf %s' % (baredir, tgz_path), shell=True)
    subprocess.run('git clone %s %s' % (baredir, clonedir), shell=True)

    yield kitipy.Executor(clonedir, '', dispatcher)

    shutil.rmtree(baredir)
    shutil.rmtree(clonedir)


def test_ensure_tag_exists(executor, dispatcher):
    kctx = kitipy.Context({}, executor, dispatcher)

    git_actions.ensure_tag_exists(kctx, "v0.5")

    with pytest.raises(click.ClickException):
        git_actions.ensure_tag_exists(kctx, "v2.0")


def test_ensure_tag_is_recent(executor, dispatcher):
    kctx = kitipy.Context({}, executor, dispatcher)

    git_actions.ensure_tag_is_recent(kctx, "v0.4")

    with pytest.raises(click.ClickException):
        git_actions.ensure_tag_is_recent(kctx, "v0.1", last=2)
