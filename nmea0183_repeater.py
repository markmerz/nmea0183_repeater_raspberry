#!/usr/bin/python3

import serial
import os
import json
import glob
import subprocess
import threading
import sys
import queue
import signal

MAX_WRITE_QUEUE_LEN = 100 # how many messages to hold in write queue before starting to drop messages

repeatLock = threading.Lock()
continue_work = True
reader_threads = []
writer_threads = []
debug_flag = False

def main():
    global debug_flag
    config_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.json")
    with open(config_file) as json_file:    
        configurations = json.load(json_file)

    if "DEBUG" in configurations:
        if configurations["DEBUG"].upper() in ["YES", "TRUE"]:
            debug_flag = True

    # we map physical usb ports to ttyUSB# hooks. This hack also works with usb hubs. This way You can be sure
    # that when udev decides to map serial converters in some other way, Your configuration stays stable.
    # Shell command: 
    # $ udevadm info -q path -n /dev/ttyUSB1
    # returns something like:
    # /devices/pci0000:00/0000:00:14.0/usb3/3-3/3-3.4/3-3.4.3/3-3.4.3.1/3-3.4.3.1:1.0/ttyUSB1/tty/ttyUSB1
    # This strings prefix 'til "ttyUSB" represents physical usb port on chassis or on usb hub.
    for dev_hook in glob.iglob("/dev/ttyUSB*"):
        config = match_configuration(get_udev_path(dev_hook), configurations)
        if config is not None:
            new_reader_thread = nmea0183_reader(dev_hook, config, repeat_message)
            new_writer_thread = nmea0183_writer(new_reader_thread.ser, config)
            new_writer_thread.start()            
            new_reader_thread.start()
            writer_threads.append(new_writer_thread)
            reader_threads.append(new_reader_thread)
        else:
            print("{}: WARN: No config found for {}".format(sys.argv[0], dev_hook), file=sys.stderr)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    for r_thread in reader_threads:
        r_thread.join()
    for w_thread in writer_threads:
        w_thread.join()

    print("{}: Normal exit.".format(sys.argv[0]), file=sys.stderr)

def repeat_message(name, message):
    global debug_flag
    if debug_flag:
        repeatLock.acquire()
        print("FROM {}: {}".format(name, message), end="")

    for writer in writer_threads:
        if writer.name != name:
            writer.send(message)

    if debug_flag:
        repeatLock.release()

def match_configuration(udev_path, configurations):
    for config in configurations["configurations"]:
        if udev_path.startswith(config["port_device_prefix"]):
            return config
    else:
        return None

def get_udev_path(dev_hook):
    result = subprocess.run(["udevadm", "info", "-q", "path", "-n", dev_hook], stdout=subprocess.PIPE)
    return result.stdout.decode('utf-8').strip()

def signal_handler(sig, frame):
    global continue_work

    if continue_work == False: # we already had a chance. on second signal we exit
        if sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
            print("{}: Forced exit.".format(sys.argv[0]), file=sys.stderr)
            sys.exit(0)

    if sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
        print("{}: Preparing for exit.. Signal again to force.".format(sys.argv[0]), file=sys.stderr)
        continue_work = False

class nmea0183_reader(threading.Thread):
    def __init__(self, port, config, callback):
        threading.Thread.__init__(self)
        self.port = port
        self.config = config
        self.callback = callback
        self.name = config["name"] + "_reader"
        self.ser = serial.Serial(self.port, int(self.config["port_speed"]), timeout=1)
        
    def run(self):        
        charbuf = bytearray()
        while continue_work:
            char = self.ser.read(size=1)
            if len(char) > 0:
                charbuf.extend(char)
                if char == b'\n':
                    line = charbuf.decode("iso-8859-1")
                    self.callback(self.config["name"], line)
                    charbuf.clear()

class nmea0183_writer(threading.Thread):
    def __init__(self, ser, config):
        threading.Thread.__init__(self)
        self.ser = ser
        self.config = config
        self.name = config["name"]
        self.write_queue = queue.Queue()
     
        if "accept_messages" in config:
            self.accept_messages = set(config["accept_messages"])
        else:
            self.accept_messages = None

        if "deny_messages" in config:
            self.deny_messages = set(config["deny_messages"])
        else:
            self.deny_messages = None
        

    def run(self):
        while continue_work:
            try:            
                message = self.write_queue.get(block=True, timeout=1)
                self.ser.write(bytes(message, "iso-8859-1"))
            except queue.Empty:
                pass
            

    def send(self, message):
        message_type = message[3:6]
        if self.accept_messages is not None:
            if message_type not in self.accept_messages:
                return
        if self.deny_messages is not None:
            if message_type in self.deny_messages:
                return

        # we drop messages when queue size builds up
        if self.write_queue.qsize() < MAX_WRITE_QUEUE_LEN:
            self.write_queue.put(message)
        else:
            if debug_flag:
                print("{}: ERR: Dropping messages to {}".format(sys.argv[0], self.name), file=sys.stderr)


if __name__ == "__main__":
    main()