"""Training-job-scoped exact input preparation; no recipe or model changes."""
from contextlib import contextmanager
import threading

_LOCK=threading.RLock()


@contextmanager
def training_preparation(training):
    from cloudstudio import runtime
    from cloud_rgb_input import exact_rgb_input
    # Both verified aliases must change/restore together. Generic concurrent
    # callers immediately use their original function; owned jobs serialize.
    with _LOCK:
        modules=(training,runtime)
        originals=tuple(module.letterbox for module in modules)
        owner=threading.get_ident();active=True
        def wrapper(original):
            def prepare(image,size):
                if not active or threading.get_ident()!=owner:return original(image,size)
                value=exact_rgb_input(image,size)
                return original(image,size) if value is None else value
            return prepare
        try:
            for module,original in zip(modules,originals):module.letterbox=wrapper(original)
            yield
        finally:
            active=False
            for module,original in zip(modules,originals):module.letterbox=original
