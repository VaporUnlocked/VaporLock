"""Post-weights-only checkpoint schema validation inside a bounded child.

Never use unrestricted pickle or a fallback loader. This adapter validates
resource bounds and state structure; it does not claim checkpoint authorship.
"""
from __future__ import annotations

import math
import re
from cloud_contract import CloudInputError, MAX_CHECKPOINT, finite, integer, model_config


def _validate_rng(rng, torch):
    if type(rng) is not dict or set(rng) != {'python', 'torch', 'cuda', 'numpy'}:
        raise CloudInputError('Invalid checkpoint random-generator state.')
    if (not isinstance(rng['torch'], torch.Tensor) or rng['torch'].dtype != torch.uint8
            or rng['torch'].ndim != 1 or not 1 <= rng['torch'].numel() <= 32768
            or type(rng['cuda']) is not list or len(rng['cuda']) > 16):
        raise CloudInputError('Invalid checkpoint Torch random-generator state.')
    for value in rng['cuda']:
        if not isinstance(value, torch.Tensor) or value.dtype != torch.uint8 or value.ndim != 1 or value.numel() > 32768:
            raise CloudInputError('Invalid checkpoint CUDA random-generator state.')
    try:
        torch.Generator(device='cpu').set_state(rng['torch'])
    except (RuntimeError, TypeError) as exc:
        raise CloudInputError('Invalid checkpoint Torch random-generator contents.') from exc
    import random
    probe = random.Random()
    try:
        probe.setstate(rng['python'])
    except (ValueError, TypeError, OverflowError) as exc:
        raise CloudInputError('Invalid checkpoint Python random-generator state.') from exc
    numpy_rng = rng['numpy']
    if (type(numpy_rng) is not dict or set(numpy_rng) != {'generator', 'keys', 'position', 'has_gauss', 'cached_gaussian'}
            or numpy_rng['generator'] != 'MT19937' or type(numpy_rng['keys']) is not list
            or len(numpy_rng['keys']) != 624):
        raise CloudInputError('Invalid checkpoint NumPy random-generator state.')
    for key in numpy_rng['keys']:
        integer(key, 0, 2**32 - 1, 'NumPy random-generator key')
    integer(numpy_rng['position'], 0, 624, 'NumPy random-generator position')
    integer(numpy_rng['has_gauss'], 0, 1, 'NumPy random-generator cache')
    finite(numpy_rng['cached_gaussian'], -1e20, 1e20, 'NumPy cached Gaussian')


