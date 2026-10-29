"""Bounded crosshair calibration data. No inference model, file or network access."""
FIELDS=('crosshair_enabled','crosshair_calibration')
KEYS={'geometry','point','samples'}

def integers(values,count,low,high):
    return type(values) is list and len(values)==count and all(type(n) is int and low<=n<=high for n in values)

def validate(values):
    if type(values.get('crosshair_enabled',False)) is not bool:raise ValueError('Invalid crosshair toggle')
    t=values.get('crosshair_calibration',{})
    if type(t) is not dict:raise ValueError('Invalid crosshair calibration')
    if not t:return
    if set(t)!=KEYS or not integers(t['geometry'],9,-1000000,1000000):raise ValueError('Invalid calibration geometry')
    g=t['geometry'];p=t['point'];s=t['samples']
    if not 1<=g[0]<=32 or min(g[3],g[4],g[7],g[8])<1:raise ValueError('Invalid calibration display')
    if not integers(p,2,0,1000000) or p[0]>=g[3] or p[1]>=g[4]:raise ValueError('Invalid aiming point')
    if type(s) is not list or not 5<=len(s)<=128:raise ValueError('Invalid calibration samples')
    coords=set();positive=negative=0
    for row in s:
        if not integers(row,6,-31,255) or abs(row[0])>31 or abs(row[1])>31 or min(row[2:5])<0 or row[5] not in (0,1):raise ValueError('Invalid calibration sample')
        xy=tuple(row[:2])
        if xy in coords:raise ValueError('Duplicate calibration sample')
        coords.add(xy);positive+=row[5];negative+=not row[5]
    if not 1<=positive<=64 or not 4<=negative<=64:raise ValueError('Insufficient foreground or boundary')

def wire(settings):
    validate(vars(settings));t=settings.crosshair_calibration
    if not t:return ''
    return ' '.join(map(str,[1,*t['geometry'],*t['point'],len(t['samples']),*(n for row in t['samples'] for n in row)]))

def geometry(settings,display):
    return [display.index,display.left,display.top,display.width,display.height,
            settings.region_x,settings.region_y,settings.region_width,settings.region_height]

def remaining_ms(payload,now):
    import math
    if payload.get('reference_inside',True) is not True:return 0.
    if not payload.get('crosshair_detected',False):return math.inf
    age=payload.get('crosshair_age_ms');emitted=payload.get('emitted_tick_ms')
    if type(age) not in (float,int) or not math.isfinite(age) or age<0 or type(emitted) is not int or type(now) is not int or now<emitted:return 0.
    return max(0.,100.-age-(now-emitted))

def sample_template(image,point,mask,source_geometry,origin):
    """Keep sparse selected foreground and adjacent not-foreground constraints."""
    x,y=point
    if not mask or any(abs(px-x)>31 or abs(py-y)>31 for px,py in mask):raise ValueError('Select crosshair pixels within 31 pixels of the aim point.')
    if any(not 0<=px<image.width() or not 0<=py<image.height() for px,py in mask):raise ValueError('Invalid sample position')
    positive=sorted(mask,key=lambda p:(p[1],p[0]));negative={}
    for px,py in positive:
        c=image.pixelColor(px,py);rgb=(c.red(),c.green(),c.blue())
        for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)):
            q=(px+dx,py+dy)
            if q in mask or not 0<=q[0]<image.width() or not 0<=q[1]<image.height() or abs(q[0]-x)>31 or abs(q[1]-y)>31:continue
            color=image.pixelColor(*q)
            if max(abs(a-b) for a,b in zip(rgb,(color.red(),color.green(),color.blue())))>=64:negative[q]=rgb
    def sparse(seq):return [seq[i*len(seq)//min(64,len(seq))] for i in range(min(64,len(seq)))]
    samples=[]
    for px,py in sparse(positive):
        c=image.pixelColor(px,py);samples.append([px-x,py-y,c.red(),c.green(),c.blue(),1])
    for px,py in sparse(sorted(negative)):
        samples.append([px-x,py-y,*negative[px,py],0])
    result={'geometry':list(source_geometry),'point':[origin[0]+x,origin[1]+y],'samples':samples}
    validate({'crosshair_calibration':result});return result

def capture_patch(display):
    """Capture only 127x127 physical pixels. GDI excludes cursor; no disk writes."""
    import ctypes
    from ctypes import wintypes as W
    from PySide6.QtGui import QImage
    if display.index<1 or min(display.width,display.height)<127:raise ValueError('Select a physical display for calibration.')
    u=ctypes.WinDLL('user32',use_last_error=True);g=ctypes.WinDLL('gdi32',use_last_error=True)
    u.GetDC.argtypes=[W.HWND];u.GetDC.restype=W.HDC;u.ReleaseDC.argtypes=[W.HWND,W.HDC]
    u.SetThreadDpiAwarenessContext.argtypes=[W.HANDLE];u.SetThreadDpiAwarenessContext.restype=W.HANDLE
    g.CreateCompatibleDC.argtypes=[W.HDC];g.CreateCompatibleDC.restype=W.HDC
    g.CreateDIBSection.argtypes=[W.HDC,ctypes.c_void_p,W.UINT,ctypes.POINTER(ctypes.c_void_p),W.HANDLE,W.DWORD];g.CreateDIBSection.restype=W.HBITMAP
    g.SelectObject.argtypes=[W.HDC,W.HANDLE];g.SelectObject.restype=W.HANDLE
    g.DeleteDC.argtypes=[W.HDC];g.DeleteObject.argtypes=[W.HANDLE]
    g.BitBlt.argtypes=[W.HDC,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HDC,ctypes.c_int,ctypes.c_int,W.DWORD]
    class Header(ctypes.Structure):
        _fields_=[('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bits',W.WORD),('compression',W.DWORD),('image',W.DWORD),('xp',W.LONG),('yp',W.LONG),('used',W.DWORD),('important',W.DWORD)]
    previous=u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4));dc=mem=bitmap=old=None
    try:
        dc=u.GetDC(None);mem=g.CreateCompatibleDC(dc)
        header=Header(ctypes.sizeof(Header),127,-127,1,32,0,0,0,0,0,0);bits=ctypes.c_void_p()
        bitmap=g.CreateDIBSection(dc,ctypes.byref(header),0,ctypes.byref(bits),None,0)
        if not dc or not mem or not bitmap or not bits.value:raise OSError('Calibration capture unavailable')
        old=g.SelectObject(mem,bitmap);origin=(display.width//2-63,display.height//2-63)
        if not g.BitBlt(mem,0,0,127,127,dc,display.left+origin[0],display.top+origin[1],0x40CC0020):raise OSError('Calibration capture failed')
        g.GdiFlush();data=ctypes.string_at(bits,127*127*4)
        return QImage(data,127,127,127*4,QImage.Format.Format_RGB32).copy(),origin
    finally:
        if old:g.SelectObject(mem,old)
        if bitmap:g.DeleteObject(bitmap)
        if mem:g.DeleteDC(mem)
        if dc:u.ReleaseDC(None,dc)
        if previous:u.SetThreadDpiAwarenessContext(previous)
