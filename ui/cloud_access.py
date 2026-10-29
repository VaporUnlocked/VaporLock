"""Local Cloud protocol compatibility, with no license or service dependency."""
from cloud_contract import CloudInputError

def edition():
    # Stable private-pipe namespace shared with existing native engines.
    return 'premium'

def pipe_name(name, *, build=None):
    import re
    if build is not None and build != edition():
        raise CloudInputError('Conflicting .cloud protocol identity.')
    if type(name) is not str or re.fullmatch(r'VaporLock\.Cloud\.premium\.[0-9a-f]{32}',name) is None:
        raise CloudInputError('Invalid private .cloud pipe name.')
    return name
