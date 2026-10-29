"""Closed annotation backend choices for the licensed native worker.

No downloads, provider plugins, DLL paths or dataset writes occur here.
Provider registration is not evidence of actual GPU node execution.
"""
from dataclasses import dataclass

BACKENDS = ('auto', 'tensorrt-rtx', 'cuda', 'directml', 'cpu')
PROVIDERS = {'cuda': 'CUDAExecutionProvider', 'directml': 'DmlExecutionProvider',
             'cpu': 'CPUExecutionProvider'}


class AnnotationProviderError(RuntimeError):
    def __init__(self, code, backend):
        self.code, self.backend = code, backend
        super().__init__(f'Annotation backend {backend}: {code}')


@dataclass(frozen=True)
class ProviderSelection:
    requested: str
    selected: str
    active_providers: tuple[str, ...]
    attempts: tuple[tuple[str, str], ...]
    device_index: int | None
    node_execution_verified: bool = False


def create_annotation_session(runtime, model, requested='auto'):
    if type(requested) is not str or requested not in BACKENDS:
        raise AnnotationProviderError('INVALID_BACKEND', 'unknown')
    if requested == 'tensorrt-rtx':
        raise AnnotationProviderError('TENSORRT_ANNOTATION_UNAVAILABLE', requested)
    available = frozenset(runtime.get_available_providers())
    candidates = ('directml', 'cpu') if requested == 'auto' else (requested,)
    attempts = []
    for backend in candidates:
        primary = PROVIDERS[backend]
        if primary not in available:
            attempts.append((backend, 'NOT_INSTALLED'))
            if requested != 'auto':
                raise AnnotationProviderError('NOT_INSTALLED', backend)
            continue
        options = runtime.SessionOptions()
        options.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2
        if backend == 'directml':
            options.enable_mem_pattern = False
        if backend == 'cuda':
            providers = [(primary, {'device_id': 0, 'gpu_mem_limit': 1073741824,
                                    'do_copy_in_default_stream': True}), 'CPUExecutionProvider']
        elif backend == 'directml':
            providers = [(primary, {'device_id': 0}), 'CPUExecutionProvider']
        else:
            providers = [primary]
        session = None
        try:
            # Verified in matched ORT builds: prevents constructor CPU retries.
            session = runtime.InferenceSession(model, sess_options=options,
                providers=providers, enable_fallback=False)
            session.disable_fallback()
            actual = tuple(session.get_providers())
            if not actual or actual[0] != primary:
                raise AnnotationProviderError('PROVIDER_NOT_RETAINED', backend)
        except Exception as error:
            session = None
            code = error.code if isinstance(error, AnnotationProviderError) else 'INITIALIZATION_FAILED'
            attempts.append((backend, code))
            if requested != 'auto':
                raise AnnotationProviderError(code, backend) from None
            continue
        return session, ProviderSelection(requested, backend, actual, tuple(attempts),
                                           0 if backend != 'cpu' else None)
    raise AnnotationProviderError('NO_USABLE_PROVIDER', requested)
