import traceback
import uuid
from tempfile import mkstemp
import socket
import threading
import os

import signal

import sys

import utils
from time import time, sleep
from datetime import datetime
import re
import Config
import subprocess


class MasterServer(object):
    __UUID = ""
    __config = None  # type: Config
    __sync_timer_interval = 0
    __purge_timer_interval = 0
    __sync_slots = 1  # hard coded for master server.
    __sync_slots_used = 0
    __path = ""
    __exiting = False
    __sync_timer = None  # type: threading.Timer
    __purge_timer = None  # type: threading.Timer()
    __master_data_lock = None # type: threading.Lock()


    def signal_handler(self, signum, frame):
        self.__exiting = True
        self.__sync_timer.cancel()
        self.__purge_timer.cancel()

    def __init__(self, config):
        """
        The constructor
        :param config: an mprov Config object.
        :type config: mprov.Config.Config
        """
        self.__UUID =  str(uuid.uuid4())
        self.__path = config.get_conf_val("path")
        self.__config = config
        if not os.path.exists(self.__path):
            utils.print_err("Error: Path " + self.__path + " doesn't exist! Exiting.")
            exit(1)

        self.workers = list()  # type: list MasterServerWorkerEntry
        self.client_requests = list()  # type: list MasterServerClientRequest

        self.__master_data_lock = threading.Lock()

        # set up our listen socket.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # type: socket
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", 4017))

        # convert the "sync" parameter to seconds
        tmp_timer_str = self.__config.get_conf_val("sync")
        self.__sync_timer_interval = int(re.sub("\D", "", tmp_timer_str))
        if tmp_timer_str[-1:] == "m":
            self.__sync_timer_interval = self.__sync_timer_interval * 60

        # convert the "worker_purge" parameter to seconds
        tmp_timer_str = self.__config.get_conf_val("worker_purge")
        self.__purge_timer_interval = int(re.sub("\D", "", tmp_timer_str))
        if tmp_timer_str[-1:] == "m":
            self.__purge_timer_interval = self.__purge_timer_interval * 60

    def run(self):
        """
        Run the MasterServer
        :return: none
        """
        self.sock.listen(1024)

        # create a timer for syncing
        self.__sync_timer = threading.Timer(self.__sync_timer_interval, self._worker_sync_timer())
        self.__sync_timer.start()

        # create a timer for purge checking
        self.__purge_timer = threading.Timer(self.__purge_timer_interval, self._stale_purge_timer)
        self.__purge_timer.start()

        while True:

            try:
                client, address = self.sock.accept()
            except KeyboardInterrupt as kbd_int:
                self.signal_handler(signal.SIGINT, None)
                return

            client.settimeout(60)
            threading.Thread(target=self._handle_connection, args=(client, address)).start()

    def _handle_connection(self, client, address):
        """
        Handle the incoming connection
        :param client:  the worker or client wanting to talk to us.
        :type client: socket.socket
        :type address: List[str]
        :param address: possibly the INET address? It's returned from sock.accept()
        :return: True if we close OK, False if we don't. I don't think this matters.
        """
        size = 1024
        while True:
            try:
                packet = utils.parse_packet(client.recv(size))
                if packet is not None:
                    if "execmd" in packet:
                        self._handle_cmd(client, address, packet["raw_packet"])
                    elif "worker" in packet:
                        self._handle_worker_req(client, address, packet["raw_packet"])
                    elif "client" in packet:
                        if "worker_state" in packet:
                            # the client thinks the worker is bad, let's see if they are a valid client.
                            if packet['worker_state'] == "error":
                                req = self._find_req_by_uuid(packet["uuid"])
                                # valid request
                                if req is not None:
                                    worker = self._find_worker_by_uuid(req.get_worker_uuid())
                                    if worker is not None:
                                        worker.set_status("error")
                                    else:
                                        utils.print_err("Error: client reported error on unknown worker.")
                                else:
                                    utils.print_err("Error: unknown request attempted to report worker error")
                        self._handle_client_req(client, address, packet["raw_packet"])
                    elif "verify" in packet:
                        req_uuid = packet["uuid"]
                        client_req = self._find_req_by_uuid(req_uuid)  # type: MasterServerClientRequest
                        if client_req is None:
                            client.send("err: unable to find request")
                        else:
                            client.send(client_req.serialize() + "uuid=" + req_uuid)
                    else:
                        client.send("Error: Unrecognized command.\n")
                else:
                    client.close()
                    return False
            except socket.timeout:
            #    print e
                client.close()
                return False

    def _handle_worker_req(self, client, address, data):
        """
        sub function to handle a worker request.

        :param client:
        :param address:
        :param data:
        :type client: socket.socket
        :type address: List[str]
        :return:
        """

        worker_obj = MasterServerWorkerEntry(data + " ip=" + address[0])

        # look for this worker already in the list, and just update it's hb entry.
        for worker in self.workers:  # type: MasterServerWorkerEntry
            # check for the UUID.
            if worker_obj.get_uuid() == worker.get_uuid():

                # if the worker is in the list, but it's errored,
                # and it has sent us a heartbeat, it's up, so try a
                # sync again.
                if worker.get_status() == "error":
                    threading.Thread(target=self._sync_worker, args=(worker,))
                worker.set_last_hb(time())
                # print "Worker: " + worker.get_name() + ": hb."
                client.sendall("ok")
                return
            else:
                if (worker_obj.get_name() == worker.get_name()) and \
                        (worker_obj.get_ip() == worker.get_ip()) :
                    # the name and ip seem the same as a worker already in memory.
                    # remove thahttp://www.bebe.com/DIPDYE-SEAMLESS-LEGGINGS/94877.pro?selectedColor=vsn&source=PJ_AD:Z:BEBE_BEBE&affiliateId=58012&clickId=2137279513&extid=af_58012&affiliateCustomUrl={deeplink}&utm_medium=affiliate&utm_source=EEAN&utm_campaign=58012&utm_content=1-6671&cvosrc=affiliate.pepperjam.58012&cvo_cid=2137279513t worker
                    self.workers.remove(worker)
                    # break out of the for loop and tread this as a new worker.
                    break

        # worker was not found in the existing entries, so create a new one
        worker_obj.set_last_sync(0)
        worker_obj.set_last_hb(time())
        worker_obj.set_slots_in_use(0)
        worker_obj.set_status("outdated")
        self.workers.append(worker_obj)
        client.sendall("ok")
        print "New worker: " + address[0] + " registered"
        threading.Thread(target=self._sync_worker, args=(worker_obj,)).start()

    def _handle_client_req(self, connection, address, data):
        """
        sub function to handle a client sync request.

        :param connection:
        :type connection: socket.socket
        :param address: list str
        :param data: str
        :return:
        """
        client_obj = MasterServerClientRequest(data + " ip=" + address[0])  # type: MasterServerClientRequest


        # look for this client_request already in the list, and just update it's hb entry.
        for m_client in self.client_requests:  # type: MasterServerClientRequest
            if client_obj.get_uuid() == m_client.get_uuid():
                m_client.set_last_hb(time())
                tmp_worker = self._find_worker_by_uuid(m_client.get_worker_uuid())
                if tmp_worker is None:
                    utils.print_err("Error: unable to get worker by uuid for client. Worker UUID: " +
                                    m_client.get_worker_uuid())
                    utils.print_err("Error: Purging client.")
                    self.client_requests.remove(m_client)

                if client_obj.get_done() == "done":
                    tmp_worker.set_slots_in_use(tmp_worker.get_slots_in_use()-1)

                connection.sendall("ok uuid=" + client_obj.get_uuid() +
                                   " worker_ip=" + tmp_worker.get_ip() + "\n")
                return

        client_obj.set_last_hb(time())
        self.client_requests.append(client_obj)
        #
        # look up the least used worker and send the client there.

        free_worker = self._find_least_updated_worker()  # type: MasterServerWorkerEntry
        if free_worker is None:
            connection.sendall("err no workers found.")
            self.client_requests.remove(client_obj)
            return

        client_obj.set_worker_uuid(free_worker.get_uuid())
        free_worker.set_slots_in_use(free_worker.get_slots_in_use() + 1 )
    
        connection.sendall("ok uuid=" + client_obj.get_uuid() + " worker_ip=" + free_worker.get_ip() + "\n")

    def _handle_cmd(self, connection, address, data):
        """
        sub function to handle execmd commands from a direct connection to the socket.

        :param connection:
        :param address:
        :param data: incoming command string.
        :type connection: socket.socket
        :return:
        """

        # security check, only localhost connections.
        if address[0] != "127.0.0.1":
            connection.send("Permission Denied.\n\n")
            connection.close()
            return
        packet = utils.parse_packet(data)
        if "list" in packet:
            if "workers" in packet:
                connection.send("Currently registered workers:\n")
                for worker in self.workers:  # type: MasterServerWorkerEntry
                    connection.send("\t" + worker.get_name() + " " +
                                    worker.get_uuid() + " " +
                                    worker.get_ip() + " " +
                                    datetime.fromtimestamp(worker.get_last_sync()).strftime('%Y-%m-%d %H:%M:%S') + " " +
                                    str(worker.get_slots_total()) + " " +
                                    str(worker.get_slots_in_use()) + " " +
                                    worker.get_status() + "\n")
            elif "clients" in packet:
                connection.send("Current client requests:\n")
                for m_client in self.client_requests:  # type: MasterServerClientRequest
                    connection.send("\t" + m_client.get_ip() + " " +
                                    m_client.get_client_uuid() + " " +
                                    m_client.get_req_mod() + " " +
                                    str(m_client.get_start_time()) + " " +
                                    m_client.get_uuid() + "\n")
            else:
                connection.send("Error: Unrecognized command.\n")
        elif "purge" in packet:
            # issue a request to purge any stale worker nodes.
            # worker_purge calls the function to also purge stale clients.
            self._do_stale_purge()
            connection.send("ok\n")

        elif "sync" in packet:
            # issue a request to sync all worker nodes.
            print "Full sync requested. Invalidating all workers."
            self._do_worker_syncs()
            connection.send("ok\n")
        else:
            connection.send("Error: Unrecognized command.\n")

    def _find_worker_by_uuid(self, worker_uuid):
        """
        find a worker object by uuid
        :param worker_uuid:
        :return: the worker if found, None otherwise.
        :rtype: MasterServerWorkerEntry

        """
        tmp_worker = None  # type: MasterServerWorkerEntry
        for worker in self.workers:  # type: MasterServerWorkerEntry
            if worker.get_uuid() == worker_uuid:
                tmp_worker = worker

        return tmp_worker

    def _find_least_updated_worker(self):
        """
        find the least used, but up-to-date worker node.

        :return: the worker in question, None otherwise
        :rtype: MasterServerWorkerEntry
        """

        tmp_worker = None  # type: MasterServerWorkerEntry
        self.__master_data_lock.acquire()

        for worker in self.workers:  # type: MasterServerWorkerEntry
            # if this worker is updated.
            if worker.get_status() == "updated":
                # if this worker has no used slots, return it.
                if worker.get_slots_in_use() <= 0:
                    self.__master_data_lock.release()
                    return worker
                else:
                    # if this worker has used slots but less used slots than total slots.
                    if worker.get_slots_in_use() < worker.get_slots_total():
                        # assign tmp_worker if this is the first found worker
                        if tmp_worker is None:
                            tmp_worker = worker
                        # otherwise, see if this worker has less slots in use than
                        # the previous one.
                        elif worker.get_slots_in_use() < tmp_worker.get_slots_in_use():
                            # if so, assign it to tmp_worker and iterate.
                            tmp_worker = worker
        # should either be None, if no worker is found to have any open slots.
        # or the worker with the least open slots.
        self.__master_data_lock.release()
        return tmp_worker

    def _worker_sync_timer(self):
        self._do_worker_syncs()
        if not self.__exiting:
            self.__sync_timer = threading.Timer(self.__sync_timer_interval, self._worker_sync_timer)
            self.__sync_timer.start()

    def _do_worker_syncs(self):
        """"
        timer function to sync the worker nodes and remove any stale workers.
        """
        print "Worker Sync Started."
        # first invalidate all the workers.
        for worker in self.workers:
            if worker.get_status() == "updated":
                worker.set_status("outdated")

        # now step through the remaining workers and sync anyone that isn't syncing.
        self.__master_data_lock.acquire()
        for worker in self.workers:  # type: MasterServerWorkerEntry
            if worker.get_status() == "outdated":
                threading.Thread(target=self._sync_worker, args=(worker,)).start()
        self.__master_data_lock.release()

    def _sync_worker(self, worker):
        """

        :param worker: worker to sync
        :type  worker: MasterServerWorkerEntry
        :return: True | False
        :rtype: int
        """

        force_master_sync = False

        # attempt to sync to a worker.  As a last result, try to sync from master.
        while not force_master_sync:
            self.__master_data_lock.acquire()
            worker.set_status("waiting")
            self.__master_data_lock.release()
            # only try to send us to a worker if we aren't being forced to a master sync
            # if we are not forcing a master sync,
            # attempt to hand off the client to a worker
            worker_sync = self._find_least_updated_worker()
            if worker_sync is not None:
                # increment the worker's use count.
                self.__master_data_lock.acquire()
                worker_sync.set_slots_in_use(worker_sync.get_slots_in_use() + 1)
                self.__master_data_lock.release()
                # don't allow a worker to sync to itself.
                if worker_sync.get_uuid() == worker.get_uuid():
                    worker_sync = None
                    self.__master_data_lock.acquire()
                    worker_sync.set_slots_in_use(worker_sync.get_slots_in_use() - 1)
                    # if we are alone, no one is syncing to the master, let us do it.
                    if self.__sync_slots_used < self.__sync_slots:
                        self.__sync_slots_used = self.__sync_slots_used + 1
                        force_master_sync = True
                    self.__master_data_lock.release()

                else:
                    # we have a valid, updated worker, try a sync
                    worker.set_sync_src_uuid(worker_sync.get_uuid())
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                    try:
                        # get the ip of the worker we are going to sync TO
                        worker_address = (worker.get_ip(), 4018)
                        # connect to the recipient worker and tell it to sync FROM worker_sync
                        sock.connect(worker_address)
                        # increment the worker's in use counter.
                        self.__master_data_lock.acquire()
                        worker.set_status("syncing")
                        self.__master_data_lock.release()
                        sock.sendall("sync master worker=" + worker_sync.get_ip())
                        packet = utils.parse_packet(sock.recv(1024))  # type: dict
                        # then decrement it after it's over.
                        self.__master_data_lock.acquire()
                        worker_sync.set_slots_in_use(worker_sync.get_slots_in_use() - 1)
                        self.__master_data_lock.release()
                        if packet is None:
                            utils.print_err("Error: empty packet from " + worker_sync.get_ip())
                            worker.set_status("waiting")
                        else:
                            if "ok" not in packet:
                                self.__master_data_lock.acquire()
                                worker.set_status("error")
                                self.__master_data_lock.release()
                                sock.close()
                                return False

                            # status is updated from an updated worker, set to upadted
                            self.__master_data_lock.acquire()
                            worker.set_status("updated")
                            worker.set_last_sync(time())
                            self.__master_data_lock.release()
                            return True
                    except Exception as e:
                        self.__master_data_lock.acquire()
                        worker.set_status("error")
                        self.__master_data_lock.release()
                        utils.print_err("Error: worker to worker sync failed")
                        utils.print_err(e)

                        sock.close()
            else:
                # no updated nodes, if the master is available, let's try that.
                self.__master_data_lock.acquire()
                if self.__sync_slots_used < self.__sync_slots:
                    self.__sync_slots_used = self.__sync_slots_used + 1
                    force_master_sync = True
                self.__master_data_lock.release()
            # otherwise, wait a bit and try again.
            sleep(5)

        # no worker found or master sync forced, so proceed with
        # normal master sync.


        # connect to the worker and wait for it to reply that it's ready.
        # create a new socket for the worker connection.
        worker.set_sync_src_uuid(self.__UUID)
        self.__master_data_lock.acquire()
        worker.set_status("syncing")
        self.__master_data_lock.release()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        worker_address = (worker.get_ip(), 4018)

        sock.settimeout(30)

        try:
            sock.connect(worker_address)
            sock.sendall("sync master repo_uuid=" + self.__UUID)
            packet = utils.parse_packet(sock.recv(1024))  # type: dict
            if "ok" not in packet:
                self.__sync_slots_used = self.__sync_slots_used - 1
                self.__master_data_lock.acquire()
                worker.set_status("error")
                self.__master_data_lock.release()
                sock.close()
                return False
        except Exception as e:
            self.__sync_slots_used = self.__sync_slots_used - 1
            utils.print_err("Error: Unable to talk to worker at " + worker_address[0] + ": " + e.message)
            sock.close()
            self.__master_data_lock.acquire()
            worker.set_status("error")
            self.__master_data_lock.release()
            return False

        # get the worker's parameters
        worker_uuid = packet["worker_uuid"]
        port = packet["port"]
        rsync_mod = packet["module"]
        # worker_uuid, port, rsync_mod = data[3:].split()

        # double check the worker's supplied params.
        if worker_uuid == "":
            self.__sync_slots_used = self.__sync_slots_used - 1
            self.__master_data_lock.acquire()
            worker.set_status("error")
            self.__master_data_lock.release()
            sock.close()
            return False

        if port == "":
            self.__sync_slots_used = self.__sync_slots_used - 1
            self.__master_data_lock.acquire()
            worker.set_status("error")
            self.__master_data_lock.release()
            sock.close()
            return False

        if rsync_mod == "":
            self.__master_data_lock.acquire()
            worker.set_status("error")
            self.__master_data_lock.release()
            sock.close()
            return False

        sock.close()

        # if the worker is ready, start the sync
        # create a temporary file with the password
        fd, file_path = mkstemp()
        pass_file = open(file_path, "w")
        pass_file.write(worker_uuid)
        pass_file.close()
        os.close(fd)

        if not os.path.isdir("/tmp/mprov/") :
            # mprov tmp dir doesn't exist.
            os.mkdir("/tmp/mprov", 700)

        # open an rsync log for logging.
        rsync_log = open("/tmp/mprov/master_sync_" + rsync_mod + ".log", "w+")

        rsync_args=["/usr/bin/rsync",
                    "-avx",
                    "--delete",
                    "--progress",
                    "--password-file=" + file_path,
                    "--port=" + port,
                    self.__path + "/",
                    "root@" + worker.get_ip() + "::" + rsync_mod]
        print "Run: " + " ".join(rsync_args)

        rsync_log.write("Run: " + " ".join(rsync_args) + "\n")

        # wait a couple of seconds for the worker to set up the sync
        sleep(5)

        rsync_proc = subprocess.Popen(rsync_args, stdout=rsync_log, stderr=rsync_log)

        # wait for the rsync to finish.
        rsync_proc.communicate()

        return_code = rsync_proc.returncode

        rsync_log.close()

        # connect to the worker and tell it to close up the rsync channel.
        # create a new socket for the worker connection.
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        worker_address = (worker.get_ip(), 4018)

        # worker should reply in 30 seconds or so...
        sock2.settimeout(30)
        try:
            sock2.connect(worker_address)

            # send it the signal to shut-down.
            sock2.sendall("stop master repo_uuid=" + self.__UUID)
        except Exception as e:
            print e
            self.__master_data_lock.acquire()
            self.__sync_slots_used = self.__sync_slots_used - 1
            worker.set_status("error")
            self.__master_data_lock.release()
            sock2.close()
            return False

        sock2.close()

        # now, let's check the return code of the rsync process and see if it was an error.
        if return_code != 0  and return_code != 24:
            utils.print_err("Error: rsync returned '" + str(return_code) + "'")
            utils.print_err("Error: marking worker as status 'error'")
            self.__master_data_lock.acquire()
            worker.set_status("error")
            self.__master_data_lock.release()
            self.__sync_slots_used = self.__sync_slots_used - 1
            return False
        else:

            self.__master_data_lock.acquire()
            worker.set_status("updated")

            self.__sync_slots_used = self.__sync_slots_used - 1
            if self.__sync_slots_used < 0:
                self.__sync_slots_used = 0
            worker.set_last_sync(time())
            self.__master_data_lock.release()
            os.remove(file_path)

        # once the sync is done, exit our self.
        return True

    def _stale_purge_timer(self):
        self._do_stale_purge()
        if not self.__exiting:
            # restart the timer.
            self.__purge_timer = threading.Timer(self.__purge_timer_interval, self._stale_purge_timer)
            self.__purge_timer.start()

    def _do_stale_purge(self):
        """
        timer function to purge stale stuff.
        :return:
        """
        self._do_worker_purge()
        self._do_client_purge()

    def _do_worker_purge(self):
        """
        timer function to purge stale worker nodes.
        """
        self.__master_data_lock.acquire()
        for worker in self.workers:  # type: MasterServerWorkerEntry

            # easier to understand if this data collection is broken out.
            curr_time = time()
            w_hb = worker.get_last_hb()
            time_check = curr_time - self.__purge_timer_interval
            # print "Worker: " + worker.get_uuid() + " last_hb: " + str(w_hb) + "; time_check: " + str(time_check)

            # if we haven't heard from this worker in a while, remove it.
            if w_hb <= time_check:
                print "Purging worker: " + worker.get_name()
                self.workers.remove(worker)
        self.__master_data_lock.release()

    def _do_client_purge(self):
        """
        timer function to purge stale client requests.
        :return:
        """
        self.__master_data_lock.acquire()
        for m_client in self.client_requests:  # type: MasterServerClientRequest

            # easier to understand if this data collection is broken out.
            curr_time = time()
            w_hb = m_client.get_last_hb()
            time_check = curr_time - self.__purge_timer_interval

            # if we haven't heard from this client in a while, remove it.
            if w_hb <= time_check:
                print "Purging client: " + m_client.get_ip()
                self.client_requests.remove(m_client)
                tmp_worker = self._find_worker_by_uuid(m_client.get_worker_uuid())  # type: MasterServerWorkerEntry
                tmp_worker.set_slots_in_use(tmp_worker.get_slots_in_use()-1)
        self.__master_data_lock.release()

    def _find_req_by_uuid(self, req_uuid):
        self.__master_data_lock.acquire()
        for client_req in self.client_requests:  # type: MasterServerClientRequest
            if client_req.get_uuid() == req_uuid:
                self.__master_data_lock.release()
                return client_req
        self.__master_data_lock.release()
        return None


