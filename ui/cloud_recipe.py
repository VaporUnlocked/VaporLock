"""Torch-free access to the trainer's verified recipe schema."""
from functools import lru_cache
import importlib.util
import sys
from types import ModuleType

_SCHEMA_HASHES = {
    'config': 'f989231ef0f65b3f30ac3148767c2ab5a1c31520e730fb87e48df0a958f57e78',
    'training_settings': '304ffa8dd3ba39546fa396fcfad8d72c1abc550003e5e5ab96a738a481845c59',
}


@lru_cache(maxsize=1)
def schema():
    # The GUI reads only the two pure-Python schema modules. The worker uses
    # the same definitions and still loads its trainer in the isolated child.
    from cloud_contract import CloudInputError, json_value
    from dataset_paths import reject_reparse
    import hashlib

    if getattr(sys, 'frozen', False):
        from cloud_production import component_root
        source = component_root('sdk')
    else:
        from cloud_source_adapter import source_root
        source = source_root()
    root = reject_reparse(source)
    descriptor = reject_reparse(root / 'IMPORT_MANIFEST.json')
    with descriptor.open('rb') as stream:
        manifest = json_value(stream.read(128 * 1024 + 1), 128 * 1024)
    try:
        entries = {entry['path']: entry for entry in manifest['files']}
    except (TypeError, KeyError) as error:
        raise CloudInputError('The .cloud training recipe manifest is invalid.') from error
    package_name = '_vaporlock_cloud_recipe'
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package
    loaded = [package_name]
    try:
        for name, digest in _SCHEMA_HASHES.items():
            relative = f'training_source/cloudstudio/{name}.py'
            entry = entries.get(relative)
            path = reject_reparse(root / relative)
            with path.open('rb') as stream:
                source = stream.read(128 * 1024 + 1)
            if (entry is None or len(source) > 128 * 1024 or len(source) != entry.get('bytes')
                    or entry.get('sha256') != digest or hashlib.sha256(source).hexdigest() != digest):
                raise CloudInputError('The .cloud training recipe schema failed verification.')
            qualified = f'{package_name}.{name}'
            spec = importlib.util.spec_from_file_location(qualified, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified] = module
            loaded.append(qualified)
            exec(compile(source, str(path), 'exec', dont_inherit=True), module.__dict__)
        return module
    except BaseException:
        for name in loaded:
            sys.modules.pop(name, None)
        raise


BASIC_FIELDS = frozenset({'epochs', 'batch_size', 'device', 'patience'})
MODEL_FIELDS = frozenset({'architecture', 'input_size', 'channels', 'mask_size', 'vertices', 'roi_size'})
PATH_FIELDS = frozenset({'resume_path', 'init_model_path'})


def resolve_recipe(preset, overrides):
    from cloud_contract import CloudInputError

    settings = schema()
    if type(preset) is not str or preset not in settings.PRESETS:
        raise CloudInputError('Choose a supported .cloud training preset.')
    if (type(overrides) is not dict or set(overrides) - settings.SETTING_BY_NAME.keys() - PATH_FIELDS
            or any(overrides.get(key) is not None for key in PATH_FIELDS)):
        raise CloudInputError('Unsupported .cloud recipe setting.')
    try:
        resolved = settings.resolve_settings(preset, overrides=overrides)
    except ValueError as error:
        raise CloudInputError(str(error)) from error
    if resolved['allow_no_validation']:
        raise CloudInputError('Cloud Studio requires a reviewed validation split.')
    return resolved


def load_recipe(path):
    from cloud_contract import json_value
    from dataset_paths import reject_reparse

    path = reject_reparse(path)
    if not path.is_file():
        raise ValueError('Choose a .cloud training recipe JSON file.')
    with path.open('rb') as stream:
        document = json_value(stream.read(1024 * 1024 + 1), 1024 * 1024)
    if type(document) is not dict:
        raise ValueError('Choose a .cloud training recipe JSON file.')
    preset = 'player'
    if 'training_config' in document:
        settings = schema()
        if (set(document) - settings.RUN_CONFIG_FIELDS
                or not {'run_id', 'training_config', 'model_config'} <= document.keys()):
            raise ValueError('Invalid .cloud run configuration.')
        model = settings.ModelConfig.from_dict(document['model_config'])
        config = settings.validate_settings(document['training_config'], partial=True)
        config = {name: value for name, value in config.items() if name not in PATH_FIELDS}
        resolved = resolve_recipe('legacy', config)
        if model != settings.ModelConfig(**{key: resolved[key] for key in MODEL_FIELDS}):
            raise ValueError('Run configuration and model dimensions disagree.')
        return 'legacy', resolved
    elif 'settings' in document or 'format' in document:
        if (document.get('format') != 'cloudstudio-training-settings'
                or type(document.get('version')) is not int or document['version'] != 1
                or set(document) - {'format', 'version', 'preset', 'settings'}):
            raise ValueError('Unsupported .cloud training recipe format.')
        preset, document = document.get('preset', 'player'), document.get('settings')
        if type(document) is dict and 'architecture' not in document:
            document = {**document, 'architecture': 'cloudvision_s1_v2'}
    if type(document) is not dict:
        raise ValueError('Invalid .cloud training recipe settings.')
    values = {name: value for name, value in document.items() if name not in PATH_FIELDS}
    return preset, resolve_recipe(preset, values)


def save_recipe(path, preset, values):
    import json
    from dataset_paths import reject_reparse
    from dataset_storage import atomic_text

    settings = resolve_recipe(preset, values)
    document = {'format': 'cloudstudio-training-settings', 'version': 1, 'preset': preset,
                'settings': {key: value for key, value in settings.items() if value is not None}}
    atomic_text(reject_reparse(path), json.dumps(document, indent=2, allow_nan=False) + '\n')
