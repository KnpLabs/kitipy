import click
import json
import os
import subprocess
import yaml
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from ..context import Context
from ..executor import Executor
from ..utils import append_cmd_flags


class BaseStack(ABC):
    @property
    @abstractmethod
    def config(self):
        pass

    @abstractmethod
    def build(self, services: List[str] = [],
              **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def push(self,
             services: List[str] = [],
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def up(self, services: List[str] = [],
           **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def down(self, **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def restart(self,
                services: Optional[List[str]] = None,
                _pipe: bool = False,
                _check: bool = True,
                **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def ps(self,
           services: List[str] = [],
           _pipe: bool = True,
           _check: bool = False,
           **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def count_services(self, filter: Optional[Dict[str, str]] = None) -> int:
        pass

    @abstractmethod
    def logs(self, services: List[str] = [],
             **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def exec(self, service: str, cmd: str,
             **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def run(self, service: str, cmd: str,
            **kwargs) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def inspect(self, service: str, replica_id: int = 1,
                **kwargs) -> subprocess.CompletedProcess:
        pass


class ComposeStack(BaseStack):
    def __init__(self,
                 executor: Executor,
                 stack_name='',
                 file='',
                 basedir: str = None):
        self.name = stack_name
        self._executor = executor
        self._env = os.environ.copy()
        self._env.update({
            'COMPOSE_PROJECT_NAME': stack_name,
            'COMPOSE_FILE': file,
        })
        self._basedir = basedir
        self._loaded = False
        self._config = None

    @property
    def config(self):
        self._load_config()
        return self._config

    def _load_config(self):
        if self._loaded:
            return

        res = self._run('docker-compose config 2>/dev/null', pipe=True)
        self._config = yaml.safe_load(res.stdout)
        self._loaded = True

    def check_config(self) -> bool:
        res = self._run('docker-compose config 2>&1 1>/dev/null')
        return res.returncode == 0

    def build(self,
              services: List[str] = [],
              _pipe: bool = False,
              _check: bool = True,
              **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose build', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)),
                         pipe=_pipe,
                         check=_check)

    def push(self,
             services: List[str] = [],
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose push', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)),
                         pipe=_pipe,
                         check=_check)

    def up(self,
           services: List[str] = [],
           _pipe: bool = False,
           _check: bool = True,
           **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose up', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)),
                         pipe=_pipe,
                         check=_check)

    def down(self, _pipe: bool = False, _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose down', **kwargs)
        return self._run(cmd, pipe=_pipe, check=_check)

    def restart(self,
                services: Optional[List[str]] = None,
                _pipe: bool = False,
                _check: bool = True,
                **kwargs) -> subprocess.CompletedProcess:
        if services is None:
            services = []

        cmd = append_cmd_flags('docker-compose restart', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)),
                         pipe=_pipe,
                         check=_check)

    def ps(self,
           services: List[str] = [],
           _pipe: bool = True,
           _check: bool = False,
           **kwargs) -> subprocess.CompletedProcess:
        # docker-compose ps --filter doesn't work without --services
        # see https://github.com/docker/compose/issues/5996
        if 'filter' in kwargs and 'services' not in kwargs:
            kwargs['services'] = True

        cmd = append_cmd_flags('docker-compose ps', **kwargs)
        res = self._run('%s %s' % (cmd, ' '.join(services)),
                        pipe=_pipe,
                        check=_check)
        return res

    def count_services(self, filter: Optional[Dict[str, str]] = None) -> int:
        services = self.ps(filter=filter)
        return len(services.stdout.splitlines())

    def logs(self,
             services: List[str] = [],
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose logs', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)))

    def exec(self,
             service: str,
             cmd: str,
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        exec = append_cmd_flags('docker-compose exec', **kwargs)
        return self._run('%s %s %s' % (exec, service, ' '.join(cmd)),
                         pipe=_pipe,
                         check=_check)

    def run(self,
            service: str,
            cmd: str,
            _pipe: bool = False,
            _check: bool = True,
            **kwargs) -> subprocess.CompletedProcess:
        if len(kwargs) == 0:
            kwargs = {"rm": True}
        run = append_cmd_flags('docker-compose run', **kwargs)
        return self._run('%s %s %s' % (run, service, cmd),
                         pipe=_pipe,
                         check=_check)

    def inspect(self,
                service: str,
                replica_id: int = 1,
                _pipe: bool = False,
                _check: bool = True,
                **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker inspect', **kwargs)
        return self._run('%s %s_%s_%d' % (cmd, self.name, service, replica_id),
                         pipe=_pipe,
                         check=_check)

    def raw(self, args: List[str]):
        cmd = 'docker-compose %s' % (' '.join(args))
        return self._run(cmd)

    def get_ip_address(self, service: str, network: str):
        inspect = self.inspect(service)
        if inspect.returncode != 0:
            raise RuntimeError('Could not inspect service %s.' % (service))

        data = json.loads(inspect.stdout)
        return data[0]['NetworkSettings']['Networks'][network]['IPAddress']

    def _run(self, cmd: str, **kwargs) -> subprocess.CompletedProcess:
        kwargs.setdefault('env', self._env)
        kwargs.setdefault('cwd', self._basedir)
        return self._executor.run(cmd, **kwargs)


class SwarmStack(BaseStack):
    def __init__(self,
                 executor: Executor,
                 stack_name='',
                 file='',
                 basedir: str = None):
        self.name = stack_name
        self._executor = executor
        self._env = {
            'COMPOSE_FILE': file,
        }
        self._basedir = basedir
        self._loaded = False
        self._config = None

    @property
    def config(self):
        self._load_config()
        return self._config

    def _load_config(self):
        if self._loaded:
            return

        res = self._run('docker-compose config', pipe=True)
        self._config = yaml.safe_load(res.stdout)
        self._loaded = True

    def check_config(self):
        """Check if the config of this stack is valid.

        Raises:
            subprocess.SubprocessError: When the config is not valid.
        """
        # The only way to check if a swarm file is valid is actually to
        # load it through docker-compose and ignore the error messages about
        # swarm-specific parameters not supported by docker-compose.
        res = self._run(
            'docker-compose config 2>&1 1>/dev/null | grep -v " Compose does not support \'deploy\' configuration"'
        )

    def build(self,
              services: List[str] = [],
              _pipe: bool = False,
              _check: bool = True,
              **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose build', **kwargs)
        # Unfortunatly, the only way to build images from swarm files is to use
        # docker-compose, which complains about external secrets.
        cmd = '%s %s 2>&1 | grep -v "External secrets are not available"' % (
            cmd, ' '.join(services))
        return self._run(cmd, pipe=_pipe, check=_check)

    def push(self,
             services: List[str] = [],
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose push', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)),
                         pipe=_pipe,
                         check=_check)

    def up(self,
           services: List[str] = [],
           _pipe: bool = False,
           _check: bool = True,
           **kwargs) -> subprocess.CompletedProcess:
        kwargs.setdefault('resolve-image', 'never')
        kwargs.setdefault('prune', True)
        cmd = append_cmd_flags(
            'docker stack deploy -c %s ' % (self.config['file']), **kwargs)
        return self._run('%s %s' % (cmd, self.name), pipe=_pipe, check=_check)

    def down(self, _pipe: bool = False, _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker stack rm', **kwargs)
        return self._run('%s %s' % (cmd, self.name), pipe=_pipe, check=_check)

    def restart(self,
                services: Optional[List[str]] = None,
                _pipe: bool = False,
                _check: bool = True,
                **kwargs) -> subprocess.CompletedProcess:
        """
        Args:
            service (Optional[List[str]]):
                List of services to restart. All services are restarted when
                the list is None (the default value).

        Raises:
            subprocess.CalledProcessError: When it fails to restart services
                (unless _check is False).
        
        Returns
            :class:`subprocess.CompletedProcess`: When the process run successfully or
                when _check is False.
        """
        if services is None:
            services = self.config['services'].keys()

        kwargs['force'] = True
        basecmd = append_cmd_flags('docker service update ', **kwargs)
        cmd = []

        for svc in services:
            cmd.append('%s %s' % (basecmd, svc))

        return self._run(' && '.join(cmd), pipe=_pipe, check=_check)

    def ps(self,
           services: List[str] = [],
           _pipe: bool = True,
           _check: bool = False,
           **kwargs) -> subprocess.CompletedProcess:
        kwargs['filter'] = () if 'filter' not in kwargs else kwargs['filter']
        kwargs['filter'] += ('label=com.docker.stack.namespace=%s' %
                             (self.name))
        cmd = append_cmd_flags('docker service ls', **kwargs)
        res = self._run('%s %s' % (cmd, ' '.join(services)),
                        pipe=_pipe,
                        check=_check)
        return res

    def count_services(self, filter: Optional[Dict[str, str]] = None) -> int:
        services = self.ps(filter=filter)
        # @TODO: improve it (this is actually buggy)
        return len(services.stdout.splitlines())

    def logs(self,
             services: List[str] = [],
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker-compose logs', **kwargs)
        return self._run('%s %s' % (cmd, ' '.join(services)),
                         pipe=_pipe,
                         check=_check)

    def exec(self,
             service: str,
             cmd: str,
             _pipe: bool = False,
             _check: bool = True,
             **kwargs) -> subprocess.CompletedProcess:
        # @TODO: this is not going to work (docker-compose in SwarmStack)
        basecmd = append_cmd_flags('docker-compose exec', **kwargs)
        return self._run('%s %s %s' % (basecmd, service, ' '.join(cmd)),
                         pipe=_pipe,
                         check=_check)

    def run(self,
            service: str,
            cmd: str,
            _pipe: bool = False,
            _check: bool = True,
            **kwargs) -> subprocess.CompletedProcess:
        if len(kwargs) == 0:
            kwargs = {"rm": True}
        run = append_cmd_flags('docker-compose run', **kwargs)
        return self._run('%s %s %s' % (run, service, cmd),
                         pipe=_pipe,
                         check=_check)

    def inspect(self,
                service: str,
                replica_id: int = 1,
                _pipe: bool = False,
                _check: bool = True,
                **kwargs) -> subprocess.CompletedProcess:
        cmd = append_cmd_flags('docker inspect', **kwargs)
        # @TODO: this won't work properly
        return self._run('%s %s_%s_%d' % (cmd, self.name, service, replica_id),
                         pipe=_pipe,
                         check=_check)

    def _run(self, cmd: str, **kwargs) -> subprocess.CompletedProcess:
        """Execute a command using global env vars and basedir as default values.

        Args:
            cmd (str): Command to execute
            **kwargs: Extra arguments passed to Executor.run().

        Raises:
            subprocess.CalledProcessError: When the passed command fail to execute.

        Returns:
            :class:`subprocess.CompletedProcess`: 
        """
        kwargs.setdefault('env', self._env)
        kwargs.setdefault('cwd', self._basedir)
        return self._executor.run(cmd, **kwargs)


def load_stack(ctx: Context, stack_name: str):
    executor = ctx.executor
    config = ctx.config

    if 'stacks' not in config:
        raise click.BadParameter('No top key "stacks" found in the config.')

    stack_config = next(
        (v for k, v in config['stacks'].items() if k == stack_name), None)
    if stack_config is None:
        raise click.BadParameter('Stack %s not defined in the config.' %
                                 (stack_name))

    stack_path = stack_config.get('path', None)
    if stack_path is None:
        stack_path = os.getcwd()

    # @TODO: add templated stack filename
    # stack_file = stack_config.get('file', '%s.yml' % (stage))
    stack_file = stack_config.get('file', None)
    if stack_file is None:
        raise RuntimeError(
            'No property "file" found in the config of "%s" stack.' %
            (stack_config['name']))

    if 'swarm' in stack_config and stack_config['swarm']:
        return SwarmStack(executor,
                          stack_name=stack_name,
                          basedir=stack_path,
                          file=stack_file)

    return ComposeStack(executor,
                        stack_name=stack_name,
                        basedir=stack_path,
                        file=stack_file)
