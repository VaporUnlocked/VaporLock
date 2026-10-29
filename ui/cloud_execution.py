"""Original VaporLock execution improvements for the in-house Cloud trainer.

Candidate optimizations are explicit and are NOT enabled by the worker until
matched output/gradient and repeated timing checks pass. Network equations,
sample order, losses, precision, thresholds and optimizer settings are unchanged.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager,nullcontext


def background_proposals(decoded, logits, targets, limit, input_size):
    """Keep the quick selection path; suppress long rejection runs together.

    The baseline visits rejected candidates one by one and reads a GPU boolean
    for each. After four consecutive rejects, filter all remaining candidates
    against the retained boxes in one vector pass. Ordinary two-box selection
    keeps its short path. Eligibility, score ties, >0 intersection and >.5
    suppression comparisons are deliberately identical to the baseline.
    """
    import torch
    device = decoded.device
    selected = []; batches = []
    if not limit:
        return decoded.new_empty((0, 4)), torch.empty(0, dtype=torch.int64, device=device)
    with torch.no_grad():
        for batch, target in enumerate(targets):
            all_boxes = decoded[batch].reshape(-1, 4).detach()
            all_scores = logits[batch, 0].reshape(-1).float().detach()
            order = torch.argsort(all_scores, descending=True, stable=True)[:min(512, len(all_boxes))]
            candidates = all_boxes[order]
            sizes = candidates[:, 2:] - candidates[:, :2]
            eligible = (sizes >= 1 / input_size).all(dim=1)
            truth = target['boxes'].to(device)
            if len(truth):
                overlap = (torch.minimum(candidates[:, None, 2:], truth[None, :, 2:]) -
                           torch.maximum(candidates[:, None, :2], truth[None, :, :2]))
                eligible &= ~(overlap > 0).all(dim=2).any(dim=1)
            candidates = candidates[eligible]
            chosen = []; rejected = 0
            # Index only the candidates we actually visit. Iterating a whole
            # Tensor eagerly creates an unbound Tensor object for every row,
            # even when the second accepted candidate ends selection.
            while len(candidates) and len(chosen) < limit:
                candidate = candidates[0]
                candidates = candidates[1:]
                if chosen:
                    previous = torch.stack(chosen)
                    intersection = (torch.minimum(previous[:, 2:], candidate[2:]) -
                                    torch.maximum(previous[:, :2], candidate[:2])).clamp_min(0).prod(dim=1)
                    union = ((previous[:, 2:] - previous[:, :2]).prod(dim=1) +
                             (candidate[2:] - candidate[:2]).prod() - intersection).clamp_min(1e-8)
                    if bool((intersection / union > .5).any()):
                        rejected += 1
                        if rejected >= 4 and len(candidates):
                            intersection = (torch.minimum(candidates[:, None, 2:], previous[None, :, 2:]) -
                                            torch.maximum(candidates[:, None, :2], previous[None, :, :2])).clamp_min(0).prod(dim=2)
                            union = ((previous[:, 2:] - previous[:, :2]).prod(dim=1)[None, :] +
                                     (candidates[:, 2:] - candidates[:, :2]).prod(dim=1)[:, None] - intersection).clamp_min(1e-8)
                            candidates = candidates[~(intersection / union > .5).any(dim=1)]
                            rejected = 0
                        continue
                chosen.append(candidate); rejected = 0
            selected.extend(chosen); batches.extend([batch] * len(chosen))
    return (torch.stack(selected) if selected else decoded.new_empty((0, 4)),
            torch.as_tensor(batches, dtype=torch.int64, device=device))


class MaskCache:
    """Byte-bounded reuse of immutable source labels, never augmented samples."""
    def __init__(self, decode, maximum_bytes=64 * 1024 * 1024):
        if type(maximum_bytes) is not int or not 0 <= maximum_bytes <= 256 * 1024 * 1024:
            raise ValueError('Invalid source-mask cache budget.')
        self.decode = decode; self.maximum_bytes = maximum_bytes
        self.items = OrderedDict(); self.bytes = 0; self.hits = 0; self.misses = 0

    def __call__(self, record):
        # Records belong to the held, immutable manifest snapshot for this job.
        # Retain the record itself to make Python object-id reuse impossible.
        key = id(record)
        cached = self.items.pop(key, None)
        if cached is not None:
            self.items[key] = cached; self.hits += 1
            return list(cached[1]), list(cached[2])
        self.misses += 1
        sources, origins = self.decode(record)
        size = sum(source.nbytes for source in sources)
        if self.maximum_bytes and size <= self.maximum_bytes:
            for source in sources:
                source.setflags(write=False)
            while self.items and self.bytes + size > self.maximum_bytes:
                _, discarded = self.items.popitem(last=False); self.bytes -= discarded[3]
            # Zero-instance negative images must not create an unbounded cache
            # merely because their decoded pixel byte count is zero.
            if len(self.items) >= 20_000:
                _, discarded = self.items.popitem(last=False); self.bytes -= discarded[3]
            self.items[key] = (record, tuple(sources), tuple(origins), size)
            self.bytes += size
        return sources, origins

    def clear(self):
        self.items.clear(); self.bytes = 0


@contextmanager
def training_execution(training, *, cache_bytes=64 * 1024 * 1024, fast_rgb=True):
    """Harness-only candidate path until parity/timing gates are satisfied."""
    from cloudstudio import training_masks
    old_proposals = training_masks._background_proposals
    old_decode = training._decoded_mask_sources
    cache = MaskCache(old_decode, cache_bytes)
    from cloud_training_input import training_preparation
    try:
        training_masks._background_proposals = background_proposals
        training._decoded_mask_sources = cache
        with training_preparation(training) if fast_rgb else nullcontext():
            yield cache
    finally:
        training_masks._background_proposals = old_proposals
        training._decoded_mask_sources = old_decode
        cache.clear()
