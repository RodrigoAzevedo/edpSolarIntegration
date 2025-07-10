import logging

_LOGGER = logging.getLogger(__name__)
class TrapezoidalRiemannSum:
    def __init__(self):
        self.last_timestamp = None
        self.last_value = None
        self.sum = 0.0

    def add_point(self, timestamp, value):
        """
        Feed a new point (timestamp, value) to the Riemann sum calculator.
        The first point initializes the state; area is only calculated from the second point onward.
        Returns the current sum.
        """
        if self.last_timestamp is not None and self.last_value is not None:
            delta_t = timestamp - self.last_timestamp
            area = 0.5 * (self.last_value + value) * delta_t
            self.sum += area
        # Update last point for next call
        self.last_timestamp = timestamp
        self.last_value = value
        return self.sum

    def get_sum(self):
        """
        Return the current Riemann sum.
        """
        return self.sum

    def reset(self):
        """
        Reset the Riemann sum calculator to its initial state.
        """
        self.last_timestamp = None
        self.last_value = None
        self.sum = 0.0

    def get_last_point(self):
        """
        Return the last timestamp and value as a tuple.
        """
        return (self.last_timestamp, self.last_value) 