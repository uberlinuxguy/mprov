import re
import sys
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
    __sync_result = "error"
    __sync_thread = None  # type: threading.Thread
    __ctrl_thread = None  # type: threading.Thread
    __hb_timer = None  # type: threading.Timer
    __thread_condition = threading.Condition(threading.Lock())
    __data_lock = threading.RLock()

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
        self.__retries = 0
        self.__hb_timer = None  # type: threading.Timer
        self.__req_uuid = ""
        self.__syncing = False
        with self.__data_lock:
            self.__state = "new"

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

        utils.print_err("Attempting to connect to master: " + master_address[0])
        try:
            self.__master_connection.connect(master_address)
        except Exception as e:
            utils.print_err("Error: Master Connection failed in run(). ")
            utils.print_err("Error: " + str(e))


        # this lock will be released by the wait() call below, letting the register thread start.
        self.__thread_condition.acquire()

        # start the process with a call to register.
        # this sets up the timer functions and threads.
        #print("Starting register thread.")
        sys.stdout.flush()
        self.__hb_timer = threading.Thread(name="reg_thread", target=self._register_with_master).start()

        # wait for the sync to start.
        #print("Waiting for sync thread to start")
        sys.stdout.flush()
        while self.__sync_thread is None:
            #print(".")
            sys.stdout.write(".")
            sys.stdout.flush()
            self.__thread_condition.wait()

        #print("Waiting for sync to finish")
        sys.stdout.flush()

        # wait for the sync thread to finish
        while self.__sync_thread.isAlive():
            sys.stdout.write("^")
            sys.stdout.flush()
            self.__sync_thread.join(1)
            self.__thread_condition.wait()

        self.__thread_condition.release()
        # if we get here, tell the master we are done and cancel the timer.
        self.__hb_timer.cancel()
        #self._register_with_master()
        print "Sync complete."
        #print "Sync Thread Complete, waiting for control thread."

        # wait on the control thread.
        if self.__ctrl_thread is not None:
            self.__ctrl_thread.join()
        #print "Control thread complete"

        # wait on the hb timer.
        #self.__thread_condition.acquire()
        #self.__thread_condition.wait()

        # exit now and return the status.
        if self.__sync_result == "pass":
            #print "Exit 0"
            return 0
        else:
            #print "Exit 1"
            return 1

    def _register_with_master(self):
        with self.__thread_condition:


            if self.__master_connection is None:
                self.__master_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                # master should reply in 30 seconds or so...
                self.__master_connection.settimeout(30)
                master_address = (self.__config.get_conf_val("ms"), 4017)
                utils.print_err("Attempting to connect to master: " + master_address[0])
                try:
                    self.__master_connection.connect(master_address)
                except Exception as e:
                    utils.print_err("Error: Master Connection failed in _register_with_master(). ")
                    utils.print_err("Error: " + str(e))
                    self._restart_reg_timer()
                    self.__thread_condition.notify()
                    return

            try:

                cmd_send = "client client_uuid=" + self.__my_uuid + " image=" + self.__config.get_conf_val("image")
                with self.__data_lock:
                    if self.__req_uuid != "":
                        cmd_send = cmd_send + " uuid=" + self.__req_uuid
                        cmd_send = cmd_send + " state=" + self.__state
                    else:
                        cmd_send = cmd_send + " state=" + self.__state
                #print("Sending master: " + cmd_send)
                self.__master_connection.sendall(cmd_send)

                packet = utils.parse_packet(self.__master_connection.recv(1024))  # type: dict
                if packet is None:
                    utils.print_err("Error: Unable to parse packet.  Will retry")
                    self.__master_connection.close()
                    self.__master_connection = None
                print packet["raw_packet"]
                with self.__data_lock:
                    if self.__state == "done" or self.__state == "syncing":
                        if self.__state == "syncing":
                            self._restart_reg_timer()
                        self.__thread_condition.notify()
                        print "Sent state: " + self.__state
                        return
                if "ok" not in packet:
                    if "err" in packet:
                        utils.print_err("No worker found, waiting for more workers...")

                    else:
                        utils.print_err("Error: Master Server responded poorly to our register request. Will retry.")
                        utils.print_err("Error: Master Server Said: '" + packet["raw_packet"] + "'")
                    self.__master_connection.close()
                    self.__master_connection = None
                else:


                    # print "Master replied ok to us."
                    self.__req_uuid = packet["uuid"]

                    worker_ip = packet["worker_ip"]
                    if worker_ip is None:
                        # we don't have a worker.  So exit.
                        self._restart_reg_timer()
                        self.__thread_condition.notify()
                        return

                    # thread off to connect to the worker and start the sync
                    # set out syncing flag, so we know below that we shouldn't try to spawn another thread.

                    if not self.__syncing and self.__state == "new":
                        with self.__data_lock:
                            self.__syncing = True
                            self.__state = "syncing"
                        # now let's start the thread to talk to the worker.
                        self.__sync_thread = threading.Thread(target=self._handle_sync, args=(worker_ip,))
                        if self.__sync_thread is not None:
                            self.__sync_thread.start()

                # set this function up as a re-occuring timer based on the -b/--heartbeat option.
                self._restart_reg_timer()
            except Exception as e:
                if self.__master_connection is not None:
                    self.__master_connection.close()
                self.__master_connection = None
                utils.print_err("Error: Problem communicating with master server. Will Retry")
                self._restart_reg_timer()
            self.__thread_condition.notify()


    def _restart_reg_timer(self):
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
            sock.sendall("control client uuid=" + self.__req_uuid)
            packet = utils.parse_packet(sock.recv(1024))
            if "ok" not in packet:
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
            with self.__data_lock:
                self.__state="new"

        if not control_connection_good:
            with self.__data_lock:
                self.__state = "new"
            self._cancel_rsync()
            sock.close()

            return False
        #print "** CTRL THREAD STARTING LOOP"
        while self.__syncing:
            # if we get here we have a good connection, so let's make sure it doesn't go anywhere
            try:
                #print("Recv")
                sock.sendall("ok")
                packet = utils.parse_packet(sock.recv(15))
                if "ok" not in packet:
                    #print "Exit, not ok"
                    self._cancel_rsync()
                    self.__syncing = False
                    sock.close()
                    return False
                #print("Recvd")
            except Exception as e:
                print e
                #print "Exit, exception"
                sock.close()
                self._cancel_rsync()
                self.__syncing = False
                return False
            # utils.print_err("worker hb: " + str(time()))
            # small sleep to loosen the tight loop
            sleep(1)
        #print "** Exit Loop"
        # we are no longer syncing, close everything down.
        sock.close()

        self._cancel_rsync()
        self.__syncing = False
        print("** CTRL THREAD END")
        return

    def _handle_sync(self, worker_ip):

        if self.__retries > 5:
            utils.print_err("Error: Maximum retries (5) reached.  Exiting")
            sys.exit(-1)


        # connect to the worker and wait for it to reply that it's ready.
        # create a new socket for the worker connection.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        worker_address = (worker_ip, 4018)

        # give the worker 1 minute to reply, he has to check with the master.
        sock.settimeout(60)

        try:
            sock.connect(worker_address)
            sock.sendall("sync client uuid=" + self.__req_uuid + " client_uuid=" + self.__my_uuid)
            packet = utils.parse_packet(sock.recv(1024))
            if packet is None:
                utils.print_err("Error: Worker " + worker_ip + " replied badly.  Will retry.")
                utils.print_err("Error Packet is empty")
                sock.close()
                self.__syncing = False
                self.__retries += 1
                with self.__data_lock:
                    self.__state = "new"
                return False
            if "ok" not in packet:
                utils.print_err("Error: Worker replied badly.  Will retry.")
                utils.print_err("Error Packet: "  + packet["raw_packet"])
                sock.close()
                self.__syncing = False
                self.__retries += 1
                with self.__data_lock:
                    self.__state = "new"
                return False
        except Exception as e:
            sock.close()
            utils.print_err("Error: Exception in worker comms: " + str(e))
            self.__syncing = False
            self.__retries += 1
            utils.print_err(("Error: Sending master worker IP: " + worker_ip))
            self._notify_master_error(worker_ip)
            with self.__data_lock:
                self.__state = "new"
            return False

        # worker is connected
        # Establish a control connection in a separate thread
        self.__ctrl_thread = threading.Thread(target=self._worker_control, args=(worker_address,))
        if self.__ctrl_thread is not None:
            self.__ctrl_thread.start()

        # generate the module_name
        module_name = str(uuid.uuid4())
        sync_port = "8971"  # TODO: make this dynamic, and random.

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
        print "Starting sync from worker: " + worker_address[0]
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
        sock.sendall("ok client_uuid=" + self.__my_uuid + " port=" + sync_port + " module=" + module_name)

        # wait for the rsyncd to terminate.
        rsyncd_proc.communicate()
        os.remove(secrets_path)
        os.remove(rsyncd_path)

        #wait for the return packet to tell us if the sync was successful.
        try:
            res_packet = utils.parse_packet(sock.recv(1024))
            if res_packet is not None:
                if "ok" in res_packet:
                    if "result" in res_packet:
                        if res_packet['result'] == "error":
                            print "Worker Sync Failed! (Err: 101)"
                            self.__sync_result = 'error'
                            self._cancel_rsync()
                        elif res_packet['result'] == "pass":
                            print "Worker Sync Complete"
                            self.__sync_result = 'pass'
                    else:
                        print "Worker Sync Failed! (Err: 102)"
                        self.__sync_result = 'error'
                        self._cancel_rsync()
                else:
                    print "Worker Sync Failed! (Err: 103)"
                    self.__sync_result = 'error'
                    self._cancel_rsync()
            else:
                print "Worker Sync Failed! (Err: 104)"
                self.__sync_result = 'error'
                self._cancel_rsync()
        except socket.timeout:
            utils.print_err("Error: worker control comms. timeout.")
            print "Worker Sync Failed! (Err: 105)"
            self.__sync_result = 'error'
            self._cancel_rsync()

        sock.close()

        # set the state to done and let the register function handle letting the master know.
        with self.__data_lock:
            self.__state = "done"

        self.__syncing = False
        self.__exiting = True
        print "Exit Worker Thread"

    def _cancel_rsync(self):
        # cancel the rsync.
        if self.__rsyncd_pid > 0:
            # attempt to terminate the rsyncd process
            try:
                os.kill(self.__rsyncd_pid, signal.SIGTERM)
            except OSError:
                return

    def _notify_master_error(self, worker_ip):
        if self.__master_connection is None:
            self.__master_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # master should reply in 30 seconds or so...
            self.__master_connection.settimeout(30)
            master_address = (self.__config.get_conf_val("ms"), 4017)

            try:
                self.__master_connection.connect(master_address)

            except Exception as e:
                utils.print_err("Error: Master Connection failed in _notify_master_error() ")
                utils.print_err("Error: " + str(e))
        with self.__data_lock:
            self.__state = "new"
        if self.__master_connection is not None:
            cmd_send = "client client_uuid=" + self.__my_uuid + " image=" + self.__config.get_conf_val("image")
            if self.__req_uuid != "":
                cmd_send = cmd_send + " uuid=" + self.__req_uuid
            with self.__data_lock:
                cmd_send = cmd_send + " state=" + self.__state + " worker_ip="
            cmd_send = cmd_send +  worker_ip + " worker_state=error"

            #utils.print_err("Sending to master: " + cmd_send)
            self.__master_connection.sendall(cmd_send)
            self.__master_connection.recv(1024)
        else:
            utils.print_err("Error: Unable to notify master.")
            utils.print_err("Error: Stale connection likely present on master.")
