"""Deterministic synthetic fixtures for testing software, not player accuracy."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import numpy as np
from PIL import Image, ImageDraw

from .data import Project


def make_demo(path, count=72, size=160, seed=7):
    if not isinstance(count, int) or not 12 <= count <= 10000:
        raise ValueError("Demo count must be 12 to 10000 images.")
    if not isinstance(size, int) or not 64 <= size <= 640:
        raise ValueError("Demo image size must be 64 to 640.")
    project = Project.create(path, "Synthetic software test — not game training")
    rng = np.random.default_rng(seed)
    # A concave silhouette containing a head, torso, two arms and a leg gap.
    # Ground truth follows this known geometry; it is not an artist impression
    # or any kind of pretrained player detector.
    template = np.array([
        [-.10, 0], [.10, 0], [.16, .05], [.15, .17], [.09, .21],
        [.25, .24], [.44, .47], [.45, .57], [.34, .61], [.20, .43],
        [.18, .61], [.28, .94], [.26, 1], [.08, 1], [0, .73],
        [-.08, 1], [-.26, 1], [-.28, .94], [-.18, .61], [-.20, .43],
        [-.34, .61], [-.45, .57], [-.44, .47], [-.25, .24],
        [-.09, .21], [-.15, .17], [-.16, .05]
    ], dtype=np.float32)
    for index in range(count):
        split = "train" if index < int(count * .65) else "val" if index < int(count * .85) else "test"
        background = np.zeros((size, size, 3), dtype=np.uint8)
        base = rng.integers(12, 45, size=(3,))
        noise = rng.normal(0, 2.5, (size, size, 1))
        background[:] = np.clip(base + noise, 0, 255).astype(np.uint8)
        image = Image.fromarray(background)
        draw = ImageDraw.Draw(image)
        # Random low-contrast scene stripes avoid a perfectly blank background.
        for _ in range(3):
            y = int(rng.integers(0, size))
            draw.line([(0, y), (size - 1, y)], fill=tuple(int(x) for x in np.minimum(base + 10, 255)), width=1)
        annotations = []
        negative = index % 7 == 6
        n_instances = 0 if negative else 2 if index % 5 == 4 else 1
        for instance in range(n_instances):
            height = float(rng.uniform(.38, .70) * size)
            width = height * float(rng.uniform(.55, .76))
            if n_instances == 2:
                center_x = float(size * (.27 if instance == 0 else .73) + rng.uniform(-.03, .03) * size)
                width = min(width, size * .42)
            else:
                center_x = float(rng.uniform(.28, .72) * size)
            top_y = float(rng.uniform(.04, max(.05, .94 - height / size)) * size)
            polygon = template * [width, height] + [center_x, top_y]
            polygon[:, 0] = np.clip(polygon[:, 0], 2, size - 2)
            polygon[:, 1] = np.clip(polygon[:, 1], 2, size - 2)
            colour = (int(rng.integers(40, 90)), int(rng.integers(160, 240)), int(rng.integers(160, 240)))
            draw.polygon([tuple(point) for point in polygon], fill=colour)
            left, top = polygon.min(axis=0) - 1
            right, bottom = polygon.max(axis=0) + 1
            annotations.append({"id": uuid4().hex, "class_id": 0,
                                "box": [float(left), float(top), float(right), float(bottom)],
                                "wrapper": polygon.tolist(), "source": "synthetic"})
        record = project.add_pil(image, f"synthetic_{index:04}", split, f"synthetic_{seed}_{index:04}")
        project.set_annotations(record["id"], annotations, reviewed=True)
    project.manifest["synthetic"] = True
    project.manifest["purpose"] = "Procedural software fixture. Its training/validation scores do not measure game-player recognition."
    project.manifest["generator"] = {"seed": seed, "count": count, "size": size}
    project.save()
    return project
