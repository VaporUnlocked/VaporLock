"""Bounded, read-only local Ultralytics CSV history. Never imports an SDK."""
import csv
import io
import math
import os
from pathlib import Path
from profile_library import checked_path, read_bounded

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10000
# Exact SDK column names; arbitrary CSV text is never a UI label or expression.
METRICS = {
    'train/box_loss': 'Training box loss', 'val/box_loss': 'Validation box loss',
    'train/cls_loss': 'Training class loss', 'val/cls_loss': 'Validation class loss',
    'train/dfl_loss': 'Training distribution loss', 'val/dfl_loss': 'Validation distribution loss',
    'train/seg_loss': 'Training mask loss', 'val/seg_loss': 'Validation mask loss',
    'metrics/precision(B)': 'Box precision', 'metrics/recall(B)': 'Box recall',
    'metrics/mAP50(B)': 'Box mAP50', 'metrics/mAP50-95(B)': 'Box mAP50–95',
    'metrics/precision(M)': 'Mask precision', 'metrics/recall(M)': 'Mask recall',
    'metrics/mAP50(M)': 'Mask mAP50', 'metrics/mAP50-95(M)': 'Mask mAP50–95',
}


def run_names(root):
    root = checked_path(root)
    if not root.exists(): return []
    names = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 4096: break
            try:
                path = checked_path(root, entry.name)
                if path.is_dir():
                    names.append(entry.name)
            except (OSError, ValueError): continue
            if len(names) >= 128: break
    return sorted(names, key=str.casefold)


def parse_csv(data):
    if type(data) is not bytes or len(data) > MAX_BYTES: raise ValueError('CURVES_TOO_LARGE')
    text = data.decode('utf-8-sig')
    # A writer may still be filling the final row. Never show it prematurely.
    partial = bool(text and not text.endswith(('\n', '\r')))
    if partial: text = text[:max(text.rfind('\n'), text.rfind('\r')) + 1]
    reader = csv.reader(io.StringIO(text), strict=True)
    header = next(reader, [])
    header = [name.strip() for name in header]
    if not header: return {'rows':[], 'metrics':(), 'partial':partial}
    if len(header) > 64 or any(len(s)>128 for s in header) or len(set(header)) != len(header) or 'epoch' not in header:
        raise ValueError('CURVES_INVALID_HEADER')
    epoch_index = header.index('epoch')
    columns = {key: header.index(key) for key in METRICS if key in header}
    rows = []
    previous = 0
    for index, row in enumerate(reader):
        if index >= MAX_ROWS: raise ValueError('CURVES_TOO_MANY_ROWS')
        if len(row) != len(header) or any(len(s)>128 for s in row): raise ValueError('CURVES_INVALID_ROW')
        epoch = float(row[epoch_index])
        if not math.isfinite(epoch) or epoch != int(epoch) or not previous < epoch <= 1000000:
            raise ValueError('CURVES_INVALID_EPOCH')
        values = {}
        for key, column in columns.items():
            try: value = float(row[column])
            except ValueError: value = math.nan
            values[key] = value if math.isfinite(value) and 0 <= value <= 1e12 else None
        rows.append((int(epoch), values)); previous = epoch
    return {'rows': rows, 'metrics': tuple(columns), 'partial': partial}


def read_run(root, name):
    path = checked_path(root, name, 'results.csv')
    data = read_bounded(path, MAX_BYTES)
    # Recheck parents after the read as well; no links or redirected roots.
    checked_path(root, name, 'results.csv')
    try: return parse_csv(data)
    except csv.Error as error: raise ValueError('CURVES_INVALID_CSV') from error