class MasterServerWorkerEntry(object):
    """

    """

    __name = ""
    __UUID = ""
    __last_sync = 0
    __slots_total = 0
    __slots_in_use = 0
    __status = "inactive"
    __ip = ""
    __last_hb = 0
    __sync_src_uuid = ""

    def __init__(self, info):
        packet = utils.parse_packet(info)
        self.__name = packet["name"]
        self.__UUID = packet["worker_uuid"]
        self.__slots_total = packet["slots"]
        self.__ip = packet["ip"]

    def get_slots_in_use(self):
        return int(self.__slots_in_use)

    def get_slots_total(self):
        return int(self.__slots_total)

    def get_status(self):
        return self.__status

    def get_ip(self):
        return self.__ip

    def get_name(self):
        return self.__name

    def get_uuid(self):
        return self.__UUID

    def get_last_sync(self):
        return self.__last_sync

    def get_last_hb(self):
        return self.__last_hb

    def set_last_sync(self, last_sync):
        self.__last_sync = last_sync

    def set_slots_in_use(self, slots_in_use):
        if slots_in_use < 0:
            self.__slots_in_use = 0
        else:
            self.__slots_in_use = slots_in_use

    def set_status(self, status):
        self.__status = status

    def set_last_hb(self, last_hb):
        self.__last_hb = last_hb

    def set_uuid(self, uuid):
        self.__UUID = uuid

    def set_sync_src_uuid(self, src_uuid):
        self.__sync_src_uuid = src_uuid

    def get_sync_src_uuid(self):
        return self.__sync_src_uuid

