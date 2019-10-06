from kitipy import Context
import os
from typing import Optional, Tuple


def galaxy_install(kctx: Context,
                   dest: str,
                   file: str = 'galaxy-requirements.yml',
                   force: bool = False):
    """Install Ansible dependencies using ``ansible-galaxy install``.

    Args:
        kctx (kitipy.Context):
            Context to use to run ``ansible-galaxy``.
        dest (str):
            Directory path where dependencies should be installed.
        file (str):
            Requirements file (defaults to ``galaxy-requirements.yml``).
        force (bool):
            Whehter ``--force`` flag should be added to ``ansible-galaxy`` command.
    """
    kctx.run('ansible-galaxy install -r %s -p %s %s' % (
        file,
        dest,
        '--force' if force else '',
    ))


def run_playbook(kctx: Context,
                 inventory: str,
                 playbook: str,
                 hosts: Optional[Tuple[str]] = None,
                 tags: Optional[Tuple[str]] = None,
                 ask_become_pass: bool = False):
    """Run a given Ansible playbook using ``ansible-playbook``.

    Args:
        kctx (kitipy.Context):
            Context to use to run the playbook.
        inventory (str):
            Path to Ansible host inventory.
        playbook (str):
            Path to the Ansible playbook to run.
        hosts (Optional[Tuple[str]]):
            List of targeted hosts. Use None to target all hosts (default value).
        tags (Optional[Tuple[str]]):
            List of targeted tags. Use None to apply all the tags (default value).
        ask_become_pass (bool):
            Whether ``--ask-become-pass`` should be added to the ``ansible-playbook``
            command.
    """
    cmd = 'ansible-playbook -i %s' % (inventory)
    if hosts is not None and len(hosts) > 0:
        cmd += ' -l ' + ','.join(hosts)
    if tags is not None and len(tags) > 0:
        cmd += ' -t ' + ','.join(tags)
    if ask_become_pass:
        cmd += ' --ask-become-pass'

    cmd += ' ' + playbook

    kctx.run(cmd)
