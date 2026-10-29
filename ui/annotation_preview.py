"""Read-only, bounded image samples for native annotation confirmation.

No inference, network, credentials, dataset writes or output image copies.
Local samples show saved labels; the next annotation run decides eligibility.
"""
from collections import Counter
from io import BytesIO
import json
from pathlib import Path
import secrets

from PIL import Image, ImageDraw
from annotation_output import (MAX_IMAGES, MAX_LABEL_BYTES, decode_image, digest,
    held_directory, parse_labels, plain, read_bytes)
from annotation_variations import VariationOptions, effect_seed, vary_image
from dataset_paths import display_path

EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')


def source_signature(root):
    """Identity/stat binding, not a substitute for worker content snapshots."""
    root = plain(root); images = root / 'images'; records = []
    with held_directory(images):
        for path in sorted(images.iterdir()):
            if path.suffix.lower() not in EXTENSIONS: continue
            plain(path)
            info = path.stat()
            if not path.is_file() or info.st_nlink != 1: raise ValueError('Unsupported preview source.')
            label = plain(root / 'labels' / (path.stem + '.txt'))
            label_info = label.stat() if label.exists() else None
            records.append([path.name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                None if label_info is None else [label_info.st_dev, label_info.st_ino, label_info.st_size, label_info.st_mtime_ns]])
            if len(records) > MAX_IMAGES: raise ValueError('Preview image limit exceeded.')
        classes = read_bytes(root/'classes.txt', MAX_LABEL_BYTES) if (root/'classes.txt').exists() else b''
    return digest(json.dumps([str(root), records, digest(classes)], separators=(',', ':')).encode())


def _sample(name, data, labels, names, options, seed):
    original = decode_image(data)
    changed, effects = vary_image(original, options, effect_seed(seed, name, overwrite=not options.extra_copies))
    # Show the same final encoding used for overwrite, including JPEG effects.
    if options.enabled and not options.extra_copies:
        from annotation_output import encode_replacement
        changed = decode_image(encode_replacement(data, changed, Path(name).suffix.lower()))
    def thumbnail(image):
        image = image.copy(); image.thumbnail((480, 300))
        draw = ImageDraw.Draw(image); width, height = image.size
        for item in labels:
            if 'bbox' in item:
                cx,cy,w,h=item['bbox']; shape=((cx-w/2)*width,(cy-h/2)*height,(cx+w/2)*width,(cy+h/2)*height)
                draw.rectangle(shape, outline='#44e1ed', width=2); x,y=shape[:2]
            else:
                points=list(zip(item['segments'][::2],item['segments'][1::2]))
                points=[(x*width,y*height) for x,y in points]
                draw.line(points+[points[0]],fill='#44e1ed',width=2);x,y=points[0]
            draw.text((max(0,x),max(0,y-12)),str(item['classId']),fill='#44e1ed')
        stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()
    return {'name':name,'before':thumbnail(original),'after':thumbnail(changed),
        'effects':effects,'dimensions':original.size,'labelCount':len(labels),'sourceSHA256':digest(data)}


def local_preview(root, options, *, seed=None):
    options.validate();root=plain(root)
    seed=secrets.randbits(64) if seed is None else seed
    signature=source_signature(root)
    names=read_bytes(root/'classes.txt',MAX_LABEL_BYTES).decode().splitlines() if (root/'classes.txt').exists() else []
    paths=sorted(p for p in (root/'images').iterdir() if p.suffix.lower() in EXTENSIONS)
    samples=[];unavailable=0
    for path in paths[:3]:
        try:
            label=root/'labels'/(path.stem+'.txt')
            labels=parse_labels(read_bytes(label,MAX_LABEL_BYTES),len(names)) if label.is_file() and names else []
            samples.append(_sample(path.name,read_bytes(path),labels,names,options,seed))
        except (ValueError,OSError,UnicodeError):unavailable+=1
    if source_signature(root)!=signature:raise ValueError('Preview sources changed.')
    return {'target':display_path(root),'seed':seed,'options':options,'signature':signature,'samples':samples,
        'sources':len(paths),'labels':None,'replace':len(paths) if options.enabled and not options.extra_copies else 0,
        'copies':len(paths)*options.copies if options.enabled and options.extra_copies else 0,
        'classes':names,'splits':{},'local':True,'unavailable':unavailable}


def cloud_preview(root, expected_sha256):
    root=plain(root);raw=read_bytes(root/'platform-preview.json',4*1024*1024)
    if digest(raw)!=expected_sha256:raise ValueError('Preview plan changed.')
    plan=json.loads(raw);options=VariationOptions(**{**plan['variations'],'effects':tuple(plan['variations']['effects'])}).validate()
    records=plan['records'];names=plan['target']['classNames']
    if type(records) is not list or not 1<=len(records)<=MAX_IMAGES:raise ValueError('Preview bounds exceeded.')
    from ultralytics_annotation import identifier
    samples=[];splits=Counter();classes=Counter();unavailable=0
    for record in records:
        image_id=identifier(record['id'])
        if record['image'] not in ('images/'+image_id+ext for ext in EXTENSIONS) or record['label']!='labels/'+image_id+'.txt':
            raise ValueError('Invalid preview source path.')
        data=read_bytes(root/record['label'],MAX_LABEL_BYTES)
        if digest(data)!=record['labelSHA256']:raise ValueError('Preview labels changed.')
        labels=parse_labels(data,len(names),plan['target']['task'])
        if labels:
            splits[record['split']]+=1;classes.update(item['classId'] for item in labels)
            if len(samples)+unavailable<3:
                try:
                    image_data=read_bytes(root/record['image'])
                    if digest(image_data)!=record['sourceSHA256']:raise ValueError('Preview image changed.')
                    samples.append(_sample(Path(record['image']).name,image_data,labels,names,options,plan['seed']))
                except (ValueError,OSError,UnicodeError):unavailable+=1
    return {'target':plan['target']['owner']+'/'+plan['target']['dataset'],'folder':display_path(root),
        'seed':plan['seed'],'options':options,'samples':samples,'sources':len(records),'labels':plan['annotated'],
        'replace':plan['annotated'] if options.enabled and not options.extra_copies else 0,
        'copies':plan['annotated']*options.copies if options.enabled and options.extra_copies else 0,
        'classes':[f'{names[i]}: {classes[i]}' for i in sorted(classes)],'splits':dict(splits),
        'local':False,'unavailable':unavailable,'signature':expected_sha256}
