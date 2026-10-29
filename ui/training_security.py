"""Bounded, run-only credentials and settings for private training processes."""
from __future__ import annotations

import importlib
import inspect
import json
import math
import os
import queue
import re
import threading

MAX_RECORD = 16384
MAX_CREDENTIAL = 256
SDK_VERSION = '8.4.156'
_TOKEN = re.compile(r'ul_[a-fA-F0-9]{40}')
_AUTH = re.compile(r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)[^\s,;"\x27]+')
_REMOTE = ('clearml', 'comet', 'dvc', 'hub', 'mlflow', 'neptune', 'raytune', 'wandb', 'sync')


def authentication_environment(name: str) -> bool:
    name = name.upper()
    return (name.startswith(('ULTRALYTICS_', 'WANDB_', 'COMET_', 'CLEARML_', 'NEPTUNE_', 'MLFLOW_', 'HUB_'))
            or any(word in name for word in ('API_KEY', 'APIKEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')))


def clean_environment(environment: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if not authentication_environment(key)}


def scrub_environment() -> None:
    for name in list(os.environ):
        if authentication_environment(name):
            del os.environ[name]


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate training setting.')
        result[key] = value
    return result


def credential_packet(key: str) -> bytes:
    if type(key) is not str or (key and not _TOKEN.fullmatch(key)):
        raise ValueError('The run-only Platform key has an invalid format.')
    return (json.dumps({'v': 1, 'key': key}, separators=(',', ':')) + '\n').encode('ascii')


def read_credential(stream, timeout: float = 10) -> str:
    result = queue.Queue(maxsize=1)
    def read():
        try:
            raw = stream.read(MAX_CREDENTIAL + 1)
            result.put(raw)
        except Exception:
            result.put(None)
    threading.Thread(target=read, daemon=True, name='TrainingCredential').start()
    try:
        raw = result.get(timeout=timeout)
        if not isinstance(raw, bytes) or len(raw) > MAX_CREDENTIAL:
            raise ValueError()
        payload = json.loads(raw.decode('ascii'), object_pairs_hook=pairs_unique)
        if type(payload) is not dict or set(payload) != {'v', 'key'} or type(payload['v']) is not int or payload['v'] != 1:
            raise ValueError()
        credential_packet(payload['key'])
        return payload['key']
    except Exception:
        raise ValueError('The training credential handoff was invalid or timed out.') from None


def write_credential(stream, key: str) -> None:
    raw = credential_packet(key)
    try:
        offset = 0
        while offset < len(raw):
            written = stream.write(raw[offset:])
            if not isinstance(written, int) or written <= 0:
                raise OSError('Credential pipe closed.')
            offset += written
        stream.flush()
    finally:
        stream.close()


def validate_platform(project: str, key: str) -> None:
    credential_packet(key)
    if bool(project) != bool(key):
        raise ValueError('Enter both a Platform project and run-only key, or leave both blank.')
    if project and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}', project):
        raise ValueError('Platform project must be owner/project using letters, numbers, hyphens or underscores.')


_BOUNDS = {
    'momentum': (0, 1), 'box': (0, 100), 'cls': (0, 100), 'dfl': (0, 100),
    'pose': (0, 100), 'kobj': (0, 100), 'multi_scale': (0, 1),
    'hsv_h': (0, 1), 'hsv_s': (0, 1), 'hsv_v': (0, 1), 'degrees': (0, 180),
    'translate': (0, 1), 'scale': (0, 1), 'shear': (0, 180), 'perspective': (0, .001),
    'flipud': (0, 1), 'fliplr': (0, 1), 'bgr': (0, 1), 'mosaic': (0, 1),
    'mixup': (0, 1), 'cutmix': (0, 1), 'copy_paste': (0, 1), 'erasing': (0, .9),
}
_BOOL = {'rect', 'overlap_mask', 'single_cls'}
CONTROLLED = {'data', 'epochs', 'imgsz', 'batch', 'device', 'workers', 'patience', 'optimizer',
              'lr0', 'lrf', 'weight_decay', 'warmup_epochs', 'close_mosaic', 'cache', 'amp',
              'pretrained', 'cos_lr', 'seed', 'deterministic', 'fraction', 'save', 'project', 'name', 'exist_ok'}


