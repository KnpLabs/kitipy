import os
import subprocess
from ..context import Context, get_current_executor
from ..utils import append_cmd_flags
from typing import Any, Dict, List, Optional, Union


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


def network_inspect(networks: Union[str, List[str]],
                    _pipe: bool = False,
                    _check: bool = True,
                    **kwargs) -> subprocess.CompletedProcess:
    exec = get_current_executor()
    cmd = append_cmd_flags("docker network inspect", **kwargs)
    networks = [networks] if isinstance(networks, str) else networks
    return exec.run('%s %s' % (cmd, ' '.join(networks)),
                    pipe=_pipe,
                    check=_check)


def network_exists(name: str) -> bool:
    return network_inspect(name, _pipe=True, _check=False).returncode == 0


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


def buildx_build(context: str, **kwargs) -> subprocess.CompletedProcess:
    """Run `docker buildx build` (equivalent to `docker build`) through kitipy
    executor. Supported by Docker +19.03.3.

    Args:
        context (str):
            Root dir of the build context.
        **kwargs:
            Takes any CLI Flag accepted by `docker buildx build`.

    Raises:
        subprocess.SubprocessError: When the suprocess fails.

    Returns:
        :class:`subprocess.CompletedProcess`: When the process is successful.
    """
    exec = get_current_executor()
    cmd = append_cmd_flags('docker buildx build', **kwargs)
    env = os.environ.copy()
    env.update({"DOCKER_BUILDKIT": "1", "DOCKER_CLI_EXPERIMENTAL": "enabled"})
    return exec.local(cmd + ' ' + context, env=env)


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
                  rm: bool = True,
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

            --rm flag is enabled by default.
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
    kwargs.setdefault('rm', True)
    shcmd = append_cmd_flags('docker container run', **kwargs)
    shcmd += ' %s %s' % (image, cmd)
    return exec.run(shcmd, pipe=_pipe, check=_check)


def get_service_labels(kctx: Context, service_name: str) -> List[str]:
    """
    Raises:
        KeyError: when service_name does not exist in this stack config.
    """
    services = kctx.stack.config.get('services', {})
    service = services[service_name]
    labels = service.get('labels', [])

    return normalize_labels(labels)


def normalize_labels(labels: Union[Dict[str, str], List[str]]) -> List[str]:
    if not isinstance(labels, dict):
        return labels

    return [k + "=" + v for k, v in labels.items()]


def find_service_label(kctx: Context,
                       service_name: str,
                       label: str,
                       one: bool = False) -> Union[None, str, List[str]]:
    pattern = label + "="
    plen = len(pattern)

    labels = get_service_labels(kctx, service_name)
    found = [label[plen:] for v in labels if v.startswith(pattern)]

    if one:
        return found[0] if len(found) > 0 else None

    return found


def find_default_shell(kctx: Context, service_name: str) -> Optional[str]:
    label = "io.kitipy.docker.default_shell"
    return kctx.stack.find_service_label(service_name, label, one=True)
