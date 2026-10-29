"""Displayed-image identity and transactional manual annotation saves."""
from contextlib import ExitStack
import json
from pathlib import Path
from annotation_labels import parse_labels, polygon_valid, MAX_BYTES,validate_manual_rows
from dataset_paths import local_path
from dataset_transaction import snapshot_bytes, HeldFile,AnnotationCommitUncertain
from dataset_storage import dataset_lock, recover_annotations, commit_annotations


def optional(path,maximum=MAX_BYTES):
    return snapshot_bytes(path,maximum=maximum) if path.exists() else (None,b'')


class ReviewDocument:
    def __init__(self,root,image):
        self.root=local_path(root);self.image=local_path(image)
        relative=self.image.relative_to(self.root/'images')
        self.label=self.root/'labels'/relative.with_suffix('.txt')
        self.classes=self.root/'classes.txt';self.review=self.root/'.vaporlock-review.json'
        self.needs_refresh=False
        with dataset_lock(self.root):
            recover_annotations(self.root)
            self.image_snapshot,self.image_bytes=snapshot_bytes(self.image,maximum=128*1024*1024)
            self.label_snapshot,self.label_bytes=optional(self.label)
            self.class_snapshot,self.class_bytes=optional(self.classes)
            self.rows=parse_labels(self.label_bytes)
            validate_manual_rows(self.rows)
            self.names=self.class_bytes.decode('utf-8-sig').splitlines()
            highest=max((r['class_id'] for r in self.rows),default=0)
            while len(self.names)<=highest:self.names.append(f'Class {len(self.names)}')
            self.saved_class_bytes=('\n'.join(self.names)+'\n').encode('utf-8') if not self.class_bytes or highest>=len(self.class_bytes.decode('utf-8-sig').splitlines()) else self.class_bytes

    def save(self,data,authorize=lambda:False):
        if self.needs_refresh:raise ValueError('Refresh and recover this dataset before editing.')
        rows=parse_labels(data)
        validate_manual_rows(rows)
        if any(r['class_id']>=len(self.names) for r in rows):
            raise ValueError('Choose a class from the dataset class map before saving.')
        with dataset_lock(self.root),ExitStack() as stack:
            recover_annotations(self.root)
            from annotation_output import held_directory
            stack.enter_context(held_directory(self.image.parent))
            if stack.enter_context(HeldFile(self.image,read_only=True)).snapshot()!=self.image_snapshot:
                raise ValueError('The displayed image changed. Refresh before saving.')
            # Include classes in the transaction even unchanged, so absent/present
            # state and exact identity are protected through label publication.
            review_snapshot,review_bytes=optional(self.review)
            review=json.loads(review_bytes.decode('utf-8')) if review_bytes else {}
            if type(review) is not dict or type(review.get('decisions',{})) is not dict:
                raise ValueError('Invalid review decisions; existing files were preserved.')
            review.setdefault('decisions',{}).pop(self.image.relative_to(self.root).as_posix(),None)
            self.label.parent.mkdir(parents=True,exist_ok=True)
            if not authorize():raise ValueError('Annotation editing is no longer authorized.')
            try:
                result=commit_annotations(self.root,{self.label:data.decode('utf-8'),
                    self.classes:self.saved_class_bytes.decode('utf-8'),self.review:json.dumps(review,ensure_ascii=True)},
                    expected_updates={self.label:self.label_snapshot,self.classes:self.class_snapshot,self.review:review_snapshot})
            except AnnotationCommitUncertain:
                self.needs_refresh=True;raise
            self.rows=rows
            if result['cleanup_pending']:
                self.label_bytes=data;self.needs_refresh=True
                return result
            self.label_snapshot,self.label_bytes=optional(self.label)
            self.class_snapshot,self.class_bytes=optional(self.classes)
            self.rows=rows
            return result


def to_annotations(rows):
    from PySide6.QtCore import QPointF
    result=[]
    for row in rows:
        x,y,x2,y2=row['box']
        item=({'kind':'polygon','points':[QPointF(*p) for p in row['points']]} if row['points'] else
              {'kind':'box','x':x,'y':y,'w':x2-x,'h':y2-y})
        item['class_id']=row['class_id'];item['_original_row']=row['row']
        item['_original_geometry']=geometry(item);result.append(item)
    return result


