.. _best-practices:

Best practices
==============

Using a `ssh_config` file
-------------------------

kitipy forces the use of a `ssh_config` file to define the stage targets
hostnames, ports, usernames, etc...

As such, you're encouraged to commit, push and share a common file used by
everyone in your team, both dev and ops people. This is particularly useful to
share a common set of host aliases, so everyone speaks the same language.

If you're not sure how to name your hosts in your `ssh_config` file, we
recommend a reverse-back syntax, like: ``<project-name>.<stage>[.<region>].<vm-id>``.

For instance::

    Host foo.prod.vm1
        Hostname <ip-address>
        Port 2022
        User app
        ProxyCommand ssh -F .ssh/config -W %h:%p foo.prod.jumphost

Note that you can specify which `IdentityFile` to use in your own
`~/.ssh/config` file and you can inclue the versioned ``ssh_config`` file::

    Host foo.*
        IdentityFile ~/.ssh/id_rsa_foo
        Include ~/projects/github.com/KnpLabs/foo/.ssh/config

Naming conventions
------------------

By convention, the variable name used for :class:`kitipy.Context` instance is
``kctx``.

In the other hand, the variable name used for :class:`click.Context` instance
is ``click_ctx``.
