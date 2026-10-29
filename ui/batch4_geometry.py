"""Native physical-pixel and box-anchor geometry. No capture or output effects."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import math
import os

ANCHOR_BASES = {'center': .5, 'top': 0., 'bottom': 1., 'upper-quarter': .25}


def anchor_point(mode, x, y):
    if mode not in ANCHOR_BASES or any(type(v) not in (int, float) or abs(v)>1e308 or not math.isfinite(v)
            for v in (x, y)):
        raise ValueError('Invalid anchor')
    # rawTargetAnchor clamps offsets, not the resulting point.
    return .5 + max(-1.,min(1.,x)), ANCHOR_BASES[mode] + max(-1.,min(1.,y))


def anchor_offsets(mode, x, y):
    if mode not in ANCHOR_BASES or not all(math.isfinite(v) for v in (x, y)):
        raise ValueError('Invalid anchor')
    return max(-1., min(1., x-.5)), max(-1., min(1., y-ANCHOR_BASES[mode]))


@dataclass(frozen=True)
class Display:
    index: int
    device: str
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self):
        if not (type(self.index) is int and 0 <= self.index <= 32
                and all(type(v) is int and abs(v) <= 1000000 for v in (self.left,self.top,self.width,self.height))
                and self.width > 0 and self.height > 0):
            raise ValueError('Invalid display')


def region_pixels(display, x, y, width, height):
    if any(type(v) is not int or abs(v)>1000000 for v in (x,y,width,height)) or min(width,height)<1:
        raise ValueError('Invalid region')
    width, height = min(width,display.width), min(height,display.height)
    return (min(max((display.width-width)//2+x,0),display.width-width),
            min(max((display.height-height)//2+y,0),display.height-height), width,height)


def region_offsets(display, left, top, width, height):
    if (any(type(v) is not int for v in (left,top,width,height)) or min(left,top)<0
            or min(width,height)<1 or left+width>display.width or top+height>display.height):
        raise ValueError('Region is outside display')
    return left-(display.width-width)//2, top-(display.height-height)//2, width, height


def enumerate_displays():
    """Same enumeration as native ScreenCapture, under physical DPI context."""
    if os.name != 'nt':
        raise OSError('Physical display enumeration requires Windows')
    user = ctypes.WinDLL('user32',use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype=ctypes.c_void_p
    previous=user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous: raise OSError('Physical DPI context unavailable')
    class Info(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),
                  ('flags',wintypes.DWORD),('device',wintypes.WCHAR*32)]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HANDLE,wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT),wintypes.LPARAM)
    user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Info)]
    user.GetMonitorInfoW.restype=wintypes.BOOL
    user.EnumDisplayMonitors.argtypes=[wintypes.HDC,ctypes.POINTER(wintypes.RECT),callback_type,wintypes.LPARAM]
    user.EnumDisplayMonitors.restype=wintypes.BOOL
    displays=[]; failed=[]
    @callback_type
    def visit(handle,dc,rect,context):
        try:
            info=Info();info.size=ctypes.sizeof(info)
            if not user.GetMonitorInfoW(handle,ctypes.byref(info)): raise OSError('Monitor unavailable')
            r=info.monitor
            displays.append(Display(len(displays)+1,info.device,r.left,r.top,r.right-r.left,r.bottom-r.top))
            return True
        except (OSError,ValueError):
            failed.append(True);return False
    try:
        if not user.EnumDisplayMonitors(None,None,visit,0) or failed or not displays:
            raise OSError('Display enumeration failed')
        # Match native GDI virtual desktop bounds, including negative origins.
        virtual=Display(0,'virtual',*(user.GetSystemMetrics(i) for i in (76,77,78,79)))
        return (virtual,*displays)
    finally:
        user.SetThreadDpiAwarenessContext(previous)
