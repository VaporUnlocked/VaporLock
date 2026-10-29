"""Candidate held, fixed-checkpoint SAM adapter. Imported only in the child."""
import hashlib
from pathlib import Path
import time

from cloud_annotation_protocol import HQ_SAM_BYTES, HQ_SAM_SHA256, command, event
from cloud_contract import CloudInputError
from cloud_editor_contract import ProposalIdentity
from cloud_project import ProjectLease


class MaskAssistant:
    def __init__(self,device):
        if device not in ('cpu','cuda'):
            raise CloudInputError('Choose CPU or NVIDIA CUDA for mask assistance.')
        self.device=device;self.assistant=self.lease=None

    def _load(self):
        if self.assistant is not None:return

        root=Path(__file__).resolve().parents[1]
        import sys
        if getattr(sys,'frozen',False):
            from cloud_production import admit
            selected=admit('sam')/'sam_hq_vit_tiny.pth'
        else:
            selected=root/'assets/cloud-annotation/sam_hq_vit_tiny.pth'
        # Fixed application-owned asset. Never accepted from the request/model.
        lease=ProjectLease(selected)
        try:
            checkpoint=lease.hold_file(selected,HQ_SAM_BYTES)
            if checkpoint.stat().st_size!=HQ_SAM_BYTES:
                raise CloudInputError('The bundled mask assistant checkpoint has the wrong size.')
            with checkpoint.open('rb') as stream:
                checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            if checksum!=HQ_SAM_SHA256:
                raise CloudInputError('The bundled mask assistant checkpoint failed its integrity check.')
            from cloud_dependencies import activate
            activate('train-cpu' if self.device=='cpu' else 'train-cuda')
            from cloud_source_adapter import enable_source
            enable_source('training')
            from cloudstudio.annotation_assist import AnnotationAssistant
            assistant=AnnotationAssistant(checkpoint_path=checkpoint,device=self.device,
                cpu_threads=2,backend='light_hq_sam')
            # One image/crop embedding and bounded prompt-refinement state.
            assistant._cache_limit=1;assistant._refinement_cache_limit=16
            self.assistant=assistant;self.lease=lease
        except BaseException:
            lease.stack.close();raise

    def propose(self,request):
        command(request);identity=ProposalIdentity.from_wire(request['identity'])
        self._load()
        from cloud_annotation_decode import decode
        image,rgb,pixel_sha=decode(request['path'])
        if image.size!=(identity.width,identity.height) or pixel_sha!=identity.pixel_sha256:
            raise CloudInputError('The image pixels changed. Reopen the image before requesting a mask.')
        # The standalone adapter is geometry-only and has no project write access.
        started=time.monotonic()
        result=self.assistant.propose(image,list(identity.box),request['points'],request['labels'],
            max_vertices=128,tolerance=1.5,crop_mode='auto')
        if result.get('mask') is None:
            raise CloudInputError('No usable mask was generated. Refine the points, adjust the selection, or draw the mask manually.')
        payload=dict(type='mask_proposed',identity=identity.wire(),mask=result['mask'],
                     device=self.assistant.device,elapsed_ms=(time.monotonic()-started)*1000)
        event(payload)
        return payload

    def close(self):
        if self.assistant is not None:
            self.assistant.clear_cache();self.assistant=None
        if self.lease is not None:
            self.lease.stack.close();self.lease=None
