"""
    MD = Mufasa Daemon
"""

from struct import pack, unpack
from mulsoc import ManagedSocket

MD_REG_CLIENT           = 100 # Register request

MD_REGISTER_OK          = 110 # Register accepted
MD_REGISTER_FAIL        = 120 # Registed failed

MD_PING                 = 130 # Ping request
MD_PONG                 = 140 # Pong reply

MD_QUIT                 = 150 # Quit request / notification

class MDPackException(Exception):
    """
        This exception is thrown if the message passed to dcpPackMessage
        exceeds 196 chars. Or if the message contains invalid characters.
    """

class MDUnpackException(Exception):
    """
        This exception is thrown when a message passed to one of the unpacking
        facilities proves to be in an incorrect format.
    """

def mdPackMessage(type, message = ''):
    """
        Returns a packed MD message, ready for sending through a socket.
    """
    if len(message) > 196:
        raise MDPackException("Message exceeds 196 characters.")
    if mdValidateMessage(message) != MDV_NO_VIOLATION:
        raise MDPackException("Message contains non-printable characters.")
    header = pack('!HH', len(message) + 4, type)
    return header + message

def mdPackWords(*words):
    """
        Similar to the 'print' function but instead returns a string containing
        the words seperated by spaces.
    """
    packstr = ''
    for w in words[:-1]:
        packstr += str(w) + ' '
    return packstr + words[-1]

def mdValidateMessage(message):
    for i in message:
        if i not in md_charset:
            return MDV_INVALID_MESSAGE

    return MDV_NO_VIOLATION

def mdValidateMessageHeader(_type, length):
    """
        Validates the message header of a message that has not yet been
        completely received.
    """
    # FIXME: Implement
    return MDV_NO_VIOLATION

# List of protocol violations
MDV_NO_VIOLATION, \
MDV_MANUAL_VIOLATION, \
MDV_UNIMPLEMENTED, \
MDV_INVALID_MESSAGE = range(4)

# Build debug dictionary for converting numbers to their
# equivalent DCPV_*** string name
ldict = dict(locals())
mdv2str = {}
for i in ldict.iterkeys():
    if i[:5] == 'DCPV_':
        mdv2str[ldict[i]] = i
del ldict
del i

mdv2strerr = {
    MDV_NO_VIOLATION : "No violation",
    MDV_MANUAL_VIOLATION : " Manual Violation",
    MDV_UNIMPLEMENTED : "Unimplemented",
    MDV_INVALID_MESSAGE : "Invalid message"
}

# Build accepted character set
from string import letters, digits, punctuation, hexdigits
md_charset = letters + digits + punctuation + " \t\r\n"
md_name_charset = letters + digits + '_'
del letters, digits, punctuation
hexdigits = hexdigits[:16]

