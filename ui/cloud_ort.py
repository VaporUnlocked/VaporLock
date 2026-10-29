"""Persistent .cloud execution with provider-local shared feature tensors.

The owned Cloud runtime preserves SDK decode/NMS and exact-mask equations while
accepting typed native frames. No graph paths, disk cache, Torch runtime,
implicit install/download, or retry on a different execution provider.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from types import MappingProxyType

from cloud_contract import CloudInputError, integer

PROVIDERS = {'cpu': 'CPUExecutionProvider', 'directml': 'DmlExecutionProvider',
             'cuda': 'CUDAExecutionProvider'}


@dataclass(frozen=True)
class _Features:
    owner: object
    generation: int
    value: object


class CloudOrtNetwork:
    def __init__(self, config, metadata, weights, *, backend='cpu', device_id=0, threads=0):
        if backend not in PROVIDERS:
            raise CloudInputError('Choose CPU, DirectML or CUDA for this .cloud backend.')
        integer(device_id, 0, 63, 'Inference GPU')
        integer(threads, 0, 64, 'Inference CPU threads')
        if backend == 'cpu' and device_id != 0:
            raise CloudInputError('A CPU .cloud session does not select a GPU.')
        import onnxruntime as ort
        from cloud_graphs import build_graphs
        provider = PROVIDERS[backend]
        if provider not in ort.get_available_providers():
            raise CloudInputError('The selected .cloud runtime is not installed. Choose another backend or install its runtime explicitly.')
        graphs = build_graphs(config, weights,roi_counts=(1,4,16) if backend=='directml' and config.is_mask else ())
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        options.add_session_config_entry('session.inter_op.allow_spinning', '0')
        if backend == 'directml':
            # Required by the DirectML EP, rather than an optional speed tweak.
            options.enable_mem_pattern = False
        provider_options = {'device_id': str(device_id)}
        if backend == 'cuda':
            provider_options['use_tf32'] = '0'
        providers = [provider] if backend == 'cpu' else [(provider, provider_options), 'CPUExecutionProvider']
        self.detector = ort.InferenceSession(graphs.detector, sess_options=options, providers=providers,
                                             enable_fallback=False, read_config_from_model=False)
        self._wrappers={count:ort.InferenceSession(graph,sess_options=options,providers=providers,
                         enable_fallback=False,read_config_from_model=False) for count,graph in graphs.roi_wrappers}
        self.wrapper = (self._wrappers[1] if self._wrappers else
                        ort.InferenceSession(graphs.wrapper,sess_options=options,providers=providers,
                                             enable_fallback=False,read_config_from_model=False))
        self.warmup_roi_counts=tuple(self._wrappers) or (1,)
        for session in (self.detector,*(tuple(self._wrappers.values()) or (self.wrapper,))):
            if provider not in session.get_providers():
                raise CloudInputError('The requested .cloud provider did not initialize. No CPU substitute was selected.')
            session.disable_fallback()
        self.config = config; self.metadata = dict(metadata)
        self.backend = backend; self.provider = provider; self.device_id = device_id
        self._device = {'cpu': 'cpu', 'directml': 'dml', 'cuda': 'cuda'}[backend]
        self._thread = threading.get_ident(); self._generation = 0; self._feature = None
        self._owner = object()
        self.roi_batch_plans = {}
        self.roi_batch_costs_ms = {}
        if self._wrappers:
            self._calibrate_roi_batches()

    def _calibrate_roi_batches(self):
        """Choose bounded head batches once, before accepting live observations.

        Padding five rows to sixteen can cost more than two smaller GPU runs.
        Measure completed, warmed heads on this device rather than imposing a
        particular GPU's preferred partition. This changes dispatch only: every
        real row still uses the same FP32 graph and its original input/order.
        """
        import numpy as np
        import statistics
        import time
        import math
        boxes = np.tile(np.array([[.1, .1, .9, .9]], dtype=np.float32), (16, 1))
        try:
            features, _, _ = self.detect(np.zeros(
                (1, 3, self.config.input_size, self.config.input_size), dtype=np.float32))
            capacities = tuple(self._wrappers)
            for capacity in capacities:
                self._run_group(features, boxes[:capacity], capacity)
            samples = {capacity: [] for capacity in capacities}
            for turn in range(3):
                order = capacities[turn:] + capacities[:turn]
                for capacity in order:
                    started = time.perf_counter_ns()
                    self._run_group(features, boxes[:capacity], capacity)
                    samples[capacity].append((time.perf_counter_ns() - started) / 1e6)
            self.roi_batch_costs_ms = {
                capacity: statistics.median(values) for capacity, values in samples.items()}
            if any(not math.isfinite(value) or value <= 0
                   for values in samples.values() for value in values):
                raise CloudInputError('The DirectML ROI calibration did not produce valid timings.')
            # Dynamic program over actual rows and at most four sequential runs.
            # Include a small per-run dispatch/concatenation allowance; ties prefer
            # fewer runs. No timing, search or allocation of sessions on live data.
            choices = {(0, 0): (0., ())}
            for count in range(1, 17):
                for runs in range(1, 5):
                    candidates = []
                    for rows in range(1, count + 1):
                        previous = choices.get((count - rows, runs - 1))
                        if previous is None:
                            continue
                        capacity = next(size for size in capacities if rows <= size)
                        candidates.append((previous[0] + self.roi_batch_costs_ms[capacity] + .01,
                                           previous[1] + ((rows, capacity),)))
                    if candidates:
                        choices[count, runs] = min(candidates, key=lambda item: item[0])
                alternatives = [choices[count, runs] for runs in range(1, 5) if (count, runs) in choices]
                best = min(item[0] for item in alternatives)
                # Within 3% of the estimate, avoid extra calls/concatenation.
                self.roi_batch_plans[count] = min(
                    (item for item in alternatives if item[0] <= best * 1.03),
                    key=lambda item: (len(item[1]), item[0], item[1]))[1]
            self.roi_batch_plans = MappingProxyType(self.roi_batch_plans)
            self.roi_batch_costs_ms = MappingProxyType(self.roi_batch_costs_ms)
        finally:
            # Calibration must not leave a synthetic feature available to callers.
            self.release_observation()

    def _tensor(self, value, shape, device):
        if (value.shape() != list(shape) or value.data_type() != 'tensor(float)'
                or value.device_name().lower() != device):
            raise CloudInputError('Unexpected .cloud tensor type, shape or device.')

    def _check_thread(self):
        # ORT DirectML sessions must never receive concurrent Run calls. A live
        # pipeline is single-owner; fail instead of racing a retained feature.
        if threading.get_ident() != self._thread:
            raise CloudInputError('A .cloud session must run on its owning pipeline thread.')

    def detect(self, images):
        self._check_thread()
        self._generation += 1; self._feature = None
        import numpy as np
        images = np.asarray(images)
        size = self.config.input_size
        if (images.dtype != np.dtype('float32') or images.shape != (1, 3, size, size)
                or not np.isfinite(images).all()):
            raise CloudInputError('The .cloud detector requires one finite FP32 RGB observation.')
        # A failed new observation also invalidates the previous feature. Never
        # allow a caller to obtain masks from an old observation after failure.
        binding = self.detector.io_binding()
        binding.bind_cpu_input('images', np.ascontiguousarray(images))
        binding.bind_output('features', self._device, self.device_id)
        binding.bind_output('objectness_logits', 'cpu')
        binding.bind_output('raw_boxes', 'cpu')
        self.detector.run_with_iobinding(binding)
        binding.synchronize_outputs()
        values = binding.get_outputs()
        side = size // self.config.grid_stride
        if len(values) != 3:
            raise CloudInputError('Unexpected .cloud detector outputs.')
        self._tensor(values[0], [1, self.config.channels, side, side], self._device)
        self._tensor(values[1], [1, 1, side, side], 'cpu')
        self._tensor(values[2], [1, 4, side, side], 'cpu')
        features = _Features(self._owner, self._generation, values[0])
        self._feature = features
        # Only the small heads needed for exact baseline selection return to
        # CPU. The feature map is passed as an OrtValue, never .numpy()'d.
        return features, values[1].numpy(), values[2].numpy()

    def wrap(self, features, boxes, batch_indices):
        self._check_thread()
        import numpy as np
        if (type(features) is not _Features or features.owner is not self._owner
                or features.generation != self._generation or features is not self._feature):
            raise CloudInputError('The .cloud features do not belong to this observation.')
        boxes = np.asarray(boxes); indices = np.asarray(batch_indices)
        if (boxes.dtype != np.dtype('float32') or boxes.ndim != 2 or boxes.shape[1] != 4
                or not 0 <= len(boxes) <= 16 or not np.isfinite(boxes).all()
                or indices.dtype != np.dtype('int64') or indices.shape != (len(boxes),)
                or np.any(indices != 0)):
            raise CloudInputError('Invalid .cloud live ROI batch.')
        name = 'mask_logits' if self.config.is_mask else 'wrappers'
        shape = ((len(boxes), 1, self.config.mask_size, self.config.mask_size) if self.config.is_mask
                 else (len(boxes), self.config.vertices, 2))
        if not len(boxes):
            return np.empty(shape, dtype=np.float32)
        if not self._wrappers:
            return self._run_group(features, boxes, len(boxes))
        chunks = []
        offset = 0
        for rows, capacity in self.roi_batch_plans[len(boxes)]:
            chunks.append(self._run_group(features, boxes[offset:offset + rows], capacity))
            offset += rows
        return chunks[0] if len(chunks) == 1 else np.concatenate(chunks, axis=0)

    def _run_group(self, features, boxes, capacity):
        import numpy as np
        count = len(boxes)
        name = 'mask_logits' if self.config.is_mask else 'wrappers'
        shape = ((count, 1, self.config.mask_size, self.config.mask_size) if self.config.is_mask
                 else (count, self.config.vertices, 2))
        wrapper = self.wrapper
        if self._wrappers:
            wrapper=self._wrappers[capacity]
            padded=np.empty((capacity,4),dtype=np.float32)
            padded[:count]=boxes;padded[count:]=boxes[0]
            boxes=padded
            shape=(capacity,*shape[1:])
        binding = wrapper.io_binding()
        binding.bind_ortvalue_input('features', features.value)
        binding.bind_cpu_input('boxes', np.ascontiguousarray(boxes))
        binding.bind_output(name, 'cpu')
        wrapper.run_with_iobinding(binding)
        binding.synchronize_outputs()
        values = binding.get_outputs()
        if len(values) != 1:
            raise CloudInputError('Unexpected .cloud ROI outputs.')
        self._tensor(values[0], shape, 'cpu')
        return values[0].numpy()[:count]

    def release_observation(self):
        self._check_thread()
        self._feature = None; self._generation += 1


def runtime_from_weights(config, metadata, weights, *, backend='cpu', device_id=0, threads=0):
    """Retain the SDK's complete prediction semantics with our network backend.

    Inputs must come from the held, verified SDK loader. This function does not
    select/read model files, acquire entitlements or bypass worker admission.
    """
    from cloud_runtime import CloudRuntime
    runtime = CloudRuntime.__new__(CloudRuntime)
    runtime.backend = 'onnx'; runtime._torch = None
    runtime.network = CloudOrtNetwork(config, metadata, weights, backend=backend, device_id=device_id, threads=threads)
    runtime.config = config; runtime.metadata = dict(metadata)
    runtime.device = 'cpu' if backend == 'cpu' else backend + ':' + str(device_id)
    return runtime
