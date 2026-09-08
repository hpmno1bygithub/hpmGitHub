# -*- coding: utf-8 -*-
"""On-demand Metal pixel canvas used by the asset editor.

The logical artwork remains ordinary JSON pixel data.  Metal only replaces the
old pool of up to 1024 retained UIKit cell views with one MTKView and one
instanced draw call.  The view is paused and draws solely after an edit, frame
change, selection change, or layout update.
"""

import ctypes
import threading
import time

import mainthread

try:
    import numpy as _np
except Exception:
    _np = None

try:
    import MetalKit  # noqa: F401
except Exception as exc:
    MetalKit = None
    _METALKIT_ERROR = repr(exc)
else:
    _METALKIT_ERROR = ""

from rubicon.objc import ObjCClass, ObjCInstance, NSObject, objc_method
from rubicon.objc.types import CGSize

from ios_host.objc_view import WrapperView


MTL_PRIMITIVE_TYPE_TRIANGLE = 3
MAX_GRID = 128
MAX_QUADS = MAX_GRID * MAX_GRID * 2
GRID_RGBA = (0.157, 0.196, 0.235, 1.0)
GOLD_RGBA = (0.945, 0.804, 0.341, 1.0)
EMPTY_RGBA = (0.094, 0.129, 0.165, 1.0)
_LOADED_LIBRARIES = []


class Float2(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


class Float4(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_float), ("y", ctypes.c_float),
        ("z", ctypes.c_float), ("w", ctypes.c_float),
    ]


class InstanceData(ctypes.Structure):
    _fields_ = [("center", Float2), ("half_size", Float2), ("color", Float4)]


class UniformData(ctypes.Structure):
    _fields_ = [("viewport", Float2)]


SHADER_SOURCE = r'''
#include <metal_stdlib>
using namespace metal;

struct InstanceData {
    float2 center;
    float2 halfSize;
    float4 color;
};

struct UniformData { float2 viewport; };

struct VertexOut {
    float4 position [[position]];
    float4 color;
};

vertex VertexOut editor_vertex(
    uint vid [[vertex_id]],
    uint iid [[instance_id]],
    const device InstanceData* instances [[buffer(0)]],
    constant UniformData& uniforms [[buffer(1)]])
{
    const float2 corner[6] = {
        float2(-1.0, -1.0), float2(1.0, -1.0), float2(-1.0, 1.0),
        float2(-1.0, 1.0), float2(1.0, -1.0), float2(1.0, 1.0)
    };
    InstanceData inst = instances[iid];
    float2 pixel = inst.center + corner[vid] * inst.halfSize;
    float2 ndc;
    ndc.x = (pixel.x / max(1.0, uniforms.viewport.x)) * 2.0 - 1.0;
    ndc.y = 1.0 - (pixel.y / max(1.0, uniforms.viewport.y)) * 2.0;
    VertexOut out;
    out.position = float4(ndc, 0.0, 1.0);
    out.color = inst.color;
    return out;
}

fragment float4 editor_fragment(VertexOut in [[stage_in]]) { return in.color; }
'''


def _call0(obj, name):
    value = getattr(obj, name)
    return value() if callable(value) else value


def _ptr_value(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value.value)
    except Exception:
        try:return int(value)
        except Exception:return 0


def _device():
    errors=[]
    try:
        import Metal
        fn=getattr(Metal,"MTLCreateSystemDefaultDevice",None)
        if fn is not None:
            device=fn()
            if device is not None:return device
    except Exception as exc:errors.append(repr(exc))
    for path in ("/System/Library/Frameworks/Metal.framework/Metal",None):
        try:
            lib=ctypes.CDLL(path) if path else ctypes.CDLL(None)
            _LOADED_LIBRARIES.append(lib)
            fn=lib.MTLCreateSystemDefaultDevice; fn.argtypes=[]; fn.restype=ctypes.c_void_p
            ptr=fn()
            if ptr:return ObjCInstance(ptr)
        except Exception as exc:errors.append(repr(exc))
    raise RuntimeError("Metal device unavailable: "+" | ".join(errors))


def _rgba(value):
    text=str(value or "").strip().lstrip("#")
    if len(text)==6:text+="FF"
    if len(text)!=8:return EMPTY_RGBA
    try:return tuple(int(text[i:i+2],16)/255.0 for i in (0,2,4,6))
    except Exception:return EMPTY_RGBA


