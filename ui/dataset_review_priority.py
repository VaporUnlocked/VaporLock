"""Read and generate review priorities without changing labels or review decisions."""
from collections import Counter
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path

from annotation_labels import overlap
from cloud_contract import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_IMAGES
from dataset_paths import reject_reparse

FILENAME = ".vaporlock-review-priority.json"
MAX_REPORT = 8 * 1024 * 1024
SIGNALS = ("disagreement", "uncertain", "rare", "unreadable")


def read_json(path, limit=MAX_REPORT):
    if not path.is_file():
        return {}
    with reject_reparse(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Review metadata exceeds its size limit.")
    value = json.loads(raw)
    return value if type(value) is dict else {}


def image_revision(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def load_priorities(root):
    try:
        report = read_json(root / FILENAME)
        records = report.get("images")
        return records if report.get("version") == 1 and type(records) is dict else {}
    except (OSError, ValueError):
        return {}


def signals_for(records, key, path):
    record = records.get(key)
    if type(record) is not dict or type(record.get("signals")) is not list:
        return ()
    try:
        if record.get("revision") != image_revision(path):
            return ()
    except OSError:
        return ()
    return tuple(signal for signal in SIGNALS if signal in record["signals"])


def review_order(decision, signals):
    return (decision in ("accepted", "rejected"), -len(signals),
            "disagreement" not in signals, "uncertain" not in signals, "rare" not in signals)


def class_key(names, class_id):
    name = names[class_id].strip().casefold() if 0 <= class_id < len(names) else ""
    return name or f"class-{class_id}"


def predictions_disagree(primary, comparison):
    def corners(item):
        x, y, width, height = item["box"]
        return x, y, x + width, y + height

    unmatched = set(range(len(comparison)))
    for item in primary:
        candidates = [(overlap(corners(item), corners(comparison[index])), index)
                      for index in sorted(unmatched) if item["category"] == comparison[index]["category"]]
        best, index = max(candidates, default=(0, None))
        if best < .5:
            return True
        unmatched.remove(index)
    return bool(unmatched)


def discover_images(root):
    from dataset_health import IMAGE_SUFFIXES

    images = []
    for directory, folders, files in os.walk(root / "images", followlinks=False):
        path = reject_reparse(Path(directory))
        folders[:] = [name for name in sorted(folders) if reject_reparse(path / name).is_dir()]
        for name in sorted(files):
            image = path / name
            if image.suffix.lower() in IMAGE_SUFFIXES:
                images.append(reject_reparse(image))
            if len(images) > MAX_IMAGES:
                raise ValueError(f"Review analysis supports up to {MAX_IMAGES:,} images.")
    return images


def run_review_analysis(args):
    from annotation_labels import MAX_BYTES, parse_labels
    from annotation_output import held_directory, read_bytes
    from annotation_provider import create_annotation_session
    from assist_worker import predict
    from dataset_storage import atomic_text
    from dataset_transaction import HeldFile
    from dataset_worker import emit, ort, parse_names
    from PIL import Image

    root = reject_reparse(args.dataset)
    images = discover_images(root)
    decisions = read_json(root / ".vaporlock-review.json").get("decisions", {})
    if type(decisions) is not dict:
        raise ValueError("Invalid review decisions.")
    queue = [image for image in images if decisions.get(image.relative_to(root).as_posix()) != "rejected"]
    if not queue:
        raise ValueError("No eligible images were found in this dataset.")
    class_file = root / "classes.txt"
    dataset_names = read_bytes(class_file, MAX_BYTES).decode("utf-8-sig").splitlines() if class_file.is_file() else []
    threshold = args.confidence
    args.confidence = max(.01, threshold - .12)
    counts = Counter()
    categories = {}
    records = {}

    with ExitStack() as stack:
        stack.enter_context(held_directory(root))
        models = []
        for selected in filter(None, (args.model, args.compare_model)):
            path = reject_reparse(selected)
            if path.suffix.lower() != ".onnx" or not path.is_file() or path.stat().st_size > 1024**3:
                raise ValueError("Choose an existing local ONNX model under 1 GiB.")
            from dataset_paths import lock_identity
            identity = lock_identity(path)
            if any(member[0] == identity for member in models):
                raise ValueError("Choose a different ONNX model for comparison.")
            stack.enter_context(held_directory(path.parent))
            stack.enter_context(HeldFile(path, read_only=True))
            session, backend = create_annotation_session(ort, str(path), args.provider)
            emit("provider", requested=backend.requested, selected=backend.selected, device_index=backend.device_index)
            models.append((identity, session, parse_names(session)))
        if len(models) == 2 and bool(models[0][2]) != bool(models[1][2]):
            raise ValueError("For comparison, use two models with class names or two with the same numeric class mapping.")

        for index, path in enumerate(queue, 1):
            key = path.relative_to(root).as_posix()
            revision = image_revision(path)
            flags = []
            records[key] = dict(revision=revision, signals=flags)
            try:
                with Image.open(io.BytesIO(read_bytes(path, MAX_IMAGE_BYTES))) as source:
                    if source.width * source.height > MAX_IMAGE_PIXELS or getattr(source, "n_frames", 1) != 1:
                        raise ValueError("Only still images within the pixel limit are supported.")
                    pixels = source.convert("RGB")
            except (OSError, ValueError, Image.DecompressionBombError):
                flags.append("unreadable")
            else:
                with pixels:
                    predictions = []
                    for _, session, names in models:
                        detections = predict(session, pixels, args, box_only=True)["candidates"]
                        predictions.append([dict(candidate, category=class_key(names, candidate["class_id"]))
                                            for candidate in detections])
                primary = predictions[0]
                if any(abs(candidate["confidence"] - threshold) <= .12 for candidate in primary):
                    flags.append("uncertain")
                confident = [[candidate for candidate in group if candidate["confidence"] >= threshold]
                             for group in predictions]
                if len(confident) == 2 and predictions_disagree(*confident):
                    flags.append("disagreement")

                relative = path.relative_to(root / "images")
                label = (root / "labels" / relative).with_suffix(".txt")
                # Existing labels supply class prevalence, including deliberately empty negatives.
                # Predictions supply estimated prevalence for images awaiting their first labels.
                try:
                    classes = ({class_key(dataset_names, row["class_id"]) for row in parse_labels(read_bytes(label, MAX_BYTES))}
                               if label.is_file() else {candidate["category"] for candidate in confident[0]})
                except (OSError, ValueError):
                    classes = set()
                categories[key] = classes
                counts.update(classes)
            if revision != image_revision(path):
                raise ValueError("A source image changed during analysis. Run the check again.")
            if index == 1 or index % 5 == 0 or index == len(queue):
                emit("progress", current=index, total=len(queue), file=path.name)

        largest = max(counts.values(), default=0)
        rare = {category for category, count in counts.items() if largest >= 5 and count * 5 <= largest}
        for key, classes in categories.items():
            if rare & classes:
                records[key]["signals"].append("rare")
        text = json.dumps(dict(version=1, images=records), ensure_ascii=True, separators=(",", ":"))
        if len(text) > MAX_REPORT:
            raise ValueError("Review priorities exceed the supported report size.")
        atomic_text(reject_reparse(root / FILENAME), text)
    emit("review_priorities_ready", images=len(queue), prioritized=sum(bool(row["signals"]) for row in records.values()))
    return 0
