"""Personal scale for owned native Qt styles; independent of palette/theme CSS."""
import re

_FONT_SIZE = re.compile(r'(font-size\s*:\s*)([0-9]+(?:\.[0-9]+)?)(pt|px)(\s*;)')


def scaled_native_stylesheet(source, scale):
    if type(source) is not str or scale not in ('1','1.5','2'):
        raise ValueError('NATIVE_TYPOGRAPHY_REJECTED')
    factor=float(scale)
    return _FONT_SIZE.sub(lambda match: match[1]+format(float(match[2])*factor,'.6g')+match[3]+match[4],source)
