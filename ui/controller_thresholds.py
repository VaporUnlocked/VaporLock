"""Trigger hysteresis contract shared by settings and the input tester."""
def validate_thresholds(press, release):
    # Raw readings are 0..255. Zero is deliberately excluded as a threshold:
    # press=0 arms at rest and release=0 can never release under '< release'.
    if type(press) is not int or type(release) is not int or not 1 <= release <= press <= 255:
        raise ValueError('Use thresholds from 1 to 255, with release no higher than press.')
    return press, release


class TriggerLatch:
    def __init__(self):
        self.identity = None
        self.down = False

    def sample(self, value, press=30, release=30, identity=None):
        validate_thresholds(press, release)
        key = (identity, press, release)
        if value is None:
            self.identity, self.down = None, False
            return False
        if key != self.identity:
            self.identity, self.down = key, False
        self.down = value >= (release if self.down else press)
        return self.down
