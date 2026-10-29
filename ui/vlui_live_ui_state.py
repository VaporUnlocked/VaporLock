"""Bounded owner-scoped UI history. Never Settings, permission or command authority."""
from copy import deepcopy
import json
from vlui_numeric_catalog import FIELDS as NUMERIC_FIELDS
from vlui_discrete_catalog import FIELDS as DISCRETE_FIELDS

ROUTES=frozenset(('general','overview','model','detection','selection','output','dataset','profiles','appearance','shortcuts','automation','diagnostics'))
FIELDS=frozenset(('locale','mode','scale','name','selectProfile','fps','open','save','saveAs','reset','undo','resetPalette')) | frozenset(
    role+':'+kind for role in ('steel','cobalt','ice','baby','ultraviolet','carbon','pink','highlight','border','button_text') for kind in ('color','opacity')) | frozenset(
    field_id+':'+kind for field_id in NUMERIC_FIELDS for kind in ('slider','expert')) | frozenset(DISCRETE_FIELDS)

def valid_ui_state(state):
    return (type(state) is dict and set(state)== {'version','route','field'}
            and type(state['version']) is int and state['version']==1
            and type(state['route']) is str and state['route'] in ROUTES
            and (state['field'] is None or type(state['field']) is str and state['field'] in FIELDS))

def decode_ui_state_wire(wire):
    """Strict bounded display-only projection; never a command or Settings."""
    if type(wire) is not str or not 1<=len(wire)<=1024:return None
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('UI_HISTORY_DUPLICATE')
            result[key]=value
        return result
    def nonfinite(value):raise ValueError('UI_HISTORY_NONFINITE')
    try:state=json.loads(wire,object_pairs_hook=pairs,parse_constant=nonfinite)
    except (ValueError,TypeError,RecursionError):return None
    return state if valid_ui_state(state) else None

class LiveUIHistory:
    def __init__(self):self._generation=None;self._last=None
    def begin(self,generation):
        if type(generation) is not int or not 1<=generation<=2**53-1:raise ValueError('UI_HISTORY_REJECTED')
        self._generation=generation
        return deepcopy(self._last)
    def observe(self,generation,state):
        if type(generation) is not int or generation!=self._generation:return False
        if not valid_ui_state(state):
            return False
        self._last=deepcopy(state);return True
