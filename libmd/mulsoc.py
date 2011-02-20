# mulsoc.py - Socket Multiplexer
"""
    The non-blocking sockets library

    This library provides a straight forward implementation of non-blocking
    sockets, designed to be used in an event driven application.
"""

import socket
from select import select, error as select_error
from time import time
import errno
from events import DeadEventQueue, DeferredCall
from struct import calcsize, pack, unpack
from fcntl import ioctl
from array import array

# IO Control constants
# These constants where taken from
# <bits/ioctls.h> and <net/if.h>
SIOCGIFNAME = 0x8910
SIOCGIFCONF = 0x8912
SIOCGIFADDR = 0x8915
IFNAMSIZ = 16

# ioctl communication structures

# NOTE: To make the sizes of the structures compatible with C every size is
# rounded up to the next multiple of 4 when needed.
size2C = lambda n: (n + 3) & -4

class CSockaddr(object):
    """
        Python version of <bits/socket.h>::struct sockaddr
        and <netinet/in.h>::struct sockaddr_in

        used for extracting IPv4 addresses from IOCtl calls.
    """

    _size = calcsize('H14s')
    _csize = size2C(_size)

    def __init__(self):
        self.sa_family = 0x0
        self.sa_data = '\0' * 14

    def calcsize(self):
        return self._csize

    def toPack(self):
        return pack('H14s', self.sa_family, self.sa_data)

    def fromPack(self, pack):
        # FIXME: It might be more proper to
        # throw an exception if the passed pack is not
        # of size self._csize

        pack = pack[:self._size]
        self.sa_family, self.sa_data = unpack('H14s', pack)

    def fromString(self, ipv4addr):
        """
            Initialise sockaddr from IPv4 adress only.
        """

        self.sa_data = socket.inet.aton(ipv4addr).ljust(14, '\0')
        self.sa_family = socket.AF_INET

    def toString(self):
        # We skip the first 2 bytes since they are a
        # uint16_t presenting a port
        return socket.inet_ntoa(self.sa_data[2:6])

# The size of this structure is critical to the size difference of ifreq on
# 64-bit and 32-bit, therefore we need to know the size
# See struct ifmap in <net/if.h>
IFMAPSIZE = size2C(calcsize('LLHccc'))

class CInterfaceRequest(object):
    """
        Python version of <net/if.h>::struct ifreq

        Used for enumerating network interfaces and querying
        IPv4 adresses.
    """

    _size = _csize = IFNAMSIZ + IFMAPSIZE
    _int_size = calcsize('i')

    def __init__(self):
        self.name = ''

        # The following members are all in a union
        # (start at the same logical address)
        self.addr = CSockaddr()
        self.index = 0

    def calcsize(self):
        return self._size

    def fromPack(self, pack):
        name = pack[:IFNAMSIZ]
        pack = pack[IFNAMSIZ:]
        x = name.find('\0')

        if x == -1:
            self.name = ''
        else:
            self.name = name[:x]

        self.addr.fromPack(pack[:self.addr.calcsize()])
        self.index = unpack('i', pack[:self._int_size])

    def toPack(self):
        pack = self.name.ljust(IFNAMSIZ, '\0')
        pack += self.addr.toPack()
        return pack.ljust(self._csize, '\0')

    def lock(self):
        """
            Lock interface structure and return pointer to structure.
        """

        self.lock_buf = array('B', self.toPack())
        return self.lock_buf.buffer_info()[0]

    def unlock(self):
        """
            Unlock structure and update any changes made to the structure data.
        """

        self.fromPack(self.lock_buf.tostring())
        del self.lock_buf

class CInterfaceConfig(object):
    """
        Python version of <net/if.h>::struct ifconf

        Used for enumerating network interfaces. This
        structure is passed to an SIOCGIFCONF ioctl call
    """

    _size = calcsize('iP')
    _csize = size2C(_size)

    def __init__(self):
        self.buf_len = 0x0
        self.buf_ptr = 0x0

    def fromPack(self, pack):
        pack = pack[:self._size]
        self.buf_len, self.buf_ptr = unpack('iP', pack)

    def toPack(self):
        return pack('iP', self.buf_len, self.buf_ptr).ljust(self._csize, '\0')

    def lock(self):
        """
            Locks the structure buffer and returns a pointer
            to be used to manipulate the structure.
        """

        self.lock_buf = array('B', self.toPack())
        return self.lock_buf.buffer_info()[0]

    def unlock(self):
        """
            Unlock the structure buffer, and update any members changed
            during the lock.
        """

        x = self.lock_buf.tostring()
        del self.lock_buf
        self.fromPack(x)