def validate_checkpoint(checkpoint, training, train_images):
    import torch
    from cloudstudio.config import ModelConfig, weight_shapes

    required = {'checkpoint_version', 'architecture', 'config', 'model_config', 'model', 'optimizer',
                'rng', 'state', 'dataset_fingerprint', 'run_id', 'scaler', 'best_model', 'history'}
    optional = {'resume_safe', 'latest_evaluation', 'resolved_device', 'initialization', 'gradients',
                'batch_sizing', 'model_artifacts', 'torch_version'}
    if (type(checkpoint) is not dict or not required <= set(checkpoint)
            or set(checkpoint) - required - optional or checkpoint.get('checkpoint_version') != 3
            or checkpoint.get('resume_safe', True) is not True):
        raise CloudInputError('Unsupported, incomplete or unsafe-to-resume .cloudtrain checkpoint.')
    architecture = model_config(checkpoint['model_config'])
    if checkpoint['architecture'] != architecture:
        raise CloudInputError('Checkpoint architecture is inconsistent.')
    if type(checkpoint['config']) is not dict:
        raise CloudInputError('Invalid checkpoint training recipe.')
    config = training.TrainingConfig(**checkpoint['config'])
    if (config.architecture != architecture
            or training._model_config(config).to_dict() != ModelConfig.from_dict(checkpoint['model_config']).to_dict()):
        raise CloudInputError('Checkpoint model and training recipe disagree.')
    if (type(checkpoint['run_id']) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,128}', checkpoint['run_id'])
            or type(checkpoint['dataset_fingerprint']) is not str
            or not re.fullmatch('[0-9a-f]{64}', checkpoint['dataset_fingerprint'])):
        raise CloudInputError('Invalid checkpoint run or dataset identity.')
    shapes = weight_shapes(ModelConfig.from_dict(checkpoint['model_config']))
    tensor_bytes = 0; nodes = 0
    pending = [(checkpoint, 0)]
    while pending:
        value, depth = pending.pop(); nodes += 1
        if depth > 32 or nodes > 500_000:
            raise CloudInputError('Checkpoint state structure exceeds its resource limit.')
        if isinstance(value, torch.Tensor):
            if (value.device.type != 'cpu' or value.layout != torch.strided or value.ndim > 8
                    or value.dtype not in (torch.float32, torch.float64, torch.int64, torch.int32, torch.uint8)
                    or value.numel() > MAX_CHECKPOINT // max(value.element_size(), 1)):
                raise CloudInputError('Unsupported checkpoint tensor allocation or type.')
            tensor_bytes += value.numel() * value.element_size()
            if tensor_bytes > MAX_CHECKPOINT or not bool(torch.isfinite(value).all()):
                raise CloudInputError('Checkpoint tensor budget or finite-value check failed.')
        elif isinstance(value, dict):
            if any(type(key) not in (str, int) for key in value):
                raise CloudInputError('Unsupported checkpoint mapping key.')
            pending.extend((item, depth + 1) for item in value.values())
        elif type(value) in (list, tuple):
            pending.extend((item, depth + 1) for item in value)
        elif value is None or type(value) in (bool, str, int):
            if type(value) is str and len(value) > 1_000_000:
                raise CloudInputError('Checkpoint text exceeds its limit.')
        elif type(value) is float and math.isfinite(value):
            pass
        else:
            raise CloudInputError('Unsupported checkpoint state value.')

    def weights(value, *, partial=False):
        if type(value) is not dict and not isinstance(value, dict):
            raise CloudInputError('Invalid checkpoint model state.')
        if (set(value) - set(shapes)) or (not partial and set(value) != set(shapes)):
            raise CloudInputError('Checkpoint model tensor names do not match the architecture.')
        for name, tensor in value.items():
            if not isinstance(tensor, torch.Tensor) or tensor.dtype != torch.float32 or tuple(tensor.shape) != shapes[name]:
                raise CloudInputError('Checkpoint model tensor shape or type is invalid.')
    weights(checkpoint['model'])
    if checkpoint['best_model'] is not None:
        weights(checkpoint['best_model'])
    weights(checkpoint.get('gradients', {}), partial=True)
    if type(checkpoint['history']) is not list or len(checkpoint['history']) > 10_000:
        raise CloudInputError('Checkpoint history exceeds its supported limit.')
    if any(type(row) is not dict for row in checkpoint['history']):
        raise CloudInputError('Invalid checkpoint history.')
    state = checkpoint['state']
    state_keys = {'next_epoch', 'batch_offset', 'order', 'running', 'running_samples', 'steps', 'updates_seen',
                  'best_score', 'best_loss', 'best_epoch', 'bad_epochs', 'accumulation_samples', 'window_end',
                  'window_positives', 'window_start_state', 'schedule_total_updates', 'schedule_updates_per_epoch'}
    if type(state) is not dict or set(state) != state_keys:
        raise CloudInputError('Invalid checkpoint sampler state.')
    for key in ('next_epoch', 'batch_offset', 'running_samples', 'steps', 'updates_seen', 'bad_epochs',
                'accumulation_samples', 'window_end', 'window_positives', 'schedule_total_updates', 'schedule_updates_per_epoch'):
        integer(state.get(key), 0, 100_000_000, 'Checkpoint ' + key)
    if (state['next_epoch'] > config.epochs or state['batch_offset'] > train_images
            or state['running_samples'] != state['batch_offset'] or state['steps'] > state['updates_seen']
            or state['window_end'] > train_images or state['accumulation_samples'] > state['batch_offset']
            or not state['schedule_total_updates'] or not state['schedule_updates_per_epoch']):
        raise CloudInputError('Checkpoint cursor is outside the training project.')
    for key in ('best_loss', 'best_score'):
        if state[key] is not None:
            finite(state[key], -1e12 if key == 'best_score' else 0, 1e12, 'Checkpoint ' + key)
    if state['best_epoch'] is not None:
        integer(state['best_epoch'], 1, state['next_epoch'], 'Checkpoint best epoch')
    loss_fields = {'loss', 'objectness_loss', 'box_loss', 'contour_loss', 'contour_position_loss', 'contour_edge_loss',
                   'mask_bce_loss', 'mask_dice_loss', 'mask_boundary_loss', 'background_mask_loss'}
    def running_losses(value):
        if type(value) is not dict or set(value) - loss_fields:
            raise CloudInputError('Invalid checkpoint accumulated loss state.')
        for loss in value.values():
            finite(loss, 0, 1e20, 'Accumulated loss')
    running_losses(state['running'])
    if len(checkpoint['history']) != state['next_epoch']:
        raise CloudInputError('Checkpoint epoch history does not match its completed cursor.')
    history_fields = loss_fields | {'epoch', 'learning_rate', 'optimizer_steps', 'predicted_guide_fraction',
                                   'selection_score', 'selection_metric', 'validation_improved', 'evaluation'}
    for epoch, row in enumerate(checkpoint['history'], 1):
        if set(row) - history_fields or type(row.get('epoch')) is not int or row['epoch'] != epoch:
            raise CloudInputError('Invalid checkpoint epoch history schema or order.')
        running_losses({k: v for k, v in row.items() if k in loss_fields})
        integer(row.get('optimizer_steps'), 0, state['steps'], 'Historical optimizer steps')
        finite(row.get('learning_rate'), 0, 1, 'Historical learning rate')
        finite(row.get('predicted_guide_fraction'), 0, 1, 'Historical guidance fraction')
        if row.get('selection_score') is not None:
            finite(row['selection_score'], -1e12, 1, 'Historical selection score')
        if (type(row.get('selection_metric')) is not str or len(row['selection_metric']) > 128
                or type(row.get('validation_improved')) is not bool):
            raise CloudInputError('Invalid checkpoint history selection state.')
    order = state.get('order')
    if order is not None and (type(order) is not list or len(order) != train_images
            or any(type(v) is not int for v in order) or set(order) != set(range(train_images))):
        raise CloudInputError('Checkpoint sampler permutation does not match the training project.')
    if order is None and (state['batch_offset'] or state['running_samples'] or state['accumulation_samples']):
        raise CloudInputError('Checkpoint epoch and accumulation cursors disagree.')
    window = state['window_start_state']
    sizing = checkpoint.get('batch_sizing')
    resolved = sizing.get('resolved_batch_size') if type(sizing) is dict else config.batch_size
    integer(resolved, 1, 256, 'Resolved checkpoint batch size')
    if config.batch_size != -1 and resolved != config.batch_size:
        raise CloudInputError('Checkpoint resolved batch disagrees with its explicit recipe.')
    capacity = resolved * max(1, math.ceil(config.nominal_batch_size / resolved))
    if (state['schedule_updates_per_epoch'] != math.ceil(train_images / capacity)
            or state['schedule_total_updates'] % state['schedule_updates_per_epoch']
            or not 1 <= state['schedule_total_updates'] // state['schedule_updates_per_epoch'] <= 10000):
        raise CloudInputError('Checkpoint update schedule disagrees with its effective batch.')
    if state['accumulation_samples']:
        if type(window) is not dict or set(window) != {'offset', 'rng', 'running', 'running_samples'}:
            raise CloudInputError('Checkpoint unfinished accumulation is missing its recovery state.')
        integer(window['offset'], 0, state['batch_offset'], 'Accumulation recovery offset')
        if (window['running_samples'] != window['offset'] or state['window_end'] <= state['batch_offset']
                or state['accumulation_samples'] != state['batch_offset'] - window['offset']
                or window['offset'] % capacity != 0
                or (state['batch_offset'] - window['offset']) % resolved != 0
                or state['window_end'] != min(train_images, window['offset'] + capacity)):
            raise CloudInputError('Checkpoint unfinished accumulation cursor is invalid.')
        running_losses(window['running'])
        if not checkpoint.get('gradients'):
            raise CloudInputError('Checkpoint unfinished accumulation is missing gradients.')
    elif window is not None or checkpoint.get('gradients') or state['window_end'] or state['window_positives']:
        raise CloudInputError('Checkpoint has gradients without an unfinished accumulation window.')
    scaler = checkpoint['scaler']
    if type(scaler) is not dict:
        raise CloudInputError('Invalid checkpoint AMP scaler state.')
    if scaler:
        if set(scaler) != {'scale', 'growth_factor', 'backoff_factor', 'growth_interval', '_growth_tracker'}:
            raise CloudInputError('Invalid checkpoint AMP scaler fields.')
        finite(scaler['scale'], 1e-30, 1e30, 'AMP scale')
        finite(scaler['growth_factor'], 1.00000001, 100, 'AMP growth factor')
        finite(scaler['backoff_factor'], 1e-8, .99999999, 'AMP backoff factor')
        integer(scaler['growth_interval'], 1, 100_000_000, 'AMP growth interval')
        integer(scaler['_growth_tracker'], 0, scaler['growth_interval'], 'AMP growth tracker')
    _validate_rng(checkpoint['rng'], torch)
    if window is not None:
        _validate_rng(window['rng'], torch)
    optimizer = checkpoint['optimizer']
    if type(optimizer) is not dict or set(optimizer) != {'state', 'param_groups'}:
        raise CloudInputError('Invalid checkpoint optimizer state.')
    groups = optimizer['param_groups']; states = optimizer['state']
    if type(groups) is not list or not 1 <= len(groups) <= 2 or any(type(group) is not dict for group in groups) or type(states) is not dict:
        raise CloudInputError('Unsupported checkpoint optimizer layout.')
    # Network construction is validation only and must not advance the trainer's
    # seeded CPU random stream. No CUDA kernels or RNG are used by this probe.
    with torch.random.fork_rng(devices=[]):
        model = training.CloudNet(ModelConfig.from_dict(checkpoint['model_config']))
    fresh = training._make_optimizer(model, config)
    reference = fresh.state_dict()['param_groups']
    if len(reference) != len(groups):
        raise CloudInputError('Checkpoint optimizer grouping does not match its recipe.')
    parameters = {}
    for actual, expected, live_group in zip(groups, reference, fresh.param_groups):
        if (set(actual) != set(expected) or actual['params'] != expected['params']
                or type(actual['params']) is not list or any(type(item) is not int for item in actual['params'])):
            raise CloudInputError('Checkpoint optimizer parameter groups do not match the network.')
        for key, expected_value in expected.items():
            if key == 'lr':
                finite(actual[key], 0, 1, 'Optimizer learning rate')
            elif actual[key] != expected_value or type(actual[key]) is not type(expected_value):
                raise CloudInputError('Checkpoint optimizer hyperparameters disagree with its recipe.')
        for identifier, parameter in zip(expected['params'], live_group['params']):
            parameters[identifier] = parameter
    if any(type(k) is not int or k not in parameters for k in states):
        raise CloudInputError('Checkpoint optimizer parameters do not match the network.')
    for index, value in states.items():
        if type(value) is not dict or set(value) != {'step', 'exp_avg', 'exp_avg_sq'}:
            raise CloudInputError('Unsupported checkpoint AdamW state.')
        for key in ('exp_avg', 'exp_avg_sq'):
            tensor = value[key]
            if not isinstance(tensor, torch.Tensor) or tensor.dtype != torch.float32 or tensor.shape != parameters[index].shape:
                raise CloudInputError('Checkpoint AdamW tensor shape or type is invalid.')
            if key == 'exp_avg_sq' and bool((tensor < 0).any()):
                raise CloudInputError('Checkpoint AdamW second moments must be nonnegative.')
        step = value['step']
        if (not isinstance(step, torch.Tensor) or step.numel() != 1 or step.item() < 0
                or step.item() > 100_000_000 or step.item() != int(step.item())):
            raise CloudInputError('Checkpoint optimizer step is invalid.')
    del fresh, model, parameters
    return checkpoint
