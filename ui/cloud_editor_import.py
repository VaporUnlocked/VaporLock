"""Candidate parent import transaction. Original files are never written/deleted."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets

from cloud_annotation_protocol import event
from cloud_contract import CloudInputError, MAX_IMAGE_BYTES
from cloud_editor_contract import (MAX_IMAGE_TOTAL, TOKEN, encoded, image_record, manifest)
from cloud_editor_store import CommitUncertain


class ImageImportUncertain(CommitUncertain):
    def __init__(self,record,revision,operation,png_sha256,receipt):
        super().__init__('Image import needs reconciliation. Its owned image and receipt were retained.')
        self.record=deepcopy(record);self.revision=revision;self.operation=operation
        self.png_sha256=png_sha256;self.receipt=receipt


def _open_receipt(path):return path.open('xb')


@contextmanager
def _receipt(path,commit_state,outcome):
    stream=_open_receipt(path);primary=None
    try:yield stream
    except BaseException as exc:primary=exc;raise
    finally:
        try:stream.close()
        except BaseException as cleanup:
            if isinstance(primary,ImageImportUncertain):
                primary.add_note('Import receipt close also failed: '+type(cleanup).__name__)
            elif commit_state[0]:raise outcome() from cleanup
            elif primary is not None:primary.add_note('Receipt cleanup also failed: '+type(cleanup).__name__)
            else:raise


def reconcile_import(store,outcome):
    if not isinstance(outcome,ImageImportUncertain):
        raise CloudInputError('There is no image import to reconcile.')
    value,revision=store.load()
    expected=dict(operation=outcome.operation,image=outcome.record['id'],file=outcome.record['file'],png_sha256=outcome.png_sha256)
    record=next((r for r in value['images'] if r['id']==outcome.record['id']),None)
    if (revision==outcome.revision+1 and value.get('_vaporlock_last_import')==expected and record==outcome.record):
        path=store.guard.hold_file(store.root/record['file'],MAX_IMAGE_BYTES)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=outcome.png_sha256:
                raise CloudInputError('Imported image changed before reconciliation.')
        return True
    if revision==outcome.revision and record is None:
        return False  # Known unreferenced owned file/receipt remain; no auto-delete.
    raise CloudInputError('Import outcome conflicts with a newer project revision. Reopen and inspect the retained import receipt.')


def append_capture(store, image, *, revision, split, group):
    """Import trusted, unscaled desktop pixels as a canonical PNG."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    from cloud_contract import MAX_IMAGE_PIXELS

    width, height = image.width(), image.height()
    if not (0 < width * height <= MAX_IMAGE_PIXELS):
        raise CloudInputError('The capture is empty or exceeds the supported image size.')
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    stride = image.bytesPerLine()
    pixels = image.constBits()
    rgb = b''.join(pixels[row:row+width*3] for row in range(0, stride*height, stride))
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, 'PNG'):
        raise CloudInputError('Could not encode the captured pixels as PNG.')
    png = bytes(buffer.data())
    result = dict(type='decoded', request=secrets.token_hex(16), capability=secrets.token_hex(16),
        width=width, height=height, pixel_sha256=hashlib.sha256(f'{width}x{height}:'.encode()+rgb).hexdigest(),
        rgb_bytes=len(rgb), rgb_sha256=hashlib.sha256(rgb).hexdigest(),
        png_bytes=len(png), png_sha256=hashlib.sha256(png).hexdigest())
    return append_image(store, result, {'png': png, 'rgb': rgb}, revision=revision, split=split, group=group)


