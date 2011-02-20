

import os

from libmd import *
import sys

# Fetch the system locale settings, so ncurses can do its job correctly
# UTF8 strings to be precise
# For more info see: http://docs.python.org/3.1/library/curses.html
import locale
locale.setlocale(locale.LC_ALL, '')

from socket import gethostbyname, error as se
from libmd.md import *

def addNotice(s):
    gui.stdscr.touchwin()
    gui.getMainWindow().addNotice(s)
    gui.redrawFromScratch()
    gui.stdscr.refresh()

def addIncoming(s):
    gui.stdscr.touchwin()
    gui.getMainWindow().addIncoming(s)
    gui.redrawFromScratch()
    gui.stdscr.refresh()

def addOutgoing(s):
    gui.stdscr.touchwin()
    gui.getMainWindow().addOutgoing(s)
    gui.redrawFromScratch()
    gui.stdscr.refresh()

class MDGUI(SocketMultiplexer):

    def __init__(self):
        SocketMultiplexer.__init__(self)
        self.addReader(StandardInput())

        self.connecting, self.cli_connected = False, False
        self.client = None


    def run(self):
        gui.registerCommand('quit', self.quitCall)
        gui.registerCommand('connect', self.connectCall)

        # Initialise main window
        mainwin = gui.getMainWindow()
        mainwin.setTitle("Deadline v0.1")
        mainwin.setTitleAlignment(TITLE_MODE_CENTERED)
        gui.show()
        addNotice("Welcome to MDGUI v0.1")
        addNotice("You can type '/quit' to quit," +
            " or type something else to simply see it" +
            " show up in this window :-)")
        self.startMultiplex()

    def onSignal(self):
        """
            Handles SIGWINCH for terminal resizing.
        """
        gui.stdscr.touchwin()
        while gui.inputEvent():
            pass

    def quitCall(self, quit_msg = None):
        print 'Quitting'
#        if self.client is not None:
#            if quit_msg is None:
#                self.client.sendQuit('Goooooood niiiiiiiiight ding ding ding')
#            else:
#                self.client.sendQuit(quit_msg)

        self.stopMultiplex()

    def connectCall(self, server):
        if self.cli_connected:
            addNotice('We are already connected')
            return

        s = server.split(' ')
        if len(s) != 2:
            addNotice('Invalid format. Correct format: <host> <port>')
            return False

        host, port = s
        try:
            ip = gethostbyname(host)
        except se, e:
            addNotice('Could not resolve:' + host)
            return False

        try:
            port = int(port)
        except ValueError:
            addNotice('Could not turn port into an int...')
            return False

        self.connecting = True
        self.connect(ip, port, sock=ClientSocket)
        addOutgoing('Connecting to %s' % repr(server))

    def onNoExecute(self, str):
        self.onMsg('#all %s' % str)

        # Return false so the message is not printed.
        return False


class StandardInput(object):
    """
        Standard input handler for SocketMultiplexer
    """

    def fileno(self):
        return sys.stdin.fileno()

    def handleRead(self):
        while gui.inputEvent():
            pass


class ClientSocket(ManagedMDSocket):

    def __init__(self, mux, ip, port):
        ManagedMDSocket.__init__(self, mux, ip, port)

        addNotice('Initialised the socket!')
        self.muxer.client = self

    def onConnect(self):
        addNotice('Connected.')

        self.regHandlers({
            MD_REGISTER_OK      :   self.registerOk,
            MD_REGISTER_FAIL    :   self.registerFail
        })

        addNotice('Calling sendRegister')
        self.sendRegister('MMLD Client', 'Secr0t')
        addNotice('Called sendRegister')

    def registerOk(self, params = None):
        addIncoming('registerOk')

    def registerFail(self, reason):
        pass


gui = DeadGUI()
app = MDGUI()

gui.onNoExecute = app.onNoExecute

try:
    app.run()
finally:
    gui.hide()

print 'Bye!'
