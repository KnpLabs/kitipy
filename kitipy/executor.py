import click
import os.path
import paramiko
import random
import select
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import termios
import tty
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional, Tuple, Union
from .dispatcher import Dispatcher


class BaseExecutor(ABC):
    @abstractmethod
    def local(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            shell: bool = True,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def run(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            shell: bool = True,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def interactive(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        pass

    @abstractmethod
    def copy(self, local_path: str, remote_path: str):
        pass

    @abstractmethod
    def mkdtemp(self,
                suffix: Optional[str] = None,
                prefix: Optional[str] = None,
                dir: Optional[str] = None) -> str:
        pass

    @abstractmethod
    def cd(self, path: str):
        pass

    @abstractmethod
    def local_cd(self, path: str):
        pass

    @abstractmethod
    def path_exists(self, path: str) -> bool:
        pass

    @property
    @abstractmethod
    def is_local(self) -> bool:
        pass

    @property
    @abstractmethod
    def is_remote(self) -> bool:
        pass

    @property
    @abstractmethod
    def cwd(self) -> str:
        pass

    @property
    @abstractmethod
    def local_cwd(self) -> Optional[str]:
        pass


class Executor(BaseExecutor):
    """Executor provides a common abstraction to execute commands and
    manipulate files on both local computer and remote machines.

    It can be used either in local mode, when instantiated with no hostname, or
    in remote mode, when a hostname is provided. In remote mode, it uses a
    SSH/SFTP client to do its job. Remote connections are lazily opened when
    the first command is run or when the first file is copied.

    The SSH/SFTP connections are automatically closed when the executor got
    destroyed.
    """
    def __init__(self,
                 local_basedir: str,
                 remote_basedir: str,
                 dispatcher: Dispatcher,
                 hostname: Optional[str] = None,
                 ssh_config_file: str = '~/.ssh/config',
                 paramiko_config: Dict[str, Any] = {}):
        """
        Args:
            basedir (str):
                Base directory where commands should be executed. Most generally,
                for local executors, this is the current working directory. For 
                remote executors, this is generaly the base directory of your
                project.
            dispatcher (Dispatcher):
                Event dispatcher used to signal when file transfers start/end
                and signal how much data have been transfered during file
                uploads.
            hostname (Optional[str]):
                The SSH hostname (could be an alias) to connect to. Leave empty
                to use the Executor to local mode.
            ssh_config_file (str):
                Path to the OpenSSH client config file used by paramiko. This
                path could either be relative, absolute or startin with ~/.
            paramiko_config (Dict):
                These are extra parameters passed to paramiko when opening the
                SSH connection. This is useful to tweak paramiko-specific 
                parameters like look_for_key (which uses ~/.ssh/id_rsa if 
                other authentication mechanisms don't work).
        """
        self._ssh = None
        self._sftp = None
        self._local_basedir = local_basedir
        self._remote_basedir = remote_basedir
        self._dispatcher = dispatcher
        self._ssh_config = None  # type: Optional[Dict[str, str]]
        self._missing_host_key_policy = InteractiveWarningPolicy()

        if hostname is not None:
            self._load_ssh_config(hostname, ssh_config_file, paramiko_config)

    def __del__(self):
        """Close SSH/SFTP connections when the Executor is destroyed."""

        if self._ssh is not None:
            self._ssh.close()
        if self._sftp is not None:
            self._sftp.close()

    def _load_ssh_config(self, hostname: str, ssh_config_file: str,
                         paramiko_config: Dict[str, Any]):
        """Load the SSH config from an OpenSSH ssh_config file for a given host
        and prepare parameters used to open paramiko connection.

        Attrs:
            hostname (str):
                Hostname or host alias as defined in the ssh_config file.
            ssh_config_file (str):
                Path to the ssh_config file to load. This path can either be an
                absolute path or a relative path, in which case its relative
                root is the base directory of the ssh_config file.
            paramiko_config (Dict[str, Any]):
                Extra parameters to pass to paramiko when opening the
                connection. This is useful to change default paramiko behavior
                like disabling look_for_keys to not try ~/.ssh/id_rsa key by
                default.
        """
        ssh_config_path = os.path.expanduser(ssh_config_file)
        ssh_config = paramiko.SSHConfig()

        with open(ssh_config_path) as f:
            ssh_config.parse(f)

        host_config = ssh_config.lookup(hostname)
        # @TODO: accept only a subset of all paramiko args (or it might be used to overwrite stage-specific parameters).
        cfg = paramiko_config
        # The hostname parameter for paramiko is defined here but it might be
        # rewritten by the loop below if it's just an alias to another
        # hostname. For instance, if a ssh_file declares a host "foobar.prod"
        # server, kitipy will automatically load the config for "Host foobar.prod"
        # and rewrite the hostname below with the real Hostname of this alias,
        # as defined in the ssh_config file.
        cfg.update({'hostname': hostname, 'port': 22})

        for hk, ck in (('hostname', 'hostname'), ('user', 'username'),
                       ('port', 'port'), ('connecttimeout', 'timeout'),
                       ('compression', 'compress')):
            if hk in host_config:
                cfg[ck] = host_config[hk]

        if 'proxycommand' in host_config:
            cfg['sock'] = paramiko.ProxyCommand(host_config['proxycommand'])

        if 'identityfile' in host_config:
            cfg['key_filename'] = []

            for identity_file in host_config['identityfile']:
                if os.path.exists(identity_file):
                    cfg['key_filename'].append(identity_file)
                    continue

                # Relative identity files are resolved with the base directory
                # of the SSH config as their relative root.
                # @TODO: This is unusual (does not follow openssh behavior), maybe remove?
                if not os.path.isabs(identity_file):
                    key_basedir = os.path.dirname(ssh_config_path)
                    identity_file = os.path.join(key_basedir, identity_file)

                cfg['key_filename'].append(identity_file)

        self._ssh_config = cfg

    def set_missing_host_key_policy(self,
                                    policy: paramiko.MissingHostKeyPolicy):
        """Set the missing_host_key_policy used by paramiko when it stumbles
        upon a server with an unknown signature.

        This method has to be called before the first command is run or the
        first file is copied.

        Args:
            policy (paramiko.MissingHostKeyPolicy):
                The missing host key policy used by paramiko when a host key
                is not known by the running system.
        
        Raises:
            RuntimeError: When an SSH command or a SFTP file has been copied.
        """
        if self._ssh is not None:
            raise RuntimeError(
                "This method has to be called before any SSH or SFTP session is started."
            )

        self._missing_host_key_policy = policy

    # @TODO: manage private keys with passphrase
    @property
    def ssh(self) -> paramiko.SSHClient:
        """Get previously opened SSH connection or open it.

        Raises:
            RuntimeError: When the Executor is running in local mode.
            paramiko.SSHException: When it fails to open the connection.
        
        Returns:
            paramiko.SSHClient: The underlying SSH client
        """

        if self.is_local:
            raise RuntimeError(
                "No SSH connection available: this is a local executor.")

        if self._ssh == None:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(self._missing_host_key_policy)
            client.connect(**self._ssh_config)
            self._ssh = client

        return self._ssh

    @property
    def sftp(self) -> paramiko.SFTPClient:
        """Get previously opened SFTP connection or open it.

        Raises:
            RuntimeError: When the Executor is running in local mode.
            paramiko.SSHException: When it fails to open the connection.
        
        Returns:
            paramiko.SFTPClient: The underlying SFTP client
        """

        if self.is_local:
            raise RuntimeError(
                "No SFTP connection available: this is a local executor.")

        if self._sftp == None:
            # @TODO: test what happens when both ssh/sftp connections are open and executor got destroyed (does it fail to close both?)
            self._sftp = self.ssh.open_sftp()

        return self._sftp

    def local(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            shell: bool = True,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a command on local host.
        
        This method is particularly useful when you want to run some commands
        on local host whereas the Executor is running in remote mode. For
        instance, you might want to check if a given git tag or some Docker 
        images exists on a remote repository/registry before deploying it, 
        or you might want to fetch the local git author name to log deployment
        events somewhere. Such checks are generally better run locally.

        Args:
            cmd (str):
                Command and args to run.
            env (Optional[Dict[str, str]]):
                Env vars used to run the given cmd. When this is None (the
                default value) the subprocess will inherit its env vars from
                kitipy, so any env vars declared before running kitipy will be
                made available for the command.
            cwd (Optional[str]):
                Working directory where the command should be run. When this is
                None (the default value), the current working directory is used.
            shell (bool):
                Whether the command should be run in a shell (True by default).
            input (Optional[str]):
                Standard input of the subprocess.
            text (bool):
                Whether stdin/stdout/stderr streams should be converted from/into
                strings using encoding parameter or kept in binary format.
            encoding (Optional[str]):
                Determine the encoding used to convert streams from/to binary format.
            pipe (bool):
                Whether the subprocess output should be piped to kitipy and
                made available through the returned subprocess.CompletedProcess
                (when True), or outputted to kitipy stdout/stderr (when False).
                This is similar to Popen(..., stdout=PIPE, stderr=PIPE).
            check (bool):
                Check if the executed command returns exit code 0 or raise an
                error otherwise.
        Raises:
            subprocess.SubprocessError: When check mode is enable and the
                command returns an exit code > 0.
        
        Returns:
            subprocess.CompletedProcess
        """
        cwd = cwd or self._local_basedir

        res = subprocess.run(cmd,
                             env=env,
                             cwd=cwd,
                             shell=shell,
                             input=input,
                             text=text,
                             encoding=encoding,
                             stdout=subprocess.PIPE if pipe else None,
                             stderr=subprocess.PIPE if pipe else None,
                             check=check)

        # Take care of initializing stdout/stderr to avoid dumb bugs. Also this
        # is consistent with _remote() behavior (return empty strings when the
        # command fail and check is False).
        res.stdout = res.stdout if res.stdout else ''
        res.stderr = res.stderr if res.stderr else ''

        return res

    def _remote(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a command on remote host.

        Args:
            cmd (str):
                Command and args to run
            env (Dict[str, str]):
                Env vars used to run the given cmd.
            cwd (Optional[str]):
                Working directory where the command should be run. When this is
                None (the default value), the current working directory is used.
            input (Optional[str]):
                If passed, it's written to command stdin.
            text (bool):
                Whether stdin/stdout/stderr streams should be converted from/into
                strings using encoding parameter or kept in binary format.
            encoding (Optional[str]):
                Determine the encoding used to convert streams from/to binary format.
            pipe (bool):
                Whether the subprocess output should be piped to kitipy and
                made available through the returned subprocess.CompletedProcess
                (when True), or outputted to kitipy stdout/stderr (when False).
                This is similar to Popen(..., stdout=PIPE, stderr=PIPE).
            check (bool):
                Check if the executed command returns exit code 0 or raise an
                error otherwise.

        Raises:
            RuntimeError: When the Executor is running in local mode.
            
            paramiko.SSHException:
                When the SSH client fail to run the command. Note that this
                won't be raised when the command could not be found or it
                exits with code > 0 though, but only when something fails at
                the SSH client/server lower level.
        
        Returns:
            subprocess.CompletedProcess
        """
        if not self.is_remote:
            raise RuntimeError(
                'This Executor is running in local mode, could not run following command: %s'
                % (cmd))

        cwd = cwd or self._remote_basedir
        if cwd:
            self.ssh.exec_command('cd ' + cwd)

        sin, sout, serr = self.ssh.exec_command(cmd, environment=env)
        channel = sin.channel

        if input is not None:
            sin.write(input)

        # We don't need stdin anymore, close it
        sin.close()
        channel.shutdown_write()

        encoding = encoding if encoding else sys.getdefaultencoding()
        stdout, stderr = self._read_ssh_chunks(channel, text, encoding, pipe)
        while not channel.closed or channel.recv_ready(
        ) or channel.recv_stderr_ready():
            rlist, _, _ = select.select([channel], [], [])

            if len(rlist) > 0:
                chunks = self._read_ssh_chunks(channel, text, encoding, pipe)
                stdout += chunks[0]  # type: ignore
                stderr += chunks[1]  # type: ignore

            if channel.exit_status_ready():
                chunks = self._read_ssh_chunks(channel, text, encoding, pipe,
                                               True)
                stdout += chunks[0]  # type: ignore
                stderr += chunks[1]  # type: ignore

                channel.shutdown_read()
                channel.close()

                break

        sout.close()
        serr.close()

        returncode = sin.channel.recv_exit_status()
        completed = subprocess.CompletedProcess(cmd, returncode, stdout,
                                                stderr)
        if check:
            completed.check_returncode()

        return completed

    def _read_ssh_chunks(
            self,
            channel: paramiko.channel.Channel,
            text: bool,
            encoding: str,
            pipe: bool,
            empty_buffer: bool = False,
    ) -> Tuple[Union[bytes, str], Union[bytes, str]]:
        stdout_chunk = b''  # type: Union[bytes, str]
        stderr_chunk = b''  # type: Union[bytes, str]

        if channel.recv_ready():
            if empty_buffer:
                stdout_chunk = channel.in_buffer.empty()
            else:
                stdout_chunk = channel.recv(len(channel.in_buffer))
        if channel.recv_stderr_ready():
            if empty_buffer:
                stderr_chunk = channel.in_stderr_buffer.empty()
            else:
                stderr_chunk = channel.recv(len(channel.in_stderr_buffer))

        if text:
            stdout_chunk = stdout_chunk.decode(encoding)  # type: ignore
            stderr_chunk = stderr_chunk.decode(encoding)  # type: ignore

        if not pipe:
            print(stdout_chunk, file=sys.stdout, end='')
            print(stderr_chunk, file=sys.stderr, end='')
            stdout_chunk = ''
            stderr_chunk = ''

        return (stdout_chunk, stderr_chunk)

    # @TODO: cmd signature have to be changed to accept list too (due to shell opts)
    def run(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            shell: bool = True,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        """This method is the way to ubiquitously run a command on either local
        or remote target, depending on how the executor was set. More precisely, 
        it checks if there's a remote hostname set on the executor to know
        whether the command should be locally or remotely.

        Args:
            cmd (str):
                Command and args to run
            env (Optional[Dict[str, str]]):
                Env vars used to run the given cmd. When this is None (the
                default value) and the Executor is running in local mode, the
                subprocess will inherit its env vars from kitipy, so any env
                vars declared before running kitipy will be made available for
                the command.
            cwd (Optional[str]):
                Working directory where the command should be run. When this is
                None (the default value), in local mode, the current working
                directory is used and in remote mode, the user home directory
                is used.
            shell (bool):
                Whether the command should be run in a shell (True by default).
            input (Optional[str]):
                Standard input of the subprocess.
            text (bool):
                Whether stdin/stdout/stderr streams should be converted from/into
                strings using encoding parameter or kept in binary format.
            encoding (Optional[str]):
                Determine the encoding used to convert streams from/to binary format.
            pipe (bool):
                Whether the subprocess output should be piped to kitipy and
                made available through the returned subprocess.CompletedProcess
                (when True), or outputted to kitipy stdout/stderr (when False).
                This is similar to Popen(..., stdout=PIPE, stderr=PIPE).
            check (bool):
                Check if the executed command returns exit code 0 or raise an
                error otherwise.
        Raises:
            RuntimeError: When the Executor is running in local mode.
            
            paramiko.SSHException:
                When the SSH client fail to run the command. Note that this
                won't be raised when the command could not be found or it
                exits with code > 0 though, but only when something fails at
                the SSH client/server lower level.
        
        Returns:
            subprocess.CompletedProcess
        """
        if self.is_remote:
            return self._remote(cmd,
                                env=env,
                                input=input,
                                text=text,
                                encoding=encoding,
                                pipe=pipe,
                                check=check)

        return self.local(cmd,
                          env=env,
                          cwd=cwd,
                          shell=shell,
                          input=input,
                          text=text,
                          encoding=encoding,
                          pipe=pipe,
                          check=check)

    def interactive(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        if not self.is_remote:
            return self.local(cmd, env=env, cwd=cwd, check=check)

        cwd = cwd or self._remote_basedir
        if cwd:
            cmd = 'cd %s; %s' % (cwd, cmd)

        oldtty = termios.tcgetattr(sys.stdin)

        sin, sout, serr = self.ssh.exec_command(cmd, environment=env)
        channel = sin.channel
        encoding = sys.getdefaultencoding()

        try:
            tty.setraw(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
            channel.settimeout(0.0)

            while True:
                r, w, e = select.select([channel, sys.stdin], [], [])
                if channel in r:
                    try:
                        x = channel.recv(1024).decode(encoding)
                        if len(x) == 0:
                            break
                        sys.stdout.write(x)
                        sys.stdout.flush()
                    except socket.timeout:
                        pass
                if sys.stdin in r:
                    x = sys.stdin.read(1)
                    if len(x) == 0:
                        break
                    channel.send(x)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)

        returncode = sin.channel.recv_exit_status()
        completed = subprocess.CompletedProcess(cmd, returncode, '', '')
        if check:
            completed.check_returncode()

        return completed

    def copy(self, local_path: str, remote_path: str):
        """This method transfers files from your computer to a remote target.

        This method uses the dispatcher to emit events in order to let the UI
        display what's going on.

        Args:
            local_path (str): Path to the file to transfer.
            remote_path (str): Destination path on the remote target.
        """
        local_fullpath = local_path
        remote_fullpath = remote_path
        if not os.path.isabs(local_fullpath) and self._local_basedir:
            local_fullpath = os.path.join(self._local_basedir, local_fullpath)
        if not os.path.isabs(remote_fullpath) and self._remote_basedir:
            remote_fullpath = os.path.join(self._remote_basedir,
                                           remote_fullpath)

        if self.is_local:
            shutil.copy(local_fullpath, remote_fullpath)
            return

        size = os.path.getsize(local_path)
        label = "Transfer %s to %s" % (local_path, remote_path)
        self._dispatcher.emit('file_transfer.start', size=size, label=label)

        fn = lambda current, total: self._dispatcher.emit(
            'file_transfer.update', current=current, total=total)

        try:
            self.sftp.put(local_fullpath, remote_fullpath, callback=fn)
        finally:
            self._dispatcher.emit('file_transfer.end')

    def mkdtemp(self,
                suffix: Optional[str] = None,
                prefix: Optional[str] = None,
                dir: Optional[str] = None) -> str:
        """Creates a temporary file with a unique name. It runs tempfile.mkdtemp()
        in local mode and uses `mktemp -d` in remote mode. It's the caller
        responsibility to clean up this directory when it's not used anymore.

        Args:
            suffix (Optional[str]):
                Suffix of the temporary filename.
            prefix (Optional[str]):
                Prefix of the temporary filename.
            dir (Optional[str]):
                Directory where the temporary file should be created. This 
                defaults to the default temporary directory in local mode and
                to `/tmp` in remote mode.
        
        Raises:
            paramiko.SSHException: When running in remote mode and the command fails.

        Returns:
            str: The path to the temporary directory.
        """
        if self.is_local:
            return tempfile.mkdtemp(suffix, prefix, dir)

        prefix = prefix or ''
        suffix = suffix or ''
        filename_tpl = os.path.join('/tmp', prefix + 'XXXXXXXX' + suffix)
        res = self._remote("mktemp -d %s" % (filename_tpl))
        return res.stdout

    def cd(self, path: str):
        if self.is_remote:
            return self._remote_cd(path)
        return self.local_cd(path)

    @contextmanager
    def local_cd(self, path: str):
        if not os.path.isabs(path):
            # @TODO: the original basedir set in the constructor should be resolved
            # into absolute path in some way or another
            path = self._join_paths(self._local_basedir, path)

        previous_basedir = self._local_basedir
        try:
            self._local_basedir = path
            yield None
        finally:
            self._local_basedir = previous_basedir

    @contextmanager
    def _remote_cd(self, path: str):
        if not os.path.isabs(path):
            # @TODO: the original basedir set in the constructor should be resolved
            # into absolute path in some way or another
            path = self._join_paths(self._remote_basedir, path)

        previous_basedir = self._remote_basedir
        try:
            self._remote_basedir = path
            yield None
        finally:
            self._remote_basedir = previous_basedir

    # @TODO: Switch to pathlib to better manage paths?
    def _join_paths(self, *paths: Optional[str]):
        parts = [path for path in paths if path]
        return os.path.join(*parts)

    def path_exists(self, path: str) -> bool:
        """Check if the given path exists. In local mode, it uses
        `os.path.exists` and `ls` in remote mode.
        """
        if self.is_local:
            return os.path.exists(path)

        res = self._remote("ls %s 1>/dev/null 2>&1" % (path), check=False)
        return res.returncode == 0

    @property
    def is_local(self) -> bool:
        return self._ssh_config is None

    @property
    def is_remote(self) -> bool:
        return self._ssh_config is not None

    @property
    def cwd(self) -> str:
        if self.is_remote:
            return self._remote_basedir
        return self._local_basedir

    @property
    def local_cwd(self) -> Optional[str]:
        return self._local_basedir


def _create_executor(config: Dict, stage_name: str,
                     dispatcher: Dispatcher) -> Executor:
    """Instantiate a new executor for the given stage.

    Args:
        config (Dict):
            The whole kitipy config.
        stage_name (str):
            The name of the stage to instantiate an Executor for.
        dispatcher (Dispatcher):
            The dispatcher later used by the instantiated Executor.
    """

    stage = config['stages'][stage_name]

    if stage.get('type', None) not in ('remote', 'local'):
        raise click.BadParameter(
            'Stage "%s" has no "type" field or its value is invalid (should be either: local or remote).'
            % (stage_name))

    local_basedir = stage.get('local_basedir', os.getcwd())
    remote_basedir = stage.get('remote_basedir', '~/')

    if stage['type'] == 'local':
        return Executor(local_basedir, remote_basedir, dispatcher)

    if 'hostname' not in stage:
        raise click.BadParameter(
            'Remote stage "%s" has no hostname field defined.' % (stage))

    # @TODO: verify and explain better all the mess around basedir/cwd
    basedir = stage.get('basedir')
    params = {
        'hostname': stage['hostname'],
    }

    if 'ssh_config' in config:
        params['ssh_config_file'] = config['ssh_config']
    if 'paramiko_config' in config:
        # @TODO: we shouldn't be that much permissive with paramiko config
        params['paramiko_config'] = config['paramiko_config']

    return Executor(local_basedir, remote_basedir, dispatcher, **params)


class InteractiveWarningPolicy(paramiko.MissingHostKeyPolicy):
    """InteractiveWarningPolicy implements a paramiko MissingHostKeyPolicy
    that uses click.confirmation() helper to ask for confirmation when a new
    host_key is detected. This is the default paramiko MissingHostKeyPolicy
    used by kitipy.
    """
    def missing_host_key(self, client, hostname, key):
        confirm_msg = "WARNING: Host key for %s not found (%s). Do you want to add it to your ~/.ssh/known_hosts?" % (
            hostname, key)

        if not click.confirm(confirm_msg):
            raise RuntimeError("Unknown host key for %s." % (hostname))

        client._host_keys.add(hostname, key.get_name(), key)
        if client._host_keys_filename is not None:
            client.save_host_keys(client._host_keys_filename)


class ProxyExecutor(BaseExecutor):
    def __init__(self, executor: BaseExecutor):
        self._executor = executor

    def local(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            shell: bool = True,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        return self._executor.local(cmd, env, cwd, shell, input, text,
                                    encoding, pipe, check)

    def run(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            shell: bool = True,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            pipe: bool = False,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        return self._executor.run(cmd, env, cwd, shell, input, text, encoding,
                                  pipe, check)

    def interactive(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            check: bool = True,
    ) -> subprocess.CompletedProcess:
        return self._executor.interactive(cmd, env, cwd, check)

    def copy(self, local_path: str, remote_path: str):
        return self._executor.copy(local_path, remote_path)

    def mkdtemp(self,
                suffix: Optional[str] = None,
                prefix: Optional[str] = None,
                dir: Optional[str] = None) -> str:
        return self._executor.mkdtemp(suffix, prefix, dir)

    def cd(self, path: str):
        return self._executor.cd(path)

    def local_cd(self, path: str):
        return self._executor.local_cd(path)

    def path_exists(self, path: str) -> bool:
        return self._executor.path_exists(path)

    @property
    def is_local(self) -> bool:
        return self._executor.is_local

    @property
    def is_remote(self) -> bool:
        return self._executor.is_remote

    @property
    def cwd(self) -> str:
        return self._executor.cwd

    @property
    def local_cwd(self) -> Optional[str]:
        return self._executor.local_cwd
