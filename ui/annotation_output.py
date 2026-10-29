"""Protected post-annotation overwrites and explicitly requested extra copies."""
from contextlib import ExitStack, contextmanager
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import uuid

from PIL import Image
from annotation_variations import VariationOptions, vary_image, effect_seed, MAX_PIXELS
from dataset_paths import reject_reparse

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024**3
MAX_IMAGES = 10_000
MAX_LABEL_BYTES = 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plain(path):
    return reject_reparse(path)


@contextmanager
def held_directory(path):
    path = plain(path)
    if not path.is_dir():
        raise ValueError('Annotation output parent must be an existing folder.')
    with ExitStack() as stack:
        if os.name == 'nt':
            from dataset_transaction import HeldFile
            for parent in reversed((path, *path.parents)):
                stack.enter_context(HeldFile(parent, directory=True))
        yield path


def read_bytes(path, maximum=MAX_IMAGE_BYTES):
    if os.name == 'nt':
        from dataset_transaction import snapshot_bytes
        return snapshot_bytes(plain(path), maximum=maximum)[1]
    path = plain(path)
    if path.stat().st_size > maximum or path.stat().st_nlink != 1:
        raise ValueError('Annotation input exceeds its file limit or is hard linked.')
    return path.read_bytes()


def write_new(path, data):
    path = plain(path)
    with held_directory(path.parent):
        with path.open('xb') as output:
            if output.write(data) != len(data):
                raise OSError('Annotation output write was incomplete.')
            output.flush()
            os.fsync(output.fileno())


def decode_image(data):
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError('Image exceeds the annotation byte limit.')
    with Image.open(BytesIO(data)) as image:
        if min(image.size) < 1 or image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('Unsupported image or image exceeds the annotation pixel limit.')
        # Match the current annotator's stored-pixel coordinate system. Never
        # transpose EXIF orientation or reinterpret the accepted coordinates.
        return image.convert('RGB').copy()


def parse_labels(data, classes, task=None):
    if type(data) is not bytes or len(data) > MAX_LABEL_BYTES:
        raise ValueError('Annotation labels exceed the supported size.')
    output = []
    for row in data.decode('utf-8', errors='strict').splitlines():
        values = row.split()
        if not values:
            continue
        if len(values) > 10001 or len(output) >= 10000 or not values[0].isdigit():
            raise ValueError('Invalid annotation label.')
        class_id = int(values[0]); coords = [float(v) for v in values[1:]]
        if not 0 <= class_id < classes or any(not math.isfinite(v) or not 0 <= v <= 1 for v in coords):
            raise ValueError('Invalid annotation class or coordinate.')
        if len(coords) == 4 and task != 'segment':
            cx, cy, w, h = coords
            if min(w, h) <= 0 or cx - w/2 < -1e-5 or cy - h/2 < -1e-5 or cx + w/2 > 1.00001 or cy + h/2 > 1.00001:
                raise ValueError('Annotation box is outside the image.')
            output.append({'classId': class_id, 'bbox': coords})
        elif len(coords) >= 6 and len(coords) % 2 == 0 and task != 'detect':
            output.append({'classId': class_id, 'segments': coords})
        else:
            raise ValueError('Annotation shape does not match the dataset task.')
    return output


