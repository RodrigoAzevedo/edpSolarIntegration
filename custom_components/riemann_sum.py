class TrapezoidalRiemannSumMulti:
    def __init__(self):
        self.last_state = {}
        self.sums = {}

    def add_point(self, output_name, current_timestamp, current_value):
        if output_name in self.last_state:
            last_timestamp, last_value = self.last_state[output_name]
            delta_t = current_timestamp - last_timestamp
            if delta_t < 0:
                raise ValueError("Current timestamp must be >= last timestamp")
            area = 0.5 * (last_value + current_value) * delta_t
            self.sums[output_name] = self.sums.get(output_name, 0) + area
        else:
            self.sums[output_name] = self.sums.get(output_name, 0)
        self.last_state[output_name] = (current_timestamp, current_value)
        return self.sums[output_name]
