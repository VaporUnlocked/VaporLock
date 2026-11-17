"""Aggregate the existing .cloud box matches by target size and visibility."""

SIZE_BANDS = {
    "small": "Small (<32 px)",
    "medium": "Medium (32–95 px)",
    "large": "Large (96+ px)",
}
VISIBILITY = {
    "unlabeled": "Visibility not labeled",
    "visible": "Fully visible",
    "partial": "Partially occluded",
    "heavy": "Heavily occluded",
}
SLICE_LABELS = {**SIZE_BANDS, **VISIBILITY}


class EvaluationSlices:
    def __init__(self):
        self.totals = {key: [0, 0, 0.0] for key in SLICE_LABELS}

    def add(self, record, matches):
        matched = {target_index: float(overlap) for _, target_index, overlap in matches}
        scale = 640 / max(record["width"], record["height"])
        for index, target in enumerate(record["annotations"]):
            left, top, right, bottom = target["box"]
            size = min(right - left, bottom - top) * scale
            band = "small" if size < 32 else "medium" if size < 96 else "large"
            visibility = target.get("visibility", "unlabeled")
            if type(visibility) is not str or visibility not in VISIBILITY:
                visibility = "unlabeled"
            for key in (band, visibility):
                bucket = self.totals[key]
                bucket[0] += 1
                if index in matched:
                    bucket[1] += 1
                    bucket[2] += matched[index]

    def results(self):
        return {name: dict(targets=targets, matched=matched, mean_iou=iou / matched if matched else None)
                for name, (targets, matched, iou) in self.totals.items()}
