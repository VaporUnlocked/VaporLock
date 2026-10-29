"""Dispatch validated frames to the owned typed runtime or real-image fallback.

Typed frames avoid PIL conversion at exact model size. Generic predictors and
overrides retain real images; the older owned-RGB helper remains a fallback.
Normalization, channel order, owning array lifetime and geometry stay unchanged.
"""
from contextlib import contextmanager
import threading

_INPUT_LOCK = threading.RLock()


@contextmanager
def _owned_rgb(image):
    # Prediction is sequential in both transports. Serialize any additional
    # owned callers too, so overlapping contexts cannot restore stale aliases.
    with _INPUT_LOCK:
        with _prepare_owned_rgb(image):
            yield


@contextmanager
def _prepare_owned_rgb(image):
    from cloud_sdk import runtime as sdk
    from cloud_rgb_input import exact_rgb_input

    original=sdk.letterbox
    owner=threading.get_ident()
    active=True
    def prepare(candidate,size):
        if not active or candidate is not image or threading.get_ident()!=owner:
            return original(candidate,size)
        # A fresh owning CHW allocation avoids HWC float/normalization
        # temporaries and the final transpose copy. Keep division, not reciprocal
        # multiplication; every possible 8-bit channel value is parity-tested.
        prepared=exact_rgb_input(candidate,size)
        return original(candidate,size) if prepared is None else prepared
    sdk.letterbox=prepare
    try:
        yield
    finally:
        active=False
        sdk.letterbox=original


def predict_bgrx(runtime,width,height,pixels,**settings):
    """Transport-owned image construction; no IPC flag can enable fast mode."""
    from cloud_contract import CloudInputError,MAX_FRAME_PIXELS
    from PIL import Image
    if (type(width) is not int or type(height) is not int or not 1<=width<=4096 or not 1<=height<=4096
            or width*height>MAX_FRAME_PIXELS or type(pixels) not in (bytes,bytearray)
            or len(pixels)!=width*height*4):
        raise CloudInputError('Invalid .cloud live pixel buffer.')
    from cloud_runtime import owns_frame_path,uses_base_preprocessing
    if owns_frame_path(runtime):
        return runtime.predict_bgrx(width,height,pixels,**settings)
    image=Image.frombytes('RGB',(width,height),bytes(pixels),'raw','BGRX')
    if not uses_base_preprocessing():
        return runtime.predict(image,**settings)
    with _owned_rgb(image):
        return runtime.predict(image,**settings)
