import click
import kitipy
import os
from kitipy.docker import docker_actions


def is_tool_enabled(tool: str):
    def only(click_ctx: click.Context) -> bool:
        kctx = kitipy.get_current_context(click_ctx)
        if 'toolbox' not in kctx.config or 'php' not in kctx.config['toolbox']:
            return False

        toolbox = kctx.config['toolbox']['php']
        if toolbox is None or toolbox is True:
            return True

        if not isinstance(toolbox, dict):
            return False

        return tool in toolbox and toolbox[tool] is not False

    return only


@kitipy.group(name='PHP Tools')
def php_tools():
    pass


# @TODO: Add psalm
# @TODO: Add infection


@php_tools.task(name='cs-fixer', filters=[is_tool_enabled('php-cs-fixer')])
@click.option('--diff/--no-diff', 'show_diff', default=True)
@click.option('--fix/--no-fix', default=None)
def php_cs_fixer(kctx: kitipy.Context, show_diff: bool, fix: bool):
    """Run php-cs-fixer"""
    if not show_diff and not fix:
        kctx.fail(
            "You can't use both --no-diff and --no-fix at the same time.")

    # @TODO: Update this image
    dry_run = lambda: docker_actions.container_run(
        'knplabs/php-cs-fixer:v2.13.1',
        'fix --dry-run --diff --verbose',
        volume=kctx.cwd + ':/workdir',
        user=os.getuid())

    apply = lambda: docker_actions.container_run(
        'knplabs/php-cs-fixer:v2.13.1',
        'fix --verbose --config=.php_cs',
        volume=kctx.cwd + ':/workdir',
        user=os.getuid())

    kitipy.confirm_and_apply(
        dry_run,
        'Do you want to reformat your code using php-cs-fixer?',
        apply,
        show_dry_run=show_diff,
        ask_confirm=fix is None,
        should_apply=fix if fix is not None else True)


@php_tools.task(filters=[is_tool_enabled('phpcpd')])
def phpcpd(kctx: kitipy.Context):
    """Run phpcpd"""
    docker_actions.container_run('phpqa/phpcpd:4.0.0',
                                 '--progress src/',
                                 volume=kctx.cwd + ':/app')


@php_tools.task(filters=[is_tool_enabled('phpmd')])
def phpmd(kctx: kitipy.Context):
    """Run phpmd"""
    docker_actions.container_run('phpqa/phpmd:2.6.0',
                                 'phpmd src/ text phpmd.xml',
                                 volume=kctx.cwd + ':/app')


# @TODO: Add a method to easily configure phpmetrics (e.g. folders to analyze)
@php_tools.task(filters=[is_tool_enabled('phpmetrics')])
def phpmetrics(kctx: kitipy.Context):
    """Run phpmetrics"""
    docker_actions.container_run('phpqa/phpmetrics:2.3.2',
                                 '--report-html=.phpmetrics/  src/',
                                 volume=kctx.cwd + ':/app',
                                 user=os.getuid())

    if kitipy.utils.is_interactive():
        click.launch(os.path.join(kctx.cwd, '.phpmetrics/index.html'))


# @TODO: Add a method to confiure phpstan (e.g level)
@php_tools.task(filters=[is_tool_enabled('phpstan')])
def phpstan(kctx: kitipy.Context):
    """Run phpstan"""
    docker_actions.container_run('phpstan/phpstan',
                                 'analyse --level=7 -vvv src/',
                                 volume=kctx.cwd + ':/app')


@php_tools.task(filters=[is_tool_enabled('rector')])
def rector(kctx: kitipy.Context):
    """Run rector"""
    # @TODO: this is broken (image doesn't work)
    # @TODO: use confirm_and_apply to ask for confirmation before applying any changes (if possible)
    docker_actions.container_run(
        'rector/rector',
        'process -vvv /app --config=/app/rector.yaml --autoload-file=/app/vendor/autoload.php',
        volume=kctx.cwd + ':/app')
