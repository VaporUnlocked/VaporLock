"""Bounded image batches; outputs stay separate until each image is decoded."""
import numpy as np

MAX_TENSOR_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_DECODED_BYTES = 128 * 1024 * 1024


def batch_plan(shape, requested, width, height, element_bytes=4):
    if type(requested) is not int or requested not in (1, 2, 4, 8):
        raise ValueError('Annotation batch size must be 1, 2, 4 or 8.')
    if len(shape) != 4 or type(width) is not int or type(height) is not int or not 1 <= width <= 8192 or not 1 <= height <= 8192:
        raise ValueError('Annotation image tensor dimensions are unsupported.')
    declared = shape[0]
    if type(declared) is int:
        if not 1 <= declared <= 8 or declared > requested:
            raise ValueError('The model requires a larger fixed batch. Choose a compatible batch size or model.')
        effective, reason = declared, 'fixed-model'
    elif declared is None or type(declared) is str:
        effective, reason = requested, 'dynamic-model'
    else: raise ValueError('Unsupported annotation batch dimension.')
    if 2 * effective * width * height * 3 * element_bytes > MAX_TENSOR_BYTES:
        raise ValueError('Annotation batch tensor memory limit exceeded. Choose a smaller batch or model.')
    return effective, reason


def split_outputs(outputs, count):
    if not outputs or len(outputs) > 32: raise ValueError('Unsupported annotation outputs.')
    arrays = [np.asarray(item) for item in outputs]
    if sum(item.nbytes for item in arrays) > MAX_OUTPUT_BYTES:
        raise ValueError('Annotation output memory limit exceeded. Existing labels were preserved.')
    for item in arrays:
        if item.dtype.kind not in 'fiu' or not np.isfinite(item).all():
            raise ValueError('The annotation model returned nonfinite or nonnumeric output. Existing labels were preserved.')
        if count > 1 and (item.ndim not in (3, 4) or item.shape[0] != count):
            raise ValueError('Annotation output batch dimensions do not match the input. Existing labels were preserved.')
        if count == 1 and (item.ndim not in (2, 3, 4) or (item.ndim >= 3 and item.shape[0] != 1)):
            raise ValueError('Unsupported annotation output dimensions. Existing labels were preserved.')
    return [[item[index:index+1] if item.ndim >= 3 else item for item in arrays] for index in range(count)]


def run_pass(session, name, tensors, fixed_size):
    if not tensors: raise ValueError('Empty annotation batch.')
    count = fixed_size or len(tensors)
    if not len(tensors) <= count <= 8: raise ValueError('Invalid annotation batch.')
    if any(t.shape[0] != 1 or t.shape != tensors[0].shape or t.dtype != tensors[0].dtype for t in tensors):
        raise ValueError('Annotation image tensors do not match.')
    if 2 * count * tensors[0].nbytes > MAX_TENSOR_BYTES:
        raise ValueError('Annotation tensor memory limit exceeded.')
    padded = tensors + [tensors[-1]] * (count-len(tensors))
    tensor = tensors[0] if count == 1 else np.concatenate(padded, axis=0)
    # Padded outputs are validated but never decoded, counted, or published.
    return split_outputs(session.run(None, {name:tensor}), count)[:len(tensors)]
