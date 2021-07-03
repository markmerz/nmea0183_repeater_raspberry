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
import select
import socket

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

    for config in configurations["configurations"]:
        if "network_type" in config and config["network_type"] == "tcp_server":
            new_tcp_server_thread = nmea0183_tcp_server(config, repeat_message)
            new_tcp_server_thread.start()
            writer_threads.append(new_tcp_server_thread)

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
        if "port_device_prefix" in config and udev_path.startswith(config["port_device_prefix"]):
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
            char = self.ser.read(size=1) # blocks until timeout is reached. Timeout is defined when creating serial.Serial object.
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

class nmea0183_tcp_server(threading.Thread):
    def __init__(self, config, callback):
        threading.Thread.__init__(self)
        
        self.config = config
        self.callback = callback
        self.name = config["name"]
        self.write_queue = queue.Queue()
        tcpport = int(config["network_port"])
        
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.setblocking(0)        
        self.server.bind(('0.0.0.0', tcpport))
        self.server.listen(5)
        self.inputs = [self.server]
        self.outputs = []
        self.message_queues = {}
        self.line_buffers = {}

        if "accept_messages" in config:
            self.accept_messages = set(config["accept_messages"])
        else:
            self.accept_messages = None

        if "deny_messages" in config:
            self.deny_messages = set(config["deny_messages"])
        else:
            self.deny_messages = None

    def run(self):
        while self.inputs and continue_work:
            readable, writable, exceptional = select.select(self.inputs, self.outputs, self.inputs, 0.3)
            for s in readable:
                if s is self.server:
                    connection, client_address = s.accept()
                    connection.setblocking(0)
                    self.inputs.append(connection)
                    self.message_queues[connection] = queue.Queue()
                else:                    
                    data = s.recv(1024)
                    if data:
                        if s not in self.line_buffers:
                            self.line_buffers[s] = bytearray()
                        
                        for char in [bytes([b]) for b in data]:
                            self.line_buffers[s].extend(char)
                            if char == b'\n':                                
                                line = self.line_buffers[s].decode("iso-8859-1")
                                self.line_buffers[s].clear()

                                # send to serials
                                self.callback(self.config["name"], line)

                                # send to other tcp clients connected to this port
                                # we need to process it here as regular send() framework
                                # does not know about sockets and we would get a local echo
                                repeat_message = True
                                message_type = line[3:6]
                                if self.accept_messages is not None:
                                    if message_type not in self.accept_messages:
                                        repeat_message = False
                                if self.deny_messages is not None:
                                    if message_type in self.deny_messages:
                                        repeat_message = False
                                if repeat_message:                                                                
                                    for mq_key in self.message_queues:
                                        if mq_key != s:
                                            self.message_queues[mq_key].put(bytes(line, "iso-8859-1"))
                                            if mq_key not in self.outputs:
                                                self.outputs.append(mq_key)                                                        
                    else:
                        if s in self.outputs:
                            self.outputs.remove(s)
                        self.inputs.remove(s)
                        if s in self.line_buffers:
                            del self.line_buffers[s]
                        s.close()
                        del self.message_queues[s]                                                

            for s in writable:
                try:
                    next_msg = self.message_queues[s].get_nowait()
                except queue.Empty:
                    self.outputs.remove(s)
                else:
                    s.send(next_msg)

            for s in exceptional:
                self.inputs.remove(s)
                if s in self.outputs:
                    self.outputs.remove(s)
                if s in self.line_buffers:
                    del self.line_buffers[s]
                s.close()
                del self.message_queues[s]

    def send(self, message):
        message_type = message[3:6]
        if self.accept_messages is not None:
            if message_type not in self.accept_messages:
                return
        if self.deny_messages is not None:
            if message_type in self.deny_messages:
                return
        for mq_key in self.message_queues:                
            self.message_queues[mq_key].put(bytes(message, "iso-8859-1"))
            if mq_key not in self.outputs:
                self.outputs.append(mq_key)
            
if __name__ == "__main__":
    main()