def _write_instances(buffer_obj, quads):
    count=min(len(quads),MAX_QUADS)
    if count<=0:return 0
    ptr=_ptr_value(_call0(buffer_obj,"contents"))
    if not ptr:raise RuntimeError("MTLBuffer.contents is null")
    if _np is not None and ctypes.sizeof(InstanceData)==32:
        packed=_np.asarray(quads[:count],dtype=_np.float32,order="C")
        if packed.shape==(count,8):
            if not packed.flags.c_contiguous:packed=_np.ascontiguousarray(packed)
            ctypes.memmove(ptr,int(packed.ctypes.data),count*32)
            return count
    array=(InstanceData*count)()
    for i,(cx,cy,hx,hy,r,g,b,a) in enumerate(quads[:count]):
        array[i].center=Float2(float(cx),float(cy))
        array[i].half_size=Float2(float(hx),float(hy))
        array[i].color=Float4(float(r),float(g),float(b),float(a))
    ctypes.memmove(ptr,ctypes.addressof(array),ctypes.sizeof(InstanceData)*count)
    return count


def _write_uniform(buffer_obj, grid):
    data=UniformData(Float2(float(grid),float(grid)))
    ptr=_ptr_value(_call0(buffer_obj,"contents"))
    if not ptr:raise RuntimeError("uniform buffer is null")
    ctypes.memmove(ptr,ctypes.addressof(data),ctypes.sizeof(UniformData))


class MetalCanvasState:
    def __init__(self):
        self.lock=threading.RLock()
        self.running=True; self.ready=False; self.draw_pending=False
        self.grid=16; self.quads=[]; self.generation=0
        self.device=None; self.queue=None; self.pipeline=None; self.delegate=None
        self.instance_buffers=[]; self.uniform_buffers=[]; self.buffer_index=0
        self.last_error=""; self.draw_count=0

    def publish_grid(self, colors, boundaries, grid):
        grid=max(1,min(MAX_GRID,int(grid)))
        count=grid*grid
        colors=list(colors or ())[:count]
        boundaries=list(boundaries or ())[:count]
        if len(colors)<count:colors.extend(["#18212A"]*(count-len(colors)))
        if len(boundaries)<count:boundaries.extend([False]*(count-len(boundaries)))
        quads=[]
        inset=0.055 if grid<=48 else 0.0
        for index in range(count):
            x=index%grid; y=index//grid; cx=x+0.5; cy=y+0.5
            outer=GOLD_RGBA if bool(boundaries[index]) else GRID_RGBA
            inner=_rgba(colors[index])
            quads.append((cx,cy,0.5,0.5,*outer))
            quads.append((cx,cy,0.5-inset,0.5-inset,*inner))
        with self.lock:
            self.grid=grid; self.quads=quads; self.generation+=1

    def publish_cell(self, x, y, value, boundary=False):
        with self.lock:
            x=int(x); y=int(y)
            if not (0<=x<self.grid and 0<=y<self.grid):return False
            index=(y*self.grid+x)*2; cx=x+0.5; cy=y+0.5
            outer=GOLD_RGBA if bool(boundary) else GRID_RGBA
            inner=_rgba(value)
            if index+1>=len(self.quads):return False
            self.quads[index]=(cx,cy,0.5,0.5,*outer)
            half=0.445 if self.grid<=48 else 0.5
            self.quads[index+1]=(cx,cy,half,half,*inner)
            self.generation+=1
            return True

    def snapshot(self):
        with self.lock:return self.grid,tuple(self.quads),self.generation

    def schedule_draw(self):
        with self.lock:
            if not self.running or not self.ready or self.draw_pending:return False
            self.draw_pending=True; return True

    def begin_draw(self):
        with self.lock:self.draw_pending=False


def _draw(self, view) -> None:
    state=getattr(self,"canvas_state",None)
    if state is None or not state.running or not state.ready:return
    state.begin_draw()
    try:
        descriptor=view.currentRenderPassDescriptor; drawable=view.currentDrawable
        if descriptor is None or drawable is None:return
        grid,quads,_generation=state.snapshot()
        index=state.buffer_index%len(state.instance_buffers); state.buffer_index+=1
        instance_buffer=state.instance_buffers[index]; uniform_buffer=state.uniform_buffers[index]
        count=_write_instances(instance_buffer,quads); _write_uniform(uniform_buffer,grid)
        command=_call0(state.queue,"commandBuffer")
        if command is None:return
        encoder=command.renderCommandEncoderWithDescriptor_(descriptor)
        if encoder is None:return
        encoder.setRenderPipelineState_(state.pipeline)
        encoder.setVertexBuffer_offset_atIndex_(instance_buffer,0,0)
        encoder.setVertexBuffer_offset_atIndex_(uniform_buffer,0,1)
        if count:
            encoder.drawPrimitives_vertexStart_vertexCount_instanceCount_(
                MTL_PRIMITIVE_TYPE_TRIANGLE,0,6,int(count)
            )
        _call0(encoder,"endEncoding"); command.presentDrawable_(drawable); _call0(command,"commit")
        state.draw_count+=1
    except Exception as exc:
        state.last_error="draw: "+repr(exc)
        print("ASSET EDITOR Metal draw warning:",repr(exc))


