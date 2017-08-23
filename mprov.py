#!/usr/bin/python

"""
mprov is a multi-threaded provisioning tool.  It's meant to
be used in environments with many, many, many hosts.  The key
to mprov is that is will allow hosts that have pxebooted to talk to
a central server, ask for the rsync of a system image on that server,
and be directed to a 'worker' who will actually perform the rsync.
"""
import signal

import mprov.Config
from mprov.utils import print_err
if __name__ == "__main__":

    ''' Create a new Config object. '''
    config = mprov.Config.Config()

    ''' A variable to hold what program we are going to launch '''
    program = None

    ''' Now figure out what program to launch based off the command line args. '''
    if config.get_conf_val("master"):
        import mprov.MasterServer
        program = mprov.MasterServer.MasterServer(config)
    elif config.get_conf_val("worker"):
        import mprov.WorkerServer
        program = mprov.WorkerServer.WorkerServer(config)
    elif config.get_conf_val("client"):
        import mprov.Client
        program = mprov.Client.Client(config)
    else:
        ''' XXX: Should never get here, but in case we do... '''
        print_err("Error: Not sure what to run!? How'd you do that?!?!\n\n")
        exit(1)

    ''' hand off to the program the user wants. '''
    program.run()
    signal.signal(signal.SIGINT, program.signal_handler)
    exit(0)
