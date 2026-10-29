"""Pure annotation draft/approval state; no UI, image decoder or model imports."""
from __future__ import annotations

from copy import deepcopy
import secrets

from cloud_contract import CloudInputError
from cloud_editor_contract import ProposalIdentity, annotation, digest, dimensions, image_record
from cloud_editor_store import CommitUncertain


class AnnotationSession:
    def __init__(self, store):
        self.store = store; self.session = secrets.token_hex(16)
        self.manifest, self.revision = store.load()
        self.cursor = 0; self.generation = 0; self.pending = {}; self.failures = {}
        self.target_generations = {}
        self.undo_stack = []; self.draft = None; self.original = None
        self.saving = False; self.uncertain = None; self.closed = False
        self.explicit_negative = False
        self._read_current()

    def _read_current(self):
        self.generation += 1; self.pending.clear(); self.failures.clear(); self.undo_stack.clear()
        self.explicit_negative = False; self.target_generations.clear()
        rows = self.manifest['images']
        self.original = deepcopy(rows[self.cursor]) if rows else None
        self.draft = deepcopy(self.original)
        if self.original:
            self.explicit_negative = bool(self.original['reviewed'] and not self.original['annotations'])

    @property
    def dirty(self):
        return self.draft != self.original or bool(self.explicit_negative and self.original and not self.original['reviewed'])

    def _editable(self):
        if self.closed or self.saving or self.uncertain:
            raise CloudInputError('Finish or reconcile the current save before editing.')
        if self.draft is None:
            raise CloudInputError('Import an image before annotating.')

    def _changed(self, touched=None, *, record_undo=True):
        self._editable()
        # Bounded undo bytes, not merely a count of potentially huge masks.
        from cloud_editor_contract import encoded
        if record_undo:
            snapshot = (deepcopy(self.draft), self.explicit_negative,
                        deepcopy(self.failures), tuple(self.pending), touched)
            self.undo_stack.append(snapshot)
        while len(self.undo_stack) > 16 or sum(len(encoded(v[0])) for v in self.undo_stack) > 8 * 1024 ** 2:
            self.undo_stack.pop(0)
        self.generation += 1
        if touched is None:
            self.pending.clear(); self.failures.clear(); self.target_generations.clear()
        else:
            self.pending.pop(touched, None); self.failures.pop(touched, None)
            self.target_generations[touched] = self.generation
        self.draft['reviewed'] = False; self.explicit_negative = False

    def add_box(self, box):
        self._editable()
        if len(self.draft['annotations']) >= 300:
            raise CloudInputError('One image supports at most 300 targets.')
        value = dict(id=secrets.token_hex(16), class_id=0, box=list(box), source='manual')
        annotation(value, *dimensions(self.draft), complete=False)
        self._changed(value['id']); self.draft['annotations'].append(value)
        return value['id']

    def target(self, identity):
        value = next((a for a in self.draft['annotations'] if a['id'] == identity), None)
        if value is None:
            raise CloudInputError('Select an annotation first.')
        return value

    def set_mask(self, identity, mask, *, record_undo=True):
        self._editable()
        value = deepcopy(self.target(identity)); value['mask'] = deepcopy(mask); value.pop('wrapper', None)
        annotation(value, *dimensions(self.draft), complete=True)
        self._changed(identity, record_undo=record_undo)
        index = next(i for i, a in enumerate(self.draft['annotations']) if a['id'] == identity)
        self.draft['annotations'][index] = value

    def remove(self, identity):
        self._editable(); self.target(identity); self._changed(identity)
        self.draft['annotations'] = [a for a in self.draft['annotations'] if a['id'] != identity]
        # No implicit negative when the final target is deleted.

    def negative(self):
        self._editable()
        if self.draft['annotations']:
            raise CloudInputError('Remove targets before explicitly marking no targets.')
        self._changed(); self.explicit_negative = True

    def undo(self):
        self._editable()
        if not self.undo_stack:
            return
        snapshot, negative, failures, interrupted, touched = self.undo_stack.pop()
        self.generation += 1
        if touched is None:
            self.draft=snapshot;self.explicit_negative=negative;self.failures=failures
            self.pending.clear();self.target_generations.clear()
        else:
            # Undo one user action, preserving unrelated asynchronous masks.
            previous=next((a for a in snapshot['annotations'] if a['id']==touched),None)
            current=self.draft['annotations']
            index=next((i for i,a in enumerate(current) if a['id']==touched),None)
            if index is not None:current.pop(index)
            if previous is not None:
                old_index=next(i for i,a in enumerate(snapshot['annotations']) if a['id']==touched)
                current.insert(min(old_index,len(current)),previous)
            self.pending.pop(touched,None);self.target_generations.pop(touched,None)
            self.failures.pop(touched,None)
            if touched in failures:self.failures[touched]=failures[touched]
            interrupted=(touched,) if touched in interrupted else ()
            self.explicit_negative=negative and not current
            self.draft['reviewed']=False
            comparison=deepcopy(self.draft);comparison['reviewed']=self.original['reviewed']
            if comparison==self.original and not self.pending and not self.failures:
                self.draft['reviewed']=self.original['reviewed']
        for identity in interrupted:
            self.failures[identity] = 'Undo cancelled an unfinished mask. Regenerate it before approval.'

    def request(self, identity, checkpoint_sha256, *, record_undo=True):
        self._editable(); value = self.target(identity)
        if not self.draft.get('pixel_sha256'):
            raise CloudInputError('Verify the image pixels before requesting a mask.')
        # A refinement is a new edit generation. Never let a superseded reply win.
        self._changed(identity,record_undo=record_undo)
        proposal = ProposalIdentity(self.session, self.store.project_id, self.revision,
            self.draft['id'], self.draft['pixel_sha256'], *dimensions(self.draft),
            identity, tuple(value['box']), self.generation, secrets.token_hex(16),
            'light_hq_sam', checkpoint_sha256)
        self.pending[identity] = proposal
        return proposal

    def accepts(self, proposal):
        return (not self.closed and not self.saving and self.uncertain is None and self.draft is not None
                and self.pending.get(proposal.annotation) == proposal
                and proposal.session == self.session and proposal.project == self.store.project_id
                and proposal.revision == self.revision and proposal.image == self.draft['id']
                and proposal.pixel_sha256 == self.draft.get('pixel_sha256')
                and (proposal.width, proposal.height) == dimensions(self.draft)
                and proposal.generation == self.target_generations.get(proposal.annotation)
                and proposal.box == tuple(self.target(proposal.annotation)['box']))

    def result(self, proposal, mask=None, error=''):
        if not self.accepts(proposal):
            return False
        self.pending.pop(proposal.annotation)
        if error or mask is None:
            self.failures[proposal.annotation] = str(error or 'No mask was generated.')[:2000]
            return True
        try:
            self.set_mask(proposal.annotation, mask,record_undo=False)
        except CloudInputError as exc:
            self.failures[proposal.annotation] = str(exc)
        return True

    def cancel(self):
        self.generation += 1
        for identity in self.pending:
            self.failures[identity] = 'Mask request cancelled. Regenerate or deliberately undo the refinement before approval.'
        self.pending.clear(); self.target_generations.clear()
        # Missing masks still block approval after cancellation.

    def approval_reason(self):
        if self.closed: return 'Project editor is closed.'
        if self.saving: return 'Saving review…'
        if self.uncertain: return 'Reconcile the previous save before continuing.'
        if self.draft is None: return 'Import an image before annotating.'
        if self.pending: return 'Wait for the mask or cancel the request.'
        if self.failures: return 'Correct the failed mask before approving.'
        if not self.draft['annotations'] and not self.explicit_negative:
            return 'Explicitly mark this image as having no targets before approving a negative.'
        try:
            proposed = deepcopy(self.draft); proposed['reviewed'] = True
            image_record(proposed)
        except CloudInputError as exc:
            return str(exc)
        return ''

    def approve(self):
        reason = self.approval_reason()
        if reason:
            raise CloudInputError(reason)
        record = deepcopy(self.draft); record['reviewed'] = True
        operation = secrets.token_hex(16); self.saving = True
        try:
            revision = self.store.commit(record, revision=self.revision,
                previous_digest=digest(self.original), operation=operation)
        except CommitUncertain:
            self.uncertain = (operation, record, self.revision, True)
            raise
        finally:
            self.saving = False
        return self._ack(record, revision)

    def _ack(self, record, revision, advance=True):
        self.manifest['images'][self.cursor] = deepcopy(record); self.revision = revision
        self.uncertain = None
        at_end = self.cursor + 1 == len(self.manifest['images'])
        if advance and not at_end: self.cursor += 1
        self._read_current()
        return not at_end

    def reconcile(self):
        if self.uncertain is None:
            raise CloudInputError('There is no uncertain save to reconcile.')
        operation, record, previous, advance = self.uncertain
        revision = self.store.reconcile(operation, record, previous, digest(self.original))
        if revision is None:
            self.uncertain = None
            return False
        return self._ack(record, revision, advance)

    def navigate(self, index, *, discard=False):
        self._editable()
        if type(index) is not int or not 0 <= index < len(self.manifest['images']):
            raise CloudInputError('Image is outside the queue.')
        if self.dirty and not discard:
            raise CloudInputError('Save the draft or explicitly discard changes before leaving this image.')
        self.cancel(); self.cursor = index; self._read_current()

    def save_draft(self):
        self._editable()
        if self.pending:
            raise CloudInputError('Wait for the mask or cancel before saving the draft.')
        record = deepcopy(self.draft); record['reviewed'] = False
        operation = secrets.token_hex(16); self.saving = True
        try:
            revision = self.store.commit(record, revision=self.revision,
                previous_digest=digest(self.original), operation=operation)
        except CommitUncertain:
            # Draft reconciliation must never advance the image queue.
            self.uncertain = (operation, record, self.revision, False)
            raise
        finally:
            self.saving = False
        self.manifest['images'][self.cursor] = deepcopy(record); self.revision = revision
        self._read_current()

    def close(self, *, discard=False):
        if self.saving or self.uncertain:
            raise CloudInputError('Reconcile the save outcome before closing this project.')
        if self.dirty and not discard:
            raise CloudInputError('Save the draft or explicitly discard it before closing.')
        self.cancel(); self.closed = True; self.store.close()
