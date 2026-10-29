"""Parent-owned, single-request staging capabilities for isolated image codecs."""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import secrets

from cloud_annotation_protocol import event
from cloud_contract import CloudInputError, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from cloud_editor_contract import encoded
from cloud_project import ProjectLease
from dataset_paths import reject_reparse


class ImageCapability:
    def __init__(self,parent):
        self.request=secrets.token_hex(16);self.token=secrets.token_hex(16)
        self.guard=ProjectLease(parent);self.closed=False;self.verified=False;self.expected_files={}
        try:
            parent=self.guard._directory(reject_reparse(parent))
            # Successful capabilities are removed exactly. Retained failed or
            # unknown folders have a finite bound; never recursively clean them.
            for index,_ in enumerate(parent.iterdir()):
                if index>=15:
                    raise CloudInputError('Temporary image staging is full. Retained incomplete work needs inspection before another image can load.')
            self.root=parent/('cloud-annotation-'+self.token)
            self.root.mkdir(exist_ok=False);self.guard._directory(self.root)
            self.root_identity=self.root.stat().st_ino
            marker=dict(schema_version=1,capability=self.token,request=self.request)
            with (self.root/'capability.json').open('xb') as stream:
                stream.write(encoded(marker));stream.flush();os.fsync(stream.fileno())
            marker_path=self.guard.hold_file(self.root/'capability.json',4096)
            self.expected_files['capability.json']=(marker_path.stat().st_ino,hashlib.sha256(encoded(marker)).hexdigest())
        except BaseException:
            self.guard.stack.close();raise

    def command(self,path):
        if self.closed:raise CloudInputError('Image staging capability is closed.')
        return dict(command='decode',request=self.request,path=str(path),output=str(self.root),capability=self.token)

    def read(self,result):
        event(result)
        if (self.closed or result.get('type')!='decoded' or result['request']!=self.request
                or result['capability']!=self.token):
            raise CloudInputError('Decoded image does not belong to the active request.')
        outputs={}
        for name,key,maximum in (('image.rgb','rgb',MAX_IMAGE_PIXELS*3),('image.png','png',MAX_IMAGE_BYTES)):
            path=self.guard.hold_file(self.root/name,maximum)
            size=result[key+'_bytes']
            if path.stat().st_size!=size:
                raise CloudInputError('Decoded image artifact length disagrees with its result.')
            with path.open('rb') as stream:data=stream.read(size+1)
            if len(data)!=size or hashlib.sha256(data).hexdigest()!=result[key+'_sha256']:
                raise CloudInputError('Decoded image artifact failed its integrity check.')
            outputs[key]=data
            self.expected_files[name]=(path.stat().st_ino,result[key+'_sha256'])
        pixel_sha=hashlib.sha256(f"{result['width']}x{result['height']}:".encode()+outputs['rgb']).hexdigest()
        if pixel_sha!=result['pixel_sha256']:
            raise CloudInputError('Decoded image pixels disagree with their canonical identity.')
        self.verified=True
        return outputs

    def cleanup(self):
        """Delete only the exact three verified owned files and empty owned dir."""
        if not self.verified or set(self.expected_files)!={'capability.json','image.rgb','image.png'}:
            raise CloudInputError('Incomplete image staging was retained for inspection.')
        self.close()
        from dataset_transaction import HeldFile,_api
        for name,(identity,checksum) in self.expected_files.items():
            path=reject_reparse(self.root/name)
            with HeldFile(path) as held:
                info=held.info()
                if ((info.index_high<<32)|info.index_low)!=identity or held.snapshot()['sha256']!=checksum:
                    raise CloudInputError('Temporary image identity changed; no replacement file was removed.')
                held.delete()
        # Hold DELETE on this exact directory, reject replacement/reparse, and
        # mark only that empty identity for removal. No path-based tree delete.
        import ctypes
        api=_api();handle=api.CreateFileW(str(self.root),0x10080,3,None,3,0x02200000,None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        directory=HeldFile.__new__(HeldFile);directory.api=api;directory.path=self.root;directory.handle=handle
        try:
            info=directory.info()
            if (not info.attributes&0x10 or info.attributes&0x400
                    or ((info.index_high<<32)|info.index_low)!=self.root_identity):
                raise CloudInputError('Temporary directory identity changed; it was retained.')
            if next(self.root.iterdir(),None) is not None:
                raise CloudInputError('Temporary directory contains unknown files; it was retained.')
            directory.delete()
        finally:directory.close()

    def close(self):
        # Keep this known owned artifact for explicit bounded reconciliation.
        # Never recursively delete paths learned from the worker response.
        self.closed=True;self.guard.stack.close()