del size2C

class SocketMultiplexer(object):
    """
        Abstract socket multiplexer, useful for managing lots of sockets
        with a single thread.
        Inherit to create a multiplexed socket based application.
        You should only override the on***() callback methods.
    """

    # Setup optimised listen queue default
    # The maximum should be 128 by default under linux 2.0 and up
    # To check do a 'cat /proc/sys/net/core/somaxconn'
    LISTEN_QUEUE_MAXIMUM = socket.SOMAXCONN
    LISTEN_QUEUE_DEFAULT = min(16, socket.SOMAXCONN)

    class Deadlock(Exception):
        """
            This class represents the occurrence of a deadlock in the event
            processing system. (It would wait forever on nothing)
        """

    def __init__(self, sock = None):
        """
            Initialise a base SocketMultiplexer that uses 'sock' as its
            ManagedSocket instantiator.
        """

        self._keep_running = False
        if sock is None:
            sock = ManagedSocket
        self._sock = sock
        self.eq = DeadEventQueue()
        self._alarm = None
        self._reads, self._writes = [], []

    def startMultiplex(self):
        """
            Begin multiplexing non-blocking sockets.
            This call does not return, until either a deadlock exception occurs
            or stopMultiplex is called.
        """

        self._keep_running = True
        tick = None

        try:
            while self._keep_running:
                try:

                    # Handle the events system
                    if self.eq.nextEventTicks() is None:
                        tick = None
                    elif tick is None:
                        tick = time()
                    else:
                        newtick = time()
                        if newtick - tick > 0.0:
                            self.eq.elapseTime(newtick - tick)
                        tick = newtick
                        del newtick

                    # Guard against activity deadlocks
                    # They really shouldn't occur, but it is good practice to
                    # catch them.
                    if len(self._reads) + len(self._writes) == 0 and \
                            self.eq.nextEventTicks() is None:
                        raise SocketMultiplexer.Deadlock("No events left")

                    # Wait for activity
                    reads, writes, excepts = \
                        select(self._reads, self._writes, [],
                            self.eq.nextEventTicks())

                    # Handle the events system
                    # I know this isn't the nicest solution, but
                    # this is required to fix a nasty bug triggering over
                    # execution.
                    if self.eq.nextEventTicks() is None:
                        tick = None
                    elif tick is None:
                        tick = time()
                    else:
                        newtick = time()
                        if newtick - tick > 0.0:
                            self.eq.elapseTime(newtick - tick)
                        tick = newtick
                        del newtick

                except select_error, e:
                    if e.args[0] == errno.EINTR:
                        self.onSignal()
                        continue
                    raise e

                # Handle reads and writes
                for r in reads: r.handleRead()
                for w in writes: w.handleWrite()
        finally:
            self._keep_running = False

        return True

    def timeFlow(self):
        """
            Executes the flow of time.

            This function will be used in the future to
            prevent clock jumps and streamline the events system.
        """

    def stopMultiplex(self):
        """
            Stop multiplexing.
        """
        if not self._keep_running:
            return False
        self._keep_running = False
        return True

    def connect(self, ip, port, **keywords):
        """
            Initiate a client connection to the specified server.

            Additionally you can specify 'sock = <some class' in the
            function call to override the default socket instantiator.
            Any additional keywords shall be passed on to
            the socket constructor.
        """
        try:
            sock = keywords['sock']
            del keywords['sock']
        except KeyError:
            sock = self._sock
        new = sock(self, ip, port, **keywords)
        new.connect()
        return True

    def listen(self, ip, port,
            queue_length = None, **keywords):
        """
            Create a new socket that will start listening on
            the specified address.

            Additionally you can specify 'sock = <some class' in the
            function call to override the default socket instantiator.
            Any additional keywords shall be passed on to
            the socket constructor.
        """
        if queue_length == None:
            queue_length = SocketMultiplexer.LISTEN_QUEUE_DEFAULT
        try:
            sock = keywords['sock']
            del keywords['sock']
        except KeyError:
            sock = self._sock
        new = sock(self, ip, port, **keywords)
        if not new.listen(queue_length):
            return False
        return True

    def addReader(self, sock):
        """
            Add socket to the list of sockets watched for reading
        """
        if sock in self._reads:
            return False
        self._reads.append(sock)
        return True

    def delReader(self, sock):
        """
            Delete socket from the list of sockets watched for reading
        """
        try:
            self._reads.remove(sock)
        except ValueError:
            return False
        return True

    def addWriter(self, sock):
        """
            Add socket to the list of sockets watched for writing
        """
        if sock in self._writes:
            return False
        self._writes.append(sock)
        return True

    def delWriter(self, sock):
        """
            Delete socket from the list of sockets watched for writing
        """
        try:
            self._writes.remove(sock)
        except ValueError:
            return False
        return True

    def setAlarm(self, seconds):
        """
            Sets an alarm that will occur in 'seconds' time, seconds may be
            fractional. If seconds is None any pending alarm will be cancelled
        """

        if self._alarm is not None:
            self.eq.cancelEvent(self._alarm)
        if seconds is not None:
            self._alarm = DeferredCall(seconds, self.execAlarm)
            self.eq.scheduleEvent(self._alarm)
        return True

    def execAlarm(self):
        """
            Handler that executes the onAlarm() method.
        """
        self._alarm = None
        self.onAlarm()

    def onAlarm(self):
        """
            Called when the alarm set by setAlarm() occurs
        """

    def onSignal(self):
        """
            Called when select() is interrupted by a signal.
        """