class ManagedMDSocket(ManagedSocket):

    def __init__(self, *argv):
        ManagedSocket.__init__(self, *argv)
        
        self.recv_activity = False

        self.stream = ''

        self.curtype = None
        self.curlen = None

        self.handlers = {
                MD_REG_CLIENT   : (self.handleSendMessage, self.onRegClient),
                MD_REGISTER_OK  : (self.handleRawArg, self.onRegisterOk),
                MD_REGISTER_FAIL: (self.handleRawArg, self.onRegisterFail),
                MD_PING         : (self.handleRawArg, self.onPing),
                MD_PONG         : (self.handleRawArg, self.onPong)
        }

    def onRecv(self, data):
        self.recv_activity = True
        self.stream += data
        while self.isConnected() and self.handleStream():
            pass

    def handleStream(self):
        """
            This function does the actual stream processing.
        """

        # An incomplete header is not quite interesting, wait for more
        if len(self.stream) < 4:
            return False

        if self.curtype is None:
            self.curlen, self.curtype = unpack('!HH', self.stream[:4])

            # This message header is rubbish, kill the connection.
            r = mdValidateMessageHeader(self.curtype, self.curlen)
            if r != MDV_NO_VIOLATION:
                return self.handleProtocolViolation(r)

        # Dispatch message
        if len(self.stream) >= self.curlen:
            _type, length = self.curtype, self.curlen
            self.curtype = self.curlen = None
            message = self.stream[4:length]
            self.stream = self.stream[length:]

            # Message conforms to protocol specifications?
            r = mdValidateMessage(message)
            if r != MDV_NO_VIOLATION:
                return self.handleProtocolViolation(r)

            if self.handlers.has_key(_type):
                dispatch, handler = self.handlers[_type]
                dispatch(message, handler)
            else:
                self.onUnknown(_type, message)

            return True

        return False


    def handleSendMessage(self, message, handler):
        """
            Internal Handler Dispatch function.

            This handler processes messages sent by clients to servers.
        """

        message = message.strip()
        if not len(message):
            return self.handleProtocolViolation(DCPV_INVALID_ARGUMENTS)
        target = message.split(None, 1)[0]
        message = message[len(target):].lstrip()

        handler(target, message)

    def handleRawArg(self, message, handler):
        """
            Internal Handler Dispatch function.

            This function executes protocol handlers that process their
            arguments directly and unmodified.
        """
        handler(message)

    def pollRecvActivity(self):
        """
            This method returns true if the socket received
            data since the last call to this method, if the
            method was never called before it returns True
            if the socket ever received anything.
        """

        x = self.recv_activity
        self.recv_activity = False
        return x

    def regHandlers(self, handlerDict):
        """
            Replaces handlers in the handler-library of ManagedDCPSocket
        """

        for key, newHandler in handlerDict.iteritems():
            self.handlers[key] = (self.handlers[key][0], newHandler)

    def onRegClient(self, name, passwd):
        """
            Called upon receiving a client registration request.
        """
        print 'Internal onRegClient called!'
        self.handleProtocolViolation(MDV_UNIMPLEMENTED)

    def onRegisterOk(self):
        '''
            Called when register was allowed (CLIENT)
        ''' 
        print 'Internal onRegisterOk called!'
        self.handleProtocolViolation(MDV_UNIMPLEMENTED)
        
    def onRegisterFail(self, reason):
        ''' 
            Called when the registration was not allowed (CLIENT)
        ''' 
        print 'Internal onRegisterFail called!'
        self.handleProtocolViolation(MDV_UNIMPLEMENTED)

    def onPing(self, string):
        """
            Called upon receiving a PING message

            Default implementation automatically replies with the appropriate
            PONG response.
        """
        self.sendPong(string)

    def onPong(self, string):
        """
            Called upon receiving a PONG message
        """
        self.handleProtocolViolation(MDV_UNIMPLEMENTED)

    def onProtocolViolation(self, reason):
        """
            Called upon violation of the DCP protocl by the peer. After the
            this method returns to the caller, the socket will automatically
            drop the connection to the peer.
        """
        print 'Protocol violation:', reason

    def onUnknown(self, _type, msg):
        print 'onUnknown:', type, 'mesg:', msg

    def sendRegister(self, name, pwd):
        """
            Register a client name + possible password
        """
        print 'sendRegister intern'
        msg = mdPackWords(name, pwd)
        self.send(mdPackMessage(MD_REG_CLIENT, msg))

    def sendRegisterOk(self):
        """
            Let client know the registration is succesful
        """
        self.send(mdPackMessage(MD_REGISTER_OK, ''))

    def sendPing(self, string):
        """
            Send a PING containing message 'string' to the peer.
        """
        self.send(mdPackMessage(MD_PING, string))

    def sendPong(self, string):
        """
            Send a PONG containing reply 'string' to the peer.
        """
        self.send(mdPackMessage(MD_PONG, string))

    def handleProtocolViolation(self, reason = None):
        """
            Handles a protocol violation.

            This function always returns False.
        """
        self.onProtocolViolation(reason)
        self.close()
        return False

    def manualViolation(self):
        """
            This method can be called by user code implementing the
            ManagedDCPSocket, to manually call for a protocol violation.
        """
        return self.handleProtocolViolation(MDV_MANUAL_VIOLATION)

