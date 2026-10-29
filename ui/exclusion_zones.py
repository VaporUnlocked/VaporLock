"""Typed, bounded capture-pixel exclusion rectangles shared by both editions."""
MAX_ZONES = 16
MAX_PIXEL = 1000000


def validate_zones(value):
    if type(value) is not list or len(value) > MAX_ZONES: raise ValueError('Invalid exclusion zones')
    result = []
    for zone in value:
        if type(zone) is not dict or set(zone) != {'x', 'y', 'width', 'height'}:
            raise ValueError('Invalid exclusion rectangle')
        if any(type(v) is not int for v in zone.values()): raise ValueError('Invalid exclusion coordinate')
        x, y, w, h = (zone[k] for k in ('x', 'y', 'width', 'height'))
        if not (0 <= x < MAX_PIXEL and 0 <= y < MAX_PIXEL and 1 <= w <= MAX_PIXEL-x and 1 <= h <= MAX_PIXEL-y):
            raise ValueError('Exclusion rectangle out of bounds')
        result.append(dict(x=x, y=y, width=w, height=h))
    return result


def zones_wire(value):
    zones = validate_zones(value)
    return ' '.join([str(len(zones)), *(str(z[k]) for z in zones for k in ('x', 'y', 'width', 'height'))])
