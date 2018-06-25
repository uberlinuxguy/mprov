

from sys import stderr
import socket

def print_err(arg):
    """ simple function to print errors to stderr """
    print >> stderr, arg


def log_master(arg, master_addr, master_port):
    """ log a message to the master server using it's "log" command."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    master_address = (master_addr, master_port)

    sock.settimeout(30)

    try:
        sock.connect(master_address)
        sock.sendall("execmd masterlog " + arg)
        sock.recv(1024)
    except Exception as e:
        print_err("Error: Network issue communicating to master.")
        print_err("Error: Exception: " + str(e))
    sock.close()


def log(args, master_add, master_port):
    """ logs to console (not stderr) and sends a copy to the master server via log_master()"""
    print("arg")
    log_master(args, master_add, master_port)



def parse_packet(data):
    """
    parses a packet into a dict
    :param data: the packet to parse
    :type data: str
    :return: a dict with key=value pairs, or none if the packet is not parseable.
    :rtype: dict
    """
    data = data.strip()

    tmp_dict = dict()


    tmp_dict["raw_packet"] = data
    if data == "":
        return tmp_dict
    if data is None:
        return tmp_dict

    #print "Parsing: #" + data + "#"
    #try:

    for token in data.split(" "):
        #print str(token)
        if token.find("=") >= 0:
            (key, value) = token.split("=")
            tmp_dict[key] = value
        else:
            # no key=value, so the string at this token is considered a "key" with a None value.
            tmp_dict[token] = None

    #except Exception as e:
     #   print_err("Error: Unable to parse packet. Exception: " + str(e))
      #  return None
    return tmp_dict