class MasterServerClientRequest(object):

    __UUID = ""
    __client_UUID = ""
    __worker_UUID = ""
    __worker_slot = 0
    __start_time = 0
    __req_mod = ""
    __ip = ""
    __last_hb = ""
    __done = False

    def set_last_hb(self, hb):
        self.__last_hb = hb

    def get_last_hb(self):
        return self.__last_hb

    def set_uuid(self, req_uuid):
        self.__UUID = req_uuid

    def set_client_uuid(self, client_uuid):
        self.__client_UUID = client_uuid

    def set_worker_uuid(self, worker_uuid):
        self.__worker_UUID = worker_uuid

    def set_start_time(self, start_time):
        self.__start_time = start_time

    def set_req_mod(self, req_mod):
        self.__req_mod = req_mod

    def set_ip(self, ip):
        self.__ip = ip

    def get_uuid(self):
        return self.__UUID

    def get_client_uuid(self):
        return self.__client_UUID

    def get_worker_uuid(self):
        return self.__worker_UUID

    def get_start_time(self):
        return self.__start_time

    def get_req_mod(self):
        return self.__req_mod

    def get_ip(self):
        return self.__ip

    def get_done(self):
        return self.__done

    def __init__(self, data):
        """

        :param data:
        :type data: str
        """
        packet = utils.parse_packet(data)
        self.__client_UUID = packet["client_uuid"]
        self.__req_mod = packet["image"]
        self.__ip = packet["ip"]
        self.__done = packet["state"]
        if "uuid" in packet:
            self.__UUID=packet["uuid"]
        else:
            print "New Client Request from: " + self.__ip
            self.__UUID = str(uuid.uuid4())
        self.__start_time = time()

    def serialize(self):
        return "client_uuid=" + self.__client_UUID + \
               " image=" + self.__req_mod + \
               " ip=" + self.__ip + \
               " uuid=" + self.__UUID + "\n"
