"""
config.py handles the commandline arguments that are "configurable"
within mprov
"""

import argparse
from utils import print_err


class Config(object):
    """
    Parses the command line args for all 3 modes of operation.
    and acts as a runtime repo for them.
    """

    def print_config(self):
        """
        Used to print the config based off a set of commandline args.
        :return: none.
        """
        print "mprov configuration:"

        # get a dict of the namespace
        cmd_args_dict = self.cmdArgs.__dict__
        for key in cmd_args_dict:
            print "\t" + key + ": " + str(cmd_args_dict[key])
        print "\n"

    def __init__(self):
        """
        load the config options and parse them into the config object.
        """
        parser = argparse.ArgumentParser()

        # specifies what program type we will be.
        type_args = parser.add_mutually_exclusive_group()
        type_args.add_argument("-m", "--master", help="Start the master server", action="store_true")
        type_args.add_argument("-w", "--worker", help="Start the worker server", action="store_true")
        type_args.add_argument("-c", "--client", help="Start the client [default]", action="store_true")

        # add all the rest of the args to the parser

        # a couple of shared args.
        parser.add_argument("-p", "--path",
                            help="Where the image files are (master,worker) or where to sync to (client)")

        parser.add_argument("--ms", help="The master server to connect to (worker,client)")

        # master only args.
        parser.add_argument("--sync",
                            help="How long we should wait between periodic syncs with the workers.(master)")
        parser.add_argument("-u", "--worker_purge",
                            help="How long to wait before we purge an unresponsive worker (master)")

        # worker only args
        parser.add_argument("-s", "--slots", help="How many slots this worker has (worker)")
        parser.add_argument("-b", "--heartbeat", help="How often we send our heartbeat to the master(worker/client)")

        # client only args
        parser.add_argument("-i", "--image", help="Image to sync to the client.(client)")

        # debug args
        parser.add_argument("--print_config", help="Print out the parsed args.", action="store_true")
        self.cmdArgs = parser.parse_args()

        # if the user requested to print the config, then just run print_config and exit.
        if self.cmdArgs.print_config:
            self.print_config()
            exit(1)

        # if no type is set, set client.
        if (self.cmdArgs.master is False) and (self.cmdArgs.worker is False) and (self.cmdArgs.client is False):
            print "Defaulting to client."
            self.cmdArgs.client = True

        # check to make sure we don't have any args we don't need in the mode specified.
        # check for master args and error on others.
        arg_err = False
        if self.cmdArgs.master:

            # check for master args and default if not set
            if self.cmdArgs.path is None:
                print_err("Error: You MUST specify a path!")
                exit(1)

            if self.cmdArgs.sync is None:
                self.cmdArgs.sync = "10m"

            if self.cmdArgs.worker_purge is None:
                self.cmdArgs.worker_purge = "5m"

            arg_err = False

            # now let's see if any other things are set that don't need to be.
            if self.cmdArgs.slots is not None:
                print_err("Error: Argument '-s/--slots' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.ms is not None:
                print_err("Error: Argument '--ms' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.heartbeat is not None:
                print_err("Error: Argument '-b/--heartbeat' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.image is not None:
                print_err("Error: Argument '-i/--image' supplied but not used in this mode!!\n")
                arg_err = True

            if arg_err:
                exit(1)
        elif self.cmdArgs.worker:
            # check for worker args and default if not set
            if self.cmdArgs.path is None:
                print_err("Error: You MUST specify a path!")
                exit(1)

            if self.cmdArgs.ms is None:
                print_err("Error: You MUST specify a master server to talk to with --ms")
                exit(1)

            # optional args that havew a default.
            if self.cmdArgs.slots is None:
                self.cmdArgs.slots = 10

            if self.cmdArgs.heartbeat is None:
                self.cmdArgs.heartbeat = "10"

            # now check for other args that shouldn't be set.
            if self.cmdArgs.sync is not None:
                print_err("Error: Argument '--sync' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.worker_purge is not None:
                print_err("Error: Argument '--worker_purge/-u' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.image is not None:
                print_err("Error: Argument '-i/--image' supplied but not used in this mode!!\n")
                arg_err = True

            if arg_err:
                exit(1)
        elif self.cmdArgs.client:
            # check for client args and default if not set
            if self.cmdArgs.path is None:
                print_err("Error: You MUST specify a path!")
                exit(1)

            if self.cmdArgs.ms is None:
                print_err("Error: You MUST specify a master server to talk to with --ms")
                exit(1)

            if self.cmdArgs.image is None:
                print_err("Error: You MUST specify an image to pull with -i or --image")
                exit(1)

            if self.cmdArgs.heartbeat is None:
                self.cmdArgs.heartbeat = 10

            # now check for other args that shouldn't be set.
            if self.cmdArgs.slots is not None:
                print_err("Error: Argument '-s/--slots' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.sync is not None:
                print_err("Error: Argument '--sync' supplied but not used in this mode!!\n")
                arg_err = True

            if self.cmdArgs.worker_purge is not None:
                print_err("Error: Argument '--worker_purge/-u' supplied but not used in this mode!!\n")
                arg_err = True

            if arg_err:
                exit(1)

    def get_conf_val(self, key):
        """
        get a key's value from the config object.
        :param key: the key to retrieve.
        :return: The value from the config.
        """
        return self.cmdArgs.__dict__[key]
