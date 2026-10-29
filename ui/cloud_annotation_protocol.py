"""Typed, byte-bounded local annotation protocol; no model/decoder imports."""
from pathlib import Path
import re
from copy import deepcopy

from cloud_contract import CloudInputError, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, integer
from cloud_editor_contract import HASH, TOKEN, ProposalIdentity, annotation, encoded

HQ_SAM_SHA256='0f32c075ccdd870ae54db2f7630e7a0878ede5a2b06d05d6fe02c65a82fb7196'
HQ_SAM_BYTES=42526881
PROTOCOL=1
MAX_POINTS=256


def token(value):
    if type(value) is not str or not TOKEN.fullmatch(value):
        raise CloudInputError('Invalid annotation request identity.')
    return value


def sha(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise CloudInputError('Invalid annotation content digest.')
    return value


def local_file(value):
    if type(value) is not str or len(value)>32000:
        raise CloudInputError('Invalid annotation image path.')
    from dataset_paths import local_path
    return local_path(value)


def command(value):
    if type(value) is not dict:
        raise CloudInputError('Invalid annotation command.')
    kind=value.get('command')
    if kind=='close' and set(value)=={'command'}:return kind
    if kind=='decode':
        if set(value)!={'command','request','path','output','capability'}:
            raise CloudInputError('Invalid decoder request fields.')
        token(value['request']);token(value['capability']);local_file(value['path']);local_file(value['output'])
        return kind
    if kind=='mask':
        if set(value)!={'command','identity','path','points','labels'}:
            raise CloudInputError('Invalid mask request fields.')
        identity=ProposalIdentity.from_wire(value['identity']);local_file(value['path'])
        if identity.checkpoint_sha256!=HQ_SAM_SHA256:
            raise CloudInputError('The mask checkpoint identity is not the bundled assistant.')
        points,labels=value['points'],value['labels']
        if type(points) is not list or type(labels) is not list or not len(points)==len(labels)<=MAX_POINTS:
            raise CloudInputError('Too many or inconsistent mask refinement points.')
        from cloud_contract import finite
        for point,label in zip(points,labels):
            if type(point) is not list or len(point)!=2 or type(label) is not int or label not in (0,1):
                raise CloudInputError('Invalid refinement point.')
            x=finite(point[0],0,identity.width,'Refinement X');y=finite(point[1],0,identity.height,'Refinement Y')
            if x>=identity.width or y>=identity.height:
                raise CloudInputError('Refinement point leaves the image.')
            if label and not (identity.box[0]<=x<identity.box[2] and identity.box[1]<=y<identity.box[3]):
                raise CloudInputError('Foreground points must lie inside the selection box.')
        return kind
    raise CloudInputError('Unsupported annotation command.')


def event(value):
    if type(value) is not dict:
        raise CloudInputError('Invalid annotation event.')
    kind=value.get('type')
    if kind=='annotation_closed' and set(value)=={'type'}:return True
    if kind=='error' and set(value)=={'type','message'}:
        if type(value['message']) is not str or len(value['message'])>1024:
            raise CloudInputError('Invalid annotation error.')
        return True
    if kind=='annotation_ready' and set(value)=={'type','protocol','edition','pid','device'}:
        if type(value['protocol']) is not int or value['protocol']!=PROTOCOL or value['edition'] not in ('free','premium') or value['device'] not in ('cpu','cuda'):
            raise CloudInputError('Invalid annotation worker readiness.')
        integer(value['pid'],1,2**32-1,'Annotation process');return False
    if kind=='decoded' and set(value)=={'type','request','capability','width','height','pixel_sha256','rgb_bytes','rgb_sha256','png_bytes','png_sha256'}:
        token(value['request']);token(value['capability'])
        width=integer(value['width'],1,32768,'Image width');height=integer(value['height'],1,32768,'Image height')
        if width*height>MAX_IMAGE_PIXELS or value['rgb_bytes']!=width*height*3 or type(value['rgb_bytes']) is not int:
            raise CloudInputError('Decoded image exceeds its allocation limit.')
        integer(value['png_bytes'],1,MAX_IMAGE_BYTES,'Canonical PNG bytes')
        for key in ('pixel_sha256','rgb_sha256','png_sha256'):sha(value[key])
        return False
    if kind=='mask_proposed' and set(value)=={'type','identity','mask','device','elapsed_ms'}:
        identity=ProposalIdentity.from_wire(value['identity'])
        if identity.checkpoint_sha256!=HQ_SAM_SHA256 or value['device'] not in ('cpu','cuda:0'):
            raise CloudInputError('Unexpected annotation assistant identity.')
        from cloud_contract import finite
        finite(value['elapsed_ms'],0,120000,'Mask proposal time')
        annotation(dict(id=identity.annotation,class_id=0,box=list(identity.box),mask=value['mask']),
                   identity.width,identity.height,complete=True)
        encoded(value)
        return False
    if kind=='mask_failed' and set(value)=={'type','identity','message'}:
        ProposalIdentity.from_wire(value['identity'])
        if type(value['message']) is not str or not 1<=len(value['message'])<=1024:
            raise CloudInputError('Invalid mask failure.')
        return False
    if kind=='decode_failed' and set(value)=={'type','request','capability','message'}:
        token(value['request']);token(value['capability'])
        if type(value['message']) is not str or not 1<=len(value['message'])<=1024:
            raise CloudInputError('Invalid decode failure.')
        return False
    raise CloudInputError('Unknown annotation event fields.')


class AnnotationPeer:
    """Parent-only readiness/request ledger, terminal/rejection permanently latch."""
    def __init__(self,pid,edition,device):
        self.pid=pid;self.edition=edition;self.device=device
        self.ready=False;self.closing=False;self.rejected=False;self.terminal=None;self.requests={}

    def expect(self,value):
        kind=command(value)
        if kind=='close':self.closing=True;return
        if not self.ready or self.closing or self.rejected or self.terminal:
            raise CloudInputError('Annotation peer is not ready for work.')
        identity=value['identity']['request'] if kind=='mask' else value['request']
        if identity in self.requests or len(self.requests)>=2:
            raise CloudInputError('Annotation peer request budget exceeded.')
        self.requests[identity]=deepcopy(value)

    def receive(self,value):
        try:
            terminal=event(value);kind=value['type']
            if self.rejected or self.terminal:
                raise CloudInputError('Annotation peer output followed rejection or completion.')
            if kind=='error':self.terminal='error';return terminal
            if kind=='annotation_ready':
                if self.ready or value['pid']!=self.pid or value['edition']!=self.edition or value['device']!=self.device:
                    raise CloudInputError('Annotation readiness does not match its created process and build.')
                self.ready=True;return False
            if not self.ready:raise CloudInputError('Annotation result preceded readiness.')
            if kind=='annotation_closed':
                if not self.closing:raise CloudInputError('Unexpected annotation completion.')
                self.terminal=kind;return True
            request=value['identity']['request'] if kind.startswith('mask_') else value['request']
            expected=self.requests.pop(request,None)
            if expected is None:raise CloudInputError('Unrequested annotation response.')
            if kind.startswith('mask_'):
                if expected['command']!='mask' or expected['identity']!=value['identity']:
                    raise CloudInputError('Mask response differs from its admitted identity.')
            elif expected['command']!='decode' or expected['capability']!=value['capability']:
                raise CloudInputError('Decoder response differs from its admitted capability.')
            return False
        except BaseException:
            self.rejected=True;raise

    @property
    def publishable(self):return self.ready and not self.closing and not self.rejected and self.terminal is None
