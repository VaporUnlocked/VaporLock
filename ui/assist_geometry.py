"""Bounded, normalized model suggestions. No writes or model execution."""
from copy import deepcopy
import math
from annotation_labels import polygon_valid

MAX_CANDIDATES=128
MAX_VERTICES=4096

def finite(v):return type(v) in (int,float) and math.isfinite(v)

def validate(items):
    if type(items) is not list or len(items)>MAX_CANDIDATES:raise ValueError('Invalid suggestion count.')
    result=[];vertices=0
    for item in items:
        if type(item) is not dict or set(item)!= {'kind','class_id','confidence','box','points'}:raise ValueError('Invalid suggestion.')
        if (type(item['class_id']) is not int or not 0<=item['class_id']<=999999
                or not finite(item['confidence']) or not 0<=item['confidence']<=1):raise ValueError('Invalid model class or confidence.')
        box=item['box'];points=item['points'];kind=item['kind']
        if type(box) is not list or len(box)!=4 or not all(finite(v) and 0<=v<=1 for v in box):raise ValueError('Invalid box.')
        x,y,w,h=box
        if min(w,h)<=0 or x+w>1.000001 or y+h>1.000001:raise ValueError('Invalid box area.')
        if type(points) is not list:raise ValueError('Invalid outline.')
        if kind=='polygon':
            vertices+=len(points)
            if (vertices>MAX_VERTICES or not 3<=len(points)<=256
                    or any(type(p) is not list or len(p)!=2 or not all(finite(v) and 0<=v<=1 for v in p) for p in points)
                    or not polygon_valid(points)
                    or not polygon_valid([(round(x,6),round(y,6)) for x,y in points])):raise ValueError('Invalid outline.')
        elif kind!='box' or points:raise ValueError('Invalid suggestion type.')
        result.append(deepcopy(item))
    return result

def area(item):
    points=item['points']
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(points,points[1:]+points[:1])))/2 if points else item['box'][2]*item['box'][3]

def contains(item,x,y):
    if item['kind']=='box':
        a,b,w,h=item['box'];return a<=x<=a+w and b<=y<=b+h
    inside=False;points=item['points']
    for (ax,ay),(bx,by) in zip(points,points[1:]+points[:1]):
        cross=(x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross)<1e-12 and min(ax,bx)<=x<=max(ax,bx) and min(ay,by)<=y<=max(ay,by):return True
        if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:inside=not inside
    return inside

def hit(items,x,y):
    indexes=[i for i,item in enumerate(items) if contains(item,x,y)]
    return min(indexes,key=lambda i:(area(items[i]),-items[i]['confidence'],i)) if indexes else -1
