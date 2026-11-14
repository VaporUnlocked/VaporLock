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


class EndOfVideo(Exception):
    pass


def decode_video(path, milliseconds):
    import cv2
    from PIL import Image

    guard = ProjectLease(path)
    try:
        selected = guard.hold_file(path, 8 * 1024**3)
        if selected.suffix.lower() not in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
            raise CloudInputError('Choose an MP4, MKV, AVI, MOV, or WebM video.')
        from dataset_paths import display_path
        reader = cv2.VideoCapture(display_path(selected))
        try:
            if not reader.isOpened():
                raise CloudInputError('The video could not be opened.')
            width = reader.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = reader.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if not (1 <= width <= 32768 and 1 <= height <= 32768 and width * height <= MAX_IMAGE_PIXELS):
                raise CloudInputError('The video exceeds the supported frame size.')
            if milliseconds:
                fps = reader.get(cv2.CAP_PROP_FPS)
                frames = reader.get(cv2.CAP_PROP_FRAME_COUNT)
                if fps > 0 and frames > 0 and milliseconds >= frames * 1000 / fps:
                    raise EndOfVideo()
                if not reader.set(cv2.CAP_PROP_POS_MSEC, milliseconds):
                    raise CloudInputError('This video does not support frame seeking.')
            ok, frame = reader.read()
            if not ok:
                if milliseconds:
                    raise EndOfVideo()
                raise CloudInputError('No frames could be decoded from the video.')
            height, width = frame.shape[:2]
            if not (1 <= width <= 32768 and 1 <= height <= 32768 and width * height <= MAX_IMAGE_PIXELS):
                raise CloudInputError('The decoded video frame exceeds the supported size.')
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            rgb = image.tobytes()
            return image, rgb, hashlib.sha256(f'{width}x{height}:'.encode()+rgb).hexdigest()
        finally:
            reader.release()
    finally:
        guard.stack.close()


def decode(path, *, preserve_orientation=False):
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
                if preserve_orientation and source.getexif().get(274, 1) != 1:
                    raise CloudInputError('Normalize this image and its YOLO labels together before importing.')
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
        if 'video_time_ms' in request:
            try:
                image,rgb,pixel_sha=decode_video(request['path'], request['video_time_ms'])
            except EndOfVideo:
                return dict(type='decode_exhausted', request=request['request'], capability=capability)
        else:
            image,rgb,pixel_sha=decode(request['path'], preserve_orientation=request.get('preserve_orientation',False))
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
