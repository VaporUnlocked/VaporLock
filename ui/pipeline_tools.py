"""Original local measurements, conservative recommendations and file identities."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import time


def finite(value):
    try:
        return type(value) in (float, int) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def pipeline_projection(metrics):
    """Bounded decimal strings preserve the integer-only bridge JSON grammar."""
    return {key: f'{value:.1f}' if finite(value) and value <= 1_000_000_000 else None
            for key, value in metrics.items()}


def pipeline_metrics(data, *, elapsed=0, active=True, native_now=None):
    """Allowlisted facts. Old samples are never presented as current output."""
    empty = dict.fromkeys(('capture', 'inference', 'writes', 'age', 'inference_ms'))
    if not active or type(data) is not dict or not finite(elapsed) or elapsed > 2:
        return empty
    emitted = data.get('emitted_tick_ms')
    if (type(native_now) is not int or type(emitted) is not int
            or not 0 <= emitted <= native_now or native_now-emitted > 2000):
        return empty
    for target, key in (('capture', 'capture_fps'), ('inference', 'inference_fps'),
                        ('writes', 'output_fps'), ('inference_ms', 'inference_ms')):
        value = data.get(key)
        if finite(value): empty[target] = float(value)
    age = data.get('output_observation_age_ms')
    if (finite(age) and type(native_now) is int and type(emitted) is int
            and 0 <= emitted <= native_now and native_now-emitted <= 2000):
        empty['age'] = float(age) + native_now-emitted
    return empty


def recommended_rates(results):
    """Conservative measured starting point; no optimality/quality claim."""
    choices = []
    for result in results:
        if (type(result) is not dict or result.get('provider') not in ('dml','cuda','trt','cpu')
                or not all(finite(result.get(k)) for k in ('fps','inference_ms','frames','seconds'))
                or result['fps'] <= 0 or result['inference_ms'] <= 0
                or result['frames'] < 8 or result['seconds'] < 2):
            continue
        # Do not extrapolate past observed completed work; reserve 10% headroom.
        rate = max(1, min(120, math.floor(min(result['fps'], 1000 / result['inference_ms']) * .9)))
        choices.append((rate, -result['inference_ms'], result['provider']))
    if not choices: return None
    rate, _, provider = max(choices)
    return {'provider': provider,
            'capture_fps': rate, 'inference_fps': rate, 'frame_skip': 0}


class IdentityUnavailable(ValueError):
    pass


def model_identity(path, *, cancelled=lambda: False, seconds=30, max_bytes=2*1024**3, allow_external=True):
    """Hash ONNX and bounded local tensor dependencies; no compatibility claim.

    Wire field numbers follow the public ONNX schema (onnx/onnx.proto).
    Tensor data is skipped during the metadata walk, then hashed as raw bytes.
    Future/unsupported metadata returns unknown rather than guessed compatibility.
    """
    path = Path(path).absolute()
    if str(path).startswith(('\\\\', '//')) or path.suffix.lower() != '.onnx':
        raise IdentityUnavailable('A local ONNX model is required')
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 0x400:
            raise IdentityUnavailable('Model identity cannot traverse reparse points')
    path = path.resolve(strict=True)
    deadline = time.monotonic() + seconds
    fields_left = 1_000_000
    dependencies=set()
    def check():
        if cancelled() or time.monotonic() >= deadline:
            raise IdentityUnavailable('Model identity check cancelled or timed out')
    # Windows Python can report different ctime semantics for stat and fstat
    # (creation time versus metadata change time). File identity, size and write
    # time are comparable across both APIs; the content digest binds the bytes.
    def signature(stat): return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= max_bytes:
            raise IdentityUnavailable('Model exceeds the identity check limit')
        def varint(end):
            result = 0
            for shift in range(0, 70, 7):
                if stream.tell() >= end: raise IdentityUnavailable('Invalid model metadata')
                raw = stream.read(1)
                if len(raw) != 1: raise IdentityUnavailable('Model metadata changed during reading')
                byte = raw[0]
                result |= (byte & 127) << shift
                if not byte & 128: return result
            raise IdentityUnavailable('Invalid model metadata')
        def walk(kind, end, depth=0):
            nonlocal fields_left
            if depth > 32: raise IdentityUnavailable('Model metadata nesting exceeds limit')
            entry={}; locations=[]; external=False
            while stream.tell() < end:
                check(); fields_left -= 1
                if fields_left < 0: raise IdentityUnavailable('Model metadata exceeds limit')
                tag = varint(end); field, wire = tag >> 3, tag & 7
                if field == 0: raise IdentityUnavailable('Invalid model metadata')
                supported = {'model': {1,2,3,4,5,6,7,8,14,20,25},
                             'graph': {1,2,5,10,11,12,13,14,15,16},
                             'node': {1,2,3,4,5,6,7,8,9,10},
                             'tensor': {1,2,3,4,5,6,7,8,9,10,11,12,13,14,16},
                             'attribute': {1,2,3,4,5,6,7,8,9,10,11,13,14,15,20,21,22,23},
                             'function': {1,4,5,6,7,8,9,10,11,12,13,14},
                             'training': {1,2,3,4}, 'sparse': {1,2,3}, 'entry': {1,2}}
                if field not in supported[kind]:
                    raise IdentityUnavailable('Unknown model dependency metadata')
                if wire == 0:
                    value = varint(end)
                    if kind == 'tensor' and field == 14:
                        if value not in (0,1): raise IdentityUnavailable('Unknown tensor storage')
                        external=value==1
                    continue
                if wire in (1,5):
                    stop = stream.tell() + (8 if wire == 1 else 4)
                elif wire == 2:
                    length = varint(end); stop = stream.tell() + length
                else: raise IdentityUnavailable('Invalid model metadata')
                if stop > end: raise IdentityUnavailable('Invalid model metadata')
                child = {('model',7): 'graph', ('graph',1): 'node', ('graph',5): 'tensor',
                         ('node',5): 'attribute', ('model',20): 'training', ('model',25): 'function',
                         ('training',1): 'graph', ('training',2): 'graph', ('function',7): 'node',
                         ('function',11): 'attribute', ('graph',15): 'sparse',
                         ('sparse',1): 'tensor', ('sparse',2): 'tensor',
                         ('attribute',5): 'tensor', ('attribute',10): 'tensor',
                         ('attribute',6): 'graph', ('attribute',11): 'graph',
                         ('attribute',22): 'sparse', ('attribute',23): 'sparse',
                         ('tensor',13): 'entry'}.get((kind,field))
                if child:
                    if wire != 2: raise IdentityUnavailable('Invalid model metadata')
                    value=walk(child, stop, depth+1)
                    if child=='entry' and value.get(1)=='location':
                        locations.append(value.get(2,''))
                elif kind=='entry':
                    if wire!=2 or stop-stream.tell()>4096 or field in entry:
                        raise IdentityUnavailable('Invalid external tensor metadata')
                    entry[field]=stream.read(stop-stream.tell()).decode('utf-8',errors='strict')
                else: stream.seek(stop)
            if kind=='tensor' and (external or locations):
                if len(locations)!=1 or not locations[0]: raise IdentityUnavailable('Missing external tensor location')
                relative=locations[0].replace('\\','/')
                if (relative.startswith('/') or ':' in relative or any(p in ('','..','.') for p in relative.split('/'))):
                    raise IdentityUnavailable('External tensor must remain in the model folder')
                dependencies.add(relative)
                if len(dependencies)>64: raise IdentityUnavailable('Too many model dependencies')
            return entry
        walk('model', before.st_size)
        if dependencies and not allow_external:raise IdentityUnavailable('Assist requires a self-contained ONNX model.')
        stream.seek(0); digest = hashlib.sha256()
        while chunk := stream.read(1024*1024):
            check(); digest.update(chunk)
        if signature(os.fstat(stream.fileno())) != signature(before) or signature(path.stat()) != signature(before):
            raise IdentityUnavailable('Model changed during the identity check')
    closure=[]; total=before.st_size
    for relative in sorted(dependencies):
        dependency=path.parent/relative
        for part in (dependency,*dependency.parents):
            if part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400:
                raise IdentityUnavailable('External tensor cannot traverse reparse points')
        with dependency.open('rb') as stream:
            prior=os.fstat(stream.fileno());total+=prior.st_size
            if total>8*1024**3: raise IdentityUnavailable('Model dependencies exceed limit')
            hashed=hashlib.sha256()
            while chunk:=stream.read(1024*1024): check();hashed.update(chunk)
            if signature(os.fstat(stream.fileno()))!=signature(prior) or signature(dependency.stat())!=signature(prior):
                raise IdentityUnavailable('Model dependency changed')
            closure.append((relative,prior.st_size,hashed.hexdigest()))
    if signature(path.stat())!=signature(before): raise IdentityUnavailable('Model changed')
    return (str(path), before.st_size, digest.hexdigest(), tuple(closure))


def engine_identity(paths, *, cancelled=lambda:False, seconds=30):
    """Bind measurements to exact executable content, without exposing paths."""
    deadline=time.monotonic()+seconds; result=[]
    for provider,path in sorted(paths.items()):
        path=Path(path)
        with path.open('rb') as stream:
            before=os.fstat(stream.fileno())
            if before.st_size>256*1024**2: raise IdentityUnavailable('Engine exceeds identity limit')
            digest=hashlib.sha256()
            while chunk:=stream.read(1024*1024):
                if cancelled() or time.monotonic()>=deadline: raise IdentityUnavailable('Engine identity check stopped')
                digest.update(chunk)
            after=path.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
                raise IdentityUnavailable('Engine changed during identity check')
            result.append((provider,str(path),before.st_size,digest.hexdigest()))
    return tuple(result)


def profile_summary(settings):
    """Explicit allowlist; never serialize credentials or arbitrary metadata."""
    raw = getattr(settings, 'model_path', '')
    model = str(raw).replace('\\', '/').rsplit('/', 1)[-1][:160]
    provider = getattr(settings, 'provider', 'auto')
    if provider not in ('auto','dml','cuda','trt','cpu'): provider = 'Unknown'
    width, height = getattr(settings, 'region_width', None), getattr(settings, 'region_height', None)
    region = f'{width} × {height}' if all(type(x) is int and 1 <= x <= 32768 for x in (width,height)) else 'Unknown'
    return {'model': model or 'Test signal', 'provider': provider, 'capture': region,
            'compatibility': 'Unknown / not validated'}
