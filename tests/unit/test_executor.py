import kitipy
import os.path
import paramiko
import pathlib
import pytest
import shutil
import socket
import tempfile
from kitipy import InteractiveWarningPolicy
from unittest import mock


@pytest.fixture(params=["local", "remote", "remote_with_jumphost"])
def executor(request):
    dispatcher = kitipy.Dispatcher()

    ssh_config_file = os.path.join(os.path.dirname(__file__), '..', '.ssh',
                                   'config')
    ssh_config_file = os.path.abspath(ssh_config_file)

    if request.param == "local":
        basedir = tempfile.mkdtemp()
        yield kitipy.Executor(basedir, '', dispatcher)
        shutil.rmtree(basedir)
        return

    hostname = "testhost"
    if request.param == "remote_with_jumphost":
        hostname = 'testhost-via-jumphost'

    executor = kitipy.Executor(os.getcwd(),
                               '/home/app',
                               dispatcher,
                               hostname=hostname,
                               ssh_config_file=ssh_config_file,
                               paramiko_config={
                                   'look_for_keys': False,
                               })
    executor.set_missing_host_key_policy(paramiko.AutoAddPolicy)
    yield executor


def executors_run_testdata():
    return (
        ("echo yolo", 0, "yolo\n", ""),
        ("/bin/false", 1, "", ""),
    )


@pytest.mark.parametrize("cmd, returncode, stdout, stderr",
                         executors_run_testdata())
def test_executors_run(executor: kitipy.Executor, cmd: str, returncode: int,
                       stdout: str, stderr: str):
    returned = executor.run(cmd, pipe=True, check=False)

    assert returned.returncode == returncode
    assert returned.stdout == stdout
    assert returned.stderr == stderr


def test_executors_local(executor: kitipy.Executor):
    returned = executor.local('hostname', pipe=True, check=False)

    assert returned.returncode == 0
    assert returned.stdout == "%s\n" % (socket.gethostname())
    assert returned.stderr == ''


def test_interactive_warning_policy_confirmed():
    policy = InteractiveWarningPolicy()

    with mock.patch('click.confirm') as confirm:
        confirm.return_value = True
        client = mock.Mock(spec=paramiko.SSHClient)
        client._host_keys = mock.Mock(spec=paramiko.HostKeys)
        client._host_keys_filename = "some_file"

        key = mock.Mock(spec=paramiko.PKey)
        key.get_name.return_value = "key name"

        policy.missing_host_key(client, "[localhost]:2022", key)

        confirm.assert_called_once()
        client._host_keys.add.assert_called_once_with("[localhost]:2022",
                                                      "key name", key)
        client.save_host_keys.assert_called_once_with("some_file")


def test_interactive_warning_policy_refused():
    policy = InteractiveWarningPolicy()

    with mock.patch('click.confirm') as confirm:
        confirm.return_value = False
        client = mock.Mock(spec=paramiko.SSHClient)
        client._host_keys = mock.Mock(spec=paramiko.HostKeys)
        key = mock.Mock(spec=paramiko.PKey)

        with pytest.raises(RuntimeError):
            policy.missing_host_key(client, "[localhost]:2022", key)

        confirm.assert_called_once()
        client._host_keys.add.assert_not_called()
        client.save_host_keys.assert_not_called()
