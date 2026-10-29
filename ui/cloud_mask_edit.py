"""Lossless source-pixel mask editing, independent of Qt and inference models."""
from __future__ import annotations

from itertools import groupby
import math

from cloud_contract import CloudInputError, MAX_IMAGE_PIXELS, check_mask, finite, integer


class MaskRaster:
    """A fixed selection-box crop. Holes and separate components stay exact."""
    def __init__(self, width, height, box, encoded=None):
        from cloud_editor_contract import annotation
        annotation(dict(id='raster',class_id=0,box=list(box)),width,height,complete=False)
        self.width,self.height=width,height;self.box=tuple(box)
        self.x=max(0,math.ceil(box[0]-.5));self.y=max(0,math.ceil(box[1]-.5))
        self.w=min(width,math.ceil(box[2]-.5))-self.x
        self.h=min(height,math.ceil(box[3]-.5))-self.y
        if self.w*self.h>MAX_IMAGE_PIXELS:
            raise CloudInputError('Mask editing exceeds its pixel budget.')
        self.pixels=bytearray(self.w*self.h)
        if encoded is not None:
            check_mask(encoded,width,height)
            ox,oy=encoded['origin'];mh,mw=encoded['shape'];position=0
            for i,run in enumerate(encoded['counts']):
                end=position+run
                if i%2:
                    while position<end:
                        row,col=divmod(position,mw);stop=min(end,(row+1)*mw)
                        y=oy+row;left=ox+col;right=left+stop-position
                        if not self.y<=y<self.y+self.h or left<self.x or right>self.x+self.w:
                            raise CloudInputError('Mask foreground leaves its selection box.')
                        start=(y-self.y)*self.w+left-self.x
                        self.pixels[start:start+right-left]=b'\1'*(right-left)
                        position=stop
                position=end

    def stroke(self,start,end,radius,*,erase=False):
        radius=integer(radius,1,128,'Brush radius')
        points=[]
        for point in (start,end):
            if type(point) not in (tuple,list) or len(point)!=2:
                raise CloudInputError('Invalid brush position.')
            points.append(tuple(finite(v,-32768,65536,'Brush position') for v in point))
        a,b=points;distance=math.hypot(b[0]-a[0],b[1]-a[1])
        steps=max(1,math.ceil(distance/max(1,radius/2)))
        if steps>131072:raise CloudInputError('Brush stroke exceeds its step budget.')
        value=0 if erase else 1
        for n in range(steps+1):
            cx=a[0]+(b[0]-a[0])*n/steps;cy=a[1]+(b[1]-a[1])*n/steps
            top=max(self.y,math.ceil(cy-radius-.5));bottom=min(self.y+self.h,math.floor(cy+radius-.5)+1)
            for y in range(top,bottom):
                dy=y+.5-cy;span=math.sqrt(max(0,radius*radius-dy*dy))
                left=max(self.x,math.ceil(cx-span-.5));right=min(self.x+self.w,math.floor(cx+span-.5)+1)
                if right>left:
                    offset=(y-self.y)*self.w+left-self.x
                    self.pixels[offset:offset+right-left]=bytes([value])*(right-left)

    def encode(self):
        counts=[];foreground=False
        for value,group in groupby(self.pixels):
            if bool(value)!=foreground:counts.append(0);foreground=bool(value)
            counts.append(sum(1 for _ in group));foreground=not foreground
        if not counts:counts=[0]
        # groupby emits one run per value. Only the initial zero is permitted.
        result=dict(encoding='cloud-rle-v1',size=[self.height,self.width],origin=[self.x,self.y],
                    shape=[self.h,self.w],counts=counts)
        check_mask(result,self.width,self.height)
        return result

    def rgba(self,color=(98,220,205,110)):
        """Straight RGBA bytes for an exact nearest-neighbor display overlay."""
        if len(color)!=4 or any(type(c) is not int or not 0<=c<=255 for c in color):
            raise CloudInputError('Invalid mask preview color.')
        rgba=bytearray(len(self.pixels)*4)
        for channel,value in enumerate(color):
            table=bytes([0])+bytes([value])*255
            rgba[channel::4]=self.pixels.translate(table)
        return bytes(rgba)
