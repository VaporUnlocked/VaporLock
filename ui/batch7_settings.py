"""Batch 7 settings; no renderer or API authority."""
FIELDS=('dataset_annotation_additive','inference_cpu_placement')

def validate(values):
    if type(values.get('dataset_annotation_additive',False)) is not bool:
        raise ValueError('Invalid annotation mode')
    if values.get('dataset_annotation_additive') and (values.get('dataset_annotation_replace') or values.get('dataset_annotation_delete_unannotated')):
        raise ValueError('Additive annotation cannot replace labels or delete images')
    placement=values.get('inference_cpu_placement',{})
    if type(placement) is not dict:raise ValueError('Invalid CPU placement')
    if not placement:return
    import re
    if set(placement)!= {'identity','cpus'} or type(placement['identity']) is not str or not re.fullmatch('[0-9a-f]{64}',placement['identity']):
        raise ValueError('Invalid CPU placement identity')
    cpus=placement['cpus']
    if type(cpus) is not list or not 1<=len(cpus)<=64 or any(type(n) is not int or not 0<=n<64 for n in cpus) or cpus!=sorted(set(cpus)):
        raise ValueError('Invalid CPU placement selection')
