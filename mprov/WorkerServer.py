import re
from tempfile import mkstemp
from time import sleep, time
import Config
import utils
import socket
import threading
import uuid
import os
import signal
import subprocess


class WorkerServer(object):

    __master_sync_active = False
    __worker_slots = 0
    __slots_in_use = 0
    __my_uuid = ""
    __sync_requests = list()
    __state="waiting"

    __config = None  # type: Config
    __hb_timer_interval = 0
    __path = ""
    __rsyncd_pid = 0
    __exiting = False
    __hb_timer = None  # type: threading.Timer
    __master_connection = None  # type: socket.socket
    __last_master_sync_ip = None
    __repo_sync_uuid = ""

    def signal_handler(self, signum, frame):
        self.__exiting = True
        self.__hb_timer.cancel()

    def __init__(self, config):
        """
        Constructor
        :param config: a mprov Config object.
        :type config: mprove.Config.Config
        """

        self.__path = config.get_conf_val("path")
        self.__config = config
        self.__status = "outdated"
        if not os.path.exists(self.__path):
            utils.print_err("Error: Path " + self.__path + " doesn't exist! Exiting.")
            exit(1)

        self.__my_uuid = str(uuid.uuid4())

        # set up our listen socket.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # type: socket
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", 4018))

        # convert the "heartbeat" parameter to seconds
        tmp_timer_str = self.__config.get_conf_val("heartbeat")
        self.__hb_timer_interval = int(re.sub("\D", "", tmp_timer_str))
        if tmp_timer_str[-1:] == "m":
            self.__hb_timer_interval = self.__hb_timer_interval * 60

    def run(self):
        """
        Run the WorkerServer
        :return: none
        """
        self.sock.listen(1024)
        self.__master_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        master_address = (self.__config.get_conf_val("ms"), 4017)

        #
        self.__master_connection.settimeout(60)
        utils.print_err("Connecting to " + self.__config.get_conf_val("ms"))
        try:
            self.__master_connection.connect(master_address)
        except Exception as e:
            utils.print_err("Error: Unable to connect to master. Will Retry.")

            self.__master_connection.close()
            self.__master_connection = None


        # register with the master server, starts a timer that runs every "heartbeat" interval
        self._register_with_master()
        while True:
            try:
                connection, address = self.sock.accept()
            except KeyboardInterrupt as kbd_int:
                self.signal_handler(signal.SIGINT, None)
                return

            connection.settimeout(600)
            threading.Thread(target=self._handle_connection, args=(connection, address)).start()

    def _handle_connection(self, connection, address):
        """
        Handle the incoming connection
        :param connection:  socket to communicate on.
        :type connection: socket.socket
        :type address: List[str]
        :param address: the address that is connected to us.
        :return: True if we close OK, False if we don't. I don't think this matters.
        """
        size = 1024
        while True:
            try:
                data = connection.recv(size)
                packet = utils.parse_packet(data)
                if packet is not None:

                    if "sync" in packet:
                        if "master" in packet:
                            self.__master_sync_active = True
                            self._handle_master_sync(connection, packet)
                            self.__master_sync_active = False
                        elif "client" in packet:
                            if self.__master_sync_active:
                                connection.send("retry\n")
                                connection.close()
                                return False
                            self._handle_client_sync(connection, address, packet["uuid"])
                        elif "worker" in packet:
                            if self.__master_sync_active:
                                connection.send("retry\n")
                                connection.close()
                                return False
                            self._handle_worker_sync(connection, address)
                    elif "stop" in packet:
                        if "master" in packet:
                            if "repo_uuid" in packet:
                                if packet["repo_uuid"] == self.__repo_sync_uuid:
                                    # we are getting a stop from where we expect.
                                    self._handle_stop_master_sync(connection, address)
                                    return True
                                else:
                                    utils.print_err("Error: stop command unknown repo_uuid: " + packet["repo_uuid"])
                            else:
                                utils.print_err("Error: stop command from source with no UUID.")
                            utils.print_err("Error: should be: " + self.__repo_sync_uuid)
                            utils.print_err("Error: this is probably bad.")
                            connection.close()
                            return False

                        elif "client"in packet:
                            # purge a stale client request, kill any rsyncs running, and open the slot.
                            if address[0] == self.__config.get_conf_val("ms"):
                                self._handle_stop_client_sync(connection, address, packet["uuid"])
                                connection.close()
                                return False
                    elif "control" in packet:
                        # a control connection request
                        if "client" in packet:
                            # this is a client connection request.  Run the handler.
                            self._handle_client_control(connection, packet["uuid"])
                    else:
                        utils.print_err("Unrecognized Packet: " + data)
                        connection.close()
                        return False
                # done parsing, close the connection
                connection.close()
                return True
            except Exception as e:
                utils.print_err("Error: Unhandled exception thrown: " + e.message)
                utils.print_err("Error: Packet generating error: " + data)

                connection.close()
                return False

    def _handle_master_sync(self, connection, packet):
        """
        handle a sync request from the master server.
        :param connection: the socket connection to communicate on
        :param packet: the incoming packet
        :type connection: socket.socket
        :type packet: dict
        :return:
        """

        # let the master server redirect us to another worker that has an updated
        # copy of the image repo

        print "Repository Sync"

        self.__state="syncing"

        sync_connection = None
        if "repo_uuid" in packet:
            self.__repo_sync_uuid = packet["repo_uuid"]

        # if the master server is sending us a worker to sync from, then connect to the worker.
        if "worker" in packet:
            worker_address=packet["worker"]

            #connect to the worker
            sync_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            worker_address = (worker_address, 4018)
            try:
                sync_connection.connect(worker_address)
                sync_connection.send("sync worker")
                packet_reply = utils.parse_packet(sync_connection.recv(1024))
                if "ok" not in packet_reply:
                    utils.print_err("Error in worker to worker sync. bad reply.")
                    sync_connection.close()
                    connection.close()
                    return
                if "repo_uuid" in packet_reply:
                    self.__repo_sync_uuid=packet_reply["repo_uuid"]

            except Exception as e:
                utils.print_err("Error in worker to worker sync.")
                sync_connection.close()
                connection.close()
                return
        else:
            sync_connection = connection
        self.__last_master_sync_ip = sync_connection.getpeername()[0]
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

        sync_connection.send("ok worker_uuid=" + self.__my_uuid + " port=" + sync_port + " module=" + module_name + "\n")

        rsyncd_proc.communicate()

        reply="ok"
        self.__state="updated"
        # check the return code here!
        if rsyncd_proc.returncode != 0 and rsyncd_proc.returncode != 24 \
                and rsyncd_proc.returncode != 20:
            utils.print_err("Error: rsync from " + sync_connection.getpeername()[0] + " died unexpectedly with RC=" +
                            str(rsyncd_proc.returncode) + "!!!")
            reply="err"
            self.__state="err"


        # if we used a different connection for the sync, then tell the master we are done.
        if sync_connection != connection:
            connection.send(reply)
            connection.close()
        # clean up the tmp files.
        os.remove(rsyncd_path)
        os.remove(secrets_path)
        sync_connection.close()
        print "Sync Complete."

    def _handle_stop_master_sync(self, connection, address):
        """
        should stop the rsync daemon
        :param connection: socket
        :param address: address of who is connected.
        :return:
        """
        if self.__rsyncd_pid > 0:
            # attempt to terminate the rsyncd process
            try:
                os.kill(self.__rsyncd_pid, signal.SIGTERM)
            except Exception as e :
                utils.print_err("Error: rsync died unexpectedly!")
        return

    def _register_with_master(self):
        """
        register ourself with the master server.
        :return:
        """
        # set this function up as a re-occuring timer based on the -b/--heartbeat option.
        # utils.print_err("HB: interval " + str(self.__hb_timer_interval) + " at " + str(time()))

        if self.__master_connection is None:
            print "Setting up new master connection."
            self.__master_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            master_address = (self.__config.get_conf_val("ms"), 4017)

            # master should reply in 30 seconds or so...
            self.__master_connection.settimeout(60)
            try:
                self.__master_connection.connect(master_address)
            except Exception as e:
                self.__master_connection.close()
                self.__master_connection = None
                utils.print_err("Error: Problem communicating with master server. Will Retry")
        if self.__master_connection is not None:
            try:
                my_hostname = socket.gethostname()
                self.__master_connection.send("worker name=" + my_hostname + " worker_uuid=" + self.__my_uuid +
                                              " slots=" + str(self.__config.get_conf_val("slots")) + " status=" +
                                              self.__state + "\n")


                packet = utils.parse_packet(self.__master_connection.recv(1024))
                if "ok" not in packet:
                    utils.print_err("Error: Master Server responded poorly to our register request. Will retry.")
                    self.__master_connection.close()
                    self.__master_connection = None
            except Exception as e:
                if self.__master_connection is not None:
                    self.__master_connection.close()
                self.__master_connection = None
                utils.print_err("Error: Problem communicating with master server. Will Retry")
        # set this function up as a re-occuring timer based on the -b/--heartbeat option.
        # utils.print_err("HB: interval " + str(self.__hb_timer_interval) + " at " + str(time()))
        self.__hb_timer = threading.Timer(self.__hb_timer_interval, self._register_with_master)
        self.__hb_timer.start()

    def _handle_stop_client_sync(self, connection, address, req_uuid):
        """
        handle command to shut-down the client sync.
        This should be coming from the master server

        :param connection:
        :param address:
        :param  req_uuid:
        :return:
        """
        # Set the sync to inactive on the request.  A separate thread will perform the clean up via
        # the control connection
        cli_req = self._find_request_by_uuid(req_uuid)
        if cli_req is not None:
            cli_req.set_sync_active(False)

        return

    def _cleanup_client_req(self, req_uuid):
        cli_req = self._find_request_by_uuid(req_uuid)
        if cli_req is not None:
            # remove the object reference from the tracked reqeusts.
            self.__sync_requests.remove(cli_req)

            # kill local rsync for the client.
            if cli_req.get_rsync_pid() != "":
                if cli_req.get_rsync_pid() > 0:
                    # attempt to terminate the rsyncd process
                    os.kill(cli_req.get_rsync_pid(), signal.SIGTERM)

            # release the slot the client was holding.
            self.__slots_in_use = self.__slots_in_use - 1

        return

    def _find_request_by_uuid(self, req_uuid):
        for request in self.__sync_requests:  # type: WorkerServerSync
            if request.get_uuid() == req_uuid:
                return request
        return None

    def _handle_client_sync(self, connection, address, req_uuid):
        """
        handle a new client request for a sync.

        :param connection:
        :param address:
        :param req_uuid:
        :return:
        """
        # first check with the master for the information about the client.
        # create a new socket for the master connection.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        master_address = (self.__config.get_conf_val("ms"), 4017)

        sock.settimeout(30)

        packet = None # type: dict
        try:
            sock.connect(master_address)
            sock.sendall("verify uuid=" + req_uuid)
            packet = utils.parse_packet(sock.recv(1024))
        except Exception as e:
            utils.print_err("Error: Network issue communicating to master.")
            sock.close()

        if packet is None:
            utils.print_err("Error: Master didn't verify sync request.")
            self._cleanup_client_req(req_uuid)
            return

        cli_uuid = packet["client_uuid"]
        req_mod = packet["image"]
        cli_ip = packet["ip"]
        req_uuid = packet["uuid"]

        # make an WorkerServerSync object and stick it in the list.
        cli_req = WorkerServerSync(req_uuid,
                                   cli_uuid,
                                   address[0],
                                   req_mod,
                                   True)

        self.__sync_requests.append(cli_req)

        # now we have a valid client object, so tell the client to proceed.
        connection.send("ok\n")

        # now we wait for the client to tell us it's ready.
        connection.settimeout(60)
        packet_client = None # type: dict
        try:
            packet_client = utils.parse_packet(connection.recv(1024))  # this will block until the client is ready.
        except Exception as e:
            utils.print_err("Error: Client didn't reply back. Stopping request.")
            utils.print_err("Error: Exception: " + e.message)
            connection.close()
            cli_req.set_sync_active(False)
            return

        if packet_client is None:
            utils.print_err("Error: Client didn't reply back. Stopping request.")
            connection.close()
            cli_req.set_sync_active(False)
            return

        if "ok" not in packet_client:
            utils.print_err("Error: Client wasn't ready.")
            connection.close()
            cli_req.set_sync_active(False)
            return

        # handle incoming client sync request.

        # check for available slots.
        if self.__slots_in_use > 0:
            if self.__slots_in_use >= self.__worker_slots:
                utils.print_err("Error: Worker Full, but client asked for sync.  Shouldn't happen.")
                # no slots available.  Disconnect
                cli_req.set_sync_active(False)
                connection.close()
                return

        # parse the "ok" packet
        rsync_uuid = packet_client["client_uuid"]
        sync_port = packet_client["port"]
        mod_name = packet_client["module"]

        if rsync_uuid != cli_req.get_client_uuid():
            utils.print_err("Error: request ID mismatch!")
            cli_req.set_sync_active(False)
            connection.sendall("err")
            connection.close()
            return

        # and set up the rsync.
        # if the client is ready, start the sync
        # create a temporary file with the password
        fd, file_path = mkstemp()
        pass_file = open(file_path, "w")
        pass_file.write(rsync_uuid)
        pass_file.close()
        os.close(fd)

        rsync_args = ["/usr/bin/rsync",
                      "-avx",
                      "--progress",
                      "--port=" + sync_port,
                      "--exclude=/tmp",
                      "--exclude=/proc",
                      "--exclude=/sys",
                      "--exclude=/run",
                      "--exclude=/dev",
                      "--password-file=" + file_path,
                      self.__path + "/" + cli_req.get_req_mod() + "/",
                      "root@" + address[0] + "::" + mod_name ]
        print " ".join(rsync_args)

        # logging of the rsync.
        if not os.path.isdir("/tmp/mprov/"):
            # mprov tmp dir doesn't exist.
            os.mkdir("/tmp/mprov", 700)

        # open an rsync log for logging.
        rsync_log = open("/tmp/mprov/client_sync_" + mod_name + ".log", "w+")

        # we are about to run but give the client a couple of seconds to make sure the
        # rsync is set up on their end
        sleep(5)

        rsync_proc = subprocess.Popen(rsync_args, stdout=rsync_log, stderr=rsync_log)

        # wait for the rsync to finish.
        rsync_proc.communicate()
        rsync_log.close()

        # examine return code and log.... something...
        return_code = rsync_proc.returncode
        if return_code != 0 and return_code != 24:
            utils.print_err("Error: Client rsync died prematurely! RC=" + str(return_code))
            connection.send("ok result=error")
        else:
            connection.send("ok result=pass")
            os.remove(file_path)

        # mark the sync as done.
        cli_req.set_sync_active(False)
        connection.close()
        return

    def _handle_client_control(self, connection, req_uuid):
        # let's see if we have a client request for this uuid
        cli_req = self._find_request_by_uuid(req_uuid)  # type: WorkerServerSync
        if cli_req is None:
            connection.sendall("err")
            return False

        # start the control link by sending "ok"
        connection.sendall("ok")

        # we have a valid request UUID, so let's check it's status
        while cli_req.is_sync_active():
            try:
                packet = utils.parse_packet(connection.recv(10))
                if "ok" not in packet:
                    # something went wrong on teh client side.
                    # Clean up the request and remove it.
                    connection.sendall("err")
                    self._cleanup_client_req(req_uuid)
                    return False

                connection.sendall("ok")

            except Exception as e:
                connection.sendall("err")
                self._cleanup_client_req(req_uuid)
                return False
            # a small sleep here.
            sleep(1)

        # if we exit the loop, it's time to turn things down.
        connection.sendall("close")
        # Clean up the request and remove it.
        self._cleanup_client_req(req_uuid)
        return False

    def _handle_worker_sync(self, connection, address):
        """
        handle a new worker request for a sync.

        :param connection:
        :param address:
        :return:
        """
        print "Worker to worker sync requested from: " + address[0]

        # tell the other end to proceed.
        connection.send("ok repo_uuid=" + self.__my_uuid)

        # now we wait for the worker to tell us it's ready.
        connection.settimeout(60)
        packet_worker = None  # type: dict
        try:
            packet_worker = utils.parse_packet(connection.recv(1024))  # this will block until the worker is ready.
        except Exception as e:
            utils.print_err("Error: Client didn't reply back. Stopping request.")
            utils.print_err("Error: Exception: " + e.message)
            connection.close()
            return

        if packet_worker is None:
            utils.print_err("Error: Client didn't reply back. Stopping request.")
            connection.close()
            return

        if "ok" not in packet_worker:
            utils.print_err("Error: Client wasn't ready.")
            connection.close()
            return

        # handle incoming worker sync request.

        # check for available slots.
        if self.__slots_in_use > 0:
            if self.__slots_in_use >= self.__worker_slots:
                utils.print_err("Error: Worker Full, but client asked for sync.  Shouldn't happen.")
                # no slots available.  Disconnect
                connection.close()
                return

        # parse the "ok" packet
        rsync_uuid = packet_worker["worker_uuid"]
        sync_port = packet_worker["port"]
        mod_name = packet_worker["module"]

        # and set up the rsync.
        # if the client is ready, start the sync
        # create a temporary file with the password
        fd, file_path = mkstemp()
        pass_file = open(file_path, "w")
        pass_file.write(rsync_uuid)
        pass_file.close()
        os.close(fd)

        rsync_args = ["/usr/bin/rsync",
                      "-avx",
                      "--delete",
                      "--progress",
                      "--exclude=/tmp",
                      "--exclude=/proc",
                      "--exclude=/sys",
                      "--exclude=/run",
                      "--exclude=/dev",
                      "--port=" + sync_port,
                      "--password-file=" + file_path,
                      self.__path + "/",
                      "root@" + address[0] + "::" + mod_name]
        print " ".join(rsync_args)

        # logging of the rsync.
        if not os.path.isdir("/tmp/mprov/"):
            # mprov tmp dir doesn't exist.
            os.mkdir("/tmp/mprov", 700)

        # open an rsync log for logging.
        rsync_log = open("/tmp/mprov/worker_sync_" + mod_name + ".log", "w+")

        # we are about to run but give the worker a couple of seconds to make sure the
        # rsync is set up on their end
        sleep(5)

        rsync_proc = subprocess.Popen(rsync_args, stdout=rsync_log, stderr=rsync_log)

        # wait for the rsync to finish.
        rsync_proc.communicate()
        rsync_log.close()

        # examine return code and log.... something...
        return_code = rsync_proc.returncode
        if return_code != 0 and return_code != 24:
            utils.print_err("Error: Worker rsync to " + address[0] + " died prematurely! RC=" + str(return_code))
        else:
            os.remove(file_path)

        # connect to the worker and tell it to close up the rsync channel.
        # create a new socket for the worker connection.
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        worker_address = (address[0], 4018)

        # worker should reply in 30 seconds or so...
        sock2.settimeout(30)
        try:
            sock2.connect(worker_address)

            # send it the signal to shut-down.
            sock2.sendall("stop master repo_uuid=" + self.__my_uuid)
        except Exception as e:
            print e
            sock2.close()
            return False

        sock2.close()

        connection.close()
        return