class ManagedSocket(object):
    """
        This class represents a managed non-blocking socket.
        Inherit this class for managing connection specific states the easy way.
        You should only override the on***() callback methods.
    """

    WATCH_READ, WATCH_WRITE = [1, 2]
    UNBOUND, CONNECTING, CONNECTED, DISCONNECTED, LISTENING, CLOSED = range(6)

    def __init__(self, muxer, ip, port):
        """
            Instantiate an abstract managed socket.

            This method can be called in 2 ways:
                the expected way, a muxer, an ip, and a port, or

                the unexpected way, re-using an already connected socket
                (that has been obtained through accepting).
                In this case 'port' should contain a tuple describing
                the peer's address (ip, port).
                'ip' should contain a tuple containing only the socket object.
        """

        if type(ip) is tuple:
            self._sock = ip[0]
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            # To prevent could not start listening bug?
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            self._ip =  self._port = None
            self._peer_ip, self._peer_port = port
            self._state = ManagedSocket.CONNECTED
            muxer.addReader(self)
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._ip = ip
            self._port = port
            self._peer_ip =  self._peer_port = None
            self._state = ManagedSocket.UNBOUND

        # Setup common states
        self._sock.setblocking(0)
        self.muxer = muxer
        self._wbuf = ''

        # Last write blocked flag, used for speeding up non-blocking writes
        self._lwb = False

    def fileno(self):
        """
            Return this socket's file descriptor for waiting.
        """
        return self._sock.fileno()

    def listen(self, queue_length):
        """
            Start listening for clients.
        """

        if self._state != ManagedSocket.UNBOUND:
            return False
        try:
            # For now listen even if address is in use
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self._ip, self._port))
        except socket.error, e:
            error = e.args[0]
            if error != errno.EADDRINUSE and error != errno.EACCES:
                raise e
            return False
        self._state = ManagedSocket.DISCONNECTED
        self._sock.listen(queue_length)
        self.muxer.addReader(self)
        self._state = ManagedSocket.LISTENING
        return True

    def connect(self):
        """
            Start connecting to client.
        """

        if self._state != ManagedSocket.UNBOUND:
            return False
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self._peer_ip, self._listening_port = self._ip, self._port
        self.handleConnect()

        return self._state != ManagedSocket.DISCONNECTED

    def handleConnect(self):
        """
            Function negotiating a non-blocked connect, this is for internal
            use only.
        """

        if self._state == ManagedSocket.CONNECTED:
            return False

        if self._state != ManagedSocket.CONNECTING:
            self._state = ManagedSocket.CONNECTING
            self.muxer.addWriter(self)

        try:
            self._sock.connect((self._ip, self._port))
            self._state = ManagedSocket.CONNECTED
            self._ip, self._port = self._sock.getsockname()
            self._peer_ip, self._peer_port = self._sock.getpeername()
            self.onConnect()
            self.muxer.addReader(self)
            self.muxer.delWriter(self)
        except socket.error, e:
            error = e.args[0]
            if error in (errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ECONNRESET):
                self._state = ManagedSocket.DISCONNECTED
                self.muxer.delWriter(self)
                self.onConnectionRefuse()
                return False
            elif error == errno.EAGAIN:
                return False
            elif error != errno.EINPROGRESS:
                raise e
        return True

    def handleRead(self):
        """
            Internal function that mediates non-blocking reads.
        """

        # Read data
        data = ''
        d = ''
        if self._state == ManagedSocket.CONNECTED:
            try:
                while self._state == ManagedSocket.CONNECTED:
                    d = self._sock.recv(4096)
                    if d == '':
                        break
                    data += d
            except socket.error, e:
                error = e.args[0]
                if error == errno.ECONNRESET or error == errno.ETIMEDOUT:
                    self.muxer.delReader(self)
                    if self._lwb:
                        self.muxer.delWriter(self)
                    self._state = ManagedSocket.DISCONNECTED
                    self.onDisconnect()
                elif error != errno.EWOULDBLOCK and error != errno.EINTR:
                    raise e

            if data != '':
                self.onRecv(data)

            # Connection lost or shutdown
            if d =='' and self._state == ManagedSocket.CONNECTED:
                self.muxer.delReader(self)
                if self._lwb:
                    self.muxer.delWriter(self)
                self._state = ManagedSocket.DISCONNECTED
                self.onDisconnect()
            return True

        # Accept a new client
        elif self._state == ManagedSocket.LISTENING:
            try:
                while self._state == ManagedSocket.LISTENING:
                    conn, addr = self._sock.accept()
                    self.onAccept(self.muxer._sock(self.muxer, (conn,), addr))
            except socket.error, e:
                error = e.args[0]
                if error not in (errno.EWOULDBLOCK, errno.EINTR, errno.EMFILE):
                    raise e

            return True

        return False

    def handleWrite(self):
        """
            Internal function that mediates non-blocking writes.
        """

        if self._state == ManagedSocket.CONNECTING:
            self.handleConnect()
            return True
        elif self._state != ManagedSocket.CONNECTED:
            return False

        while len(self._wbuf) and self._state == ManagedSocket.CONNECTED:
            try:
                x = self._sock.send(self._wbuf[:4096])
                self._wbuf = self._wbuf[x:]
            except socket.error, e:
                error = e.args[0]

                # Connection lost
                if error == errno.EPIPE or error == errno.ECONNRESET \
                    or error == errno.ETIMEDOUT:
                    self._state = ManagedSocket.DISCONNECTED
                    self.muxer.delReader(self)
                    if self._lwb:
                        self.muxer.delWriter(self)
                    self.onDisconnect()
                    self._wbuf = ''
                    return False
                elif error == errno.EWOULDBLOCK:
                    if not self._lwb:
                        self._lwb = True
                        self.muxer.addWriter(self)
                elif error != errno.EINTR:
                    raise e
                break

        if not len(self._wbuf) and self._lwb:
            self.muxer.delWriter(self)

        return True

    def send(self, data):
        """
            Place data in the output buffer for sending. All data will be
            sent ASAP to the peer socket.
        """

        if self._state != ManagedSocket.CONNECTED:
            return False
        self._wbuf += data
        self.handleWrite()
        return self._state == ManagedSocket.CONNECTED

    def close(self):
        """
            Close socket, you should delete this socket after closing, it will
            not be of any more worth.
        """

        if self._state == ManagedSocket.CLOSED:
            return False

        if self._state == ManagedSocket.CONNECTED:

            # There are some rare conditions in which our socket has become
            # disconnected before executing this call
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except socket.error, e:
                error = e.args[0]
                if error != errno.ENOTCONN:
                    raise e

            self.muxer.delReader(self)
            if self._lwb:
                self.muxer.delWriter(self)
        elif self._state == ManagedSocket.CONNECTING:
            self.muxer.delWriter(self)
        elif self._state == ManagedSocket.LISTENING:
            self.muxer.delReader(self)

        self._sock.close()
        self._sock = None
        self._state = ManagedSocket.CLOSED
        return True

    # The following IP and port detection code was heavily based upon
    # the following 2 activestate recipes:
    """
http://code.activestate.com/recipes/439094-get-the-ip-address-associated-with-a-network-inter/

http://code.activestate.com/recipes/439093-get-names-of-all-up-network-interfaces-linux-only/
    """
    # A lot of info also came from <net/if.h> and <ioctl.h>

    # NOTE: This code does NOT support IPv6, which luckily for us isn't a
    # problem since the DCP server ident format does not even support the : used
    # in IPv6 formats.

    # Socket info services
    def socketInterfaces(self):
        """
            Returns a list of strings representing the working
            interfaces in the system. (Interfaces that are down are not
            included in this list)
        """

        if self.isClosed():
            return []

        # This function is currenly implemented to receive a maximum of
        # 8 interfaces from the kernel. As I have yet to meet a machine
        # with such an amount of interfaces I think it is fair to assume
        # this code will work on any average machine.

        # To Be Interfaces
        ifconf = CInterfaceConfig()
        tbi = [CInterfaceRequest() for i in range(8)]
        ifreqbuf = array('B', '\0' * (tbi[0].calcsize() * 8))

        # Setup kernel call
        ifconf.buf_len = ifreqbuf.buffer_info()[1]
        ifconf.buf_ptr = ifreqbuf.buffer_info()[0]

        # Query interfaces
        fno = self.fileno()
        loq = ifconf.lock()
        ioctl(fno, SIOCGIFCONF, loq)

        # Update data from kernel
        ifconf.unlock()

        # buf_len has been set by the kernel
        # to the size of the structures that
        # were successfully filled.
        sdata = ifreqbuf.tostring()[:ifconf.buf_len]

        # Read interfaces from query
        ifaces = []
        for iface in tbi:
            if len(sdata) >= iface.calcsize():
                newface = sdata[:iface.calcsize()]
                sdata = sdata[iface.calcsize():]
                iface.fromPack(newface)
                ifaces.append(iface.name)
            else:
                break

        return ifaces

    def socketInterface(self):
        """
            Returns the string of a non-loopback interface when available,
            otherwise returns either 'lo' or None if no network interfaces
            are available.
        """

        if self.isClosed():
            return None

        # This code is currently not bridge-resistant, it does not look
        # for eth* or wlan* interfaces, so it might be fooled by an
        # unusual network setup.

        ifaces = self.socketInterfaces()
        if ifaces:
            if len(ifaces) == 1:
                return ifaces[0]
            else:
                for x in ifaces:
                    if x != 'lo':
                        return x
        return None

    def socketInterfaceIP(self, name):
        """
            Returns the IPv4 adress in string format of the given interface
            'name' if available, otherwise returns None
        """

        if self.isClosed():
            return None

        ifreq = CInterfaceRequest()
        ifreq.name = name

        # Do kernel call
        # This might throw an exception if an invalid
        # interface name is passed.
        fno = self.fileno()
        loq = ifreq.lock()
        ioctl(fno, SIOCGIFADDR, loq)

        # Update data from kernel
        ifreq.unlock()

        return ifreq.addr.toString()

    def socketIP(self):
        """
            This returns the IP of the interface this socket is bound to,
            or None if the socket is not bound.
            This method applies to listening sockets and connected sockets.
        """

        if not self.isBound():
            return None

        if self.isListening() and self._ip == '':
            iface = self.socketInterface()
            # FIXME: This caching behaviour might introduce a
            # a bug if the interface IP is changed during
            # the application lifetime.
            self._ip = self.socketInterfaceIP(iface)

        return self._ip

    def socketPort(self):
        """
            This returns the port this socket is bound to.
            This method applies to listening sockets and connected sockets.
        """

        if not self.isBound():
            return None

        return self._port

    def peerIP(self):
        """
            This returns the IP of the peer this socket last was or is connected
            to, or None if the socket never was connected.
        """
        return self._peer_ip

    def peerPort(self):
        """
            This method does the same thing but now for port numbers.
        """
        return self._peer_port

    def peerListeningPort(self):
        """
            This method returns the port used in a connect() call,
            needless to say this method only returns something useful
            when the socket is obtained through a connect(), otherwise
            returns None.
        """
        return self._listening_port

    def bytesInSendQueue(self):
        return len(self._wbuf)

    # Various functions for determining the state of the socket
    def isConnected(self):
        return self._state == ManagedSocket.CONNECTED

    def isListening(self):
        return self._state == ManagedSocket.LISTENING

    def isClosed(self):
        return self._state == ManagedSocket.CLOSED

    def isBound(self):
        return self._state != ManagedSocket.UNBOUND

    # Callbacks
    def onRecv(self, data):
        """
            This callback is called on incoming data.
        """

    def onDisconnect(self):
        """
            Called when the connection is lost or when the other socket closes.
        """

    def onConnectionRefuse(self):
        """
            Called when a pending connection is refused by the server.
        """

    def onConnect(self):
        """
            Called when successfully connected to a server.
        """

    def onAccept(self, sock):
        """
            Called whenever a new client is accepted on a socket that was
            listening. 'sock' is the accepted socket.
        """

