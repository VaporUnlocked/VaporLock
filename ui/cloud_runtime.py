"""VaporLock-owned prepared-input execution for validated Cloud models.

The prediction equations originate from our preserved owner SDK. This explicit
prepared-input entry removes the PIL round trip for native frames; the immutable
SDK remains an independent parity reference. Generic PIL predict is inherited.
No model/request/environment field supplies code, preparation or runtime classes.
"""
from __future__ import annotations

import time
import numpy as np
from cloud_sdk import runtime as _sdk
from cloud_sdk.geometry import letterbox as _BASE_LETTERBOX
from cloud_sdk.masks import project_roi_mask as _BASE_PROJECT
from cloud_sdk.masks import encode_mask as _BASE_ENCODE

_BASE_PREDICT=_sdk.CloudRuntime.predict
_BASE_PREVIEW=_sdk._mask_preview


class CloudRuntime(_sdk.CloudRuntime):
    def predict_bgrx(self,width,height,pixels,**settings):
        from cloud_frame_input import prepare_bgrx
        return _predict_prepared(self,lambda:prepare_bgrx(width,height,pixels,self.config.input_size),**settings)


_FRAME_PREDICT=CloudRuntime.predict_bgrx


def owns_frame_path(runtime):
    """Respect generic, subclassed and overridden prediction/preparation paths."""
    return (type(runtime) is CloudRuntime
        and getattr(runtime.predict,'__func__',None) is _BASE_PREDICT
        and getattr(runtime.predict_bgrx,'__func__',None) is _FRAME_PREDICT
        and _sdk.letterbox is _BASE_LETTERBOX)


def uses_base_preprocessing():
    return _sdk.letterbox is _BASE_LETTERBOX


