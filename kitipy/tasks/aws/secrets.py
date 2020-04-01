"""This package provides two tasks for easily managing secrets stored through
AWS Secrets Manager.

It expects no particular stage configuration but it needs following stack
parameters:

* `secrets_resolver`: a function returning a list of secret ARNs ;
* `secret_arn_resolver`: a function that takes a kctx and a `secret_name`
  parameter and returns the ARN of that secret ;

@TODO:

* Add support for binary secrets ;
"""

import click
import kitipy
import kitipy.libs.aws.secretsmanager as sm

secret_delimiter = click.style('%', fg="black", bg="white")


def format_secret_value(val: str, show_value: bool):
    formatted = "(None)"
    if val != "" and show_value:
        formatted = val + secret_delimiter
    if val != "" and not show_value:
        formatted = "(%d characters)" % (len(val))

    return formatted


@kitipy.group()
def secrets():
    """Manage secrets stored by AWS Secrets Manager."""
    pass


@secrets.task()
@click.option("--show-values",
              default=False,
              help="Whether secret values should be disaplyed.")
def show(kctx: kitipy.Context, show_values: bool):
    """Show secrets stored by AWS Secrets Manager."""
    # @TODO: kctx.stack should be the raw config dict
    stack = kctx.config['stacks'][kctx.stack.name]
    secrets = stack['secrets_resolver'](kctx)

    kctx.echo(("NOTE: Secret values end with %s. This is here to help you " +
               "see invisible characters (e.g. whitespace, line breaks, " +
               "etc...).\n") % (secret_delimiter))

    client = sm.new_client()
    for secret_arn in secrets:
        secret = sm.describe_secret_with_current_value(client, secret_arn)
        kctx.echo("=================================")
        kctx.echo("ID: %s" % (secret["ARN"]))
        kctx.echo("Name: %s" % (secret["Name"]))
        kctx.echo("Value: %s\n" %
                  (format_secret_value(secret["SecretString"], show_values)))


@secrets.task()
@click.argument('secret-name', type=str, nargs=1)
def edit(kctx: kitipy.Context, secret_name):
    """Edit secrets stored by AWS Secrets Manager."""
    stack = kctx.config['stacks'][kctx.stack.name]
    secret_arn = stack['secret_arn_resolver'](kctx=kctx,
                                              secret_name=secret_name)
    client = sm.new_client()
    secret = sm.describe_secret_with_current_value(client, secret_arn)

    if secret == None:
        kctx.fail("Secret \"%s\" not found." % (secret_name))

    value = click.edit(text=secret['SecretString'])

    if value == None:
        kctx.info("Secret value was not changed. Aborting.")
        raise click.exceptions.Abort()

    trim_question = ("Your secret value ends with a new line. This is " +
                     "generally abnormal. Would you want to trim it " +
                     "automatically?")
    if value.endswith("\n") and click.confirm(trim_question, default=True):
        value = value.rstrip("\n")

    kctx.echo(("NOTE: Secret values end with %s. This is here to help you " +
               "see invisible characters (e.g. whitespace, line breaks, " +
               "etc...).\n") % (secret_delimiter))

    kctx.echo("ID: %s" % (secret["ARN"]))
    kctx.echo("Name: %s" % (secret["Name"]))
    kctx.echo("Previous value: %s" %
              (format_secret_value(secret["SecretString"], True)))
    kctx.echo("New value: %s" % (format_secret_value(value, True)))
    click.confirm("\nDo you confirm this change?", abort=True)

    sm.put_secret_value(client, secret["ARN"], value)
