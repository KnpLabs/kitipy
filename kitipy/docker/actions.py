import subprocess
from ..context import get_current_executor
from ..utils import append_cmd_flags
from typing import Any, Dict, List, Sequence, Union


def network_ls(_pipe: bool = False, _check: bool = True,
               **kwargs) -> subprocess.CompletedProcess:
    """Run `docker network ls` through kitipy executor.

    Args:
        **kwargs:
            Takes any CLI flag accepted by `docker network ls`.
        _pipe (bool):
            Whether executor pipe mode should be enabled.
        _check (bool):
            Whether the exit code of the subprocess should be checked.
    
    Raises:
        subprocess.SubprocessError: When check mode is enabled and the
            subprocess fails.
    
    Returns:
        :class:`subprocess.CompletedProcess`: When the subprocess is successful or check
            mode is disabled.
    """
    exec = get_current_executor()
    cmd = append_cmd_flags("docker network ls", **kwargs)
    return exec.run(cmd, pipe=_pipe, check=_check)


def network_create(name: str,
                   _pipe: bool = False,
                   _check: bool = True,
                   **kwargs) -> subprocess.CompletedProcess:
    """Run `docker network create` through kitipy executor.

    Args:
        name (str):
            Name of the network.
        **kwargs:
            Takes any CLI flag accepted by `docker network create`.
        _pipe (bool):
            Whether executor pipe mode should be enabled.
        _check (bool):
            Whether the exit code of the subprocess should be checked.
    
    Raises:
        subprocess.SubprocessError: When check mode is enabled and the
            subprocess fails.
    
    Returns:
        :class:`subprocess.CompletedProcess`: When the subprocess is successful or check
            mode is disabled.
    """
    exec = get_current_executor()
    cmd = append_cmd_flags("docker network create", **kwargs)
    return exec.run("%s %s" % (cmd, name), pipe=_pipe, check=_check)


def secret_create(name: str,
                  file: str,
                  _pipe: bool = False,
                  _check: bool = True,
                  **kwargs) -> subprocess.CompletedProcess:
    """Run `docker secret create` through kitipy executor. When executed
    through a remote executor, kitipy looks for the given secret file on the
    local computer and never uploads it to the remote target; instead, the file
    is piped through the standard input stream of the SSH connection.
    
    If the secret file contains a newline character (\\n or \\r\\n or \\r) at the
    end of the file it trims it automatically.

    Args:
        name (str):
            Name of the secret.
        file (str):
            File path of the secret file.
        **kwargs:
            Takes any CLI flag accepted by `docker secret create`.
        _pipe (bool):
            Whether executor pipe mode should be enabled.
        _check (bool):
            Whether the exit code of the subprocess should be checked.
    
    Raises:
        subprocess.SubprocessError: When check mode is enabled and the
            subprocess fails.
    
    Returns:
        :class:`subprocess.CompletedProcess`: When the subprocess is successful or check
            mode is disabled.
    """
    exec = get_current_executor()
    cmd = append_cmd_flags('docker secret create', **kwargs)

    with open(file, 'r') as f:
        secret = f.read().rstrip('\r\n')
        return exec.run('%s %s -' % (cmd, name),
                        input=secret,
                        pipe=_pipe,
                        check=_check)


def buildx_imagetools_inspect(image: str,
                              _pipe: bool = False,
                              _check: bool = True,
                              **kwargs) -> subprocess.CompletedProcess:
    """Run `docker buildx imagetools inspect` through kitipy executor. This is
    useful to test if the given image exists on a remote repo.
    
    Note that you need docker v19.03+ or you need to install docker-buildx
    plugin manually. This function won't test if buildx is available first.

    Args:
        image (str):
            The image to check. This should be prefixed with the image repo or
            default image repo is used (docker.io).
        **kwargs:
            Takes any CLI flag accepted by `docker buildx imagetools inspect`.
        _pipe (bool):
            Whether executor pipe mode should be enabled.
        _check (bool):
            Whether the exit code of the subprocess should be checked.
    
    Raises:
        subprocess.SubprocessError: When check mode is enabled and the
            subprocess fails.
    
    Returns:
        :class:`subprocess.CompletedProcess`: When the subprocess is successful or check
            mode is disabled.
    """
    exec = get_current_executor()
    cmd = append_cmd_flags('docker buildx imagetools inspect %s' % (image),
                           **kwargs)
    return exec.run("%s >/dev/null 2>&1" % (cmd), pipe=_pipe, check=_check)


def container_ps(_pipe: bool = False, _check: bool = True,
                 **kwargs) -> subprocess.CompletedProcess:
    """Run `docker container ps` (equivalent to `docker ps`) through kitipy
    executor.

    Args:
        **kwargs:
            Takes any CLI Flag accepted by `docker ps`.
        _pipe (bool):
            Whether executor pipe mode should be enabled.
        _check (bool):
            Whether the exit code of the docker run command should be checked.
    
    Raises:
        subprocess.SubprocessError: When check mode is enabled and the
            subprocess fails.
    
    Returns:
        :class:`subprocess.CompletedProcess`: When the process is successful or check
            mode is disabled.
    """
    exec = get_current_executor()
    cmd = append_cmd_flags('docker container ps', **kwargs)
    return exec.run(cmd, pipe=_pipe, check=_check)


def container_run(image: str,
                  cmd: str,
                  _pipe: bool = False,
                  _check: bool = True,
                  **kwargs) -> subprocess.CompletedProcess:
    """Run `docker container run` (equivalent to `docker run`) through kitipy
    executor.
    
    Args:
        image (str):
            Name of the image to run.
        cmd (str):
            Command to run inside the container, with its args.
        **kwargs:
            Take any CLI flag accepted by `docker run`.
        _pipe (bool):
            Whether executor pipe mode should be enabled.
        _check (bool):
            Whether the exit code of the subprocess should be checked.
    
    Raises:
        subprocess.SubprocessError: When check mode is enabled and the
            subprocess fails.
    
    Returns:
        :class:`subprocess.CompletedProcess`: When the process is successful or check
            mode is disabled.
    """
    exec = get_current_executor()
    shcmd = append_cmd_flags('docker container run', **kwargs)
    shcmd += ' %s %s' % (image, cmd)
    return exec.run(shcmd, pipe=_pipe, check=_check)
