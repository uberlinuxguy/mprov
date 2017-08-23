import re
import socket
import threading
import uuid
from tempfile import mkstemp
from time import sleep, time

import subprocess

import os

import signal

from mprov import utils


class Client(object):
    __retries = 0

    def signal_handler(self, signum, frame):
        self.__exiting = True
        if self.__hb_timer is not None:
            self.__hb_timer.cancel()

    def __init__(self, config):
        """
        The constructor.
        :param config: an mprov Config object
        :type config: mprov.Config.Config
        """
        self.__exiting = False
        self.__my_uuid = str(uuid.uuid4())
        self.__config = config
        self.__path = config.get_conf_val("path")
        self.__retries = 3  # hard coded for now.
        self.__hb_timer = None  # type: threading.Timer
        self.__req_uuid = ""
        self.__syncing = False
        self.__req_image = config.get_conf_val("image")
        self.__master_connection = None  # type: socket.socket

        # convert the "heartbeat" parameter to seconds
        tmp_timer_str = str(self.__config.get_conf_val("heartbeat"))
        self.__hb_timer_interval = int(re.sub("\D", "", str(tmp_timer_str)))
        if tmp_timer_str[-1:] == "m":
            self.__hb_timer_interval = self.__hb_timer_interval * 60

    def run(self):
        """
        run the mprov client.
        :return:
        """

        self.__master_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # master should reply in 30 seconds or so...
        self.__master_connection.settimeout(30)
        master_address = (self.__config.get_conf_val("ms"), 4017)

        try:
            self.__master_connection.connect(master_address)
        except Exception as e:
            utils.print_err("Error: Master Connection failed. ")
            utils.print_err("Error: " + e.message)
            return

        self._register_with_master()

    def _register_with_master(self):

        data = ""
        if self.__master_connection is None:
            self.__master_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # master should reply in 30 seconds or so...
            self.__master_connection.settimeout(30)
            master_address = (self.__config.get_conf_val("ms"), 4017)

            try:
                self.__master_connection.connect(master_address)
            except Exception as e:
                utils.print_err("Error: Master Connection failed. ")
                utils.print_err("Error: " + e.message)
                return

        try:
            cmd_send = "client " + self.__my_uuid + " " + self.__config.get_conf_val("image")
            if self.__req_uuid != "":
                cmd_send = cmd_send + " uuid=" + self.__req_uuid
            cmd_send += "\n"

            self.__master_connection.send(cmd_send)

            data = self.__master_connection.recv(1024)  # type: str
            if data[:2] != "ok":
                utils.print_err("Error: Master Server responded poorly to our register request. Will retry.")
                self.__master_connection.close()
                self.__master_connection = None
            else:
                # print "Master replied ok to us."
                (ok_str, self.__req_uuid) = data.split(None, 1)
                (uuid_label, self.__req_uuid) = self.__req_uuid.split('=', 1)

        except Exception as e:
            self.__master_connection.close()
            self.__master_connection = None
            utils.print_err("Error: Problem communicating with master server. Will Retry")

        if self.__master_connection is not None:
            # thread off to connect to the worker and start the sync
            # set out syncing flag, so we know below that we shouldn't try to spawn another thread.
            if not self.__syncing:
                self.__syncing = True

                # get the ip from the data.
                if data is not "":
                    try:
                        (resp, my_uuid, worker_ip) = data.split(None, 2)
                    except Exception as e:
                        # print e
                        return

                    # now let's start the thread to talk to the worker.
                    threading.Thread(target=self._handle_sync, args=(worker_ip,)).start()
                else:
                    self.__syncing = False
                    self.__master_connection.close()
                    self.__master_connection = None

        # set this function up as a re-occuring timer based on the -b/--heartbeat option.
        self.__hb_timer = threading.Timer(self.__hb_timer_interval, self._register_with_master)
        self.__hb_timer.start()

    def _worker_control(self, worker_address):
        # Establish a control connection in a separate thread
        # connect to the worker and wait for it to reply that it's ready.
        # create a new socket for the worker connection.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # give the worker 1 minute to reply, he has to check with the master.
        sock.settimeout(60)

        sock.connect(worker_address)

        control_connection_good = False
        try:
            sock.sendall("control client " + self.__req_uuid)
            data = sock.recv(1024)  # type: str
            if data[:2] != "ok":
                utils.print_err("Error: Worker replied badly. Cannot continue.  Shutting down rsync ")
                sock.close()
                self.__syncing = False
                self._cancel_rsync()
            else:
                control_connection_good = True

        except Exception as e:
            sock.close()
            utils.print_err("Error: Exception in worker comms: " + e.message)
            self.__syncing = False
            self._cancel_rsync()

        if not control_connection_good:
            self._cancel_rsync()
            sock.close()
            return False

        while self.__syncing:
            # if we get here we have a good connection, so let's make sure it doesn't go anywhere
            try:
                sock.sendall("ok\n")
                data = sock.recv(15)
                if data[:2] != "ok":
                    self._cancel_rsync()
                    self.__syncing = False
                    sock.close()
                    return False
            except Exception as e:
                print e
                sock.close()
                self._cancel_rsync()
                self.__syncing = False
                return False
            utils.print_err("worker hb: " + str(time()))
            # small sleep to loosen the tight loop
            sleep(1)

        # we are no longer syncing, close everything down.
        sock.close()
        self._cancel_rsync()
        self.__syncing = False
        return False

    def _handle_sync(self, worker_ip):
        # connect to the worker and wait for it to reply that it's ready.
        # create a new socket for the worker connection.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        worker_address = (worker_ip, 4018)

        # give the worker 1 minute to reply, he has to check with the master.
        sock.settimeout(60)

        sock.connect(worker_address)

        try:
            sock.sendall("sync client " + self.__req_uuid)
            data = sock.recv(1024)  # type: str
            if data[:2] != "ok":
                utils.print_err("Error: Worker replied badly.  Will retry.")
                sock.close()
                self.__syncing = False
                self.__retries += 1
                return False
        except Exception as e:
            sock.close()
            utils.print_err("Error: Exception in worker comms: " + e.message)
            self.__syncing = False
            self.__retries += 1
            return False

        # worker is connected
        # Establish a control connection in a separate thread
        threading.Thread(target=self._worker_control, args=(worker_address,)).start()

        # generate the module_name
        module_name = str(uuid.uuid4())
        sync_port = "8970"  # TODO: make this dynamic, and random.

        # output the rsyncd config and a secret file
        rsyncd_fd, rsyncd_path = mkstemp()
        secrets_fd, secrets_path = mkstemp()

        rsyncd_file = open(rsyncd_path, "w")
        file_contents = "[" + module_name + "]\n" + \
                        "\tpath = " + self.__path + "\n" + \
                        "\tread only = no\n" + \
                        "\tauth users = root\n" + \
                        "\tsecrets file = " + secrets_path + "\n" + \
                        "\tuid = 0\n" + \
                        "\tgid = 0\n"

        rsyncd_file.write(file_contents)
        rsyncd_file.close()
        os.close(rsyncd_fd)

        # output the rsyncd secrets file.
        rsyncd_file = open(secrets_path, "w")
        rsyncd_file.write("root:" + self.__my_uuid)
        rsyncd_file.close()
        os.close(secrets_fd)

        # setup the rsyncd command.
        rsyncd_proc = subprocess.Popen(["/usr/bin/rsync",
                                        "--daemon",
                                        "--port=" + sync_port,
                                        "--no-detach",
                                        "-v",
                                        "-4",
                                        "--config=" + rsyncd_path], shell=False)
        self.__rsyncd_pid = rsyncd_proc.pid

        # tell the worker we are good to go.
        sock.send("ok " + self.__my_uuid + " " + sync_port + " " + module_name + "\n")

        # wait for the rsyncd to terminate.
        rsyncd_proc.communicate()
        sock.close()
        print "Worker Sync Complete."

        self.__syncing = False
        self.__exiting = True

    def _cancel_rsync(self):
        # cancel the rsync.
        if self.__rsyncd_pid > 0:
            # attempt to terminate the rsyncd process
            os.kill(self.__rsyncd_pid, signal.SIGTERM)
