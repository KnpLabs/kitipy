import click
import importlib.util
import kitipy
import os.path
import subprocess
from typing import List, Optional, Tuple


def apidoc(kctx: kitipy.Context,
           pkg_basedir: str,
           exclude: List[str] = [],
           _pipe: bool = False,
           _check: bool = True,
           **kwargs):
    """Run sphinx-apidoc command."""
    cmd = kitipy.append_cmd_flags('sphinx-apidoc', **kwargs)
    cmd = '%s %s %s' % (cmd, pkg_basedir, ' '.join(exclude))
    return kctx.local(cmd, pipe=_pipe, check=_check)


def build(kctx: kitipy.Context,
          source_dir: str,
          build_dir: str,
          args: List[str] = [],
          _pipe: bool = False,
          _check: bool = True,
          _env: Optional[Tuple[str]] = None) -> subprocess.CompletedProcess:
    """Run sphinx-build command."""
    cmd = 'sphinx-build %s' % (' '.join(args))
    return kctx.local('%s %s %s' % (cmd, source_dir, build_dir),
                      pipe=_pipe,
                      check=_check,
                      env=_env)
