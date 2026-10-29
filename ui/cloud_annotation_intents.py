"""Monotonic parent publication revocation; no worker can renew an intent."""


class PublicationLatch:
    def __init__(self):self.generation=0;self.closed=False
    def capture(self):return self.generation
    def accepts(self,generation):return not self.closed and type(generation) is int and generation==self.generation
    def revoke(self):self.generation+=1
    def close(self):self.revoke();self.closed=True