def encode_replacement(source_bytes, varied, extension):
    """Shared final encoder for representative preview and actual overwrite."""
    with Image.open(BytesIO(source_bytes)) as source:
        orientation=source.getexif().get(274,1);size=source.size
    if type(orientation) is not int or not 1<=orientation<=8:
        raise ValueError('Invalid source image orientation.')
    fmt={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.bmp':'BMP','.webp':'WEBP'}[extension]
    kwargs={}
    if fmt in ('JPEG','WEBP'):kwargs['quality']=95
    if fmt in ('JPEG','PNG','WEBP') and orientation!=1:
        exif=Image.Exif();exif[274]=orientation;kwargs['exif']=exif.tobytes()
    stream=BytesIO();varied.save(stream,format=fmt,**kwargs);data=stream.getvalue()
    with Image.open(BytesIO(data)) as check:
        check.load()
        if check.format!=fmt or check.size!=size or check.getexif().get(274,1)!=orientation:
            raise ValueError('Encoded effects changed image geometry, format or orientation.')
    if len(data)>MAX_IMAGE_BYTES:raise ValueError('Encoded effects exceed the image byte limit.')
    return data


def new_output(parent, prefix):
    parent = plain(parent)
    with held_directory(parent):
        root = parent / (prefix + '-' + uuid.uuid4().hex)
        root.mkdir(exist_ok=False)
        with held_directory(root):
            (root / 'images').mkdir()
            (root / 'labels').mkdir()
            write_new(root / '.vaporlock-incomplete', b'Incomplete output; do not use until result.json exists.\n')
    return root


def _expected_sources(sources, expected_context):
    if expected_context is not None and {p.name for p in sources}!=set(expected_context):
        raise ValueError('Accepted annotation sources changed before image effects.')


def _bound_source(source, label, expected_context):
    from dataset_transaction import snapshot_bytes
    image_snapshot,image_bytes=snapshot_bytes(source,maximum=MAX_IMAGE_BYTES)
    label_snapshot,label_bytes=snapshot_bytes(label,maximum=MAX_LABEL_BYTES)
    if expected_context is not None:
        expected=expected_context[source.name]
        if image_snapshot!=expected['image'] or digest(label_bytes)!=expected['labelSHA256']:
            raise ValueError('Accepted annotation bytes changed before image effects.')
    return image_snapshot,image_bytes,label_snapshot,label_bytes


def generate_variations(root, options: VariationOptions, *, seed=None, eligible_images=None,
        expected_context=None, expected_classes_sha256=None, return_receipt=False, emit=lambda *_a, **_kw: None):
    options.validate()
    if return_receipt and (not options.enabled or not options.extra_copies):
        raise ValueError('Producer receipts require explicit extra-copy effects.')
    if not options.enabled:
        return None
    if not options.extra_copies:
        return overwrite_images(root, options, seed=seed, eligible_images=eligible_images,
            expected_context=expected_context,expected_classes_sha256=expected_classes_sha256,emit=emit)
    root = plain(root)
    seed = secrets.randbits(64) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('Invalid variation seed.')
    class_bytes=read_bytes(root/'classes.txt',MAX_LABEL_BYTES)
    if expected_classes_sha256 is not None and digest(class_bytes)!=expected_classes_sha256:
        raise ValueError('Accepted annotation class map changed before image effects.')
    names = class_bytes.decode('utf-8').splitlines()
    sources = sorted(p for p in (root / 'images').iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.bmp', '.webp'))
    if eligible_images is not None:sources=[p for p in sources if p.name in eligible_images]
    if len(sources) > MAX_IMAGES:
        raise ValueError('Too many source images for one variation job.')
    _expected_sources(sources,expected_context)
    output = new_output(root, 'vaporlock-variations')
    records = []; total_bytes = 0; source_count = 0
    with held_directory(output):
        for index, source in enumerate(sources):
            label = root / 'labels' / (source.stem + '.txt')
            if not label.is_file():
                if expected_context is not None:raise ValueError('Accepted annotation labels are missing.')
                continue
            _snapshot,source_bytes,_label_snapshot,labels=_bound_source(source,label,expected_context)
            if not parse_labels(labels, len(names)):
                continue
            image = decode_image(source_bytes)
            source_count += 1
            base = f'source-{index:06d}'
            copies = []
            for number in range(options.copies):
                image_seed = effect_seed(seed, source.name, number)
                variant, effects = vary_image(image, options, image_seed)
                stream = BytesIO(); variant.save(stream, format='PNG')
                copies.append((f'{base}-variation-{number+1:02d}.png', stream.getvalue(), effects, image_seed, False))
            for name, data, effects, image_seed, original in copies:
                total_bytes += len(data) + len(labels)
                if total_bytes > MAX_OUTPUT_BYTES:
                    raise ValueError('Variation output exceeds the 8 GiB job limit.')
                image_path = output / 'images' / name
                label_path = output / 'labels' / (Path(name).stem + '.txt')
                write_new(label_path, labels)
                write_new(image_path, data)
                records.append({'image': 'images/' + name, 'label': 'labels/' + label_path.name,
                    'imageSHA256': digest(data), 'labelSHA256': digest(labels), 'sourceImage': source.name,
                    'sourceSHA256': digest(source_bytes), 'seed': image_seed, 'effects': effects,
                    'original': original, 'width': image.width, 'height': image.height})
            emit('variation_progress', current=source_count, generated=source_count * options.copies)
        write_new(output / 'classes.txt', ('\n'.join(names)+'\n').encode('utf-8'))
        manifest_bytes=json.dumps({'schema': 1, 'complete': True, 'seed': seed,
            'sources': source_count, 'generated': source_count * options.copies, 'bytes': total_bytes,
            'files': records}, ensure_ascii=False, indent=2).encode('utf-8')
        write_new(output / 'result.json',manifest_bytes)
    emit('variations_ready', folder=str(output), sources=source_count, generated=source_count * options.copies)
    # The cloud consumer keeps these immutable producer bytes. A mutable disk
    # manifest cannot supply new hashes for substituted images or labels.
    return (output,manifest_bytes) if return_receipt else output


def overwrite_images(root, options, *, seed=None, eligible_images=None, expected_context=None,
        expected_classes_sha256=None, emit=lambda *_a, **_kw: None, checkpoint=lambda _stage: None):
    """Replace encoded pixels only after their accepted labels are committed."""
    from dataset_storage import dataset_lock, recover_annotations
    from dataset_transaction import HeldFile, snapshot_bytes, snapshot_file, commit_transaction
    options.validate()
    if not options.enabled:
        return None
    if os.name != 'nt':
        raise OSError('Image overwrite requires Windows file identity protection.')
    root = plain(root)
    seed = secrets.randbits(64) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('Invalid image effect seed.')
    with dataset_lock(root):
        recover_annotations(root)
        paths = sorted(p for p in (root/'images').iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png','.bmp','.webp'))
        if eligible_images is not None:paths=[p for p in paths if p.name in eligible_images]
        if len(paths)>MAX_IMAGES:
            raise ValueError('Too many images for one effects job.')
        _expected_sources(paths,expected_context)
        staging=new_output(root,'vaporlock-effects');replacements=[];records=[];total_bytes=0
        with held_directory(staging), HeldFile(root/'classes.txt') as classes_file:
            class_info=classes_file.info()
            if ((class_info.size_high<<32)|class_info.size_low) > MAX_LABEL_BYTES:
                raise ValueError('Annotation class map exceeds the supported size.')
            class_bytes=b''.join(classes_file.blocks())
            if expected_classes_sha256 is not None and digest(class_bytes)!=expected_classes_sha256:
                raise ValueError('Accepted annotation class map changed before image effects.')
            names = class_bytes.decode('utf-8').splitlines()
            for index,path in enumerate(paths):
                label=root/'labels'/(path.stem+'.txt')
                if not label.is_file():
                    if expected_context is not None:raise ValueError('Accepted annotation labels are missing.')
                    continue
                image_snapshot,image_bytes,label_snapshot,label_bytes=_bound_source(path,label,expected_context)
                if not parse_labels(label_bytes,len(names)):continue
                image=decode_image(image_bytes)
                image_seed=effect_seed(seed,path.name,overwrite=True)
                varied,effects=vary_image(image,options,image_seed)
                data=encode_replacement(image_bytes,varied,path.suffix.lower())
                total_bytes+=len(data)
                if len(data)>MAX_IMAGE_BYTES or total_bytes>MAX_OUTPUT_BYTES:
                    raise ValueError('Image effects exceed the output limit.')
                produced_hash=digest(data);produced_size=len(data)
                staged=staging/'images'/path.name;write_new(staged,data)
                replacement_snapshot=snapshot_file(staged)
                if (replacement_snapshot['sha256']!=produced_hash
                        or replacement_snapshot['size']!=produced_size):
                    raise ValueError('Encoded image staging changed before publication.')
                replacements.append({'image':path,'label':label,'image_snapshot':image_snapshot,
                    'label_snapshot':label_snapshot,'replacement':staged,'replacement_snapshot':replacement_snapshot})
                records.append({'image':path.name,'beforeSHA256':digest(image_bytes),'afterSHA256':digest(data),
                    'labelSHA256':digest(label_bytes),'seed':image_seed,'effects':effects})
                emit('variation_progress',current=len(replacements),generated=0,mode='overwrite')
            if not replacements:
                # An empty pass creates no image copies or encoded outputs.
                emit('effects_complete',replaced=0,generated=0,cleanup_pending=False)
                return None
            checkpoint('effects-staged')
            result=commit_transaction(root,{},replacements=replacements,checkpoint=checkpoint)
            cleanup_pending=result['cleanup_pending'];report_pending=False
            # Temporary encoded files are internal staging, not extra dataset
            # images. Delete only the exact identities that this job created,
            # and only after the replacement commit is durably confirmed.
            for item in replacements:
                try:
                    with HeldFile(item['replacement']) as temporary:
                        if temporary.snapshot()!=item['replacement_snapshot']:
                            cleanup_pending=True
                            continue
                        temporary.delete()
                except OSError:
                    cleanup_pending=True
            try:
                write_new(staging/'result.json',json.dumps({'schema':1,'mode':'overwrite','complete':True,
                    'seed':seed,'replaced':len(replacements),'generated':0,'cleanup_pending':cleanup_pending,'files':records},indent=2).encode())
            except OSError:
                report_pending=True
            # A report/cleanup failure cannot undo or hide committed pixels.
            emit('effects_complete',replaced=len(replacements),generated=0,cleanup_pending=cleanup_pending,
                report_pending=report_pending,report=str(staging/'result.json'))
        return staging
