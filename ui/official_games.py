"""Bundled, reviewed official game defaults. Owner supplies entries and artwork.

This catalog is code-packaged data, never downloaded or read from user profiles.
No presets are fabricated. Images, when supplied, are bounded static bytes inside
the catalog, decoded with the same card image validator as profile artwork.
"""
import re
from profile_library import plain

ENTRIES=()
# Narrow tuning-only schema. No paths, models, credentials, device consent,
# API settings, automatic actions, hotkeys, runtime selection or edition gates.
NUMBER_FIELDS={
 'confidence':(.01,1),'nms_iou':(.01,1),'acquisition_radius':(1,2000),
 'near_radius':(1,2000),'snap_radius':(0,2000),'retention_radius':(1,4000),
 'gain':(0,10),'max_step':(1,2000),'deadzone':(0,1000),
 'movement_smoothing':(0,.98),'anchor_x':(-1,1),'anchor_y':(-1,1),
 'assistance_x':(0,4),'assistance_y':(0,4),'acquire_confidence':(0,1),'retain_confidence':(0,1)}
INTEGER_FIELDS={'acquisition_radius','near_radius','snap_radius','retention_radius','max_step','deadzone'}
CHOICE_FIELDS={'anchor_mode':('center','top','bottom','upper-quarter'),'fov_shape':('circle','rectangle')}


def validated_catalog(entries=None):
    entries=ENTRIES if entries is None else entries
    if type(entries) not in (tuple,list) or len(entries)>64:raise ValueError('Invalid official catalog')
    result=[];ids=set()
    for entry in entries:
        if type(entry) is not dict or set(entry)!={'id','name','description','settings','image'}:
            raise ValueError('Invalid official game')
        key=entry['id']
        if type(key) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',key) or key in ids:
            raise ValueError('Invalid game ID')
        ids.add(key);name=plain(entry['name'],80);description=plain(entry['description'],600)
        if not name:raise ValueError('Missing game name')
        settings=entry['settings']
        if type(settings) is not dict or not settings:raise ValueError('Missing game defaults')
        for field,value in settings.items():
            if field in NUMBER_FIELDS:
                low,high=NUMBER_FIELDS[field]
                if type(value) not in (int,float) or not low<=value<=high:raise ValueError('Invalid game setting')
                if field in INTEGER_FIELDS and type(value) is not int:raise ValueError('Invalid game integer')
            elif field in CHOICE_FIELDS:
                if type(value) is not str or value not in CHOICE_FIELDS[field]:raise ValueError('Invalid game choice')
            else:raise ValueError('Game setting not permitted')
        image=entry['image']
        if type(image) is not bytes or len(image)>1024*1024:raise ValueError('Invalid game artwork')
        result.append(dict(id=key,name=name,description=description,settings=dict(settings),image=image))
    return tuple(result)
