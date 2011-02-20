# Deadline event driven subsystems
"""
    Deadline Event Queue

    This file implements a simple time driven event system, that can be used
    to schedule delayed execution, periodic (cron) jobs in combination
    with non-blocking socket operations.
"""

from heapq import heappush, heappop, heapify

class DeadEvent(object):
    """
        Abstract event class, useful for implementing your own
        events
    """

    def __init__(self, delay):
        delay = float(delay)
        self.delay = delay
        self.odelay = delay

    def trigger(self, eq):
        """
Method called when event occurs.
        """
        pass

    def getDelay(self):
        return self.delay

    def elapseTime(self, time, eq):
        """
            Elapse 'time' units of time.
            If the event is triggered the function returns True
        """

        self.delay -= time
        if self.delay <= 0.001:
            self.trigger(eq)
            return True
        return False

    # Rich comparison interface for use in the heap queue
    def __lt__(self, other):
        return self.delay < other.delay

    def __le__(self, other):
        return self.delay <= other.delay

    def __eq__(self, other):
        return self.delay == other.delay

    def __ne__(self, other):
        return self.delay != other.delay

    def __gt__(self, other):
        return self.delay > other.delay

    def __ge__(self, other):
        return self.delay >= other.delay

# Some useful basic events
class DeferredCall(DeadEvent):
    """
        This event executes a function call after
        a given period of time.
    """

    def __init__(self, delay, call, *args, **kargs):
        DeadEvent.__init__(self, delay)
        self.call = call
        self.args = args
        self.kargs = kargs

    def trigger(self, eq):
        self.call(*self.args, **self.kargs)

class PeriodicCall(DeferredCall):
    """
        This event keeps calling a specified call at the given
        frequency as long as the call keeps returning True.
    """

    def trigger(self, eq):
        if self.call(*self.args, **self.kargs):
            self.delay = self.odelay
            eq.scheduleEvent(self)

class DeadEventQueue(object):
    def __init__(self):
        self.events = []
        self.isheap = True
        self.elapsing = False

    def scheduleEvent(self, event):
        """
            Schedule an event for execution.
        """

        # Since it is possible for events to be scheduled
        # during the execution of elapseTime (which would ruin our queue)
        # (Events can re-schedule themselves upon triggering)
        # we defer those calls until the elapse call is complete
        if self.elapsing:
            self.scheds.append(event)
            return

        # Guarantee heap structure
        if not self.isheap:
            heapify(self.events)
            self.isheap = True

        heappush(self.events, event)

    def cancelEvent(self, ev):
        """
            Cancel a scheduled event.

            Cancelling is an expensive operation since it breaks the heap
            structure, it is therefore recommended to group inserts together
            and keep them separate from cancels which should also be grouped.
            This way the penalty for cancellation is minimized.
        """

        # Same goes for events being cancelled
        if self.elapsing:
            self.cancels.append(ev)
        else:
            if ev in self.events:
                self.events.remove(ev)
                self.isheap = len(self.events) == 0

    def elapseTime(self, time):
        """
            "Atomically" elapse the time of events in the queue
        """

        self.scheds = []
        self.cancels = []

        # Guarantee heap structure
        if not self.isheap:
            heapify(self.events)
            self.isheap = True

        # Do elapse
        self.elapsing = True
        while len(self.events) and self.events[0].elapseTime(time, self):
            heappop(self.events)
        for ev in self.events[1:]: ev.elapseTime(time, self)
        self.elapsing = False
 
        # Insert events scheduled in the mean time
        for ev in self.scheds: self.scheduleEvent(ev)
        for ev in self.cancels: self.cancelEvent(ev)

        del self.cancels
        del self.scheds

    def nextEventTicks(self):
        """
            Returns the ticks remaining till the next event.
        """

        # Guarantee heap structure
        if not self.isheap:
            heapify(self.events)
            self.isheap = True

        if len(self.events):
            return self.events[0].getDelay()

        return None

