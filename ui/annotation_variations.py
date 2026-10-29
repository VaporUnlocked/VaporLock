"""Post-annotation pixel effects; no geometry, model, file or network effects."""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import random

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

EFFECTS = ('blur', 'colour', 'exposure', 'noise', 'compression')
STRENGTHS = ('mild', 'medium', 'strong')
MAX_PIXELS = 16_000_000


@dataclass(frozen=True)
class VariationOptions:
    enabled: bool = False
    copies: int = 1
    strength: str = 'medium'
    effects: tuple[str, ...] = EFFECTS
    extra_copies: bool = False

    def validate(self):
        if (type(self.enabled) is not bool or type(self.extra_copies) is not bool or type(self.copies) is not int
                or not 1 <= self.copies <= 10 or self.strength not in STRENGTHS
                or type(self.effects) is not tuple or (self.enabled and not self.effects)
                or len(set(self.effects)) != len(self.effects)
                or any(type(effect) is not str or effect not in EFFECTS for effect in self.effects)):
            raise ValueError('Invalid random variation options.')
        return self


def vary_image(original: Image.Image, options: VariationOptions, seed: int):
    """Return a new RGB image and reproducible effect description.

    Labels are deliberately not an input: no effect can translate, resize,
    crop, rotate or flip their coordinate system. Caller preserves label bytes.
    """
    options.validate()
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('Invalid variation seed.')
    width, height = original.size
    if min(width, height) < 1 or width * height > MAX_PIXELS:
        raise ValueError('Image exceeds the variation pixel limit.')
    result = original.convert('RGB').copy()
    if not options.enabled:
        return result, {}
    rng = random.Random(seed)
    amount = {'mild': .25, 'medium': .55, 'strong': .9}[options.strength]
    selected = [effect for effect in options.effects if rng.random() < .65]
    if not selected:
        selected = [rng.choice(options.effects)]
    applied = {}
    for effect in selected:
        if effect == 'blur':
            radius = rng.uniform(.2, .4 + 3 * amount)
            result = result.filter(ImageFilter.GaussianBlur(radius))
            applied[effect] = {'radius': round(radius, 5)}
        elif effect == 'colour':
            hue = rng.randint(-max(1, round(35 * amount)), max(1, round(35 * amount)))
            saturation = rng.uniform(1 - .8 * amount, 1 + .8 * amount)
            pixels = np.asarray(result.convert('HSV')).copy()
            pixels[:, :, 0] = ((pixels[:, :, 0].astype(np.int16) + hue) % 256).astype(np.uint8)
            pixels[:, :, 1] = np.clip(pixels[:, :, 1].astype(np.float32) * saturation, 0, 255).astype(np.uint8)
            result = Image.fromarray(pixels, 'HSV').convert('RGB')
            applied[effect] = {'hue': hue, 'saturation': round(saturation, 5)}
        elif effect == 'exposure':
            brightness = rng.uniform(1 - .7 * amount, 1 + .7 * amount)
            contrast = rng.uniform(1 - .7 * amount, 1 + .7 * amount)
            result = ImageEnhance.Brightness(result).enhance(brightness)
            result = ImageEnhance.Contrast(result).enhance(contrast)
            applied[effect] = {'brightness': round(brightness, 5), 'contrast': round(contrast, 5)}
        elif effect == 'noise':
            sigma = rng.uniform(1, 2 + 22 * amount)
            noise_rng = np.random.default_rng(rng.getrandbits(64))
            pixels = np.asarray(result).astype(np.float32)
            pixels += noise_rng.normal(0, sigma, pixels.shape).astype(np.float32)
            result = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
            applied[effect] = {'sigma': round(sigma, 5)}
        else:
            quality = rng.randint(max(35, round(95 - 65 * amount)), 94)
            with BytesIO() as stream:
                result.save(stream, format='JPEG', quality=quality)
                stream.seek(0)
                with Image.open(stream) as compressed:
                    result = compressed.convert('RGB').copy()
            applied[effect] = {'quality': quality}
    if result.size != original.size:
        raise ValueError('Variation changed image dimensions.')
    return result, applied


def effect_seed(seed, source_name, copy=0, *, overwrite=False):
    """Stable per-source choice, unaffected by skipped neighbouring images."""
    if type(seed) is not int or not 0 <= seed < 2**64 or type(copy) is not int or not 0 <= copy < 10:
        raise ValueError('Invalid effect preview seed.')
    mode = 'overwrite' if overwrite else 'copy'
    return int.from_bytes(hashlib.sha256(f'{seed}:{source_name}:{mode}:{copy}'.encode('utf-8')).digest()[:8], 'big')
