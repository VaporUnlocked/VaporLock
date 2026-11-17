"""Read-only checks for saved .cloud projects and local YOLO datasets."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import warnings

from cloud_contract import CloudInputError, MAX_IMAGES, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from dataset_paths import reject_reparse


SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
EXAMPLES = 8
MIN_TARGET_PIXELS = 8
NEAR_DISTANCE = 3
MAX_CANDIDATES = 128


@dataclass(frozen=True)
class Sample:
    path: Path
    name: str
    split: str
    labels: tuple
    review: str = "reviewed"
    dimensions: tuple | None = None
    label_path: Path | None = None


def _read_text(path, limit=1_048_576):
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Label or metadata file exceeds its size limit.")
    return payload.decode("utf-8-sig")


def _yolo_labels(path):
    result = []
    for line in _read_text(path).splitlines():
        if not line.strip():
            continue
        category, *coords = line.split()
        class_id = int(category)
        values = list(map(float, coords))
        if not 0 <= class_id < 10_000 or not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
            raise ValueError("Invalid class ID or coordinates.")
        if len(values) == 4:
            _, _, width, height = values
        elif len(values) >= 6 and len(values) % 2 == 0:
            xs, ys = values[::2], values[1::2]
            width, height = max(xs) - min(xs), max(ys) - min(ys)
        else:
            raise ValueError("Expected a box or segmentation polygon.")
        if width <= 0 or height <= 0:
            raise ValueError("Empty target box.")
        result.append((class_id, width, height))
    return tuple(result)


def scan_yolo(selected, model_size, progress):
    root = reject_reparse(selected)
    image_root = reject_reparse(root / "images")
    if not image_root.is_dir():
        raise CloudInputError("Choose a YOLO dataset folder containing images and labels folders.")
    review_file = root / ".vaporlock-review.json"
    try:
        decisions = json.loads(_read_text(reject_reparse(review_file))).get("decisions", {}) if review_file.exists() else {}
        if not isinstance(decisions, dict):
            raise ValueError("Invalid review decisions.")
        class_file = root / "classes.txt"
        names = _read_text(reject_reparse(class_file)).splitlines() if class_file.exists() else []
    except (OSError, ValueError, AttributeError) as error:
        raise CloudInputError("Could not read dataset classes or review decisions.") from error
    samples = []
    excluded = 0
    split_scan = False
    layout = [("unsplit", image_root)] + [(split, image_root / split) for split in SPLITS]
    for split, directory in layout:
        directory = reject_reparse(directory)
        if not directory.is_dir():
            continue
        split_scan |= split in SPLITS
        entries = sorted(path for path in directory.iterdir()
                         if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file())
        stems = Counter(path.stem.casefold() for path in entries)
        for image in entries:
            name = image.relative_to(root).as_posix()
            if decisions.get(name) == "rejected":
                excluded += 1
                continue
            label = root / "labels" / image.with_suffix(".txt").relative_to(image_root)
            state = "ambiguous" if stems[image.stem.casefold()] != 1 else "reviewed"
            samples.append(Sample(image, name, split, (), state, label_path=label))
            if len(samples) > MAX_IMAGES:
                raise CloudInputError(f"Dataset health checks support up to {MAX_IMAGES:,} images at a time.")
    return _scan(samples, names, model_size, progress, split_scan=split_scan, excluded=excluded)


def scan_cloud(selected, model_size, progress):
    from cloud_project import ProjectLease
    with ProjectLease(selected, check_split_readiness=False) as project:
        samples = []
        groups = defaultdict(set)
        for record in project.manifest["images"]:
            name, split = record["file"], record["split"]
            if not record["reviewed"]:
                samples.append(Sample(project.root / name, name, split, (), "unreviewed"))
                continue
            width, height = record["width"], record["height"]
            labels = tuple((a["class_id"], (a["box"][2]-a["box"][0]) / width,
                            (a["box"][3]-a["box"][1]) / height) for a in record["annotations"])
            groups[record["group_id"]].add(split)
            samples.append(Sample(project.root / name, name, split, labels,
                                  dimensions=(width, height)))
        leaking_groups = sum(len(splits) > 1 for splits in groups.values())
        return _scan(samples, project.manifest["classes"], model_size, progress,
                     split_scan=True, leaking_groups=leaking_groups)


def _fingerprint(sample):
    from PIL import Image
    path = reject_reparse(sample.path)
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Missing or oversized image.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            width, height = image.size
            if (width * height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1
                    or (sample.dimensions is not None and image.size != sample.dimensions)):
                raise ValueError("Unsupported image size or frame count.")
            rgb = image.convert("RGB")
            digest = hashlib.sha256(f"{width}x{height}:".encode())
            digest.update(rgb.tobytes())
            small = rgb.resize((9, 8), Image.Resampling.BOX).convert("L").tobytes()
            thumbnail = rgb.resize((8, 8), Image.Resampling.BOX).tobytes()
    difference = 0
    for y in range(8):
        for x in range(8):
            difference = (difference << 1) | (small[y*9+x] > small[y*9+x+1])
    return width, height, digest.digest(), difference, thumbnail


def _scan(samples, names, model_size, progress, *, split_scan, excluded=0, leaking_groups=0):
    from PIL import Image
    counts = Counter()
    classes = defaultdict(Counter)
    findings = defaultdict(list)
    hashes = defaultdict(list)
    buckets = defaultdict(list)
    representatives = set()
    if split_scan:
        all_splits = ("train", "val") + tuple(
            split for split in ("test", "unsplit") if any(s.split == split for s in samples))
    else:
        all_splits = ("unsplit",)
    train_split = "train" if split_scan else "unsplit"
    total = len(samples)

    def flag(issue, detail):
        counts[issue] += 1
        if len(findings[issue]) < EXAMPLES:
            findings[issue].append(detail)

    for index, sample in enumerate(samples, 1):
        if index == 1 or index % 20 == 0:
            progress(index - 1, total)
        display = f"[{sample.split}] {sample.name[:240]}"
        state, labels = sample.review, sample.labels
        if state == "reviewed" and sample.label_path is not None:
            try:
                label_path = reject_reparse(sample.label_path)
                if label_path.is_file():
                    labels = _yolo_labels(label_path)
                else:
                    state = "missing"
            except (OSError, ValueError):
                state = "invalid"
        if state != "reviewed":
            flag(state, display)
        elif not labels:
            counts["negative"] += 1
        if state == "unreviewed":
            continue
        try:
            width, height, digest, signature, thumbnail = _fingerprint(sample)
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            flag("unreadable", display)
            continue
        counts["scanned"] += 1
        counts[sample.split] += 1
        scale = model_size / max(width, height)
        for category, bw, bh in labels:
            classes[category][sample.split] += 1
            short_edge = min(bw * width, bh * height) * scale
            if short_edge < MIN_TARGET_PIXELS:
                flag("tiny", f"{display} · class {category} · {short_edge:.1f} px")
        digests = hashes[digest]
        digests.append((sample.split, display))
        representative = (digest, sample.split)
        if representative in representatives:
            continue
        representatives.add(representative)
        if split_scan and sample.split not in SPLITS:
            continue
        # At most three changed bits guarantees one of four 16-bit bands agrees.
        # Bound unusually large buckets so similar video frames keep the scan responsive.
        keys = [(band, (signature >> (16 * band)) & 0xFFFF) for band in range(4)]
        candidates = {}
        for key in keys:
            for split in (SPLITS if split_scan else ("unsplit",)):
                if split_scan and split == sample.split:
                    continue
                for candidate in buckets.get((*key, split), ()):
                    candidates[candidate[0]] = candidate
        for other_digest, other_hash, other_thumb, other_display in candidates.values():
            if (other_digest != digest and (signature ^ other_hash).bit_count() <= NEAR_DISTANCE
                    and sum(abs(a-b) for a, b in zip(thumbnail, other_thumb)) <= 6 * len(thumbnail)):
                flag("near", f"{other_display} ↔ {display}")
                break
        record = (digest, signature, thumbnail, display)
        for key in keys:
            bucket = buckets[(*key, sample.split)]
            if len(bucket) < MAX_CANDIDATES:
                bucket.append(record)
            else:
                counts["bounded"] += 1

    for group in hashes.values():
        if len(group) < 2:
            continue
        first = group[0][1]
        per_split = dict(group)
        across = len(set(per_split) & set(SPLITS)) > 1
        detail = " ↔ ".join(per_split.values()) if across else f"{first} · {len(group)} copies"
        flag("leakage" if across else "exact", detail)
    progress(total, total)

    lines = ["Dataset health", f"{total:,} images · {counts['scanned']:,} checked for duplicates",
             " · ".join(f"{name}: {counts[name]:,}" for name in all_splits) or "No supported images found.",
             f"{counts['negative']:,} labeled or reviewed negatives · {excluded:,} excluded by review",
             f"Tiny target threshold: shorter box edge below {MIN_TARGET_PIXELS} px at {model_size} × {model_size} input.",
             "", "Findings"]
    if split_scan and not counts["train"]:
        lines.append("No readable training images found.")
    if split_scan and not counts["val"]:
        lines.append("No readable validation images found.")
    if leaking_groups:
        lines.append(f"{leaking_groups:,} reviewed recording groups cross dataset splits.")
    for key, title in (
        ("leakage", "Exact duplicate groups crossing splits"),
        ("near", "Possible near-duplicate frames" + (" crossing splits" if split_scan else "")),
        ("exact", "Other exact duplicate groups"), ("missing", "Images missing label files"),
        ("unreviewed", "Unreviewed project images"),
        ("invalid", "Unreadable or invalid label files"), ("ambiguous", "Images sharing a label filename"),
        ("unreadable", "Unreadable, unsupported, or changed source images"), ("tiny", "Tiny targets"),
    ):
        lines.append(f"{title}: {counts[key]:,}")
        lines.extend("  " + item for item in findings[key])
    if counts["bounded"]:
        lines.append("Some near-duplicate groups exceeded the comparison limit. Exact duplicate checks covered every readable image.")
    if not split_scan:
        lines.append("This dataset is unsplit. Check an exported split dataset to measure leakage.")

    categories = sorted(set(classes) | set(range(len(names))))
    lines.extend(("", "Class balance"))
    if not categories:
        lines.append("No classes with valid labels found.")
    else:
        largest = max((classes[c][train_split] for c in categories), default=0)
        for category in categories[:80]:
            name = names[category][:120] if category < len(names) else f"Class {category}"
            samples_by_split = classes[category]
            tally = " · ".join(f"{split}: {samples_by_split[split]:,}" for split in all_splits)
            flags = []
            if not samples_by_split[train_split]:
                flags.append("no training labels" if split_scan else "no labels")
            elif largest >= 10 and samples_by_split[train_split] * 5 < largest:
                flags.append("underrepresented")
            if counts["val"] and not samples_by_split["val"]:
                flags.append("missing from validation")
            reason = " · " + ", ".join(flags) if flags else ""
            lines.append(f"{name} · {tally}{reason}")
        if len(categories) > 80:
            lines.append(f"{len(categories)-80:,} more classes.")
    return "\n".join(lines)
