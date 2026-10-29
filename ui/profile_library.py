"""Bounded local card metadata. Never changes profile configuration bytes."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from vlui_native_injection import writable_native_path

MAX_IMAGE_BYTES=8*1024*1024
MAX_META_BYTES=8192


def checked_path(root,*parts):
    root=Path(root).absolute()
    if any(not isinstance(s,str) or s in ('','.', '..') or '/' in s or '\\' in s or ':' in s for s in parts):
        raise ValueError('Unsafe library path')
    path=root.joinpath(*parts)
    writable_native_path(path)
    for node in (*reversed(path.parents),path):
        try:info=node.lstat()
        except FileNotFoundError:continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400
                or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1)):
            raise ValueError('Linked library paths are not supported')
    return path


def read_bounded(path,limit):
    path=Path(path)
    with path.open('rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('File too large')
        data=stream.read(limit+1);after=os.fstat(stream.fileno());current=path.stat()
        stamp=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
        if len(data)>limit or stamp(info)!=stamp(after) or stamp(after)!=stamp(current):
            raise ValueError('File changed')
        return data


def unique_json(data):
    def pairs(items):
        obj={}
        for key,value in items:
            if key in obj:raise ValueError('Duplicate member')
            obj[key]=value
        return obj
    def invalid(_):raise ValueError('Nonfinite value')
    return json.loads(data.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid)


def plain(value,limit):
    if type(value) is not str or len(value)>limit or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError('Invalid text')
    return value.strip()


def validate_metadata(raw):
    if type(raw) is not dict or set(raw)!={'schema','description','tags','image','game_id'} or type(raw['schema']) is not int or raw['schema']!=1:
        raise ValueError('Invalid card metadata')
    description=plain(raw['description'],600)
    if type(raw['tags']) is not list or len(raw['tags'])>12:raise ValueError('Invalid tags')
    tags=list(dict.fromkeys(plain(v,32) for v in raw['tags'] if v!=''))
    image=raw['image'];game=raw['game_id']
    if type(image) is not str or (image and not re.fullmatch(r'[0-9a-f]{64}\.png',image)):
        raise ValueError('Invalid image name')
    if type(game) is not str or (game and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',game)):
        raise ValueError('Invalid game ID')
    return dict(schema=1,description=description,tags=tags,image=image,game_id=game)


def empty_metadata():return dict(schema=1,description='',tags=[],image='',game_id='')


class ProfileLibrary:
    def __init__(self,root):self.root=Path(root).absolute()
    def profile(self,path):
        path=Path(path).absolute()
        if path.parent!=self.root or path.suffix.lower()!='.json':raise ValueError('Not a local profile')
        return checked_path(self.root,path.name)
    def meta_path(self,path):
        path=self.profile(path)
        key=hashlib.sha256(path.name.casefold().encode('utf-8')).hexdigest()
        return checked_path(self.root,'.library',key+'.json')
    def read(self,path):
        try:return validate_metadata(unique_json(read_bounded(self.meta_path(path),MAX_META_BYTES)))
        except (OSError,ValueError,TypeError,RecursionError):return empty_metadata()
    def list(self):
        root=checked_path(self.root)
        if not root.exists():return []
        # Bound discovery work as well as the returned number of cards.
        items=[]
        with os.scandir(root) as entries:
            for index,entry in enumerate(entries):
                if index>=4096:raise ValueError('Profile library exceeds display limit')
                if entry.name.lower().endswith('.json') and entry.is_file(follow_symlinks=False):
                    path=self.profile(entry.path)
                    items.append((path,self.read(path)))
                    if len(items)>128:raise ValueError('Profile library exceeds display limit')
        return sorted(items,key=lambda row:row[0].name.casefold())
    def image_bytes(self,name):
        if not re.fullmatch(r'[0-9a-f]{64}\.png',name):raise ValueError('Invalid image')
        data=read_bounded(checked_path(self.root,'.library','images',name),1024*1024)
        if hashlib.sha256(data).hexdigest()+'.png'!=name:raise ValueError('Image changed')
        return data
    def save(self,path,raw,*,image_data=None,guard=lambda:True):
        target=self.meta_path(path);value=validate_metadata(raw)
        if image_data is not None:
            if type(image_data) is not bytes or len(image_data)>1024*1024:raise ValueError('Invalid image')
            # Enforce the same safe static decode/encode even for direct callers.
            image_data=normalize_image(image_data)
            value['image']=hashlib.sha256(image_data).hexdigest()+'.png'
        if not guard():raise ValueError('Profile changed')
        checked_path(self.root,'.library').mkdir(exist_ok=True)
        if image_data is not None:
            checked_path(self.root,'.library','images').mkdir(exist_ok=True)
            image_path=checked_path(self.root,'.library','images',value['image'])
            if not image_path.exists():self.atomic(image_path,image_data,guard)
        self.atomic(target,json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'),guard)
        return value
    def atomic(self,target,data,guard):
        relative=target.relative_to(self.root).parts
        checked_path(self.root,*relative)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.tmp',delete=False) as stream:
                temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
            if not guard():raise ValueError('Profile changed')
            checked_path(self.root,*relative);checked_path(self.root,*temporary.relative_to(self.root).parts)
            os.replace(temporary,target)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)


def normalize_image(data):
    """Accept only bounded static PNG/JPEG, discard metadata, keep ≤640×360."""
    from PySide6.QtCore import QBuffer,QByteArray,QIODevice,Qt,QSize
    from PySide6.QtGui import QImageReader,QImage,QPainter
    if type(data) is not bytes or not 0<len(data)<=MAX_IMAGE_BYTES:raise ValueError('Invalid image')
    buffer=QBuffer();buffer.setData(QByteArray(data));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader=QImageReader(buffer);reader.setDecideFormatFromContent(True)
    if bytes(reader.format()).lower() not in (b'png',b'jpeg',b'jpg') or reader.supportsAnimation():
        raise ValueError('Choose a static PNG or JPEG image')
    size=reader.size()
    if min(size.width(),size.height())<1 or max(size.width(),size.height())>4096 or size.width()*size.height()>16000000:
        raise ValueError('Image dimensions are too large')
    reader.setAutoTransform(True);reader.setScaledSize(size.scaled(QSize(640,360),Qt.AspectRatioMode.KeepAspectRatio))
    decoded=reader.read()
    if decoded.isNull():raise ValueError('Invalid image')
    decoded=decoded.scaled(640,360,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    clean=QImage(decoded.size(),QImage.Format.Format_RGB32);clean.fill(Qt.GlobalColor.black)
    painter=QPainter(clean);painter.drawImage(0,0,decoded);painter.end()
    output=QBuffer();output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not clean.save(output,'PNG'):raise ValueError('Image conversion failed')
    result=bytes(output.data())
    if len(result)>1024*1024:raise ValueError('Converted image too large')
    return result
