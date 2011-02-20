# libdcp - The Distributed Chat Protocol
"""
    The Distributed Chat Protocol

    This library provides a good basis for anyone inspired to write a tool
    for the DCP protocol.
"""

from gui import DeadGUI, TITLE_MODE_CENTERED
from mulsoc import SocketMultiplexer, ManagedSocket
from md import ManagedMDSocket, mdv2str, mdv2strerr
from events import DeferredCall, PeriodicCall
from log import PyLogger

