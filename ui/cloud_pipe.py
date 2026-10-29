"""Local, single-client native inference transport; no network listener."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import struct

from cloud_contract import CloudInputError, FRAME, MAX_FRAME_PIXELS, frame_header

IDENTITY = struct.Struct('<QQQQ')
RESPONSE = struct.Struct('<8sQQIIIIfffffQQQQ')
DETECTION = struct.Struct('<fffffIIIIII')
MAX_BODY = 8 * 1024 * 1024


def _api():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    advapi = C.WinDLL('advapi32', use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, C.c_void_p]
    kernel.CreateNamedPipeW.restype = W.HANDLE
    kernel.ConnectNamedPipe.argtypes = [W.HANDLE, C.c_void_p]; kernel.ConnectNamedPipe.restype = W.BOOL
    kernel.GetNamedPipeClientProcessId.argtypes = [W.HANDLE, C.POINTER(W.ULONG)]; kernel.GetNamedPipeClientProcessId.restype = W.BOOL
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; kernel.OpenProcess.restype = W.HANDLE
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; kernel.WaitForSingleObject.restype = W.DWORD
    kernel.CloseHandle.argtypes = [W.HANDLE]; kernel.CloseHandle.restype = W.BOOL
    kernel.LocalFree.argtypes = [C.c_void_p]; kernel.LocalFree.restype = C.c_void_p
    advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]; advapi.OpenProcessToken.restype = W.BOOL
    advapi.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    advapi.GetTokenInformation.restype = W.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]; advapi.ConvertSidToStringSidW.restype = W.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = W.BOOL
    return kernel, advapi


class PipeServer:
    def __init__(self, name, *, build=None):
        from cloud_access import pipe_name
        name=pipe_name(name,build=build)
        self.kernel, advapi = _api(); self.handle =  self.peer =  self.stream = None
        token = W.HANDLE(); sid_text = W.LPWSTR(); descriptor = C.c_void_p()
        class Security(C.Structure):
            _fields_ = [('length', W.DWORD), ('descriptor', C.c_void_p), ('inherit', W.BOOL)]
        try:
            if not advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, C.byref(token)):
                raise OSError('Cannot obtain the .cloud pipe identity.')
            length = W.DWORD(); advapi.GetTokenInformation(token, 1, None, 0, C.byref(length))
            if not 0 < length.value <= 65536:
                raise OSError('Invalid pipe identity size.')
            user = C.create_string_buffer(length.value)
            if not advapi.GetTokenInformation(token, 1, user, length.value, C.byref(length)):
                raise OSError('Cannot read the .cloud pipe identity.')
            sid = C.cast(user, C.POINTER(C.c_void_p))[0]
            if not advapi.ConvertSidToStringSidW(sid, C.byref(sid_text)):
                raise OSError('Cannot encode the .cloud pipe identity.')
            # Read/write data, EA/attributes, read-control and synchronize;
            # deliberately exclude FILE_CREATE_PIPE_INSTANCE (bit 0x4).
            sddl = 'D:P(A;;0x0012019b;;;SY)(A;;0x0012019b;;;' + sid_text.value + ')'
            if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None):
                raise OSError('Cannot protect the .cloud pipe.')
            security = Security(C.sizeof(Security), descriptor, False)
            handle = self.kernel.CreateNamedPipeW('\\\\.\\pipe\\' + name, 3 | 0x80000, 8, 1, 65536, 65536, 5000, C.byref(security))
            if handle in (None, C.c_void_p(-1).value):
                raise OSError('Cannot create the private .cloud pipe.')
            self.handle = handle
        finally:
            if descriptor.value: self.kernel.LocalFree(descriptor)
            if sid_text: self.kernel.LocalFree(C.cast(sid_text, C.c_void_p))
            if token: self.kernel.CloseHandle(token)

    def connect(self, expected_pid):
        from cloud_contract import integer
        integer(expected_pid, 1, 2**32 - 1, 'Native client process')
        self.peer = self.kernel.OpenProcess(0x101000, False, expected_pid)
        if not self.peer or self.kernel.WaitForSingleObject(self.peer, 0) != 258:
            raise CloudInputError('The admitted native .cloud client is unavailable.')
        if not self.kernel.ConnectNamedPipe(self.handle, None) and C.get_last_error() != 535:
            raise CloudInputError('The private .cloud client connection failed.')
        actual = W.ULONG()
        if (not self.kernel.GetNamedPipeClientProcessId(self.handle, C.byref(actual)) or actual.value != expected_pid
                or self.kernel.WaitForSingleObject(self.peer, 0) != 258):
            raise CloudInputError('The .cloud pipe client does not match the admitted native process.')
        import msvcrt
        descriptor = msvcrt.open_osfhandle(self.handle, os.O_RDWR | os.O_BINARY)
        self.handle = None
        self.stream = os.fdopen(descriptor, 'r+b', buffering=0)
        return self.stream

    def close(self):
        if self.stream is not None:
            self.stream.close(); self.stream = None
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle); self.handle = None
        if self.peer is not None:
            self.kernel.CloseHandle(self.peer); self.peer = None


def exact(stream, count, *, eof=False):
    data = bytearray(count); view = memoryview(data); offset = 0
    while offset < count:
        read = stream.readinto(view[offset:])
        if not read:
            if eof and offset == 0: return None
            raise CloudInputError('The .cloud observation transport was truncated.')
        offset += read
    return data


def serve(server, runtime, read_command):
    command = read_command()
    if type(command) is not dict or set(command) != {'command', 'pid'} or command['command'] != 'client':
        raise CloudInputError('The .cloud native client was not admitted.')
    stream = server.connect(command['pid']); previous = 0
    from PIL import Image
    from cloud_live_input import predict_bgrx
    import numpy as np
    while True:
        header = exact(stream, FRAME.size, eof=True)
        if header is None: return
        sequence, captured_ns, width, height, length, confidence, iou, maximum = frame_header(header)
        identity = IDENTITY.unpack(exact(stream, IDENTITY.size))
        if sequence <= previous:
            raise CloudInputError('Nonmonotonic .cloud native observation.')
        previous = sequence
        pixels=exact(stream,length)
        from cloud_native_masks import native_masks
        with native_masks(width,height,maximum) as cache:
            result = predict_bgrx(runtime,width,height,pixels,confidence=confidence,nms_iou=iou,max_detections=maximum,debug=True)
            body = bytearray(); mask_pixels = 0
            for item in result['detections']:
                x1, y1, x2, y2 = item['primary_box']; mask = item.get('mask'); packed = b''
                ox = oy = mw = mh = 0
                if mask is not None:
                    from cloud_sdk.masks import decode_mask
                    cached=cache.get(mask)
                    if cached is None:
                        pixels = decode_mask(mask, full=False); mh, mw = pixels.shape; ox, oy = mask['origin']
                        packed = np.packbits(pixels.reshape(-1), bitorder='little').tobytes()
                    else:
                        mh,mw=cached.height,cached.width;ox,oy=cached.origin;packed=cached.pixels
                    mask_pixels += mh * mw
                    if mask_pixels > MAX_FRAME_PIXELS * 4:
                        raise CloudInputError('The .cloud masks exceeded their observation budget.')
                polygon = item.get('wrapper', []) if mask is None else []
                if len(polygon) > 2048:
                    raise CloudInputError('The .cloud polygon exceeded its observation budget.')
                if polygon:
                    # S1's actual polygon, rasterized for the existing native mask
                    # renderer; no invented contour from a detection-only box.
                    from PIL import ImageDraw
                    import math
                    ox=max(0,math.floor(min(p[0] for p in polygon)))
                    oy=max(0,math.floor(min(p[1] for p in polygon)))
                    mw=min(width,math.ceil(max(p[0] for p in polygon))+1)-ox
                    mh=min(height,math.ceil(max(p[1] for p in polygon))+1)-oy
                    if mw>0 and mh>0:
                        mask_pixels+=mw*mh
                        if mask_pixels>MAX_FRAME_PIXELS*4:raise CloudInputError('The .cloud contour mask exceeded its observation budget.')
                        raster=Image.new('1',(mw,mh))
                        ImageDraw.Draw(raster).polygon([(x-ox,y-oy) for x,y in polygon],fill=1)
                        packed=np.packbits(np.asarray(raster,dtype=bool).reshape(-1),bitorder='little').tobytes()
                    else:ox=oy=mw=mh=0
                    polygon=[]
                body.extend(DETECTION.pack(x1, y1, x2, y2, item['confidence'], ox, oy, mw, mh, len(packed), len(polygon)))
                body.extend(packed)
                for x, y in polygon: body.extend(struct.pack('<ff', x, y))
                if len(body) > MAX_BODY:
                    raise CloudInputError('The .cloud binary result exceeded its observation budget.')
            timing = result['timing_ms']; maximum_score = result['diagnostics']['max_confidence']
            response = RESPONSE.pack(b'VLCRES01', sequence, captured_ns, width, height, len(result['detections']), len(body),
                timing['preprocess'], timing['inference'], timing['postprocess'], timing['total'],
                -1 if maximum_score is None else maximum_score, *identity)

        for part in (response, body):
            view = memoryview(part)
            while view:
                written = stream.write(view)
                if not written: raise BrokenPipeError('The native .cloud client stopped reading.')
                view = view[written:]
        stream.flush()
