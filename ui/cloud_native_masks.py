"""Candidate native-only reuse of exact SDK masks; no format or protocol change.

The original encoder still owns crop/RLE/empty-mask decisions. Keep the packed
pixels from that same call rather than decoding them again for transport.
Only the native observation transport installs this scoped optimization.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import threading
import hashlib

from cloud_contract import CloudInputError,MAX_FRAME_PIXELS

_LOCK=threading.RLock()
_CURRENT=threading.local()
_MAX_BODY=8*1024*1024


@dataclass(frozen=True)
class PackedMask:
    origin:tuple
    width:int
    height:int
    pixels:bytes


class _Observation:
    def __init__(self,width,height,maximum):
        self.width=width;self.height=height;self.maximum=maximum
        self.owner=threading.get_ident();self.active=True
        self.records={};self.native_records={};self.pixel_count=0;self.byte_count=0
        self.projecting=False;self.encoder=None;self.preview=None
        self.original_encode=None;self.original_preview=None

    def owns(self):
        return self.active and threading.get_ident()==self.owner and getattr(_CURRENT,'observation',None) is self

    def get(self,mask):
        if not self.owns():return None
        if type(mask) is PackedMask:
            # Only the immutable artifact made and retained by this exact
            # observation is accepted. Constructed, expired and foreign values
            # cannot acquire ownership by matching pixels or geometry.
            return mask if self.native_records.get(id(mask)) is mask else None
        value=self.records.get(id(mask))
        if value is None or value[0] is not mask:return None
        try:
            if self.signature(mask)!=value[2]:return None
        except (ValueError,TypeError,OverflowError):return None
        return value[1]

    def pack_native(self,array,origin,image_size):
        import numpy as np
        from cloud_sdk.masks import MAX_RUNS
        pixels=np.asarray(array)
        if (not self.owns() or not self.projecting or tuple(image_size)!=(self.width,self.height)
                or pixels.ndim!=2 or pixels.dtype!=np.dtype(bool)
                or not isinstance(origin,(tuple,list)) or len(origin)!=2
                or any(type(v) is not int or v<0 for v in origin)
                or origin[0]+pixels.shape[1]>self.width or origin[1]+pixels.shape[0]>self.height):
            raise CloudInputError('Invalid .cloud native projected mask.')
        rows=np.flatnonzero(pixels.any(axis=1));cols=np.flatnonzero(pixels.any(axis=0))
        ox=oy=w=h=0;packed=b''
        if len(rows):
            y0,y1,x0,x1=int(rows[0]),int(rows[-1])+1,int(cols[0]),int(cols[-1])+1
            flat=np.ascontiguousarray(pixels[y0:y1,x0:x1]).reshape(-1)
            # Keep the SDK's refusal of excessive alternating runs, even
            # though this transport no longer materializes Python RLE lists.
            runs=int(np.count_nonzero(flat[1:]!=flat[:-1]))+1+int(flat[0])
            if runs>MAX_RUNS:raise ValueError('Mask has too many alternating runs; no pixels were discarded.')
            ox,oy=origin[0]+x0,origin[1]+y0;w,h=x1-x0,y1-y0
            packed=np.packbits(flat,bitorder='little').tobytes()
        self.reserve(w*h,len(packed))
        value=PackedMask((ox,oy),w,h,packed)
        self.native_records[id(value)]=value
        return value

    def reserve(self,pixels,length):
        if (len(self.records)+len(self.native_records)>=self.maximum
                or self.pixel_count+pixels>MAX_FRAME_PIXELS*4 or self.byte_count+length>_MAX_BODY):
            raise CloudInputError('The .cloud masks exceeded their observation budget.')
        self.pixel_count+=pixels;self.byte_count+=length

    def signature(self,mask):
        from cloud_sdk.masks import _schema
        import numpy as np
        width,height,x,y,w,h,counts=_schema(mask,(self.width,self.height))
        digest=hashlib.sha256()
        # Bound temporary allocation even for the encoder's maximum run count.
        for start in range(0,len(counts),65536):
            digest.update(np.asarray(counts[start:start+65536],dtype='<u4').tobytes())
        return width,height,x,y,w,h,digest.digest()

    def remember(self,array,origin,image_size,encoded):
        if not self.owns() or tuple(image_size)!=(self.width,self.height):return
        import numpy as np
        height,width=encoded['shape'];ox,oy=encoded['origin']
        count=height*width;length=(count+7)//8
        self.reserve(count,length)
        if count:
            x=ox-origin[0];y=oy-origin[1]
            crop=np.asarray(array,dtype=bool)[y:y+height,x:x+width]
            if crop.shape!=(height,width):raise CloudInputError('Invalid .cloud native mask crop.')
            pixels=np.packbits(crop.reshape(-1),bitorder='little').tobytes()
        else:pixels=b''
        if len(pixels)!=length:raise CloudInputError('Invalid .cloud native mask payload.')
        # Retain the original dictionary too: object-id reuse cannot associate
        # another mask with these bytes. Both are released when the call ends.
        self.records[id(encoded)]=(encoded,PackedMask((ox,oy),width,height,pixels),self.signature(encoded))

    def close(self):
        self.active=False;self.records.clear();self.native_records.clear()


def project_native_mask(probabilities,box,image_size,projector,encoder,preview):
    """Use the unchanged SDK projection with an internal packed-mask sink.

    Only the owned typed-frame path calls this helper. Generic prediction and
    modified SDK hooks keep their original RLE contract and mutation checks.
    """
    observation=getattr(_CURRENT,'observation',None)
    if observation is None or not observation.owns():return None
    from cloud_sdk import masks,runtime
    if (masks.project_roi_mask is not projector or masks.encode_mask is not observation.encoder
            or observation.original_encode is not encoder or observation.original_preview is not preview
            or runtime._mask_preview is not observation.preview
            or tuple(image_size)!=(observation.width,observation.height)):
        return None
    if observation.projecting:raise CloudInputError('Nested .cloud native projection.')
    observation.projecting=True
    try:
        result=projector(probabilities,box,image_size)
        if observation.get(result) is None:raise CloudInputError('Invalid .cloud native projection result.')
        return result
    finally:observation.projecting=False


@contextmanager
def native_masks(width,height,maximum):
    if (type(width) is not int or type(height) is not int or not 1<=width<=4096 or not 1<=height<=4096
            or width*height>MAX_FRAME_PIXELS or type(maximum) is not int or not 1<=maximum<=256):
        raise CloudInputError('Invalid .cloud native mask observation.')
    from cloud_sdk import masks,runtime
    with _LOCK:
        original_encode=masks.encode_mask;original_preview=runtime._mask_preview
        observation=_Observation(width,height,maximum)
        observation.original_encode=original_encode;observation.original_preview=original_preview
        previous=getattr(_CURRENT,'observation',None)
        def encode(array,*,origin=(0,0),image_size=None):
            if observation.owns() and observation.projecting:
                return observation.pack_native(array,origin,image_size)
            result=original_encode(array,origin=origin,image_size=image_size)
            if observation.owns() and image_size is not None:
                observation.remember(array,origin,image_size,result)
            return result
        def preview(mask,box):
            # The native S2 consumer uses the exact mask, never this legacy
            # approximate hull. Unrelated masks/callers retain SDK behavior.
            if observation.get(mask) is not None:return []
            return original_preview(mask,box)
        observation.encoder=encode
        observation.preview=preview
        try:
            masks.encode_mask=encode;runtime._mask_preview=preview
            _CURRENT.observation=observation
            yield observation
        finally:
            observation.close()
            _CURRENT.observation=previous
            masks.encode_mask=original_encode;runtime._mask_preview=original_preview