def _predict_prepared(self, prepare, confidence: float = 0.35,
            nms_iou: float = 0.5, max_detections: int = 32,
            debug: bool = False, min_height: float = 0) -> dict:
    """Return only usable encasing geometry in original, oriented image pixels."""

    for name, value in (("confidence", confidence), ("nms_iou", nms_iou)):
        if not np.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be a finite number between 0 and 1")
    if type(max_detections) is not int or not 1 <= max_detections <= 256:
        raise ValueError("max_detections must be an integer from 1 to 256")
    if not np.isfinite(min_height) or min_height < 0:
        raise ValueError("min_height must be finite and nonnegative")
    started = time.perf_counter()
    array, meta = prepare()
    preprocess_done = time.perf_counter()
    self._synchronize()
    inference_started = time.perf_counter()
    if self.backend in ("numpy", "onnx"):
        features, logits, raw_boxes = self.network.detect(array[None])
        decoded = _sdk.decode_boxes_numpy(raw_boxes, self.config)[0].reshape(-1, 4)
        scores = _sdk._sigmoid(logits[0, 0]).reshape(-1)
    else:
        torch = self._torch
        from cloud_sdk.model import decode_boxes
        with torch.inference_mode():
            input_tensor = torch.from_numpy(array[None]).to(self.device)
            features, logits, raw_boxes = self.network.detect(input_tensor)
            decoded = decode_boxes(raw_boxes, self.config)[0].reshape(-1, 4).float().cpu().numpy()
            scores = logits[0, 0].sigmoid().reshape(-1).float().cpu().numpy()
    self._synchronize()
    detector_done = time.perf_counter()
    detector_seconds = detector_done - inference_started
    inference_seconds = detector_seconds

    # Discard letterbox padding and use the visible-image ROI as guidance.
    size = self.config.input_size
    width, height = meta["original_width"], meta["original_height"]
    low = np.asarray([meta["pad_x"], meta["pad_y"]], dtype=np.float32) / size
    high = low + np.asarray([width * meta.get("scale_x", meta["scale"]),
                             height * meta.get("scale_y", meta["scale"])], dtype=np.float32) / size
    boxes = decoded.copy()
    boxes[:, :2] = np.maximum(boxes[:, :2], low)
    boxes[:, 2:] = np.minimum(boxes[:, 2:], high)
    extents = boxes[:, 2:] - boxes[:, :2]
    original_heights = extents[:, 1] * size / meta.get("scale_y", meta["scale"])
    eligible = np.isfinite(scores) & np.isfinite(boxes).all(axis=1)
    eligible &= (extents[:, 0] > 1e-7) & (extents[:, 1] > 1e-7) & (original_heights >= min_height)
    valid = eligible & (scores >= confidence)
    candidates = np.flatnonzero(valid)
    candidate_limit = max(256, min(2048, max_detections * 16))
    candidates = candidates[np.argsort(-scores[candidates], kind="stable")[:candidate_limit]]
    kept = _sdk._nms(boxes[candidates], scores[candidates], nms_iou, max_detections)
    selected = candidates[kept]
    selected_boxes = boxes[selected]
    filtering_done = time.perf_counter()

    polygons: list[np.ndarray] = []
    encasing_seconds = 0.0
    if len(selected):
        self._synchronize()
        inference_started = time.perf_counter()
        # Limit the gathered ROI feature copies on the torch path.
        for start in range(0, len(selected), 16):
            batch_boxes = selected_boxes[start:start + 16]
            if self.backend in ("numpy", "onnx"):
                chunk = self.network.wrap(features, batch_boxes,
                                          np.zeros(len(batch_boxes), dtype=np.int64))
            else:
                torch = self._torch
                with torch.inference_mode():
                    chunk = self.network.wrap(features,
                                              torch.from_numpy(batch_boxes).to(self.device),
                                              torch.zeros(len(batch_boxes), dtype=torch.int64,
                                                          device=self.device)).float().cpu().numpy()
            polygons.extend(chunk)
        self._synchronize()
        encasing_seconds = time.perf_counter() - inference_started
        inference_seconds += encasing_seconds

    geometry_started = time.perf_counter()
    detections: list[dict] = []
    invalid = 0
    empty_masks = 0
    for index, polygon in zip(selected, polygons):
        if self.config.is_mask:
            from cloud_sdk.masks import project_roi_mask
            from cloud_native_masks import project_native_mask
            if not np.isfinite(polygon).all():
                invalid += 1
                continue
            source_box = _sdk.transform_box(boxes[index] * size, meta, inverse=True)
            probabilities=_sdk._sigmoid(polygon[0])
            native=(project_native_mask(probabilities,source_box,(width,height),_BASE_PROJECT,_BASE_ENCODE,_BASE_PREVIEW)
                    if type(self) is CloudRuntime and project_roi_mask is _BASE_PROJECT else None)
            encoded=native if native is not None else project_roi_mask(probabilities,source_box,(width,height))
            if (native.width==0 if native is not None else not any(encoded["counts"][1::2])):
                empty_masks += 1
                continue
            item = {"class_id": 0, "class_name": "player", "confidence": float(scores[index]),
                    "mask": encoded, "wrapper": [] if native is not None else _sdk._mask_preview(encoded, source_box),
                    "wrapper_is_approximate": True, "wrapper_kind": "mask_convex_envelope_preview"}
            if debug:
                item["primary_box"] = source_box
            detections.append(item)
            continue
        restored = _sdk.transform_points(polygon * size, meta, inverse=True)
        restored[:, 0] = restored[:, 0].clip(0, width)
        restored[:, 1] = restored[:, 1].clip(0, height)
        if (not np.isfinite(restored).all() or _sdk.polygon_area(restored) <= 1e-4
                or not _sdk._simple_prediction(restored)):
            invalid += 1
            continue
        item = {"class_id": 0, "class_name": "player", "confidence": float(scores[index]),
                "wrapper": restored.tolist()}
        if debug:
            item["primary_box"] = _sdk.transform_box(boxes[index] * size, meta, inverse=True)
        detections.append(item)
    diagnostics = None
    if debug:
        primary_detections = [
            {"class_id": 0, "class_name": "player", "confidence": float(scores[index]),
             "primary_box": _sdk.transform_box(boxes[index] * size, meta, inverse=True)}
            for index in selected
        ]
        diagnostics = {"primary_detections": primary_detections,
                       "primary_count": len(primary_detections),
                       "max_confidence": float(scores[eligible].max()) if np.any(eligible) else None,
                       "raw_max_confidence": float(scores[np.isfinite(scores)].max()) if np.isfinite(scores).any() else None,
                       "candidates_above_threshold": int(np.count_nonzero(valid)),
                       "confidence_threshold": float(confidence)}
    finished = time.perf_counter()
    preprocessing = preprocess_done - started
    total = finished - started
    result = {"detections": detections, "invalid_contours": invalid,
              "timing_ms": {"preprocess": preprocessing * 1000,
                            "inference": inference_seconds * 1000,
                            "postprocess": max(0, total - preprocessing - inference_seconds) * 1000,
                            "total": total * 1000}}
    if self.config.is_mask:
        result["empty_masks"] = empty_masks
    if diagnostics is not None:
        diagnostics["stage_timing_ms"] = {
            "detector": detector_seconds * 1000, "encasing": encasing_seconds * 1000,
            "filter": (filtering_done - detector_done) * 1000,
            "geometry": (finished - geometry_started) * 1000}
        result["diagnostics"] = diagnostics
    return result
