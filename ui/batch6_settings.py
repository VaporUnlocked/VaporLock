"""Closed settings for device selection and local annotation batching."""
DEVICE_FIELDS = ('inference_dml_device', 'inference_cuda_device', 'inference_trt_device')
FIELDS = (*DEVICE_FIELDS, 'dataset_annotation_batch')


def validate(values):
    for key in DEVICE_FIELDS:
        value = values.get(key, -1)
        if type(value) is not int or not -1 <= value < 64:
            raise ValueError('Invalid inference GPU index')
    value = values.get('dataset_annotation_batch', 1)
    if type(value) is not int or value not in (1, 2, 4, 8):
        raise ValueError('Invalid annotation batch size')


def device_for(settings, provider):
    validate(vars(settings))
    return getattr(settings, 'inference_' + provider + '_device') if provider in ('dml', 'cuda', 'trt') else -1
