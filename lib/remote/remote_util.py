import os
import re
import sys
import copy
import urllib
import uuid
import time
import logging
import stat
import json
import TestInput
from subprocess import Popen, PIPE

import logger
from builds.build_query import BuildQuery
import testconstants
from testconstants import VERSION_FILE
from testconstants import WIN_REGISTER_ID
from testconstants import MEMBASE_VERSIONS
from testconstants import COUCHBASE_VERSIONS
from testconstants import MISSING_UBUNTU_LIB
from testconstants import MV_LATESTBUILD_REPO
from testconstants import SHERLOCK_BUILD_REPO
from testconstants import COUCHBASE_VERSIONS
from testconstants import WIN_CB_VERSION_3
from testconstants import COUCHBASE_VERSION_2
from testconstants import COUCHBASE_VERSION_3
from testconstants import COUCHBASE_FROM_VERSION_3,\
                          COUCHBASE_FROM_SPOCK, SYSTEMD_SERVER
from testconstants import COUCHBASE_RELEASE_VERSIONS_3, CB_RELEASE_BUILDS
from testconstants import SHERLOCK_VERSION, WIN_PROCESSES_KILLED
from testconstants import COUCHBASE_FROM_VERSION_4, COUCHBASE_FROM_WATSON,\
                          COUCHBASE_FROM_SPOCK
from testconstants import RPM_DIS_NAME
from testconstants import LINUX_DISTRIBUTION_NAME, LINUX_CB_PATH, \
                          LINUX_COUCHBASE_BIN_PATH
from testconstants import WIN_COUCHBASE_BIN_PATH,\
                          WIN_CB_PATH
from testconstants import WIN_COUCHBASE_BIN_PATH_RAW
from testconstants import WIN_TMP_PATH, WIN_TMP_PATH_RAW
from testconstants import WIN_UNZIP, WIN_PSSUSPEND

from testconstants import MAC_CB_PATH, MAC_COUCHBASE_BIN_PATH

from testconstants import CB_VERSION_NAME
from testconstants import CB_REPO
from testconstants import CB_RELEASE_APT_GET_REPO
from testconstants import CB_RELEASE_YUM_REPO

from testconstants import LINUX_NONROOT_CB_BIN_PATH,\
                          NR_INSTALL_LOCATION_FILE

from membase.api.rest_client import RestConnection, RestHelper

log = logger.Logger.get_logger()
logging.getLogger("paramiko").setLevel(logging.WARNING)

try:
    import paramiko
except ImportError:
    log.warn("{0} {1} {2}".format("Warning: proceeding without importing",
                                  "paramiko due to import error.",
                                  "ssh connections to remote machines will fail!\n"))

class RemoteMachineInfo(object):
    def __init__(self):
        self.type = ''
        self.ip = ''
        self.distribution_type = ''
        self.architecture_type = ''
        self.distribution_version = ''
        self.deliverable_type = ''
        self.ram = ''
        self.cpu = ''
        self.disk = ''
        self.hostname = ''


class RemoteMachineProcess(object):
    def __init__(self):
        self.pid = ''
        self.name = ''
        self.vsz = 0
        self.rss = 0
        self.args = ''


class RemoteMachineHelper(object):
    remote_shell = None

    def __init__(self, remote_shell):
        self.remote_shell = remote_shell

    def monitor_process(self, process_name,
                        duration_in_seconds=120):
        # monitor this process and return if it crashes
        end_time = time.time() + float(duration_in_seconds)
        last_reported_pid = None
        while time.time() < end_time:
            # get the process list
            process = self.is_process_running(process_name)
            if process:
                if not last_reported_pid:
                    last_reported_pid = process.pid
                elif not last_reported_pid == process.pid:
                    message = 'process {0} was restarted. old pid : {1} new pid : {2}'
                    log.info(message.format(process_name, last_reported_pid, process.pid))
                    return False
                    # check if its equal
            else:
                # we should have an option to wait for the process
                # to start during the timeout
                # process might have crashed
                log.info("{0}:process {1} is not running or it might have crashed!"
                                       .format(self.remote_shell.ip, process_name))
                return False
            time.sleep(1)
        #            log.info('process {0} is running'.format(process_name))
        return True

    def monitor_process_memory(self, process_name,
                               duration_in_seconds=180,
                               end=False):
        # monitor this process and return list of memories in 7 seconds interval
        end_time = time.time() + float(duration_in_seconds)
        count = 0
        vsz = []
        rss = []
        while time.time() < end_time and not end:
            # get the process list
            process = self.is_process_running(process_name)
            if process:
                vsz.append(process.vsz)
                rss.append(process.rss)
            else:
                log.info("{0}:process {1} is not running.  Wait for 2 seconds"
                                   .format(self.remote_shell.ip, process_name))
                count += 1
                time.sleep(2)
                if count == 5:
                    log.error("{0}:process {1} is not running at all."
                                   .format(self.remote_shell.ip, process_name))
                    exit(1)
            log.info("sleep for 7 seconds before poll new processes")
            time.sleep(7)
        return vsz, rss

    def is_process_running(self, process_name):
        if getattr(self.remote_shell, "info", None) is None:
            self.remote_shell.info = self.remote_shell.extract_remote_info()

        if self.remote_shell.info.type.lower() == 'windows':
             output, error = self.remote_shell.execute_command('tasklist| grep {0}'
                                                .format(process_name), debug=False)
             if error or output == [""] or output == []:
                 return None
             words = output[0].split(" ")
             words = filter(lambda x: x != "", words)
             process = RemoteMachineProcess()
             process.pid = words[1]
             process.name = words[0]
             log.info("process is running on {0}: {1}".format(self.remote_shell.ip, words))
             return process
        else:
            processes = self.remote_shell.get_running_processes()
            for process in processes:
                if process.name == process_name:
                    return process
                elif process_name in process.args:
                    return process
            return None


