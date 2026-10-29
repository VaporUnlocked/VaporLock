"""Inference-only suggestions from immutable input; never writes dataset labels."""
import hashlib,io,json,re
from pathlib import Path
import numpy as np
from PIL import Image
from assist_geometry import validate,MAX_CANDIDATES,MAX_VERTICES


def predict(session,image,args):
    from dataset_worker import input_image,decode,parse_names,instance_polygons
    from annotation_batch import batch_plan,run_pass
    inputs=session.get_inputs()
    if len(inputs)!=1 or len(inputs[0].shape)!=4:raise ValueError('Assist needs one image input.')
    meta=inputs[0];shape=meta.shape;nchw=shape[1]==3
    if not nchw and shape[3]!=3:raise ValueError('Assist needs RGB model input.')
    h,w=(shape[2],shape[3]) if nchw else (shape[1],shape[2])
    w=w if type(w) is int else 640;h=h if type(h) is int else 640
    if not 1<=w<=2048 or not 1<=h<=2048:raise ValueError('Assist model input exceeds 2048 pixels.')
    batch_plan(shape,1,w,h)
    tensor,sx,sy,px,py,sw,sh=input_image(image,w,h,nchw,meta.type)
    declared=session.get_outputs()
    if not 1<=len(declared)<=2:raise ValueError('Unsupported number of model outputs.')
    for output in declared:
        known=[v for v in output.shape if type(v) is int]
        if len(output.shape)>4 or any(v<=0 or v>65536 for v in known) or np.prod(known,dtype=object)*8>32*1024**2:
            raise ValueError('Declared model output exceeds the assistance limit.')
    outputs=run_pass(session,meta.name,[tensor],1)[0]
    if len(outputs)>2 or sum(o.nbytes for o in outputs)>32*1024**2:raise ValueError('Assist output exceeds supported size.')
    detection=outputs[0]
    if detection.ndim not in (2,3) or max(detection.shape)>65536:raise ValueError('Unsupported detection output.')
    metadata=session.get_modelmeta().custom_metadata_map
    if sum(len(str(k))+len(str(v)) for k,v in metadata.items())>65536:raise ValueError('Model metadata exceeds supported size.')
    names=parse_names(session)
    if len(names)>10000:raise ValueError('Too many model classes.')
    prototype=outputs[1] if len(outputs)==2 else None
    if prototype is None and metadata.get('task') in ('segment','segmentation'):
        raise ValueError('Segmentation model did not return instance masks.')
    channels=0
    if prototype is not None:
        if (prototype.ndim!=4 or prototype.shape[0]!=1 or not 1<=prototype.shape[1]<=256
                or not 1<=prototype.shape[2]<=512 or not 1<=prototype.shape[3]<=512):raise ValueError('Unsupported instance masks.')
        channels=prototype.shape[1]
    fmt=args.output_format;units=args.coordinate_units
    if fmt=='auto' and str(metadata.get('end2end','')).casefold() in ('true','1'):fmt='end-to-end'
    if units=='auto':units=metadata.get('coordinate_units','pixels')
    if fmt not in ('auto','yolo-class','yolo-objectness','end-to-end') or units not in ('pixels','normalized'):raise ValueError('Unsupported model geometry metadata.')
    stats={}
    items=decode(detection,names,args.confidence,args.nms_iou,sx,sy,px,py,sw,sh,w,h,channels,fmt,units,candidate_limit=512,stats=stats)
    items.sort(key=lambda a:(-a.score,a.class_id,a.box))
    truncated=stats.get('omitted',0)+max(0,len(items)-MAX_CANDIDATES);items=items[:MAX_CANDIDATES]
    if prototype is not None:instance_polygons(items,prototype,sx,sy,px,py,sw,sh,w,h,strict=True)
    candidates=[];skipped=0;vertices=0
    for a in items:
        x,y,x2,y2=a.box
        points=[[float(px/sw),float(py/sh)] for px,py in a.polygon] if prototype is not None else []
        item=dict(kind='polygon' if prototype is not None else 'box',class_id=a.class_id,confidence=a.score,
                  box=[x/sw,y/sh,(x2-x)/sw,(y2-y)/sh],points=points)
        try:
            if prototype is not None and not points:raise ValueError()
            validate([item])
            if vertices+len(points)>MAX_VERTICES:raise ValueError()
        except ValueError:skipped+=1;continue
        vertices+=len(points);candidates.append(item)
    return dict(candidates=validate(candidates),skipped=skipped,truncated=truncated)


def run(args):
    from dataset_transaction import HeldFile
    from annotation_output import held_directory
    from dataset_paths import local_path
    from annotation_provider import create_annotation_session
    from dataset_worker import emit,ort
    if (not .01<=args.confidence<=.99 or not .01<=args.nms_iou<=.99
            or not re.fullmatch('[0-9a-f]{32}',args.request_id)
            or not re.fullmatch('[0-9a-f]{64}',args.image_sha256)):raise ValueError('Invalid assistance request.')
    image_path=local_path(args.image);model=local_path(args.model)
    if model.suffix.lower()!='.onnx':raise ValueError('Assist requires a local ONNX model.')
    with held_directory(model.parent),HeldFile(model,read_only=True) as held_model,HeldFile(image_path,read_only=True) as held_image:
        info=held_model.info()
        if (info.size_high<<32)|info.size_low>1024**3:raise ValueError('Model exceeds 1 GiB.')
        info=held_image.info()
        if (info.size_high<<32)|info.size_low>32*1024**2:raise ValueError('Image exceeds 32 MiB.')
        source=b''.join(held_image.blocks());image_hash=hashlib.sha256(source).hexdigest()
        if image_hash!=args.image_sha256:raise ValueError('Image identity changed.')
        from pipeline_tools import model_identity
        from dataset_paths import display_path
        identity=model_identity(Path(display_path(model)),allow_external=False,max_bytes=1024**3,
            cancelled=lambda:(None or False))
        with Image.open(io.BytesIO(source)) as image:
            if image.width*image.height>16*1024**2 or getattr(image,'n_frames',1)!=1:raise ValueError('Assist needs a still image under 16 megapixels.')
            pixels=image.convert('RGB')
        session,backend=create_annotation_session(ort,str(model),args.provider)
        emit('provider',requested=backend.requested,selected=backend.selected,device_index=backend.device_index)
        result=predict(session,pixels,args)
        result.update(request_id=args.request_id,image_sha256=image_hash,model_sha256=identity[2],backend=backend.selected)
        if len(json.dumps(result))>256*1024:raise ValueError('Suggestions exceed supported size.')
        emit('assist_result',**result)
    return 0
