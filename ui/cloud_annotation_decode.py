"""Candidate isolated image parsing and exact parent-owned staging capability.

Imported only by the annotation child. No project database or annotation writes.
"""
import hashlib
import io
import os
from pathlib import Path
import warnings

from cloud_contract import CloudInputError, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, json_value
from cloud_project import ProjectLease
from cloud_editor_contract import encoded
from dataset_paths import reject_reparse


def decode(path):
    from PIL import Image, ImageOps
    guard=ProjectLease(path)
    try:
        selected=guard.hold_file(path,MAX_IMAGE_BYTES)
        if selected.suffix.lower() not in ('.png','.jpg','.jpeg','.bmp','.webp'):
            raise CloudInputError('Choose a PNG, JPEG, BMP or WebP image.')
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with selected.open('rb') as stream, Image.open(stream) as source:
                width,height=source.size
                if (width<=0 or height<=0 or width*height>MAX_IMAGE_PIXELS
                        or max(width,height)>32768 or getattr(source,'n_frames',1)!=1
                        or source.format not in ('PNG','JPEG','BMP','WEBP')):
                    raise CloudInputError('The image format, dimensions or frame count is unsupported.')
                # Exactly the existing Cloud Studio identity recipe: EXIF
                # transpose then RGB. Alpha is discarded; no ICC transform.
                image=ImageOps.exif_transpose(source).convert('RGB');image.load()
        rgb=image.tobytes();width,height=image.size
        if len(rgb)!=width*height*3:
            raise CloudInputError('Unexpected decoded RGB size.')
        digest=hashlib.sha256(f'{width}x{height}:'.encode()+rgb).hexdigest()
        return image,rgb,digest
    finally:guard.stack.close()


def stage(request):
    from cloud_annotation_protocol import command
    command(request)
    folder=reject_reparse(request['output']);capability=request['capability']
    if folder.name!='cloud-annotation-'+capability or not folder.is_dir():
        raise CloudInputError('Invalid decoded-image staging capability.')
    guard=ProjectLease(folder)
    try:
        guard._directory(folder)
        marker=guard._read(folder/'capability.json',4096)
        if marker!={'schema_version':1,'capability':capability,'request':request['request']}:
            raise CloudInputError('Decoded-image capability does not match the parent request.')
        if {p.name for p in folder.iterdir()}!={'capability.json'}:
            raise CloudInputError('Decoded-image staging folder is not empty.')
        image,rgb,pixel_sha=decode(request['path'])
        buffer=io.BytesIO();image.save(buffer,format='PNG');png=buffer.getvalue()
        if len(png)>MAX_IMAGE_BYTES:
            raise CloudInputError('Canonical image exceeds its encoded byte limit.')
        for name,data in (('image.rgb',rgb),('image.png',png)):
            path=folder/name
            with path.open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        return dict(type='decoded',request=request['request'],capability=capability,
            width=image.width,height=image.height,pixel_sha256=pixel_sha,
            rgb_bytes=len(rgb),rgb_sha256=hashlib.sha256(rgb).hexdigest(),
            png_bytes=len(png),png_sha256=hashlib.sha256(png).hexdigest())
    finally:guard.stack.close()
