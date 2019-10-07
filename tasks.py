#!/usr/bin/env python3
import kitipy
import kitipy.docker
import kitipy.docker.tasks
import subprocess

config = {
    'stacks': {
        'kitipy': {
            'file': 'tests/docker-compose.yml',
        },
    },
}


def local_with_venv(kctx: kitipy.Context, cmd: str, **kwargs):
    return kctx.local('. .venv/bin/activate; ' + cmd, **kwargs)


@kitipy.root(config_file=None, config=config)
def root():
    pass


@root.command()
@click.option('--diff/--no-diff', 'show_diff', default=True)
@click.option('--force', is_flag=True, default=None)
def format(kctx: kitipy.Context, show_diff, force):
    """Run yapf to detect style divergences and fix them."""
    confirm_message = 'Do you want to reformat your code using yapf?'

    if show_diff:
        diff = local_with_venv(kctx,
                               'yapf --diff -r kitipy/ tests/ tasks*.py',
                               check=False,
                               pipe=True)
        kctx.echo(diff.stdout, nl=False)

        if diff.returncode != 0 and len(diff.stdout) == 0:
            raise click.ClickException('Failed to properly execute yapf')

        if len(diff.stdout) == 0:
            sys.exit(0)
        confirm_message = 'Do you want to apply this diff?'

    if force == None:
        force = click.confirm(confirm_message, default=True)
    if force:
        local_with_venv(kctx, 'yapf -vvv -p -i -r kitipy/ tests/ tasks*.py')


@root.command()
def lint(kctx: kitipy.Context):
    """Run mypy, a static type checker, to detect type errors."""
    local_with_venv(kctx, 'mypy -p kitipy')
    # @TODO: find a way to fix types errors in tasks files
    # local_with_venv(kctx, 'mypy tasks*.py')


@root.group()
def test():
    pass


@test.command(name='all')
def test_all(kctx: kitipy.Context):
    """Execute all the tests suites."""
    kctx.invoke(test_unit)
    kctx.invoke(test_tasks)


@test.command(name='unit')
def test_unit(kctx: kitipy.Context):
    # Be sure the SSH container used for tests purpose is up and running.
    # @TODO: add a common way to kitipy to wait for a port to be open
    kctx.invoke(kitipy.docker.tasks.up)

    expected_services = len(kctx.stack.config['services'])
    # @TODO: this won't work as is with Swarm, find how to generalize that sort of tests
    tester = lambda kctx: expected_services == kctx.stack.count_services(
        filter=('status=running'))
    kitipy.wait_for(tester,
                    interval=1,
                    max_checks=5,
                    label="Waiting for services start up...")

    # Host key might change if docker-compose down is used between two test run,
    # thus we start by removing any existing host key.
    kctx.local("ssh-keygen -R '[127.0.0.1]:2022' 1>/dev/null 2>&1")
    kctx.local("ssh-keygen -R '[127.0.0.1]:2023' 1>/dev/null 2>&1")
    kctx.local("ssh-keygen -R testhost 1>/dev/null 2>&1")

    # Ensure first that we're actually able to connect to SSH hosts, or
    # tests will fail anyway.
    kctx.local('ssh -F tests/.ssh/config testhost /bin/true 1>/dev/null 2>&1')
    kctx.local('ssh -F tests/.ssh/config jumphost /bin/true 1>/dev/null 2>&1')
    kctx.local(
        'ssh -F tests/.ssh/config testhost-via-jumphost /bin/true 1>/dev/null 2>&1'
    )

    local_with_venv(kctx, 'pytest tests/unit/ -vv')


@test.command(name='tasks')
@click.argument('suites', nargs=-1, type=str)
def test_tasks(kctx: kitipy.Context, suites: List[str]):
    if len(suites) == 0:
        local_with_venv(kctx, 'pytest tests/tasks/ -vv')
        return

    for suite in suites:
        local_with_venv(kctx, 'pytest tests/tasks/test_%s.py -vv' % (suite))


@test.command(name='generate-git-tgz')
@click.option('--keep-tmp-dir', 'keep', type=bool, default=False, is_flag=True)
def test_generate_git_tgz(kctx: kitipy.Context, keep: bool):
    """(Re) Generate tests/git-archive.tgz.
    
    This command has been implemented and used to generate the original
    tests/git-archive.tgz file, which is used to test git-related helper
    functions.
    """
    tempdir = kctx.executor.mkdtemp()
    commands = ['cd ' + tempdir, 'git init']
    commits = (
        ('foo', 'v0.1'),
        ('bar', 'v0.2'),
        ('baz', 'v0.3'),
        ('pi', 'v0.4'),
        ('yolo', 'v0.5'),
    )

    for commit, tag in commits:
        commands.append('touch ' + commit)
        commands.append('git add ' + commit)
        commands.append('git commit -m "%s"' % (commit))
        commands.append('git tag %s HEAD' % (tag))

    basedir = os.path.dirname(os.path.abspath(__file__))
    tgz_path = os.path.join(basedir, 'tests', 'tasks', 'testdata',
                            'git-repo.tgz')
    commands.append('tar zcf %s .' % (tgz_path))

    try:
        kctx.run(' && '.join(commands))
    finally:
        if not keep:
            kctx.run('rm -rf %s' % (tempdir))


root.add_command(kitipy.docker.tasks.compose)

if __name__ == "__main__":
    root()
