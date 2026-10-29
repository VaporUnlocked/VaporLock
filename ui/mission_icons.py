"""Theme-aware semantic icons shared with the administrator's native assets."""
from functools import lru_cache
from pathlib import Path
import sys
from PySide6.QtCore import QByteArray, Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

NAVIGATION = {'games':'games','profiles':'profiles','model':'model','overview':'overview',
    'detection':'detection','selection':'target','output':'movement','dataset':'database',
    'shortcuts':'keyboard','automation':'code','diagnostics':'pulse','misc':'wrench','capture':'display'}

@lru_cache(maxsize=256)
def icon(name, foreground='#F2F8F6', active='#B5EF82', disabled='#8EA5A2'):
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'assets/icons'
    if (Path(__file__).resolve().parents[2]/'information/alpha-manifest.json').is_file():
        root=Path(__file__).resolve().parents[2]/'assets/icons'
    # Callers use code-owned semantic names, never catalog/profile paths.
    if name not in set(NAVIGATION.values()) | {'book','display','globe','key','issue','package','gear','search','refresh','save','undo','lock','unlock','start','stop','folder','download','copy','restore','trash','upload','filter','chart','image','tag','link','shield','clock','calendar','chip','edit','accept','exclude','expand','palette','power','connection'}:
        raise ValueError('Unknown native icon')
    source=(root/(name+'.svg')).read_text(encoding='utf8')
    result=QIcon()
    for mode,state,color in ((QIcon.Mode.Normal,QIcon.State.Off,foreground),
                            (QIcon.Mode.Normal,QIcon.State.On,active),
                            (QIcon.Mode.Active,QIcon.State.Off,active),
                            (QIcon.Mode.Selected,QIcon.State.Off,active),
                            (QIcon.Mode.Disabled,QIcon.State.Off,disabled),
                            (QIcon.Mode.Disabled,QIcon.State.On,disabled)):
        renderer=QSvgRenderer(QByteArray(source.replace('currentColor',color).encode()))
        for size in (16,20,24,32,40,48,64,96,128):
            pixmap=QPixmap(size,size);pixmap.fill(Qt.GlobalColor.transparent)
            painter=QPainter(pixmap);renderer.render(painter);painter.end()
            result.addPixmap(pixmap,mode,state)
    return result

def apply(widget, name, palette, scale=1.0, primary=False, danger=False):
    role='button_text' if primary else 'pink' if danger else 'ice'
    widget.setIcon(icon(name,palette[role],palette[role] if primary or danger else palette['baby'],palette['steel']))
    widget.setIconSize(QSize(round(20*float(scale)),round(20*float(scale))))
    widget.setProperty('missionIcon',name)