def geometry(item):
    return (item.get('class_id'),item.get('kind'),tuple((p.x(),p.y()) for p in item.get('points',[])),
        *(item.get(k) for k in ('x','y','w','h')))


def serialize(annotations):
    rows=[]
    for item in annotations:
        if geometry(item)==item.get('_original_geometry') and '_original_row' in item:
            rows.append(item['_original_row']);continue
        class_id=item.get('class_id')
        if type(class_id) is not int or not 0<=class_id<=999999:raise ValueError('Invalid annotation class.')
        if item.get('kind')=='polygon':
            points=[(p.x(),p.y()) for p in item['points']]
            rounded=[(round(x,6),round(y,6)) for x,y in points]
            if not polygon_valid(points) or not polygon_valid(rounded):raise ValueError('Polygon needs distinct, non-crossing vertices and visible area.')
            coordinates=' '.join(f'{x:.6f} {y:.6f}' for x,y in rounded)
        elif item.get('kind')=='box':
            x,y,w,h=(item[k] for k in ('x','y','w','h'))
            coordinates=f'{x+w/2:.6f} {y+h/2:.6f} {w:.6f} {h:.6f}'
        else:raise ValueError('Unsupported annotation geometry.')
        rows.append(f'{class_id} {coordinates}')
    data=('\n'.join(rows)+ ('\n' if rows else '')).encode('utf-8');parse_labels(data)
    return data


def allowed(center):
    from batch4_guard import editable
    return (not False and not getattr(getattr(center,'_review_document',None),'needs_refresh',False)
        and editable(center,stopped=True) and True)


def show(center,row):
    from PySide6.QtGui import QImage
    canvas=center.dataset_review_preview
    if not 0<=row<len(center.dataset_review_records):return
    if canvas.has_draft():
        # Do not silently discard a drawing when selecting another queue item.
        center.dataset_review_list.blockSignals(True)
        center.dataset_review_list.setCurrentRow(getattr(center,'_review_loaded_row',row))
        center.dataset_review_list.blockSignals(False)
        canvas.notice('Finish or cancel the current polygon edit first.');return
    try:
        document=ReviewDocument(center.dataset_root_for_tools(),center.dataset_review_records[row])
        image=QImage.fromData(document.image_bytes)
        if image.isNull():raise ValueError('The image could not be opened.')
        center._review_document=document;center._review_loaded_row=row
        center._review_stamp=(center._active_profile_path,id(center.settings),center._tuning_epoch,
                              0)
        canvas.set_review_image(image,to_annotations(document.rows));canvas.notice('Labels loaded. Edits save after a valid finish.')
    except (OSError,ValueError,UnicodeError):
        center._review_document=None;canvas.set_review_image(QImage(),[])
        canvas.notice('Image or labels are invalid or busy. Refresh before editing; files were preserved.')


def save(center,annotations):
    document=getattr(center,'_review_document',None);canvas=center.dataset_review_preview
    def current():
        return document is getattr(center,'_review_document',None) and allowed(center) and (
            center._active_profile_path,id(center.settings),center._tuning_epoch,0)==center._review_stamp and (
            document.root==local_path(center.dataset_root_for_tools()))
    try:
        if document is None or not current():raise ValueError('Editing is unavailable. Refresh before saving.')
        result=document.save(serialize(annotations),current)
        canvas.accept_saved(to_annotations(document.rows))
        canvas.notice('Labels saved. Cleanup is pending; refresh before editing again.' if result['cleanup_pending'] else 'Labels saved.')
        center.dataset_review_decisions.pop(document.image.relative_to(document.root).as_posix(),None)
        return True
    except AnnotationCommitUncertain:
        canvas.restore_saved();canvas.notice('Save outcome is uncertain. Refresh to recover before editing again.')
        return False
    except (OSError,ValueError,UnicodeError):
        canvas.restore_saved();canvas.notice('Save failed. Refresh and check the dataset before retrying.')
        return False
