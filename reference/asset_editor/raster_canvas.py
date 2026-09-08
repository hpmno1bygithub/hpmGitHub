# -*- coding: utf-8 -*-
"""FIX98 bounded, single-UIImageView fallback for the pixel canvas.

Uses an in-memory PNG only when Metal cannot initialise. No per-pixel UIKit
views, temporary image files, or retained frame history. The authoring grid is
never resampled; magnification uses nearest-neighbour filtering.
"""
import base64
import struct
import zlib
import mainthread
from types import SimpleNamespace
from Foundation import NSData
from UIKit import UIImageView, UIImage
from ios_host.objc_view import WrapperView

MAX_GRID = 128


def _chunk(kind, data):
    return struct.pack('>I',len(data)) + kind + data + struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)


def grid_png(colors, boundaries, grid):
    """A tiny display-only grid. Neither the model nor the saved PNG is altered."""
    grid = int(grid)
    if not 1 <= grid <= MAX_GRID:
        raise ValueError('canvas grid out of range')
    # At overview scales a native pixel per cell is enough. At editing scales
    # use 4 pixels per cell for visible cell separators / selection contours.
    scale = 4 if grid <= 48 else 1
    raw = bytearray()
    values = []
    for c in colors:
        text=str(c).lstrip('#')
        try: values.append(bytes.fromhex((text[:6] if len(text)>=6 else '18212A')+'FF'))
        except ValueError: values.append(bytes.fromhex('18212AFF'))
    values=(values+[bytes.fromhex('18212AFF')]*(grid*grid))[:grid*grid]
    for y in range(grid*scale):
        raw.append(0)
        for x in range(grid*scale):
            index=(y//scale)*grid+(x//scale)
            border=scale>1 and (x%scale==0 or y%scale==0)
            if border:
                raw.extend(bytes.fromhex('F1CD57FF' if index<len(boundaries) and boundaries[index] else '28323CFF'))
            else:raw.extend(values[index])
    side=grid*scale
    return (b'\x89PNG\r\n\x1a\n'+_chunk(b'IHDR',struct.pack('>IIBBBBB',side,side,8,6,0,0,0))+
            _chunk(b'IDAT',zlib.compress(bytes(raw),1))+_chunk(b'IEND',b''))


class _RasterView(WrapperView):
    objc_class=UIImageView
    def configure_view(self, view):
        view.contentMode=0
        view.clipsToBounds=True
        view.userInteractionEnabled=False
        view.layer.magnificationFilter='nearest'
        view.layer.minificationFilter='nearest'


class RasterPixelCanvas:
    def __init__(self):
        self.state=SimpleNamespace(last_error='')
        self.running=True;self.pending=False;self.grid=16
        self.colors=[];self.boundaries=[]
        self.view=_RasterView();self.view.user_interaction_enabled=False
    @property
    def available(self):return self.running
    @property
    def frame(self):return self.view.frame
    @frame.setter
    def frame(self,v):self.view.frame=v
    def set_grid(self,colors,boundaries,grid):
        if not 1<=int(grid)<=MAX_GRID:return False
        self.grid=int(grid);self.colors=list(colors);self.boundaries=list(boundaries)
        return self.request_draw()
    def set_cell(self,x,y,value,boundary=False):
        x=int(x);y=int(y);index=y*self.grid+x
        if not (0<=x<self.grid and 0<=y<self.grid and index<len(self.colors)):return False
        self.colors[index]=value;self.boundaries[index]=bool(boundary)
        return self.request_draw()
    def request_draw(self):
        if not self.running or self.pending:return False
        self.pending=True
        def draw():
            try:
                if not self.running:return
                encoded=base64.b64encode(grid_png(self.colors,self.boundaries,self.grid)).decode('ascii')
                data=NSData.alloc().initWithBase64EncodedString_options_(encoded,0)
                image=UIImage.imageWithData_(data)
                if image is None:raise RuntimeError('UIImage decode failed')
                self.view.objc_view.image=image
            except Exception as exc:
                self.state.last_error=repr(exc)
                print('ASSET EDITOR bitmap fallback warning:',repr(exc))
            finally:self.pending=False
        try:mainthread.run_async(draw);return True
        except Exception:
            self.pending=False;raise
    def close(self):
        self.running=False;self.colors=[];self.boundaries=[]
        try:self.view.objc_view.image=None
        except Exception:pass
