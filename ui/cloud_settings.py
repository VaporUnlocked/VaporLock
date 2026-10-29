"""Profile-safe .cloud training draft settings; no jobs or input reads."""
import re


def validate(values):
    if values.get('cloud_annotation_device','cpu') not in ('cpu','cuda'): raise ValueError('Invalid .cloud mask assistant device.')
    if values.get('cloud_import_split','train') not in ('train','val','test'): raise ValueError('Invalid .cloud image import split.')
    for name in ('cloud_project','cloud_output','cloud_initial_model','cloud_checkpoint'):
        value=values.get(name,'')
        if type(value) is not str or len(value)>32000 or '\x00' in value: raise ValueError('Invalid .cloud path.')
    if values.get('cloud_mode','fresh') not in ('fresh','initialize','resume'): raise ValueError('Invalid .cloud training mode.')
    if re.fullmatch(r'auto|cpu|cuda(?::[0-9]{1,2})?',values.get('cloud_device','auto')) is None: raise ValueError('Invalid .cloud training device.')
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}',values.get('cloud_run_name','cloud-training')) is None: raise ValueError('Use a short run name containing letters, digits, hyphens or underscores.')
    for name,default,low,high in (('cloud_epochs',300,1,10000),('cloud_batch',8,0,256),('cloud_patience',75,0,10000)):
        value=values.get(name,default)
        if type(value) is not int or not low<=value<=high: raise ValueError('Invalid .cloud training limit.')


def busy(center):
    suite=getattr(center,'_cloud_suite',None)
    return bool(suite and suite.job and not suite.job.done)
