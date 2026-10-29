"""Bounded YOLO label parsing and conservative additive annotation.

Existing rows remain byte-for-byte authoritative; predictions never relabel them.
This module has no filesystem, licensing or UI authority.
"""
import math
import re

MAX_BYTES = 1024 * 1024
MAX_ROWS = 10000
MAX_VERTICES = 1024


def polygon_valid(points, *, simple=True):
    if not 3 <= len(points) <= MAX_VERTICES:
        return False
    if any(len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v)
            or not 0 <= v <= 1 for v in p) for p in points):
        return False
    if len(set(tuple(p) for p in points)) != len(points):
        return False
    area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))
    if abs(area) < 1e-10:
        return False
    if not simple:
        return True
    def orient(a,b,c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on(a,b,c):
        return min(a[0],b[0])-1e-12 <= c[0] <= max(a[0],b[0])+1e-12 and min(a[1],b[1])-1e-12 <= c[1] <= max(a[1],b[1])+1e-12
    edges=list(zip(points,points[1:]+points[:1]))
    for i,(a,b) in enumerate(edges):
        for j in range(i+1,len(edges)):
            if j == i+1 or (i==0 and j==len(edges)-1):continue
            c,d=edges[j]; o1,o2,o3,o4=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
            if (o1*o2<0 and o3*o4<0) or any(abs(o)<1e-12 and hit for o,hit in
                    ((o1,on(a,b,c)),(o2,on(a,b,d)),(o3,on(c,d,a)),(o4,on(c,d,b)))):
                return False
    return True


def parse_labels(data):
    if type(data) is not bytes or len(data)>MAX_BYTES:
        raise ValueError('Label file exceeds the supported limit.')
    text=data.decode('utf-8-sig'); rows=[]
    for line in text.splitlines():
        fields=line.split()
        if not fields:continue
        if len(rows)>=MAX_ROWS or not re.fullmatch(r'[0-9]{1,6}',fields[0]):
            raise ValueError('Invalid label class or row count.')
        if int(fields[0])>=10000:raise ValueError('Label class exceeds the supported limit.')
        try: values=[float(value) for value in fields[1:]]
        except ValueError: raise ValueError('Invalid label coordinates.') from None
        if any(not math.isfinite(v) or not 0<=v<=1 for v in values):
            raise ValueError('Label coordinates must be finite and normalized.')
        if len(values)==4:
            x,y,w,h=values; box=(x-w/2,y-h/2,x+w/2,y+h/2)
            if w<=0 or h<=0 or min(box)<-1e-6 or max(box)>1+1e-6:
                raise ValueError('Invalid label box.')
            points=None
        elif len(values)>=6 and len(values)%2==0:
            points=list(zip(values[::2],values[1::2]))
            if not polygon_valid(points,simple=False):raise ValueError('Invalid label polygon.')
            box=(min(values[::2]),min(values[1::2]),max(values[::2]),max(values[1::2]))
        else:raise ValueError('Invalid label coordinate count.')
        rows.append(dict(class_id=int(fields[0]),box=box,points=points,row=line))
    return rows


def overlap(a,b):
    area=lambda r:max(0.,r[2]-r[0])*max(0.,r[3]-r[1])
    intersection=max(0.,min(a[2],b[2])-max(a[0],b[0]))*max(0.,min(a[3],b[3])-max(a[1],b[1]))
    union=area(a)+area(b)-intersection
    return intersection/union if union>0 else 0.


def validate_manual_rows(rows):
    polygons=[r['points'] for r in rows if r['points']]
    if sum(len(p)**2 for p in polygons)>1048576:
        raise ValueError('Polygon validation exceeds the supported editing limit.')
    if any(not polygon_valid(p) for p in polygons):raise ValueError('Invalid polygon geometry.')


def append_predictions(original, predictions, threshold):
    if type(threshold) not in (int,float) or not math.isfinite(threshold) or not .01<=threshold<=.99:
        raise ValueError('Invalid additive overlap threshold.')
    existing=parse_labels(original)
    incoming=parse_labels(''.join(predictions).encode('utf-8'))
    boxes=[row['box'] for row in existing]; added=[]
    for row in incoming:
        if any(overlap(row['box'],box)>=threshold for box in boxes):continue
        added.append(row['row']+'\n');boxes.append(row['box'])
    text=original.decode('utf-8')
    if added and text and not text.endswith(('\n','\r')):text+='\n'
    result=text+''.join(added)
    if len(result.encode('utf-8'))>MAX_BYTES or len(existing)+len(added)>MAX_ROWS:
        raise ValueError('Merged labels exceed the supported limit.')
    return result,len(added)
