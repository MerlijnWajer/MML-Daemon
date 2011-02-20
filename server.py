#!/usr/bin/env python
import sys

from libmd import SocketMultiplexer, ManagedMDSocket, PeriodicCall, DeferredCall
from libmd.md import *


# Log levels
LVL_ALWAYS = 0          # Will always be shown.
LVL_NOTABLE = 42        # Notable information.
LVL_INFORMATIVE = 314   # Informative
LVL_VERBOSE = 666       # Verbose information. This should be everything except
                        # stuff like what variables contain.
LVL_PINGPONG = 1337
LVL_DEBUG = 9001        # What variables contain

#PING_RUN_PERIOD = 30.0                  # Time between ping rounds
PING_RUN_PERIOD = 10                    # Time between ping rounds
PING_TIMEOUT = 5.0                      # Ping response timeout

if PING_TIMEOUT >= PING_RUN_PERIOD:
    print 'err: PING_RUN_PERIOD <= PING_TIMEOUT'
    sys.exit(1)

class MDServer(SocketMultiplexer):

    def __init__(self):
        print 'MDServer init'    
        SocketMultiplexer.__init__(self, MDSocket)

        self.client2sock = {}

    def run(self, port):
        """

        """
        if not self.listen('', port, sock = MDServerListener):
            log.log([], LVL_ALWAYS, log.ERROR,
                'Couldn\'t start listening for clients')
            exit(1)

        # Initiate control server job
#        self.connect(controlIP, controlPort, sock = ControlServerJob)

#        # Initiate ping system
        self.ping_event = PeriodicCall(PING_RUN_PERIOD, self.doPings)
        self.eq.scheduleEvent(self.ping_event)

#        self.clientPort = port


        log.log([], LVL_ALWAYS, log.INFO,
            'Server up and running at %s:%i' %
            (self.listener.socketIP(), port))

        try:
            self.startMultiplex()
        except KeyboardInterrupt:
            return self.kill('Received SIGINT')

    def kill(self, reason):
        '''
            Stop the server.
        '''
        log.log([], LVL_ALWAYS, log.ERROR, 'Stopping: ' + reason)

       # Stop listening for clients
        if self.listener is not None:
            self.listener.close()
            self.listener = None

        # Full stop.
        sys.exit(0)
        return True

    def regClient(self, name, passwd, source_socket):
        print 'registerClient', name

        self.client2sock[name] = source_socket

    def delClient(self, name):
        print 'delClient', name

        if name in self.client2sock:
            del self.client2sock[name]
        else:
            print 'ERR: delClient called but client not in client2sock'
        # ELSE: Error

    def doPings(self):
        '''
            Executes periodic pings.
        '''
        print 'doPings'
        # Ping all inactive clients.
        cc = dict(self.client2sock)
        for k, c in cc.iteritems():
            if not c.pollRecvActivity():
                c.doPing()

        # Since we're running in a non-blocking environment
        # the latency received by serially calling the ping
        # methods should be trivial in the ping check delay
        # scheduled afterwards.
        self.eq.scheduleEvent(DeferredCall(PING_TIMEOUT, self.checkPings))

        return True

    def checkPings(self):
        '''
            Drop any irresponsive clients.
        '''
        print 'checkPings'
        # Check all clients
        cc = dict(self.client2sock)
        for k, c in cc.iteritems():
            c.checkPing()

class MDServerListener(ManagedMDSocket):
    def __init__(self, *args):
        ManagedMDSocket.__init__(self, *args)
        self.muxer.listener = self

class MDSocket(ManagedMDSocket):
    def __init__(self, muxer, ip, port):
        print 'MDSocket init'
        ManagedMDSocket.__init__(self, muxer, ip, port)

        self.regHandlers({
            MD_REG_CLIENT           :   self.onRegClient,
            MD_PONG                 : self.pong
        })

        self.pong_received = True

    def __del__(self):
        print 'MDSocket Del'
        ManagedMDSocket.__del__(self)

    def onConnect(self):
        print 'Connected'

    def onRegClient(self, name, passwd):
        print 'regClient'

        self.client_name, self.client_pass = name, passwd
        self.muxer.regClient(name, passwd, self)
        self.sendRegisterOk()

    def doPing(self):
        print 'doPing'
        self.pong_received = False
        self.sendPing('hai')

    def checkPing(self):
        if not self.pong_received:
            print 'no pong!'

            return self.manualViolation()

        return True

    def pong(self, _id):
        print 'Pong'
        # Possibly check pong message for _id? Must match our ping request, etc
        if self.pong_received:
            print 'Already received pong'
            return self.manualViolation()

        self.pong_received = True

    def onProtocolViolation(self, reason):
        '''
            Called on a protocol violation.
        '''
        print 'ProtocolViolation'
        self.drop(reason, True)


    def drop(self, reason, conn_alive):
        print 'Dropping'
        if hasattr(self, 'client_name'):
            print 'Dropping client:', self.client_name
            self.muxer.delClient(self.client_name)

        self.close()

    def onDisconnect(self):
        self.drop('onDisconnect', False)

    def onConnectionRefuse(self):
        self.drop('onConnectionRefuse', False)


# Execute only when run as standalone program
if __name__ == '__main__':
    from optparse import OptionParser
    parse = OptionParser()
    parse.add_option('-l', '--log', dest='log',
        help='File to log to. Default is stdout/stderr', default=None,
        type=str)
    parse.add_option('-q','--quiet-level',dest='quiet',
            help='Verbosity level. 0 is minimum and 10000 is max.',
            default=10000, type=int)

    opt = parse.parse_args()[0]

    from libmd import PyLogger
    log = PyLogger()

    # If no log is specified, then log info + warning to stdout and errors to
    # stderr. Else, log info + warning + error to the file
    if opt.log == None:
        log.assign_logfile(sys.stdout, opt.quiet, (PyLogger.WARNING,
                PyLogger.INFO))
        log.assign_logfile(sys.stderr, opt.quiet, (PyLogger.ERROR,))
    else:
        log.assign_logfile(opt.log, opt.quiet, (PyLogger.WARNING,
                PyLogger.INFO, PyLogger.ERROR), False)

    from socket import gethostbyname, error as se

    server = MDServer()
    server.run(2001)

