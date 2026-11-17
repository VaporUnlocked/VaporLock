"""Private .cloud worker entry point. Never run a project or model as code."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading


def _bootstrap():
    # -I excludes user site/PYTHONPATH/current working directory. Add only our
    # application module directory, never a selected dataset/model directory.
    sys.dont_write_bytecode = True
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    if sys.argv[1:] != ['--gated'] or sys.stdin.buffer.readline(32) != b'VLCLOUD-GO-1\n':
        raise SystemExit(2)


def emit(payload):
    from cloud_contract import MAX_JSON
    raw = json.dumps(payload, allow_nan=False, ensure_ascii=payload.get('type') != 'dataset_health_checked',
                     separators=(',', ':')).encode('utf-8', errors='replace') + b'\n'
    if len(raw) > MAX_JSON:
        raise ValueError('Cloud result exceeds its record limit.')
    sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()


def read_request():
    from cloud_contract import MAX_JSON, CloudInputError, json_value
    raw = sys.stdin.buffer.readline(MAX_JSON + 1)
    if not raw or len(raw) > MAX_JSON or not raw.endswith(b'\n'):
        raise CloudInputError('Invalid .cloud worker request.')
    result = json_value(raw)
    if type(result) is not dict:
        raise CloudInputError('Invalid .cloud worker request.')
    return result


def recipe(request, training=None, checkpoint=None):
    from cloud_contract import CloudInputError, integer
    from cloud_recipe import MODEL_FIELDS
    from cloudstudio.training_settings import resolve_settings, SETTING_BY_NAME, PRESETS
    mode = request.get('mode', 'fresh')
    if mode not in ('fresh', 'initialize', 'resume'):
        raise CloudInputError('Choose a fresh, initialized or resumed .cloud run.')
    overrides = request.get('overrides', {})
    preset = request.get('preset', 'player')
    if type(preset) is not str or preset not in PRESETS:
        raise CloudInputError('Choose a supported .cloud training preset.')
    if type(overrides) is not dict or set(overrides) - SETTING_BY_NAME.keys():
        raise CloudInputError('Unsupported .cloud recipe setting.')
    if mode == 'resume':
        if checkpoint is None:
            raise CloudInputError('Resume requires a validated .cloudtrain checkpoint.')
        values = dict(checkpoint['config'])
        # The original sampler/optimizer recipe is inherited, never silently
        # replaced by the current tab's fresh-run settings.
        if any(k != 'device' and values.get(k) != v for k, v in overrides.items()):
            raise CloudInputError('Resume inherits the checkpoint recipe. Only the execution device may be changed here.')
        values.update(overrides)
        values.update(resume_path=request['checkpoint'], init_model_path=None)
        return values
    try:
        values = resolve_settings(preset, overrides=overrides)
    except ValueError as error:
        raise CloudInputError('Invalid .cloud recipe. ' + str(error)) from error
    if values['allow_no_validation']:
        raise CloudInputError('Cloud Studio requires a reviewed validation split.')
    values.update(resume_path=None, init_model_path=request.get('initial_model') if mode == 'initialize' else None)
    if mode == 'initialize':
        from cloud_contract import inspect_model_stream
        path = Path(request.get('initial_model', ''))
        with path.open('rb') as stream:
            information = inspect_model_stream(stream, os.fstat(stream.fileno()).st_size)
        from cloudstudio.config import ModelConfig
        capacities = vars(ModelConfig.from_dict(information['config']))
        if any(name in overrides and overrides[name] != capacities[name] for name in MODEL_FIELDS):
            raise CloudInputError('The recipe architecture or model dimensions differ from the initial .cloud model.')
        values.update(capacities)
    integer(values['epochs'], 1, 10000, 'Epochs')
    return values


def check_project(request):

    from cloud_contract import CloudInputError
    from cloud_project import ProjectLease
    if set(request) - {'operation', 'project'}:
        raise CloudInputError('Unsupported project-check fields.')
    with ProjectLease(request['project']) as project:
        project.check_image_headers()

        emit({'type': 'project_checked', 'counts': project.counts})


def check_dataset_health(request):
    from cloud_contract import CloudInputError, integer
    from dataset_health import scan_cloud, scan_yolo

    scanners = {'cloud': scan_cloud, 'yolo': scan_yolo}
    kind, source = request.get('kind'), request.get('source')
    if (set(request) != {'operation', 'source', 'kind', 'model_size'}
            or type(kind) is not str or kind not in scanners
            or type(source) is not str or not 1 <= len(source) <= 32000):
        raise CloudInputError('Choose a supported dataset folder.')
    size = integer(request['model_size'], 32, 4096, 'Model input size')

    def progress(scanned, total):
        emit(dict(type='dataset_health_progress', scanned=scanned, total=total))

    try:
        report = scanners[kind](source, size, progress)
    except (OSError, ValueError) as error:
        if isinstance(error, CloudInputError):
            raise
        raise CloudInputError('Could not read the selected dataset. Choose a local dataset folder.') from error
    emit(dict(type='dataset_health_checked', report=report))


def recipe_summary(values, checkpoint=None):
    summary={key:values[key] for key in ('epochs','batch_size','device','patience','input_size','channels','mask_size','architecture') if key in values}
    summary['completed_epochs']=checkpoint['state']['next_epoch'] if checkpoint else 0
    return summary


def check_resume(request):
    from cloud_contract import CloudInputError, MAX_CHECKPOINT, inspect_checkpoint_archive
    from cloud_project import ProjectLease

    if set(request)!={'operation','project','checkpoint'}:raise CloudInputError('Unsupported checkpoint-check fields.')
    with ProjectLease(request['project']) as project:
        project.check_image_headers()
        selected=project.hold_file(request['checkpoint'],MAX_CHECKPOINT)
        with selected.open('rb') as stream:inspect_checkpoint_archive(stream,os.fstat(stream.fileno()).st_size)
        from cloud_source_adapter import enable_source
        enable_source('training')
        from cloudstudio import training
        from cloud_checkpoint import validate_checkpoint
        checkpoint=validate_checkpoint(training._load_checkpoint(selected),training,project.counts['train'])

        emit({'type':'resume_checked','recipe':recipe_summary(checkpoint['config'],checkpoint)})


def check_model(request):

    from cloud_contract import MAX_DESCRIPTOR,MAX_WEIGHTS,CloudInputError,inspect_model_stream
    from cloud_project import ProjectLease
    if set(request)!={'operation','model'}:raise CloudInputError('Unsupported model-check fields.')
    lease=ProjectLease('.')
    try:
        selected=lease.hold_file(request['model'],MAX_DESCRIPTOR+MAX_WEIGHTS+56)
        with selected.open('rb') as stream:info=inspect_model_stream(stream,os.fstat(stream.fileno()).st_size)
        from cloud_source_adapter import enable_source
        enable_source('runtime')
        from cloud_sdk.format import load_cloud
        load_cloud(selected)

        emit({'type':'model_checked','model':info})
    finally:lease.stack.close()


def evaluate_model(request):
    from cloud_contract import CloudInputError, MAX_DESCRIPTOR, MAX_WEIGHTS, finite, inspect_model_stream
    from cloud_project import ProjectLease
    from cloud_results import EVALUATION_METRICS
    from cloud_source_adapter import enable_source

    if set(request) != {'operation', 'project', 'model', 'split', 'confidence', 'device'}:
        raise CloudInputError('Invalid .cloud evaluation request.')
    split = request['split']
    if split not in ('val', 'test'):
        raise CloudInputError('Choose validation or test images for evaluation.')
    confidence = finite(request['confidence'], 0, 1, 'Evaluation confidence')
    with ProjectLease(request['project']) as project:
        project.check_image_headers()
        selected = project.hold_file(request['model'], MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
        with selected.open('rb') as stream:
            inspect_model_stream(stream, os.fstat(stream.fileno()).st_size)
        enable_source('training')
        from cloudstudio.data import Project
        from cloudstudio.evaluation import evaluate_with_predictor, match_detections
        from evaluation_slices import EvaluationSlices
        from cloudstudio import training
        from cloud_training_input import training_preparation

        bounded = Project(project.root, project.manifest)
        records = bounded.training_records(split)
        if not records:
            raise CloudInputError('The selected split has no reviewed images.')
        runtime = inspection_runtime(selected, request['device'])
        slices = EvaluationSlices()
        pending_records = iter(records)

        def predict(image):
            result = runtime.predict(image, confidence=confidence, debug=True)
            record = next(pending_records)
            slices.add(record, match_detections(record['annotations'], result['detections']))
            return result

        with training_preparation(training):
            metrics = evaluate_with_predictor(bounded, records, predict, split=split, confidence=confidence)
        emit({'type': 'evaluated', 'split': split, 'confidence': confidence, 'images': len(records),
              'metrics': {key: metrics.get(key) for key in EVALUATION_METRICS}, 'slices': slices.results()})


def inspection_runtime(path, device):
    import torch
    from cloud_contract import CloudInputError
    from cloudstudio.runtime import CloudRuntime
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise CloudInputError('NVIDIA CUDA is unavailable. Choose CPU or check the CUDA runtime.')
    torch.set_num_threads(min(4, torch.get_num_threads()))
    return CloudRuntime(path, backend='torch', device=device)


def inspect_image(request):
    from cloud_annotation_decode import decode
    from cloud_contract import CloudInputError, MAX_DESCRIPTOR, MAX_WEIGHTS, finite, integer, inspect_model_stream
    from cloud_project import ProjectLease
    from cloud_source_adapter import enable_source

    if set(request) != {'operation', 'model', 'image', 'confidence', 'nms_iou', 'max_detections', 'min_height', 'device'}:
        raise CloudInputError('Invalid .cloud image inspection request.')
    confidence = finite(request['confidence'], 0, 1, 'Inspection confidence')
    nms_iou = finite(request['nms_iou'], 0, 1, 'Box NMS overlap')
    maximum = integer(request['max_detections'], 1, 256, 'Maximum detections')
    min_height = integer(request['min_height'], 0, 32768, 'Minimum detection height')
    lease = ProjectLease('.')
    try:
        selected = lease.hold_file(request['model'], MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
        with selected.open('rb') as stream:
            inspect_model_stream(stream, os.fstat(stream.fileno()).st_size)
        image, _, _ = decode(request['image'])
        enable_source('training')
        from cloudstudio import training
        from cloud_training_input import training_preparation

        runtime = inspection_runtime(selected, request['device'])
        with training_preparation(training):
            prediction = runtime.predict(image, confidence=confidence, nms_iou=nms_iou,
                                         max_detections=maximum, min_height=min_height, debug=True)
        emit({'type': 'image_inspected', 'width': image.width, 'height': image.height,
              'device': str(runtime.device), 'invalid_contours': prediction['invalid_contours'],
              'empty_masks': prediction.get('empty_masks', 0), 'timing_ms': prediction['timing_ms'],
              'primary_count': prediction['diagnostics']['primary_count'],
              'detections': [{key: item[key] for key in ('class_id', 'confidence', 'primary_box', 'mask', 'wrapper')
                              if key in item} for item in prediction['detections']]})
    finally:
        lease.stack.close()


def export_model(request):
    from tempfile import TemporaryDirectory
    from cloud_contract import CloudInputError, MAX_DESCRIPTOR, MAX_WEIGHTS, inspect_model_stream
    from cloud_project import ProjectLease, NewRunLease
    from cloud_source_adapter import enable_source

    if set(request) != {'operation', 'model', 'output_parent', 'run_name'}:
        raise CloudInputError('Invalid .cloud ONNX export request.')
    source = ProjectLease('.')
    try:
        selected = source.hold_file(request['model'], MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
        with selected.open('rb') as stream:
            inspect_model_stream(stream, os.fstat(stream.fileno()).st_size)
        enable_source('training')
        from cloudstudio.export import export_onnx, read_onnx_export
        import torch
        import shutil

        torch.set_num_threads(min(4, torch.get_num_threads()))
        with TemporaryDirectory(prefix='vaporlock-cloud-export-') as staging:
            export_onnx(selected, staging)
            read_onnx_export(staging)
            # Publish the manifest after both checked graphs are in place.
            with NewRunLease(request['output_parent'], request['run_name'], staging) as output:
                for name in ('detector.onnx', 'wrapper.onnx', 'model.json'):
                    with (Path(staging) / name).open('rb') as origin, (output / name).open('xb') as target:
                        shutil.copyfileobj(origin, target)
                        target.flush()
                        os.fsync(target.fileno())
                emit({'type':'exported', 'output':str(output)})
    finally:
        source.stack.close()


def train_model(request):
    from cloud_contract import CloudInputError, MAX_CHECKPOINT, MAX_DESCRIPTOR, MAX_WEIGHTS, inspect_checkpoint_archive
    from cloud_project import ProjectLease, NewRunLease
    # Packaged Free explicitly permits this dedicated .cloud job. Premium
    # retains its original independent license monitor and operation checks.

    if set(request) - {'operation', 'project', 'output_parent', 'run_name', 'mode', 'initial_model', 'checkpoint', 'overrides', 'preset'}:
        raise CloudInputError('Unsupported .cloud training fields.')
    # main() has already admitted the selected provider/common inventories.
    # Initialize NumPy before any thread blocks on piped stdin: on Windows,
    # first native initialization can deadlock against that concurrent read.
    import numpy  # Native initialization only; no model or training operations.
    stop = threading.Event()
    from cloud_stop_reader import StopListener
    with StopListener(stop), ProjectLease(request['project']) as project:
        emit({'type': 'phase', 'phase': 'checking'})
        project.check_image_headers()
        mode = request.get('mode', 'fresh')
        if mode == 'initialize':
            selected = project.hold_file(request.get('initial_model', ''), MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
            request = {**request, 'initial_model': str(selected)}
        if mode == 'resume':
            selected = project.hold_file(request.get('checkpoint', ''), MAX_CHECKPOINT)
            with selected.open('rb') as stream:
                inspect_checkpoint_archive(stream, os.fstat(stream.fileno()).st_size)
            request = {**request, 'checkpoint': str(selected)}
        from cloud_source_adapter import enable_source
        enable_source('training')
        from cloudstudio import training
        from cloudstudio.data import Project
        from cloudstudio.geometry import validate_annotation
        checkpoint = None
        original_loader = training._load_checkpoint
        if mode == 'resume':
            from cloud_checkpoint import validate_checkpoint
            checkpoint = validate_checkpoint(original_loader(request['checkpoint']), training, project.counts['train'])
            training._load_checkpoint = lambda path: checkpoint if str(path) == request['checkpoint'] else (_ for _ in ()).throw(CloudInputError('Checkpoint identity changed.'))
        values = recipe(request, training, checkpoint)
        s1 = values['architecture'] == 'cloudvision_s1_v2'
        for record in project.manifest['images']:
            if record['reviewed']:
                for annotation in record['annotations']:
                    errors = validate_annotation(annotation, record['width'], record['height'], require_complete=True)
                    if errors:
                        raise CloudInputError('A reviewed instance has invalid box or encasing geometry.')
                    if s1 and record['split'] == 'train' and annotation.get('mask') is not None:
                        from cloudstudio.masks import annotation_mask, polygon_mask
                        width, height = record['width'], record['height']
                        polygon = annotation.get('wrapper') or []
                        if len(polygon) < 3 or not numpy.array_equal(annotation_mask(annotation, width, height),
                                                                   polygon_mask(polygon, width, height)):
                            raise CloudInputError('S1 needs reviewed polygons that preserve each training mask. Choose the S2 spatial mask architecture for this project.')
        emit({'type':'configuration','recipe':recipe_summary(values,checkpoint)})
        config = training.TrainingConfig(**values)
        # The owner's trainer asks Project.open once. Supply the already bounded,
        # read-only manifest while all source files remain held. No database
        # migration and no second unbounded SQLite metadata load can occur.
        bounded = Project(project.root, project.manifest)
        errors = bounded._structure_errors()
        if errors:
            raise CloudInputError('The selected .cloud project is invalid.')
        class BoundedProject:
            @classmethod
            def open(cls, path):
                if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(project.root)):
                    raise CloudInputError('Training project identity changed.')
                return bounded
        training.Project = BoundedProject
        with NewRunLease(request['output_parent'], request['run_name'], project.root) as output:

            emit({'type': 'phase', 'phase': 'starting', 'counts': project.counts})
            def progress(event):

                # Preserve metrics but do not leak project names/paths/labels
                # from owner events into public status or engine diagnostics.
                keys = ('event', 'epoch', 'epochs', 'batch', 'batches', 'step', 'device',
                        'train_images', 'val_images', 'loss', 'detector_loss', 'box_loss',
                        'mask_loss', 'learning_rate', 'resolved_batch_size', 'effective_batch_size')
                emit({'type': 'progress', **{k: event[k] for k in keys if k in event}})
            from cloud_execution import training_execution
            from cloud_training_io import protected_training_log,protected_training_outputs
            with protected_training_outputs(output), protected_training_log(training), training_execution(training):
                result = training.train(project.root, output, config, progress=progress, should_stop=stop.is_set)

            paths = {}
            for key, filename in (('best_cloud', 'best.cloud'), ('last_cloud', 'last.cloud'), ('checkpoint', 'last.cloudtrain')):
                candidate = output / filename
                # Only paths in this new held run can become result actions.
                paths[key] = None
                if result.get(key) and candidate.is_file():
                    held = project.hold_file(candidate, MAX_CHECKPOINT if key == 'checkpoint' else MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
                    if key == 'checkpoint':
                        with held.open('rb') as stream:
                            inspect_checkpoint_archive(stream, os.fstat(stream.fileno()).st_size)
                        from cloud_checkpoint import validate_checkpoint
                        validate_checkpoint(original_loader(held), training, project.counts['train'])
                    else:
                        from cloudstudio.format import load_cloud
                        load_cloud(held)
                    paths[key] = str(held)
            emit({'type': 'trained', 'status': result['status'], 'epochs_completed': result.get('epochs_completed', 0),
                  'output': str(output), **paths})


def inference(request):
    from cloud_contract import CloudInputError, FRAME, MAX_FRAME_PIXELS, frame_header, inspect_model_stream
    from cloud_project import ProjectLease
    from cloud_access import edition
    build=edition()

    if (set(request) - {'operation', 'model', 'backend', 'device', 'native_pipe'}
            or not {'operation', 'model', 'backend', 'device'} <= set(request)
            or request['backend'] not in ('numpy', 'torch', 'ort')):
        raise CloudInputError('Unsupported .cloud inference request.')
    endpoint=None
    if 'native_pipe' in request:
        from cloud_access import pipe_name
        endpoint=pipe_name(request['native_pipe'],build=build)
    device = request['device']
    import re
    if type(device) is not str or re.fullmatch(r'cpu|auto|cuda(?::[0-9]{1,2})?|directml(?::[0-9]{1,2})?', device) is None:
        raise CloudInputError('Unsupported .cloud device.')
    lease = ProjectLease('.');server=None
    try:
        from cloud_contract import MAX_DESCRIPTOR, MAX_WEIGHTS
        model = lease.hold_file(request['model'], MAX_DESCRIPTOR + MAX_WEIGHTS + 56)
        with model.open('rb') as stream:
            info = inspect_model_stream(stream, os.fstat(stream.fileno()).st_size)
        from cloud_source_adapter import enable_source
        enable_source('runtime')
        from cloud_runtime import CloudRuntime
        from PIL import Image
        if request['backend'] == 'ort':
            from cloud_sdk.format import load_cloud
            from cloud_ort import runtime_from_weights
            config, weights, metadata = load_cloud(model)
            backend = device.split(':')[0]
            if backend not in ('cpu', 'directml', 'cuda'):
                raise CloudInputError('Select CPU, DirectML or CUDA explicitly for the .cloud runtime.')
            runtime = runtime_from_weights(config, metadata, weights, backend=backend,
                device_id=int(device.split(':')[1]) if ':' in device else 0, threads=2)
        else:
            runtime = CloudRuntime(model, backend=request['backend'], device=device)
        if endpoint is not None:
            # Compile/allocate before native capture starts. This warmup is not
            # an observation and is never submitted to tracking or output.
            from cloud_warmup import warmup
            warmup(runtime)
        if endpoint is not None:
            from cloud_pipe import PipeServer
            server = PipeServer(endpoint,build=build)
        # Both backends own immutable loaded weights now. Future observations
        # never reopen the model, so ancestor directory pins are no longer needed.
        lease.stack.close()

        emit({'type': 'ready', 'edition':build, 'backend': request['backend'], 'device': str(runtime.device), 'model': info, 'pid': os.getpid()})
        if server is not None:
            try:
                from cloud_pipe import serve
                serve(server, runtime, read_request)
                emit({'type': 'inference_closed'})
            finally:
                server.close()
            return
        previous = 0
        while True:
            header = sys.stdin.buffer.read(FRAME.size)
            if not header:
                emit({'type': 'inference_closed'})
                return
            sequence, captured_ns, width, height, length, confidence, iou, maximum = frame_header(header)
            if sequence <= previous:
                raise CloudInputError('Nonmonotonic .cloud frame identity.')
            previous = sequence

            pixels = bytearray(length); view = memoryview(pixels); offset = 0
            while offset < length:
                count = sys.stdin.buffer.readinto(view[offset:])
                if not count:
                    raise CloudInputError('Truncated .cloud frame payload.')
                offset += count
            # RGB conversion is transport adaptation only. Pixel-center resize,
            # network, NMS and mask projection remain the owner's SDK operations.
            from cloud_live_input import predict_bgrx
            result = predict_bgrx(runtime,width,height,pixels,confidence=confidence,nms_iou=iou,max_detections=maximum,debug=True)
            detections = []
            for item in result['detections']:
                detections.append({key: item[key] for key in ('class_id', 'confidence', 'primary_box', 'mask', 'wrapper') if key in item})

            emit({'type': 'detections', 'sequence': sequence, 'captured_ns': captured_ns,
                  'width': width, 'height': height, 'detections': detections, 'timing_ms': result['timing_ms']})
    finally:
        if server is not None:server.close()
        lease.stack.close()


def main():
    _bootstrap()
    try:
        request = read_request()
        operations = {'check_project': check_project, 'check_resume':check_resume,'check_model':check_model,
                      'train': train_model, 'inference': inference, 'evaluate': evaluate_model, 'inspect': inspect_image,
                      'export': export_model, 'dataset_health': check_dataset_health}
        operation = request.get('operation')
        if operation not in operations:
            raise ValueError('Unsupported .cloud worker operation.')
        from cloud_access import edition
        edition()  # No missing/unknown build may enter any Cloud operation.
        from cloud_dependencies import activate, activate_common, activate_training
        if operation == 'train':
            overrides = request.get('overrides', {})
            if type(overrides) is not dict:
                from cloud_contract import CloudInputError
                raise CloudInputError('Invalid .cloud training overrides.')
            activate_training(overrides.get('device', 'auto'))
        elif operation == 'inference':
            device = request.get('device', 'cpu').split(':')[0]
            activate('cpu' if device in ('cpu', 'auto') else device)
        elif operation in ('check_resume', 'export'):
            activate_training('cpu')
        elif operation in ('evaluate', 'inspect'):
            activate_training(request.get('device', 'auto'))
        else:
            activate_common()
        operations[operation](request)
        return 0
    except Exception as error:
        from cloud_contract import CloudInputError
        message = str(error) if isinstance(error, CloudInputError) else '.cloud processing failed (' + type(error).__name__ + ').'
        from cloud_contract import bundled_dependency_error
        message = bundled_dependency_error(error) or message
        emit({'type': 'error', 'message': message})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