class WorkerServerSync:
    __UUID = ""
    __client_UUID = ""
    __sync_active = False
    __rsync_pid = ""
    __client_ip = ""
    __req_mod = ""

    def __init__(self, req_uuid, cl_uuid, cl_ip, cl_mod, sync_active):
        self.__UUID = req_uuid
        self.__client_UUID = cl_uuid
        self.__client_ip = cl_ip
        self.__req_mod = cl_mod
        self.__sync_active = sync_active

    def get_req_mod(self):
        return self.__req_mod

    def set_req_mod(self, req_mod):
        self.__req_mod = req_mod

    def get_client_ip(self):
        return self.__client_ip

    def set_client_ip(self, cl_ip):
        self.__client_ip = cl_ip

    def get_rsync_pid(self):
        return self.__rsync_pid

    def set_rsync_pid(self, rsync_pid):
        self.__rsync_pid = rsync_pid

    def get_uuid(self):
        return self.__UUID

    def set_uuid(self, req_uuid):
        self.__UUID = req_uuid

    def get_client_uuid(self):
        return self.__client_UUID

    def set_client_uuid(self, cl_uuid):
        self.__client_UUID = cl_uuid

    def is_sync_active(self):
        return self.__sync_active

    def set_sync_active(self, sync_active=True):
        self.__sync_active = sync_active