def append_image(store,result,artifacts,*,revision,split='train',group=None,yolo_rows=()):
    """Consume verified decoder output or canonical pixels from the native desktop capture.

    Files precede DB references. A failure may leave one explicitly receipted
    orphan; retaining it is safer than deleting anything after an uncertain save.
    """
    event(result)
    if result.get('type')!='decoded' or type(artifacts) is not dict or set(artifacts)!={'png','rgb'}:
        raise CloudInputError('Import requires a verified decoded image capability.')
    for name in ('png','rgb'):
        data=artifacts[name]
        if (type(data) is not bytes or len(data)!=result[name+'_bytes']
                or hashlib.sha256(data).hexdigest()!=result[name+'_sha256']):
            raise CloudInputError('Import artifact bytes changed after verification.')
    pixel_sha=hashlib.sha256(f"{result['width']}x{result['height']}:".encode()+artifacts['rgb']).hexdigest()
    if pixel_sha!=result['pixel_sha256']:
        raise CloudInputError('Import pixel identity changed after verification.')
    value,actual=store.load()
    if actual!=revision:raise CloudInputError('Project changed before image import. Reopen it before continuing.')
    duplicate=next((r for r in value['images'] if r.get('pixel_sha256')==pixel_sha),None)
    if duplicate is not None:
        return dict(record=deepcopy(duplicate),revision=revision,duplicate=True)
    identity=secrets.token_hex(16);operation=secrets.token_hex(16)
    record=dict(id=identity,file='images/image-'+identity+'.png',width=result['width'],height=result['height'],
        pixel_sha256=pixel_sha,split=split,group_id=group or identity,reviewed=False,annotations=[],
        added_at=datetime.now(timezone.utc).isoformat())
    width,height=record['width'],record['height']
    for row in yolo_rows:
        box=[min(1.,max(0.,value))*scale for value,scale in zip(row['box'],(width,height,width,height))]
        label=dict(id=secrets.token_hex(16),class_id=0,source='yolo',box=box)
        if row['points']:
            label['wrapper']=[[x*width,y*height] for x,y in row['points']]
        record['annotations'].append(label)
    image_record(record);value['images'].append(record);manifest(value)
    images=store.guard._directory(store.root/'images')
    total=0
    for old in value['images'][:-1]:
        path=store.guard.hold_file(store.root/old['file'],MAX_IMAGE_BYTES)
        total+=path.stat().st_size
        if total+len(artifacts['png'])>MAX_IMAGE_TOTAL:
            raise CloudInputError('Project source bytes exceed the supported limit.')
    destination=images/Path(record['file']).name
    receipt=store.root/('.cloud-import-'+operation+'.jsonl')
    commit_state=[False]
    outcome=lambda:ImageImportUncertain(record,revision,operation,result['png_sha256'],receipt)
    with _receipt(receipt,commit_state,outcome) as journal:
        journal.write(encoded(dict(schema_version=1,operation=operation,image=identity,
            file=record['file'],png_sha256=result['png_sha256'],expected_revision=revision))+b'\n')
        journal.flush();os.fsync(journal.fileno())
        with destination.open('xb') as output:
            output.write(artifacts['png']);output.flush();os.fsync(output.fileno())
        published=store.guard.hold_file(destination,MAX_IMAGE_BYTES)
        with published.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=result['png_sha256']:
                raise CloudInputError('New owned image failed verification; its import receipt was preserved.')
        try:
            with store.connection() as db:
                try:
                    db.execute('BEGIN IMMEDIATE');current,actual=store._snapshot(db)
                    if actual!=revision or actual>=2**53-1:
                        raise CloudInputError('Project changed during import; the unreferenced owned image was retained with its receipt.')
                    current['images'].append(record);manifest(current)
                    ordinal=db.execute('SELECT coalesce(max(ordinal),-1)+1 FROM images').fetchone()[0]
                    db.execute('INSERT INTO images VALUES(?,?,?)',(identity,ordinal,encoded(record).decode('utf-8')))
                    metadata={k:v for k,v in current.items() if k!='images'}
                    metadata['_vaporlock_last_import']=dict(operation=operation,image=identity,file=record['file'],png_sha256=result['png_sha256'])
                    metadata['updated_at']=datetime.now(timezone.utc).isoformat()
                    db.execute('UPDATE state SET revision=?,metadata=? WHERE singleton=1',(actual+1,encoded(metadata).decode('utf-8')))
                    try:
                        commit_state[0]=True
                        db.commit()
                    except BaseException as exc:
                        raise CommitUncertain('Image import needs reconciliation.') from exc
                except BaseException as primary:
                    try:db.rollback()
                    except BaseException as cleanup:primary.add_note('Import rollback also failed: '+type(cleanup).__name__)
                    raise
        except CommitUncertain as exc:
            raise ImageImportUncertain(record,revision,operation,result['png_sha256'],receipt) from exc
        try:
            journal.write(encoded(dict(state='committed',revision=revision+1))+b'\n')
            journal.flush();os.fsync(journal.fileno())
        except BaseException as exc:
            raise ImageImportUncertain(record,revision,operation,result['png_sha256'],receipt) from exc
    return dict(record=record,revision=revision+1,duplicate=False)
