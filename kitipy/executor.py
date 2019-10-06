import click
import os.path
import paramiko
import random
import string
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional
from .dispatcher import Dispatcher


class Executor(object):
    """Executor provides a common abstraction to ubiquitously run commands and
    manipulate files on both local computer and remote machines.

    It can be used either in local mode, when instantiated with no hostname, or
    in remote mode, when a hostname is provided.
    
    In remote mode, it uses a SSH/SFTP client to do its job. Remote connections
    are lazily opened when the first command is run or when the first file is
    copied. The SSH/SFTP connections are automatically closed when the executor got
    destroyed.
    """
    def __init__(self,
                 basedir: str,
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
                to use the Executor in local mode.
            ssh_config_file (str):
                Path to the OpenSSH client config file used by paramiko. This
                path could either be relative, absolute or startin with ~/.
            paramiko_config (Dict):
                These are extra parameters passed to paramiko when opening the
                SSH connection. This is useful to tweak paramiko-specific 
                parameters like ``look_for_key`` (which uses ~/.ssh/id_rsa if 
                other authentication mechanisms don't work).
        """
        self._ssh = None
        self._sftp = None
        self._basedir = basedir
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
                like disabling look_for_keys to not try ``~/.ssh/id_rsa`` key
                by default.
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
                if not os.path.isabs(identity_file):
                    key_basedir = os.path.dirname(ssh_config_path)
                    identity_file = os.path.join(key_basedir, identity_file)

                cfg['key_filename'].append(identity_file)

        self._ssh_config = cfg

    def set_missing_host_key_policy(self,
                                    policy: paramiko.MissingHostKeyPolicy):
        """Set the ``missing_host_key_policy`` used by paramiko when it
        stumbles upon a server with an unknown signature.

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
            paramiko.ssh_exception.SSHException: When it fails to open the connection.
        
        Returns:
            paramiko.client.SSHClient: The underlying SSH client
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
            paramiko.ssh_exception.SSHException: When it fails to open the connection.
        
        Returns:
            paramiko.sftp_client.SFTPClient: The underlying SFTP client.
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
        on local host whereas the Executor is running in remote mode. 
        
        For instance, you might want to check if a given git tag or some Docker
        images exists on a remote repository/registry before deploying them, 
        or you might want to fetch the local git author name to log deployment
        events somewhere. Such checks are generally better run locally.

        Args:
            cmd (str):
                Command and args to run.
            env (Optional[Dict[str, str]]):
                Env vars used to run the given ``cmd``. When this is ``None``
                (the default value) the subprocess will inherit its env vars
                from kitipy, so any env vars declared before running kitipy
                will be made available to the command.
            cwd (Optional[str]):
                Working directory where the command should be run. When this is
                ``None`` (the default value), the current working directory is
                used.
            shell (bool):
                Whether the command should be run in a shell (``True`` by
                default).
            input (Optional[str]):
                Standard input of the subprocess.
            text (bool):
                Whether ``stdin``, ``stdout`` and ``stderr`` streams should be
                converted from/into strings using encoding parameter or kept in
                binary format.
            encoding (Optional[str]):
                Determine the encoding used to convert streams from/to binary format.
            pipe (bool):
				Whether the subprocess output should be piped to kitipy and
				made available through the returned :class:`subprocess.CompletedProcess`
				(when ``True``), or outputted to kitipy ``stdout``/``stderr``
                (when ``False``, the default value).
				This is similar to :code:`subprocess.Popen('...', pipe=True)`.
            check (bool):
                Check if the executed command returns exit code 0 or raise an
                error otherwise.
        Raises:
            subprocess.SubprocessError: When check mode is enabled and the
                command returns an exit code > 0.
        
        Returns:
            :class:`subprocess.CompletedProcess`
        """
        cwd = cwd or self._basedir

        return subprocess.run(cmd,
                              env=env,
                              cwd=cwd,
                              shell=shell,
                              input=input,
                              text=text,
                              encoding=encoding,
                              stdout=subprocess.PIPE if pipe else None,
                              stderr=subprocess.PIPE if pipe else None,
                              check=check)

    # @TODO: emulate pipe/nopipe behavior for remote mode.
    def _remote(
            self,
            cmd: str,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None,
            input: Optional[str] = None,
            text: bool = True,
            encoding: Optional[str] = None,
            check: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command on remote host.

        Args:
            cmd (str):
                Command and args to run
            env (Optional[Dict[str, str]]):
                Env vars used to run the given ``cmd``. When this is ``None``
                (the default value), the default env vars set by the remote
                shell will be used.
            cwd (Optional[str]):
                Working directory where the command should be run. When this is
                ``None`` (the default value), the current working directory is
                used.
            input (Optional[str]):
                If passed, it's written to command stdin.
            text (bool):
                Whether ``stdin``, ``stdout`` and ``stderr`` streams should be
                converted from/into strings using encoding parameter or kept in
                binary format.
            encoding (Optional[str]):
                Determine the encoding used to convert streams from/to binary format.
            check (bool):
                Check if the executed command returns exit code 0 or raise an
                error otherwise.
        Raises:
            RuntimeError: When the Executor is running in local mode.
            
            paramiko.ssh_exception.SSHException:
                When the SSH client fail to run the command. Note that this
                won't be raised when the command could not be found or it
                exits with code > 0 though, but only when something fails at
                the SSH client/server lower level.
            
            subprocess.SubprocessError: When check mode is enabled and the
                command returns an exit code > 0.
        
        Returns:
            :class:`subprocess.CompletedProcess`
        """
        cwd = cwd or self._basedir

        if not self.is_remote:
            raise RuntimeError(
                'This Executor is running in local mode, could not run following command: %s'
                % (cmd))

        self.ssh.exec_command('cd ' + cwd)

        streams = self.ssh.exec_command(cmd, environment=env)

        if input is not None:
            streams[0].write(input)

        # Following line is blocking until the remote process has ended. We can
        # then retrieve the exit code and fully read stdout/stderr.
        returncode = streams[0].channel.recv_exit_status()
        out = streams[1].read()
        err = streams[2].read()

        if text:
            if encoding is None:
                encoding = sys.getdefaultencoding()
            out = out.decode(encoding)
            err = err.decode(encoding)

        completed = subprocess.CompletedProcess(cmd, returncode, out, err)
        if check:
            completed.check_returncode()

        return completed

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
        or remote target, depending on how the Executor was set. More precisely, 
        it checks if there's a remote hostname set on the executor to know
        whether the command should be locally or remotely.

        Args:
            cmd (str):
                Command and args to run
            env (Optional[Dict[str, str]]):
                Env vars used to run the given ``cmd``.
                
                In local mode, when this is ``None`` (the default value) the
                subprocess will inherit its env vars from kitipy, so any env
                vars declared before running kitipy will be made available to
                the command.

                In remote mode, when this is ``None``, the default env vars set
                by the remote shell will be used.
            cwd (Optional[str]):
                Working directory where the command should be run.
                
                In local mode, when this is ``None`` (the default value), the
                current working directory is used.

                In remote mode, when this is ``None``, the default login
                directory (usually the user home directory) will be used.
            shell (bool):
                Whether the command should be run in a shell (``True`` by
                default). Note that in remote mode, this parameter has no
                effect (all the commands run inside a shell).
            input (Optional[str]):
                Standard input of the subprocess.
            text (bool):
                Whether ``stdin``, ``stdout`` and ``stderr`` streams should be
                converted from/into strings using encoding parameter or kept in
                binary format.
            encoding (Optional[str]):
                Determine the encoding used to convert streams from/to binary format.
            pipe (bool):
				Whether the subprocess output should be piped to kitipy and
				made available through the returned :class:`subprocess.CompletedProcess`
				(when ``True``), or outputted to kitipy ``stdout``/``stderr``
                (when ``False``, the default value).
				This is similar to :code:`subprocess.Popen('...', pipe=True)`.
            check (bool):
                Check if the executed command returns exit code 0 or raise an
                error otherwise.
        Raises:
            paramiko.ssh_exception.SSHException:
                When the SSH client fail to run the command. Note that this
                won't be raised when the command could not be found or it
                exits with code > 0 though, but only when something fails at
                the SSH client/server lower level.
            
            subprocess.SubprocessError: When check mode is enabled and the
                command returns an exit code > 0.
        
        Returns:
            :class:`subprocess.CompletedProcess`
        """
        if self.is_remote:
            return self._remote(cmd,
                                env=env,
                                input=input,
                                text=text,
                                encoding=encoding,
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

    def copy(self, local_path: str, remote_path: str):
        """This method transfers files from your computer to a remote target.
        It does nothing when executed in local mode.

        This method uses the dispatcher to emit following events in order to
        let the UI display what's going on:
        
            * ``file_transfer.start``: With total ``size`` and ``label``
              parameters.
            * ``file_transfer.update``: With the ``current`` transfered size
              and ``total`` size.
            * ``file_transfer.end``: When the file transfers ends or when an
              exception is raised.

        These are handled by listeners defined by :func:`kitipy.set_up_file_transfer_listeners`, 
        which is called by :class:`kitipy.RootCommand` constructor. Thus, you
        generally don't need to handle them by yourself, unless you're an
        advanced kitipy user.

        Args:
            local_path (str): Path to the file to transfer.
            remote_path (str): Destination path on the remote target.
        
        Raises:
            paramiko.ssh_exception.SSHException: When the copy fails.
        """
        if not self.is_remote:
            return

        size = os.path.getsize(local_path)
        label = "Transfer %s to %s" % (local_path, remote_path)
        self._dispatcher.emit('file_transfer.start', size=size, label=label)

        fn = lambda current, total: self._dispatcher.emit(
            'file_transfer.update', current=current, total=total)

        try:
            self.sftp.put(local_path, remote_path, callback=fn)
        finally:
            self._dispatcher.emit('file_transfer.end')

    def mkdtemp(self,
                suffix: Optional[str] = None,
                prefix: Optional[str] = None,
                dir: Optional[str] = None) -> str:
        """Creates a temporary directory with a unique name. It runs
        :func:`tempfile.mkdtemp` in local mode and uses ``mktemp -d`` in
        remote mode. It's the caller responsibility to clean up this directory
        when it's not used anymore.

        Args:
            suffix (Optional[str]):
                Suffix of the temporary directory.
            prefix (Optional[str]):
                Prefix of the temporary directory.
            dir (Optional[str]):
                Base directory where the temporary directory should be created.
                This  defaults to the default temporary directory in local mode
                and to ``/tmp`` in remote mode.
        
        Raises:
            paramiko.ssh_exception.SSHException: When running in remote mode and a low-level
                SSH error happens.
            
            subprocess.SubprocessError: When ``mktemp -d`` fails.

        Returns:
            str: The path to the temporary directory.
        """
        if self.is_local:
            return tempfile.mkdtemp(suffix, prefix, dir)

        dir = dir or '/tmp'
        prefix = prefix or ''
        suffix = suffix or ''
        filename_tpl = os.path.join(dir, prefix + 'XXXXXXXX' + suffix)
        res = self._remote("mktemp -d %s" % (filename_tpl))
        return res.stdout

    def path_exists(self, path: str) -> bool:
        """Check if the given path exists. It uses :code:`os.path.exists` in 
        local mode and ``ls`` in remote mode.
        """
        if self.is_local:
            return os.path.exists(path)

        res = self._remote("ls %s 1>/dev/null 2>&1" % (path), check=False)
        return res.returncode == 0

    @property
    def is_local(self) -> bool:
        """Check if the Executor run in local mode (initialized with no hostname)."""
        return self._ssh_config is None

    @property
    def is_remote(self) -> bool:
        """Check if the Executor run in remote mode (initialized with a hostname)."""
        return self._ssh_config is not None


class InteractiveWarningPolicy(paramiko.MissingHostKeyPolicy):
    """InteractiveWarningPolicy implements a paramiko MissingHostKeyPolicy
    that uses :func:`click.confirm` helper to ask for confirmation when a new
    host_key is detected. This is the default paramiko MissingHostKeyPolicy
    used by kitipy.
    """
    def missing_host_key(self, client, hostname, key):
        """Called when an :class:`paramiko.client.SSHClient` receives a server
        key for a server that isn’t in either the system or local
        :class:`paramiko.hostkeys.HostKeys` object. To accept the key, simply
        return. To reject, raised an exception (which will be passed to the
        calling application).
        """
        confirm_msg = "WARNING: Host key for %s not found (%s). Do you want to add it to your ~/.ssh/known_hosts?" % (
            hostname, key)

        if not click.confirm(confirm_msg):
            raise RuntimeError("Unknown host key for %s." % (hostname))

        client._host_keys.add(hostname, key.get_name(), key)
        if client._host_keys_filename is not None:
            client.save_host_keys(client._host_keys_filename)
