"""Architecture-defined in-memory graphs for VaporLock's .cloud backend.

No Torch tracing, graph files, supplied operators, checkpoint objects or model
code. The only variable inputs are the SDK's strict FP32 tensor schema. These
live graphs deliberately have one image per observation; training is separate.
Graphs are private execution details; weights and model equations are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from cloud_contract import CloudInputError, MAX_WEIGHTS

GRAPH_REVISION = 1
MAX_GRAPH_BYTES = MAX_WEIGHTS + 2 * 1024 * 1024


@dataclass(frozen=True)
class CloudGraphs:
    detector: bytes
    wrapper: bytes
    roi_wrappers: tuple = ()


class _Graph:
    def __init__(self):
        import numpy as np
        import onnx
        self.np = np; self.onnx = onnx
        self.nodes = []; self.initializers = []; self.names = set(); self.serial = 0

    def tensor(self, name, value):
        if name in self.names:
            raise CloudInputError('Duplicate internal .cloud graph value.')
        self.names.add(name)
        self.initializers.append(self.onnx.numpy_helper.from_array(self.np.array(value, copy=True, order='C'), name))
        return name

    def constant(self, value, dtype=None):
        self.serial += 1
        return self.tensor('constant_' + str(self.serial), self.np.asarray(value, dtype=dtype or self.np.float32))

    def op(self, kind, *inputs, output=None, **attributes):
        if output is None:
            self.serial += 1; output = 'value_' + str(self.serial)
        self.nodes.append(self.onnx.helper.make_node(kind, list(inputs), [output], **attributes))
        return output

    def conv(self, source, name, weights, *, stride=1, dilation=1, groups=1, relu=False):
        for suffix in ('.weight', '.bias'):
            key = name + suffix
            if key not in self.names:
                self.tensor(key, weights[key])
        kernel = weights[name + '.weight'].shape[-1]; pad = dilation * (kernel // 2)
        value = self.op('Conv', source, name + '.weight', name + '.bias', strides=[stride, stride],
                        dilations=[dilation, dilation], pads=[pad] * 4, group=groups)
        return self.op('Relu', value) if relu else value

    def separable(self, source, name, weights, channels, *, stride=1, dilation=1):
        source = self.conv(source, name + '_dw', weights, stride=stride, dilation=dilation, groups=channels, relu=True)
        return self.conv(source, name + '_pw', weights, relu=True)

    def resize(self, source, *, linear=False):
        return self.op('Resize', source, '', self.constant([1, 1, 2, 2]),
                       mode='linear' if linear else 'nearest',
                       coordinate_transformation_mode='half_pixel' if linear else 'asymmetric',
                       nearest_mode='floor')

    def slice(self, source, start, end):
        return self.op('Slice', source, self.constant([start], self.np.int64),
                       self.constant([end], self.np.int64), self.constant([1], self.np.int64))

    def reshape(self, source, shape):
        return self.op('Reshape', source, self.constant(shape, self.np.int64))

    def value(self, name, shape, *, integer=False):
        return self.onnx.helper.make_tensor_value_info(name,
            self.onnx.TensorProto.INT64 if integer else self.onnx.TensorProto.FLOAT, shape)

    def finish(self, name, inputs, outputs):
        if len(self.nodes) > 256 or len(self.initializers) > 192:
            raise CloudInputError('Internal .cloud graph exceeds its topology budget.')
        allowed = {'Conv', 'Relu', 'Add', 'Mul', 'Resize', 'Identity', 'Clip', 'Slice',
                   'Max', 'Sub', 'Reshape', 'GridSample', 'Transpose', 'Gather', 'Shape',
                   'Concat', 'Expand', 'Flatten', 'Gemm', 'Tanh', 'Sigmoid'}
        if any(node.op_type not in allowed or node.domain for node in self.nodes):
            raise CloudInputError('Unsupported internal .cloud operator.')
        for value in self.initializers:
            if (value.external_data or value.data_location != self.onnx.TensorProto.DEFAULT
                    or len(value.dims) > 4 or any(not 1 <= d <= 32768 for d in value.dims)):
                raise CloudInputError('Invalid or external internal .cloud tensor storage.')
        graph = self.onnx.helper.make_graph(self.nodes, name, inputs, outputs, self.initializers)
        model = self.onnx.helper.make_model(graph, producer_name='VaporLock Cloud',
            producer_version=str(GRAPH_REVISION), opset_imports=[self.onnx.helper.make_opsetid('', 17)])
        # Opset17 needs no newer IR feature. Avoid coupling model loading to the
        # ONNX package's latest default IR version.
        model.ir_version = 8
        if model.ByteSize() > MAX_GRAPH_BYTES:
            raise CloudInputError('Internal .cloud graph exceeds its allocation limit.')
        self.onnx.checker.check_model(model, full_check=True)
        return model.SerializeToString()


def build_graphs(config, weights, *, roi_counts=()):
    """Build only the two known live architectures from validated learned values.

    Call after enabling the pinned runtime source. There are no file writes and
    no file-supplied graph nodes, external data references or custom operators.
    """
    import numpy as np
    from cloud_sdk.config import ModelConfig, weight_shapes
    if type(config) is not ModelConfig or type(weights) is not dict:
        raise CloudInputError('Unsupported .cloud graph inputs.')
    if roi_counts not in ((),(1,4,16)):
        raise CloudInputError('Unsupported .cloud live ROI shapes.')
    expected = weight_shapes(config)
    if set(weights) != set(expected):
        raise CloudInputError('The .cloud weight directory does not match its architecture.')
    total = 0
    for name, shape in expected.items():
        value = weights[name]
        if (type(value) is not np.ndarray or value.shape != shape or value.dtype != np.dtype('<f4')
                or not np.isfinite(value).all()):
            raise CloudInputError('The .cloud graph requires finite FP32 architecture weights.')
        total += value.nbytes
    if total > MAX_WEIGHTS:
        raise CloudInputError('The .cloud weights exceed their allocation limit.')
    return CloudGraphs(_detector(config, weights), _wrapper(config, weights),
                       tuple((count,_wrapper(config,weights,count)) for count in roi_counts))


def _detector(config, weights):
    g = _Graph(); c = config.channels; size = config.input_size
    features = g.conv('images', 'conv1', weights, stride=2, relu=True)
    if config.is_mask:
        detail = g.separable(features, 'detail', weights, c // 2, stride=2)
        stage8 = g.separable(detail, 'stage8', weights, c, stride=2)
        stage16 = g.separable(stage8, 'stage16', weights, c, stride=2)
        stage32 = g.separable(stage16, 'stage32', weights, c, stride=2)
        context = g.separable(stage32, 'context', weights, c, dilation=2)
        pyramid = g.op('Mul', g.op('Add', stage32, context), g.constant([.5]))
        for stage, name in ((stage16, 'fuse16'), (stage8, 'fuse8'), (detail, 'fuse4')):
            pyramid = g.op('Mul', g.op('Add', stage, g.resize(pyramid)), g.constant([.5]))
            pyramid = g.separable(pyramid, name, weights, c)
        features = pyramid
    else:
        features = g.conv(features, 'conv2', weights, stride=2, relu=True)
        features = g.conv(features, 'conv3', weights, stride=2, relu=True)
        for number, dilation in enumerate((1, 2, 4, 8), 1):
            context = g.conv(features, 'context' + str(number), weights, dilation=dilation)
            features = g.op('Relu', g.op('Add', features, context))
    g.op('Identity', features, output='features')
    logits = g.conv(features, 'objectness', weights); boxes = g.conv(features, 'box_head', weights)
    g.op('Identity', logits, output='objectness_logits'); g.op('Identity', boxes, output='raw_boxes')
    side = size // config.grid_stride
    return g.finish('VaporLockCloudDetector', [g.value('images', [1, 3, size, size])],
                    [g.value('features', [1, c, side, side]),
                     g.value('objectness_logits', [1, 1, side, side]), g.value('raw_boxes', [1, 4, side, side])])


def _wrapper(config, weights, count=None):
    g = _Graph(); np = g.np; c = config.channels; sample = config.sample_size
    boxes = g.op('Clip', 'boxes', g.constant(0), g.constant(1))
    origin = g.slice(boxes, 0, 2)
    extent = g.op('Max', g.op('Sub', g.slice(boxes, 2, 4), origin), g.constant(0))
    line = ((np.arange(sample, dtype=np.float32) + np.float32(.5)) / sample if config.is_mask
            else np.linspace(0, 1, sample, dtype=np.float32))
    gy, gx = np.meshgrid(line, line, indexing='ij')
    grid = g.constant(np.stack((gx, gy), axis=-1)[None])
    coordinates = g.op('Add', g.reshape(origin, [-1, 1, 1, 2]),
        g.op('Mul', grid, g.reshape(extent, [-1, 1, 1, 2])))
    coordinates = g.op('Sub', g.op('Mul', coordinates, g.constant(2)), g.constant(1))
    # Live observations have batch1. One shared feature map serves all selected
    # boxes; never expand/copy it once per ROI. Grid layout preserves ROI order.
    grid = g.reshape(coordinates, [1, -1, sample, 2])
    sampled = g.op('GridSample', 'features', grid, mode='bilinear', padding_mode='zeros', align_corners=1)
    sampled = g.op('Transpose', g.reshape(sampled, [c, -1, sample, sample]), perm=[1, 0, 2, 3])
    if config.is_mask:
        xy = np.stack((gx * np.float32(2) - 1, gy * np.float32(2) - 1))[None]
        shape=(g.constant([count,2,sample,sample],np.int64) if count is not None else
               g.op('Concat',g.op('Gather',g.op('Shape',sampled),g.constant([0],np.int64),axis=0),
                    g.constant([2,sample,sample],np.int64),axis=0))
        xy = g.op('Expand', g.constant(xy), shape)
        encoded = g.conv(g.op('Concat', sampled, xy, axis=1), 'mask_reduce', weights, relu=True)
        residual = g.conv(encoded, 'mask_dw', weights, groups=config.mask_channels, relu=True)
        residual = g.conv(residual, 'mask_pw', weights, relu=True)
        encoded = g.op('Relu', g.op('Add', encoded, residual))
        encoded = g.resize(encoded, linear=True)
        residual = g.conv(encoded, 'mask_refine_dw', weights, groups=config.mask_channels)
        encoded = g.op('Relu', g.op('Add', encoded, residual))
        result = g.conv(encoded, 'mask_out', weights)
        name = 'mask_logits'; shape = [count or 'detections', 1, config.mask_size, config.mask_size]
    else:
        result = g.op('Flatten', sampled, axis=1)
        for name in ('roi_fc', 'wrapper_head'):
            for suffix in ('.weight', '.bias'):
                g.tensor(name + suffix, weights[name + suffix])
            result = g.op('Gemm', result, name + '.weight', name + '.bias', transB=1)
            result = g.op('Relu' if name == 'roi_fc' else 'Tanh', result)
        result = g.reshape(result, [-1, config.vertices, 2])
        # The S1 architecture's fixed perimeter template; not a learned tensor.
        distance = np.arange(config.vertices, dtype=np.float32) * np.float32(4 / config.vertices)
        x = np.where(distance < 1, distance, np.where(distance < 2, np.ones_like(distance),
                     np.where(distance < 3, 3 - distance, np.zeros_like(distance))))
        y = np.where(distance < 1, np.zeros_like(distance), np.where(distance < 2, distance - 1,
                     np.where(distance < 3, np.ones_like(distance), 4 - distance)))
        template = np.stack((x, y), axis=-1) * np.float32(.9) + np.float32(.05)
        template = np.log(template / (1 - template)).astype(np.float32)
        result = g.op('Sigmoid', g.op('Add', g.constant(template), g.op('Mul', result, g.constant(5))))
        result = g.op('Add', g.reshape(origin, [-1, 1, 2]), g.op('Mul', result, g.reshape(extent, [-1, 1, 2])))
        name = 'wrappers'; shape = [count or 'detections', config.vertices, 2]
    g.op('Identity', result, output=name)
    side = config.input_size // config.grid_stride
    # Only one-image observations are admitted. The adapter checks batch_indices
    # are all zero before running this graph, so no unused index input is needed.
    return g.finish('VaporLockCloudWrapper', [g.value('features', [1, c, side, side]),
                    g.value('boxes', [count or 'detections', 4])], [g.value(name, shape)])
