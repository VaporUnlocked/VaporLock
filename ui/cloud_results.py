"""Read metrics and verified filenames from a local Cloud Studio run."""
import math

from cloud_contract import json_value


METRICS = {
    'loss': 'Training loss', 'objectness_loss': 'Target-presence loss', 'box_loss': 'Box loss',
    'contour_loss': 'Encasing loss', 'mask_bce_loss': 'Mask foreground/background loss',
    'mask_dice_loss': 'Mask overlap loss', 'mask_boundary_loss': 'Mask boundary loss',
    'background_mask_loss': 'Background-mask loss', 'learning_rate': 'Learning rate',
    'selection_score': 'Validation selection score', 'emitted_box_f1_at_50': 'Validation box F1',
    'emitted_box_recall_at_50': 'Validation box recall', 'mask_f1_at_50': 'Validation mask F1',
    'mean_mask_iou_on_box_matches': 'Validation mask IoU', 'false_positives_per_frame': 'False positives per frame',
}

ARTIFACTS = {'best_cloud': 'best.cloud', 'last_cloud': 'last.cloud', 'checkpoint': 'last.cloudtrain'}
EVALUATION_METRICS = {
    'emitted_box_precision_at_50': 'Box precision', 'emitted_box_recall_at_50': 'Box recall',
    'emitted_box_f1_at_50': 'Box F1', 'false_positives_per_frame': 'False positives per frame',
    'mask_f1_at_50': 'Mask F1', 'mean_mask_iou_on_box_matches': 'Mask IoU on box matches',
    'mean_mask_boundary_f1_on_box_matches': 'Mask boundary F1', 'wrapper_f1_at_50': 'Encasing F1',
}


def read_run(root, name):
    from profile_library import checked_path, read_bounded
    path = checked_path(root, name, 'metrics.json')
    data = json_value(read_bounded(path, 32 * 1024 * 1024), 32 * 1024 * 1024)
    history = data.get('history') if type(data) is dict else None
    if type(history) is not list or len(history) > 10_000:
        raise ValueError('Invalid .cloud epoch history.')
    rows = []
    available = set()
    previous = 0
    for record in history:
        epoch = record.get('epoch') if type(record) is dict else None
        if type(epoch) is not int or not previous < epoch <= 10_000:
            raise ValueError('Invalid .cloud epoch number.')
        evaluation = record.get('evaluation')
        evaluation = evaluation if type(evaluation) is dict else {}
        values = {}
        for key in METRICS:
            value = record.get(key, evaluation.get(key))
            if type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value):
                values[key] = value
                available.add(key)
        rows.append((epoch, values))
        previous = epoch
    return {'rows': rows, 'metrics': tuple(key for key in METRICS if key in available), 'partial': False}


def artifacts(root, name):
    from profile_library import checked_path
    output = checked_path(root, name)
    return {key: path for key, filename in ARTIFACTS.items()
            if (path := checked_path(output, filename)).is_file()}


def cloud_run_names(root):
    from profile_library import checked_path
    from training_curves import run_names
    runs = []
    for name in run_names(root):
        try:
            if checked_path(root, name, 'run.json').is_file() or checked_path(root, name, 'config.json').is_file():
                runs.append(name)
        except (OSError, ValueError):
            continue
    return runs


def publish_model(path, library, name):
    import os
    import secrets
    import shutil
    from cloud_contract import MAX_DESCRIPTOR, MAX_WEIGHTS, inspect_model_stream
    from cloud_project import ProjectLease
    from dataset_paths import reject_reparse

    lease = ProjectLease(path)
    try:
        source = lease.hold_file(path, MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
        library = reject_reparse(library)
        library.mkdir(exist_ok=True)
        identity = secrets.token_hex(6)
        target = library / f'{name}-{identity}.cloud'
        temporary = library / f'.cloud-publish-{identity}.tmp'
        with source.open('rb') as incoming:
            inspect_model_stream(incoming, os.fstat(incoming.fileno()).st_size)
            incoming.seek(0)
            staged = False
            try:
                with temporary.open('xb') as outgoing:
                    staged = True
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.rename(temporary, target)
            finally:
                if staged:
                    temporary.unlink(missing_ok=True)
        return target
    finally:
        lease.stack.close()