class RemoteMachineShellConnection:
    _ssh_client = None

    def __init__(self, username='root',
                 pkey_location='',
                 ip=''):
        self.username = username
        self.use_sudo = True
        self.nonroot = False
        """ in nonroot, we could extract Couchbase Server at
            any directory that non root user could create
        """
        self.nr_home_path = "/home/%s/" % self.username
        if self.username == 'root':
            self.use_sudo = False
        elif self.username != "Administrator":
            self.use_sudo = False
            self.nonroot = True
        # let's create a connection
        self._ssh_client = paramiko.SSHClient()
        self.ip = ip
        self.remote = (self.ip != "localhost" and self.ip != "127.0.0.1")
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        log.info('connecting to {0} with username : {1} pem key : {2}'.format(ip, username, pkey_location))
        try:
            if self.remote:
                self._ssh_client.connect(hostname=ip, username=username, key_filename=pkey_location)
        except paramiko.AuthenticationException:
            log.info("Authentication failed for {0}".format(self.ip))
            exit(1)
        except paramiko.BadHostKeyException:
            log.info("Invalid Host key for {0}".format(self.ip))
            exit(1)
        except Exception:
            log.info("Can't establish SSH session with {0}".format(self.ip))
            exit(1)

    def __init__(self, serverInfo):
        # let's create a connection
        self.username = serverInfo.ssh_username
        self.password = serverInfo.ssh_password
        self.ssh_key = serverInfo.ssh_key
        self.input = TestInput.TestInputParser.get_test_input(sys.argv)
        self.use_sudo = True
        self.nonroot = False
        self.nr_home_path = "/home/%s/" % self.username
        if self.username == 'root':
            self.use_sudo = False
        elif self.username != "Administrator":
            self.use_sudo = False
            self.nonroot = True
        self._ssh_client = paramiko.SSHClient()
        self.ip = serverInfo.ip
        self.remote = (self.ip != "localhost" and self.ip != "127.0.0.1")
        self.port = serverInfo.port
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        msg = 'connecting to {0} with username:{1} password:{2} ssh_key:{3}'
        log.info(msg.format(serverInfo.ip, serverInfo.ssh_username,
                            serverInfo.ssh_password, serverInfo.ssh_key))
        # added attempts for connection because of PID check failed.
        # RNG must be re-initialized after fork() error
        # That's a paramiko bug
        max_attempts_connect = 2
        attempt = 0
        while True:
            try:
                if self.remote and serverInfo.ssh_key == '':
                    self._ssh_client.connect(hostname=serverInfo.ip.replace('[', '').replace(']',''),
                                             username=serverInfo.ssh_username,
                                             password=serverInfo.ssh_password)
                elif self.remote:
                    self._ssh_client.connect(hostname=serverInfo.ip.replace('[', '').replace(']',''),
                                             username=serverInfo.ssh_username,
                                             key_filename=serverInfo.ssh_key)
                break
            except paramiko.AuthenticationException:
                log.error("Authentication failed")
                exit(1)
            except paramiko.BadHostKeyException:
                log.error("Invalid Host key")
                exit(1)
            except Exception as e:
                if str(e).find('PID check failed. RNG must be re-initialized') != -1 and\
                        attempt != max_attempts_connect:
                    log.error("Can't establish SSH session to node {1} :\
                              {0}. Will try again in 1 sec".format(e, self.ip))
                    attempt += 1
                    time.sleep(1)
                else:
                    log.error("Can't establish SSH session to node {1} :\
                                                   {0}".format(e, self.ip))
                    exit(1)
        log.info("Connected to {0}".format(serverInfo.ip))
        """ self.info.distribution_type.lower() == "ubuntu" """
        self.cmd_ext = ""
        self.bin_path = LINUX_COUCHBASE_BIN_PATH
        self.msi = False
        if self.nonroot:
            self.bin_path = self.nr_home_path + self.bin_path
        self.extract_remote_info()
        os_type = self.info.type.lower()
        if os_type == "windows":
            self.cmd_ext = ".exe"
            self.bin_path = WIN_COUCHBASE_BIN_PATH

    """
        In case of non root user, we need to switch to root to
        run command
    """
    def connect_with_user(self, user="root"):
        if self.info.distribution_type.lower() == "mac":
            log.info("This is Mac Server.  Skip re-connect to it as %s" % user)
            return
        max_attempts_connect = 2
        attempt = 0
        while True:
            try:
                log.info("Connect to node: %s as user: %s" % (self.ip, user))
                if self.remote and self.ssh_key == '':
                    self._ssh_client.connect(hostname=self.ip.replace('[', '').replace(']',''),
                                             username=user,
                                             password=self.password)
                break
            except paramiko.AuthenticationException:
                log.error("Authentication for root failed")
                exit(1)
            except paramiko.BadHostKeyException:
                log.error("Invalid Host key")
                exit(1)
            except Exception as e:
                if str(e).find('PID check failed. RNG must be re-initialized') != -1 and\
                        attempt != max_attempts_connect:
                    log.error("Can't establish SSH session to node {1} as root:\
                              {0}. Will try again in 1 sec".format(e, self.ip))
                    attempt += 1
                    time.sleep(1)
                else:
                    log.error("Can't establish SSH session to node {1} :\
                                                   {0}".format(e, self.ip))
                    exit(1)
        log.info("Connected to {0} as {1}".format(self.ip, user))

    def sleep(self, timeout=1, message=""):
        log.info("{0}:sleep for {1} secs. {2} ...".format(self.ip, timeout, message))
        time.sleep(timeout)

    def get_running_processes(self):
        # if its linux ,then parse each line
        # 26989 ?        00:00:51 pdflush
        # ps -Ao pid,comm
        processes = []
        output, error = self.execute_command('ps -Ao pid,comm,vsz,rss,args', debug=False)
        if output:
            for line in output:
                # split to words
                words = line.strip().split(' ')
                words = filter(None, words)
                if len(words) >= 2:
                    process = RemoteMachineProcess()
                    process.pid = words[0]
                    process.name = words[1]
                    if words[2].isdigit():
                        process.vsz = int(words[2])/1024
                    else:
                        process.vsz = words[2]
                    if words[3].isdigit():
                        process.rss = int(words[3])/1024
                    else:
                        process.rss = words[3]
                    process.args = " ".join(words[4:])
                    processes.append(process)
        return processes

    def get_mem_usage_by_process(self, process_name):
        """Now only linux"""
        output, error = self.execute_command('ps -e -o %mem,cmd|grep {0}'.format(process_name),
                                             debug=False)
        if output:
            for line in output:
                if not 'grep' in line.strip().split(' '):
                    return float(line.strip().split(' ')[0])

    def stop_network(self, stop_time):
        """
        Stop the network for given time period and then restart the network
        on the machine.
        :param stop_time: Time duration for which the network service needs
        to be down in the machine
        :return: Nothing
        """
        self.extract_remote_info()
        os_type = self.info.type.lower()
        if os_type == "unix" or os_type == "linux":
            if self.info.distribution_type.lower() == "ubuntu":
                command = "ifdown -a && sleep {} && ifup -a"
            else:
                command = "service network stop && sleep {} && service network " \
                          "start"
            output, error = self.execute_command(command.format(stop_time))
            self.log_command_output(output, error)
        elif os_type == "windows":
            command = "net stop Netman && timeout {} && net start Netman"
            output, error = self.execute_command(command.format(stop_time))
            self.log_command_output(output, error)

    def stop_network(self, stop_time):
        """
        Stop the network for given time period and then restart the network
        on the machine.
        :param stop_time: Time duration for which the network service needs
        to be down in the machine
        :return: Nothing
        """
        self.extract_remote_info()
        os_type = self.info.type.lower()
        if os_type == "unix" or os_type == "linux":
            if self.info.distribution_type.lower() == "ubuntu":
                command = "ifdown -a && sleep {} && ifup -a"
            else:
                command = "nohup service network stop && sleep {} && service network " \
                          "start &"
            output, error = self.execute_command(command.format(stop_time))
            self.log_command_output(output, error)
        elif os_type == "windows":
            command = "net stop Netman && timeout {} && net start Netman"
            output, error = self.execute_command(command.format(stop_time))
            self.log_command_output(output, error)

    def stop_membase(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            log.info("STOP SERVER")
            o, r = self.execute_command("net stop membaseserver")
            self.log_command_output(o, r)
            o, r = self.execute_command("net stop couchbaseserver")
            self.log_command_output(o, r)
            self.sleep(10, "Wait to stop service completely")
        if self.info.type.lower() == "linux":
            o, r = self.execute_command("/etc/init.d/membase-server stop")
            self.log_command_output(o, r)

    def start_membase(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("net start membaseserver")
            self.log_command_output(o, r)
        if self.info.type.lower() == "linux":
            o, r = self.execute_command("/etc/init.d/membase-server start")
            self.log_command_output(o, r)

    def start_server(self, os="unix"):
        self.extract_remote_info()
        os = self.info.type.lower()
        if not os:
            os = "unix"
        if os == "windows":
            o, r = self.execute_command("net start couchbaseserver")
            self.log_command_output(o, r)
        elif os == "unix" or os == "linux":
            if self.is_couchbase_installed():
                if self.nonroot:
                    log.info("Start Couchbase Server with non root method")
                    o, r = self.execute_command('%s%scouchbase-server \-- -noinput -detached '\
                                              % (self.nr_home_path, LINUX_COUCHBASE_BIN_PATH))
                    self.log_command_output(o, r)
                else:
                    fv, sv, bn = self.get_cbversion("linux")
                    if self.info.distribution_version.lower() in SYSTEMD_SERVER \
                                                 and sv in COUCHBASE_FROM_WATSON:
                        """from watson, systemd is used in centos 7, suse 12 """
                        log.info("Running systemd command on this server")
                        o, r = self.execute_command("systemctl start couchbase-server.service")
                        self.log_command_output(o, r)
                    else:
                        o, r = self.execute_command("/etc/init.d/couchbase-server start")
                        self.log_command_output(o, r)
        elif os == "mac":
            o, r = self.execute_command("open /Applications/Couchbase\ Server.app")
            self.log_command_output(o, r)
        else:
            self.log.error("don't know operating system or product version")

    def stop_server(self, os="unix"):
        self.extract_remote_info()
        os = self.info.type.lower()
        if not os:
            os = "unix"
        if os == "windows":
            o, r = self.execute_command("net stop couchbaseserver")
            self.log_command_output(o, r)
        elif os == "unix" or os == "linux":
            if self.is_couchbase_installed():
                if self.nonroot:
                    o, r = self.execute_command("%s%scouchbase-server -k"
                                                % (self.nr_home_path,
                                                   LINUX_COUCHBASE_BIN_PATH))
                    self.log_command_output(o, r)
                else:
                    fv, sv, bn = self.get_cbversion("linux")
                    if self.info.distribution_version.lower() in SYSTEMD_SERVER \
                                                 and sv in COUCHBASE_FROM_WATSON:
                        """from watson, systemd is used in centos 7, suse 12 """
                        log.info("Running systemd command on this server")
                        o, r = self.execute_command("systemctl stop couchbase-server.service")
                        self.log_command_output(o, r)
                    else:
                        o, r = self.execute_command("/etc/init.d/couchbase-server stop"\
                                                                     , use_channel=True)
            else:
                log.info("No Couchbase Server installed yet!")
        elif os == "mac":
            o, r = self.execute_command("ps aux | grep Couchbase | awk '{print $2}'\
                                                                   | xargs kill -9")
            self.log_command_output(o, r)
            o, r = self.execute_command("killall -9 epmd")
            self.log_command_output(o, r)
        else:
            self.log.error("don't know operating system or product version")

    def restart_couchbase(self):
        """
        Restart the couchbase server on the machine.
        :return: Nothing
        """
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("net stop couchbaseserver")
            self.log_command_output(o, r)
            o, r = self.execute_command("net start couchbaseserver")
            self.log_command_output(o, r)
        if self.info.type.lower() == "linux":
            fv, sv, bn = self.get_cbversion("linux")
            if "centos 7" in self.info.distribution_version.lower() \
                    and sv in COUCHBASE_FROM_WATSON:
                """from watson, systemd is used in centos 7 """
                log.info("this node is centos 7.x")
                o, r = self.execute_command("service couchbase-server restart")
                self.log_command_output(o, r)
            else:
                o, r = self.execute_command(
                    "/etc/init.d/couchbase-server restart")
                self.log_command_output(o, r)
        if self.info.distribution_type.lower() == "mac":
            o, r = self.execute_command(
                "open /Applications/Couchbase\ Server.app")
            self.log_command_output(o, r)

    def stop_schedule_tasks(self):
        log.info("STOP ALL SCHEDULE TASKS: installme, removeme and upgrademe")
        output, error = self.execute_command("cmd /c schtasks /end /tn installme")
        self.log_command_output(output, error)
        output, error = self.execute_command("cmd /c schtasks /end /tn removeme")
        self.log_command_output(output, error)
        output, error = self.execute_command("cmd /c schtasks /end /tn upgrademe")
        self.log_command_output(output, error)

    def kill_erlang(self, os="unix"):
        if os == "windows":
            o, r = self.execute_command("taskkill /F /T /IM epmd.exe*")
            self.log_command_output(o, r)
            o, r = self.execute_command("taskkill /F /T /IM erl*")
            self.log_command_output(o, r)
            o, r = self.execute_command("tasklist | grep erl.exe")
            kill_all = False
            while len(o) >= 1 and not kill_all:
                self.execute_command("taskkill /F /T /IM erl*")
                o, r = self.execute_command("tasklist | grep erl.exe")
                if len(o) == 0:
                    kill_all = True
                    log.info("all erlang processes were killed")
        else:
            o, r = self.execute_command("kill "
                        " $(ps aux | grep 'beam.smp' | awk '{print $2}')")
            self.log_command_output(o, r)
        return o, r

    def kill_cbft_process(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("taskkill /F /T /IM cbft.exe*")
            self.log_command_output(o, r)
        else:
            o, r = self.execute_command("killall -9 cbft")
            self.log_command_output(o, r)
        return o, r

    def kill_memcached(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("taskkill /F /T /IM memcached*")
            self.log_command_output(o, r)
        else:
            # Changed from kill -9 $(ps aux | grep 'memcached' | awk '{print $2}' as grep was also returning eventing
            # process which was using memcached-cert
            o, r = self.execute_command("kill -9 $(ps aux | pgrep 'memcached')")
            self.log_command_output(o, r)
        return o, r

    def stop_memcached(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("taskkill /F /T /IM memcached*")
            self.log_command_output(o, r)
        else:
            o, r = self.execute_command("kill -SIGSTOP $(pgrep memcached)")
            self.log_command_output(o, r)
        return o, r

    def start_memcached(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("taskkill /F /T /IM memcached*")
            self.log_command_output(o, r)
        else:
            o, r = self.execute_command("kill -SIGCONT $(pgrep memcached)")
            self.log_command_output(o, r)
        return o, r

    def kill_goxdcr(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("taskkill /F /T /IM goxdcr*")
            self.log_command_output(o, r)
        else:
            o, r = self.execute_command("killall -9 goxdcr")
            self.log_command_output(o, r)
        return o, r

    def kill_eventing_process(self, name):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command(command="taskkill /F /T /IM {0}*".format(name))
            self.log_command_output(o, r)
        else:
            o, r = self.execute_command(command="killall -9 {0}".format(name))
            self.log_command_output(o, r)
        return o, r

    def reboot_node(self):
        self.extract_remote_info()
        if self.extract_remote_info().type.lower() == 'windows':
            o, r = self.execute_command("shutdown -r -f -t 0")
            self.log_command_output(o, r)
        elif self.extract_remote_info().type.lower() == 'linux':
            o, r = self.execute_command("reboot")
            self.log_command_output(o, r)
        return o, r

    def change_log_level(self, new_log_level):
        log.info("CHANGE LOG LEVEL TO %s".format(new_log_level))
        # ADD NON_ROOT user config_details
        output, error = self.execute_command("sed -i '/loglevel_default, /c \\{loglevel_default, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_ns_server, /c \\{loglevel_ns_server, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_stats, /c \\{loglevel_stats, %s\}'. %s"
                                             % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_rebalance, /c \\{loglevel_rebalance, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_cluster, /c \\{loglevel_cluster, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_views, /c \\{loglevel_views, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_error_logger, /c \\{loglevel_error_logger, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_mapreduce_errors, /c \\{loglevel_mapreduce_errors, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_user, /c \\{loglevel_user, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_xdcr, /c \\{loglevel_xdcr, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/loglevel_menelaus, /c \\{loglevel_menelaus, %s\}'. %s"
                                            % (new_log_level, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)

    def configure_log_location(self, new_log_location):
        mv_logs = testconstants.LINUX_LOG_PATH + '/' + new_log_location
        print " MV LOGS %s" % mv_logs
        error_log_tag = "error_logger_mf_dir"
        # ADD NON_ROOT user config_details
        log.info("CHANGE LOG LOCATION TO %s".format(mv_logs))
        output, error = self.execute_command("rm -rf %s" % mv_logs)
        self.log_command_output(output, error)
        output, error = self.execute_command("mkdir %s" % mv_logs)
        self.log_command_output(output, error)
        output, error = self.execute_command("chown -R couchbase %s" % mv_logs)
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/%s, /c \\{%s, \"%s\"\}.' %s"
                                        % (error_log_tag, error_log_tag, mv_logs, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)

    def change_stat_periodicity(self, ticks):
        # ADD NON_ROOT user config_details
        log.info("CHANGE STAT PERIODICITY TO every %s seconds" % ticks)
        output, error = self.execute_command("sed -i '$ a\{grab_stats_every_n_ticks, %s}.'  %s"
        % (ticks, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)

    def change_port_static(self, new_port):
        # ADD NON_ROOT user config_details
        log.info("=========CHANGE PORTS for REST: %s, MCCOUCH: %s,MEMCACHED: %s, MOXI: %s, CAPI: %s==============="
                % (new_port, new_port + 1, new_port + 2, new_port + 3, new_port + 4))
        output, error = self.execute_command("sed -i '/{rest_port/d' %s" % testconstants.LINUX_STATIC_CONFIG)
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '$ a\{rest_port, %s}.' %s"
                                             % (new_port, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/{mccouch_port/d' %s" % testconstants.LINUX_STATIC_CONFIG)
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '$ a\{mccouch_port, %s}.' %s"
                                             % (new_port + 1, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/{memcached_port/d' %s" % testconstants.LINUX_STATIC_CONFIG)
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '$ a\{memcached_port, %s}.' %s"
                                             % (new_port + 2, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/{moxi_port/d' %s" % testconstants.LINUX_STATIC_CONFIG)
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '$ a\{moxi_port, %s}.' %s"
                                             % (new_port + 3, testconstants.LINUX_STATIC_CONFIG))
        self.log_command_output(output, error)
        output, error = self.execute_command("sed -i '/port = /c\port = %s' %s"
                                             % (new_port + 4, testconstants.LINUX_CAPI_INI))
        self.log_command_output(output, error)
        output, error = self.execute_command("rm %s" % testconstants.LINUX_CONFIG_FILE)
        self.log_command_output(output, error)
        output, error = self.execute_command("cat %s" % testconstants.LINUX_STATIC_CONFIG)
        self.log_command_output(output, error)

    def is_couchbase_installed(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            if self.file_exists(WIN_CB_PATH, VERSION_FILE):
                log.info("{0} **** The version file {1} {2}  exists"\
                          .format(self.ip, WIN_CB_PATH, VERSION_FILE ))
                # print running process on windows
                RemoteMachineHelper(self).is_process_running('memcached')
                RemoteMachineHelper(self).is_process_running('erl')
                return True
        elif self.info.distribution_type.lower() == 'mac':
            output, error = self.execute_command('ls %s%s' % (MAC_CB_PATH, VERSION_FILE))
            self.log_command_output(output, error)
            for line in output:
                if line.find('No such file or directory') == -1:
                    return True
        elif self.info.type.lower() == "linux":
            if self.nonroot:
                if self.file_exists("/home/%s/" % self.username, NR_INSTALL_LOCATION_FILE):
                    output, error = self.execute_command("cat %s" % NR_INSTALL_LOCATION_FILE)
                    if output and output[0]:
                        log.info("Couchbase Server was installed in non default path %s"
                                                                            % output[0])
                        self.nr_home_path = output[0]
                file_path = self.nr_home_path + LINUX_CB_PATH
                if self.file_exists(file_path, VERSION_FILE):
                    log.info("non root couchbase installed at %s " % self.ip)
                    return True
            else:
                if self.file_exists(LINUX_CB_PATH, VERSION_FILE):
                    log.info("{0} **** The version file {1} {2}  exists".format(self.ip,
                                                      LINUX_CB_PATH, VERSION_FILE ))
                    return True
        return False

    def is_moxi_installed(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            self.log.error('Not implemented')
        elif self.info.distribution_type.lower() == 'mac':
            self.log.error('Not implemented')
        elif self.info.type.lower() == "linux":
            if self.file_exists(testconstants.LINUX_MOXI_PATH, 'moxi'):
                return True
        return False

    # /opt/moxi/bin/moxi -Z port_listen=11211 -u root -t 4 -O /var/log/moxi/moxi.log
    def start_moxi(self, ip, bucket, port, user=None, threads=4, log_file="/var/log/moxi.log"):
        if self.is_couchbase_installed():
            prod = "couchbase"
        else:
            prod = "membase"
        cli_path = "/opt/" + prod + "/bin/moxi"
        args = ""
        args += "http://{0}:8091/pools/default/bucketsStreaming/{1} ".format(ip, bucket)
        args += "-Z port_listen={0} -u {1} -t {2} -O {3} -d".format(port,
                                                                    user or prod,
                                                                    threads, log_file)
        self.extract_remote_info()
        if self.info.type.lower() == "linux":
            o, r = self.execute_command("{0} {1}".format(cli_path, args))
            self.log_command_output(o, r)
        else:
            raise Exception("running standalone moxi is not supported for windows")

    def stop_moxi(self):
        self.extract_remote_info()
        if self.info.type.lower() == "linux":
            o, r = self.execute_command("killall -9 moxi")
            self.log_command_output(o, r)
        else:
            raise Exception("stopping standalone moxi is not supported on windows")
    def is_url_live(self, url):
        live_url = False
        log.info("Check if url {0} is ok".format(url))
        status = urllib.urlopen(url).getcode()
        if status == 200:
            log.info("This url {0} is live".format(url))
            live_url = True
        else:
            mesg = "\n===============\n"\
                   "        This url {0} \n"\
                   "        is failed to connect.\n"\
                   "        Check version in params to make sure it correct pattern or build number.\n"\
                   "===============\n".format(url)
            self.stop_current_python_running(mesg)
        return live_url

    def is_ntp_installed(self):
        ntp_installed = False
        do_install = False
        os_version = ""
        log.info("Check if ntp is installed")
        self.extract_remote_info()
        if self.info.type.lower() == 'linux':
            log.info("\nThis OS version %s" % self.info.distribution_version.lower())
            if "centos 7" in self.info.distribution_version.lower():
                os_version = "centos 7"
                log.info("Check ntpd service in centos 7.x server")
                output, e = self.execute_command("systemctl status ntpd")
                for line in output:
                    if "Active: active (running)" in line:
                        log.info("ntp was installed in this server {0}".format(self.ip))
                        ntp_installed = True
                if not ntp_installed:
                    log.info("ntp not installed yet or not run.\n"\
                             "Let remove any old one and install ntp")
                    self.execute_command("yum erase -y ntp", debug=False)
                    self.execute_command("yum install -y ntp", debug=False)
                    self.execute_command("systemctl start ntpd", debug=False)
                    self.execute_command("systemctl enable ntpd", debug=False)
                    self.execute_command("firewall-cmd --add-service=ntp --permanent",
                                                                          debug=False)
                    self.execute_command("firewall-cmd --reload", debug=False)
                    do_install = True
                timezone, _ = self.execute_command("date")
                if "PST" not in timezone[0]:
                    log.info("Set time zone in centos 7 to America/Los_Angeles (PST)")
                    self.execute_command("timedatectl set-timezone America/Los_Angeles",
                                                                            debug=False)
            elif "centos release 6" in self.info.distribution_version.lower():
                os_version = "centos 6"
                log.info("Check ntpd service in centos 6")
                output, e = self.execute_command("/etc/init.d/ntpd status")
                if not output:
                    log.info("ntp was not installed on {0} server yet.  "\
                             "Let install ntp on this server ".format(self.ip))
                    self.execute_command("yum install -y ntp ntpdate", debug=False)
                    self.execute_command("chkconfig ntpd on", debug=False)
                    self.execute_command("ntpdate pool.ntp.org", debug=False)
                    self.execute_command("/etc/init.d/ntpd start", debug=False)
                    do_install = True
                elif output and "ntpd is stopped" in output[0]:
                    log.info("ntp is not running.  Let remove it and install again in {0}"\
                                                            .format(self.ip))
                    self.execute_command("yum erase -y ntp", debug=False)
                    self.execute_command("yum install -y ntp ntpdate", debug=False)
                    self.execute_command("chkconfig ntpd on", debug=False)
                    self.execute_command("ntpdate pool.ntp.org", debug=False)
                    self.execute_command("/etc/init.d/ntpd start", debug=False)
                    do_install = True
                elif output and "is running..." in output[0]:
                    ntp_installed = True
                    log.info("ntp was installed in this server {0}".format(self.ip))
                timezone, _ = self.execute_command("date")
                if "PST" not in timezone[0]:
                    log.info("Set time zone in centos 6 to America/Los_Angeles (PST)")
                    self.execute_command("cp /etc/localtime /root/old.timezone",
                                                                    debug=False)
                    self.execute_command("rm -rf /etc/localtime", debug=False)
                    self.execute_command("ln -s /usr/share/zoneinfo/America/Los_Angeles "
                                         "/etc/localtime", debug=False)
            else:
                log.info("will add install in other os later, no set do install")

        if do_install:
            log.info("Check ntpd service after installed")
            if os_version == "centos 7":
                output, e = self.execute_command("systemctl status ntpd")
                for line in output:
                    if "Active: active (running)" in line:
                        log.info("ntp is installed and running on this server %s" % self.ip)
                        ntp_installed = True
                        break;
            if os_version == "centos 6":
                output, e = self.execute_command("/etc/init.d/ntpd status")
                if output and " is running..." in output[0]:
                    log.info("ntp is installed and running on this server %s" % self.ip)
                    ntp_installed = True

        log.info("Date and time on this server {0}".format(self.ip))
        output, _ = self.execute_command("date")
        log.info("\n{0}".format(output))
        if not ntp_installed and "centos" in os_version:
            mesg = "\n===============\n"\
                   "        This server {0} \n"\
                   "        failed to install ntp service.\n"\
                   "===============\n".format(self.ip)
            self.stop_current_python_running(mesg)

    def download_build(self, build):
        return self.download_binary(build.url, build.deliverable_type, build.name,
                                                latest_url=build.url_latest_build,
                                                version=build.product_version)

    def disable_firewall(self):
        self.extract_remote_info()
        if self.info.type.lower() == "windows":
            output, error = self.execute_command('netsh advfirewall set publicprofile state off')
            self.log_command_output(output, error)
            output, error = self.execute_command('netsh advfirewall set privateprofile state off')
            self.log_command_output(output, error)
            # for details see RemoteUtilHelper.enable_firewall for windows
            output, error = self.execute_command('netsh advfirewall firewall delete rule name="block erl.exe in"')
            self.log_command_output(output, error)
            output, error = self.execute_command('netsh advfirewall firewall delete rule name="block erl.exe out"')
            self.log_command_output(output, error)
        else:
            command_1 = "/sbin/iptables -F"
            command_2 = "/sbin/iptables -t nat -F"
            if self.nonroot:
                log.info("\n Non root or non sudo has no right to disable firewall")
                return
            output, error = self.execute_command(command_1)
            self.log_command_output(output, error)
            output, error = self.execute_command(command_2)
            self.log_command_output(output, error)
            self.connect_with_user(user=self.username)

    def download_binary(self, url, deliverable_type, filename, latest_url=None,
                                              version="", skip_md5_check=True):
        self.extract_remote_info()
        self.disable_firewall()
        file_status = False
        if self.info.type.lower() == 'windows':
            self.terminate_processes(self.info, [s for s in WIN_PROCESSES_KILLED])
            self.terminate_processes(self.info, \
                                     [s + "-*" for s in COUCHBASE_FROM_VERSION_3])
            self.disable_firewall()
            remove_words = ["-rel", ".exe"]
            for word in remove_words:
                filename = filename.replace(word, "")
            """couchbase-server-enterprise_3.5.0-968-windows_amd64
               couchbase-server-enterprise_4.0.0-1655-windows_amd64
               sherlock changed from 3.5. to 4.0 """
            filename_version = ""
            if len(filename) > 40:
                if "enterprise" in filename:
                    filename_version = filename[28:33]
                elif "community" in filename:
                    filename_version = filename[27:32]
            if filename_version in COUCHBASE_FROM_VERSION_4:
                log.info("This version is {0}".format(filename_version))
                tmp = filename.split("_")
                version = tmp[1].replace("-windows", "")
            else:
                tmp = filename.split("-")
                tmp.reverse()
                version = tmp[1] + "-" + tmp[0]

            exist = self.file_exists('/cygdrive/c/tmp/', '{0}.exe'.format(version))
            command = "cd /cygdrive/c/tmp;cmd /c 'c:\\automation\\wget.exe "\
                                        "--no-check-certificate"\
                                        " -q {0} -O {1}.exe';ls -lh;".format(url, version)
            file_location = "/cygdrive/c/tmp/"
            deliverable_type = "exe"
            if not exist:
                output, error = self.execute_command(command)
                self.log_command_output(output, error)
                if not self.file_exists(file_location, '{0}.exe'.format(version)):
                    file_status = self.check_and_retry_download_binary(command,
                                                             file_location, version)
                return file_status
            else:
                log.info('File {0}.exe exist in tmp directory'.format(version))
                return True

        elif self.info.distribution_type.lower() == 'mac':
            command = "cd ~/Downloads ; rm -rf couchbase-server* ;"\
                      " rm -rf Couchbase\ Server.app ; curl -O {0}".format(url)
            output, error = self.execute_command(command)
            self.log_command_output(output, error)
            output, error = self.execute_command('ls -lh  ~/Downloads/%s' % filename)
            self.log_command_output(output, error)
            for line in output:
                if line.find('No such file or directory') == -1:
                    return True
            return False
        else:
        # try to push this build into
        # depending on the os
        # build.product has the full name
        # first remove the previous file if it exist ?
        # fix this :
            command1 = "cd /tmp ; D=$(mktemp -d cb_XXXX) ; mv {0} $D ; mv core.* $D ;"\
                                     " rm -f * ; mv $D/* . ; rmdir $D".format(filename)
            command_root = "cd /tmp;wget -q -O {0} {1};cd /tmp;ls -lh".format(filename, url)
            file_location = "/tmp"
            output, error = self.execute_command_raw(command1)
            self.log_command_output(output, error)
            if skip_md5_check:
                if self.nonroot:
                    output, error = self.execute_command("ls -lh ")
                    self.log_command_output(output, error)
                    log.info("remove old couchbase server binary ")
                    if self.file_exists("/home/%s/" % self.username, NR_INSTALL_LOCATION_FILE):
                        output, error = self.execute_command("cat %s"
                                             % NR_INSTALL_LOCATION_FILE)
                        if output and output[0]:
                            log.info("Couchbase Server was installed in non default path %s"
                                                                            % output[0])
                        self.nr_home_path = output[0]
                    self.execute_command_raw('cd %s;rm couchbase-server-*'
                                                      % self.nr_home_path)
                    if "nr_install_dir" in self.input.test_params and \
                                           self.input.test_params["nr_install_dir"]:
                        self.nr_home_path = self.nr_home_path + self.input.test_params["nr_install_dir"]
                        op, er = self.execute_command("echo %s > %s" % (self.nr_home_path,
                                                              NR_INSTALL_LOCATION_FILE))
                        self.log_command_output(op, er)
                        op, er = self.execute_command("rm -rf %s" % self.nr_home_path)
                        self.log_command_output(op, er)
                        op, er = self.execute_command("mkdir %s" % self.nr_home_path)
                        self.log_command_output(op, er)
                    output, error = self.execute_command("ls -lh ")
                    self.log_command_output(output, error)
                    output, error = self.execute_command_raw('cd {2}; pwd;'
                                                             ' wget -q -O {0} {1};ls -lh'
                                                             .format(filename, url,
                                                                     self.nr_home_path))
                    self.log_command_output(output, error)
                else:
                    output, error = self.execute_command_raw(command_root)
                    self.log_command_output(output, error)
            else:
                log.info('get md5 sum for local and remote')
                output, error = self.execute_command_raw('cd /tmp ; rm -f *.md5 *.md5l ; wget -q -O {1}.md5 {0}.md5 ; md5sum {1} > {1}.md5l'.format(url, filename))
                self.log_command_output(output, error)
                if str(error).find('No such file or directory') != -1 and latest_url != '':
                    url = latest_url
                    output, error = self.execute_command_raw('cd /tmp ; rm -f *.md5 *.md5l ; wget -q -O {1}.md5 {0}.md5 ; md5sum {1} > {1}.md5l'.format(url, filename))
                log.info('comparing md5 sum and downloading if needed')
                output, error = self.execute_command_raw('cd /tmp;diff {0}.md5 {0}.md5l || wget -q -O {0} {1};rm -f *.md5 *.md5l'.format(filename, url))
                self.log_command_output(output, error)
            # check if the file exists there now ?
            if self.nonroot:
                """ binary is saved at current user directory """
                return self.file_exists(self.nr_home_path, filename)
            else:
                file_status = self.file_exists(file_location, filename)
                if not file_status:
                    file_status = self.check_and_retry_download_binary(command_root,
                                                                      file_location,
                                                                      version)
                return file_status
            # for linux environment we can just
            # figure out what version , check if /tmp/ has the
            # binary and then return True if binary is installed


    def get_file(self, remotepath, filename, todir):
        if self.file_exists(remotepath, filename):
            if self.remote:
                sftp = self._ssh_client.open_sftp()
                try:
                    filenames = sftp.listdir(remotepath)
                    for name in filenames:
                        if name == filename:
                            sftp.get('{0}/{1}'.format(remotepath, filename), todir)
                            sftp.close()
                            return True
                    sftp.close()
                    return False
                except IOError:
                    return False
        else:
            os.system("cp {0} {1}".format('{0}/{1}'.format(remotepath, filename), todir))

    def read_remote_file(self, remote_path, filename):
        if self.file_exists(remote_path, filename):
            if self.remote:
                sftp = self._ssh_client.open_sftp()
                remote_file = sftp.open('{0}/{1}'.format(remote_path, filename))
                try:
                    out = remote_file.readlines()
                finally:
                    remote_file.close()
                return out
            else:
                txt = open('{0}/{1}'.format(remote_path, filename))
                return txt.read()
        return None

    def write_remote_file(self, remote_path, filename, lines):
        cmd = 'echo "%s" > %s/%s' % (''.join(lines), remote_path, filename)
        self.execute_command(cmd)

    def create_whitelist(self, path, whitelist):
        if not os.path.exists(path):
            self.execute_command("mkdir %s" % path)
        filepath = os.path.join(path, "curl_whitelist.json")
        if os.path.exists(filepath):
            os.remove(filepath)
        file_data = json.dumps(whitelist)
        self.create_file(filepath, file_data)

    def remove_directory(self, remote_path):
        if self.remote:
            sftp = self._ssh_client.open_sftp()
            try:
                log.info("removing {0} directory...".format(remote_path))
                sftp.rmdir(remote_path)
            except IOError:
                return False
            finally:
                sftp.close()
        else:
            try:
                p = Popen("rm -rf {0}".format(remote_path) , shell=True, stdout=PIPE, stderr=PIPE)
                stdout, stderro = p.communicate()
            except IOError:
                return False
        return True

    def rmtree(self, sftp, remote_path, level=0):
        for f in sftp.listdir_attr(remote_path):
            rpath = remote_path + "/" + f.filename
            if stat.S_ISDIR(f.st_mode):
                self.rmtree(sftp, rpath, level=(level + 1))
            else:
                rpath = remote_path + "/" + f.filename
                print('removing %s' % (rpath))
                sftp.remove(rpath)
        print('removing %s' % (remote_path))
        sftp.rmdir(remote_path)

    def remove_directory_recursive(self, remote_path):
        if self.remote:
            sftp = self._ssh_client.open_sftp()
            try:
                log.info("removing {0} directory...".format(remote_path))
                self.rmtree(sftp, remote_path)
            except IOError:
                return False
            finally:
                sftp.close()
        else:
            try:
                p = Popen("rm -rf {0}".format(remote_path) , shell=True, stdout=PIPE, stderr=PIPE)
                p.communicate()
            except IOError:
                return False
        return True

    def list_files(self, remote_path):
        if self.remote:
            sftp = self._ssh_client.open_sftp()
            files = []
            try:
                file_names = sftp.listdir(remote_path)
                for name in file_names:
                    files.append({'path': remote_path, 'file': name})
                sftp.close()
            except IOError:
                return []
            return files
        else:
            p = Popen("ls {0}".format(remote_path) , shell=True, stdout=PIPE, stderr=PIPE)
            files, stderro = p.communicate()
            return files

    def file_ends_with(self, remotepath, pattern):
        """
         Check if file ending with this pattern is present in remote machine
        """
        sftp = self._ssh_client.open_sftp()
        files_matched = []
        try:
            file_names = sftp.listdir(remotepath)
            for name in file_names:
                if name.endswith(pattern):
                    files_matched.append("{0}/{1}".format(remotepath, name))
        except IOError:
            # ignore this error
            pass
        sftp.close()
        if len(files_matched) > 0:
            log.info("found these files : {0}".format(files_matched))
        return files_matched

    # check if this file exists in the remote
    # machine or not
    def file_starts_with(self, remotepath, pattern):
        sftp = self._ssh_client.open_sftp()
        files_matched = []
        try:
            file_names = sftp.listdir(remotepath)
            for name in file_names:
                if name.startswith(pattern):
                    files_matched.append("{0}/{1}".format(remotepath, name))
        except IOError:
            # ignore this error
            pass
        sftp.close()
        if len(files_matched) > 0:
            log.info("found these files : {0}".format(files_matched))
        return files_matched

    def file_exists(self, remotepath, filename, pause_time=30):
        sftp = self._ssh_client.open_sftp()
        try:
            filenames = sftp.listdir_attr(remotepath)
            for name in filenames:
                if filename in name.filename and int(name.st_size) > 0:
                    sftp.close()
                    return True
                elif filename in name.filename and int(name.st_size) == 0:
                    if name.filename == NR_INSTALL_LOCATION_FILE:
                        continue
                    log.info("File {0} will be deleted".format(filename))
                    if not remotepath.endswith("/"):
                        remotepath += "/"
                    self.execute_command("rm -rf {0}*{1}*".format(remotepath,filename))
                    self.sleep(pause_time, "** Network or sever may be busy. **"\
                                           "\nWait {0} seconds before executing next instrucion"\
                                                                             .format(pause_time))

            sftp.close()
            return False
        except IOError:
            return False

    def delete_file(self, remotepath, filename):
        sftp = self._ssh_client.open_sftp()
        delete_file = False
        try:
            filenames = sftp.listdir_attr(remotepath)
            for name in filenames:
                if name.filename == filename:
                    log.info("File {0} will be deleted".format(filename))
                    sftp.remove(remotepath + filename)
                    delete_file = True
                    break
            if delete_file:
                """ verify file is deleted """
                filenames = sftp.listdir_attr(remotepath)
                for name in filenames:
                    if name.filename == filename:
                        log.error("fail to remove file %s " % filename)
                        delete_file = False
                        break
            sftp.close()
            return delete_file
        except IOError:
            return False

    def download_binary_in_win(self, url, version, msi_install=False):
        self.terminate_processes(self.info, [s for s in WIN_PROCESSES_KILLED])
        self.terminate_processes(self.info, \
                                 [s + "-*" for s in COUCHBASE_FROM_VERSION_3])
        self.disable_firewall()
        version = version.replace("-rel", "")
        deliverable_type = "exe"
        if url and url.split(".")[-1] == "msi":
            deliverable_type = "msi"
        exist = self.file_exists('/cygdrive/c/tmp/', version)
        log.info('**** about to do the wget ****')
        command = "cd /cygdrive/c/tmp;cmd /c 'c:\\automation\\wget.exe "\
                        " --no-check-certificate"\
                        " -q {0} -O {1}.{2}';ls -lh;".format(url, version, deliverable_type)
        file_location = "/cygdrive/c/tmp/"
        if not exist:
            output, error = self.execute_command(command)
            self.log_command_output(output, error)
        else:
            log.info('File {0}.{1} exist in tmp directory'.format(version,
                                                                  deliverable_type))
        file_status = self.file_exists('/cygdrive/c/tmp/', version)
        if not file_status:
            file_status = self.check_and_retry_download_binary(command, file_location, version)
        return file_status

    def check_and_retry_download_binary(self, command, file_location,
                                        version, time_of_try=3):
        count = 1
        file_status = self.file_exists(file_location, version, pause_time=60)
        while count <= time_of_try and not file_status:
            log.info(" *** try to download binary again {0} time(s)".format(count))
            output, error = self.execute_command(command)
            self.log_command_output(output, error)
            file_status = self.file_exists(file_location, version, pause_time=60)
            count += 1
            if not file_status and count == 3:
                log.error("build {0} did not download completely at server {1}"
                                                   .format(version, self.ip))
                mesg = "stop job due to failure download build {0} at {1} " \
                                                     .format(version, self.ip)
                self.stop_current_python_running(mesg)
        return file_status

    def copy_file_local_to_remote(self, src_path, des_path):
        sftp = self._ssh_client.open_sftp()
        try:
            sftp.put(src_path, des_path)
        except IOError:
            log.error('Can not copy file')
        finally:
            sftp.close()

    def copy_file_remote_to_local(self, rem_path, des_path):
        sftp = self._ssh_client.open_sftp()
        try:
            sftp.get(rem_path, des_path)
        except IOError as e:
            if e:
                print e
            log.error('Can not copy file')
        finally:
            sftp.close()


    # copy multi files from local to remote server
    def copy_files_local_to_remote(self, src_path, des_path):
        files = os.listdir(src_path)
        log.info("copy files from {0} to {1}".format(src_path, des_path))
        # self.execute_batch_command("cp -r  {0}/* {1}".format(src_path, des_path))
        for file in files:
            if file.find("wget") != 1:
                a = ""
            full_src_path = os.path.join(src_path, file)
            full_des_path = os.path.join(des_path, file)
            self.copy_file_local_to_remote(full_src_path, full_des_path)


    # create a remote file from input string
    def create_file(self, remote_path, file_data):
        output, error = self.execute_command("echo '{0}' > {1}".format(file_data, remote_path))

    def find_file(self, remote_path, file):
        sftp = self._ssh_client.open_sftp()
        try:
            files = sftp.listdir(remote_path)
            for name in files:
                if name == file:
                    found_it = os.path.join(remote_path, name)
                    log.info("File {0} was found".format(found_it))
                    return found_it
            else:
                log.error('File(s) name in {0}'.format(remote_path))
                for name in files:
                    log.info(name)
                log.error('Can not find {0}'.format(file))
        except IOError:
            pass
        sftp.close()

    def find_build_version(self, path_to_version, version_file, product):
        sftp = self._ssh_client.open_sftp()
        ex_type = "exe"
        if self.file_exists(WIN_CB_PATH, VERSION_FILE):
            path_to_version = WIN_CB_PATH
        else:
            path_to_version = testconstants.WIN_MB_PATH
        try:
            log.info(path_to_version)
            f = sftp.open(os.path.join(path_to_version, VERSION_FILE), 'r+')
            tmp_str = f.read().strip()
            full_version = tmp_str.replace("-rel", "")
            if full_version == "1.6.5.4-win64":
                full_version = "1.6.5.4"
            build_name = short_version = full_version
            return build_name, short_version, full_version
        except IOError:
            log.error('Can not read version file')
        sftp.close()

    def find_windows_info(self):
        if self.remote:
            found = self.find_file("/cygdrive/c/tmp", "windows_info.txt")
            if isinstance(found, basestring):
                if self.remote:

                    sftp = self._ssh_client.open_sftp()
                    try:
                        f = sftp.open(found)
                        log.info("get windows information")
                        info = {}
                        for line in f:
                            (key, value) = line.split('=')
                            key = key.strip(' \t\n\r')
                            value = value.strip(' \t\n\r')
                            info[key] = value
                        return info
                    except IOError:
                        log.error("can not find windows info file")
                    sftp.close()
            else:
                return self.create_windows_info()
        else:
            try:
                txt = open("{0}/{1}".format("/cygdrive/c/tmp", "windows_info.txt"))
                log.info("get windows information")
                info = {}
                for line in txt.read():
                    (key, value) = line.split('=')
                    key = key.strip(' \t\n\r')
                    value = value.strip(' \t\n\r')
                    info[key] = value
                return info
            except IOError:
                log.error("can not find windows info file")

    def create_windows_info(self):
            systeminfo = self.get_windows_system_info()
            info = {}
            info["os_name"] = "2k8"
            if "OS Name" in systeminfo:
                info["os"] = systeminfo["OS Name"].find("indows") and "windows" or "NONE"
            if systeminfo["OS Name"].find("2008 R2") != -1:
                info["os_name"] = 2008
            elif systeminfo["OS Name"].find("2016") != -1:
                info["os_name"] = 2016
            if "System Type" in systeminfo:
                info["os_arch"] = systeminfo["System Type"].find("64") and "x86_64" or "NONE"
            info.update(systeminfo)
            self.execute_batch_command("rm -rf  /cygdrive/c/tmp/windows_info.txt")
            self.execute_batch_command("touch  /cygdrive/c/tmp/windows_info.txt")
            sftp = self._ssh_client.open_sftp()
            try:
                f = sftp.open('/cygdrive/c/tmp/windows_info.txt', 'w')
                content = ''
                for key in sorted(info.keys()):
                    content += '{0} = {1}\n'.format(key, info[key])
                f.write(content)
                log.info("/cygdrive/c/tmp/windows_info.txt was created with content: {0}".format(content))
            except IOError:
                log.error('Can not write windows_info.txt file')
            finally:
                sftp.close()
            return info

    # Need to add new windows register ID in testconstant file when
    # new couchbase server version comes out.
    def create_windows_capture_file(self, task, product, version):
        src_path = "resources/windows/automation"
        des_path = "/cygdrive/c/automation"

        # remove dot in version (like 2.0.0 ==> 200)
        reg_version = version[0:5:2]
        reg_id = WIN_REGISTER_ID[reg_version]
        uuid_name = uuid.uuid4()

        if task == "install":
            template_file = "cb-install.wct"
            file = "{0}_{1}_install.iss".format(uuid_name, self.ip)
            #file = "{0}_install.iss".format(self.ip)
        elif task == "uninstall":
            template_file = "cb-uninstall.wct"
            file = "{0}_{1}_uninstall.iss".format(uuid_name, self.ip)
            #file = "{0}_uninstall.iss".format(self.ip)

        # create in/uninstall file from windows capture template (wct) file
        full_src_path_template = os.path.join(src_path, template_file)
        full_src_path = os.path.join(src_path, file)
        full_des_path = os.path.join(des_path, file)

        f1 = open(full_src_path_template, 'r')
        f2 = open(full_src_path, 'w')
        """ replace ####### with reg ID to install/uninstall """
        if "2.2.0-837" in version:
            reg_id = "2B630EB8-BBC7-6FE4-C9B8-D8843EB1EFFA"
        log.info("register ID: {0}".format(reg_id))
        for line in f1:
            line = line.replace("#######", reg_id)
            if product == "mb" and task == "install":
                line = line.replace("Couchbase", "Membase")
            f2.write(line)
        f1.close()
        f2.close()

        self.copy_file_local_to_remote(full_src_path, full_des_path)
        """ remove capture file from source after copy to destination """
        self.sleep(4, "wait for remote copy completed")
        """ need to implement to remove only at the end
            of installation """
        #os.remove(full_src_path)
        return uuid_name

    def get_windows_system_info(self):
        try:
            info = {}
            o, _ = self.execute_batch_command('systeminfo')
            for line in o:
                line_list = line.split(':')
                if len(line_list) > 2:
                    if line_list[0] == 'Virtual Memory':
                        key = "".join(line_list[0:2])
                        value = " ".join(line_list[2:])
                    else:
                        key = line_list[0]
                        value = " ".join(line_list[1:])
                elif len(line_list) == 2:
                    (key, value) = line_list
                else:
                    continue
                key = key.strip(' \t\n\r')
                if key.find("[") != -1:
                    info[key_prev] += '|' + key + value.strip(' |')
                else:
                    value = value.strip(' |')
                    info[key] = value
                    key_prev = key
            return info
        except Exception as ex:
            log.error("error {0} appeared during getting  windows info".format(ex))

    # this function used to modify bat file to run task schedule in windows
    def modify_bat_file(self, remote_path, file_name, name, version, task):
        found = self.find_file(remote_path, file_name)
        sftp = self._ssh_client.open_sftp()
        capture_iss_file = ""

        product_version = ""
        if version[:5] in MEMBASE_VERSIONS:
            product_version = version[:5]
            name = "mb"
        elif version[:5] in COUCHBASE_VERSIONS:
            product_version = version[:5]
            name = "cb"
        else:
            log.error('Windows automation does not support {0} version yet'\
                                                               .format(version))
            sys.exit()

        if "upgrade" not in task:
            uuid_name = self.create_windows_capture_file(task, name, version)
        try:
            f = sftp.open(found, 'w')
            name = name.strip()
            version = version.strip()
            if task == "upgrade":
                content = 'c:\\tmp\{3}.exe /s -f1c:\\automation\{0}_{1}_{2}.iss' \
                                .format(name, product_version, task, version)
            else:
                content = 'c:\\tmp\{0}.exe /s -f1c:\\automation\{3}_{2}_{1}.iss' \
                             .format(version, task, self.ip, uuid_name)
            log.info("create {0} task with content:{1}".format(task, content))
            f.write(content)
            log.info('Successful write to {0}'.format(found))
            if "upgrade" not in task:
                capture_iss_file = '{0}_{1}_{2}.iss'.format(uuid_name, self.ip, task)
        except IOError:
            log.error('Can not write build name file to bat file {0}'.format(found))
        sftp.close()
        return capture_iss_file

    def compact_vbuckets(self, vbuckets, nodes, upto_seq, cbadmin_user="cbadminbucket",
                                                          cbadmin_password="password"):
        """
            compact each vbucket with cbcompact tools
        """
        for node in nodes:
            log.info("Purge 'deleted' keys in all %s vbuckets in node %s.  It will take times "
                                                                         % (vbuckets, node.ip))
            for vbucket in range(0, vbuckets):
                cmd = "%scbcompact%s %s:11210 compact %d -u %s -p %s "\
                      "--dropdeletes --purge-only-upto-seq=%d" \
                                                  % (self.bin_path,
                                                     self.cmd_ext,
                                                     node.ip, vbucket,
                                                     cbadmin_user,
                                                     cbadmin_password,
                                                     upto_seq)
                self.execute_command(cmd, debug=False)
        log.info("done compact")

    def set_vbuckets_win(self, vbuckets):
        bin_path = WIN_COUCHBASE_BIN_PATH
        bin_path = bin_path.replace("\\", "")
        src_file = bin_path + "service_register.bat"
        des_file = "/tmp/service_register.bat_{0}".format(self.ip)
        local_file = "/tmp/service_register.bat.tmp_{0}".format(self.ip)

        self.copy_file_remote_to_local(src_file, des_file)
        f1 = open(des_file, "r")
        f2 = open(local_file, "w")
        """ when install new cb server on windows, there is not
            env COUCHBASE_NUM_VBUCKETS yet.  We need to insert this
            env to service_register.bat right after  ERL_FULLSWEEP_AFTER 512
            like -env ERL_FULLSWEEP_AFTER 512 -env COUCHBASE_NUM_VBUCKETS vbuckets
            where vbucket is params passed to function when run install scripts """
        for line in f1:
            if "-env COUCHBASE_NUM_VBUCKETS " in line:
                tmp1 = line.split("COUCHBASE_NUM_VBUCKETS")
                tmp2 = tmp1[1].strip().split(" ")
                log.info("set vbuckets of node {0} to {1}" \
                                 .format(self.ip, vbuckets))
                tmp2[0] = vbuckets
                tmp1[1] = " ".join(tmp2)
                line = "COUCHBASE_NUM_VBUCKETS ".join(tmp1)
            elif "-env ERL_FULLSWEEP_AFTER 512" in line:
                log.info("set vbuckets of node {0} to {1}" \
                                 .format(self.ip, vbuckets))
                line = line.replace("-env ERL_FULLSWEEP_AFTER 512", \
                  "-env ERL_FULLSWEEP_AFTER 512 -env COUCHBASE_NUM_VBUCKETS {0}" \
                                 .format(vbuckets))
            f2.write(line)
        f1.close()
        f2.close()
        self.copy_file_local_to_remote(local_file, src_file)

        """ re-register new setup to cb server """
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_stop.bat")
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_unregister.bat")
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_register.bat")
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_start.bat")
        self.sleep(10, "wait for cb server start completely after reset vbuckets!")

        """ remove temporary files on slave """
        os.remove(local_file)
        os.remove(des_file)

    def set_fts_query_limit_win(self, name, value):
        bin_path = WIN_COUCHBASE_BIN_PATH
        bin_path = bin_path.replace("\\", "")
        src_file = bin_path + "service_register.bat"
        des_file = "/tmp/service_register.bat_{0}".format(self.ip)
        local_file = "/tmp/service_register.bat.tmp_{0}".format(self.ip)

        self.copy_file_remote_to_local(src_file, des_file)
        f1 = open(des_file, "r")
        f2 = open(local_file, "w")
        """ when install new cb server on windows, there is not
            env CBFT_ENV_OPTIONS yet.  We need to insert this
            env to service_register.bat right after  ERL_FULLSWEEP_AFTER 512
            like -env ERL_FULLSWEEP_AFTER 512 -env CBFT_ENV_OPTIONS vbuckets
            where vbucket is params passed to function when run install scripts """
        for line in f1:
            if "-env CBFT_ENV_OPTIONS " in line:
                tmp1 = line.split("CBFT_ENV_OPTIONS")
                tmp2 = tmp1[1].strip().split(" ")
                log.info("set CBFT_ENV_OPTIONS of node {0} to {1}" \
                                 .format(self.ip, value))
                tmp2[0] = value
                tmp1[1] = " ".join(tmp2)
                line = "CBFT_ENV_OPTIONS ".join(tmp1)
            elif "-env ERL_FULLSWEEP_AFTER 512" in line:
                log.info("set CBFT_ENV_OPTIONS of node {0} to {1}" \
                                 .format(self.ip, value))
                line = line.replace("-env ERL_FULLSWEEP_AFTER 512", \
                  "-env ERL_FULLSWEEP_AFTER 512 -env {0} {1}" \
                                 .format(name, value))
            f2.write(line)
        f1.close()
        f2.close()
        self.copy_file_local_to_remote(local_file, src_file)

        """ re-register new setup to cb server """
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_stop.bat")
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_unregister.bat")
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_register.bat")
        self.execute_command(WIN_COUCHBASE_BIN_PATH + "service_start.bat")
        self.sleep(10, "wait for cb server start completely after setting CBFT_ENV_OPTIONS")

        """ remove temporary files on slave """
        os.remove(local_file)
        os.remove(des_file)

    def create_directory(self, remote_path):
        sftp = self._ssh_client.open_sftp()
        try:
            sftp.stat(remote_path)
        except IOError, e:
            if e[0] == 2:
                log.info("Directory at {0} DOES NOT exist. We will create on here".format(remote_path))
                sftp.mkdir(remote_path)
                sftp.close()
                return False
            raise
        else:
            log.error("Directory at {0} DOES exist. Fx returns True".format(remote_path))
            return True

    # this function will remove the automation directory in windows
    def create_multiple_dir(self, dir_paths):
        sftp = self._ssh_client.open_sftp()
        try:
            for dir_path in dir_paths:
                if dir_path != '/cygdrive/c/tmp':
                    output = self.remove_directory('/cygdrive/c/automation')
                    if output:
                        log.info("{0} directory is removed.".format(dir_path))
                    else:
                        log.error("Can not delete {0} directory or directory {0} does not exist.".format(dir_path))
                self.create_directory(dir_path)
            sftp.close()
        except IOError:
            pass

    def couchbase_upgrade(self, build, save_upgrade_config=False, forcefully=False):
        # upgrade couchbase server
        self.extract_remote_info()
        log.info('deliverable_type : {0}'.format(self.info.deliverable_type))
        log.info('/tmp/{0} or /tmp/{1}'.format(build.name, build.product))
        command = ''
        if self.info.type.lower() == 'windows':
                print "build name in couchbase upgrade    ", build.product_version
                self.couchbase_upgrade_win(self.info.architecture_type, \
                                     self.info.windows_name, build.product_version)
                log.info('********* continue upgrade process **********')

        elif self.info.deliverable_type == 'rpm':
            # run rpm -i to install
            if save_upgrade_config:
                self.couchbase_uninstall()
                install_command = 'rpm -i /tmp/{0}'.format(build.name)
                command = 'INSTALL_UPGRADE_CONFIG_DIR=/opt/couchbase/var/lib/membase/config {0}'\
                                             .format(install_command)
            else:
                command = 'rpm -U /tmp/{0}'.format(build.name)
                if forcefully:
                    command = 'rpm -U --force /tmp/{0}'.format(build.name)
        elif self.info.deliverable_type == 'deb':
            if save_upgrade_config:
                self.couchbase_uninstall()
                install_command = 'dpkg -i /tmp/{0}'.format(build.name)
                command = 'INSTALL_UPGRADE_CONFIG_DIR=/opt/couchbase/var/lib/membase/config {0}'\
                                             .format(install_command)
            else:
                command = 'dpkg -i /tmp/{0}'.format(build.name)
                if forcefully:
                    command = 'dpkg -i --force /tmp/{0}'.format(build.name)
        output, error = self.execute_command(command, use_channel=True)
        return output, error

    def couchbase_upgrade_win(self, architecture, windows_name, version):
        task = "upgrade"
        bat_file = "upgrade.bat"
        deleted = False
        self.modify_bat_file('/cygdrive/c/automation', bat_file, 'cb',\
                                                         version, task)
        self.stop_schedule_tasks()
        self.remove_win_backup_dir()
        self.remove_win_collect_tmp()
        output, error = self.execute_command("cat "
                        "'/cygdrive/c/Program Files/Couchbase/Server/VERSION.txt'")
        log.info("version to upgrade from: {0} to {1}".format(output, version))
        log.info('before running task schedule upgrademe')
        if '1.8.0' in str(output):
            """ run installer in second time as workaround for upgrade 1.8.0 only:
            #   Installer needs to update registry value in order to upgrade
            #   from the previous version.
            #   Please run installer again to continue."""
            output, error = \
                        self.execute_command("cmd /c schtasks /run /tn upgrademe")
            self.log_command_output(output, error)
            self.sleep(200, "because upgrade version is {0}".format(output))
            output, error = self.execute_command("cmd /c "
                                      "schtasks /Query /FO LIST /TN upgrademe /V")
            self.log_command_output(output, error)
            self.stop_schedule_tasks()
        # run task schedule to upgrade Membase server
        output, error = self.execute_command("cmd /c schtasks /run /tn upgrademe")
        self.log_command_output(output, error)
        deleted = self.wait_till_file_deleted(WIN_CB_PATH, \
                       VERSION_FILE, timeout_in_seconds=600)
        if not deleted:
            log.error("Uninstall was failed at node {0}".format(self.ip))
            sys.exit()
        self.wait_till_file_added(WIN_CB_PATH, VERSION_FILE, \
                                       timeout_in_seconds=600)
        log.info("installed version: {0}".format(version))
        output, error = self.execute_command("cat "
                       "'/cygdrive/c/Program Files/Couchbase/Server/VERSION.txt'")
        ended = self.wait_till_process_ended(version[:10])
        if not ended:
            sys.exit("*****  Node %s failed to upgrade  *****" % (self.ip))
        self.sleep(10, "wait for server to start up completely")
        ct = time.time()
        while time.time() - ct < 10800:
            output, error = self.execute_command("cmd /c "
                      "schtasks /Query /FO LIST /TN upgrademe /V| findstr Status ")
            if "Ready" in str(output):
                log.info("upgrademe task complteted")
                break
            elif "Could not start":
                log.exception("Ugrade failed!!!")
                break
            else:
                log.info("upgrademe task still running:{0}".format(output))
                self.sleep(30)
        output, error = self.execute_command("cmd /c "
                                       "schtasks /Query /FO LIST /TN upgrademe /V")
        self.log_command_output(output, error)
        """ need to remove binary after done upgrade.  Since watson, current binary
            could not reused to uninstall or install cb server """
        self.delete_file(WIN_TMP_PATH, version[:10] + ".exe")

    """
        This method install Couchbase Server
    """
    def install_server(self, build, startserver=True, path='/tmp', vbuckets=None,
                       swappiness=10, force=False, openssl='', upr=None, xdcr_upr=None,
                       fts_query_limit=None, enable_ipv6=None):

        log.info('*****install server ***')
        server_type = None
        success = True
        start_server_after_install = True
        track_words = ("warning", "error", "fail")
        if build.name.lower().find("membase") != -1:
            server_type = 'membase'
            abbr_product = "mb"
        elif build.name.lower().find("couchbase") != -1:
            server_type = 'couchbase'
            abbr_product = "cb"
        else:
            raise Exception("its not a membase or couchbase?")
        self.extract_remote_info()

        log.info('deliverable_type : {0}'.format(self.info.deliverable_type))
        if self.info.type.lower() == 'windows':
            log.info('***** Doing the windows install')
            self.terminate_processes(self.info, [s for s in WIN_PROCESSES_KILLED])
            self.terminate_processes(self.info, \
                                     [s + "-*" for s in COUCHBASE_FROM_VERSION_3])
            # to prevent getting full disk let's delete some large files
            self.remove_win_backup_dir()
            self.remove_win_collect_tmp()
            output, error = self.execute_command("cmd /c schtasks /run /tn installme")
            success &= self.log_command_output(output, error, track_words)
            file_check = 'VERSION.txt'
            self.wait_till_file_added("/cygdrive/c/Program Files/{0}/Server/".format(server_type.title()), file_check,
                                          timeout_in_seconds=600)
            output, error = self.execute_command("cmd /c schtasks /Query /FO LIST /TN installme /V")
            self.log_command_output(output, error)
            ended = self.wait_till_process_ended(build.product_version[:10])
            if not ended:
                sys.exit("*****  Node %s failed to install  *****" % (self.ip))
            self.sleep(10, "wait for server to start up completely")
            if vbuckets and int(vbuckets) != 1024:
                self.set_vbuckets_win(vbuckets)
            if fts_query_limit:
                self.set_environment_variable(
                    name="CBFT_ENV_OPTIONS",
                    value="bleveMaxResultWindow={0}".format(int(fts_query_limit))
                )

            output, error = self.execute_command("rm -f \
                       /cygdrive/c/automation/*_{0}_install.iss".format(self.ip))
            self.log_command_output(output, error)
            self.delete_file(WIN_TMP_PATH, build.product_version[:10] + ".exe")
            # output, error = self.execute_command("cmd rm /cygdrive/c/tmp/{0}*.exe".format(build_name))
            # self.log_command_output(output, error)
        elif self.info.deliverable_type in ["rpm", "deb"]:
            if startserver and vbuckets == None:
                environment = ""
            else:
                environment = "INSTALL_DONT_START_SERVER=1 "
                start_server_after_install = False
            log.info('/tmp/{0} or /tmp/{1}'.format(build.name, build.product))

            # set default swappiness to 10 unless specify in params in all unix environment
            if self.nonroot:
                log.info("**** use root to run script/ssh.py to execute /sbin/sysctl vm.swappiness=0 "
                              "enable coredump cbenable_core_dumps.sh /tmp")
            else:
                output, error = self.execute_command('/sbin/sysctl vm.swappiness={0}'.format(swappiness))
                success &= self.log_command_output(output, error, track_words, debug=False)

            if self.info.deliverable_type == 'rpm':
                if self.nonroot:
                    op, er = self.execute_command('cd {0}; rpm2cpio {1} ' \
                        '|  cpio --extract --make-directories --no-absolute-filenames ' \
                                     .format(self.nr_home_path, build.name), debug=False)
                    self.log_command_output(op, er)
                    output, error = self.execute_command('cd {0}{1}; ./bin/install/reloc.sh `pwd` '\
                                            .format(self.nr_home_path, LINUX_CB_PATH), debug=False)
                    self.log_command_output(output, error)
                    op, er = self.execute_command('cd {0};pwd'.format(self.nr_home_path))
                    self.log_command_output(op, er)
                    """ command to start Couchbase Server in non root
                        /home/nonroot_user/opt/couchbase/bin/couchbase-server \-- -noinput -detached
                    """
                    output, error = self.execute_command("ls -lh ")
                    self.log_command_output(output, error)
                    if start_server_after_install:
                        output, error = self.execute_command('%s%scouchbase-server '\
                                                             '\-- -noinput -detached '\
                                                              % (self.nr_home_path,
                                                                 LINUX_COUCHBASE_BIN_PATH))
                else:
                    self.check_pkgconfig(self.info.deliverable_type, openssl)
                    if force:
                        output, error = self.execute_command('{0}rpm -Uvh --force /tmp/{1}'\
                                                .format(environment, build.name), debug=False)
                    else:
                        output, error = self.execute_command('{0}rpm -i /tmp/{1}'\
                                                .format(environment, build.name), debug=False)
            elif self.info.deliverable_type == 'deb':
                if self.nonroot:
                    op, er = self.execute_command('cd %s; dpkg-deb -x %s %s '
                                                % (self.nr_home_path, build.name,
                                                   self.nr_home_path))
                    self.log_command_output(op, er)
                    output, error = self.execute_command('cd {0}{1}; ./bin/install/reloc.sh `pwd`'\
                                                        .format(self.nr_home_path, LINUX_CB_PATH))
                    self.log_command_output(output, error)
                    op, er = self.execute_command('pwd')
                    self.log_command_output(op, er)
                    """ command to start Couchbase Server in non root in ubuntu the same
                        as in centos above
                    """
                    if start_server_after_install:
                        output, error = self.execute_command('%s%scouchbase-server '\
                                                             '\-- -noinput -detached '\
                                                               % (self.nr_home_path,
                                                                  LINUX_COUCHBASE_BIN_PATH))
                else:
                    self.install_missing_lib()
                    if force:
                        output, error = self.execute_command('{0}dpkg --force-all -i /tmp/{1}'\
                                                 .format(environment, build.name), debug=False)
                    else:
                        output, error = self.execute_command('{0}dpkg -i /tmp/{1}'\
                                                 .format(environment, build.name), debug=False)

            if "SUSE" in self.info.distribution_type:
                if error and error[0] == 'insserv: Service network is missed in the runlevels 2'\
                                         ' 4 to use service couchbase-server':
                        log.info("Ignore this error for opensuse os")
                        error = []
            if output:
                server_ip = "\n\n**** Installing on server: {0} ****".format(self.ip)
                output.insert(0, server_ip)
            if error:
                server_ip = "\n\n**** Installing on server: {0} ****".format(self.ip)
                error.insert(0, server_ip)
            success &= self.log_command_output(output, error, track_words, debug=False)
            nonroot_path_start = ""
            if not self.nonroot:
                nonroot_path_start = "/"
                self.create_directory(path)
                output, error = self.execute_command('/opt/{0}/bin/{1}enable_core_dumps.sh  {2}'.
                                            format(server_type, abbr_product, path), debug=False)
                success &= self.log_command_output(output, error, track_words, debug=False)

            if vbuckets:
                """
                   From spock, the file to edit is in /opt/couchbase/bin/couchbase-server
                """
                output, error = self.execute_command("sed -i 's/export ERL_FULLSWEEP_AFTER/"
                                                     "export ERL_FULLSWEEP_AFTER\\n"
                                                     "{0}_NUM_VBUCKETS={1}\\n"
                                                     "export {0}_NUM_VBUCKETS/' "
                                                     "{3}opt/{2}/bin/{2}-server".
                                                     format(server_type.upper(), vbuckets,
                                            server_type, nonroot_path_start), debug=False)
                success &= self.log_command_output(output, error, track_words)
            if upr is not None:
                protocol = "tap"
                if upr:
                    protocol = "upr"
                output, error = \
                self.execute_command("sed -i 's/END INIT INFO/END INIT INFO\\nexport"
                              " COUCHBASE_REPL_TYPE={1}/' {2}opt/{0}/etc/{0}_init.d"\
                                                      .format(server_type, protocol,
                                                              nonroot_path_start))
                success &= self.log_command_output(output, error, track_words)
            if xdcr_upr == False:
                output, error = \
                    self.execute_command("sed -i 's/ulimit -c unlimited/ulimit "
                             "-c unlimited\\n    export XDCR_USE_OLD_PATH={1}/'"
                                                     "{2}opt/{0}/etc/{0}_init.d"\
                                                    .format(server_type, "true",
                                                            nonroot_path_start))
                success &= self.log_command_output(output, error, track_words)
            if fts_query_limit:
                output, error = \
                    self.execute_command("sed -i 's/export PATH/export PATH\\n"
                            "export CBFT_ENV_OPTIONS=bleveMaxResultWindow={1},hideUI=false/'\
                            {2}opt/{0}/bin/{0}-server".format(server_type, int(fts_query_limit),
                                                              nonroot_path_start))
                success &= self.log_command_output(output, error, track_words)
                startserver = True

            if enable_ipv6:
                output, error = \
                    self.execute_command("sed -i '/ipv6, /c \\{ipv6, true\}'. %s"
                        % testconstants.LINUX_STATIC_CONFIG)
                success &= self.log_command_output(output, error, track_words)
                startserver = True

            # skip output: [WARNING] couchbase-server is already started
            # dirname error skipping for CentOS-6.6 (MB-12536)
            track_words = ("error", "fail", "dirname")
            if startserver:
                self.start_couchbase()
                if (build.product_version.startswith("1.") \
                        or build.product_version.startswith("2.0.0"))\
                        and build.deliverable_type == "deb":
                    # skip error '* Failed to start couchbase-server' for 1.* & 2.0.0 builds(MB-7288)
                    # fix in 2.0.1 branch Change-Id: I850ad9424e295bbbb79ede701495b018b5dfbd51
                    log.warn("Error '* Failed to start couchbase-server' for 1.* "
                                                          "builds will be skipped")
                    self.log_command_output(output, error, track_words)
                else:
                    success &= self.log_command_output(output, error, track_words, debug=False)
        elif self.info.deliverable_type in ["zip"]:
            """ close Safari browser before install """
            self.terminate_process(self.info, "Safari")
            o, r = self.execute_command("ps aux | grep Archive | awk '{print $2}' | xargs kill -9")
            o, r = self.execute_command("ps aux | \
                grep '/Applications/Couchbase Server.app/Contents/MacOS/Couchbase Server' \
                                                      | awk '{print $2}' | xargs kill -9 ")
            self.log_command_output(o, r)
            self.sleep(20)
            output, error = self.execute_command("cd ~/Downloads ;sudo open couchbase-server*.zip")
            self.log_command_output(output, error)
            self.sleep(20)
            cmd1 = "sudo mv ~/Downloads/couchbase-server*/Couchbase\ Server.app /Applications/"
            cmd2 = "sudo xattr -d -r com.apple.quarantine /Applications/Couchbase\ Server.app"
            cmd3 = "sudo open /Applications/Couchbase\ Server.app"
            output, error = self.execute_command(cmd1)
            self.log_command_output(output, error)
            output, error = self.execute_command(cmd2)
            self.log_command_output(output, error)
            output, error = self.execute_command(cmd3)
            self.log_command_output(output, error)

        output, error = self.execute_command("rm -f *-diag.zip", debug=False)
        self.log_command_output(output, error, track_words, debug=False)
        return success

    def install_server_win(self, build, version, startserver=True,
                           vbuckets=None, fts_query_limit=None,
                           enable_ipv6=None, windows_msi=False):


        log.info('******start install_server_win ********')
        if windows_msi:
            if enable_ipv6:
                output, error = self.execute_command("cd /cygdrive/c/tmp;"
                                                 "msiexec /i {0}.msi USE_IPV6=true /qn "\
                                                    .format(version))
            else:
                output, error = self.execute_command("cd /cygdrive/c/tmp;"
                                                     "msiexec /i {0}.msi /qn " \
                                                     .format(version))
            self.log_command_output(output, error)
            if fts_query_limit:
                self.set_fts_query_limit_win(
                    name="CBFT_ENV_OPTIONS",
                    value="bleveMaxResultWindow={0}".format(int(fts_query_limit))
                )
            return len(error) == 0
        remote_path = None
        success = True
        track_words = ("warning", "error", "fail")
        if build.name.lower().find("membase") != -1:
            remote_path = testconstants.WIN_MB_PATH
            abbr_product = "mb"
        elif build.name.lower().find("couchbase") != -1:
            remote_path = WIN_CB_PATH
            abbr_product = "cb"

        if remote_path is None:
            raise Exception("its not a membase or couchbase?")
        self.extract_remote_info()
        log.info('deliverable_type : {0}'.format(self.info.deliverable_type))
        if self.info.type.lower() == 'windows':
            task = "install"
            bat_file = "install.bat"
            dir_paths = ['/cygdrive/c/automation', '/cygdrive/c/tmp']
            capture_iss_file = ""
            # build = self.build_url(params)
            self.create_multiple_dir(dir_paths)
            self.copy_files_local_to_remote('resources/windows/automation', \
                                                    '/cygdrive/c/automation')
            #self.create_windows_capture_file(task, abbr_product, version)
            capture_iss_file = self.modify_bat_file('/cygdrive/c/automation', \
                                             bat_file, abbr_product, version, task)
            self.stop_schedule_tasks()
            self.remove_win_backup_dir()
            self.remove_win_collect_tmp()
            log.info('sleep for 5 seconds before running task '
                                'schedule uninstall on {0}'.format(self.ip))

            """ the code below need to remove when bug MB-11985
                                                            is fixed in 3.0.1 """
            if task == "install" and (version[:5] in COUCHBASE_VERSION_2 or \
                                      version[:5] in COUCHBASE_FROM_VERSION_3):
                log.info("due to bug MB-11985, we need to delete below registry "
                                        "before install version 2.x.x and 3.x.x")
                output, error = self.execute_command("reg delete \
                         'HKLM\Software\Wow6432Node\Ericsson\Erlang\ErlSrv' /f ")
                self.log_command_output(output, error)
            """ end remove code """

            """ run task schedule to install cb server """
            output, error = self.execute_command("cmd /c schtasks /run /tn installme")
            success &= self.log_command_output(output, error, track_words)
            file_check = 'VERSION.txt'
            self.wait_till_file_added(remote_path, file_check, timeout_in_seconds=600)
            if version[:3] != "2.5":
                ended = self.wait_till_process_ended(build.product_version[:10])
                if not ended:
                    sys.exit("*****  Node %s failed to install  *****" % (self.ip))
            if version[:3] == "2.5":
                self.sleep(20, "wait for server to start up completely")
            else:
                self.sleep(10, "wait for server to start up completely")
            output, error = self.execute_command("rm -f *-diag.zip")
            self.log_command_output(output, error, track_words)
            if vbuckets and int(vbuckets) != 1024:
                self.set_vbuckets_win(vbuckets)
            if fts_query_limit:
                self.set_fts_query_limit_win(
                    name="CBFT_ENV_OPTIONS",
                    value="bleveMaxResultWindow={0}".format(int(fts_query_limit))
                )

            if "4.0" in version[:5]:
                """  remove folder if it exists in work around of bub MB-13046 """
                self.execute_command("rm -rf \
                /cygdrive/c/Jenkins/workspace/sherlock-windows/couchbase/install/etc/security")
                """ end remove code for bug MB-13046 """
            if capture_iss_file:
                    log.info("****Delete {0} in windows automation directory" \
                                                          .format(capture_iss_file))
                    output, error = self.execute_command("rm -f \
                               /cygdrive/c/automation/{0}".format(capture_iss_file))
                    self.log_command_output(output, error)
                    log.info("Delete {0} in slave resources/windows/automation dir" \
                             .format(capture_iss_file))
                    os.system("rm -f resources/windows/automation/{0}" \
                                                          .format(capture_iss_file))
            self.delete_file(WIN_TMP_PATH, build.product_version[:10] + ".exe")
            log.info('***** done install_server_win *****')
            return success


    def install_server_via_repo(self, deliverable_type, cb_edition, remote_client):
        success = True
        track_words = ("warning", "error", "fail")
        if cb_edition:
            cb_edition = "-" + cb_edition
        if deliverable_type == "deb":
            self.update_couchbase_release(remote_client, deliverable_type)
            output, error = self.execute_command("yes \
                   | apt-get install couchbase-server{0}".format(cb_edition))
            self.log_command_output(output, error)
            success &= self.log_command_output(output, error, track_words)
        elif deliverable_type == "rpm":
            self.update_couchbase_release(remote_client, deliverable_type)
            output, error = self.execute_command("yes \
                  | yum install couchbase-server{0}".format(cb_edition))
            self.log_command_output(output, error, track_words)
            success &= self.log_command_output(output, error, track_words)
        return success

    def update_couchbase_release(self, remote_client, deliverable_type):
        if deliverable_type == "deb":
            """ remove old couchbase-release package """
            log.info("remove couchbase-release at node {0}".format(self.ip))
            output, error = self.execute_command("dpkg --get-selections |\
                                                              grep couchbase")
            self.log_command_output(output, error)
            for str in output:
                if "couchbase-release" in str:
                    output, error = self.execute_command("apt-get \
                                                   purge -y couchbase-release")
            output, error = self.execute_command("dpkg --get-selections |\
                                                              grep couchbase")
            self.log_command_output(output, error)
            package_remove = True
            for str in output:
                if "couchbase-release" in str:
                    package_remove = False
                    log.info("couchbase-release is not removed at node {0}" \
                                                                  .format(self.ip))
                    sys.exit("***  Node %s failed to remove couchbase-release  ***"\
                                                                        % (self.ip))
            """ install new couchbase-release package """
            log.info("install new couchbase-release repo at node {0}" \
                                                             .format(self.ip))
            self.execute_command("rm -rf /tmp/couchbase-release*")
            self.execute_command("cd /tmp; wget {0}".format(CB_RELEASE_APT_GET_REPO))
            output, error = self.execute_command("yes | dpkg -i /tmp/couchbase-release*")
            self.log_command_output(output, error)
            output, error = self.execute_command("dpkg --get-selections |\
                                                              grep couchbase")
            package_updated = False
            for str in output:
                if "couchbase-release" in str:
                    package_updated = True
                    log.info("couchbase-release installed on node {0}" \
                                               .format(self.ip))
                    return package_updated
            if not package_updated:
                sys.exit("fail to install %s on node %s" % \
                                  (CB_RELEASE_APT_GET_REPO.rsplit("/",1)[-1], self.ip))
        elif deliverable_type == "rpm":
            """ remove old couchbase-release package """
            log.info("remove couchbase-release at node {0}".format(self.ip))
            output, error = self.execute_command("rpm -qa | grep couchbase")
            self.log_command_output(output, error)
            for str in output:
                if "couchbase-release" in str:
                    output, error = self.execute_command("rpm -e couchbase-release")
            output, error = self.execute_command("rpm -qa | grep couchbase")
            self.log_command_output(output, error)
            package_remove = True
            for str in output:
                if "couchbase-release" in str:
                    package_remove = False
                    log.info("couchbase-release is not removed at node {0}" \
                                                                  .format(self.ip))
                    sys.exit("***  Node %s failed to remove couchbase-release  ***"\
                                                                        % (self.ip))
            """ install new couchbase-release package """
            log.info("install new couchbase-release repo at node {0}" \
                                                             .format(self.ip))
            self.execute_command("rm -rf /tmp/couchbase-release*")
            self.execute_command("cd /tmp; wget {0}".format(CB_RELEASE_YUM_REPO))
            output, error = self.execute_command("yes | rpm -i /tmp/couchbase-release*")
            self.log_command_output(output, error)
            output, error = self.execute_command("rpm -qa | grep couchbase")
            package_updated = False
            for str in output:
                if "couchbase-release" in str:
                    package_updated = True
                    log.info("couchbase-release installed on node {0}" \
                                               .format(self.ip))
                    return package_updated
            if not package_updated:
                sys.exit("fail to install %s on node %s" % \
                                  (CB_RELEASE_YUM_REPO.rsplit("/",1)[-1], self.ip))

    def install_moxi(self, build):
        success = True
        track_words = ("warning", "error", "fail")
        self.extract_remote_info()
        log.info('deliverable_type : {0}'.format(self.info.deliverable_type))
        if self.info.type.lower() == 'windows':
            self.log.error('Not implemented')
        elif self.info.deliverable_type in ["rpm"]:
            output, error = self.execute_command('rpm -i /tmp/{0}'.format(build.name))
            if error and ' '.join(error).find("ERROR") != -1:
                success = False
        elif self.info.deliverable_type == 'deb':
            output, error = self.execute_command('dpkg -i /tmp/{0}'.format(build.name))
            if error and ' '.join(error).find("ERROR") != -1:
                success = False
        success &= self.log_command_output(output, '', track_words)
        return success

    def wait_till_file_deleted(self, remotepath, filename, timeout_in_seconds=180):
        end_time = time.time() + float(timeout_in_seconds)
        deleted = False
        log.info("file {0} checked at {1}".format(filename, remotepath))
        while time.time() < end_time and not deleted:
            # get the process list
            exists = self.file_exists(remotepath, filename)
            if exists:
                log.error('at {2} file {1} still exists' \
                          .format(remotepath, filename, self.ip))
                time.sleep(10)
            else:
                log.info('at {2} FILE {1} DOES NOT EXIST ANYMORE!' \
                         .format(remotepath, filename, self.ip))
                deleted = True
        return deleted

    def wait_till_file_added(self, remotepath, filename, timeout_in_seconds=180):
        end_time = time.time() + float(timeout_in_seconds)
        added = False
        log.info("file {0} checked at {1}".format(filename, remotepath))
        while time.time() < end_time and not added:
            # get the process list
            exists = self.file_exists(remotepath, filename)
            if not exists:
                log.error('at {2} file {1} does not exist' \
                          .format(remotepath, filename, self.ip))
                time.sleep(10)
            else:
                log.info('at {2} FILE {1} EXISTS!' \
                         .format(remotepath, filename, self.ip))
                added = True
        return added




    def wait_till_compaction_end(self, rest, bucket, timeout_in_seconds=60):
        end_time = time.time() + float(timeout_in_seconds)

        while time.time() < end_time:
            status, progress = rest.check_compaction_status(bucket)
            if status:
                log.info("compaction progress is %s" % progress)
                time.sleep(1)
            else:
                # the compaction task has completed
                return True

        log.error("auto compaction has not ended in {0} sec.".format(str(timeout_in_seconds)))
        return False

    def wait_till_process_ended(self, process_name, timeout_in_seconds=600):
        if process_name[-1:] == "-":
            process_name = process_name[:-1]
        end_time = time.time() + float(timeout_in_seconds)
        process_ended = False
        process_running = False
        count_process_not_run = 0
        while time.time() < end_time and not process_ended:
            output, error = self.execute_command("tasklist | grep {0}" \
                                                 .format(process_name))
            self.log_command_output(output, error)
            if output and process_name in output[0]:
                self.sleep(8, "wait for process ended!")
                process_running = True
            else:
                if process_running:
                    log.info("{1}: Alright, PROCESS {0} ENDED!" \
                             .format(process_name, self.ip))
                    process_ended = True
                else:
                    if count_process_not_run < 5:
                        log.error("{1}: process {0} may not run" \
                              .format(process_name, self.ip))
                        self.sleep(5)
                        count_process_not_run += 1
                    else:
                        log.error("{1}: process {0} did not run after 25 seconds"
                                                   .format(process_name, self.ip))
                        mesg = "kill in/uninstall job due to process was not run" \
                                                     .format(process_name, self.ip)
                        self.stop_current_python_running(mesg)
        if time.time() >= end_time and not process_ended:
            log.info("Process {0} on node {1} is still running"
                     " after 10 minutes VERSION.txt file was removed"
                                      .format(process_name, self.ip))
        return process_ended

    def terminate_processes(self, info, list):
        for process in list:
            type = info.distribution_type.lower()
            if type == "windows":
                # set debug=False if does not want to show log
                self.execute_command("taskkill /F /T /IM {0}".format(process),
                                                                  debug=False)
            elif type in LINUX_DISTRIBUTION_NAME:
                self.terminate_process(info, process, force=True)

    def remove_folders(self, list):
        for folder in list:
            output, error = self.execute_command("rm -rf {0}".format(folder),
                                                                debug=False)
            self.log_command_output(output, error)

    def couchbase_uninstall(self, windows_msi=False, product=None):
        log.info('{0} *****In couchbase uninstall****'.format( self.ip))
        linux_folders = ["/var/opt/membase", "/opt/membase", "/etc/opt/membase",
                         "/var/membase/data/*", "/opt/membase/var/lib/membase/*",
                         "/opt/couchbase", "/data/*"]
        terminate_process_list = ["beam.smp", "memcached", "moxi", "vbucketmigrator",
                                  "couchdb", "epmd", "memsup", "cpu_sup", "goxdcr", "erlang"]
        self.extract_remote_info()
        log.info(self.info.distribution_type)
        type = self.info.distribution_type.lower()
        fv, sv, bn = self.get_cbversion(type)
        if type == 'windows':
            product = "cb"
            query = BuildQuery()
            os_type = "exe"
            if windows_msi:
                os_type = "msi"
                self.info.deliverable_type = "msi"
            task = "uninstall"
            bat_file = "uninstall.bat"
            product_name = "couchbase-server-enterprise"
            version_path = "/cygdrive/c/Program Files/Couchbase/Server/"
            deleted = False
            capture_iss_file = ""
            log.info("kill any in/uninstall process from version 3 in node %s"
                                                                        % self.ip)
            self.terminate_processes(self.info, \
                                     [s + "-*" for s in COUCHBASE_FROM_VERSION_3])
            exist = self.file_exists(version_path, VERSION_FILE)
            log.info("Is VERSION file existed on {0}? {1}".format(self.ip, exist))
            if exist:
                cb_releases_version = []
                for x in CB_RELEASE_BUILDS:
                    if int(CB_RELEASE_BUILDS[x]):
                        cb_releases_version.append(x)

                build_name, short_version, full_version = \
                    self.find_build_version(version_path, VERSION_FILE, product)

                if "-" in full_version:
                    msi_build = full_version.split("-")
                    """
                       In spock from build 2924 and later release, we only support
                       msi installation method on windows
                    """
                    if msi_build[0] in COUCHBASE_FROM_SPOCK:
                        os_type = "msi"
                        windows_msi = True
                        self.info.deliverable_type = "msi"
                else:
                    mesg = " ***** ERROR: ***** \n" \
                           " Couchbase Server version format is not correct. \n" \
                           " It should be 0.0.0-DDDD format\n" \
                           % (self.ip, os.getpid())
                    self.stop_current_python_running(mesg)

                build_repo = MV_LATESTBUILD_REPO
                if full_version[:5] not in COUCHBASE_VERSION_2 and \
                   full_version[:5] not in COUCHBASE_VERSION_3:
                    if full_version[:3] in CB_VERSION_NAME:
                        build_repo = CB_REPO + CB_VERSION_NAME[full_version[:3]] + "/"
                    else:
                        sys.exit("version is not support yet")
                log.info("*****VERSION file exists."
                                   "Start to uninstall {0} on {1} server"
                                   .format(product, self.ip))
                if full_version[:3] == "4.0":
                    build_repo = SHERLOCK_BUILD_REPO
                log.info('Build name: {0}'.format(build_name))
                build_name = build_name.rstrip() + ".%s" % os_type
                log.info('Check if {0} is in tmp directory on {1} server'\
                                                       .format(build_name, self.ip))
                exist = self.file_exists("/cygdrive/c/tmp/", build_name)
                if not exist:  # if not exist in tmp dir, start to download that version
                    if short_version[:5] in cb_releases_version:
                        build = query.find_couchbase_release_build(product_name,
                                                        self.info.deliverable_type,
                                                       self.info.architecture_type,
                                                                     short_version,
                                                                  is_amazon=False,
                                 os_version=self.info.distribution_version.lower())
                    elif short_version in COUCHBASE_RELEASE_VERSIONS_3:
                        build = query.find_membase_release_build(product_name,
                                                   self.info.deliverable_type,
                                                  self.info.architecture_type,
                                                                short_version,
                                                              is_amazon=False,
                            os_version=self.info.distribution_version.lower())
                    else:
                        builds, changes = query.get_all_builds(version=full_version,
                                      deliverable_type=self.info.deliverable_type,
                                      architecture_type=self.info.architecture_type,
                                      edition_type=product_name, repo=build_repo,
                                      distribution_version=\
                                            self.info.distribution_version.lower())

                        build = query.find_build(builds, product_name, os_type,
                                                   self.info.architecture_type,
                                                                  full_version,
                            distribution_version=self.info.distribution_version.lower(),
                                  distribution_type=self.info.distribution_type.lower())
                    downloaded = self.download_binary_in_win(build.url, short_version)
                    if downloaded:
                        log.info('Successful download {0}.exe on {1} server'
                                             .format(short_version, self.ip))
                    else:
                        log.error('Download {0}.exe failed'.format(short_version))
                if not windows_msi:
                    dir_paths = ['/cygdrive/c/automation', '/cygdrive/c/tmp']
                    self.create_multiple_dir(dir_paths)
                    self.copy_files_local_to_remote('resources/windows/automation',
                                                      '/cygdrive/c/automation')
                    # modify bat file to run uninstall schedule task
                    #self.create_windows_capture_file(task, product, full_version)
                    capture_iss_file = self.modify_bat_file('/cygdrive/c/automation',
                                        bat_file, product, short_version, task)
                    self.stop_schedule_tasks()

                """ Remove this workaround when bug MB-14504 is fixed """
                log.info("Kill any un/install process leftover in sherlock")
                log.info("Kill any cbq-engine.exe in sherlock")
                self.execute_command('taskkill /F /T /IM cbq-engine.exe')
                log.info('sleep for 5 seconds before running task '
                                    'schedule uninstall on {0}'.format(self.ip))
                """ End remove this workaround when bug MB-14504 is fixed """

                self.stop_couchbase()
                time.sleep(5)
                # run schedule task uninstall couchbase server
                if windows_msi:
                    log.info("******** uninstall via msi method ***********")
                    output, error = \
                        self.execute_command("cd /cygdrive/c/tmp; msiexec /x %s /qn"\
                                             % build_name)
                    self.log_command_output(output, error)
                    var_dir = "/cygdrive/c/Program\ Files/Couchbase/Server/var"
                    self.execute_command("rm -rf %s" % var_dir)
                else:
                    output, error = \
                        self.execute_command("cmd /c schtasks /run /tn removeme")
                    self.log_command_output(output, error)
                deleted = self.wait_till_file_deleted(version_path, \
                                            VERSION_FILE, timeout_in_seconds=600)
                if not deleted:
                    log.error("Uninstall was failed at node {0}".format(self.ip))
                    sys.exit()
                if full_version[:3] != "2.5":
                    uninstall_process = full_version[:10]
                    if not windows_msi:
                        ended = self.wait_till_process_ended(uninstall_process)
                        if not ended:
                            sys.exit("****  Node %s failed to uninstall  ****"
                                                              % (self.ip))
                if full_version[:3] == "2.5":
                    self.sleep(20, "next step is to install")
                else:
                    self.sleep(10, "next step is to install")
                """ delete binary after uninstall """
                self.delete_file(WIN_TMP_PATH, build_name)
                """ the code below need to remove when bug MB-11328
                                                           is fixed in 3.0.1 """
                output, error = self.kill_erlang(os="windows")
                self.log_command_output(output, error)
                """ end remove code """


                """ the code below need to remove when bug MB-11985
                                                           is fixed in 3.0.1 """
                if full_version[:5] in COUCHBASE_VERSION_2 or \
                   full_version[:5] in COUCHBASE_FROM_VERSION_3:
                    log.info("due to bug MB-11985, we need to delete below registry")
                    output, error = self.execute_command("reg delete \
                            'HKLM\Software\Wow6432Node\Ericsson\Erlang\ErlSrv' /f ")
                    self.log_command_output(output, error)
                """ end remove code """
                if capture_iss_file and not windows_msi:
                    log.info("Delete {0} in windows automation directory" \
                                                          .format(capture_iss_file))
                    output, error = self.execute_command("rm -f \
                               /cygdrive/c/automation/{0}".format(capture_iss_file))
                    self.log_command_output(output, error)
                    log.info("Delete {0} in slave resources/windows/automation dir" \
                             .format(capture_iss_file))
                    os.system("rm -f resources/windows/automation/{0}" \
                                                          .format(capture_iss_file))
            else:
                log.info("*****No couchbase server on {0} server. Free to install" \
                                                                 .format(self.ip))
        elif type in LINUX_DISTRIBUTION_NAME:
            """ check if couchbase server installed by root """
            if self.nonroot:
                test = self.file_exists(LINUX_CB_PATH, VERSION_FILE)
                if self.file_exists(LINUX_CB_PATH, VERSION_FILE):
                    mesg = " ***** ERROR: ***** \n"\
                           " Couchbase Server was installed by root user. \n"\
                           " Use root user to uninstall it at %s \n"\
                           " This python process id: %d will be killed to stop the installation"\
                           % (self.ip, os.getpid())
                    self.stop_current_python_running(mesg)
                if self.file_exists(self.nr_home_path, NR_INSTALL_LOCATION_FILE):
                    output, error = self.execute_command("cat %s"
                                             % NR_INSTALL_LOCATION_FILE)
                    if output and output[0]:
                        log.info("Couchbase Server was installed in non default path %s"
                                                                            % output[0])
                        self.nr_home_path = output[0]
            # uninstallation command is different
            if type == "ubuntu":
                if self.nonroot:
                    """ check if old files from root install left in server """
                    if self.file_exists(LINUX_CB_PATH + "etc/couchdb/", "local.ini.debsave"):
                        print " ***** ERROR: ***** \n"\
                              "Couchbase Server files was left by root install at %s .\n"\
                              "Use root user to delete them all at server %s "\
                              " (rm -rf /opt/couchbase) to remove all couchbase folder.\n" \
                              % (LINUX_CB_PATH, self.ip)
                        sys.exit(1)
                    self.stop_server()
                else:
                    if sv in COUCHBASE_FROM_VERSION_4:
                        if self.is_enterprise(type):
                            if product is not None and product == "cbas":
                                uninstall_cmd = "dpkg -r {0};dpkg --purge {1};" \
                                        .format("couchbase-server-analytics", "couchbase-server-analytics")
                            else:
                                uninstall_cmd = "dpkg -r {0};dpkg --purge {1};" \
                                        .format("couchbase-server", "couchbase-server")
                        else:
                            uninstall_cmd = "dpkg -r {0};dpkg --purge {1};" \
                                         .format("couchbase-server-community",
                                                  "couchbase-server-community")
                    else:
                        uninstall_cmd = "dpkg -r {0};dpkg --purge {1};" \
                                     .format("couchbase-server", "couchbase-server")
                    output, error = self.execute_command(uninstall_cmd)
                    self.log_command_output(output, error)
            elif type in RPM_DIS_NAME:
                """ Sometimes, vm left with unfinish uninstall/install process.
                    We need to kill them before doing uninstall """
                if self.nonroot:
                    """ check if old files from root install left in server """
                    if self.file_exists(LINUX_CB_PATH + "etc/couchdb/", "local.ini.rpmsave"):
                        print "Couchbase Server files was left by root install at %s .\n"\
                               "Use root user to delete them all at server %s "\
                               " (rm -rf /opt/couchbase) to remove all couchbase folder.\n" \
                               % (LINUX_CB_PATH, self.ip)
                        sys.exit(1)
                    self.stop_server()
                else:
                    output, error = self.execute_command("killall -9 rpm")
                    self.log_command_output(output, error)
                    output, error = self.execute_command("rm -f /var/lib/rpm/.rpm.lock")
                    self.log_command_output(output, error)
                    if sv in COUCHBASE_FROM_VERSION_4:
                        if self.is_enterprise(type):
                            if product is not None and product == "cbas":
                                uninstall_cmd = 'rpm -e {0}'.format("couchbase-server-analytics")
                            else:
                                uninstall_cmd = 'rpm -e {0}'.format("couchbase-server")
                        else:
                            uninstall_cmd = 'rpm -e {0}' \
                                          .format("couchbase-server-community")
                    else:
                        uninstall_cmd = 'rpm -e {0}'.format("couchbase-server")
                    log.info('running rpm -e to remove couchbase-server')
                    output, error = self.execute_command(uninstall_cmd)
                    if output:
                        server_ip = "\n\n**** Uninstalling on server: {0} ****".format(self.ip)
                        output.insert(0, server_ip)
                    if error:
                        server_ip = "\n\n**** Uninstalling on server: {0} ****".format(self.ip)
                        error.insert(0, server_ip)
                    self.log_command_output(output, error)
            self.terminate_processes(self.info, terminate_process_list)
            if not self.nonroot:
                self.remove_folders(linux_folders)
                self.kill_memcached()
                output, error = self.execute_command("ipcrm")
                self.log_command_output(output, error)
        elif self.info.distribution_type.lower() == 'mac':
            self.stop_server(os='mac')
            """ close Safari browser before uninstall """
            self.terminate_process(self.info, "Safari")
            self.terminate_processes(self.info, terminate_process_list)
            output, error = self.execute_command("rm -rf /Applications/Couchbase\ Server.app")
            self.log_command_output(output, error)
            output, error = self.execute_command("rm -rf ~/Library/Application\ Support/Couchbase")
            self.log_command_output(output, error)
        if self.nonroot:
            if self.nr_home_path != "/home/%s/" % self.username:
                log.info("remove all non default install dir")
                output, error = self.execute_command("rm -rf %s"
                                                        % self.nr_home_path)
                self.log_command_output(output, error)
            else:
                log.info("Remove only Couchbase Server directories opt etc and usr ")
                output, error = self.execute_command("cd %s;rm -rf opt etc usr"
                                                           % self.nr_home_path)
                self.log_command_output(output, error)
                self.execute_command("cd %s;rm -rf couchbase-server-*"
                                                      % self.nr_home_path)
                output, error = self.execute_command("cd %s;ls -lh"
                                                      % self.nr_home_path)
                self.log_command_output(output, error)
            if "nr_install_dir" not in self.input.test_params:
                self.nr_home_path = "/home/%s/" % self.username
                output, error = self.execute_command(" :> %s"
                                             % NR_INSTALL_LOCATION_FILE)
                self.log_command_output(output, error)
            output, error = self.execute_command("ls -lh")
            self.log_command_output(output, error)

    def couchbase_win_uninstall(self, product, version, os_name, query):
        log.info('*****couchbase_win_uninstall****')
        builds, changes = query.get_all_builds(version=version)
        bat_file = "uninstall.bat"
        task = "uninstall"
        deleted = False

        self.extract_remote_info()
        ex_type = self.info.deliverable_type
        if self.info.architecture_type == "x86_64":
            os_type = "64"
        elif self.info.architecture_type == "x86":
            os_type = "32"
        if product == "cse":
            name = "couchbase-server-enterprise"
            version_path = "/cygdrive/c/Program Files/Couchbase/Server/"

        exist = self.file_exists(version_path, VERSION_FILE)
        if exist:
            # call uninstall function to install couchbase server
            # Need to detect csse or cse when uninstall.
            log.info("Start uninstall cb server on this server")
            build_name, rm_version = self.find_build_version(version_path, VERSION_FILE)
            log.info('build needed to do auto uninstall {0}'.format(build_name))
            # find installed build in tmp directory to match with currently installed version
            build_name = build_name.rstrip() + ".exe"
            log.info('Check if {0} is in tmp directory'.format(build_name))
            exist = self.file_exists("/cygdrive/c/tmp/", build_name)
            if not exist:  # if not exist in tmp dir, start to download that version build
                build = query.find_build(builds, name, ex_type, self.info.architecture_type, rm_version)
                downloaded = self.download_binary_in_win(build.url, rm_version)
                if downloaded:
                    log.info('Successful download {0}.exe'.format(rm_version))
                else:
                    log.error('Download {0}.exe failed'.format(rm_version))
            # copy required files to automation directory
            dir_paths = ['/cygdrive/c/automation', '/cygdrive/c/tmp']
            self.create_multiple_dir(dir_paths)
            self.copy_files_local_to_remote('resources/windows/automation', '/cygdrive/c/automation')
            # modify bat file to run uninstall schedule task
            self.modify_bat_file('/cygdrive/c/automation', bat_file, product, rm_version, task)
            self.stop_couchbase()

            """ the code below need to remove when bug MB-11328 is fixed in 3.0.1 """
            output, error = self.kill_erlang(os="windows")
            self.log_command_output(output, error)
            """ end remove code """

            self.sleep(5, "before running task schedule uninstall")
            # run schedule task uninstall couchbase server
            output, error = self.execute_command("cmd /c schtasks /run /tn removeme")
            self.log_command_output(output, error)
            deleted = self.wait_till_file_deleted(version_path, VERSION_FILE, timeout_in_seconds=600)
            if not deleted:
                log.error("Uninstall was failed at node {0}".format(self.ip))
                sys.exit()
            ended = self.wait_till_process_ended(build_name[:10])
            if not ended:
                sys.exit("*****  Node %s failed to uninstall  *****" % (self.ip))
            self.sleep(10, "next step is to install")
            output, error = self.execute_command("rm -f \
                       /cygdrive/c/automation/*_{0}_uninstall.iss".format(self.ip))
            self.log_command_output(output, error)
            output, error = self.execute_command("cmd /c schtasks /Query /FO LIST /TN removeme /V")
            self.log_command_output(output, error)
            output, error = self.execute_command("rm -f /cygdrive/c/tmp/{0}".format(build_name))
            self.log_command_output(output, error)

            """ the code below need to remove when bug MB-11328 is fixed in 3.0.1 """
            output, error = self.kill_erlang(os="windows")
            self.log_command_output(output, error)
            """ end remove code """
        else:
            log.info('No couchbase server on this server')

    def membase_uninstall(self, save_upgrade_config=False):
        linux_folders = ["/var/opt/membase", "/opt/membase", "/etc/opt/membase",
                         "/var/membase/data/*", "/opt/membase/var/lib/membase/*"]
        terminate_process_list = ["beam", "memcached", "moxi", "vbucketmigrator",
                                  "couchdb", "epmd"]
        self.extract_remote_info()
        log.info(self.info.distribution_type)
        type = self.info.distribution_type.lower()
        if type == 'windows':
            product = "mb"
            query = BuildQuery()
            builds, changes = query.get_all_builds()
            os_type = "exe"
            task = "uninstall"
            bat_file = "uninstall.bat"
            deleted = False
            product_name = "membase-server-enterprise"
            version_path = "/cygdrive/c/Program Files/Membase/Server/"

            exist = self.file_exists(version_path, VERSION_FILE)
            log.info("Is VERSION file existed? {0}".format(exist))
            if exist:
                log.info("VERSION file exists.  Start to uninstall")
                build_name, short_version, full_version = self.find_build_version(version_path, VERSION_FILE, product)
                if "1.8.0" in full_version or "1.8.1" in full_version:
                    product_name = "couchbase-server-enterprise"
                    product = "cb"
                log.info('Build name: {0}'.format(build_name))
                build_name = build_name.rstrip() + ".exe"
                log.info('Check if {0} is in tmp directory'.format(build_name))
                exist = self.file_exists("/cygdrive/c/tmp/", build_name)
                if not exist:  # if not exist in tmp dir, start to download that version build
                    build = query.find_build(builds, product_name, os_type, self.info.architecture_type, full_version)
                    downloaded = self.download_binary_in_win(build.url, short_version)
                    if downloaded:
                        log.info('Successful download {0}_{1}.exe'.format(product, short_version))
                    else:
                        log.error('Download {0}_{1}.exe failed'.format(product, short_version))
                dir_paths = ['/cygdrive/c/automation', '/cygdrive/c/tmp']
                self.create_multiple_dir(dir_paths)
                self.copy_files_local_to_remote('resources/windows/automation', '/cygdrive/c/automation')
                # modify bat file to run uninstall schedule task
                #self.create_windows_capture_file(task, product, full_version)
                self.modify_bat_file('/cygdrive/c/automation', bat_file, product, short_version, task)
                self.stop_schedule_tasks()
                self.sleep(5, "before running task schedule uninstall")
                # run schedule task uninstall Couchbase server
                output, error = self.execute_command("cmd /c schtasks /run /tn removeme")
                self.log_command_output(output, error)
                deleted = self.wait_till_file_deleted(version_path, VERSION_FILE, timeout_in_seconds=600)
                if not deleted:
                    log.error("Uninstall was failed at node {0}".format(self.ip))
                    sys.exit()
                ended = self.wait_till_process_ended(full_version[:10])
                if not ended:
                    sys.exit("****  Node %s failed to uninstall  ****" % (self.ip))
                self.sleep(10, "next step is to install")
                output, error = self.execute_command("rm -f \
                       /cygdrive/c/automation/*_{0}_uninstall.iss".format(self.ip))
                self.log_command_output(output, error)
                output, error = self.execute_command("cmd /c schtasks /Query /FO LIST /TN removeme /V")
                self.log_command_output(output, error)
            else:
                log.info("No membase server on this server.  Free to install")
        elif type in LINUX_DISTRIBUTION_NAME:
            # uninstallation command is different
            if type == "ubuntu":
                uninstall_cmd = 'dpkg -r {0};dpkg --purge {1};' \
                                 .format('membase-server', 'membase-server')
                output, error = self.execute_command(uninstall_cmd)
                self.log_command_output(output, error)
            elif type in RPM_DIS_NAME:
                """ Sometimes, vm left with unfinish uninstall/install process.
                    We need to kill them before doing uninstall """
                output, error = self.execute_command("killall -9 rpm")
                self.log_command_output(output, error)
                uninstall_cmd = 'rpm -e {0}'.format('membase-server')
                log.info('running rpm -e to remove membase-server')
                output, error = self.execute_command(uninstall_cmd)
                self.log_command_output(output, error)
            self.terminate_processes(self.info, terminate_process_list)
            if not save_upgrade_config:
                self.remove_folders(linux_folders)

    def moxi_uninstall(self):
        terminate_process_list = ["moxi"]
        self.extract_remote_info()
        log.info(self.info.distribution_type)
        type = self.info.distribution_type.lower()
        if type == 'windows':
            self.log.error("Not implemented")
        elif type == "ubuntu":
            uninstall_cmd = "dpkg -r {0};dpkg --purge {1};".format("moxi-server", "moxi-server")
            output, error = self.execute_command(uninstall_cmd)
            self.log_command_output(output, error)
        elif type in LINUX_DISTRIBUTION_NAME:
            uninstall_cmd = 'rpm -e {0}'.format("moxi-server")
            log.info('running rpm -e to remove couchbase-server')
            output, error = self.execute_command(uninstall_cmd)
            self.log_command_output(output, error)
        self.terminate_processes(self.info, terminate_process_list)

    def log_command_output(self, output, error, track_words=(), debug=True):
        # success means that there are no track_words in the output
        # and there are no errors at all, if track_words is not empty
        # if track_words=(), the result is not important, and we return True
        success = True
        for line in error:
            if debug:
                log.error(line)
            if track_words:
                if "Warning" in line and "hugepages" in line:
                    log.info("There is a warning about transparent_hugepage "
                             "may be in used when install cb server.\
                              So we will disable transparent_hugepage in this vm")
                    output, error = self.execute_command("echo never > \
                                        /sys/kernel/mm/transparent_hugepage/enabled")
                    self.log_command_output(output, error)
                    success = True
                elif "Warning" in line and "systemctl daemon-reload" in line:
                    log.info("Unit file of couchbase-server.service changed on disk,"
                             " we will run 'systemctl daemon-reload'")
                    output, error = self.execute_command("systemctl daemon-reload")
                    self.log_command_output(output, error)
                    success = True
                elif "Warning" in line and "RPMDB altered outside of yum" in line:
                    log.info("Warming: RPMDB altered outside of yum")
                    success = True
                elif "dirname" in line:
                    log.warning("Ignore dirname error message during couchbase "
                                "startup/stop/restart for CentOS 6.6 (MB-12536)")
                    success = True
                elif "Created symlink from /etc/systemd/system" in line:
                    log.info("This error is due to fix_failed_install.py script that only "
                             "happens in centos 7")
                    success = True
                elif "Created symlink /etc/systemd/system/multi-user.target.wants/couchbase-server.service" in line:
                    log.info(line)
                    log.info("This message comes only in debian8 and debian9 during installation. "
                             "This can be ignored.")
                    success = True
                else:
                    log.info("\nIf couchbase server is running with this error."
                              "\nGo to log_command_output to add error mesg to bypass it.")
                    success = False
        for line in output:
            if debug:
                log.info(line)
            if any(s.lower() in line.lower() for s in track_words):
                if "Warning" in line and "hugepages" in line:
                    log.info("There is a warning about transparent_hugepage may be in used when install cb server.\
                              So we will disable transparent_hugepage in this vm")
                    output, error = self.execute_command("echo never > /sys/kernel/mm/transparent_hugepage/enabled")
                    self.log_command_output(output, error, debug=debug)
                    success = True
                else:
                    success = False
                    log.error('something wrong happened on {0}!!! output:{1}, error:{2}, track_words:{3}'
                              .format(self.ip, output, error, track_words))
        return success

    def execute_commands_inside(self, main_command,query, queries,bucket1,password,bucket2,source,subcommands=[], min_output_size=0,
                                end_msg='', timeout=250):
        self.extract_remote_info()
        filename = "/tmp/test2"
        iswin=False

        if self.info.type.lower() == 'windows':
            iswin = True
            filename = "/cygdrive/c/tmp/test.txt"

        filedata = ""
        if not(query==""):
            main_command = main_command + " -s=\"" + query+ '"'
        elif (self.remote and not(queries=="")):
            sftp = self._ssh_client.open_sftp()
            filein = sftp.open(filename, 'w')
            for query in queries:
                filein.write(query)
                filein.write('\n')
            fileout = sftp.open(filename,'r')
            filedata = fileout.read()
            #print filedata
            fileout.close()
        elif not(queries==""):
            f = open(filename, 'w')
            for query in queries:
                f.write(query)
                f.write('\n')
            f.close()
            fileout = open(filename,'r')
            filedata = fileout.read()
            fileout.close()

        newdata = filedata.replace("bucketname",bucket2)
        newdata = newdata.replace("user",bucket1)
        newdata = newdata.replace("pass",password)
        newdata = newdata.replace("bucket1",bucket1)

        newdata = newdata.replace("user1",bucket1)
        newdata = newdata.replace("pass1",password)
        newdata = newdata.replace("bucket2",bucket2)
        newdata = newdata.replace("user2",bucket2)
        newdata = newdata.replace("pass2",password)

        if (self.remote and not(queries=="")) :
            f = sftp.open(filename,'w')
            f.write(newdata)
            f.close()
        elif not(queries==""):
            f = open(filename,'w')
            f.write(newdata)
            f.close()
        if not(queries==""):
            if (source):
                if iswin:
                    main_command = main_command + "  -s=\"\SOURCE " + 'c:\\\\tmp\\\\test.txt'
                else:
                    main_command = main_command + "  -s=\"\SOURCE " + filename+ '"'
            else:
                if iswin:
                    main_command = main_command + " -f=" + 'c:\\\\tmp\\\\test.txt'
                else:
                    main_command = main_command + " -f=" + filename

        log.info("running command on {0}: {1}".format(self.ip, main_command))
        output=""
        if self.remote:
            (stdin, stdout, stderro) = self._ssh_client.exec_command(main_command)
            time.sleep(10)
            count = 0
            for line in stdout.readlines():
                if (count == 0) and line.lower().find("error") > 0:
                   output = "status:FAIL"
                   break

                #if line.find("results") > 0 or line.find("status") > 0 or line.find("metrics") or line.find("elapsedTime")> 0 or  line.find("executionTime")> 0 or line.find("resultCount"):
                if (count > 0):
                    output+=line.strip()
                    output = output.strip()
                    if "Inputwasnotastatement" in output:
                        output = "status:FAIL"
                        break
                    if "timeout" in output:
                        output = "status:timeout"
                else:
                    count+=1
            stdin.close()
            stdout.close()
            stderro.close()
           # main_command = main_command + " < " + '/tmp/' + filename
           # stdin,stdout, ssh_stderr = ssh.exec_command(main_command)
           # stdin.close()
           # output = []
           # for line in stdout.read().splitlines():
           #   print(line)
           #   output = output.append(line)
           # f.close()
           # ssh.close()

           #output = output + end_msg

        else:
            p = Popen(main_command , shell=True, stdout=PIPE, stderr=PIPE)
            stdout, stderro = p.communicate()
            output = stdout
            print output
            time.sleep(1)
        # for cmd in subcommands:
        #       log.info("running command {0} inside {1} ({2})".format(
        #                                                 main_command, cmd, self.ip))
        #       stdin.channel.send("{0}\n".format(cmd))
        #       end_time = time.time() + float(timeout)
        #       while True:
        #           if time.time() > end_time:
        #               raise Exception("no output in {3} sec running command \
        #                                {0} inside {1} ({2})".format(main_command,
        #                                                             cmd, self.ip,
        #                                                             timeout))
        #           output = stdout.channel.recv(1024)
        #           if output.strip().endswith(end_msg) and len(output) >= min_output_size:
        #                   break
        #           time.sleep(2)
        #       log.info("{0}:'{1}' -> '{2}' output\n: {3}".format(self.ip, main_command, cmd, output))
        # stdin.close()
        # stdout.close()
        # stderro.close()
        if (self.remote and not(queries=="")) :
            sftp.remove(filename)
            sftp.close()
        elif not(queries==""):
            os.remove(filename)

        output = re.sub('\s+', '', output)
        return (output)

    def execute_command(self, command, info=None, debug=True, use_channel=False):
        if getattr(self, "info", None) is None and info is not None :
            self.info = info
        else:
            self.extract_remote_info()

        if self.info.type.lower() == 'windows':
            self.use_sudo = False

        if self.use_sudo:
            command = "sudo " + command

        return self.execute_command_raw(command, debug=debug, use_channel=use_channel)

    def execute_command_raw(self, command, debug=True, use_channel=False):
        if debug:
            log.info("running command.raw on {0}: {1}".format(self.ip, command))
        output = []
        error = []
        temp = ''
        if self.remote and self.use_sudo or use_channel:
            channel = self._ssh_client.get_transport().open_session()
            channel.get_pty()
            channel.settimeout(900)
            stdin = channel.makefile('wb')
            stdout = channel.makefile('rb')
            stderro = channel.makefile_stderr('rb')
            channel.exec_command(command)
            data = channel.recv(1024)
            while data:
                temp += data
                data = channel.recv(1024)
            channel.close()
            stdin.close()
        elif self.remote:
            stdin, stdout, stderro = self._ssh_client.exec_command(command)
            stdin.close()

        if not self.remote:
            p = Popen(command , shell=True, stdout=PIPE, stderr=PIPE)
            output, error = p.communicate()

        if self.remote:
            for line in stdout.read().splitlines():
                output.append(line)
            for line in stderro.read().splitlines():
                error.append(line)
            if temp:
                line = temp.splitlines()
                output.extend(line)
            stdout.close()
            stderro.close()
        if debug:
            log.info('command executed successfully')
        return output, error

    def execute_non_sudo_command(self, command, info=None, debug=True, use_channel=False):
        info = info or self.extract_remote_info()
        self.info = info

        return self.execute_command_raw(command, debug=debug, use_channel=use_channel)

    def terminate_process(self, info=None, process_name='',force=False):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("taskkill /F /T /IM {0}*"\
                                            .format(process_name), debug=False)
            self.log_command_output(o, r)
        else:
            if (force == True):
                o, r = self.execute_command("kill -9 "
                   "$(ps aux | grep '{0}' |  awk '{{print $2}}') "\
                               .format(process_name), debug=False)
                self.log_command_output(o, r)
            else:
                o, r = self.execute_command("kill "
                    "$(ps aux | grep '{0}' |  awk '{{print $2}}') "\
                                 .format(process_name), debug=False)
                self.log_command_output(o, r)

    def disconnect(self):
        self._ssh_client.close()

    def extract_remote_info(self):
        # initialize params
        os_distro = "linux"
        os_version = "default"
        is_linux_distro = True
        self.use_sudo = False
        is_mac = False
        arch = "local"
        ext = "local"
        # use ssh to extract remote machine info
        # use sftp to if certain types exists or not
        if getattr(self, "info", None) is not None and isinstance(self.info, RemoteMachineInfo):
            return self.info
        mac_check_cmd = "sw_vers | grep ProductVersion | awk '{ print $2 }'"
        if self.remote:
            stdin, stdout, stderro = self._ssh_client.exec_command(mac_check_cmd)
            stdin.close()
            ver, err = stdout.read(), stderro.read()
        else:
            p = Popen(mac_check_cmd , shell=True, stdout=PIPE, stderr=PIPE)
            ver, err = p.communicate()
        if not err and ver:
            os_distro = "Mac"
            os_version = ver
            is_linux_distro = True
            is_mac = True
            self.use_sudo = False
        elif self.remote:
            is_mac = False
            sftp = self._ssh_client.open_sftp()
            filenames = sftp.listdir('/etc/')
            os_distro = ""
            os_version = ""
            is_linux_distro = False
            for name in filenames:
                if name == 'issue':
                    # it's a linux_distro . let's downlaod this file
                    # format Ubuntu 10.04 LTS \n \l
                    filename = 'etc-issue-{0}'.format(uuid.uuid4())
                    sftp.get(localpath=filename, remotepath='/etc/issue')
                    file = open(filename)
                    etc_issue = ''
                    # let's only read the first line
                    for line in file.xreadlines():
                        # for SuSE that has blank first line
                        if line.rstrip('\n'):
                            etc_issue = line
                            break
                        # strip all extra characters
                    etc_issue = etc_issue.rstrip('\n').rstrip(' ').rstrip('\\l').rstrip(' ').rstrip('\\n').rstrip(' ')
                    if etc_issue.lower().find('ubuntu') != -1:
                        os_distro = 'Ubuntu'
                        os_version = etc_issue
                        tmp_str = etc_issue.split()
                        if tmp_str and tmp_str[1][:2].isdigit():
                            os_version = "Ubuntu %s" % tmp_str[1][:5]
                        is_linux_distro = True
                    elif etc_issue.lower().find('debian') != -1:
                        os_distro = 'Ubuntu'
                        os_version = etc_issue
                        is_linux_distro = True
                    elif etc_issue.lower().find('mint') != -1:
                        os_distro = 'Ubuntu'
                        os_version = etc_issue
                        is_linux_distro = True
                    elif etc_issue.lower().find('amazon linux ami') != -1:
                        os_distro = 'CentOS'
                        os_version = etc_issue
                        is_linux_distro = True
                    elif etc_issue.lower().find('centos') != -1:
                        os_distro = 'CentOS'
                        os_version = etc_issue
                        is_linux_distro = True
                    elif etc_issue.lower().find('red hat') != -1:
                        os_distro = 'Red Hat'
                        os_version = etc_issue
                        is_linux_distro = True
                    elif etc_issue.lower().find('opensuse') != -1:
                        os_distro = 'openSUSE'
                        os_version = etc_issue
                        is_linux_distro = True
                    elif etc_issue.lower().find('suse linux') != -1:
                        os_distro = 'SUSE'
                        os_version = etc_issue
                        tmp_str = etc_issue.split()
                        if tmp_str and tmp_str[6].isdigit():
                            os_version = "SUSE %s" % tmp_str[6]
                        is_linux_distro = True
                    elif etc_issue.lower().find('oracle linux') != -1:
                        os_distro = 'Oracle Linux'
                        os_version = etc_issue
                        is_linux_distro = True
                    else:
                        log.info("It could be other operating system."
                                 "  Go to check at other location")
                    file.close()
                    # now remove this file
                    os.remove(filename)
                    break
            else:
                os_distro = "linux"
                os_version = "default"
                is_linux_distro = True
                self.use_sudo = False
                is_mac = False
                arch = "local"
                ext = "local"
                filenames = []
            """ for centos 7 only """
            for name in filenames:
                if name == "redhat-release":
                    filename = 'redhat-release-{0}'.format(uuid.uuid4())
                    if self.remote:
                        sftp.get(localpath=filename, remotepath='/etc/redhat-release')
                    else:
                        p = Popen("cat /etc/redhat-release > {0}".format(filename) , shell=True, stdout=PIPE, stderr=PIPE)
                        var, err = p.communicate()
                    file = open(filename)
                    redhat_release = ''
                    for line in file.xreadlines():
                        redhat_release = line
                        break
                    redhat_release = redhat_release.rstrip('\n').rstrip('\\l').rstrip('\\n')
                    """ in ec2: Red Hat Enterprise Linux Server release 7.2 """
                    if redhat_release.lower().find('centos') != -1 \
                         or redhat_release.lower().find('linux server') != -1:
                        if redhat_release.lower().find('release 7') != -1:
                            os_distro = 'CentOS'
                            os_version = "CentOS 7"
                            is_linux_distro = True
                    else:
                        log.error("Could not find OS name."
                                 "It could be unsupport OS")
                    file.close()
                    os.remove(filename)
                    break

        if self.remote:
            if self.find_file("/cygdrive/c/Windows", "win.ini"):
                log.info("This is windows server!")
                is_linux_distro = False
        if not is_linux_distro:
            arch = ''
            os_version = 'unknown windows'
            win_info = self.find_windows_info()
            info = RemoteMachineInfo()
            info.type = win_info['os']
            info.windows_name = win_info['os_name']
            info.distribution_type = win_info['os']
            info.architecture_type = win_info['os_arch']
            info.ip = self.ip
            info.distribution_version = win_info['os']
            info.deliverable_type = 'exe'
            info.cpu = self.get_cpu_info(win_info)
            info.disk = self.get_disk_info(win_info)
            info.ram = self.get_ram_info(win_info)
            info.hostname = self.get_hostname(win_info)
            info.domain = self.get_domain(win_info)
            self.info = info
            return info
        else:
            # now run uname -m to get the architechtre type
            if self.remote:
                stdin, stdout, stderro = self._ssh_client.exec_command('uname -m')
                stdin.close()
                os_arch = ''
                text = stdout.read().splitlines()
            else:
                p = Popen('uname -m' , shell=True, stdout=PIPE, stderr=PIPE)
                text, err = p.communicate()
                os_arch = ''
            for line in text:
                os_arch += line
                # at this point we should know if its a linux or windows ditro
            ext = { 'Ubuntu' : "deb",
                   'CentOS'  : "rpm",
                   'Red Hat' : "rpm",
                   "Mac"     : "zip",
                   "Debian"  : "deb",
                   "openSUSE": "rpm",
                   "SUSE"    : "rpm",
                   "Oracle Linux": "rpm"}.get(os_distro, '')
            arch = {'i686': 'x86',
                    'i386': 'x86'}.get(os_arch, os_arch)

            info = RemoteMachineInfo()
            info.type = "Linux"
            info.distribution_type = os_distro
            info.architecture_type = arch
            info.ip = self.ip
            info.distribution_version = os_version
            info.deliverable_type = ext
            info.cpu = self.get_cpu_info(mac=is_mac)
            info.disk = self.get_disk_info(mac=is_mac)
            info.ram = self.get_ram_info(mac=is_mac)
            info.hostname = self.get_hostname()
            info.domain = self.get_domain()
            self.info = info
            return info

    def get_extended_windows_info(self):
        info = {}
        win_info = self.extend_windows_info()
        info['ram'] = self.get_ram_info(win_info)
        info['disk'] = self.get_disk_info(win_info)
        info['cpu'] = self.get_cpu_info(win_info)
        info['hostname'] = self.get_hostname()
        return info

    def get_hostname(self, win_info=None):
        if win_info:
            if 'Host Name' not in win_info:
                win_info = self.create_windows_info()
            o = win_info['Host Name']
        o, r = self.execute_command_raw('hostname', debug=False)
        if o:
            return o

    def get_domain(self, win_info=None):
        if win_info:
            o, _ = self.execute_batch_command('ipconfig')
            suffix_dns_row = [row for row in o if row.find(" Connection-specific DNS Suffix") != -1 and \
                             len(row.split(':')[1]) > 1]
            if suffix_dns_row ==[]:
                #'   Connection-specific DNS Suffix  . : '
                ret=""
            else:
                ret = suffix_dns_row[0].split(':')[1].strip()
        else:
            ret = self.execute_command_raw('hostname -d', debug=False)
        return ret

    def get_full_hostname(self):
        info = self.extract_remote_info()
        if not info.domain:
            return None
        log.info("hostname of this {0} is {1}"
                 .format(self.ip, info.hostname[0]))
        if info.type.lower() == 'windows':
            log.info("domain name of this {0} is {1}"
                 .format(self.ip, info.domain))
            return '%s.%s' % (info.hostname[0], info.domain)
        else:
            if info.domain[0]:
                if info.domain[0][0]:
                    log.info("domain name of this {0} is {1}"
                         .format(self.ip, info.domain[0][0]))
                    return "{0}.{1}".format(info.hostname[0], info.domain[0][0])
                else:
                    mesg = "Need to set domain name in server {0} like 'sc.couchbase.com'"\
                                                                           .format(self.ip)
                    raise Exception(mesg)

    def get_cpu_info(self, win_info=None, mac=False):
        if win_info:
            if 'Processor(s)' not in win_info:
                win_info = self.create_windows_info()
            o = win_info['Processor(s)']
        elif mac:
            o, r = self.execute_command_raw('/sbin/sysctl -n machdep.cpu.brand_string')
        else:
            o, r = self.execute_command_raw('cat /proc/cpuinfo', debug=False)
        if o:
            return o

    def get_ram_info(self, win_info=None, mac=False):
        if win_info:
            if 'Virtual Memory Max Size' not in win_info:
                win_info = self.create_windows_info()
            o = "Virtual Memory Max Size =" + win_info['Virtual Memory Max Size'] + '\n'
            o += "Virtual Memory Available =" + win_info['Virtual Memory Available'] + '\n'
            o += "Virtual Memory In Use =" + win_info['Virtual Memory In Use']
        elif mac:
            o, r = self.execute_command_raw('/sbin/sysctl -n hw.memsize',debug=False)
        else:
            o, r = self.execute_command_raw('cat /proc/meminfo', debug=False)
        if o:
            return o

    def get_disk_info(self, win_info=None, mac=False):
        if win_info:
            if 'Total Physical Memory' not in win_info:
                win_info = self.create_windows_info()
            o = "Total Physical Memory =" + win_info['Total Physical Memory'] + '\n'
            o += "Available Physical Memory =" + win_info['Available Physical Memory']
        elif mac:
            o, r = self.execute_command_raw('df -h', debug=False)
        else:
            o, r = self.execute_command_raw('df -Th', debug=False)
        if o:
            return o

    def get_memcache_pid(self):
         self.extract_remote_info()
         if self.info.type == 'Linux':
             o, _ = self.execute_command("ps -eo comm,pid | awk '$1 == \"memcached\" { print $2 }'")
             return o[0]
         elif self.info.type == 'windows':
             output, error = self.execute_command('tasklist| grep memcache', debug=False)
             if error or output == [""] or output == []:
                  return None
             words = output[0].split(" ")
             words = filter(lambda x: x != "", words)
             return words[1]

    def cleanup_data_config(self, data_path):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            if "c:/Program Files" in data_path:
                data_path = data_path.replace("c:/Program Files",
                                              "/cygdrive/c/Program\ Files")
            o, r = self.execute_command("rm -rf ""{0}""/*".format(data_path))
            self.log_command_output(o, r)
            o, r = self.execute_command("rm -rf ""{0}""/*"\
                                             .format(data_path.replace("data","config")))
            self.log_command_output(o, r)
        else:
            o, r = self.execute_command("rm -rf {0}/*".format(data_path))
            self.log_command_output(o, r)
            o, r = self.execute_command("rm -rf {0}/*"\
                                             .format(data_path.replace("data","config")))
            self.log_command_output(o, r)

    def stop_couchbase(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("net stop couchbaseserver")
            self.log_command_output(o, r)
            self.sleep(10, "Wait to stop service completely")
        if self.info.type.lower() == "linux":
            if self.nonroot:
                log.info("Stop Couchbase Server with non root method")
                o, r = self.execute_command('%s%scouchbase-server -k '\
                                         % (self.nr_home_path,
                                            LINUX_COUCHBASE_BIN_PATH))
                self.log_command_output(o, r)
            else:
                fv, sv, bn = self.get_cbversion("linux")
                if self.info.distribution_version.lower() in SYSTEMD_SERVER \
                                             and sv in COUCHBASE_FROM_WATSON:
                    """from watson, systemd is used in centos 7, suse 12 """
                    log.info("Running systemd command on this server")
                    o, r = self.execute_command("systemctl stop couchbase-server.service")
                    self.log_command_output(o, r)
                else:
                    o, r = self.execute_command("/etc/init.d/couchbase-server stop",\
                                                         self.info, use_channel=True)
                    self.log_command_output(o, r)
        if self.info.distribution_type.lower() == "mac":
            o, r = self.execute_command("ps aux | grep Couchbase | awk '{print $2}'\
                                                                   | xargs kill -9")
            self.log_command_output(o, r)
            o, r = self.execute_command("killall -9 epmd")
            self.log_command_output(o, r)

    def start_couchbase(self):
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            o, r = self.execute_command("net start couchbaseserver")
            self.log_command_output(o, r)
        if self.info.type.lower() == "linux":
            if self.nonroot:
                log.info("Start Couchbase Server with non root method")
                o, r = self.execute_command('%s%scouchbase-server \-- -noinput -detached '\
                                                           % (self.nr_home_path,
                                                              LINUX_COUCHBASE_BIN_PATH))
                self.log_command_output(o, r)
            else:
                fv, sv, bn = self.get_cbversion("linux")
                if self.info.distribution_version.lower() in SYSTEMD_SERVER \
                       and sv in COUCHBASE_FROM_WATSON:
                    """from watson, systemd is used in centos 7, suse 12 """
                    log.info("Running systemd command on this server")
                    o, r = self.execute_command("systemctl start couchbase-server.service")
                    self.log_command_output(o, r)
                else:
                    o, r = self.execute_command("/etc/init.d/couchbase-server start")
                    self.log_command_output(o, r)
        if self.info.distribution_type.lower() == "mac":
            o, r = self.execute_command("open /Applications/Couchbase\ Server.app")
            self.log_command_output(o, r)

    def pause_memcached(self, os="linux", timesleep=30):
        log.info("*** pause memcached process ***")
        if os == "windows":
            self.check_cmd("pssuspend")
            cmd = "pssuspend $(tasklist | grep  memcached | gawk '{printf $2}')"
            o, r = self.execute_command(cmd)
            self.log_command_output(o, [])
        else:
            o, r = self.execute_command("killall -SIGSTOP memcached")
            self.log_command_output(o, r)
        log.info("wait %s seconds to make node down." % timesleep)
        time.sleep(timesleep)

    def unpause_memcached(self, os="linux"):
        log.info("*** unpause memcached process ***")
        if os == "windows":
            cmd = "pssuspend -r $(tasklist | grep  memcached | gawk '{printf $2}')"
            o, r = self.execute_command(cmd)
            self.log_command_output(o, [])
        else:
            o, r = self.execute_command("killall -SIGCONT memcached")
            self.log_command_output(o, r)

    def pause_beam(self):
        o, r = self.execute_command("killall -SIGSTOP beam")
        self.log_command_output(o, r)

    def unpause_beam(self):
        o, r = self.execute_command("killall -SIGCONT beam")
        self.log_command_output(o, r)


    # TODO: Windows
    def flush_os_caches(self):
        self.extract_remote_info()
        if self.info.type.lower() == "linux":
            o, r = self.execute_command("sync")
            self.log_command_output(o, r)
            o, r = self.execute_command("/sbin/sysctl vm.drop_caches=3")
            self.log_command_output(o, r)

    def get_data_file_size(self, path=None):

        output, error = self.execute_command('du -b {0}'.format(path))
        if error:
            return 0
        else:
            for line in output:
                size = line.strip().split('\t')
                if size[0].isdigit():
                    print size[0]
                    return size[0]
                else:
                    return 0

    def get_process_statistics(self, process_name=None, process_pid=None):
        '''
        Gets process statistics for windows nodes
        WMI is required to be intalled on the node
        stats_windows_helper should be located on the node
        '''
        self.extract_remote_info()
        remote_command = "cd ~; /cygdrive/c/Python27/python stats_windows_helper.py"
        if process_name:
            remote_command.append(" " + process_name)
        elif process_pid:
            remote_command.append(" " + process_pid)

        if self.info.type.lower() == "windows":
            o, r = self.execute_command(remote_command, self.info)
            if r:
                log.error("Command didn't run successfully. Error: {0}".format(r))
            return o;
        else:
            log.error("Function is implemented only for Windows OS")
            return None

    def get_process_statistics_parameter(self, parameter, process_name=None, process_pid=None):
       if not parameter:
           log.error("parameter cannot be None")

       parameters_list = self.get_process_statistics(process_name, process_pid)

       if not parameters_list:
           log.error("no statistics found")
           return None
       parameters_dic = dict(item.split(' = ') for item in parameters_list)

       if parameter in parameters_dic:
           return parameters_dic[parameter]
       else:
           log.error("parameter '{0}' is not found".format(parameter))
           return None

    def set_environment_variable(self, name, value):
        """Request an interactive shell session, export custom variable and
        restart Couchbase server.

        Shell session is necessary because basic SSH client is stateless.
        """

        shell = self._ssh_client.invoke_shell()
        self.extract_remote_info()
        if self.info.type.lower() == "windows":
            shell.send('net stop CouchbaseServer\n')
            shell.send('set {0}={1}\n'.format(name, value))
            shell.send('net start CouchbaseServer\n')
        elif self.info.type.lower() == "linux":
            shell.send('export {0}={1}\n'.format(name, value))
            if "centos 7" in self.info.distribution_version.lower():
                """from watson, systemd is used in centos 7 """
                log.info("this node is centos 7.x")
                shell.send("service couchbase-server restart\n")
            else:
                shell.send('/etc/init.d/couchbase-server restart\n')
        shell.close()

    def change_env_variables(self, dict):
        prefix = "\\n    "
        shell = self._ssh_client.invoke_shell()
        _, sv, _ = self.get_cbversion("linux")
        if sv in COUCHBASE_FROM_SPOCK:
            init_file = "couchbase-server"
            file_path = "/opt/couchbase/bin/"
        else:
            init_file = "couchbase_init.d"
            file_path = "/opt/couchbase/etc/"
        environmentVariables = ""
        self.extract_remote_info()
        if self.info.type.lower() == "windows":
            init_file = "service_start.bat"
            file_path = "\"/cygdrive/c/Program Files/Couchbase/Server/bin/\""
            prefix = "\\n"
        backupfile = file_path + init_file + ".bak"
        sourceFile = file_path + init_file
        o, r = self.execute_command("cp " + sourceFile + " " + backupfile)
        self.log_command_output(o, r)
        command = "sed -i 's/{0}/{0}".format("ulimit -l unlimited")
        """
           From spock, the file to edit is in /opt/couchbase/bin/couchbase-server
        """
        for key in dict.keys():
            o, r = self.execute_command("sed -i 's/{1}.*//' {0}".format(sourceFile, key))
            self.log_command_output(o, r)
            if sv in COUCHBASE_FROM_SPOCK:
                o, r = self.execute_command("sed -i 's/export ERL_FULLSWEEP_AFTER/export ERL_FULLSWEEP_AFTER\\n{1}="
                                            "{2}\\nexport {1}/' {0}".
                                            format(sourceFile, key, dict[key]))
                self.log_command_output(o, r)
        if self.info.type.lower() == "windows":
            command = "sed -i 's/{0}/{0}".format("set NS_ERTS=%NS_ROOT%\erts-5.8.5.cb1\bin")

        for key in dict.keys():
            if self.info.type.lower() == "windows":
                environmentVariables += prefix + 'set {0}={1}'.format(key, dict[key])
            else:
                environmentVariables += prefix + 'export {0}={1}'.format(key, dict[key])

        command += environmentVariables + "/'" + " " + sourceFile
        o, r = self.execute_command(command)
        self.log_command_output(o, r)

        if self.info.type.lower() == "linux":
            if "centos 7" in self.info.distribution_version.lower():
                """from watson, systemd is used in centos 7 """
                log.info("this node is centos 7.x")
                o, r = self.execute_command("service couchbase-server restart")
                self.log_command_output(o, r)
            else:
                o, r = self.execute_command("/etc/init.d/couchbase-server restart")
                self.log_command_output(o, r)
        else:
            o, r = self.execute_command("net stop couchbaseserver")
            self.log_command_output(o, r)
            o, r = self.execute_command("net start couchbaseserver")
            self.log_command_output(o, r)
        shell.close()

    def reset_env_variables(self):
        shell = self._ssh_client.invoke_shell()
        if getattr(self, "info", None) is None:
            self.info = self.extract_remote_info()
        """
           From spock, the file to edit is in /opt/couchbase/bin/couchbase-server
        """
        _, sv, _ = self.get_cbversion("linux")
        if sv in COUCHBASE_FROM_SPOCK:
            init_file = "couchbase-server"
            file_path = "/opt/couchbase/bin/"
        else:
            init_file = "couchbase_init.d"
            file_path = "/opt/couchbase/etc/"
        if self.info.type.lower() == "windows":
            init_file = "service_start.bat"
            file_path = "/cygdrive/c/Program\ Files/Couchbase/Server/bin/"
        backupfile = file_path + init_file + ".bak"
        sourceFile = file_path + init_file
        o, r = self.execute_command("mv " + backupfile + " " + sourceFile)
        self.log_command_output(o, r)
        if self.info.type.lower() == "linux":
            if "centos 7" in self.info.distribution_version.lower():
                """from watson, systemd is used in centos 7 """
                log.info("this node is centos 7.x")
                o, r = self.execute_command("service couchbase-server restart")
                self.log_command_output(o, r)
            else:
                o, r = self.execute_command("/etc/init.d/couchbase-server restart")
                self.log_command_output(o, r)
        else:
            o, r = self.execute_command("net stop couchbaseserver")
            self.log_command_output(o, r)
            o, r = self.execute_command("net start couchbaseserver")
            self.log_command_output(o, r)
        shell.close()

    def set_node_name(self, name):
        """Edit couchbase-server shell script in place and set custom node name.
        This is necessary for cloud installations where nodes have both
        private and public addresses.

        It only works on Unix-like OS.

        Reference: http://bit.ly/couchbase-bestpractice-cloud-ip
        """

        # Stop server
        self.stop_couchbase()

        # Edit _start function
        cmd = r"sed -i 's/\(.*\-run ns_bootstrap.*\)/\1\n\t-name ns_1@{0} \\/' \
                /opt/couchbase/bin/couchbase-server".format(name)
        self.execute_command(cmd)

        # Cleanup
        for cmd in ('rm -fr /opt/couchbase/var/lib/couchbase/data/*',
                    'rm -fr /opt/couchbase/var/lib/couchbase/mnesia/*',
                    'rm -f /opt/couchbase/var/lib/couchbase/config/config.dat'):
            self.execute_command(cmd)

        # Start server
        self.start_couchbase()

    def execute_cluster_backup(self, login_info="Administrator:password", backup_location="/tmp/backup",
                               command_options='', cluster_ip="", cluster_port="8091", delete_backup=True):
        if self.nonroot:
            backup_command = "/home/%s%scbbackup" % (self.master.ssh_username,
                                                    LINUX_COUCHBASE_BIN_PATH)
        else:
            backup_command = "%scbbackup" % (LINUX_COUCHBASE_BIN_PATH)
        backup_file_location = backup_location
        # TODO: define WIN_COUCHBASE_BIN_PATH and implement a new function under RestConnectionHelper to use nodes/self info to get os info
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            backup_command = "\"%scbbackup.exe\"" % (WIN_COUCHBASE_BIN_PATH_RAW)
            backup_file_location = "C:%s" % (backup_location)
            output, error = self.execute_command("taskkill /F /T /IM cbbackup.exe")
            self.log_command_output(output, error)
        if self.info.distribution_type.lower() == 'mac':
            backup_command = "%scbbackup" % (MAC_COUCHBASE_BIN_PATH)

        command_options_string = ""
        if command_options is not '':
            if "-b" not in command_options:
                command_options_string = ' '.join(command_options)
            else:
                command_options_string = command_options
        cluster_ip = cluster_ip or self.ip
        cluster_port = cluster_port or self.port

        if '-m accu' not in command_options_string and '-m diff' not in command_options_string and delete_backup:
            self.delete_files(backup_file_location)
            self.create_directory(backup_file_location)

        command = "%s %s%s@%s:%s %s %s" % (backup_command, "http://", login_info,
                                           cluster_ip, cluster_port, backup_file_location, command_options_string)
        if self.info.type.lower() == 'windows':
            command = "cmd /c START \"\" \"%s\" \"%s%s@%s:%s\" \"%s\" %s" % (backup_command, "http://", login_info,
                                               cluster_ip, cluster_port, backup_file_location, command_options_string)

        output, error = self.execute_command(command)
        self.log_command_output(output, error)

    def restore_backupFile(self, login_info, backup_location, buckets):
        restore_command = "%scbrestore" % (LINUX_COUCHBASE_BIN_PATH)
        backup_file_location = backup_location
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            restore_command = "\"%scbrestore.exe\"" % (WIN_COUCHBASE_BIN_PATH_RAW)
            backup_file_location = "C:%s" % (backup_location)
        if self.info.distribution_type.lower() == 'mac':
            restore_command = "%scbrestore" % (MAC_COUCHBASE_BIN_PATH)
        outputs = errors = []
        for bucket in buckets:
            command = "%s %s %s%s@%s:%s %s %s" % (restore_command, backup_file_location, "http://",
                                                  login_info, self.ip, self.port, "-b", bucket)
            if self.info.type.lower() == 'windows':
                command = "cmd /c \"%s\" \"%s\" \"%s%s@%s:%s\" %s %s" % (restore_command, backup_file_location, "http://",
                                                      login_info, self.ip, self.port, "-b", bucket)
            output, error = self.execute_command(command)
            self.log_command_output(output, error)
            outputs.extend(output)
            errors.extend(error)
        return outputs, errors

    def delete_files(self, file_location):
        command = "%s%s" % ("rm -rf ", file_location)
        output, error = self.execute_command(command)
        self.log_command_output(output, error)

    def get_data_map_using_cbtransfer(self, buckets, data_path=None, userId="Administrator",
                                      password="password", getReplica=False, mode="memory"):
        self.extract_remote_info()
        temp_path = "/tmp/"
        if self.info.type.lower() == 'windows':
            temp_path = WIN_TMP_PATH
        replicaOption = ""
        prefix = str(uuid.uuid1())
        fileName = prefix + ".csv"
        if getReplica:
             replicaOption = "  --source-vbucket-state=replica"

        source = "http://" + self.ip + ":8091"
        if mode == "disk":
            source = "couchstore-files://" + data_path
        elif mode == "backup":
            source = data_path
            fileName = ""
        # Initialize Output
        bucketMap = {}
        headerInfo = ""
        # Iterate per bucket and generate maps
        for bucket in buckets:
            if data_path == None:
                options = " -b " + bucket.name + " -u " + userId + " -p "+ password +" --single-node"
            else:
                options = " -b " + bucket.name + " -u " + userId + " -p" + password + replicaOption
            suffix = "_" + bucket.name + "_N%2FA.csv"
            if mode == "memory" or mode == "backup":
               suffix = "_" + bucket.name + "_" + self.ip + "%3A8091.csv"

            genFileName = prefix + suffix
            csv_path = temp_path + fileName
            if self.info.type.lower() == 'windows':
                csv_path = WIN_TMP_PATH_RAW + fileName
            path = temp_path + genFileName
            dest_path = "/tmp/" + fileName
            destination = "csv:" + csv_path
            log.info("Run cbtransfer to get data map")
            self.execute_cbtransfer(source, destination, options)
            file_existed = self.file_exists(temp_path, genFileName)
            if file_existed:
                self.copy_file_remote_to_local(path, dest_path)
                self.delete_files(path)
                content = []
                headerInfo = ""
                with open(dest_path) as f:
                    headerInfo = f.readline()
                    content = f.readlines()
                bucketMap[bucket.name] = content
                os.remove(dest_path)
        return headerInfo, bucketMap

    def execute_cbtransfer(self, source, destination, command_options=''):
        transfer_command = "%scbtransfer" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            transfer_command = '/home/%s%scbtransfer' % (self.username,
                                                         LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            transfer_command = "\"%scbtransfer.exe\"" % (WIN_COUCHBASE_BIN_PATH_RAW)
        if self.info.distribution_type.lower() == 'mac':
            transfer_command = "%scbtransfer" % (MAC_COUCHBASE_BIN_PATH)

        command = "%s %s %s %s" % (transfer_command, source, destination, command_options)
        if self.info.type.lower() == 'windows':
            command = "cmd /c \"%s\" \"%s\" \"%s\" %s" % (transfer_command,
                                                          source,
                                                          destination,
                                                          command_options)
        output, error = self.execute_command(command, use_channel=True)
        self.log_command_output(output, error)
        log.info("done execute cbtransfer")
        return output

    def execute_cbdocloader(self, username, password, bucket, memory_quota, file):
        f, s, b = self.get_cbversion(self.info.type.lower())
        cbdocloader_command = "%scbdocloader" % (LINUX_COUCHBASE_BIN_PATH)
        cluster_flag = "-n"
        bucket_quota_flag = "-s"
        data_set_location_flag = " "
        if f[:5] in COUCHBASE_FROM_SPOCK:
            cluster_flag = "-c"
            bucket_quota_flag = "-m"
            data_set_location_flag = "-d"
        linux_couchbase_path = LINUX_CB_PATH
        if self.nonroot:
            cbdocloader_command = "/home/%s%scbdocloader" % (self.username,
                                                             LINUX_COUCHBASE_BIN_PATH)
            linux_couchbase_path = "/home/%s%s" % (self.username, LINUX_CB_PATH)
        self.extract_remote_info()
        command = "%s -u %s -p %s %s %s:%s -b %s %s %s %s %ssamples/%s.zip"\
                                                                     % (cbdocloader_command,
                                                                        username, password,
                                                                        cluster_flag,
                                                                        self.ip,
                                                                        self.port, bucket,
                                                                        bucket_quota_flag,
                                                                        memory_quota,
                                                                        data_set_location_flag,
                                                                        linux_couchbase_path,
                                                                        file)
        if self.info.distribution_type.lower() == 'mac':
            cbdocloader_command = "%scbdocloader" % (MAC_COUCHBASE_BIN_PATH)
            command = "%s -u %s -p %s %s %s:%s -b %s %s %s %s %ssamples/%s.zip"\
                                                                     % (cbdocloader_command,
                                                                        username, password,
                                                                        cluster_flag,
                                                                        self.ip,
                                                                        self.port, bucket,
                                                                        bucket_quota_flag,
                                                                        memory_quota,
                                                                        data_set_location_flag,
                                                                        MAC_CB_PATH, file)

        if self.info.type.lower() == 'windows':
            cbdocloader_command = "%scbdocloader.exe" % (WIN_COUCHBASE_BIN_PATH)
            WIN_COUCHBASE_SAMPLES_PATH = "C:/Program\ Files/Couchbase/Server/samples/"
            command = "%s -u %s -p %s %s %s:%s -b %s %s %s %s %s%s.zip" % (cbdocloader_command,
                                                                        username, password,
                                                                        cluster_flag,
                                                                        self.ip,
                                                                        self.port, bucket,
                                                                        bucket_quota_flag,
                                                                        memory_quota,
                                                                        data_set_location_flag,
                                                                        WIN_COUCHBASE_SAMPLES_PATH,
                                                                        file)
        output, error = self.execute_command(command)
        self.log_command_output(output, error)
        return output, error

    def execute_cbcollect_info(self, file, options=""):
        cbcollect_command = "%scbcollect_info" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cbcollect_command = "/home/%s%scbcollect_info" % (self.username,
                                                              LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            cbcollect_command = "%scbcollect_info.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cbcollect_command = "%scbcollect_info" % (MAC_COUCHBASE_BIN_PATH)

        command = "%s %s %s" % (cbcollect_command, file, options)
        output, error = self.execute_command(command, use_channel=True)
        return output, error


    # works for linux only
    def execute_couch_dbinfo(self, file):
        couch_dbinfo_command = "%scouch_dbinfo" % (LINUX_COUCHBASE_BIN_PATH)
        cb_data_path = "/"
        if self.nonroot:
            couch_dbinfo_command = "/home/%s%scouch_dbinfo" % (self.username,
                                                               LINUX_COUCHBASE_BIN_PATH)
            cb_data_path = "/home/%s/" % self.username
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            couch_dbinfo_command = "%scouch_dbinfo.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            couch_dbinfo_command = "%scouch_dbinfo" % (MAC_COUCHBASE_BIN_PATH)

        command =  couch_dbinfo_command +' -i %sopt/couchbase/var/lib/couchbase/data/*/*[0-9] >' \
                                              % cb_data_path + file
        output, error = self.execute_command(command, use_channel=True)
        self.log_command_output(output, error)
        return output, error

    def execute_cbepctl(self, bucket, persistence, param_type, param, value,
                                                  cbadmin_user="cbadminbucket",
                                                  cbadmin_password="password"):
        cbepctl_command = "%scbepctl" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cbepctl_command = "/home/%s%scbepctl" % (self.username,
                                                     LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            cbepctl_command = "%scbepctl.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cbepctl_command = "%scbepctl" % (MAC_COUCHBASE_BIN_PATH)

        if bucket.saslPassword == None:
            bucket.saslPassword = ''
        if persistence != "":
            command = "%s %s:11210  -u %s -p %s -b %s %s"\
                                            % (cbepctl_command, self.ip,
                                               cbadmin_user, cbadmin_password,
                                               bucket.name, persistence)
        else:
            command = "%s %s:11210 -u %s -p %s -b %s %s %s %s"\
                                            % (cbepctl_command, self.ip,
                                               cbadmin_user, cbadmin_password,
                                               bucket.name, param_type,
                                               param, value)
        output, error = self.execute_command(command)
        self.log_command_output(output, error)
        return output, error

    def execute_cbvdiff(self, bucket, node_str, password=None):
        cbvdiff_command = "%scbvdiff" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cbvdiff_command = "/home/%s%scbvdiff" % (self.username,
                                                     LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            cbvdiff_command = "%scbvdiff.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cbvdiff_command = "%scbvdiff" % (MAC_COUCHBASE_BIN_PATH)

        if not password:
            command = "%s -b %s %s " % (cbvdiff_command, bucket.name, node_str)
        else:
            command = "%s -b %s -p %s %s " % (cbvdiff_command, bucket.name, password, node_str)
        output, error = self.execute_command(command)
        self.log_command_output(output, error)
        return output, error

    def execute_cbstats(self, bucket, command, keyname="", vbid=0,
                                      cbadmin_user="cbadminbucket",
                                      cbadmin_password="password"):
        cbstat_command = "%scbstats" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cbstat_command = "/home/%s%scbstats" % (self.username,
                                                    LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            cbstat_command = "%scbstats.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cbstat_command = "%scbstats" % (MAC_COUCHBASE_BIN_PATH)

        if command != "key" and command != "raw":
            command = "%s %s:11210 %s -u %s -p %s -b %s " % (cbstat_command,
                                                             self.ip, command,
                                                             cbadmin_user,
                                                             cbadmin_password,
                                                             bucket.name)
        else:
            command = "%s %s:11210 %s -u %s -p %s %s %s " % (cbstat_command,
                                                             self.ip, command,
                                                             cbadmin_user,
                                                             cbadmin_password,
                                                             keyname, vbid)


        output, error = self.execute_command(command)
        self.log_command_output(output, error)
        return output, error

    def couchbase_cli(self, subcommand, cluster_host, options):
        cb_client = "%scouchbase-cli" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cb_client = "/home/%s%scouchbase-cli" % (self.username,
                                                     LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            cb_client = "%scouchbase-cli.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cb_client = "%scouchbase-cli" % (MAC_COUCHBASE_BIN_PATH)

        # now we can run command in format where all parameters are optional
        # {PATH}/couchbase-cli [SUBCOMMAND] [OPTIONS]
        cluster_param = " -c {0}".format(cluster_host)
        command = cb_client + " " + subcommand + " " + cluster_param + " " + options

        output, error = self.execute_command(command, use_channel=True)
        self.log_command_output(output, error)
        return output, error

    def execute_couchbase_cli(self, cli_command, cluster_host='localhost', options='',
                              cluster_port=None, user='Administrator', password='password'):
        cb_client = "%scouchbase-cli" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cb_client = "/home/%s%scouchbase-cli" % (self.username,
                                                     LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        f, s, b = self.get_cbversion("unix")
        if self.info.type.lower() == 'windows':
            f, s, b = self.get_cbversion("windows")
            cb_client = "%scouchbase-cli.exe" % (WIN_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cb_client = "%scouchbase-cli" % (MAC_COUCHBASE_BIN_PATH)

        cluster_param = (" -c http://{0}".format(cluster_host),
                         "")[cluster_host is None]
        if cluster_param is not None:
            cluster_param += (":{0}".format(cluster_port), "")[cluster_port is None]

        user_param = (" -u {0}".format(user), "")[user is None]
        passwd_param = (" -p {0}".format(password), "")[password is None]
        if f[:5] in COUCHBASE_FROM_SPOCK and cli_command == "cluster-init":
            user_param = (" --cluster-username {0}".format(user), "")[user is None]
            passwd_param = (" --cluster-password {0}".format(password), "")[password is None]
        # now we can run command in format where all parameters are optional
        # {PATH}/couchbase-cli [COMMAND] [CLUSTER:[PORT]] [USER] [PASWORD] [OPTIONS]
        command = cb_client + " " + cli_command + cluster_param + user_param + passwd_param + " " + options
        log.info("command to run: {0}".format(command))
        output, error = self.execute_command(command, debug=False, use_channel=True)
        return output, error

    def get_cluster_certificate_info(self, bin_path, cert_path,
                                     user, password, server_cert):
        """
            Get certificate info from cluster and store it in tmp dir
        """
        cert_file_location = cert_path + "cert.pem"
        cmd = "%s/couchbase-cli ssl-manage -c %s:8091 -u %s -p %s "\
              " --cluster-cert-info > %s" % (bin_path, server_cert.ip,
                                             user, password,
                                             cert_file_location)
        output, _ = self.execute_command(cmd)
        if output and "Error" in output[0]:
            raise("Failed to get CA certificate from cluster.")
        return cert_file_location

    def execute_cbworkloadgen(self, username, password, num_items, ratio, bucket,
                                                     item_size, command_options):
        cbworkloadgen_command = "%scbworkloadgen" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            cbworkloadgen_command = "/home/%s%scbworkloadgen" % (self.username,
                                                        LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()

        if self.info.distribution_type.lower() == 'mac':
            cbworkloadgen_command = "%scbworkloadgen" % (MAC_COUCHBASE_BIN_PATH)

        if self.info.type.lower() == 'windows':
            cbworkloadgen_command = "%scbworkloadgen.exe" % (WIN_COUCHBASE_BIN_PATH)

        command = "%s -n %s:%s -r %s -i %s -b %s -s %s %s -u %s -p %s" % (cbworkloadgen_command,
                                                                          self.ip, self.port,
                                                                          ratio, num_items, bucket,
                                                                          item_size, command_options,
                                                                          username, password)

        output, error = self.execute_command(command)
        self.log_command_output(output, error)
        return output, error

    def execute_cbhealthchecker(self, username, password, command_options=None, path_to_store=''):
        command_options_string = ""
        if command_options is not None:
            command_options_string = ' '.join(command_options)

        cbhealthchecker_command = "%scbhealthchecker" % (LINUX_COUCHBASE_BIN_PATH)
        if self.info.distribution_type.lower() == 'mac':
            cbhealthchecker_command = "%scbhealthchecker" % (MAC_COUCHBASE_BIN_PATH)

        if self.info.type.lower() == 'windows':
            cbhealthchecker_command = "%scbhealthchecker" % (WIN_COUCHBASE_BIN_PATH)

        if path_to_store:
            self.execute_command('rm -rf %s; mkdir %s;cd %s' % (path_to_store,
                                                path_to_store, path_to_store))

        command = "%s -u %s -p %s -c %s:%s %s" % (cbhealthchecker_command,
                                                username, password, self.ip,
                                                self.port, command_options_string)

        if path_to_store:
            command = "cd %s; %s -u %s -p %s -c %s:%s %s" % (path_to_store,
                                                cbhealthchecker_command,
                                                username, password, self.ip,
                                                self.port, command_options_string)

        output, error = self.execute_command_raw(command)
        self.log_command_output(output, error)
        return output, error

    def execute_vbuckettool(self, keys, prefix=None):
        command = "%stools/vbuckettool" % (LINUX_COUCHBASE_BIN_PATH)
        if self.nonroot:
            command = "/home/%s%stools/vbuckettool" % (self.username,
                                                       LINUX_COUCHBASE_BIN_PATH)
        self.extract_remote_info()
        if self.info.type.lower() == 'windows':
            command = "%stools/vbuckettool.exe" % (WIN_COUCHBASE_BIN_PATH)
        if prefix:
            command = prefix + command
        command = "%s - %s" % (command, ' '.join(keys))
        output, error = self.execute_command(command, use_channel=True)
        self.log_command_output(output, error)
        return output, error

    def execute_batch_command(self, command):
        remote_command = \
            "echo \"{0}\" > /tmp/cmd.bat; chmod u=rwx /tmp/cmd.bat; /tmp/cmd.bat"\
                                                                  .format(command)
        o, r = self.execute_command_raw(remote_command)
        if r and r!=['']:
            log.error("Command didn't run successfully. Error: {0}".format(r))
        return o, r

    def remove_win_backup_dir(self):
        win_paths = [WIN_CB_PATH, testconstants.WIN_MB_PATH]
        for each_path in win_paths:
            backup_files = []
            files = self.list_files(each_path)
            for f in files:
                if f["file"].startswith("backup-"):
                    backup_files.append(f["file"])
             # keep the last one
            if len(backup_files) > 2:
                log.info("start remove previous backup directory")
                for f in backup_files[:-1]:
                    self.execute_command("rm -rf '{0}{1}'".format(each_path, f))

    def remove_win_collect_tmp(self):
        win_tmp_path = testconstants.WIN_TMP_PATH
        log.info("start remove tmp files from directory %s" % win_tmp_path)
        self.execute_command("rm -rf '%stmp*'" % win_tmp_path)

    # ps_name_or_id means process name or ID will be suspended
    def windows_process_utils(self, ps_name_or_id, cmd_file_name, option=""):
        success = False
        files_path = "cygdrive/c/utils/suspend/"
        # check to see if suspend files exist in server
        file_existed = self.file_exists(files_path, cmd_file_name)
        if file_existed:
            command = "{0}{1} {2} {3}".format(files_path, cmd_file_name, option, ps_name_or_id)
            o, r = self.execute_command(command)
            if not r:
                success = True
                self.log_command_output(o, r)
                self.sleep(30, "Wait for windows to execute completely")
            else:
                log.error("Command didn't run successfully. Error: {0}".format(r))
        else:
            o, r = self.execute_command("netsh advfirewall firewall add rule name=\"block erl.exe in\" dir=in action=block program=\"%ProgramFiles%\Couchbase\Server\\bin\erl.exe\"")
            if not r:
                success = True
                self.log_command_output(o, r)
            o, r = self.execute_command("netsh advfirewall firewall add rule name=\"block erl.exe out\" dir=out action=block program=\"%ProgramFiles%\Couchbase\Server\\bin\erl.exe\"")
            if not r:
                success = True
                self.log_command_output(o, r)
        return success

    def check_cmd(self, cmd):
        """
           Some tests need some commands to run.
           Copy or install command in server if they are not available
        """
        log.info("\n---> Run command %s to check if it is ready on server %s"
                                                            % (cmd, self.ip))
        out, err = self.execute_command(cmd)
        found_command = False
        download_command = ""
        command_output = ""
        if self.info.type.lower() == 'windows':
            wget = "/cygdrive/c/automation/wget.exe"
            if cmd == "unzip":
                download_command = WIN_UNZIP
                command_output = "UnZip 5.52 of 28 February 2005, by Info-ZIP"
                if out and command_output in out[0]:
                    log.info("unzip command is ready")
                    found_command = True
            if cmd == "pssuspend":
                download_command = WIN_PSSUSPEND
                command_output = "PsSuspend suspends or resumes processes"
                if out and command_output in out[0]:
                    log.info("PsSuspend command is ready to run")
                    found_command = True
            if not found_command and err and "command not found" in err[0]:
                self.execute_command("cd /bin; %s --no-check-certificate -q %s "
                                                        % (wget, download_command))
                out, err = self.execute_command(cmd)
                if out and command_output in out[0]:
                    log.info("%s command is ready" % cmd)
                else:
                    mesg = "Failed to download %s " % cmd
                    self.stop_current_python_running(mesg)
        elif self.info.distribution_type.lower() == 'centos':
            if cmd == "unzip":
                command_output = "UnZip 6.00 of 20 April 2009"
                if out and command_output in out[0]:
                    log.info("unzip command is ready")
                    found_command = True
            if not found_command and err and "command not found" in err[0]:
                self.execute_command("yum install -y unzip")
                out, err = self.execute_command(cmd)
                if out and command_output in out[0]:
                    log.info("%s command is ready" % cmd)
                else:
                    mesg = "Failed to install %s " % cmd
                    self.stop_current_python_running(mesg)

    def check_openssl_version(self, deliverable_type, openssl, version):

        if "SUSE" in self.info.distribution_type:
            o, r = self.execute_command("zypper -n if openssl 2>/dev/null| grep -i \"Installed: Yes\"")
            self.log_command_output(o, r)

            if o == "":
                o, r = self.execute_command("zypper -n in openssl")
                self.log_command_output(o, r)
                o, r = self.execute_command("zypper -n if openssl 2>/dev/null| grep -i \"Installed: Yes\"")
                self.log_command_output(o, r)
                if o == "":
                    log.error("Could not install openssl in opensuse/SUSE")
        sherlock = ["3.5", "4.0"]
        if version[:3] not in sherlock and "SUSE" not in self.info.distribution_type:
            if self.info.deliverable_type == "deb":
                ubuntu_version = ["12.04", "13.04"]
                o, r = self.execute_command("lsb_release -r")
                self.log_command_output(o, r)
                if o[0] != "":
                    o = o[0].split(":")
                    if o[1].strip() in ubuntu_version and "1" in openssl:
                        o, r = self.execute_command("dpkg --get-selections | grep libssl")
                        self.log_command_output(o, r)
                        for s in o:
                            if "libssl0.9.8" in s:
                                o, r = self.execute_command("apt-get --purge remove -y {0}".format(s[:11]))
                                self.log_command_output(o, r)
                                o, r = self.execute_command("dpkg --get-selections | grep libssl")
                                log.info("package {0} should not appear below".format(s[:11]))
                                self.log_command_output(o, r)
                    elif openssl == "":
                        o, r = self.execute_command("dpkg --get-selections | grep libssl")
                        self.log_command_output(o, r)
                        if not o:
                            pass   # CBQE-36124 - some SSL stuff which is not needed anymore anyway
                            #o, r = self.execute_command("apt-get install -y libssl0.9.8")
                            #self.log_command_output(o, r)
                            #o, r = self.execute_command("dpkg --get-selections | grep libssl")
                            #log.info("package {0} should not appear below".format(o[:11]))
                            #self.log_command_output(o, r)
                        elif o:
                            for s in o:
                                if "libssl0.9.8" not in s:
                                    o, r = self.execute_command("apt-get install -y libssl0.9.8")
                                    self.log_command_output(o, r)
                                    o, r = self.execute_command("dpkg --get-selections | grep libssl")
                                    log.info("package {0} should not appear below".format(s[:11]))
                                    self.log_command_output(o, r)
           
    def check_pkgconfig(self, deliverable_type, openssl):
        if "SUSE" in self.info.distribution_type:
            o, r = self.execute_command("zypper -n if pkg-config 2>/dev/null| grep -i \"Installed: Yes\"")
            self.log_command_output(o, r)
            if o == "":
                o, r = self.execute_command("zypper -n in pkg-config")
                self.log_command_output(o, r)
                o, r = self.execute_command("zypper -n if pkg-config 2>/dev/null| grep -i \"Installed: Yes\"")
                self.log_command_output(o, r)
                if o == "":
                    log.error("Could not install pkg-config in suse")


        else:
            if self.info.deliverable_type == "rpm":
                centos_version = ["6.4", "6.5"]
                o, r = self.execute_command("cat /etc/redhat-release")
                self.log_command_output(o, r)

                if o is None:
                    # This must be opensuse, hack for now....
                    o, r = self.execute_command("cat /etc/SuSE-release")
                    self.log_command_output(o, r)
                if o[0] != "":
                    o = o[0].split(" ")
                    if o[2] in centos_version:
                        o, r = self.execute_command("rpm -qa | grep pkgconfig")
                        self.log_command_output(o, r)
                        if not o:
                            o, r = self.execute_command("yum install -y pkgconfig")
                            self.log_command_output(o, r)
                            o, r = self.execute_command("rpm -qa | grep pkgconfig")
                            log.info("package pkgconfig should appear below")
                            self.log_command_output(o, r)
                        elif o:
                            for s in o:
                                if "pkgconfig" not in s:
                                    o, r = self.execute_command("yum install -y pkgconfig")
                                    self.log_command_output(o, r)
                                    o, r = self.execute_command("rpm -qa | grep pkgconfig")
                                    log.info("package pkgconfig should appear below")
                                    self.log_command_output(o, r)
                    else:
                        log.info("no need to install pkgconfig")

    def check_man_page(self):
        log.info("check if man installed on vm?")
        info_cm = self.extract_remote_info()
        man_installed = False
        if info_cm.type.lower() == "linux" and not self.nonroot:
            if "centos 7" in info_cm.distribution_version.lower():
                out_cm, err_cm = self.execute_command("rpm -qa | grep 'man-db'")
                if out_cm:
                    man_installed = True
            elif "centos 6" in info_cm.distribution_version.lower():
                out_cm, err_cm = self.execute_command("rpm -qa | grep 'man-1.6'")
                if out_cm:
                    man_installed = True
            if not man_installed:
                log.info("man page does not install man page on vm %s"
                              " Let do install it now" % self.ip)
                self.execute_command("yum install -y man")

    def install_missing_lib(self):
        if self.info.deliverable_type == "deb":
            for lib_name in MISSING_UBUNTU_LIB:
                if lib_name != "":
                    log.info("prepare install library {0}".format(lib_name))
                    o, r = self.execute_command("apt-get install -y {0}".format(lib_name))
                    self.log_command_output(o, r)
                    o, r = self.execute_command("dpkg --get-selections | grep {0}"
                                                                 .format(lib_name))
                    self.log_command_output(o, r)
                    log.info("lib {0} should appear around this line"
                                                                 .format(lib_name))

    def is_enterprise(self, os_name):
        enterprise = False
        if os_name != 'windows':
            if self.file_exists("/opt/couchbase/etc/", "runtime.ini"):
                output = self.read_remote_file("/opt/couchbase/etc/","runtime.ini")
                for x in output:
                    x = x.strip()
                    if x and "license = enterprise" in x:
                        enterprise = True
            else:
                log.info("couchbase server at {0} may not installed yet"
                          .format(self.ip))
        else:
            log.info("only check cb edition in unix enviroment")
        return enterprise

    def get_cbversion(self, os_name):
        """ fv = a.b.c-xxxx, sv = a.b.c, bn = xxxx """
        fv = sv = bn = tmp = ""
        nonroot_path_start = ""
        output = ""
        if not self.nonroot:
            nonroot_path_start = "/"
        if os_name != 'windows':
            if self.nonroot:
                if self.file_exists('/home/%s%s' % (self.username, LINUX_CB_PATH), VERSION_FILE):
                    output = self.read_remote_file('/home/%s%s' % (self.username, LINUX_CB_PATH),
                                                                   VERSION_FILE)
                else:
                    log.info("couchbase server at {0} may not installed yet"
                                                           .format(self.ip))
            else:
                if self.file_exists(LINUX_CB_PATH, VERSION_FILE):
                    output = self.read_remote_file(LINUX_CB_PATH, VERSION_FILE)
                else:
                    log.info("couchbase server at {0} may not installed yet"
                                                           .format(self.ip))
        elif os_name == "windows":
            if self.file_exists(WIN_CB_PATH, VERSION_FILE):
                output = self.read_remote_file(WIN_CB_PATH, VERSION_FILE)
            else:
                log.info("couchbase server at {0} may not installed yet"
                                                        .format(self.ip))
        if output:
            for x in output:
                x = x.strip()
                if x and x[:5] in COUCHBASE_VERSIONS and "-" in x:
                    fv = x
                    tmp = x.split("-")
                    sv = tmp[0]
                    bn = tmp[1]
                break
        return fv, sv, bn

    def set_cbauth_env(self, server):
        """ from Watson, we need to set cbauth environment variables
            so cbq could connect to the host """
        ready = RestHelper(RestConnection(server)).is_ns_server_running(300)
        if ready:
            version = RestConnection(server).get_nodes_version()
        else:
            sys.exit("*****  Node %s failed to up in 5 mins **** " % server.ip)

        if version[:5] in COUCHBASE_FROM_WATSON:
            try:
                if self.input.membase_settings.rest_username:
                    rest_username = self.input.membase_settings.rest_username
                else:
                    log.info("*** You need to set rest username at ini file ***")
                    rest_username = "Administrator"
                if self.input.membase_settings.rest_password:
                    rest_password = self.input.membase_settings.rest_password
                else:
                    log.info("*** You need to set rest password at ini file ***")
                    rest_password = "password"
            except Exception, ex:
                if ex:
                    print ex
                pass
            self.extract_remote_info()
            if self.info.type.lower() != 'windows':
                log.info("***** set NS_SERVER_CBAUTH env in linux *****")
                #self.execute_command("/etc/init.d/couchbase-server stop")
                #self.sleep(15)
                self.execute_command('export NS_SERVER_CBAUTH_URL='
                                    '"http://{0}:8091/_cbauth"'.format(server.ip),
                                                                      debug=False)
                self.execute_command('export NS_SERVER_CBAUTH_USER="{0}"'\
                                                        .format(rest_username),
                                                                debug=False)
                self.execute_command('export NS_SERVER_CBAUTH_PWD="{0}"'\
                                                    .format(rest_password),
                                                               debug=False)
                self.execute_command('export NS_SERVER_CBAUTH_RPC_URL='
                                '"http://{0}:8091/cbauth-demo"'.format(server.ip),
                                                                      debug=False)
                self.execute_command('export CBAUTH_REVRPC_URL='
                                     '"http://{0}:{1}@{2}:8091/query"'\
                                  .format(rest_username, rest_password,server.ip),
                                                                      debug=False)

    def change_system_time(self, time_change_in_seconds):

        # note that time change may be positive or negative


        # need to support Windows too
        output, error = self.execute_command("date +%s")
        if len(error) > 0:
            return False
        curr_time = int(output[-1])
        new_time = curr_time + time_change_in_seconds

        output, error = self.execute_command("date --date @" + str(new_time))
        if len(error) > 0:
            return False

        output, error = self.execute_command("date --set='" + output[-1] + "'")
        if len(error) > 0:
            return False
        else:
            return True

    def stop_current_python_running(self, mesg):
        os.system("ps aux | grep python | grep %d " % os.getpid())
        print mesg
        self.sleep(5, "==== delay kill pid %d in 5 seconds to printout message ==="\
                                                                      % os.getpid())
        os.system('kill %d' % os.getpid())

class RemoteUtilHelper(object):

    @staticmethod
    def enable_firewall(server, bidirectional=False, xdcr=False):
        """ Check if user is root or non root in unix """
        shell = RemoteMachineShellConnection(server)
        shell.info = shell.extract_remote_info()
        if shell.info.type.lower() == "windows":
            o, r = shell.execute_command('netsh advfirewall set publicprofile state on')
            shell.log_command_output(o, r)
            o, r = shell.execute_command('netsh advfirewall set privateprofile state on')
            shell.log_command_output(o, r)
            log.info("enabled firewall on {0}".format(server))
            suspend_erlang = shell.windows_process_utils("pssuspend.exe", "erl.exe", option="")
            if suspend_erlang:
                log.info("erlang process is suspended")
            else:
                log.error("erlang process failed to suspend")
        else:
            copy_server = copy.deepcopy(server)
            command_1 = "/sbin/iptables -A INPUT -p tcp -i eth0 --dport 1000:65535 -j REJECT"
            command_2 = "/sbin/iptables -A OUTPUT -p tcp -o eth0 --sport 1000:65535 -j REJECT"
            command_3 = "/sbin/iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT"
            if shell.info.distribution_type.lower() in LINUX_DISTRIBUTION_NAME \
                             and server.ssh_username != "root":
                copy_server.ssh_username = "root"
                shell.disconnect()
                log.info("=== connect to server with user %s " % copy_server.ssh_username)
                shell = RemoteMachineShellConnection(copy_server)
                o, r = shell.execute_command("whoami")
                shell.log_command_output(o, r)
            # Reject incoming connections on port 1000->65535
            o, r = shell.execute_command(command_1)
            shell.log_command_output(o, r)
            # Reject outgoing connections on port 1000->65535
            if bidirectional:
                o, r = shell.execute_command(command_2)
                shell.log_command_output(o, r)
            if xdcr:
                o, r = shell.execute_command(command_3)
                shell.log_command_output(o, r)
            log.info("enabled firewall on {0}".format(server))
            o, r = shell.execute_command("/sbin/iptables --list")
            shell.log_command_output(o, r)
            shell.disconnect()

    @staticmethod
    def common_basic_setup(servers):
        for server in servers:
            shell = RemoteMachineShellConnection(server)
            shell.start_couchbase()
            shell.disable_firewall()
            shell.unpause_memcached()
            shell.unpause_beam()
            shell.disconnect()
        time.sleep(10)

    @staticmethod
    def use_hostname_for_server_settings(server):
        shell = RemoteMachineShellConnection(server)
        info = shell.extract_remote_info()
        version = RestConnection(server).get_nodes_self().version
        time.sleep(5)
        hostname = shell.get_full_hostname()
        if version.startswith("1.8.") or version.startswith("2.0"):
            shell.stop_couchbase()
            if info.type.lower() == "windows":
                cmd = "'C:/Program Files/Couchbase/Server/bin/service_unregister.bat'"
                shell.execute_command_raw(cmd)
                cmd = 'cat "C:\\Program Files\\Couchbase\\Server\\bin\\service_register.bat"'
                old_reg = shell.execute_command_raw(cmd)
                new_start = '\n'.join(old_reg[0]).replace("ns_1@%IP_ADDR%", "ns_1@%s" % hostname)
                cmd = "echo '%s' > 'C:\\Program Files\\Couchbase\\Server\\bin\\service_register.bat'" % new_start
                shell.execute_command_raw(cmd)
                cmd = "'C:/Program Files/Couchbase/Server/bin/service_register.bat'"
                shell.execute_command_raw(cmd)
                cmd = 'rm -rf  "C:/Program Files/Couchbase/Server/var/lib/couchbase/mnesia/*"'
                shell.execute_command_raw(cmd)
            else:
                cmd = "cat  /opt/couchbase/bin/couchbase-server"
                old_start = shell.execute_command_raw(cmd)
                cmd = r"sed -i 's/\(.*\-run ns_bootstrap.*\)/\1\n\t-name ns_1@{0} \\/' \
                        /opt/couchbase/bin/couchbase-server".format(hostname)
                o, r = shell.execute_command(cmd)
                shell.log_command_output(o, r)
                time.sleep(2)
                cmd = 'rm -rf /opt/couchbase/var/lib/couchbase/data/*'
                shell.execute_command(cmd)
                cmd = 'rm -rf /opt/couchbase/var/lib/couchbase/mnesia/*'
                shell.execute_command(cmd)
                cmd = 'rm -rf /opt/couchbase/var/lib/couchbase/config/config.dat'
                shell.execute_command(cmd)
                cmd = 'echo "%s" > /opt/couchbase/var/lib/couchbase/ip' % hostname
                shell.execute_command(cmd)
        else:
            RestConnection(server).rename_node(hostname)
            shell.stop_couchbase()
        shell.start_couchbase()
        shell.disconnect()
        return hostname

    @staticmethod
    def is_text_present_in_logs(server, text_for_search, logs_to_check='debug'):
        shell = RemoteMachineShellConnection(server)
        info = shell.extract_remote_info()
        if info.type.lower() != "windows":
            path_to_log = testconstants.LINUX_COUCHBASE_LOGS_PATH
        else:
            path_to_log = testconstants.WIN_COUCHBASE_LOGS_PATH
        log_files = []
        files = shell.list_files(path_to_log)
        for f in files:
            if f["file"].startswith(logs_to_check):
                log_files.append(f["file"])
        is_txt_found = False
        for f in log_files:
            o, r = shell.execute_command("cat {0}/{1} | grep '{2}'".format(path_to_log,
                                                                          f,
                                                                          text_for_search))
            if ' '.join(o).find(text_for_search) != -1:
                is_txt_found = True
                break
        return is_txt_found
