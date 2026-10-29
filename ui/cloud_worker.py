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
    raw = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
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
    from cloudstudio.training_settings import resolve_settings
    mode = request.get('mode', 'fresh')
    if mode not in ('fresh', 'initialize', 'resume'):
        raise CloudInputError('Choose a fresh, initialized or resumed .cloud run.')
    overrides = request.get('overrides', {})
    if type(overrides) is not dict or set(overrides) - {'epochs', 'batch_size', 'device', 'patience'}:
        raise CloudInputError('Only run scheduling, batch and device overrides are permitted.')
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
    values = resolve_settings('player', overrides=overrides)
    values.update(resume_path=None, init_model_path=request.get('initial_model') if mode == 'initialize' else None)
    if mode == 'initialize':
        from cloud_contract import inspect_model_stream
        path = Path(request.get('initial_model', ''))
        with path.open('rb') as stream:
            information = inspect_model_stream(stream, os.fstat(stream.fileno()).st_size)
        if information['architecture'] != 'cloudvision_s2_v1':
            raise CloudInputError('Initialize S2 training with a compatible S2 .cloud model.')
        values.update(information['config'])
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


def train_model(request):
    from cloud_contract import CloudInputError, MAX_CHECKPOINT, MAX_DESCRIPTOR, MAX_WEIGHTS, inspect_checkpoint_archive
    from cloud_project import ProjectLease, NewRunLease
    # Packaged Free explicitly permits this dedicated .cloud job. Premium
    # retains its original independent license monitor and operation checks.

    if set(request) - {'operation', 'project', 'output_parent', 'run_name', 'mode', 'initial_model', 'checkpoint', 'overrides'}:
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
        for record in project.manifest['images']:
            if record['reviewed']:
                for annotation in record['annotations']:
                    errors = validate_annotation(annotation, record['width'], record['height'], require_complete=True)
                    if errors:
                        raise CloudInputError('A reviewed instance has invalid box or encasing geometry.')
        checkpoint = None
        original_loader = training._load_checkpoint
        if mode == 'resume':
            from cloud_checkpoint import validate_checkpoint
            checkpoint = validate_checkpoint(original_loader(request['checkpoint']), training, project.counts['train'])
            training._load_checkpoint = lambda path: checkpoint if str(path) == request['checkpoint'] else (_ for _ in ()).throw(CloudInputError('Checkpoint identity changed.'))
        values = recipe(request, training, checkpoint)
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
        operations = {'check_project': check_project, 'check_resume':check_resume,'check_model':check_model,'train': train_model, 'inference': inference}
        operation = request.get('operation')
        if operation not in operations:
            raise ValueError('Unsupported .cloud worker operation.')
        from cloud_access import edition
        edition()  # No missing/unknown build may enter any Cloud operation.
        from cloud_dependencies import activate
        if operation == 'train':
            device = request.get('overrides', {}).get('device', 'auto')
            activate('train-cpu' if device == 'cpu' else 'train-cuda')
        elif operation == 'inference':
            device = request.get('device', 'cpu').split(':')[0]
            activate('cpu' if device in ('cpu', 'auto') else device)
        else:
            activate('cpu')
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