def load_extra_arguments(text: str) -> dict[str, object]:
    if type(text) is not str or len(text) > 4096:
        raise ValueError('Additional training settings exceed the allowed size.')
    try:
        parsed = json.loads(text, object_pairs_hook=pairs_unique) if text.strip() else {}
    except (ValueError, RecursionError):
        raise ValueError('Additional training settings must be a JSON object with unique keys.') from None
    if type(parsed) is not dict or len(parsed) > 32:
        raise ValueError('Additional training settings must be a small JSON object.')
    for key, value in parsed.items():
        if key in _BOOL:
            valid = type(value) is bool
        elif key in _BOUNDS:
            low, high = _BOUNDS[key]
            valid = type(value) in (int, float) and low <= value <= high and math.isfinite(value)
        else:
            label = key if key in CONTROLLED | {'api_key', 'token', 'resume', 'save_dir', 'model', 'task', 'mode'} else 'unsupported option'
            raise ValueError(f'Additional training setting "{label}" is not allowed. Use the dedicated training controls; extra settings accept augmentation and loss hyperparameters only.')
        if not valid:
            expected = 'true or false' if key in _BOOL else f'a number from {_BOUNDS[key][0]} to {_BOUNDS[key][1]}'
            raise ValueError(f'Training setting "{key}" requires {expected}.')
    return parsed


def validate_runner_arguments(configuration: dict) -> dict:
    args = configuration.get('train_args')
    if type(args) is not dict or len(args) > 64 or not set(args) <= CONTROLLED | set(_BOUNDS) | _BOOL:
        raise ValueError('Training contains unsupported arguments.')
    extras = {key: value for key, value in args.items() if key not in CONTROLLED}
    load_extra_arguments(json.dumps(extras))
    # No nested objects or authentication strings may be passed into SDK arguments.
    for value in args.values():
        if type(value) not in (str, int, float, bool, type(None)) or (isinstance(value, str) and (_TOKEN.search(value) or len(value) > 4096)):
            raise ValueError('Invalid controlled training argument.')
        if type(value) is float and not math.isfinite(value):
            raise ValueError('Non-finite training argument.')
    return dict(args)


class Redactor:
    def __init__(self, key: str = ''):
        self.key = key

    def text(self, value: object) -> str:
        text = str(value)
        if self.key:
            text = text.replace(self.key, '[redacted]')
        text = _TOKEN.sub('[redacted]', text)
        text = _AUTH.sub(r'\1[redacted]', text)
        return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[:MAX_RECORD]

    def payload(self, value: object, depth: int = 0):
        if depth > 6:
            return '[omitted]'
        if isinstance(value, dict):
            return {self.text(k): ('[redacted]' if authentication_environment(str(k)) else self.payload(v, depth + 1)) for k, v in list(value.items())[:64]}
        if isinstance(value, list):
            return [self.payload(v, depth + 1) for v in value[:64]]
        if isinstance(value, str):
            return self.text(value)
        return value


class SafeLogStream:
    """Accumulate complete bounded records; partial writes/flush never expose key fragments."""
    encoding = 'utf-8'
    errors = 'replace'

    def __init__(self, sink, redactor: Redactor):
        self.sink, self.redactor = sink, redactor
        self.pending = ''
        self.discarding = False
        self.lock = threading.RLock()

    def write(self, value: str) -> int:
        with self.lock:
            for part in re.split('(\n|\r)', value):
                if part in ('\n', '\r'):
                    if self.discarding:
                        self.sink('[oversized training log omitted]')
                    elif self.pending:
                        self.sink(self.redactor.text(self.pending))
                    self.pending, self.discarding = '', False
                elif not self.discarding:
                    if len(self.pending) + len(part) > MAX_RECORD:
                        self.pending, self.discarding = '', True
                    else:
                        self.pending += part
        return len(value)

    def flush(self):
        pass

    def finish(self):
        self.write('\n')

    def isatty(self):
        return False


def configure_platform(project: str, key: str):
    """Configure the explicitly supported SDK callback in memory; never call login/update."""
    validate_platform(project, key)
    scrub_environment()
    sdk = importlib.import_module('ultralytics')
    utils = importlib.import_module('ultralytics.utils')
    settings = utils.SETTINGS
    if not isinstance(settings, dict):
        raise RuntimeError('Unsupported training settings integration. Repair the training runtime.')
    for name in (*_REMOTE, 'api_key'):
        dict.__setitem__(settings, name, '' if name == 'api_key' else False)
    try:
        callback = importlib.import_module('ultralytics.utils.callbacks.platform')
    except ImportError:
        if not project:
            return None
        raise RuntimeError('Platform integration is unavailable. Repair the training runtime.') from None
    detached = dict(settings)
    detached['api_key'] = key if project else ''
    callback.SETTINGS = detached
    callback._api_key = None
    if project:
        if (sdk.__version__ != SDK_VERSION or not callable(getattr(callback, 'on_pretrain_routine_start', None))
                or not callable(getattr(callback, '_send', None))
                or 'api_key' not in inspect.signature(sdk.Platform).parameters):
            raise RuntimeError('Unsupported Platform integration. Repair the training runtime.')
        original_send = callback._send
        def safe_send(event, *args, **kwargs):
            # SDK ConsoleLogger captures before our local sink, and fragments can
            # span asynchronous chunks. Do not relay raw console content remotely.
            if event == 'console_output':
                return None
            return original_send(event, *args, **kwargs)
        callback._send = safe_send
    return callback
