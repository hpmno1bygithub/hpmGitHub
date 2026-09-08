# -*- coding: utf-8 -*-
"""FIX91 native touch overlay for the pixel editor.

A single transparent UIControl owns all canvas gestures.  No per-pixel UIKit
views are required when Metal is active.  One-finger drag navigates the canvas
by default, two-finger pinch zooms around the gesture centre, and a tap still
edits the addressed pixel.  The host may switch one-finger drag to continuous
paint without rebuilding the view.
"""
import pyto_ui as ui
from Foundation import NSObject
from UIKit import UIControl, UIPanGestureRecognizer, UITapGestureRecognizer, UIPinchGestureRecognizer
from rubicon.objc import objc_method, SEL


class _PixelTarget(NSObject):
    owner = None

    @objc_method
    def panChanged(self):
        if self.owner is not None:
            self.owner._emit_pan()

    @objc_method
    def tapChanged(self):
        if self.owner is not None:
            self.owner._emit(self.owner._tap, "tap")

    @objc_method
    def pinchChanged(self):
        if self.owner is not None:
            self.owner._emit_pinch()


class PixelPaintOverlay(ui.UIKitView):
    def __init__(self, callback, navigation_callback=None):
        self.callback=callback
        self.navigation_callback=navigation_callback
        self.drag_mode="navigate" if navigation_callback is not None else "paint"
        self._target=None; self._native_control=None; self._pan=None; self._tap=None; self._pinch=None
        self._last_pan=(0.0,0.0); self._last_pinch_scale=1.0
        super().__init__()
        try:self.background_color=ui.Color.rgb(0,0,0,0)
        except Exception:pass

    def make_view(self):
        control=UIControl.alloc().init()
        target=_PixelTarget.alloc().init(); target.owner=self
        self._target=target; self._native_control=control
        pan=UIPanGestureRecognizer.alloc().initWithTarget_action_(target,SEL("panChanged"))
        tap=UITapGestureRecognizer.alloc().initWithTarget_action_(target,SEL("tapChanged"))
        pinch=UIPinchGestureRecognizer.alloc().initWithTarget_action_(target,SEL("pinchChanged"))
        try:
            pan.minimumNumberOfTouches=1; pan.maximumNumberOfTouches=1
            tap.numberOfTouchesRequired=1
        except Exception: pass
        control.addGestureRecognizer_(pan); control.addGestureRecognizer_(tap); control.addGestureRecognizer_(pinch)
        self._pan=pan; self._tap=tap; self._pinch=pinch
        return control

    def set_drag_mode(self, mode):
        self.drag_mode="paint" if str(mode)=="paint" else "navigate"

    def _emit(self, recognizer, kind):
        if recognizer is None or self._native_control is None or self.callback is None:return
        try:
            pt=recognizer.locationInView_(self._native_control); state=int(recognizer.state)
            self.callback(float(pt.x),float(pt.y),str(kind),state)
        except Exception:pass

    def _emit_pan(self):
        if self._pan is None or self._native_control is None:return
        try:
            pt=self._pan.locationInView_(self._native_control); state=int(self._pan.state)
            if self.drag_mode=="paint" or self.navigation_callback is None:
                if self.callback is not None:
                    if state==1:
                        # UIPan begins only after a movement threshold. Use the
                        # initial contact, not that already-moved point, as the
                        # rectangle / brush-selection anchor.
                        tr=self._pan.translationInView_(self._native_control)
                        sx=float(pt.x)-float(tr.x);sy=float(pt.y)-float(tr.y)
                        self.callback(sx,sy,"pan",1)
                        if abs(float(tr.x))+abs(float(tr.y))>0.0:
                            self.callback(float(pt.x),float(pt.y),"pan",2)
                    else:self.callback(float(pt.x),float(pt.y),"pan",state)
                return
            tr=self._pan.translationInView_(self._native_control)
            dx=float(tr.x)-self._last_pan[0]; dy=float(tr.y)-self._last_pan[1]
            if state==1:self._last_pan=(float(tr.x),float(tr.y)); dx=dy=0.0
            else:self._last_pan=(float(tr.x),float(tr.y))
            self.navigation_callback(float(pt.x),float(pt.y),"navigate",state,1.0,dx,dy)
            if state in (3,4,5):self._last_pan=(0.0,0.0)
        except Exception:pass

    def _emit_pinch(self):
        if self._pinch is None or self._native_control is None or self.navigation_callback is None:return
        try:
            pt=self._pinch.locationInView_(self._native_control); state=int(self._pinch.state)
            scale=float(self._pinch.scale)
            if state==1:self._last_pinch_scale=scale; delta=1.0
            else:
                old=max(1e-6,float(self._last_pinch_scale)); delta=scale/old; self._last_pinch_scale=scale
            self.navigation_callback(float(pt.x),float(pt.y),"pinch",state,delta,0.0,0.0)
            if state in (3,4,5):self._last_pinch_scale=1.0
        except Exception:pass
