

from sys import stderr

def print_err(arg):
    """ simple function to print errors to stderr """
    print >> stderr, arg


def parse_packet(data):
    """
    parses a packet into a dict
    :param data: the packet to parse
    :type data: str
    :return: a dict with key=value pairs, or none if the packet is not parseable.
    :rtype: dict
    """

    tmp_dict = dict()

    tmp_dict["raw_packet"] = data
    if data == "":
        return None
    try:
        data = data.strip()
        for token in data.split():
            if token.find("=") >= 0:
                (key, value) = token.split("=")
                tmp_dict[key] = value
            else:
                # no key=value, so the string at this token is considered a "key" with a None value.
                tmp_dict[token] = None

    except Exception as e:
        print_err("Error: Unable to parse packet. Exception: " + e.message)
        return None
    return tmp_dict