def _resize(self, view, size: CGSize) -> None:
    return


_draw=objc_method(_draw)
_resize=objc_method(_resize)
_DELEGATE_NAME="PytoRPGAssetMetalDelegate_%d"%int(time.time_ns()%1000000000000)
MetalCanvasDelegate=ObjCClass(
    _DELEGATE_NAME,(NSObject,),
    {"drawInMTKView_":_draw,"mtkView_drawableSizeWillChange_":_resize},
)


class _MetalCanvasView(WrapperView):
    objc_class=ObjCClass("MTKView")

    def __init__(self,state):
        self.state=state
        super().__init__()

    def configure_view(self,view):
        state=self.state
        try:
            if MetalKit is None:raise RuntimeError("MetalKit import failed: "+_METALKIT_ERROR)
            device=_device(); state.device=device; view.device=device
            view.paused=True; view.enableSetNeedsDisplay=True
            queue=_call0(device,"newCommandQueue")
            if queue is None:raise RuntimeError("newCommandQueue returned nil")
            state.queue=queue
            library=device.newLibraryWithSource_options_error_(SHADER_SOURCE,None,None)
            if library is None:raise RuntimeError("Metal shader compile failed")
            vertex=library.newFunctionWithName_("editor_vertex")
            fragment=library.newFunctionWithName_("editor_fragment")
            desc=ObjCClass("MTLRenderPipelineDescriptor").new()
            desc.vertexFunction=vertex; desc.fragmentFunction=fragment
            desc.colorAttachments.objectAtIndexedSubscript_(0).pixelFormat=view.colorPixelFormat
            pipeline=device.newRenderPipelineStateWithDescriptor_error_(desc,None)
            if pipeline is None:raise RuntimeError("Metal pipeline creation failed")
            state.pipeline=pipeline
            for _i in range(3):
                ib=device.newBufferWithLength_options_(MAX_QUADS*ctypes.sizeof(InstanceData),0)
                ub=device.newBufferWithLength_options_(ctypes.sizeof(UniformData),0)
                if ib is None or ub is None:raise RuntimeError("Metal buffer allocation failed")
                state.instance_buffers.append(ib); state.uniform_buffers.append(ub)
            delegate=MetalCanvasDelegate.new(); delegate.canvas_state=state
            state.delegate=delegate; view.delegate=delegate; state.ready=True
            print("ASSET EDITOR Metal canvas ready: one MTKView / on-demand / max",MAX_QUADS,"quads")
        except Exception as exc:
            state.last_error=repr(exc); state.ready=False
            print("ASSET EDITOR Metal canvas unavailable; UIKit fallback:",repr(exc))


class MetalPixelCanvas:
    """Small facade consumed by ``AssetEditorUI``."""

    def __init__(self):
        self.state=MetalCanvasState()
        self.view=_MetalCanvasView(self.state)
        try:self.view.user_interaction_enabled=False
        except Exception:pass

    @property
    def available(self):
        return bool(self.state.ready and self.state.running)

    @property
    def frame(self):
        return self.view.frame

    @frame.setter
    def frame(self,value):
        self.view.frame=value
        self.request_draw()

    def set_grid(self,colors,boundaries,grid):
        if not self.available:return False
        self.state.publish_grid(colors,boundaries,grid); self.request_draw(); return True

    def set_cell(self,x,y,value,boundary=False):
        if not self.available:return False
        changed=self.state.publish_cell(x,y,value,boundary)
        if changed:self.request_draw()
        return changed

    def request_draw(self):
        if not self.state.schedule_draw():return False

        def draw():
            if not self.available:
                self.state.begin_draw(); return
            try:
                self.view.objc_view.draw()
                self.state.begin_draw()
                return
            except Exception as exc:
                self.state.last_error="request_draw: "+repr(exc)
            try:self.view.objc_view.setNeedsDisplay()
            except Exception:pass
            self.state.begin_draw()

        try:mainthread.run_async(draw); return True
        except Exception:
            self.state.begin_draw(); return False

    def close(self):
        self.state.running=False
        try:self.view.objc_view.paused=True
        except Exception:pass
        try:self.view.objc_view.delegate=None
        except Exception:pass
        self.state.delegate=None; self.state.instance_buffers=[]; self.state.uniform_buffers=[]
