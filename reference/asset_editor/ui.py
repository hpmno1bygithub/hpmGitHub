# -*- coding: utf-8 -*-
"""Built-in pixel asset editor for Pyto RPG FIX118.

The editor opens the project's built-in tile/creature/plant silhouettes as an
editable pixel canvas.  Paint by tapping or dragging, then Save writes both a
real PNG and a lossless JSON pixel source into assets/ and updates registry.json.
"""
import copy
import colorsys
import json
import math
import threading
import time
import zlib
import pyto_ui as ui
import mainthread

from asset_editor.model import (
    AssetEditorModel, CATEGORIES, PIXEL_EDIT_CATEGORIES, PIXEL_SIZES,
    VFX_PIXEL_SIZES, DEFAULT_VFX_CANVAS_SIZE, EFFECT_STATES, TRANSPARENT, CanvasImportError,
)
from asset_editor.pixel_canvas import PixelPaintOverlay
from asset_editor.creature_auto_animation import generate_creature_animations
from config import PIXEL_WORLD_SCALE
from systems.weapon_catalog import WEAPON_DEFS
from systems.custom_tile_rules import TILE_MAP_ROLES, role_label, role_description
from ios_host.colors import DARK, DARK2, WHITE, BLUE, GREEN, GOLD, RED, color
from ios_host.haptics import selection as haptic_selection, light as haptic_light
from ios_host.orientation import lock_landscape, reassert_landscape, is_ipad

try:
    PRESENT_FULLSCREEN = ui.PresentationMode.FULLSCREEN
except Exception:
    PRESENT_FULLSCREEN = ui.PRESENTATION_MODE_FULLSCREEN


def _center():
    try:return ui.TextAlignment.CENTER
    except Exception:return ui.TEXT_ALIGNMENT_CENTER


def _normalize_hex(text):
    s=str(text or "").strip().upper()
    if not s.startswith("#"):s="#"+s
    body=s[1:]
    if len(body)==6:
        return s+"FF"
    if len(body)==8:
        return s
    raise ValueError("色碼需為 RRGGBB 或 RRGGBBAA")


def _blend_hex(fg,bg="#18212A",alpha=0.35):
    def rgb(v):
        q=str(v or "#000000").strip().lstrip("#")
        if len(q)<6:q="000000"
        try:return tuple(int(q[i:i+2],16) for i in (0,2,4))
        except Exception:return (0,0,0)
    a=max(0.0,min(1.0,float(alpha))); f=rgb(fg); b=rgb(bg)
    c=tuple(int(round(b[i]*(1.0-a)+f[i]*a)) for i in range(3))
    return "#%02X%02X%02X"%c


PALETTE=("#000000FF","#FFFFFFFF","#6C5238FF","#5C8D45FF","#2E94F0FF","#7EC8EDFF","#F1CD57FF","#D9534FFF")

# FIX116 editor performance policy. Authored canvases stay full-resolution up to 128 px.
# Only the small right-side game-scale preview is capped to 16x16 passive cells.
# This removes up to 768 UIKit views at 32x32 without changing saved artwork.
PREVIEW_MAX_GRID=16
# Large VFX sources are edited through a bounded viewport.  Pyto remains at a
# maximum of 1024 retained pixel views even when the logical effect canvas is
# 64px, 96px or 128px, matching the editor/viewport split used by mature sprite tools.
EDITOR_VIEW_GRID_MAX=32

# FIX117: view zoom is allowed below 1.0x.  Above 1.0x the editor keeps the
# existing crop-and-pan behavior; below 1.0x the complete canvas is physically
# reduced and centered inside the drawing workspace.  This is view-only and
# never resamples authored pixels.
CANVAS_ZOOM_MIN=0.25
CANVAS_ZOOM_MAX=8.0

# FIX17 generic animation keyframe markers.  They are stored in the existing
# ``events`` dictionary so old jump.takeoff assets remain fully compatible.
KEYFRAME_MARKERS=(
    ("link","銜接"),
    ("loop_start","循環起"),
    ("loop_end","循環終"),
    ("action","動作"),
)


class AssetEditorUI:
    def __init__(self, project_root):
        self.model=AssetEditorModel(project_root)
        self.root=ui.View(); self.root.title="Pyto RPG 素材工作室 FIX127"; self.root.background_color=DARK
        self.root.layout=self._layout
        self.categories=[row for row in CATEGORIES if row[0] in PIXEL_EDIT_CATEGORIES]
        self.category_index=0; self.asset_index=0; self.size_index=0
        self.canvas_size_direction=1
        self._native_export_override=None
        self.canvas_size=PIXEL_SIZES[self.size_index]
        self.asset_canvas_size=self.canvas_size
        self.export_canvas_size=self.canvas_size; self.export_scale=1.0; self.pixel_mapping=""; self.world_fit=""
        self.canvas_view_x=0; self.canvas_view_y=0
        self.canvas_zoom=1.0; self.canvas_drag_mode="navigate"; self.selection_nav_mode=False; self._nav_pan_accum=[0.0,0.0]
        self._canvas_workspace_frame=(0.0,0.0,1.0)
        self.pixels=[]; self.base_pixels=[]; self.animations={}; self.combat_bindings={}
        # FIX55 combined Attack/VFX authoring preview. The authoritative links
        # live beside the weapon pixel source, not in renderer-only Python.
        self.combat_preview=True
        self.pixel_views=[]; self.undo_stack=[]; self.dirty=False
        self.metal_canvas=None; self.metal_canvas_error=""
        self.tool="pen"; self.selected_color=PALETTE[3]
        # FIX7 animation editor state. Base remains the static fallback; animation
        # frames are optional and share the exact same canvas/anchor.
        self.animation_mode=False; self.animation_state=""; self.animation_frame_index=0
        # FIX124: explicit effect-frame editor mode.  This is deliberately
        # separate from the generic inspector layout so tapping 「逐幀編輯」
        # always produces a visible UI mode change on Pyto/iPhone.
        self.fx_editor_active=False
        self.fx_editor_panel=None; self.fx_editor_title=None; self.fx_editor_note=None
        self.fx_editor_buttons={}
        # FIX127: world-space FX choreography editor.  Pixel frames remain
        # stationary on the large drawing canvas; this separate modal edits the
        # gameplay segment schedule (delay/position/scale) and previews the same
        # moving/stamped effect path that CreatureBoundVFXSystem consumes.
        self.fx_motion_overlay=None; self.fx_motion_panel=None; self.fx_motion_visible=False
        self.fx_motion_title=None; self.fx_motion_info=None; self.fx_motion_note=None
        self.fx_motion_stage=None; self.fx_motion_ground=None; self.fx_motion_buttons={}; self.fx_motion_labels={}
        self.fx_motion_profile=None; self.fx_motion_segment_index=0; self.fx_motion_segments_backup=[]
        self.fx_motion_markers=[]; self.fx_motion_sprite_instances=[]
        self.fx_motion_preview_playing=False; self.fx_motion_preview_generation=0; self.fx_motion_preview_time=0.0
        self.fx_motion_preview_started=0.0; self.fx_motion_preview_pending=False; self.fx_motion_preview_thread=None
        self.onion_mode=0  # 0 off, 1 previous, 2 previous+next
        self.animation_playing=False; self._animation_stop=False; self._animation_thread=None
        self._animation_preview_generation=0; self._animation_ui_pending=False
        # Generic marker selected by the timeline UI.  Every animation can use
        # link/loop/action; jump additionally exposes takeoff.
        self.keyframe_marker_type="link"
        # FIX5 selection/clipboard state. Selection is a set of (x, y) cells.
        # Clipboard keeps a shaped mask so brush selections preserve holes.
        self.selection=set(); self.clipboard=None
        # FIX15 selection compositing mode. 「不遮擋」 behaves like the
        # transparent-selection option in pixel editors: transparent cells in a
        # moved/pasted selection do NOT erase artwork underneath. 「遮擋」 keeps
        # the old opaque behaviour where transparent cells can clear the target.
        # The visual selection itself is always a thin outer outline so it does
        # not cover the pixel artwork while editing.
        self.selection_transparent_mode=True
        # The selection frame uses four independent lines. Pyto's
        # CALayer border intermittently omitted the bottom edge on iPhone, so a
        # single bordered UIView cannot be used as the authoritative outline.
        self.selection_outline=None
        self.selection_outline_edges=[]
        self.selection_outline_handles=[]
        self._selection_gesture_mode=None; self._selection_anchor=None
        self._selection_brush_last=None; self._selection_move_source=set()
        self._selection_move_base=None; self._selection_move_last_delta=(0,0)
        self._flip_tap_generation=0; self._flip_last_tap=0.0
        # Resprite-style adjustable rotation dialog.  The source snapshot stays
        # immutable while the slider previews angles, so Apply creates exactly
        # one undo step and Cancel restores the frame without pixel drift.
        self.rotation_overlay=None; self.rotation_panel=None; self.rotation_visible=False
        self.rotation_slider=None; self.rotation_angle_label=None; self.rotation_angle=0.0
        self._rotation_source_payload=None; self._rotation_source_box=None
        self._rotation_original_pixels=None; self._rotation_original_selection=set()
        self._rotation_original_dirty=False; self._rotation_last_preview=0.0
        self._rotation_preview_generation=0; self._rotation_preview_scheduled=False
        self._rotation_scheduled_generation=None
        self._rotation_pending_angle=0.0
        # FIX118: canvas resolution is chosen from an explicit modal instead of
        # a one-way/cycling toolbar button.  This prevents an accidental tap
        # from trapping a 96px asset at 128px and makes view zoom clearly
        # separate from authored pixel resolution.
        self.canvas_size_overlay=None; self.canvas_size_panel=None; self.canvas_size_visible=False
        self.canvas_size_option_buttons=[]; self.canvas_size_pending=None; self.canvas_size_pending_loss=0
        self.canvas_size_title=None; self.canvas_size_info=None; self.canvas_size_note=None
        self.canvas_size_close=None; self.canvas_size_cancel=None
        self.canvas_size_crop_cancel=None; self.canvas_size_crop_apply=None
        self.preview_grid=min(self.canvas_size,PREVIEW_MAX_GRID); self.preview_cells=[]; self._preview_last=0.0; self._preview_world=(40.0,40.0); self._preview_color_cache=[]
        self.buttons={}; self.labels={}; self.palette_buttons=[]
        # FIX11 close lifecycle mirrors the main game's exact-controller UIKit
        # dismissal instead of relying on PyView.close().
        self._closing=False; self._close_completed=threading.Event()
        # FIX12 Landscape lock state: after the first real landscape layout,
        # later physical portrait rotation is ignored and UIKit is asked to keep
        # the exact fullscreen controller landscape-only.
        self.layout_locked=False; self.landscape_w=0.0; self.landscape_h=0.0
        # FIX33 iPad/Stage Manager layout guard.  Pyto can re-enter root.layout
        # while hundreds of pixel-cell frames are being updated.  Without a
        # guard, half the cells receive the old viewport and half the new one,
        # producing the split/misaligned canvas visible during window resizing.
        self._layout_in_progress=False
        self._layout_target_size=(0.0,0.0)
        self._layout_deferred=False
        # Visual colour chooser (no hexadecimal knowledge required).
        self.color_picker_overlay=None; self.color_picker_dismiss=None
        self.color_picker_panel=None; self.color_picker_swatches=[]; self.color_picker_visible=False
        self.color_picker_hue=0.33; self.color_picker_sat=0.62; self.color_picker_val=0.88
        self.color_sv_cells=[]; self.color_hue_cells=[]; self.color_sv_overlay=None; self.color_hue_overlay=None
        self.color_picker_confirm=None; self._color_sv_last_refresh=0.0
        # FIX19 custom animation-state creator.  States are plain data keys and
        # can later be bound to an AI creature action in action_editor_main.py.
        self.state_creator_overlay=None; self.state_creator_panel=None; self.state_name_field=None; self.state_creator_visible=False
        # FIX32 new-asset creator. New authored creatures are immediately registered for runtime spawning.
        self.asset_creator_overlay=None; self.asset_creator_panel=None; self.asset_id_field=None; self.asset_name_field=None; self.asset_creator_visible=False
        self._last_painted=None; self._pan_snapshot_taken=False; self._loading=False
        self._build(); self._load_selected()

    @property
    def category(self):return self.categories[self.category_index%len(self.categories)]

    @property
    def asset_ids(self):
        rows=self.model.recommended_ids(self.category[0]) or [self.category[0]+".default"]
        # FIX7 first animation pass targets the character body. The sword keeps
        # its existing procedural swing until rotated sprite support is added.
        return rows

    @property
    def asset_id(self):
        rows=self.asset_ids; self.asset_index%=len(rows); return rows[self.asset_index]

    def _is_vfx_state(self,state=None):
        return bool((self.category[0]=="weapon" and str(self.animation_state if state is None else state) in EFFECT_STATES) or (self.category[0]=="effect" and str(self.animation_state if state is None else state)=="effect"))

    def _display_grid_size(self):
        # FIX117 keeps source resolution and visual zoom independent.  At
        # 1.0x or below Metal shows the complete logical canvas; shrinking below
        # 1.0x is handled by the physical canvas frame, not by deleting pixels.
        # The UIKit fallback keeps its 32x32 retained-view cap for stability.
        renderer=getattr(self,"metal_canvas",None)
        base=max(1,int(self.canvas_size)) if renderer is not None and renderer.available else max(1,min(int(self.canvas_size),EDITOR_VIEW_GRID_MAX))
        zoom=max(CANVAS_ZOOM_MIN,min(CANVAS_ZOOM_MAX,float(getattr(self,"canvas_zoom",1.0))))
        if zoom<=1.0:
            return min(int(self.canvas_size),base)
        return min(int(self.canvas_size),max(4,int(round(base/zoom))))

    def _canvas_visual_scale(self):
        # Only sub-1x zoom changes the physical drawing square.  Zooming in
        # continues to fill the workspace and reveals fewer logical pixels.
        return max(CANVAS_ZOOM_MIN,min(1.0,float(getattr(self,"canvas_zoom",1.0))))

    def _layout_canvas_view(self):
        """Apply only canvas/overlay geometry during pinch; avoid full UI relayout."""
        try:canvas_x,canvas_y,canvas_side=self._canvas_workspace_frame
        except Exception:return
        canvas_side=max(1.0,float(canvas_side))
        visual=self._canvas_visual_scale(); active_side=max(1.0,canvas_side*visual)
        active_x=float(canvas_x)+(canvas_side-active_side)*0.5
        active_y=float(canvas_y)+(canvas_side-active_side)*0.5
        view_grid=max(1,self._display_grid_size()); cell=active_side/view_grid
        if self.metal_canvas is not None and self.metal_canvas.available:
            self.metal_canvas.frame=(active_x,active_y,active_side,active_side)
        for i,v in enumerate(self.pixel_views):
            yy=i//view_grid; xx=i%view_grid
            v.frame=(active_x+xx*cell,active_y+yy*cell,cell+0.15,cell+0.15)
        self.overlay.frame=(active_x,active_y,active_side,active_side)
        self._update_selection_outline()

    def fit_canvas_view(self):
        """Show the complete active frame, without changing a single pixel."""
        self.canvas_zoom=1.0;self.canvas_view_x=0;self.canvas_view_y=0
        self._nav_pan_accum=[0.0,0.0]
        self._rebuild_pixel_views()
        self.refresh_pixels();self.refresh_labels()
        try:self._layout()
        except Exception:pass
        n=self.canvas_size
        self.labels["status"].text=f"全圖檢視：{n}×{n} 原始像素｜目前 1.00×；雙指可縮到 {CANVAS_ZOOM_MIN:.2f}× 或放大到 {CANVAS_ZOOM_MAX:.0f}×"

    def select_all(self):
        """Select the logical frame, including offscreen/transparent edge cells."""
        if not self.pixels:return
        self._stop_animation_preview();self._reset_selection_gesture()
        self.selection={(x,y) for y in range(self.canvas_size) for x in range(self.canvas_size)}
        self.tool="select_rect";self.selection_nav_mode=False
        self._sync_overlay_drag_mode()
        self._refresh_selection_cells();self.refresh_labels()
        self.buttons["gesture_mode"].title="拖曳：框選"
        self.labels["status"].text=f"已全選目前畫布 {self.canvas_size}×{self.canvas_size}：{len(self.selection)} 個像素（含透明與視窗外）；可複製／剪下／翻轉"

    def _cancel_canvas_gesture(self):
        """A second finger or cancelled recognizer must not leave a half edit."""
        old_selection=getattr(self,"_gesture_original_selection",None)
        old_dirty=getattr(self,"_gesture_original_dirty",self.dirty)
        if self._selection_gesture_mode=="move" and self._selection_move_base is not None:
            self.pixels=copy.deepcopy(self._selection_move_base)
            self.dirty=old_dirty
            if self.undo_stack:self.undo_stack.pop()
        elif getattr(self,"_pan_snapshot_taken",False) and self.undo_stack:
            snap=self.undo_stack.pop()
            if isinstance(snap,tuple) and len(snap)>=5 and snap[0]=="frame":
                self.pixels=copy.deepcopy(snap[2]);self.combat_bindings=copy.deepcopy(snap[4]);self.dirty=old_dirty
        if old_selection is not None:self.selection=set(old_selection)
        self._reset_selection_gesture();self._last_painted=None;self._pan_snapshot_taken=False
        self._gesture_original_selection=None
        self.refresh_pixels();self.refresh_labels()

    def _sync_overlay_drag_mode(self):
        """Keep selection gestures usable while preserving pan/zoom navigation.

        Selection tools need the one-finger pan recognizer to reach the selection
        callback.  FIX91 always routed that recognizer to canvas navigation when
        the global drag mode was ``navigate``, so rectangle/brush selection could
        only select one tapped pixel after zooming.
        """
        selection_tool=self.tool in ("select_rect","select_brush")
        mode=("navigate" if self.selection_nav_mode else "paint") if selection_tool else self.canvas_drag_mode
        try:self.overlay.set_drag_mode(mode)
        except Exception:pass
        return mode

    def toggle_canvas_drag_mode(self):
        if self.tool in ("select_rect","select_brush"):
            self.selection_nav_mode=not bool(self.selection_nav_mode)
            mode=self._sync_overlay_drag_mode()
            self.buttons["gesture_mode"].title="拖曳：平移" if mode=="navigate" else "拖曳：框選"
            self.labels["status"].text=(
                "選取工具｜單指拖曳移動畫布；雙指縮放。再按一次可回框選" if mode=="navigate" else
                "選取工具｜單指拖曳框選／移動選區；雙指縮放。需要平移請按『拖曳：框選』"
            )
            return
        self.canvas_drag_mode="paint" if self.canvas_drag_mode=="navigate" else "navigate"
        self._sync_overlay_drag_mode()
        self.buttons["gesture_mode"].title="拖曳：連畫" if self.canvas_drag_mode=="paint" else "拖曳：平移"
        self.labels["status"].text=("單指拖曳連續繪製；雙指縮放。點一下仍可單點繪製" if self.canvas_drag_mode=="paint" else "單指拖曳移動畫布；雙指縮放。點一下套用目前工具")

    def _canvas_nav_gesture(self,x,y,kind,state,scale,dx,dy):
        if kind=="pinch":
            if int(state)==1:self._cancel_canvas_gesture()
            old_grid=self._display_grid_size(); old_zoom=float(self.canvas_zoom)
            nx=max(0.0,min(1.0,float(x)/max(1.0,float(self.overlay.width))))
            ny=max(0.0,min(1.0,float(y)/max(1.0,float(self.overlay.height))))
            anchor_x=float(self.canvas_view_x)+nx*old_grid; anchor_y=float(self.canvas_view_y)+ny*old_grid
            self.canvas_zoom=max(CANVAS_ZOOM_MIN,min(CANVAS_ZOOM_MAX,old_zoom*max(.6,min(1.7,float(scale)))))
            new_grid=self._display_grid_size()
            self.canvas_view_x=int(round(anchor_x-nx*new_grid)); self.canvas_view_y=int(round(anchor_y-ny*new_grid)); self._clamp_canvas_view()
            if new_grid!=old_grid:self._rebuild_pixel_views()
            # Below 1.0x the logical grid stays complete while the physical
            # square shrinks, so geometry must update even when grid count is unchanged.
            if new_grid!=old_grid or old_zoom<1.0 or self.canvas_zoom<1.0:
                try:self._layout_canvas_view()
                except Exception:pass
            self.refresh_pixels(); self.refresh_labels()
            self.labels["status"].text=f"畫布縮放 {self.canvas_zoom:.2f}×｜顯示 {new_grid}×{new_grid} 像素"
            return
        if kind=="navigate":
            if int(state)==1:self._nav_pan_accum=[0.0,0.0]; return
            grid=self._display_grid_size(); cw=max(1.0,float(self.overlay.width)/grid); ch=max(1.0,float(self.overlay.height)/grid)
            self._nav_pan_accum[0]-=float(dx)/cw; self._nav_pan_accum[1]-=float(dy)/ch
            sx=int(self._nav_pan_accum[0]); sy=int(self._nav_pan_accum[1])
            if sx or sy:
                self.canvas_view_x+=sx; self.canvas_view_y+=sy; self._nav_pan_accum[0]-=sx; self._nav_pan_accum[1]-=sy; self._clamp_canvas_view(); self.refresh_pixels(); self.refresh_labels()
            if int(state) in (3,4,5):self._nav_pan_accum=[0.0,0.0]


    def _clamp_canvas_view(self):
        grid=self._display_grid_size(); limit=max(0,int(self.canvas_size)-grid)
        self.canvas_view_x=max(0,min(limit,int(self.canvas_view_x)))
        self.canvas_view_y=max(0,min(limit,int(self.canvas_view_y)))

    def _center_canvas_origin(self):
        grid=self._display_grid_size(); n=int(self.canvas_size)
        xs=[]; ys=[]
        for y,row in enumerate(self.pixels or []):
            for x,c in enumerate(row if isinstance(row,list) else []):
                if not str(c).upper().endswith("00"):xs.append(x); ys.append(y)
        cx=(min(xs)+max(xs))*0.5 if xs else (n-1)*0.5
        cy=(min(ys)+max(ys))*0.5 if ys else (n-1)*0.5
        self.canvas_view_x=int(round(cx-(grid-1)*0.5))
        self.canvas_view_y=int(round(cy-(grid-1)*0.5))
        self._clamp_canvas_view()

    def center_canvas_view(self):
        self._center_canvas_origin(); self.refresh_pixels(); self.refresh_labels()
        self.labels["status"].text="VFX 視窗已定位到目前內容；◎ 可隨時重新置中"

    def pan_canvas_view(self,dx,dy):
        if int(self.canvas_size)<=self._display_grid_size():
            self.labels["status"].text="目前整張畫布已完整顯示，不需要平移視窗"; return
        step=max(4,self._display_grid_size()//2)
        old=(self.canvas_view_x,self.canvas_view_y)
        self.canvas_view_x+=int(dx)*step; self.canvas_view_y+=int(dy)*step; self._clamp_canvas_view()
        if old!=(self.canvas_view_x,self.canvas_view_y):self.refresh_pixels()
        self.refresh_labels()
        self.labels["status"].text=f"VFX 視窗：X {self.canvas_view_x+1}–{self.canvas_view_x+self._display_grid_size()}｜Y {self.canvas_view_y+1}–{self.canvas_view_y+self._display_grid_size()}"

    def _sync_active_canvas(self,recenter=False):
        """Adopt the native frame size; accelerated renderers use a single view."""
        new_size=max(len(self.pixels),max((len(r) for r in self.pixels),default=0)) if isinstance(self.pixels,list) and self.pixels else int(self.asset_canvas_size)
        new_size=max(1,int(new_size)); old_size=int(self.canvas_size); old_grid=self._display_grid_size()
        self.canvas_size=new_size
        if self.canvas_size in PIXEL_SIZES:self.size_index=PIXEL_SIZES.index(self.canvas_size)
        if recenter:
            self.canvas_zoom=1.0;self.canvas_view_x=0;self.canvas_view_y=0
            self._nav_pan_accum=[0.0,0.0]
        self._clamp_canvas_view()
        new_grid=self._display_grid_size()
        _metal_active=bool(self.metal_canvas is not None and self.metal_canvas.available)
        if old_grid!=new_grid or ((not _metal_active) and len(self.pixel_views)!=new_grid*new_grid):self._rebuild_pixel_views()
        preview_target=min(self.canvas_size,PREVIEW_MAX_GRID)
        if self.preview_grid!=preview_target or len(self.preview_cells)!=preview_target*preview_target:self._rebuild_preview_views()
        if old_size!=self.canvas_size:
            try:self._layout()
            except Exception:pass

    def _button(self,key,title,action,bg=DARK2,font=10):
        """Create a PytoUI button using the API supported by Pyto on iOS.

        pyto_ui.Button only accepts the title in its constructor on the target
        runtime.  In particular, ``action=`` (and styling kwargs) raise
        ``TypeError: unexpected keyword argument``.  Build first, then assign
        action/style properties just like the main game and map editor do.
        """
        def wrapped(sender):
            haptic_selection()
            return action(sender)

        b=ui.Button(title=title)
        b.background_color=bg
        b.title_color=WHITE
        b.font=ui.Font.bold_system_font_of_size(font)
        b.corner_radius=6
        b.action=wrapped
        self.root.add_subview(b)
        self.buttons[key]=b
        return b

    def _label(self,key,text,font=9,bg=DARK2,lines=2):
        l=ui.Label(text); l.text_color=WHITE; l.font=ui.Font.system_font_of_size(font); l.text_alignment=_center(); l.number_of_lines=lines
        if bg is not None:l.background_color=bg;l.corner_radius=5
        self.root.add_subview(l); self.labels[key]=l; return l

    def _build(self):
        self._button("cat_prev","◀ 類別",lambda _s:self.change_category(-1))
        self._button("cat_next","類別 ▶",lambda _s:self.change_category(1))
        self._button("asset_prev","◀ 物件",lambda _s:self.change_asset(-1))
        self._button("asset_next","物件 ▶",lambda _s:self.change_asset(1))
        self._button("asset_add","＋新增素材",lambda _s:self.open_asset_creator(),GREEN,8)
        self._button("tile_properties","圖塊屬性",lambda _s:self.open_tile_properties(),BLUE,9)
        self._button("size","16×16",lambda _s:self.change_size(),BLUE)
        self._button("pen","畫筆",lambda _s:self.set_tool("pen"),GREEN)
        self._button("erase","橡皮",lambda _s:self.set_tool("erase"),RED)
        self._button("fill","填滿",lambda _s:self.set_tool("fill"),GOLD)
        self._button("picker","吸色",lambda _s:self.set_tool("picker"),BLUE)
        self._button("gesture_mode","拖曳：平移",lambda _s:self.toggle_canvas_drag_mode(),DARK2,8)
        self._button("select_rect","矩形選取",lambda _s:self.set_tool("select_rect"),DARK2,9)
        self._button("select_brush","畫筆選取",lambda _s:self.set_tool("select_brush"),DARK2,9)
        self._button("select_all","全選",lambda _s:self.select_all(),BLUE,9)
        self._button("fit_canvas","全圖",lambda _s:self.fit_canvas_view(),BLUE,9)
        self._button("cut","剪下",lambda _s:self.cut_selection(),RED,9)
        self._button("copy","複製",lambda _s:self.copy_selection(),DARK2,9)
        self._button("paste","貼上",lambda _s:self.paste_clipboard(),BLUE,9)
        self._button("rotate_sel","旋轉角度",lambda _s:self.open_rotation_editor(),DARK2,8)
        self._button("mirror_sel","鏡射複製",lambda _s:self.mirror_selection(),DARK2,8)
        self._button("flip_sel","翻轉 H/V",lambda _s:self.request_flip_selection(),DARK2,8)
        self._button("clear_sel","取消選取",lambda _s:self.clear_selection(),DARK2,8)
        self._button("sel_overlay","不遮擋✓",lambda _s:self.toggle_selection_occlusion(),DARK2,8)
        self._button("undo","撤銷",lambda _s:self.undo())
        self._button("reload","匯入系統預設",lambda _s:self.reload_builtin())
        self._button("save","存檔 PNG",lambda _s:self.save_asset(),BLUE,11)
        # FIX7 animation controls live in the right inspector to avoid repeating
        # the FIX5 toolbar-overlap problem on iPhone landscape.
        self._button("anim_toggle","動畫模式",lambda _s:self.toggle_animation_mode(),BLUE,9)
        self._button("auto_anim","自動動畫",lambda _s:self.generate_creature_animation(),GREEN,8)
        self._button("anim_state","狀態：idle",lambda _s:self.cycle_animation_state(),DARK2,8)
        # FIX123: a dedicated non-destructive FX frame counter.  Effect assets
        # use this instead of the state-cycle button so the timeline reads like
        # an ordinary animation editor: previous / current frame / next.
        self._button("fx_frame_counter","幀 1/1",lambda _s:None,DARK2,9)
        self._button("frame_prev","◀ 幀",lambda _s:self.change_animation_frame(-1),DARK2,9)
        self._button("frame_next","幀 ▶",lambda _s:self.change_animation_frame(1),DARK2,9)
        self._button("frame_add","＋空白幀",lambda _s:self.add_animation_frame(False),DARK2,8)
        self._button("frame_dup","複製幀",lambda _s:self.add_animation_frame(True),GREEN,8)
        self._button("frame_del","刪除幀",lambda _s:self.delete_animation_frame(),RED,8)
        self._button("onion","洋蔥：關",lambda _s:self.cycle_onion_skin(),GOLD,8)
        self._button("anim_play","▶ 播放",lambda _s:self.toggle_animation_preview(),BLUE,8)
        self._button("fps_down","FPS－",lambda _s:self.adjust_animation_fps(-1),DARK2,8)
        self._button("fps_up","FPS＋",lambda _s:self.adjust_animation_fps(1),DARK2,8)
        self._button("anim_loop","循環",lambda _s:self.toggle_animation_loop(),DARK2,8)
        self._button("key_type","關鍵：銜接",lambda _s:self.cycle_keyframe_type(),DARK2,8)
        self._button("anim_event","設銜接",lambda _s:self.toggle_animation_event(),GOLD,8)
        self._button("state_add","＋狀態",lambda _s:self.open_state_creator(),GREEN,8)
        # FIX59 gives the gameplay hit event its own visible control.  It writes
        # the existing ``events.action`` marker, so old assets and runtime
        # timing stay compatible while artists no longer need to discover it
        # through the generic keyframe-type carousel.
        self._button("attack_frame","設攻擊幀",lambda _s:self.toggle_attack_frame(),RED,8)
        self._button("fx_damage_frame","FX傷害幀",lambda _s:self.toggle_fx_damage_frame(),RED,8)
        self._button("fx_motion","運動設定",lambda _s:self.open_fx_motion_editor(),BLUE,8)
        self._button("vfx_bind","VFX綁定@本幀",lambda _s:self.bind_effect_here(),GOLD,8)
        self._button("vfx_pair","切換攻擊/特效",lambda _s:self.toggle_paired_weapon_clip(),BLUE,8)
        self._button("vfx_anchor","附著：武器",lambda _s:self.cycle_vfx_anchor(),DARK2,8)
        self._button("vfx_preview","合成預覽✓",lambda _s:self.toggle_combat_preview(),GREEN,8)
        self._button("view_left","◀",lambda _s:self.pan_canvas_view(-1,0),DARK2,11)
        self._button("view_up","▲",lambda _s:self.pan_canvas_view(0,-1),DARK2,11)
        self._button("view_center","◎",lambda _s:self.center_canvas_view(),GOLD,11)
        self._button("view_down","▼",lambda _s:self.pan_canvas_view(0,1),DARK2,11)
        self._button("view_right","▶",lambda _s:self.pan_canvas_view(1,0),DARK2,11)
        self._button("close","×",lambda _s:self.close_editor(),RED,16)
        self._label("title","",12,DARK2,2)
        self._label("tool","",9,DARK2,2)
        self._label("status","點／拖曳可以直接上色",8,DARK2,5)
        try:
            self.hex_field=ui.TextField(); self.hex_field.placeholder="#RRGGBB"; self.hex_field.text="#5C8D45"; self.hex_field.background_color=WHITE; self.hex_field.text_color=DARK; self.root.add_subview(self.hex_field)
        except Exception:self.hex_field=None
        self._button("apply_hex","套用色碼",lambda _s:self.apply_hex(),DARK2,9)
        self._button("select_color","選色",lambda _s:self.open_color_picker(),BLUE,10)
        # FIX13: persistent current-colour swatch.  Tapping it reopens the
        # visual picker; eyedropper / palette / picker / manual hex all update it.
        self._button("current_color","目前色",lambda _s:self.open_color_picker(),color(self.selected_color[:7]),9)
        self.buttons["current_color"].border_width=2
        self.buttons["current_color"].border_color=WHITE
        for i,c in enumerate(PALETTE):
            b=self._button("pal%d"%i," ",lambda _s,cc=c:self.choose_color(cc),color(c[:7]),8)
            self.palette_buttons.append(b)

        # Dynamic logical-canvas preview.  The outlined square represents the
        # CURRENT editor canvas (16×16 through 128×128), not a fixed tile or
        # fixed 16×16 box.  Therefore transparent padding and per-frame offsets
        # keep exactly the same logical coordinates from editor to runtime.
        self._label("preview_title","遊戲比例預覽",9,DARK2,2)
        self.preview_bg=ui.View(); self.preview_bg.background_color=color("#0D141B"); self.preview_bg.border_width=1; self.preview_bg.border_color=color("#52606B"); self.root.add_subview(self.preview_bg)
        self.preview_ref=ui.View(); self.preview_ref.background_color=ui.Color.rgb(0,0,0,0); self.preview_ref.border_width=1; self.preview_ref.border_color=color("#71808C"); self.root.add_subview(self.preview_ref)
        self.preview_ground=ui.View(); self.preview_ground.background_color=color("#8C7A56"); self.root.add_subview(self.preview_ground)
        for _i in range(self.preview_grid*self.preview_grid):
            v=ui.View(); v.user_interaction_enabled=False; v.border_width=0
            try:v.hidden=True
            except Exception:pass
            self.root.add_subview(v); self.preview_cells.append(v)

        self.canvas_bg=ui.View(); self.canvas_bg.background_color=color("#111820"); self.canvas_bg.border_width=2; self.canvas_bg.border_color=WHITE; self.root.add_subview(self.canvas_bg)
        # FIX116: one paused/on-demand MTKView replaces per-pixel UIKit views, including 96²/128² canvases
        # cell pool.  Pixel JSON, touch mapping and selection tools stay fully
        # renderer-neutral.  A retained UIKit fallback remains for older Pyto
        # builds without MetalKit.
        try:
            from asset_editor.metal_canvas import MetalPixelCanvas
            candidate=MetalPixelCanvas()
            if candidate.available:
                self.metal_canvas=candidate; self.root.add_subview(candidate.view)
            else:
                self.metal_canvas_error=str(candidate.state.last_error); candidate.close()
        except Exception as exc:
            self.metal_canvas_error=repr(exc)
            print("ASSET EDITOR Metal canvas import warning:",repr(exc))
        if self.metal_canvas is None:
            try:
                from asset_editor.raster_canvas import RasterPixelCanvas
                self.metal_canvas=RasterPixelCanvas()
                self.root.add_subview(self.metal_canvas.view)
            except Exception as exc:
                self.metal_canvas=None
                print("ASSET EDITOR bitmap canvas unavailable:",repr(exc))
        self.overlay=PixelPaintOverlay(self._paint_gesture,self._canvas_nav_gesture); self.overlay.set_drag_mode(self.canvas_drag_mode); self.root.add_subview(self.overlay)
        self._rebuild_pixel_views()
        # FIX58 selection gizmo is parented to the touch canvas itself.  Local
        # coordinates avoid the root/view-frame mismatch that made FIX57's
        # sibling views appear off-canvas on some Pyto/UIKit versions.
        for _edge_index in range(4):
            edge=ui.View(); edge.background_color=GOLD; edge.border_width=0
            try:edge.user_interaction_enabled=False; edge.hidden=True
            except Exception:pass
            self.overlay.add_subview(edge); self.selection_outline_edges.append(edge)
        for _handle_index in range(4):
            handle=ui.View(); handle.background_color=GOLD; handle.border_width=1; handle.border_color=WHITE
            try:handle.user_interaction_enabled=False; handle.hidden=True; handle.corner_radius=2
            except Exception:pass
            self.overlay.add_subview(handle); self.selection_outline_handles.append(handle)
        try:self.root.bring_subview_to_front(self.overlay)
        except Exception:pass
        # FIX124: dedicated FX frame editor panel is a retained top-level view.
        # It does not share the ordinary inspector rows and therefore cannot be
        # lost behind the preview/game-scale views on short iPhone landscape.
        self._build_fx_frame_editor_panel()
        # FIX13: build the colour picker LAST so its full-screen modal overlay
        # lives above canvas/preview/palette siblings instead of underneath them.
        self._build_color_picker()
        # Keep the state-name modal above every editor sibling.
        self._build_state_creator()
        self._build_asset_creator()
        # Build last so the adjustable-rotation modal can always be raised above
        # the large pixel-cell pool on iPhone and iPad Stage Manager.
        self._build_rotation_editor()
        # FIX118 canvas-size chooser remains retained.  FIX127 motion editor is
        # built after it and explicitly raised when opened.
        self._build_canvas_size_picker()
        self._build_fx_motion_editor()

    def _build_canvas_size_picker(self):
        overlay=ui.View(); overlay.background_color=ui.Color.rgb(0.0,0.0,0.0,0.54); overlay.hidden=True
        try:overlay.user_interaction_enabled=True
        except Exception:pass
        self.root.add_subview(overlay); self.canvas_size_overlay=overlay

        dismiss=ui.Button(title=" ")
        try:dismiss.background_color=ui.Color.rgb(0,0,0,0.01); dismiss.title_color=ui.Color.rgb(0,0,0,0)
        except Exception:pass
        dismiss.action=lambda _s:self.close_canvas_size_picker()
        overlay.add_subview(dismiss); self.canvas_size_dismiss=dismiss

        panel=ui.View(); panel.background_color=color("#263848"); panel.corner_radius=10
        try:panel.border_width=1; panel.border_color=color("#71808C")
        except Exception:pass
        overlay.add_subview(panel); self.canvas_size_panel=panel

        title=ui.Label("素材畫布尺寸")
        title.text_color=WHITE; title.text_alignment=_center(); title.font=ui.Font.bold_system_font_of_size(13)
        panel.add_subview(title); self.canvas_size_title=title
        info=ui.Label("")
        info.text_color=GOLD; info.text_alignment=_center(); info.font=ui.Font.bold_system_font_of_size(10); info.number_of_lines=2
        panel.add_subview(info); self.canvas_size_info=info
        note=ui.Label("這裡修改的是素材原稿解析度，不是畫面倍率。\n要把畫面看大／看小，請使用『全圖』或雙指縮放。")
        note.text_color=WHITE; note.text_alignment=_center(); note.font=ui.Font.system_font_of_size(9); note.number_of_lines=2
        panel.add_subview(note); self.canvas_size_note=note

        close=ui.Button(title="×"); close.background_color=RED; close.title_color=WHITE; close.corner_radius=6; close.font=ui.Font.bold_system_font_of_size(15)
        close.action=lambda _s:self.close_canvas_size_picker(); panel.add_subview(close); self.canvas_size_close=close

        for n in PIXEL_SIZES:
            b=ui.Button(title=f"{int(n)}×{int(n)}"); b.background_color=DARK2; b.title_color=WHITE; b.corner_radius=6
            b.font=ui.Font.bold_system_font_of_size(10); b.action=lambda _s,nn=int(n):self.request_canvas_size(nn)
            panel.add_subview(b); self.canvas_size_option_buttons.append((int(n),b))

        cancel=ui.Button(title="取消"); cancel.background_color=DARK2; cancel.title_color=WHITE; cancel.corner_radius=6
        cancel.font=ui.Font.bold_system_font_of_size(11); cancel.action=lambda _s:self.close_canvas_size_picker()
        panel.add_subview(cancel); self.canvas_size_cancel=cancel
        crop_cancel=ui.Button(title="返回尺寸選擇"); crop_cancel.background_color=DARK2; crop_cancel.title_color=WHITE; crop_cancel.corner_radius=6
        crop_cancel.font=ui.Font.bold_system_font_of_size(10); crop_cancel.action=lambda _s:self._cancel_canvas_crop_prompt()
        panel.add_subview(crop_cancel); self.canvas_size_crop_cancel=crop_cancel
        crop_apply=ui.Button(title="確認裁切"); crop_apply.background_color=RED; crop_apply.title_color=WHITE; crop_apply.corner_radius=6
        crop_apply.font=ui.Font.bold_system_font_of_size(11); crop_apply.action=lambda _s:self._confirm_canvas_crop()
        panel.add_subview(crop_apply); self.canvas_size_crop_apply=crop_apply

    def _layout_canvas_size_picker(self):
        if self.canvas_size_overlay is None:return
        w=float(self.landscape_w or self.root.width or 1.0); h=float(self.landscape_h or self.root.height or 1.0)
        self.canvas_size_overlay.frame=(0,0,w,h); self.canvas_size_dismiss.frame=(0,0,w,h)
        pw=min(620.0,max(410.0,w-110.0)); ph=min(276.0,max(244.0,h-70.0)); x=(w-pw)*0.5; y=(h-ph)*0.5
        self.canvas_size_panel.frame=(x,y,pw,ph); self.canvas_size_title.frame=(16,8,pw-80,30); self.canvas_size_close.frame=(pw-48,8,34,30)
        self.canvas_size_info.frame=(16,40,pw-32,42)
        cols=4; gap=6.0; left=16.0; row_y=90.0; bw=(pw-left*2-gap*(cols-1))/cols; bh=34.0
        for i,(n,b) in enumerate(self.canvas_size_option_buttons):
            r=i//cols; c=i%cols; b.frame=(left+c*(bw+gap),row_y+r*(bh+gap),bw,bh)
        self.canvas_size_note.frame=(16,174,pw-32,42)
        self.canvas_size_cancel.frame=(pw*0.5-78,226,156,34)
        self.canvas_size_crop_cancel.frame=(16,220,(pw-38)/2.0,38)
        self.canvas_size_crop_apply.frame=(22+(pw-38)/2.0,220,(pw-38)/2.0,38)
        try:
            self.canvas_size_overlay.bring_subview_to_front(self.canvas_size_panel)
            self.root.bring_subview_to_front(self.canvas_size_overlay)
        except Exception:pass

    def _canvas_size_export_now(self):
        if self._is_vfx_state():
            raw=self._current_animation()
            try:return int((raw.get("export_size") if isinstance(raw,dict) else None) or self.canvas_size)
            except Exception:return int(self.canvas_size)
        try:return int(self._native_export_override or self.export_canvas_size or self.asset_canvas_size)
        except Exception:return int(self.asset_canvas_size)

    def _refresh_canvas_size_picker(self,confirm_crop=False):
        if self.canvas_size_panel is None:return
        is_vfx=bool(self._is_vfx_state()); current=int(self.canvas_size if is_vfx else self.asset_canvas_size)
        allowed=set(int(v) for v in (VFX_PIXEL_SIZES if is_vfx else PIXEL_SIZES))
        self.canvas_size_title.text="特效畫布尺寸" if is_vfx else "素材畫布尺寸"
        if confirm_crop and self.canvas_size_pending is not None:
            target=int(self.canvas_size_pending); loss=int(self.canvas_size_pending_loss)
            self.canvas_size_title.text="縮小畫布會裁切內容"
            self.canvas_size_info.text=f"{current}×{current} → {target}×{target} 會移除 {loss} 個非透明像素"
            self.canvas_size_note.text="裁切會依素材錨點保留位置；被切掉的內容會移除。\n套用後仍可使用『撤銷』回復。"
            for _n,b in self.canvas_size_option_buttons:b.hidden=True
            self.canvas_size_cancel.hidden=True; self.canvas_size_crop_cancel.hidden=False; self.canvas_size_crop_apply.hidden=False
            return
        self.canvas_size_pending=None; self.canvas_size_pending_loss=0
        export_now=self._canvas_size_export_now()
        scale=float(export_now)/max(1,current)
        self.canvas_size_info.text=f"目前原稿 {current}×{current} → 輸出 {export_now}×{export_now}（{scale:.1f}×）"
        self.canvas_size_note.text="這裡修改的是素材原稿解析度，不是畫面倍率。\n要把畫面看大／看小，請使用『全圖』或雙指縮放。"
        for n,b in self.canvas_size_option_buttons:
            b.hidden=(n not in allowed); b.title=f"{n}×{n}"+(" ✓" if n==current else "")
            b.background_color=GOLD if n==current else DARK2
        self.canvas_size_cancel.hidden=False; self.canvas_size_crop_cancel.hidden=True; self.canvas_size_crop_apply.hidden=True

    def open_canvas_size_picker(self):
        if str(self.category[0])=="tile":
            self.labels["status"].text=(f"圖塊原稿 {self.asset_canvas_size}×{self.asset_canvas_size}｜地圖仍為一格；圖塊尺寸不在這裡變更。\n若只是看大／看小，請使用『全圖』或雙指縮放。")
            return
        self._stop_animation_preview(); self._commit_current_frame()
        self.canvas_size_visible=True; self.canvas_size_overlay.hidden=False; self._refresh_canvas_size_picker(False); self._layout_canvas_size_picker()
        self.labels["status"].text="選擇素材原稿尺寸；這個設定與畫面縮放倍率完全分開"

    def close_canvas_size_picker(self):
        self.canvas_size_visible=False; self.canvas_size_pending=None; self.canvas_size_pending_loss=0
        if self.canvas_size_overlay is not None:self.canvas_size_overlay.hidden=True

    def _cancel_canvas_crop_prompt(self):
        self._refresh_canvas_size_picker(False); self._layout_canvas_size_picker()

    def _confirm_canvas_crop(self):
        if self.canvas_size_pending is None:return
        target=int(self.canvas_size_pending); self._apply_canvas_size(target,allow_crop=True); self.close_canvas_size_picker()

    def _build_rotation_editor(self):
        """Build a retained adjustable-angle rotation panel.

        All controls are created once.  Dragging the slider updates the current
        selection from an immutable snapshot; it never rotates an already
        rotated preview, which avoids cumulative nearest-neighbour damage.
        """
        overlay=ui.View(); overlay.background_color=ui.Color.rgb(0.0,0.0,0.0,0.52); overlay.hidden=True
        try:overlay.user_interaction_enabled=True
        except Exception:pass
        self.root.add_subview(overlay); self.rotation_overlay=overlay

        dismiss=ui.Button(title=" ")
        try:dismiss.background_color=ui.Color.rgb(0,0,0,0.01); dismiss.title_color=ui.Color.rgb(0,0,0,0)
        except Exception:pass
        dismiss.action=lambda _s:self.close_rotation_editor(False)
        overlay.add_subview(dismiss); self.rotation_dismiss=dismiss

        panel=ui.View(); panel.background_color=color("#263848"); panel.corner_radius=10
        try:panel.border_width=1; panel.border_color=color("#71808C")
        except Exception:pass
        overlay.add_subview(panel); self.rotation_panel=panel

        title=ui.Label("選取框旋轉｜拖曳角度後套用")
        title.text_color=WHITE; title.text_alignment=_center(); title.font=ui.Font.bold_system_font_of_size(12)
        panel.add_subview(title); self.rotation_title=title
        angle=ui.Label("0°")
        angle.text_color=GOLD; angle.text_alignment=_center(); angle.font=ui.Font.bold_system_font_of_size(20)
        panel.add_subview(angle); self.rotation_angle_label=angle

        slider=ui.Slider(0.0); slider.minimum_value=-180.0; slider.maximum_value=180.0
        slider.action=self._rotation_slider_changed
        panel.add_subview(slider); self.rotation_slider=slider

        def add_button(title,bg,action,font=10):
            button=ui.Button(title=title); button.background_color=bg; button.title_color=WHITE
            button.corner_radius=6; button.font=ui.Font.bold_system_font_of_size(font); button.action=action
            panel.add_subview(button); return button
        self.rotation_m15=add_button("－15°",DARK2,lambda _s:self._nudge_rotation(-15.0))
        self.rotation_m1=add_button("－1°",DARK2,lambda _s:self._nudge_rotation(-1.0))
        self.rotation_reset=add_button("歸零",GOLD,lambda _s:self._set_rotation_angle(0.0))
        self.rotation_p1=add_button("＋1°",DARK2,lambda _s:self._nudge_rotation(1.0))
        self.rotation_p15=add_button("＋15°",DARK2,lambda _s:self._nudge_rotation(15.0))
        self.rotation_cancel=add_button("取消",DARK2,lambda _s:self.close_rotation_editor(False),11)
        self.rotation_apply=add_button("套用旋轉",BLUE,lambda _s:self.close_rotation_editor(True),11)
        note=ui.Label("角度範圍 −180°～180°｜以選取框中心旋轉｜像素採最近鄰取樣")
        note.text_color=WHITE; note.text_alignment=_center(); note.font=ui.Font.system_font_of_size(9)
        panel.add_subview(note); self.rotation_note=note

    def _layout_rotation_editor(self):
        if self.rotation_overlay is None:return
        w=float(self.landscape_w or self.root.width or 1.0); h=float(self.landscape_h or self.root.height or 1.0)
        self.rotation_overlay.frame=(0,0,w,h); self.rotation_dismiss.frame=(0,0,w,h)
        pw=min(560.0,max(390.0,w-150.0)); ph=min(238.0,max(216.0,h-80.0)); x=(w-pw)*0.5; y=(h-ph)*0.5
        self.rotation_panel.frame=(x,y,pw,ph); self.rotation_title.frame=(14,8,pw-28,28)
        self.rotation_angle_label.frame=(14,38,pw-28,32); self.rotation_slider.frame=(24,72,pw-48,30)
        gap=5.0; bw=(pw-48.0-gap*4.0)/5.0; by=108.0
        for index,button in enumerate((self.rotation_m15,self.rotation_m1,self.rotation_reset,self.rotation_p1,self.rotation_p15)):
            button.frame=(14.0+index*(bw+gap),by,bw,30)
        action_y=146.0; action_w=(pw-34.0)/2.0
        self.rotation_cancel.frame=(14,action_y,action_w,34); self.rotation_apply.frame=(20+action_w,action_y,action_w,34)
        self.rotation_note.frame=(14,184,pw-28,max(24.0,ph-190.0))
        try:
            self.rotation_overlay.bring_subview_to_front(self.rotation_panel)
            self.root.bring_subview_to_front(self.rotation_overlay)
        except Exception:pass

    def open_rotation_editor(self):
        payload=self._clipboard_payload(); box=self._selection_bbox()
        if payload is None or box is None:
            self.labels["status"].text="請先選取要旋轉的繪圖框"; return
        self._stop_animation_preview()
        self.rotation_angle=0.0; self._rotation_source_payload=copy.deepcopy(payload); self._rotation_source_box=tuple(box)
        self._rotation_original_pixels=copy.deepcopy(self.pixels); self._rotation_original_selection=set(self.selection)
        self._rotation_original_dirty=bool(self.dirty); self._rotation_last_preview=0.0
        self._rotation_preview_generation+=1; self._rotation_preview_scheduled=False
        self._rotation_scheduled_generation=None; self._rotation_pending_angle=0.0
        try:self.rotation_slider.value=0.0; self.rotation_angle_label.text="0°"
        except Exception:pass
        self.rotation_visible=True; self.rotation_overlay.hidden=False; self._layout_rotation_editor()
        self.labels["status"].text="旋轉角度：拖曳滑桿可即時預覽；＋/－1° 可精準微調"

    def _rotation_slider_changed(self,sender):
        try:value=float(sender.value)
        except Exception:value=0.0
        self._set_rotation_angle(value,False)

    def _set_rotation_angle(self,value,force_preview=True):
        value=max(-180.0,min(180.0,float(value))); value=round(value,1)
        self.rotation_angle=value
        try:
            self.rotation_slider.value=value
            self.rotation_angle_label.text=("%+.1f°"%value).replace(".0°","°")
        except Exception:pass
        if force_preview:
            self._rotation_preview_generation+=1; self._rotation_preview_scheduled=False
            self._rotation_scheduled_generation=None
            self._preview_selection_rotation(value,True)
        else:
            self._schedule_rotation_preview(value)

    def _nudge_rotation(self,delta):
        self._set_rotation_angle(float(self.rotation_angle)+float(delta),True)

    def _schedule_rotation_preview(self,angle_deg):
        """Coalesce slider events before touching UIKit.

        FIX57 refreshed every native pixel view for every slider callback. On
        Pyto this can saturate the Objective-C bridge and terminate the host.
        The worker below performs timing only; the latest angle is applied on
        the main thread at most about eight times per second.
        """
        self._rotation_pending_angle=float(angle_deg)
        if self._rotation_preview_scheduled:return
        self._rotation_preview_scheduled=True; generation=int(self._rotation_preview_generation)
        self._rotation_scheduled_generation=generation
        def worker():
            time.sleep(0.12)
            try:mainthread.run_async(lambda gen=generation:self._flush_rotation_preview(gen))
            except Exception:
                if self._rotation_scheduled_generation==generation:
                    self._rotation_preview_scheduled=False; self._rotation_scheduled_generation=None
        threading.Thread(target=worker,name="AssetRotatePreview",daemon=True).start()

    def _flush_rotation_preview(self,generation):
        # A stale worker must never clear a newer worker's scheduled flag.
        # This matters when a slider drag is immediately followed by +/-1°.
        if generation!=self._rotation_scheduled_generation:return
        self._rotation_preview_scheduled=False; self._rotation_scheduled_generation=None
        if generation!=self._rotation_preview_generation or not self.rotation_visible:return
        self._preview_selection_rotation(float(self._rotation_pending_angle),True)

    @staticmethod
    def _rotate_payload_angle(payload,angle_deg):
        if not payload:return None
        w=int(payload.get("w",0)); h=int(payload.get("h",0)); mask=payload.get("mask",[]); pix=payload.get("pixels",[])
        if w<=0 or h<=0:return None
        angle=float(angle_deg)%360.0
        quarter=int(round(angle/90.0))%4
        if abs(angle-quarter*90.0)<1.0e-7 or abs(angle-(quarter*90.0+360.0))<1.0e-7:
            out=copy.deepcopy(payload)
            for _i in range(quarter):out=AssetEditorUI._transform_payload(out,"rotate")
            return out
        rad=math.radians(angle); cs=math.cos(rad); sn=math.sin(rad)
        nw=max(1,int(math.ceil(abs((w-1)*cs)+abs((h-1)*sn)-1.0e-9))+1)
        nh=max(1,int(math.ceil(abs((w-1)*sn)+abs((h-1)*cs)-1.0e-9))+1)
        out_mask=[[False for _x in range(nw)] for _y in range(nh)]
        out_pix=[[TRANSPARENT for _x in range(nw)] for _y in range(nh)]
        scx=(w-1)*0.5; scy=(h-1)*0.5; dcx=(nw-1)*0.5; dcy=(nh-1)*0.5
        # Inverse mapping fills the destination without the pinholes produced
        # by forward-only rotation of sparse pixel art.
        for ty in range(nh):
            ry=float(ty)-dcy
            for tx in range(nw):
                rx=float(tx)-dcx
                sx=int(round(scx+rx*cs+ry*sn)); sy=int(round(scy-rx*sn+ry*cs))
                if not (0<=sx<w and 0<=sy<h):continue
                if sy>=len(mask) or sx>=len(mask[sy]) or not mask[sy][sx]:continue
                out_mask[ty][tx]=True
                out_pix[ty][tx]=str(pix[sy][sx]) if sy<len(pix) and sx<len(pix[sy]) else TRANSPARENT
        # Preserve isolated one-pixel details even when no inverse sample lands
        # on them at a shallow angle.
        for sy in range(h):
            for sx in range(w):
                if sy>=len(mask) or sx>=len(mask[sy]) or not mask[sy][sx]:continue
                rx=float(sx)-scx; ry=float(sy)-scy
                tx=int(round(dcx+rx*cs-ry*sn)); ty=int(round(dcy+rx*sn+ry*cs))
                if 0<=tx<nw and 0<=ty<nh:
                    out_mask[ty][tx]=True
                    out_pix[ty][tx]=str(pix[sy][sx]) if sy<len(pix) and sx<len(pix[sy]) else TRANSPARENT
        return {"w":nw,"h":nh,"mask":out_mask,"pixels":out_pix}

    def _compose_rotation_snapshot(self,angle_deg):
        if self._rotation_original_pixels is None or self._rotation_source_payload is None or self._rotation_source_box is None:
            return None,None
        out=self._rotate_payload_angle(self._rotation_source_payload,angle_deg)
        if not out:return copy.deepcopy(self._rotation_original_pixels),set(self._rotation_original_selection)
        canvas=copy.deepcopy(self._rotation_original_pixels)
        for x,y in self._rotation_original_selection:
            if 0<=y<self.canvas_size and 0<=x<self.canvas_size:canvas[y][x]=TRANSPARENT
        x0,y0,x1,y1=self._rotation_source_box; cx=(x0+x1)*0.5; cy=(y0+y1)*0.5
        ox=int(round(cx-(int(out["w"])-1)*0.5)); oy=int(round(cy-(int(out["h"])-1)*0.5))
        new_selection=set(); mask=out.get("mask",[]); pix=out.get("pixels",[])
        for yy in range(int(out["h"])):
            for xx in range(int(out["w"])):
                if yy>=len(mask) or xx>=len(mask[yy]) or not mask[yy][xx]:continue
                tx=ox+xx; ty=oy+yy
                if not (0<=tx<self.canvas_size and 0<=ty<self.canvas_size):continue
                raw=str(pix[yy][xx]) if yy<len(pix) and xx<len(pix[yy]) else TRANSPARENT
                if not (self.selection_transparent_mode and raw.upper().endswith("00")):canvas[ty][tx]=raw
                new_selection.add((tx,ty))
        return canvas,new_selection

    def _preview_selection_rotation(self,angle_deg,force=False):
        if not self.rotation_visible:return
        self._rotation_last_preview=time.monotonic()
        canvas,selection=self._compose_rotation_snapshot(angle_deg)
        if canvas is None:return
        old_pixels=self.pixels; old_selection=set(self.selection)
        self.pixels=canvas; self.selection=selection; self._reset_selection_gesture()
        # Compare in Python and cross the UIKit bridge only for cells whose
        # colour actually changed. Selection boundary is one retained gizmo.
        grid=self._display_grid_size(); vx=int(self.canvas_view_x); vy=int(self.canvas_view_y)
        def boundary_cells(cells):
            cells=set(cells)
            return {p for p in cells if any((p[0]+dx,p[1]+dy) not in cells for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)))}
        contour=boundary_cells(old_selection)|boundary_cells(selection)
        for y in range(vy,min(self.canvas_size,vy+grid)):
            for x in range(vx,min(self.canvas_size,vx+grid)):
                try:changed=str(old_pixels[y][x])!=str(canvas[y][x])
                except Exception:changed=True
                if changed or (x,y) in contour:self._refresh_one(x,y)
        # The main canvas is the authoritative live preview. The small VFX
        # stage refreshes once on Apply, avoiding large temporary composites
        # while the rotation slider is still moving.
        self._update_selection_outline()
        self.labels["status"].text=("旋轉預覽：%+.1f°｜按『套用旋轉』才會寫入"%float(angle_deg)).replace(".0°","°")

    def close_rotation_editor(self,apply_rotation=False):
        if not self.rotation_visible:
            if self.rotation_overlay is not None:self.rotation_overlay.hidden=True
            return
        angle=float(self.rotation_angle)
        self._rotation_preview_generation+=1; self._rotation_preview_scheduled=False
        self._rotation_scheduled_generation=None
        original_pixels=copy.deepcopy(self._rotation_original_pixels) if self._rotation_original_pixels is not None else copy.deepcopy(self.pixels)
        original_selection=set(self._rotation_original_selection)
        original_dirty=bool(self._rotation_original_dirty)
        self.pixels=original_pixels; self.selection=original_selection; self._reset_selection_gesture()
        if apply_rotation and abs(angle)>1.0e-7:
            self._push_undo()
            canvas,selection=self._compose_rotation_snapshot(angle)
            if canvas is not None:self.pixels=canvas; self.selection=selection
            self.dirty=True; self._mark_current_effect_authored()
            message=("已套用選取框旋轉：%+.1f°｜中心固定、最近鄰像素"%angle).replace(".0°","°")
        else:
            self.dirty=original_dirty
            message="已取消旋轉，原像素未改動" if not apply_rotation else "角度為 0°，原像素未改動"
        self.rotation_visible=False; self.rotation_overlay.hidden=True
        self._rotation_source_payload=None; self._rotation_source_box=None; self._rotation_original_pixels=None; self._rotation_original_selection=set()
        self.refresh_pixels(); self.refresh_labels(); self.labels["status"].text=message

    def _build_asset_creator(self):
        self.asset_map_role="solid"
        self.asset_creator_edit_id=None
        overlay=ui.View(); overlay.background_color=ui.Color.rgb(0.0,0.0,0.0,0.52); overlay.hidden=True
        try:overlay.user_interaction_enabled=True
        except Exception:pass
        self.root.add_subview(overlay); self.asset_creator_overlay=overlay
        dismiss=ui.Button(title=" "); dismiss.action=lambda _s:self.close_asset_creator()
        try:dismiss.background_color=ui.Color.rgb(0,0,0,0.01); dismiss.title_color=ui.Color.rgb(0,0,0,0)
        except Exception:pass
        overlay.add_subview(dismiss); self.asset_creator_dismiss=dismiss
        panel=ui.View(); panel.background_color=color("#263848"); panel.corner_radius=10
        overlay.add_subview(panel); self.asset_creator_panel=panel
        title=ui.Label("新增素材｜先選分類，再設定 ID 與名稱")
        title.text_color=WHITE; title.text_alignment=_center(); title.font=ui.Font.bold_system_font_of_size(12)
        panel.add_subview(title); self.asset_creator_title=title
        cat_prev=ui.Button(title="◀"); cat_prev.background_color=DARK2; cat_prev.title_color=WHITE; cat_prev.corner_radius=6; cat_prev.action=lambda _s:self.cycle_asset_creator_category(-1)
        cat=ui.Button(title="分類：—"); cat.background_color=BLUE; cat.title_color=WHITE; cat.corner_radius=6; cat.action=lambda _s:self.cycle_asset_creator_category(1)
        cat_next=ui.Button(title="▶"); cat_next.background_color=DARK2; cat_next.title_color=WHITE; cat_next.corner_radius=6; cat_next.action=lambda _s:self.cycle_asset_creator_category(1)
        for v in (cat_prev,cat,cat_next):panel.add_subview(v)
        self.asset_creator_category_prev=cat_prev; self.asset_creator_category_button=cat; self.asset_creator_category_next=cat_next; self.asset_creator_category_index=0
        aid=ui.TextField(); aid.placeholder="素材 ID：可填 rainforest_branches 或 tile.rainforest_branches"; aid.background_color=WHITE; aid.text_color=DARK
        panel.add_subview(aid); self.asset_id_field=aid
        name=ui.TextField(); name.placeholder="顯示名稱，例如 洞穴獸"; name.background_color=WHITE; name.text_color=DARK
        panel.add_subview(name); self.asset_name_field=name
        tag=ui.TextField(); tag.placeholder="地圖標籤，例如 建築／機關／礦物（圖塊素材必填）"; tag.background_color=WHITE; tag.text_color=DARK
        panel.add_subview(tag); self.asset_tag_field=tag
        role=ui.Button(title="碰撞：實體"); role.background_color=BLUE; role.title_color=WHITE; role.corner_radius=6; role.action=lambda _s:self.cycle_asset_map_role()
        panel.add_subview(role); self.asset_role_button=role
        ok=ui.Button(title="建立"); ok.background_color=GREEN; ok.title_color=WHITE; ok.corner_radius=6; ok.action=lambda _s:self.create_new_asset()
        cancel=ui.Button(title="取消"); cancel.background_color=DARK2; cancel.title_color=WHITE; cancel.corner_radius=6; cancel.action=lambda _s:self.close_asset_creator()
        panel.add_subview(ok); panel.add_subview(cancel); self.asset_creator_ok=ok; self.asset_creator_cancel=cancel
        note=ui.Label("新增素材會保存分類與中文名稱；存檔後地圖編輯器重新同步即可在同類別使用。生物同步到生物放置工具；其他素材可作為地圖貼圖，圖塊另保留碰撞設定。")
        note.text_color=WHITE; note.text_alignment=_center(); note.number_of_lines=3; note.font=ui.Font.system_font_of_size(9)
        panel.add_subview(note); self.asset_creator_note=note

    def _layout_asset_creator(self):
        if self.asset_creator_overlay is None:return
        w=float(self.landscape_w or self.root.width or 1); h=float(self.landscape_h or self.root.height or 1)
        self.asset_creator_overlay.frame=(0,0,w,h); self.asset_creator_dismiss.frame=(0,0,w,h)
        pw=min(620.0,max(390.0,w-100)); ph=min(382.0,max(314.0,h-30.0)); x=(w-pw)*0.5; y=max(8.0,(h-ph)*0.5)
        self.asset_creator_panel.frame=(x,y,pw,ph); self.asset_creator_title.frame=(14,7,pw-28,26)
        self.asset_creator_category_prev.frame=(18,38,44,34); self.asset_creator_category_button.frame=(68,38,pw-136,34); self.asset_creator_category_next.frame=(pw-62,38,44,34)
        self.asset_id_field.frame=(18,78,pw-36,34); self.asset_name_field.frame=(18,118,pw-36,34)
        selected=self._asset_creator_category_id()
        is_tile=(selected=="tile")
        self.asset_tag_field.hidden=not is_tile; self.asset_role_button.hidden=not is_tile
        if is_tile:
            self.asset_tag_field.frame=(18,158,pw-190,34); self.asset_role_button.frame=(pw-164,158,146,34); button_y=202.0; note_y=246.0
        else:
            button_y=164.0; note_y=208.0
        bw=(pw-48)/2; self.asset_creator_cancel.frame=(18,button_y,bw,34); self.asset_creator_ok.frame=(30+bw,button_y,bw,34)
        self.asset_creator_note.frame=(18,note_y,pw-36,max(56.0,ph-note_y-10.0))
        try:
            self.asset_creator_overlay.bring_subview_to_front(self.asset_creator_panel)
            self.root.bring_subview_to_front(self.asset_creator_overlay)
        except Exception:pass

    def _asset_creator_category_id(self):
        if getattr(self, 'asset_creator_edit_id', None): return 'tile'
        rows=list(self.categories) or list(CATEGORIES)
        if not rows:return self.category[0]
        i=int(getattr(self,"asset_creator_category_index",0))%len(rows)
        return rows[i][0]

    def _refresh_asset_creator_category(self):
        rows=list(self.categories) or list(CATEGORIES)
        if not rows:return
        i=int(getattr(self,"asset_creator_category_index",0))%len(rows); self.asset_creator_category_index=i
        cid,label,_folder=rows[i]
        if self.asset_creator_category_button is not None:self.asset_creator_category_button.title="分類：%s（點擊切換）"%label
        self._layout_asset_creator()

    def cycle_asset_creator_category(self,delta=1):
        if getattr(self, 'asset_creator_edit_id', None): return
        rows=list(self.categories) or list(CATEGORIES)
        if not rows:return
        self.asset_creator_category_index=(int(getattr(self,"asset_creator_category_index",0))+int(delta))%len(rows)
        self._refresh_asset_creator_category()

    def cycle_asset_map_role(self):
        roles=TILE_MAP_ROLES
        ids=[r[0] for r in roles]
        try:i=(ids.index(str(self.asset_map_role))+1)%len(ids)
        except Exception:i=0
        self.asset_map_role=roles[i][0]
        self.asset_role_button.title=roles[i][1]
        self._show_tile_role_note()

    def _show_tile_role_note(self):
        text = role_description(self.asset_map_role)
        if self.asset_creator_edit_id:
            text += "\n套用只修改碰撞／攀爬設定；原圖、動畫、ID、名稱、地圖位置不變。遊戲重新載入地圖後生效。"
        else:
            text += "\n素材 ID 可含所選分類前綴。地圖編輯器按 ↻ 重新同步；可從『圖塊屬性』修改既有圖塊。"
        self.asset_creator_note.text=text
        self.asset_creator_note.text_color=WHITE

    def _set_property_fields_locked(self, locked):
        for view in (self.asset_id_field, self.asset_name_field, self.asset_tag_field):
            view.user_interaction_enabled=not locked
        for button in (self.asset_creator_category_prev, self.asset_creator_category_next, self.asset_creator_category_button):
            button.enabled=not locked

    def open_tile_properties(self):
        try:
            props=self.model.get_tile_map_properties(self.asset_id)
        except Exception as exc:
            self.labels['status'].text=str(exc)
            return
        self._stop_animation_preview(); self._commit_current_frame()
        self.asset_creator_edit_id=str(self.asset_id)
        self.asset_map_role=props['role']
        self.asset_id_field.text=self.asset_id
        self.asset_name_field.text=str(props.get('name') or self.model.display_name(self.asset_id))
        self.asset_tag_field.text=str(props.get('tag') or '')
        self.asset_role_button.title=role_label(self.asset_map_role)
        self.asset_creator_title.text='圖塊屬性｜修改既有圖塊，不必重畫'
        self.asset_creator_ok.title='套用設定'
        self.asset_creator_category_button.title='分類：圖塊（既有素材）'
        self._set_property_fields_locked(True)
        self._show_tile_role_note()
        self.asset_creator_visible=True; self.asset_creator_overlay.hidden=False
        self._set_canvas_hidden_for_asset_modal(True)
        self._layout_asset_creator()

    def apply_tile_properties(self):
        aid=self.asset_creator_edit_id
        try:
            self.model.set_tile_map_role(aid, self.asset_map_role)
        except Exception as exc:
            self.asset_creator_note.text='設定未套用：'+str(exc)
            self.asset_creator_note.text_color=RED
            return
        label=role_label(self.asset_map_role)
        self.close_asset_creator()
        self.refresh_labels()
        self.labels['status'].text='已設定 %s：%s｜地圖按 ↻；遊戲重新載入後生效。原圖未更動。'%(aid,label)

    def _set_canvas_hidden_for_asset_modal(self, hidden):
        # Pyto/iPad can reorder thousands of pixel sibling views during a
        # Stage Manager resize even after bring_subview_to_front(). Hiding the
        # expensive canvas while this short modal is open makes z-order
        # deterministic and also removes accidental paint touches.
        for v in getattr(self,"pixel_views",()):
            try:v.hidden=bool(hidden)
            except Exception:pass
        for v in (getattr(self,"canvas_bg",None),getattr(self,"overlay",None)):
            if v is not None:
                try:v.hidden=bool(hidden)
                except Exception:pass
        for v in tuple(getattr(self,"selection_outline_edges",()))+tuple(getattr(self,"selection_outline_handles",())):
            try:v.hidden=bool(hidden) or not bool(self.selection)
            except Exception:pass

    def open_asset_creator(self):
        self.asset_creator_edit_id=None
        self.asset_creator_title.text='新增素材｜先選分類，再設定 ID 與名稱'
        self.asset_creator_ok.title='建立'
        self._set_property_fields_locked(False)
        self._stop_animation_preview(); self._commit_current_frame()
        self.asset_creator_visible=True; self.asset_creator_overlay.hidden=False
        self.asset_map_role="solid"
        try:
            ids=[r[0] for r in self.categories]; self.asset_creator_category_index=ids.index(self.category[0]) if self.category[0] in ids else 0
            self.asset_id_field.text=""; self.asset_name_field.text=""; self.asset_tag_field.text=""
            self.asset_role_button.title="碰撞：實體"
        except Exception:pass
        self._set_canvas_hidden_for_asset_modal(True)
        self._refresh_asset_creator_category(); self._show_tile_role_note()
        self.labels["status"].text="新增素材：先確認分類；建立／存檔後可同步到地圖編輯器"

    def close_asset_creator(self):
        self.asset_creator_edit_id=None
        self.asset_creator_visible=False
        if self.asset_creator_overlay is not None:self.asset_creator_overlay.hidden=True
        self._set_canvas_hidden_for_asset_modal(False)

    def create_new_asset(self):
        if getattr(self, 'asset_creator_edit_id', None):
            return self.apply_tile_properties()
        raw_id=getattr(self.asset_id_field,"text","") if self.asset_id_field is not None else ""
        name=getattr(self.asset_name_field,"text","") if self.asset_name_field is not None else ""
        tag=getattr(self.asset_tag_field,"text","") if getattr(self,"asset_tag_field",None) is not None else ""
        category=self._asset_creator_category_id()
        if category=="tile" and not str(tag or "").strip():
            self.asset_creator_note.text="請先輸入地圖標籤（例如 雨林巨木）"
            self.asset_creator_note.text_color=RED
            return
        try:
            aid=self.model.create_pixel_asset(category,raw_id,name,self.canvas_size,map_tag=tag,map_role=self.asset_map_role,map_color=str(getattr(self,"selected_color","#7D8790"))[:7])
            ids=[r[0] for r in self.categories]
            if category in ids:self.category_index=ids.index(category)
            rows=self.asset_ids
            self.asset_index=rows.index(aid) if aid in rows else max(0,len(rows)-1)
            self.close_asset_creator(); self._load_selected()
            self.labels["status"].text="已新增素材：%s｜已使用你輸入的 ID；地圖編輯器按 ↻ 即可同步"%aid
        except Exception as exc:
            self.asset_creator_note.text="新增素材失敗："+str(exc)
            self.asset_creator_note.text_color=RED
            self.labels["status"].text=self.asset_creator_note.text

    def _build_state_creator(self):
        overlay=ui.View(); overlay.background_color=ui.Color.rgb(0.0,0.0,0.0,0.52); overlay.hidden=True
        try:overlay.user_interaction_enabled=True
        except Exception:pass
        self.root.add_subview(overlay); self.state_creator_overlay=overlay

        dismiss=ui.Button(title=" ")
        try:
            dismiss.background_color=ui.Color.rgb(0.0,0.0,0.0,0.01)
            dismiss.title_color=ui.Color.rgb(0.0,0.0,0.0,0.0)
        except Exception:pass
        dismiss.action=lambda _s:self.close_state_creator()
        overlay.add_subview(dismiss); self.state_creator_dismiss=dismiss

        panel=ui.View(); panel.background_color=color("#263848"); panel.corner_radius=10
        try:panel.border_width=1; panel.border_color=color("#71808C")
        except Exception:pass
        overlay.add_subview(panel); self.state_creator_panel=panel

        title=ui.Label("新增動畫狀態｜輸入狀態名稱")
        title.text_color=WHITE; title.text_alignment=_center(); title.font=ui.Font.bold_system_font_of_size(12)
        panel.add_subview(title); self.state_creator_title=title

        field=ui.TextField(); field.placeholder="例如：俯衝攻擊 / roar / sleep"
        field.background_color=WHITE; field.text_color=DARK
        panel.add_subview(field); self.state_name_field=field

        ok=ui.Button(title="新增")
        ok.background_color=GREEN; ok.title_color=WHITE; ok.corner_radius=6; ok.font=ui.Font.bold_system_font_of_size(11)
        ok.action=lambda _s:self.create_custom_animation_state()
        panel.add_subview(ok); self.state_creator_ok=ok

        cancel=ui.Button(title="取消")
        cancel.background_color=DARK2; cancel.title_color=WHITE; cancel.corner_radius=6; cancel.font=ui.Font.bold_system_font_of_size(11)
        cancel.action=lambda _s:self.close_state_creator()
        panel.add_subview(cancel); self.state_creator_cancel=cancel

        note=ui.Label("新增後會成為獨立動畫狀態。生物狀態可再到『動作編輯器 → 生物 AI』設定功能、效果與觸發距離。")
        note.text_color=WHITE; note.text_alignment=_center(); note.number_of_lines=3; note.font=ui.Font.system_font_of_size(9)
        panel.add_subview(note); self.state_creator_note=note

    @staticmethod
    def _normalize_custom_state_name(value):
        value=str(value or "").strip()
        value="_".join(value.split())
        # JSON/runtime safely support Unicode names; only path/control-like
        # separators are replaced so the visible name remains what the artist typed.
        for bad in ("/","\\",":",";","|","\n","\r","\t"):
            value=value.replace(bad,"_")
        while "__" in value:value=value.replace("__","_")
        return value.strip("_ .")[:32]

    def _layout_state_creator(self):
        if self.state_creator_overlay is None:return
        w=float(self.landscape_w or self.root.width or 1.0); h=float(self.landscape_h or self.root.height or 1.0)
        self.state_creator_overlay.frame=(0,0,w,h); self.state_creator_dismiss.frame=(0,0,w,h)
        pw=min(520.0,max(390.0,w-180.0)); ph=190.0
        x=(w-pw)*0.5; y=(h-ph)*0.5
        self.state_creator_panel.frame=(x,y,pw,ph)
        self.state_creator_title.frame=(16,10,pw-32,28)
        self.state_name_field.frame=(18,48,pw-36,34)
        bw=(pw-48.0)/2.0
        self.state_creator_cancel.frame=(18,92,bw,32)
        self.state_creator_ok.frame=(30+bw,92,bw,32)
        self.state_creator_note.frame=(18,132,pw-36,46)
        try:
            self.state_creator_overlay.bring_subview_to_front(self.state_creator_panel)
            self.root.bring_subview_to_front(self.state_creator_overlay)
        except Exception:pass

    def open_state_creator(self):
        if not self.animation_mode or not self._animation_supported():return
        self._stop_animation_preview(); self._commit_current_frame()
        self.state_creator_visible=True; self.state_creator_overlay.hidden=False
        try:self.state_name_field.text=""
        except Exception:pass
        self._layout_state_creator()
        self.labels["status"].text="輸入新狀態名稱；新增後可直接畫幀，生物狀態可交給 AI 動作系統調用"

    def close_state_creator(self):
        self.state_creator_visible=False
        if self.state_creator_overlay is not None:self.state_creator_overlay.hidden=True

    def create_custom_animation_state(self):
        name=self._normalize_custom_state_name(getattr(self.state_name_field,"text","") if self.state_name_field is not None else "")
        if not name:
            self.labels["status"].text="狀態名稱不能空白"
            return
        if name.lower()=="base":
            self.labels["status"].text="Base 是保留名稱，請改用其他狀態名稱"
            return
        self._commit_current_frame()
        existed=name in self.animations
        self.animation_state=name
        raw=self._ensure_animation_state(name)
        self.animation_mode=True; self.animation_frame_index=0; self.pixels=copy.deepcopy(raw["frames"][0])
        self.undo_stack=[]; self.selection=set(); self.dirty=True
        self._sync_active_canvas(recenter=True); self.close_state_creator(); self.refresh_pixels(); self.refresh_labels()
        self.labels["status"].text=("已切換既有狀態：" if existed else "已新增動畫狀態：")+name+"｜存檔後可到動作編輯器綁定 AI 功能"

    def _rebuild_preview_views(self):
        """Match the preview cell pool to the current logical canvas size.

        This is intentionally rebuilt only when the user changes/loads a
        different resolution, never every frame.  32×32 is therefore at most
        1024 tiny passive views and remains predictable on Pyto/iPhone.
        """
        target=min(max(1,int(self.canvas_size)),PREVIEW_MAX_GRID)
        count=target*target
        current=len(self.preview_cells)
        if current < count:
            for _i in range(count-current):
                v=ui.View(); v.user_interaction_enabled=False; v.border_width=0
                try:v.hidden=True
                except Exception:pass
                self.root.add_subview(v); self.preview_cells.append(v)
        elif current > count:
            extra=self.preview_cells[count:]
            self.preview_cells=self.preview_cells[:count]
            for v in extra:
                try:v.remove_from_superview()
                except Exception:
                    try:v.hidden=True
                    except Exception:pass
        self.preview_grid=target
        self._preview_color_cache=[]

    def _clear_pixel_views(self):
        for v in self.pixel_views:
            try:v.remove_from_superview()
            except Exception:
                try:v.hidden=True
                except Exception:pass
        self.pixel_views=[]

    def _rebuild_pixel_views(self):
        """Resize the native cell pool instead of destroying/recreating it.

        FIX2: category/object changes keep the exact same cells.  The old editor
        removed and re-added 256/576/1024 PytoUI Views on every selection, which
        is why tapping the top-left category button appeared to freeze on iPhone.
        """
        if self.metal_canvas is not None and self.metal_canvas.available:
            self._clear_pixel_views()
            return
        grid=self._display_grid_size(); count=grid*grid
        current=len(self.pixel_views)
        if current < count:
            for _i in range(count-current):
                v=ui.View(); v.user_interaction_enabled=False; v.border_width=0.25; v.border_color=color("#28323C")
                self.root.add_subview(v); self.pixel_views.append(v)
        elif current > count:
            extra=self.pixel_views[count:]
            self.pixel_views=self.pixel_views[:count]
            for v in extra:
                try:v.remove_from_superview()
                except Exception:
                    try:v.hidden=True
                    except Exception:pass
        try:
            self.root.bring_subview_to_front(self.overlay)
            for edge in self.selection_outline_edges:self.overlay.bring_subview_to_front(edge)
            for handle in self.selection_outline_handles:self.overlay.bring_subview_to_front(handle)
        except Exception:pass

    def _load_selected(self, force_builtin=False):
        if self._loading:
            return
        self._loading=True
        try:
            aid=self.asset_id
            if force_builtin:
                base=self.model.make_builtin_template(aid)
                data={"size":len(base),"pixels":base,"animations":(self.model._default_weapon_animations(aid,base) if str(aid).startswith("weapon.") else {}),"combat_bindings":(self.model._default_combat_bindings(aid) if str(aid).startswith("weapon.") else {}),"source":"builtin"}
            else:
                # FIX20: every asset reopens at its own saved/recommended logical
                # canvas. We no longer force the previously selected object's
                # canvas size onto the next asset, because that would silently
                # rescale detail density.
                data=self.model.load_pixel_asset(aid)
            old_size=int(self.canvas_size)
            self.asset_canvas_size=int(data["size"]); self.canvas_size=self.asset_canvas_size
            self.export_canvas_size=int(data.get("export_size",self.asset_canvas_size) or self.asset_canvas_size)
            self.export_scale=float(data.get("export_scale",float(self.export_canvas_size)/max(1,self.asset_canvas_size)) or 1.0)
            self.pixel_mapping=str(data.get("pixel_mapping","") or "")
            self.world_fit=str(data.get("world_fit","") or "")
            self.size_index=PIXEL_SIZES.index(self.canvas_size) if self.canvas_size in PIXEL_SIZES else 0
            self.canvas_size_direction=(-1 if self.canvas_size>=int(PIXEL_SIZES[-1]) else 1)
            self._native_export_override=None
            self._stop_animation_preview()
            if self.fx_motion_visible:
                self._stop_fx_motion_preview();self.fx_motion_visible=False
                if self.fx_motion_overlay is not None:self.fx_motion_overlay.hidden=True
            self.base_pixels=copy.deepcopy(data["pixels"]); self.animations=copy.deepcopy(data.get("animations",{}))
            self.combat_bindings=copy.deepcopy(data.get("combat_bindings",{}))
            self.animation_mode=False; self.animation_state=""; self.animation_frame_index=0
            self.pixels=copy.deepcopy(self.base_pixels)
            # FIX124: selecting an effect stays in Base/representative view.
            # The user explicitly enters a visibly different FX frame editor by
            # tapping 「逐幀編輯」. This removes the ambiguous FIX123 state where
            # the label could already say 1/N yet the screen still looked unchanged.
            self.fx_editor_active=False
            try:
                if self.fx_editor_panel is not None:self.fx_editor_panel.hidden=True
            except Exception:pass
            self.undo_stack=[]; self.dirty=False; self._last_painted=None
            self.selection=set(); self._reset_selection_gesture()
            self.canvas_zoom=1.0;self.canvas_view_x=0;self.canvas_view_y=0
            self._nav_pan_accum=[0.0,0.0]
            # Most selections keep the same size. Reuse native pixel cells; only
            # grow/shrink the pool when the actual canvas size changed.
            if len(self.pixel_views) != self._display_grid_size()*self._display_grid_size():
                self._rebuild_pixel_views()
            preview_target=min(self.canvas_size,PREVIEW_MAX_GRID)
            if self.preview_grid != preview_target or len(self.preview_cells) != preview_target*preview_target:
                self._rebuild_preview_views()
            self.refresh_pixels(); self.refresh_labels()
            try:self._layout()
            except Exception:pass
            if data.get("source")=="saved":
                self.labels["status"].text=(
                    f"已載入邏輯原稿 {self.canvas_size}×{self.canvas_size}｜輸出 {self.export_canvas_size}×{self.export_canvas_size}（{self.export_scale:.1f}× nearest）\n"
                    "畫布放大只改變檢視倍率；每個格子始終是一個真正的邏輯像素"
                )
            elif data.get("source")=="migrated_legacy":
                self.dirty=True
                self.labels["status"].text="已把舊版縮放素材轉成 FIX20 固定像素密度\n請按『存檔 PNG』寫入新版格式；動畫幀已同步轉換"
            else:
                self.labels["status"].text="已用固定像素密度匯入系統預設外觀\n1 格永遠 = %.1f 世界 px，可直接修改後存檔" % float(PIXEL_WORLD_SCALE)
        except (CanvasImportError, OSError, ValueError) as exc:
            self.labels["status"].text="匯入失敗，未改動原稿："+str(exc)
            raise
        finally:
            self._loading=False

    def change_category(self,d):
        if self._loading:return
        old=(self.category_index,self.asset_index)
        self.category_index=(self.category_index+int(d))%len(self.categories);self.asset_index=0
        try:self._load_selected()
        except (CanvasImportError,OSError,ValueError):self.category_index,self.asset_index=old

    def change_asset(self,d):
        if self._loading:return
        old=self.asset_index;rows=self.asset_ids;self.asset_index=(self.asset_index+int(d))%len(rows)
        try:self._load_selected()
        except (CanvasImportError,OSError,ValueError):self.asset_index=old

    @staticmethod
    def _opaque_pixel(value):
        raw=str(value or "").strip().upper().lstrip("#")
        if len(raw)==8:
            return raw[6:8]!="00"
        return bool(raw and raw not in ("00000000","TRANSPARENT"))

    def _grid_shrink_loss_count(self,grid,new_size,category):
        """Count authored non-transparent cells that a 1:1 canvas shrink would crop."""
        if not isinstance(grid,list) or not grid:return 0
        old_size=len(grid)
        if int(new_size)>=old_size:return 0
        shrunk=self.model.resize_canvas_pixels(grid,int(new_size),category)
        restored=self.model.resize_canvas_pixels(shrunk,old_size,category)
        loss=0
        for y,row in enumerate(grid):
            if not isinstance(row,list):continue
            back=restored[y] if y<len(restored) and isinstance(restored[y],list) else []
            for x,value in enumerate(row):
                if self._opaque_pixel(value) and (x>=len(back) or str(back[x])!=str(value)):
                    loss+=1
        return loss

    def _body_shrink_loss_count(self,new_size,category):
        loss=self._grid_shrink_loss_count(self.base_pixels,new_size,category)
        for state,raw in (self.animations or {}).items():
            if not isinstance(raw,dict):continue
            # Weapon VFX owns an independent centred workspace and must not be
            # cropped merely because the weapon-body canvas changes.
            if str(category)=="weapon" and str(state) in EFFECT_STATES:continue
            for frame in raw.get("frames",[]) or []:
                loss+=self._grid_shrink_loss_count(frame,new_size,category)
        return loss

    def _resize_body_animations_exact(self,new_size,category):
        out=copy.deepcopy(self.animations or {})
        for state,raw in out.items():
            if not isinstance(raw,dict):continue
            if str(category)=="weapon" and str(state) in EFFECT_STATES:continue
            raw["frames"]=[self.model.resize_canvas_pixels(frame,int(new_size),category) for frame in (raw.get("frames",[]) or []) if isinstance(frame,list)]
            if "canvas_size" in raw:raw["canvas_size"]=int(new_size)
        return out

    def change_size(self):
        # FIX118: tapping the toolbar button opens a direct chooser.  It never
        # cycles or silently changes the authored resolution.
        self.open_canvas_size_picker()

    def request_canvas_size(self,new_size):
        category=str(self.category[0]); is_vfx=bool(self._is_vfx_state())
        allowed=tuple(int(v) for v in (VFX_PIXEL_SIZES if is_vfx else PIXEL_SIZES))
        try:new=int(new_size)
        except Exception:return
        if new not in allowed:return
        self._commit_current_frame()
        old=int(self.canvas_size if is_vfx else self.asset_canvas_size)
        if new==old:
            self.labels["status"].text=f"畫布維持 {old}×{old}；沒有修改素材"
            self.close_canvas_size_picker(); return
        loss=0
        if new<old:
            if is_vfx:
                raw=self._current_animation(); frames=raw.get("frames",[]) if isinstance(raw,dict) else []
                loss=sum(self._grid_shrink_loss_count(frame,new,"weapon") for frame in frames if isinstance(frame,list))
            else:
                loss=self._body_shrink_loss_count(new,category)
        if loss>0:
            self.canvas_size_pending=new; self.canvas_size_pending_loss=int(loss)
            self._refresh_canvas_size_picker(True); self._layout_canvas_size_picker(); return
        self._apply_canvas_size(new,allow_crop=False); self.close_canvas_size_picker()

    def _apply_canvas_size(self,new,allow_crop=False):
        category=str(self.category[0]); is_vfx=bool(self._is_vfx_state()); new=int(new)
        old=int(self.canvas_size if is_vfx else self.asset_canvas_size)
        if new==old:return
        if is_vfx:
            raw=self._current_animation(); frames=raw.get("frames",[]) if isinstance(raw,dict) else []
            if new<old and not allow_crop:
                loss=sum(self._grid_shrink_loss_count(frame,new,"weapon") for frame in frames if isinstance(frame,list))
                if loss:return
            self._stop_animation_preview(); self._commit_current_frame(); self._push_state_undo()
            raw=self._current_animation(); frames=raw.get("frames",[]) if isinstance(raw,dict) else []
            raw["frames"]=[self.model.resize_canvas_pixels(frame,new,"weapon") for frame in frames]
            raw["canvas_size"]=new; raw["export_size"]=new; raw["export_scale"]=1.0
            self.canvas_size=new
            if new in PIXEL_SIZES:self.size_index=PIXEL_SIZES.index(new)
            frames=raw.get("frames",[])
            self.animation_frame_index=max(0,min(self.animation_frame_index,len(frames)-1)) if frames else 0
            self.pixels=copy.deepcopy(frames[self.animation_frame_index]) if frames else [[TRANSPARENT for _x in range(new)] for _y in range(new)]
            self.selection=set(); self._reset_selection_gesture(); self._center_canvas_origin()
            self._rebuild_pixel_views(); self._rebuild_preview_views(); self._mark_current_effect_authored()
            self.dirty=True; self.refresh_pixels(); self.refresh_labels()
            try:self._layout()
            except Exception:pass
            action="裁切縮小" if new<old and allow_crop else ("縮小" if new<old else "放大")
            self.labels["status"].text=(f"VFX 畫布已{action}：{old}×{old} → {new}×{new}｜原稿與輸出同步為原生 1:1\n"
                                      "畫面顯示倍率沒有改變；可用『全圖』或雙指縮放調整檢視")
            return
        if new<old and not allow_crop:
            if self._body_shrink_loss_count(new,category):return
        self.size_index=PIXEL_SIZES.index(new)
        self._stop_animation_preview(); self._commit_current_frame(); self._push_full_undo()
        self.base_pixels=self.model.resize_canvas_pixels(self.base_pixels,new,category)
        self.animations=self._resize_body_animations_exact(new,category)
        self.asset_canvas_size=new; self.canvas_size=new
        self.export_canvas_size=new; self.export_scale=1.0; self._native_export_override=new
        if self.animation_mode:
            frames=self._current_animation().get("frames",[]) if self._current_animation() else []
            self.animation_frame_index=max(0,min(self.animation_frame_index,len(frames)-1)) if frames else 0
            self.pixels=copy.deepcopy(frames[self.animation_frame_index]) if frames else copy.deepcopy(self.base_pixels)
        else:self.pixels=copy.deepcopy(self.base_pixels)
        self.selection=set(); self._reset_selection_gesture(); self._center_canvas_origin(); self._rebuild_pixel_views(); self._rebuild_preview_views()
        self.refresh_pixels(); self.refresh_labels(); self.dirty=True
        try:self._layout()
        except Exception:pass
        action="裁切縮小" if new<old and allow_crop else ("縮小" if new<old else "放大")
        self.labels["status"].text=(f"素材畫布已{action}：{old}×{old} → {new}×{new}｜原稿與輸出同步為原生 1:1\n"
                                  "畫面顯示倍率沒有改變；可用『全圖』或雙指縮放調整檢視")

    def set_tool(self,tool):
        self.tool=str(tool)
        self._reset_selection_gesture()
        if self.tool in ("select_rect","select_brush"):
            # Enter selection-ready mode by default. Pinch remains a separate
            # two-finger recognizer; the gesture button can temporarily switch
            # one-finger drag back to canvas pan without changing tools.
            self.selection_nav_mode=False
        self._sync_overlay_drag_mode()
        self.refresh_labels()
        if self.tool in ("select_rect","select_brush"):
            self.buttons["gesture_mode"].title="拖曳：框選"
            self.labels["status"].text="單指拖曳＝建立／移動選區；雙指縮放；要平移畫布按『拖曳：框選』切換"
        else:
            self.buttons["gesture_mode"].title="拖曳：連畫" if self.canvas_drag_mode=="paint" else "拖曳：平移"

    def choose_color(self,c):
        self.selected_color=str(c)
        if self.hex_field is not None:self.hex_field.text=self.selected_color[:7]
        self.tool="pen"; self._sync_overlay_drag_mode(); self.refresh_labels()

    def apply_hex(self):
        if self.hex_field is None:return
        try:self.selected_color=_normalize_hex(self.hex_field.text); self.tool="pen"; self._sync_overlay_drag_mode(); self.refresh_labels(); self.labels["status"].text="目前顏色："+self.selected_color
        except Exception as exc:self.labels["status"].text=str(exc)

    def _refresh_current_color_swatch(self):
        b=self.buttons.get("current_color")
        if b is None:return
        raw=str(self.selected_color or "#000000FF").upper()
        transparent=raw.endswith("00")
        cc="#18212A" if transparent else raw[:7]
        try:b.background_color=color(cc)
        except Exception:pass
        if transparent:
            b.title="透明"
            try:b.title_color=WHITE
            except Exception:pass
        else:
            b.title="目前色"
            try:
                q=cc.lstrip("#"); r=int(q[0:2],16); g=int(q[2:4],16); bl=int(q[4:6],16)
                lum=0.299*r+0.587*g+0.114*bl
                b.title_color=DARK if lum>=170 else WHITE
            except Exception:
                try:b.title_color=WHITE
                except Exception:pass
        try:
            if self.color_picker_current is not None:
                self.color_picker_current.background_color=color(cc)
        except Exception:pass

    def _build_color_picker(self):
        """FIX55 HSV picker with continuous drag selection.

        The old modal was a fixed swatch matrix.  This version uses a
        saturation/value field plus a hue strip; both are touch overlays, so a
        finger can slide continuously instead of landing on predefined colours.
        The visible cells are only a lightweight guide, not the selectable
        colour resolution.
        """
        overlay=ui.View(); overlay.background_color=ui.Color.rgb(0.0,0.0,0.0,0.46); overlay.hidden=True
        try:overlay.user_interaction_enabled=True
        except Exception:pass
        self.root.add_subview(overlay); self.color_picker_overlay=overlay
        dismiss=ui.Button(title=" ")
        try:dismiss.background_color=ui.Color.rgb(0,0,0,0.01); dismiss.title_color=ui.Color.rgb(0,0,0,0)
        except Exception:pass
        dismiss.action=lambda _s:self.close_color_picker(); overlay.add_subview(dismiss); self.color_picker_dismiss=dismiss

        panel=ui.View(); panel.background_color=color("#263848"); panel.corner_radius=10
        try:panel.border_width=1; panel.border_color=color("#71808C")
        except Exception:pass
        overlay.add_subview(panel); self.color_picker_panel=panel

        title=ui.Label("滑動選色｜上方：飽和度/明度　下方：色相")
        title.text_color=WHITE; title.text_alignment=_center(); title.font=ui.Font.bold_system_font_of_size(12)
        panel.add_subview(title); self.color_picker_title=title
        close=ui.Button(title="×"); close.background_color=RED; close.title_color=WHITE; close.corner_radius=6; close.font=ui.Font.bold_system_font_of_size(15)
        close.action=lambda _s:self.close_color_picker(); panel.add_subview(close); self.color_picker_close=close

        current=ui.View(); current.background_color=color(self.selected_color[:7]); current.corner_radius=6
        panel.add_subview(current); self.color_picker_current=current
        confirm=ui.Button(title="使用這個顏色"); confirm.background_color=BLUE; confirm.title_color=WHITE; confirm.corner_radius=6
        confirm.font=ui.Font.bold_system_font_of_size(10); confirm.action=lambda _s:self.confirm_visual_color()
        panel.add_subview(confirm); self.color_picker_confirm=confirm

        # 10x8 guide cells; touch coordinates still resolve the full HSV range.
        for _i in range(80):
            v=ui.View(); v.user_interaction_enabled=False; v.border_width=0
            panel.add_subview(v); self.color_sv_cells.append(v)
        for _i in range(24):
            v=ui.View(); v.user_interaction_enabled=False; v.border_width=0
            panel.add_subview(v); self.color_hue_cells.append(v)
        self.color_sv_overlay=PixelPaintOverlay(self._color_sv_gesture); panel.add_subview(self.color_sv_overlay)
        self.color_hue_overlay=PixelPaintOverlay(self._color_hue_gesture); panel.add_subview(self.color_hue_overlay)
        self._refresh_sv_guide()

    def _selected_hex_to_hsv(self):
        q=str(self.selected_color or "#5C8D45FF").lstrip("#")[:6]
        try:r=int(q[0:2],16)/255.0; g=int(q[2:4],16)/255.0; b=int(q[4:6],16)/255.0
        except Exception:r,g,b=(0.36,0.55,0.27)
        return colorsys.rgb_to_hsv(r,g,b)

    def _refresh_sv_guide(self):
        cols=10; rows=8
        for i,v in enumerate(self.color_sv_cells):
            yy=i//cols; xx=i%cols
            sat=xx/max(1,cols-1); val=1.0-yy/max(1,rows-1)
            r,g,b=colorsys.hsv_to_rgb(self.color_picker_hue,sat,val)
            try:v.background_color=color("#%02X%02X%02X"%(round(r*255),round(g*255),round(b*255)))
            except Exception:pass
        for i,v in enumerate(self.color_hue_cells):
            h=i/max(1,len(self.color_hue_cells)-1); r,g,b=colorsys.hsv_to_rgb(h,1.0,1.0)
            try:v.background_color=color("#%02X%02X%02X"%(round(r*255),round(g*255),round(b*255)))
            except Exception:pass

    def _apply_picker_hsv(self):
        r,g,b=colorsys.hsv_to_rgb(self.color_picker_hue,self.color_picker_sat,self.color_picker_val)
        cc="#%02X%02X%02XFF"%(round(r*255),round(g*255),round(b*255))
        self.selected_color=cc
        if self.hex_field is not None:self.hex_field.text=cc[:7]
        try:self.color_picker_current.background_color=color(cc[:7])
        except Exception:pass
        self._refresh_current_color_swatch()

    def _color_sv_gesture(self,x,y,kind,state):
        w=max(1.0,float(self.color_sv_overlay.width)); h=max(1.0,float(self.color_sv_overlay.height))
        self.color_picker_sat=max(0.0,min(1.0,float(x)/w)); self.color_picker_val=max(0.0,min(1.0,1.0-float(y)/h))
        self._apply_picker_hsv()
        self.labels["status"].text="滑動選色中｜S %.0f%%　V %.0f%%"%(self.color_picker_sat*100.0,self.color_picker_val*100.0)

    def _color_hue_gesture(self,x,y,kind,state):
        w=max(1.0,float(self.color_hue_overlay.width)); self.color_picker_hue=max(0.0,min(1.0,float(x)/w))
        self._apply_picker_hsv()
        # Refresh the guide only at gesture boundaries to avoid hammering UIKit
        # with 80 colour assignments on every finger-move callback.
        if kind=="tap" or int(state) in (1,3,4,5):self._refresh_sv_guide()
        self.labels["status"].text="滑動色相中｜H %.0f°"%(self.color_picker_hue*360.0)

    def _layout_color_picker(self):
        if self.color_picker_overlay is None or self.color_picker_panel is None:return
        w=float(self.landscape_w or self.root.width or 1.0); h=float(self.landscape_h or self.root.height or 1.0)
        self.color_picker_overlay.frame=(0,0,w,h)
        if self.color_picker_dismiss is not None:self.color_picker_dismiss.frame=(0,0,w,h)
        pw=min(640.0,max(430.0,w-90.0)); ph=min(350.0,max(270.0,h-42.0)); x=(w-pw)*0.5; y=(h-ph)*0.5
        self.color_picker_panel.frame=(x,y,pw,ph); self.color_picker_title.frame=(14,8,pw-76,30); self.color_picker_close.frame=(pw-48,8,34,30)
        guide_x=16.0; guide_y=46.0; guide_w=pw-32.0; guide_h=max(120.0,ph-142.0)
        cols=10; rows=8; cw=guide_w/cols; ch=guide_h/rows
        for i,v in enumerate(self.color_sv_cells):
            yy=i//cols; xx=i%cols; v.frame=(guide_x+xx*cw,guide_y+yy*ch,cw+0.5,ch+0.5)
        self.color_sv_overlay.frame=(guide_x,guide_y,guide_w,guide_h)
        hue_y=guide_y+guide_h+10.0; hue_h=28.0; hc=max(1,len(self.color_hue_cells)); hw=guide_w/hc
        for i,v in enumerate(self.color_hue_cells):v.frame=(guide_x+i*hw,hue_y,hw+0.5,hue_h)
        self.color_hue_overlay.frame=(guide_x,hue_y,guide_w,hue_h)
        self.color_picker_current.frame=(guide_x,hue_y+hue_h+10.0,max(80.0,guide_w-150.0),30.0)
        self.color_picker_confirm.frame=(guide_x+guide_w-142.0,hue_y+hue_h+10.0,142.0,30.0)
        try:
            self.color_picker_panel.bring_subview_to_front(self.color_sv_overlay); self.color_picker_panel.bring_subview_to_front(self.color_hue_overlay)
            self.root.bring_subview_to_front(self.color_picker_overlay)
        except Exception:pass

    def open_color_picker(self):
        if self.color_picker_overlay is None:return
        self.color_picker_hue,self.color_picker_sat,self.color_picker_val=self._selected_hex_to_hsv(); self._refresh_sv_guide()
        self.color_picker_visible=True; self.color_picker_overlay.hidden=False; self._layout_color_picker()
        try:self.color_picker_current.background_color=color(self.selected_color[:7]); self.root.bring_subview_to_front(self.color_picker_overlay)
        except Exception:pass
        self.labels["status"].text="選色器：手指可直接在色面與色相條上滑動；不再限制固定色塊"

    def close_color_picker(self):
        self.color_picker_visible=False
        if self.color_picker_overlay is not None:self.color_picker_overlay.hidden=True

    def confirm_visual_color(self):
        self.tool="pen"; self._sync_overlay_drag_mode(); self.refresh_labels(); self.close_color_picker(); self.labels["status"].text="已使用滑動選色："+str(self.selected_color)[:7]

    def pick_visual_color(self,c):
        # Backward-compatible callback for any already-open FIX54 button.
        self.selected_color=str(c); self.confirm_visual_color()

    def _undo_limit(self):
        # 32x32 frames contain four times as many cells as 16x16. Keeping fewer
        # snapshots at the largest canvas prevents long paint sessions from
        # accumulating avoidable Python list overhead.
        return 8 if self.canvas_size>=32 else (12 if self.canvas_size>=24 else 16)

    def _trim_undo(self):
        limit=self._undo_limit()
        if len(self.undo_stack)>limit:
            del self.undo_stack[:len(self.undo_stack)-limit]

    def _push_undo(self):
        # Ordinary paint undo stores only the current frame, never every
        # animation state. Strings are immutable so deepcopy mainly duplicates
        # the row containers and remains bounded by _undo_limit().
        self.undo_stack.append((
            "frame", int(self.canvas_size), copy.deepcopy(self.pixels),
            set(self.selection), copy.deepcopy(self.combat_bindings),
        ))
        self._trim_undo()

    def _push_state_undo(self):
        """Snapshot only the currently edited animation state."""
        self._commit_current_frame()
        raw=self._current_animation()
        if not self.animation_mode or not isinstance(raw,dict):
            self._push_undo(); return
        self.undo_stack.append((
            "anim_state", int(self.canvas_size), str(self.animation_state),
            copy.deepcopy(raw), int(self.animation_frame_index), set(self.selection),
            copy.deepcopy(self.combat_bindings),
        ))
        self._trim_undo()

    def _push_full_undo(self):
        """Compressed rare snapshot used by whole-canvas resolution changes."""
        self._commit_current_frame()
        payload={"base":self.base_pixels,"animations":self.animations,"asset_canvas_size":self.asset_canvas_size,
                 "export_canvas_size":self.export_canvas_size,"export_scale":self.export_scale,
                 "native_export_override":self._native_export_override,"canvas_size_direction":self.canvas_size_direction}
        raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        packed=zlib.compress(raw,1)
        self.undo_stack.append((
            "fullz", int(self.canvas_size), packed, bool(self.animation_mode),
            str(self.animation_state), int(self.animation_frame_index), set(self.selection),
        ))
        self._trim_undo()

    def undo(self):
        if not self.undo_stack:
            self.labels["status"].text="沒有可撤銷步驟"
            return
        snap=self.undo_stack.pop()
        if isinstance(snap,tuple) and len(snap)>=5 and snap[0]=="frame":
            _tag,old_size,old_pixels,old_selection,old_bindings=snap[:5]
            old_size=int(old_size); size_changed=(old_size!=self.canvas_size); self.canvas_size=old_size
            if self.canvas_size in PIXEL_SIZES:self.size_index=PIXEL_SIZES.index(self.canvas_size)
            self.pixels=copy.deepcopy(old_pixels); self.selection=set(old_selection)
            self.combat_bindings=copy.deepcopy(old_bindings) if isinstance(old_bindings,dict) else {}
            self._reset_selection_gesture(); self.dirty=True
            if size_changed:self._rebuild_pixel_views(); self._rebuild_preview_views()
            self.refresh_pixels(); self.refresh_labels()
            if size_changed:
                try:self._layout()
                except Exception:pass
            self.labels["status"].text="撤銷完成"
            return
        if isinstance(snap,tuple) and len(snap)>=7 and snap[0]=="fullz":
            _tag,old_size,packed,old_mode,old_state,old_index,old_selection=snap[:7]
            try:payload=json.loads(zlib.decompress(packed).decode("utf-8"))
            except Exception:
                self.labels["status"].text="撤銷資料損壞，已略過"; return
            size_changed=(int(old_size)!=self.canvas_size); self.asset_canvas_size=int(old_size); self.canvas_size=int(old_size)
            if self.canvas_size in PIXEL_SIZES:self.size_index=PIXEL_SIZES.index(self.canvas_size)
            self.base_pixels=payload.get("base",[]); self.animations=payload.get("animations",{})
            self.asset_canvas_size=int(payload.get("asset_canvas_size",len(self.base_pixels) or old_size))
            self.export_canvas_size=int(payload.get("export_canvas_size",self.asset_canvas_size) or self.asset_canvas_size)
            self.export_scale=float(payload.get("export_scale",float(self.export_canvas_size)/max(1,self.asset_canvas_size)) or 1.0)
            self._native_export_override=payload.get("native_export_override",None)
            self.canvas_size_direction=int(payload.get("canvas_size_direction",1) or 1)
            self.animation_mode=bool(old_mode); self.animation_state=str(old_state); self.animation_frame_index=int(old_index)
            if self.animation_mode:
                raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
                self.animation_frame_index=max(0,min(self.animation_frame_index,len(frames)-1)) if frames else 0
                self.pixels=copy.deepcopy(frames[self.animation_frame_index]) if frames else copy.deepcopy(self.base_pixels)
            else:self.pixels=copy.deepcopy(self.base_pixels)
            self.selection=set(old_selection); self._reset_selection_gesture(); self.dirty=True
            if size_changed:
                self._rebuild_pixel_views(); self._rebuild_preview_views()
            self.refresh_pixels(); self.refresh_labels()
            if size_changed:
                try:self._layout()
                except Exception:pass
            self.labels["status"].text="撤銷完成"
            return
        if isinstance(snap,tuple) and len(snap)>=6 and snap[0]=="anim_state":
            _tag,old_size,state,old_raw,old_index,old_selection=snap[:6]
            self.animations[str(state)]=copy.deepcopy(old_raw)
            self.animation_mode=True; self.animation_state=str(state)
            frames=self.animations[str(state)].get("frames",[])
            self.animation_frame_index=max(0,min(int(old_index),len(frames)-1)) if frames else 0
            self.pixels=copy.deepcopy(frames[self.animation_frame_index]) if frames else copy.deepcopy(self.base_pixels)
            self.selection=set(old_selection); self._reset_selection_gesture(); self.dirty=True
            if len(snap)>=7 and isinstance(snap[6],dict):self.combat_bindings=copy.deepcopy(snap[6])
            self._sync_active_canvas(recenter=True)
            self.refresh_pixels(); self.refresh_labels(); self.labels["status"].text="動畫設定已撤銷"
            return
        if isinstance(snap,tuple) and len(snap)>=8 and snap[0]=="full":
            # Compatibility with a snapshot created by an already-running FIX13
            # editor before this module was reloaded.
            _tag,old_size,old_base,old_anims,old_mode,old_state,old_index,old_selection=snap[:8]
            size_changed=(int(old_size)!=self.canvas_size); self.asset_canvas_size=int(old_size); self.canvas_size=int(old_size)
            if self.canvas_size in PIXEL_SIZES:self.size_index=PIXEL_SIZES.index(self.canvas_size)
            self.base_pixels=copy.deepcopy(old_base); self.animations=copy.deepcopy(old_anims)
            self.animation_mode=bool(old_mode); self.animation_state=str(old_state); self.animation_frame_index=int(old_index)
            if self.animation_mode:
                raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
                self.animation_frame_index=max(0,min(self.animation_frame_index,len(frames)-1)) if frames else 0
                self.pixels=copy.deepcopy(frames[self.animation_frame_index]) if frames else copy.deepcopy(self.base_pixels)
            else:self.pixels=copy.deepcopy(self.base_pixels)
            self.selection=set(old_selection); self._reset_selection_gesture(); self.dirty=True
            if size_changed:self._rebuild_pixel_views(); self._rebuild_preview_views()
            self.refresh_pixels(); self.refresh_labels(); self.labels["status"].text="撤銷完成"
            return
        if isinstance(snap,tuple) and len(snap)==3:
            old_size,old_pixels,old_selection=snap
        elif isinstance(snap,tuple) and len(snap)==2:
            old_size,old_pixels=snap; old_selection=set()
        else:
            # Backward-safe fallback for any in-memory snapshot made by an
            # older editor instance before selection-aware undo was loaded.
            old_pixels=snap
            old_size=len(old_pixels) if isinstance(old_pixels,list) else self.canvas_size
            old_selection=set()
        old_size=int(old_size)
        size_changed=(old_size!=self.canvas_size)
        self.canvas_size=old_size
        if not self.animation_mode:self.asset_canvas_size=old_size
        if self.canvas_size in PIXEL_SIZES:
            self.size_index=PIXEL_SIZES.index(self.canvas_size)
        self.pixels=copy.deepcopy(old_pixels)
        self.selection=set(old_selection)
        self._reset_selection_gesture()
        if size_changed:
            self._rebuild_pixel_views(); self._rebuild_preview_views()
        self.dirty=True
        self.refresh_pixels()
        if size_changed:
            try:self._layout()
            except Exception:pass
        self.refresh_labels()
        self.labels["status"].text="撤銷完成"

    # ------------------------------------------------------------
    # FIX124: explicit, always-visible FX frame editor panel
    # ------------------------------------------------------------
    # ------------------------------------------------------------------
    # FIX127: FX world-motion / segment choreography editor
    # ------------------------------------------------------------------
    def _build_fx_motion_editor(self):
        overlay=ui.View(); overlay.background_color=ui.Color.rgb(0.0,0.0,0.0,0.62); overlay.hidden=True
        try:overlay.user_interaction_enabled=True
        except Exception:pass
        self.root.add_subview(overlay); self.fx_motion_overlay=overlay
        dismiss=ui.Button(title=" ")
        try:dismiss.background_color=ui.Color.rgb(0,0,0,0.01);dismiss.title_color=ui.Color.rgb(0,0,0,0)
        except Exception:pass
        dismiss.action=lambda _s:self.close_fx_motion_editor(False)
        overlay.add_subview(dismiss);self.fx_motion_dismiss=dismiss
        panel=ui.View();panel.background_color=color("#243746");panel.corner_radius=10
        try:panel.border_width=1;panel.border_color=color("#71808C")
        except Exception:pass
        overlay.add_subview(panel);self.fx_motion_panel=panel

        def label(key,text,size=10,bg=None,lines=1):
            v=ui.Label(text);v.text_color=WHITE;v.font=ui.Font.system_font_of_size(size);v.number_of_lines=lines
            if bg is not None:v.background_color=bg
            panel.add_subview(v);self.fx_motion_labels[key]=v;return v
        def button(key,title,action,bg=DARK2,size=9):
            b=ui.Button(title=title);b.background_color=bg;b.title_color=WHITE;b.corner_radius=5;b.font=ui.Font.bold_system_font_of_size(size);b.action=action
            panel.add_subview(b);self.fx_motion_buttons[key]=b;return b

        self.fx_motion_title=label('title','特效遊戲運動設定',14,None,1)
        self.fx_motion_info=label('info','',9,color("#31495D"),2)
        label('global','整體時間／步進',10,None,1)
        button('mode','模式：定點',lambda _s:self._cycle_fx_motion_mode(),BLUE,9)
        button('repeat','動畫：單次',lambda _s:self._toggle_fx_motion_repeat(),GOLD,8)
        button('seg_add','＋段',lambda _s:self._fx_motion_add_segment(),GREEN,9)
        button('seg_del','刪最後',lambda _s:self._fx_motion_delete_segment(),RED,8)
        for key,action in (
            ('start_minus',lambda _s:self._fx_motion_adjust_start(-.05)),('start_plus',lambda _s:self._fx_motion_adjust_start(.05)),
            ('interval_minus',lambda _s:self._fx_motion_adjust_interval(-.02)),('interval_plus',lambda _s:self._fx_motion_adjust_interval(.02)),
            ('step_minus',lambda _s:self._fx_motion_adjust_step(-.10)),('step_plus',lambda _s:self._fx_motion_adjust_step(.10)),
            ('fps_minus',lambda _s:self._fx_motion_adjust_fps(-1.0)),('fps_plus',lambda _s:self._fx_motion_adjust_fps(1.0)),
        ):button(key,'－' if key.endswith('minus') else '＋',action,DARK2,10)
        button('start_value','起始延遲',lambda _s:None,DARK2,8)
        button('interval_value','段間隔',lambda _s:None,DARK2,8)
        button('step_value','X步距',lambda _s:None,DARK2,8)
        button('fps_value','特效幀率',lambda _s:None,DARK2,8)

        label('segment','單段位置／時間',10,None,1)
        button('seg_prev','◀',lambda _s:self._fx_motion_select_segment(-1),DARK2,10)
        button('seg_counter','段 1/1',lambda _s:None,GOLD,9)
        button('seg_next','▶',lambda _s:self._fx_motion_select_segment(1),DARK2,10)
        for key,action in (
            ('delay_minus',lambda _s:self._fx_motion_adjust_segment('delay',-.05)),('delay_plus',lambda _s:self._fx_motion_adjust_segment('delay',.05)),
            ('x_minus',lambda _s:self._fx_motion_adjust_segment('dx_tiles',-.25)),('x_plus',lambda _s:self._fx_motion_adjust_segment('dx_tiles',.25)),
            ('y_minus',lambda _s:self._fx_motion_adjust_segment('dy_tiles',-.25)),('y_plus',lambda _s:self._fx_motion_adjust_segment('dy_tiles',.25)),
            ('scale_minus',lambda _s:self._fx_motion_adjust_segment('scale',-.05)),('scale_plus',lambda _s:self._fx_motion_adjust_segment('scale',.05)),
        ):button(key,'－' if key.endswith('minus') else '＋',action,DARK2,10)
        for key in ('delay_value','x_value','y_value','scale_value'):button(key,key,lambda _s:None,DARK2,8)

        self.fx_motion_stage=ui.View();self.fx_motion_stage.background_color=color("#0D141B");self.fx_motion_stage.border_width=1;self.fx_motion_stage.border_color=color("#52606B");panel.add_subview(self.fx_motion_stage)
        self.fx_motion_ground=ui.View();self.fx_motion_ground.background_color=color("#8C7A56");self.fx_motion_stage.add_subview(self.fx_motion_ground)
        for i in range(16):
            m=ui.Label(str(i+1));m.text_alignment=_center();m.font=ui.Font.bold_system_font_of_size(7);m.text_color=WHITE;m.background_color=DARK2;m.corner_radius=5;m.hidden=True
            self.fx_motion_stage.add_subview(m);self.fx_motion_markers.append(m)
        # Up to four overlapping segment clips are enough for the shipped boss
        # timings (and keep UIKit bounded). Each sprite is a tiny 10x10 sampled
        # copy of the REAL current effect frame; moving the container is cheap.
        for _slot in range(4):
            holder=ui.View();holder.background_color=ui.Color.rgb(0,0,0,0);holder.hidden=True
            self.fx_motion_stage.add_subview(holder);cells=[]
            for _i in range(100):
                c=ui.View();c.user_interaction_enabled=False;c.hidden=True;holder.add_subview(c);cells.append(c)
            self.fx_motion_sprite_instances.append((holder,cells))
        self.fx_motion_note=label('note','主畫布固定在單一幀方便繪圖；右側預覽套用遊戲世界移動。距離模式可讓動畫沿指定距離連續前進。',8,color("#31495D"),3)
        button('preview','▶ 實戰移動預覽',lambda _s:self.toggle_fx_motion_preview(),BLUE,9)
        button('apply','套用並存檔',lambda _s:self.close_fx_motion_editor(True),GREEN,10)
        button('cancel','取消',lambda _s:self.close_fx_motion_editor(False),DARK2,10)

    def _fx_motion_segments(self):
        if not isinstance(self.fx_motion_profile,dict):return []
        binding=self.fx_motion_profile.get('binding',{})
        rows=binding.get('segments',[]) if isinstance(binding,dict) else []
        return rows if isinstance(rows,list) else []

    def _fx_motion_raw(self):
        raw=self.animations.get('effect',{}) if isinstance(self.animations,dict) else {}
        return raw if isinstance(raw,dict) else {}

    def _fx_motion_fps(self):
        try:return max(1.0,min(30.0,float(self._fx_motion_raw().get('fps',8.0) or 8.0)))
        except Exception:return 8.0

    @staticmethod
    def _fx_num(value,default=0.0):
        try:return float(value)
        except Exception:return float(default)

    def _fx_motion_stats(self):
        segs=self._fx_motion_segments();n=len(segs)
        if not n:return 0.0,0.0,0.0
        delays=[max(0.0,self._fx_num(s.get('delay',0.0))) for s in segs]
        xs=[self._fx_num(s.get('dx_tiles',0.0)) for s in segs]
        start=delays[0]
        interval=((delays[-1]-delays[0])/(n-1)) if n>1 else 0.0
        step=((xs[-1]-xs[0])/(n-1)) if n>1 else 0.0
        return start,interval,step

    def _fx_motion_mode(self):
        if not isinstance(self.fx_motion_profile,dict):return 'stationary'
        b=self.fx_motion_profile.get('binding',{})
        mode=str(b.get('world_motion_mode','') or '').lower() if isinstance(b,dict) else ''
        if mode not in ('stationary','segments','distance'):
            mode='segments' if self._fx_motion_segments() else 'stationary'
        return mode

    def _fx_motion_repeat(self):
        if not isinstance(self.fx_motion_profile,dict):return 'once'
        b=self.fx_motion_profile.get('binding',{})
        value=str(b.get('motion_repeat','once') or 'once').lower() if isinstance(b,dict) else 'once'
        return value if value in ('once','loop') else 'once'

    def _toggle_fx_motion_repeat(self):
        if not isinstance(self.fx_motion_profile,dict):return
        b=self.fx_motion_profile.get('binding',{})
        b['motion_repeat']='loop' if self._fx_motion_repeat()=='once' else 'once'
        self._refresh_fx_motion_editor()

    def _ensure_fx_distance_defaults(self):
        if not isinstance(self.fx_motion_profile,dict):return
        b=self.fx_motion_profile.get('binding',{});segs=self._fx_motion_segments();raw=self._fx_motion_raw()
        fc=max(1,len(raw.get('frames',[]) if isinstance(raw,dict) else []));fps=self._fx_motion_fps();clip=max(.05,fc/fps)
        if segs:
            delays=[max(0.0,self._fx_num(s.get('delay',0.0))) for s in segs]
            xs=[self._fx_num(s.get('dx_tiles',0.0)) for s in segs];ys=[self._fx_num(s.get('dy_tiles',0.0)) for s in segs]
            b.setdefault('travel_start_delay',delays[0]);b.setdefault('travel_start_x_tiles',xs[0]);b.setdefault('travel_start_y_tiles',ys[0])
            b.setdefault('travel_distance_tiles',xs[-1]-xs[0]);b.setdefault('travel_y_tiles',ys[-1]-ys[0])
            b.setdefault('travel_duration',max(clip,delays[-1]-delays[0]+clip))
        else:
            b.setdefault('travel_start_delay',0.0);b.setdefault('travel_start_x_tiles',0.0);b.setdefault('travel_start_y_tiles',0.0)
            b.setdefault('travel_distance_tiles',4.0);b.setdefault('travel_y_tiles',0.0);b.setdefault('travel_duration',clip)
        b.setdefault('motion_repeat','once')

    def _fx_motion_distance_values(self):
        self._ensure_fx_distance_defaults()
        b=self.fx_motion_profile.get('binding',{}) if isinstance(self.fx_motion_profile,dict) else {}
        return (
            max(0.0,min(4.0,self._fx_num(b.get('travel_start_delay',0.0)))),
            max(.05,min(8.0,self._fx_num(b.get('travel_duration',1.0),1.0))),
            max(-16.0,min(16.0,self._fx_num(b.get('travel_start_x_tiles',0.0)))),
            max(-16.0,min(16.0,self._fx_num(b.get('travel_start_y_tiles',0.0)))),
            max(-32.0,min(32.0,self._fx_num(b.get('travel_distance_tiles',4.0),4.0))),
            max(-16.0,min(16.0,self._fx_num(b.get('travel_y_tiles',0.0)))),
        )

    def open_fx_motion_editor(self):
        if self.category[0]!='effect':
            self.labels['status'].text='FX 運動設定只在「效果素材」中使用';return
        self._stop_animation_preview();self._commit_current_frame()
        ctx=self.model.effect_gameplay_motion(self.asset_id)
        if not isinstance(ctx,dict):
            self.labels['status'].text='這個效果素材目前不是 BOSS／生物遊戲直連 FX，沒有世界運動綁定可編輯';return
        self.fx_motion_profile=copy.deepcopy(ctx);self.fx_motion_segment_index=0
        self.fx_motion_segments_backup=copy.deepcopy(self._fx_motion_segments())
        self.fx_motion_visible=True;self.fx_motion_overlay.hidden=False
        self._stop_fx_motion_preview();self._refresh_fx_motion_editor();self._layout_fx_motion_editor()
        try:self.root.bring_subview_to_front(self.fx_motion_overlay)
        except Exception:pass
        self.labels['status'].text='FX 運動設定：可調世界中的段落位置、頻率、延遲與特效 FPS；右側可直接預覽移動'

    def close_fx_motion_editor(self,apply=False):
        if apply and isinstance(self.fx_motion_profile,dict):
            try:
                self._stop_fx_motion_preview();self._commit_current_frame()
                binding=self.fx_motion_profile.get('binding',{})
                binding['segment_source_fps']=float(self._fx_motion_fps())
                # Persist effect frames/FPS and gameplay motion as one deliberate
                # authoring action so editor preview and the next game run agree.
                path,row=self.model.save_pixel_asset(self.asset_id,self.category[0],self.base_pixels,self.animations,self.combat_bindings,export_size_override=self._native_export_override)
                self.export_canvas_size=int(row.get('export_size',self.asset_canvas_size) or self.asset_canvas_size);self.export_scale=float(row.get('export_scale',1.0) or 1.0);self._native_export_override=None
                saved=self.model.save_effect_gameplay_motion(self.asset_id,binding)
                self.fx_motion_profile=copy.deepcopy(saved) if isinstance(saved,dict) else self.fx_motion_profile
                self.dirty=False
                self.labels['status'].text='已存檔 FX 圖像＋FPS＋遊戲移動參數｜下次進入遊戲會直接使用這組設定'
                haptic_light()
            except Exception as exc:
                self.labels['status'].text='FX 運動設定存檔失敗：'+repr(exc);return
        self._stop_fx_motion_preview();self.fx_motion_visible=False;self.fx_motion_profile=None
        if self.fx_motion_overlay is not None:self.fx_motion_overlay.hidden=True
        self.refresh_labels()

    def _cycle_fx_motion_mode(self):
        if not isinstance(self.fx_motion_profile,dict):return
        b=self.fx_motion_profile.get('binding',{});segs=self._fx_motion_segments();mode=self._fx_motion_mode()
        playback=str(b.get('segment_playback','clip') or 'clip').lower()
        # Four explicit authoring modes: stationary -> segment clip -> segment
        # fixed-frame -> continuous distance -> stationary.  Segment data is
        # retained while distance mode is active so switching back is lossless.
        if mode=='stationary':
            if not segs:
                restore=copy.deepcopy(self.fx_motion_segments_backup) if self.fx_motion_segments_backup else [{'delay':0.0,'dx_tiles':1.0,'dy_tiles':0.0,'scale':1.0}]
                b['segments']=restore[:16]
            b['world_motion_mode']='segments';b['segment_playback']='clip';self.fx_motion_segment_index=0
        elif mode=='segments' and playback in ('clip','full_clip','animate'):
            b['segment_playback']='phase'
        elif mode=='segments':
            self.fx_motion_segments_backup=copy.deepcopy(segs);self._ensure_fx_distance_defaults();b['world_motion_mode']='distance'
        else:
            b['world_motion_mode']='stationary'
        self._refresh_fx_motion_editor()

    def _fx_motion_add_segment(self):
        if not isinstance(self.fx_motion_profile,dict):return
        b=self.fx_motion_profile.get('binding',{});segs=self._fx_motion_segments()
        if not segs:
            b['segments']=[{'delay':0.0,'dx_tiles':1.0,'dy_tiles':0.0,'scale':1.0}];b['segment_playback']='clip';self.fx_motion_segment_index=0
        elif len(segs)<16:
            start,interval,step=self._fx_motion_stats();last=copy.deepcopy(segs[-1])
            last['delay']=max(0.0,self._fx_num(last.get('delay',0.0))+(interval if interval>0 else .20))
            last['dx_tiles']=self._fx_num(last.get('dx_tiles',0.0))+(step if abs(step)>.001 else 1.0)
            segs.append(last);self.fx_motion_segment_index=len(segs)-1
        self.fx_motion_segments_backup=copy.deepcopy(self._fx_motion_segments());self._refresh_fx_motion_editor()

    def _fx_motion_delete_segment(self):
        segs=self._fx_motion_segments()
        if not segs:return
        segs.pop();self.fx_motion_segment_index=max(0,min(self.fx_motion_segment_index,len(segs)-1))
        self.fx_motion_segments_backup=copy.deepcopy(segs);self._refresh_fx_motion_editor()

    def _fx_motion_select_segment(self,delta):
        segs=self._fx_motion_segments()
        if not segs:return
        self.fx_motion_segment_index=(int(self.fx_motion_segment_index)+int(delta))%len(segs);self._refresh_fx_motion_editor()

    def _fx_motion_adjust_start(self,delta):
        if self._fx_motion_mode()=='distance':
            b=self.fx_motion_profile.get('binding',{});self._ensure_fx_distance_defaults()
            b['travel_start_delay']=max(0.0,min(4.0,self._fx_num(b.get('travel_start_delay',0.0))+float(delta)))
            self._refresh_fx_motion_editor();return
        segs=self._fx_motion_segments()
        if not segs:return
        current=max(0.0,self._fx_num(segs[0].get('delay',0.0)));target=max(0.0,min(4.0,current+float(delta)));shift=target-current
        for s in segs:s['delay']=max(0.0,min(4.0,self._fx_num(s.get('delay',0.0))+shift))
        self._refresh_fx_motion_editor()

    def _fx_motion_adjust_interval(self,delta):
        if self._fx_motion_mode()=='distance':
            b=self.fx_motion_profile.get('binding',{});self._ensure_fx_distance_defaults()
            b['travel_duration']=max(.05,min(8.0,self._fx_num(b.get('travel_duration',1.0),1.0)+float(delta)*2.5))
            self._refresh_fx_motion_editor();return
        segs=self._fx_motion_segments();n=len(segs)
        if n<2:return
        start,interval,_step=self._fx_motion_stats();new=max(.01,min(1.5,interval+float(delta)))
        for i,s in enumerate(segs):s['delay']=max(0.0,min(4.0,start+new*i))
        self._refresh_fx_motion_editor()

    def _fx_motion_adjust_step(self,delta):
        if self._fx_motion_mode()=='distance':
            b=self.fx_motion_profile.get('binding',{});self._ensure_fx_distance_defaults()
            b['travel_distance_tiles']=max(-32.0,min(32.0,self._fx_num(b.get('travel_distance_tiles',4.0),4.0)+float(delta)*2.5))
            self._refresh_fx_motion_editor();return
        segs=self._fx_motion_segments();n=len(segs)
        if n<2:return
        _start,_interval,step=self._fx_motion_stats();new=max(-4.0,min(4.0,step+float(delta)));x0=self._fx_num(segs[0].get('dx_tiles',0.0))
        for i,s in enumerate(segs):s['dx_tiles']=max(-16.0,min(16.0,x0+new*i))
        self._refresh_fx_motion_editor()

    def _fx_motion_adjust_fps(self,delta):
        raw=self._fx_motion_raw()
        if not raw:return
        raw['fps']=max(1.0,min(30.0,self._fx_motion_fps()+float(delta)));self.dirty=True
        if isinstance(self.fx_motion_profile,dict):self.fx_motion_profile.get('binding',{})['segment_source_fps']=raw['fps']
        self._refresh_fx_motion_editor()

    def _fx_motion_adjust_segment(self,key,delta):
        if self._fx_motion_mode()=='distance':
            b=self.fx_motion_profile.get('binding',{});self._ensure_fx_distance_defaults()
            mapping={'delay':'travel_start_x_tiles','dx_tiles':'travel_start_y_tiles','dy_tiles':'travel_y_tiles'}
            if key=='scale':
                target='pixel_density_scale' if str(b.get('pixel_density_mode','') or '')=='match_owner' else 'scale'
                cur=self._fx_num(b.get(target,1.0),1.0);b[target]=max(.25,min(2.5,cur+float(delta)))
            else:
                target=mapping.get(key)
                if not target:return
                cur=self._fx_num(b.get(target,0.0));b[target]=max(-16.0,min(16.0,cur+float(delta)))
            self._refresh_fx_motion_editor();return
        segs=self._fx_motion_segments()
        if not segs:return
        i=max(0,min(len(segs)-1,int(self.fx_motion_segment_index)));s=segs[i]
        cur=self._fx_num(s.get(key,1.0 if key=='scale' else 0.0));value=cur+float(delta)
        if key=='delay':value=max(0.0,min(4.0,value))
        elif key in ('dx_tiles','dy_tiles'):value=max(-16.0,min(16.0,value))
        elif key=='scale':value=max(.25,min(2.0,value))
        s[key]=value;self._refresh_fx_motion_editor()

    def _refresh_fx_motion_editor(self):
        if not self.fx_motion_visible or not isinstance(self.fx_motion_profile,dict):return
        b=self.fx_motion_profile.get('binding',{});segs=self._fx_motion_segments();n=len(segs);mode_id=self._fx_motion_mode()
        owner=str(self.fx_motion_profile.get('owner_asset',''));state=str(self.fx_motion_profile.get('owner_state',''))
        owner_zh=self.model.display_name(owner) if owner else '—';state_zh='重攻 / special' if state=='special' else '一般攻擊 / attack'
        fps=self._fx_motion_fps();start,interval,step=self._fx_motion_stats();playback=str(b.get('segment_playback','clip') or 'clip').lower()
        mode=('距離移動' if mode_id=='distance' else ('定點' if mode_id=='stationary' else ('逐段動畫' if playback in ('clip','full_clip','animate') else '逐段定幀')))
        repeat=self._fx_motion_repeat()
        self.fx_motion_title.text='特效遊戲運動設定｜'+self.model.display_name(self.asset_id)
        self.fx_motion_info.text=f'{owner_zh}・{state_zh}｜編輯器與遊戲共用同一份運動資料\n特效幀率控制內部動畫；距離模式可在一次攻擊期間連續移動'
        self.fx_motion_buttons['mode'].title='模式：'+mode
        self.fx_motion_buttons['repeat'].title='動畫：'+('循環' if repeat=='loop' else '單次')
        distance=(mode_id=='distance')
        if distance:
            dstart,duration,sx,sy,dx,dy=self._fx_motion_distance_values()
            self.fx_motion_buttons['start_value'].title=f'起始 {dstart:.2f}s'
            self.fx_motion_buttons['interval_value'].title=f'移動 {duration:.2f}s'
            self.fx_motion_buttons['step_value'].title=f'X距離 {dx:.2f}格'
            self.fx_motion_buttons['fps_value'].title=f'特效 {fps:.0f} FPS'
            self.fx_motion_labels['segment'].text='距離路徑／位置'
            self.fx_motion_buttons['seg_counter'].title='連續移動'
            self.fx_motion_buttons['delay_value'].title=f'起點X {sx:.2f}格'
            self.fx_motion_buttons['x_value'].title=f'起點Y {sy:.2f}格'
            self.fx_motion_buttons['y_value'].title=f'Y距離 {dy:.2f}格'
            scale_key='pixel_density_scale' if str(b.get('pixel_density_mode','') or '')=='match_owner' else 'scale'
            self.fx_motion_buttons['scale_value'].title=f'倍率 {self._fx_num(b.get(scale_key,1.0),1.0):.2f}×'
        else:
            self.fx_motion_labels['segment'].text='單段位置／時間'
            self.fx_motion_buttons['start_value'].title=f'起始 {start:.2f}s'
            self.fx_motion_buttons['interval_value'].title=f'間隔 {interval:.2f}s'
            self.fx_motion_buttons['step_value'].title=f'X步距 {step:.2f}格'
            self.fx_motion_buttons['fps_value'].title=f'特效 {fps:.0f} FPS'
            if n:
                i=max(0,min(n-1,int(self.fx_motion_segment_index)));self.fx_motion_segment_index=i;s=segs[i]
                self.fx_motion_buttons['seg_counter'].title=f'段 {i+1}/{n}'
                self.fx_motion_buttons['delay_value'].title=f'延遲 {self._fx_num(s.get("delay",0.0)):.2f}s'
                self.fx_motion_buttons['x_value'].title=f'X {self._fx_num(s.get("dx_tiles",0.0)):.2f}格'
                self.fx_motion_buttons['y_value'].title=f'Y {self._fx_num(s.get("dy_tiles",0.0)):.2f}格'
                self.fx_motion_buttons['scale_value'].title=f'倍率 {self._fx_num(s.get("scale",1.0),1.0):.2f}×'
            else:
                self.fx_motion_buttons['seg_counter'].title='無段落'
                for k,v in (('delay_value','延遲 —'),('x_value','X —'),('y_value','Y —'),('scale_value','倍率 —')):self.fx_motion_buttons[k].title=v
        # Segment add/delete/nav are only meaningful for segment choreography.
        for key in ('seg_add','seg_del','seg_prev','seg_next'):
            try:self.fx_motion_buttons[key].enabled=(mode_id=='segments')
            except Exception:pass
        for key in ('seg_counter','delay_minus','delay_value','delay_plus','x_minus','x_value','x_plus','y_minus','y_value','y_plus','scale_minus','scale_value','scale_plus'):
            try:self.fx_motion_buttons[key].enabled=(distance or (mode_id=='segments' and bool(n)))
            except Exception:pass
        if distance:
            speed=abs(dx)/max(.05,duration)
            self.fx_motion_note.text=f'距離模式：從起點沿 X {dx:.2f}格／Y {dy:.2f}格，{duration:.2f}s 走完（約 {speed:.2f}格/s）。動畫「循環」只重播幀，不會讓攻擊路徑無限循環。'
        elif mode_id=='stationary':self.fx_motion_note.text='定點模式：特效只在綁定錨點播放。動畫可選單次／循環。'
        else:self.fx_motion_note.text=f'目前 {n} 段｜「間隔」= 下一段出現頻率；「X步距」= 每段平均前進距離。可再逐段微調。'
        self._refresh_fx_motion_stage(self.fx_motion_preview_time if self.fx_motion_preview_playing else 0.0,previewing=self.fx_motion_preview_playing)

    def _fx_motion_duration(self):
        raw=self._fx_motion_raw();frames=raw.get('frames',[]) if isinstance(raw,dict) else [];fps=self._fx_motion_fps();fc=max(1,len(frames))
        b=self.fx_motion_profile.get('binding',{}) if isinstance(self.fx_motion_profile,dict) else {};mode=self._fx_motion_mode();segs=self._fx_motion_segments();playback=str(b.get('segment_playback','clip') or 'clip').lower()
        if mode=='distance':
            start,duration,_sx,_sy,_dx,_dy=self._fx_motion_distance_values();return max(.05,start+duration)
        if mode=='stationary' or not segs:return max(.05,fc/fps)
        end=0.0
        for s in segs:
            delay=max(0.0,self._fx_num(s.get('delay',0.0)))
            if playback in ('clip','full_clip','animate'):
                start=max(0,int(self._fx_num(s.get('start_frame',b.get('segment_start_frame',0)),0)))
                span=int(self._fx_num(s.get('clip_frames',b.get('segment_clip_frames',0)),0))
                if span<=0:span=max(1,fc-start)
                life=max(1.0/fps,float(span)/fps)
            else:life=max(1.0/fps,self._fx_num(s.get('hold',max(2.0/fps,.11)),max(2.0/fps,.11)))
            end=max(end,delay+life)
        return max(.05,end)

    def _fx_motion_active(self,t):
        raw=self._fx_motion_raw();frames=raw.get('frames',[]) if isinstance(raw,dict) else [];fc=max(1,len(frames));fps=self._fx_motion_fps()
        b=self.fx_motion_profile.get('binding',{}) if isinstance(self.fx_motion_profile,dict) else {};mode=self._fx_motion_mode();segs=self._fx_motion_segments();playback=str(b.get('segment_playback','clip') or 'clip').lower();repeat=self._fx_motion_repeat()
        if mode=='distance':
            start,duration,sx,sy,dx,dy=self._fx_motion_distance_values()
            if t<start or t>=start+duration:return []
            local=max(0.0,t-start);progress=max(0.0,min(1.0,local/max(.05,duration)))
            raw_frame=max(0,int(local*fps));frame=(raw_frame%fc if repeat=='loop' else min(fc-1,raw_frame))
            return [dict(index=0,frame=frame,dx=sx+dx*progress,dy=sy+dy*progress,scale=1.0,distance=True)]
        if mode=='stationary' or not segs:
            raw_frame=max(0,int(max(0.0,t)*fps));frame=(raw_frame%fc if repeat=='loop' else min(fc-1,raw_frame))
            return [dict(index=0,frame=frame,dx=0.,dy=0.,scale=1.)]
        active=[]
        for i,s in enumerate(segs):
            delay=max(0.0,self._fx_num(s.get('delay',0.0)))
            if playback in ('clip','full_clip','animate'):
                start=max(0,min(fc-1,int(self._fx_num(s.get('start_frame',b.get('segment_start_frame',0)),0))))
                span=int(self._fx_num(s.get('clip_frames',b.get('segment_clip_frames',0)),0));span=max(1,min(fc-start,span if span>0 else fc-start))
                life=max(1.0/fps,float(span)/fps)
                if not (delay<=t<delay+life):continue
                frame=start+max(0,min(span-1,int((t-delay)*fps)))
            else:
                life=max(1.0/fps,self._fx_num(s.get('hold',max(2.0/fps,.11)),max(2.0/fps,.11)))
                if not (delay<=t<delay+life):continue
                if 'frame' in s:frame=max(0,min(fc-1,int(self._fx_num(s.get('frame',0),0))))
                else:
                    phase=max(0.0,min(1.0,self._fx_num(s.get('phase',0.0))))
                    frame=max(0,min(fc-1,int(round(phase*(fc-1)))))
            active.append(dict(index=i,frame=frame,dx=self._fx_num(s.get('dx_tiles',0.0)),dy=self._fx_num(s.get('dy_tiles',0.0)),scale=self._fx_num(s.get('scale',1.0),1.0)))
        return active[-4:]

    def _fx_motion_map_point(self,dx,dy):
        stage=self.fx_motion_stage;w=max(1.0,float(stage.width));h=max(1.0,float(stage.height));segs=self._fx_motion_segments();mode=self._fx_motion_mode()
        if mode=='distance':
            _start,_duration,sx,sy,distx,disty=self._fx_motion_distance_values();xs=[0.0,sx,sx+distx];ys=[0.0,sy,sy+disty]
        else:
            xs=[0.0]+[self._fx_num(s.get('dx_tiles',0.0)) for s in segs];ys=[0.0]+[self._fx_num(s.get('dy_tiles',0.0)) for s in segs]
        minx=min(xs);maxx=max(xs);spanx=max(.5,maxx-minx);miny=min(ys);maxy=max(ys)
        mx=30.0;my=24.0
        x=mx+(float(dx)-minx)/spanx*max(1.0,w-2*mx)
        center_y=h*.60
        if maxy-miny<.01:y=center_y
        else:y=my+(float(dy)-miny)/max(1.0,maxy-miny)*max(1.0,h-2*my)
        return x,y

    def _set_fx_motion_sprite(self,slot,frame_index,dx,dy,scale,visible=True):
        if not (0<=slot<len(self.fx_motion_sprite_instances)):return
        holder,cells=self.fx_motion_sprite_instances[slot]
        if not visible:
            holder.hidden=True;return
        raw=self._fx_motion_raw();frames=raw.get('frames',[]) if isinstance(raw,dict) else []
        if not frames:
            holder.hidden=True;return
        fi=max(0,min(len(frames)-1,int(frame_index)));grid=self.model.resample_preview_pixels(frames[fi],10)
        size=max(30.0,min(66.0,48.0*max(.5,min(1.5,float(scale or 1.0)))))
        x,y=self._fx_motion_map_point(dx,dy);holder.frame=(x-size*.5,y-size*.5,size,size);holder.hidden=False
        cell=size/10.0
        for i,v in enumerate(cells):
            yy=i//10;xx=i%10;c=str(grid[yy][xx]);hidden=c.upper().endswith('00');v.hidden=hidden;v.frame=(xx*cell,yy*cell,cell+.1,cell+.1)
            if not hidden:v.background_color=color(c[:7])
        try:self.fx_motion_stage.bring_subview_to_front(holder)
        except Exception:pass

    def _refresh_fx_motion_stage(self,t=0.0,previewing=False):
        if self.fx_motion_stage is None:return
        segs=self._fx_motion_segments();mode=self._fx_motion_mode();active=self._fx_motion_active(max(0.0,float(t))) if isinstance(self.fx_motion_profile,dict) else []
        active_ids={int(a.get('index',-1)) for a in active}
        if mode=='distance':
            _start,_duration,sx,sy,dx,dy=self._fx_motion_distance_values()
            points=((sx,sy,'起'),(sx+dx,sy+dy,'終'))
            for i,m in enumerate(self.fx_motion_markers):
                if i>=2:m.hidden=True;continue
                px,py,label=points[i];x,y=self._fx_motion_map_point(px,py);m.text=label;m.frame=(x-9,y-9,18,18);m.hidden=False;m.background_color=BLUE if active else DARK2
        else:
            for i,m in enumerate(self.fx_motion_markers):
                m.text=str(i+1)
                if i>=len(segs):m.hidden=True;continue
                s=segs[i];x,y=self._fx_motion_map_point(self._fx_num(s.get('dx_tiles',0.0)),self._fx_num(s.get('dy_tiles',0.0)))
                m.frame=(x-7,y-7,14,14);m.hidden=False;m.background_color=BLUE if i in active_ids else DARK2
            if mode=='stationary' and not segs and self.fx_motion_markers:
                m=self.fx_motion_markers[0];x,y=self._fx_motion_map_point(0,0);m.text='1';m.frame=(x-7,y-7,14,14);m.hidden=False;m.background_color=BLUE if previewing else DARK2
        for slot in range(len(self.fx_motion_sprite_instances)):
            if slot<len(active):
                a=active[slot];self._set_fx_motion_sprite(slot,a['frame'],a['dx'],a['dy'],a['scale'],True)
            else:self._set_fx_motion_sprite(slot,0,0,0,1,False)

    def toggle_fx_motion_preview(self):
        if not self.fx_motion_visible:return
        if self.fx_motion_preview_playing:
            self._stop_fx_motion_preview();self._refresh_fx_motion_editor();return
        self.fx_motion_preview_playing=True;self.fx_motion_preview_generation+=1;gen=int(self.fx_motion_preview_generation)
        self.fx_motion_preview_time=0.0;self.fx_motion_preview_started=time.monotonic();self.fx_motion_preview_pending=False
        self.fx_motion_buttons['preview'].title='■ 停止預覽'
        self.fx_motion_preview_thread=threading.Thread(target=self._fx_motion_preview_loop,args=(gen,),daemon=True);self.fx_motion_preview_thread.start()

    def _stop_fx_motion_preview(self):
        self.fx_motion_preview_playing=False;self.fx_motion_preview_generation+=1;self.fx_motion_preview_pending=False;self.fx_motion_preview_time=0.0
        if 'preview' in self.fx_motion_buttons:self.fx_motion_buttons['preview'].title='▶ 實戰移動預覽'
        try:self._refresh_fx_motion_stage(0.0,False)
        except Exception:pass

    def _fx_motion_preview_loop(self,generation):
        duration=self._fx_motion_duration();last=-1.0
        while self.fx_motion_preview_playing and generation==self.fx_motion_preview_generation:
            time.sleep(.03);elapsed=max(0.0,time.monotonic()-self.fx_motion_preview_started)
            if elapsed>duration:
                if self._fx_motion_repeat()=='loop':
                    # Editor convenience: loop the whole preview so distance/speed
                    # can be inspected continuously.  Gameplay still traverses
                    # the authored distance exactly once per emitted attack.
                    self.fx_motion_preview_started=time.monotonic();elapsed=0.0;last=-1.0
                else:
                    try:mainthread.run_async(lambda gen=generation:self._finish_fx_motion_preview(gen))
                    except Exception:pass
                    return
            if elapsed-last<.06 or self.fx_motion_preview_pending:continue
            last=elapsed;self.fx_motion_preview_pending=True
            try:mainthread.run_async(lambda tt=elapsed,gen=generation:self._apply_fx_motion_preview(tt,gen))
            except Exception:self.fx_motion_preview_pending=False

    def _apply_fx_motion_preview(self,t,generation):
        self.fx_motion_preview_pending=False
        if not self.fx_motion_preview_playing or generation!=self.fx_motion_preview_generation or not self.fx_motion_visible:return
        self.fx_motion_preview_time=float(t);self._refresh_fx_motion_stage(t,True)

    def _finish_fx_motion_preview(self,generation):
        if generation!=self.fx_motion_preview_generation:return
        self.fx_motion_preview_playing=False;self.fx_motion_preview_pending=False;self.fx_motion_preview_time=0.0
        self.fx_motion_buttons['preview'].title='▶ 實戰移動預覽';self._refresh_fx_motion_stage(0.0,False)

    def _layout_fx_motion_editor(self):
        """FIX128 compact landscape layout for FX motion authoring.

        FIX127 stacked all global + per-segment controls vertically in the left
        column.  On iPhone landscape the logical UIKit height is much smaller
        than the screenshot pixel height, so Y/scale controls fell below the
        panel.  FIX128 uses two compact sub-columns: global timing on the left,
        selected-segment controls on the right.  Every control stays above the
        safe bottom even on ~400pt landscape heights; the preview remains fixed.
        """
        if self.fx_motion_overlay is None:return
        w=max(1.0,float(self.root.width));h=max(1.0,float(self.root.height))
        self.fx_motion_overlay.frame=(0,0,w,h);self.fx_motion_dismiss.frame=(0,0,w,h)
        # Keep a small safe margin around the panel instead of assuming 474pt
        # vertical room.  iPhone landscape is commonly only ~390-430pt high.
        pw=min(max(620.0,w-16.0),1040.0);ph=max(330.0,min(h-12.0,454.0))
        pw=min(pw,w-8.0);ph=min(ph,h-6.0)
        px=(w-pw)*.5;py=max(3.0,(h-ph)*.5);self.fx_motion_panel.frame=(px,py,pw,ph)

        left=max(390.0,min(500.0,pw*.48));gap=10.0;rx=left+gap;rw=max(210.0,pw-rx-12.0)
        self.fx_motion_title.frame=(12,6,pw-24,24)
        self.fx_motion_info.frame=(12,31,pw-24,32)

        # Left side: two columns so all 8 parameter rows fit without vertical
        # clipping.  This also leaves room for future motion parameters.
        top=69.0;inner_x=12.0;inner_w=left-22.0;col_gap=8.0;cw=(inner_w-col_gap)/2.0
        gx=inner_x;sx=inner_x+cw+col_gap
        self.fx_motion_labels['global'].frame=(gx,top,cw,18)
        self.fx_motion_labels['segment'].frame=(sx,top,cw,18)
        gy=top+20.0;sy=top+20.0

        # Global controls: mode row, segment add/delete row, then 4 compact triples.
        mode_w=max(90.0,cw*.61);repeat_w=max(70.0,cw-mode_w-4.0)
        self.fx_motion_buttons['mode'].frame=(gx,gy,mode_w,26)
        self.fx_motion_buttons['repeat'].frame=(gx+mode_w+4.0,gy,repeat_w,26);gy+=29
        half=(cw-4.0)/2.0
        self.fx_motion_buttons['seg_add'].frame=(gx,gy,half,25)
        self.fx_motion_buttons['seg_del'].frame=(gx+half+4,gy,half,25);gy+=28

        def compact_triple(x,y,minus,value,plus,width):
            bw=31.0;vw=max(62.0,width-2*bw-6.0)
            self.fx_motion_buttons[minus].frame=(x,y,bw,25)
            self.fx_motion_buttons[value].frame=(x+bw+3,y,vw,25)
            self.fx_motion_buttons[plus].frame=(x+bw+3+vw+3,y,bw,25)
            return y+28.0
        gy=compact_triple(gx,gy,'start_minus','start_value','start_plus',cw)
        gy=compact_triple(gx,gy,'interval_minus','interval_value','interval_plus',cw)
        gy=compact_triple(gx,gy,'step_minus','step_value','step_plus',cw)
        gy=compact_triple(gx,gy,'fps_minus','fps_value','fps_plus',cw)

        # Per-segment controls occupy the second column at the same height.
        nav_bw=35.0;counter_w=max(60.0,cw-2*nav_bw-6.0)
        self.fx_motion_buttons['seg_prev'].frame=(sx,sy,nav_bw,26)
        self.fx_motion_buttons['seg_counter'].frame=(sx+nav_bw+3,sy,counter_w,26)
        self.fx_motion_buttons['seg_next'].frame=(sx+nav_bw+3+counter_w+3,sy,nav_bw,26);sy+=29
        sy=compact_triple(sx,sy,'delay_minus','delay_value','delay_plus',cw)
        sy=compact_triple(sx,sy,'x_minus','x_value','x_plus',cw)
        sy=compact_triple(sx,sy,'y_minus','y_value','y_plus',cw)
        sy=compact_triple(sx,sy,'scale_minus','scale_value','scale_plus',cw)

        # Summary occupies the remaining left-side lower area; no control can be
        # hidden below it.  If height is very tight, the note simply gets shorter.
        note_y=max(gy,sy)+3.0
        note_h=max(26.0,min(48.0,ph-note_y-10.0))
        self.fx_motion_labels['note'].frame=(inner_x,note_y,inner_w,note_h)

        # Right preview and bottom action row remain stationary.
        self.fx_motion_labels['note'].number_of_lines=3
        stage_top=69.0
        action_h=30.0;action_gap=7.0
        stage_h=max(150.0,ph-stage_top-action_h-action_gap-12.0)
        self.fx_motion_stage.frame=(rx,stage_top,rw,stage_h)
        self.fx_motion_ground.frame=(8,stage_h*.60,rw-16,1.5)
        for holder,cells in self.fx_motion_sprite_instances:
            size=max(1.0,float(holder.width));cell=size/10.0
            for i,v in enumerate(cells):v.frame=((i%10)*cell,(i//10)*cell,cell+.1,cell+.1)
        by=stage_top+stage_h+action_gap;thirdr=(rw-8)/3.0
        self.fx_motion_buttons['preview'].frame=(rx,by,thirdr,action_h)
        self.fx_motion_buttons['apply'].frame=(rx+thirdr+4,by,thirdr,action_h)
        self.fx_motion_buttons['cancel'].frame=(rx+2*(thirdr+4),by,thirdr,action_h)
        self._refresh_fx_motion_stage(self.fx_motion_preview_time if self.fx_motion_preview_playing else 0.0,self.fx_motion_preview_playing)

    def _build_fx_frame_editor_panel(self):
        """FIX126: no duplicate bottom FX panel.

        FIX124/125 added a second, full-width bottom panel only to make the mode
        change unmistakable while debugging Pyto callback failures.  The normal
        inspector FX timeline is now proven to work, so keeping both consumes
        precious iPhone landscape height and duplicates every frame command.
        """
        self.fx_editor_panel=None
        self.fx_editor_title=None
        self.fx_editor_note=None
        self.fx_editor_buttons={}

    def _layout_fx_frame_editor_panel(self):
        # FIX126: integrated inspector timeline is the sole FX editor UI.
        return

    def _refresh_fx_frame_editor_panel(self):
        # FIX126: integrated inspector timeline is refreshed by refresh_labels().
        return

    def open_fx_frame_editor(self):
        if self.category[0]!="effect":return False
        self._stop_animation_preview(); self._commit_current_frame()
        raw=self.animations.get("effect") if isinstance(self.animations,dict) else None
        frames=raw.get("frames",[]) if isinstance(raw,dict) else []
        if not isinstance(frames,list) or not frames:
            self.labels["status"].text="這個效果素材沒有 effect 動畫幀；請先確認素材來源"
            return False
        self.fx_editor_active=True; self.animation_mode=True; self.animation_state="effect"
        self.animation_frame_index=max(0,min(int(self.animation_frame_index),len(frames)-1))
        self.pixels=copy.deepcopy(frames[self.animation_frame_index])
        try:self.canvas_size=int(raw.get("canvas_size",len(self.pixels)) or len(self.pixels))
        except Exception:self.canvas_size=len(self.pixels)
        self.undo_stack=[];self.selection=set();self._reset_selection_gesture()
        self._sync_active_canvas(recenter=True);self.refresh_pixels();self.refresh_labels()
        self.labels["status"].text=(f"已進入 FX 逐幀編輯｜第 {self.animation_frame_index+1}/{len(frames)} 幀｜"
                                    "使用右側整合時間軸切幀；左側大畫布就是目前單幀")
        try:self._layout()
        except Exception:pass
        return True

    def close_fx_frame_editor(self):
        if self.category[0]!="effect":return
        self._stop_animation_preview();self._commit_current_frame()
        self.fx_editor_active=False;self.animation_mode=False;self.animation_state="";self.animation_frame_index=0
        self.pixels=copy.deepcopy(self.base_pixels);self.selection=set();self.undo_stack=[]
        self._sync_active_canvas(recenter=True);self.refresh_pixels();self.refresh_labels()
        self.labels["status"].text="已離開 FX 逐幀編輯；按『逐幀編輯』可再次開啟"
        try:self._layout()
        except Exception:pass

    # ------------------------------------------------------------
    # FIX7: integrated multi-frame animation editor
    # ------------------------------------------------------------
    def _animation_supported(self):
        return self.category[0] in ("player","creature","plant","weapon","equipment","effect")

    def _animation_states(self):
        ordered=list(self.model.default_animation_states(self.category[0]))
        for name in self.animations.keys():
            if name not in ordered:ordered.append(name)
        return ordered

    @staticmethod
    def _animation_state_label(state):
        return {
            "idle":"站立", "walk":"行走", "run":"跑步",
            "crouch":"蹲下", "crouch_walk":"蹲走", "crouch_run":"蹲跑",
            "prone":"趴下", "crawl":"爬行",
            "roll":"翻滾",
            "jump":"跳躍", "fall":"下落", "climb":"攀爬",
            "move":"移動", "sway":"搖曳",
            "normal_attack":"一般攻擊", "heavy_charge":"重擊蓄力",
            "heavy_attack":"重攻擊", "normal_effect":"一般攻擊特效",
            "heavy_effect":"重攻擊特效", "effect":"特效動畫",
        }.get(str(state),str(state))

    def _current_animation(self):
        return self.animations.get(str(self.animation_state)) if self.animation_state else None

    def generate_creature_animation(self):
        """Generate idle/move loops directly from the current creature Base art.

        FIX26 regenerates only idle/move locomotion states; custom action clips remain untouched.
        Custom AI/action states remain untouched. A compressed full undo snapshot
        makes the operation reversible before the artist saves.
        """
        if self.category[0] != "creature":
            self.labels["status"].text="自動動畫目前提供給生物素材；角色／植物維持原本逐幀編輯"
            return
        self._stop_animation_preview()
        self._commit_current_frame()
        if not self.base_pixels:
            self.labels["status"].text="目前沒有可用的 Base 生物素材"
            return
        self._push_full_undo()
        try:
            species=str(self.asset_id).split(".",1)[1] if "." in str(self.asset_id) else str(self.asset_id)
            generated=generate_creature_animations(species,self.base_pixels)
            states=generated.get("states",{}) if isinstance(generated,dict) else {}
            if not isinstance(states,dict) or not states:
                raise ValueError("沒有產生動畫幀")
            # Only locomotion states are replaced. Authored attack/custom states
            # created by FIX19 remain exactly as the artist made them.
            for state in ("idle","move"):
                raw=states.get(state)
                if isinstance(raw,dict) and raw.get("frames"):
                    self.animations[state]=copy.deepcopy(raw)
            self.animation_mode=True
            self.animation_state="move" if "move" in self.animations else "idle"
            self.animation_frame_index=0
            raw=self._current_animation() or {}
            frames=raw.get("frames",[])
            self.pixels=copy.deepcopy(frames[0]) if frames else copy.deepcopy(self.base_pixels)
            self.selection=set(); self.onion_mode=0; self.dirty=True
            self._sync_active_canvas(recenter=True)
            self.refresh_pixels(); self.refresh_labels()
            try:self._layout()
            except Exception:pass
            profile=str(generated.get("profile","generic"))
            profile_zh={
                "quadruped":"四足獸", "low_quadruped":"低姿四足", "hopper":"跳躍型",
                "fish":"魚類", "snake":"蛇類", "bird":"鳥類", "arthropod":"節肢類",
                "flying_insect":"飛行昆蟲", "slime":"史萊姆", "biped":"雙足人形",
                "bat":"蝙蝠飛行", "worm":"蠕蟲",
                "generic":"通用",
            }.get(profile,profile)
            idle_count=len(self.animations.get("idle",{}).get("frames",[]))
            move_count=len(self.animations.get("move",{}).get("frames",[]))
            self.labels["status"].text=(
                f"自動動畫完成｜骨架：{profile_zh}\n"
                f"待機 {idle_count} 幀／移動 {move_count} 幀｜自動動畫 v5\n"
                "可直接播放預覽、逐幀修正；不滿意可按撤銷"
            )
        except Exception as exc:
            # Keep the just-created undo snapshot so the user can recover even
            # if a future generator profile fails halfway through.
            self.labels["status"].text="自動動畫失敗："+repr(exc)

    def _commit_current_frame(self):
        if not self.pixels:return
        if self.animation_mode:
            raw=self._current_animation()
            frames=raw.get("frames",[]) if isinstance(raw,dict) else []
            if frames and 0<=self.animation_frame_index<len(frames):
                frames[self.animation_frame_index]=copy.deepcopy(self.pixels)
                if "canvas_size" in raw or self.canvas_size!=self.asset_canvas_size:
                    raw["canvas_size"]=self.canvas_size
        else:
            self.base_pixels=copy.deepcopy(self.pixels)
            self.asset_canvas_size=len(self.pixels)

    def _ensure_animation_state(self,state):
        state=str(state)
        raw=self.animations.get(state)
        if not isinstance(raw,dict) or not isinstance(raw.get("frames"),list) or not raw.get("frames"):
            defaults={
                "idle":4.0,"walk":9.0,"run":12.0,
                "crouch":4.0,"crouch_walk":8.0,"crouch_run":11.0,
                "prone":4.0,"crawl":7.0,
                "roll":10.0,
                "jump":6.0,"fall":6.0,"climb":8.0,
                "move":8.0,"sway":3.0,
                "normal_attack":10.0,"heavy_charge":8.0,"heavy_attack":10.0,
                "normal_effect":12.0,"heavy_effect":12.0,
            }
            # Jump is a one-shot launch clip by default. Other states keep their
            # historical looping behaviour unless the artist toggles it.
            first_frame = (
                [[TRANSPARENT for _x in range(DEFAULT_VFX_CANVAS_SIZE)] for _y in range(DEFAULT_VFX_CANVAS_SIZE)]
                if state in ("normal_effect","heavy_effect") else copy.deepcopy(self.base_pixels)
            )
            self.animations[state]={
                "fps":defaults.get(state,6.0),
                "loop":False if state in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect") else True,
                "frames":[first_frame],
                "events":{},
            }
            if state in EFFECT_STATES:self.animations[state]["canvas_size"]=DEFAULT_VFX_CANVAS_SIZE
            self.dirty=True
        else:
            raw.setdefault("events",{})
            if "loop" not in raw:raw["loop"]=(state not in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect"))
        return self.animations[state]

    def _current_event_frame(self,name):
        raw=self._current_animation() or {}; events=raw.get("events",{})
        try:return int(events.get(str(name))) if str(name) in events else None
        except Exception:return None

    def _keyframe_label(self,name):
        if (
            str(name)=="action" and self.category[0]=="weapon" and
            str(self.animation_state) in ("normal_attack","heavy_attack")
        ):
            return "攻擊幀"
        return {
            "link":"銜接", "loop_start":"循環起", "loop_end":"循環終",
            "action":"動作", "takeoff":"起跳",
            "effect":"特效", "impact":"撞擊", "projectile":"投射",
        }.get(str(name),str(name))

    def _available_keyframe_types(self):
        rows=[name for name,_label in KEYFRAME_MARKERS]
        if self.animation_state=="jump":rows.append("takeoff")
        if self.category[0]=="weapon" and self.animation_state in ("normal_attack","heavy_attack"):
            # Gameplay and presentation markers are stored in the same event
            # dictionary as the existing link/loop markers. Runtime reads them
            # directly, so artists can move hit/effect timing without Python.
            rows.extend(("effect","impact","projectile"))
        if self.category[0]=="creature" and self.animation_state in ("attack","special"):
            rows.append("effect")
        return rows

    def _ensure_keyframe_type(self):
        rows=self._available_keyframe_types()
        if self.keyframe_marker_type not in rows:
            self.keyframe_marker_type=rows[0] if rows else "link"
        return self.keyframe_marker_type

    def cycle_keyframe_type(self):
        if not self.animation_mode:return
        self._stop_animation_preview(); rows=self._available_keyframe_types()
        if not rows:return
        current=self._ensure_keyframe_type(); idx=(rows.index(current)+1)%len(rows)
        self.keyframe_marker_type=rows[idx]; self.refresh_labels()
        name=self._keyframe_label(self.keyframe_marker_type)
        self.labels["status"].text=f"關鍵幀類型：{name}｜切到目標幀後按『設{name}』"

    def toggle_animation_event(self):
        """Toggle the selected generic keyframe marker on the current frame.

        ``link`` is the preferred transition entry/synchronisation pose.  When
        no explicit loop_start exists it also becomes the implicit loop start,
        so an intro can play once and the stable pose can loop afterwards.
        ``loop_start``/``loop_end`` bound the repeating segment. ``action`` is a
        generic gameplay hook reserved for attack/tool/magic events. ``takeoff``
        remains available on jump and keeps the FIX14 physics synchronisation.
        """
        if not self.animation_mode:return
        marker=self._ensure_keyframe_type(); raw=self._current_animation()
        if not raw:return
        self._push_state_undo(); raw=self._current_animation(); events=raw.setdefault("events",{})
        current=events.get(marker); idx=int(self.animation_frame_index)
        label=self._keyframe_label(marker)
        if current is not None and int(current)==idx:
            events.pop(marker,None)
            self.labels["status"].text=f"已取消第 {idx+1} 幀的『{label}』關鍵幀"
        else:
            events[marker]=idx
            # Keep explicit loop bounds valid while editing. Moving one endpoint
            # past the other collapses the opposite endpoint to the same frame
            # rather than saving an impossible range.
            if marker=="loop_start" and "loop_end" in events:
                try:
                    if int(events["loop_end"])<idx:events["loop_end"]=idx
                except Exception:events["loop_end"]=idx
            elif marker=="loop_end" and "loop_start" in events:
                try:
                    if int(events["loop_start"])>idx:events["loop_start"]=idx
                except Exception:events["loop_start"]=idx
            self.labels["status"].text=f"第 {idx+1} 幀已設為 ★{label} 關鍵幀"
        if marker=="effect" and self.category[0]=="weapon":
            self._mark_current_effect_authored(attack_state=str(self.animation_state))
        self.dirty=True; self.refresh_labels()

    def toggle_attack_frame(self):
        """Set/clear the authoritative gameplay action frame for weapon or creature."""
        _cat=self.category[0]; _state=str(self.animation_state)
        valid=(_cat=="weapon" and _state in ("normal_attack","heavy_attack")) or (_cat=="creature" and _state in ("attack","special"))
        if not (self.animation_mode and valid):
            self.labels["status"].text="攻擊幀只能在武器一般/重攻，或生物 attack/special 動畫中設定";return
        self._stop_animation_preview();raw=self._current_animation()
        if not isinstance(raw,dict):return
        self._push_state_undo();raw=self._current_animation();events=raw.setdefault("events",{})
        idx=int(self.animation_frame_index);current=events.get("action")
        try:same=(current is not None and int(current)==idx)
        except Exception:same=False
        if same:
            events.pop("action",None)
            self.labels["status"].text="已清除攻擊幀"
        else:
            events["action"]=idx
            self.labels["status"].text=f"第 {idx+1} 幀已設為 ★攻擊幀｜傷害判定與特效關鍵幀可獨立調整"
        self.dirty=True;self.refresh_labels()

    def _current_fx_frame_meta(self,create=False):
        if not (self.animation_mode and self._is_vfx_state()):return None
        raw=self._current_animation()
        if not isinstance(raw,dict):return None
        rows=raw.get("frame_events")
        if not isinstance(rows,dict):
            if not create:return None
            rows={};raw["frame_events"]=rows
        key=str(int(self.animation_frame_index))
        meta=rows.get(key)
        if not isinstance(meta,dict) and create:
            meta={};rows[key]=meta
        return meta if isinstance(meta,dict) else None

    def toggle_fx_damage_frame(self):
        """Mark/unmark the current authored FX frame as a gameplay damage beat.

        FIX120 keeps this metadata on the effect flipbook itself.  FIX103 boss
        heavy attacks read these markers at runtime, so moving a marker in the
        editor moves the corresponding heavy-hit timing without editing Python.
        """
        if not (self.animation_mode and self._is_vfx_state()):
            self.labels["status"].text="請先開啟攻擊特效的 effect 動畫，再設定 FX 傷害幀";return
        self._stop_animation_preview();self._commit_current_frame();self._push_state_undo()
        raw=self._current_animation();rows=raw.setdefault("frame_events",{})
        key=str(int(self.animation_frame_index));meta=rows.get(key)
        if not isinstance(meta,dict):meta={};rows[key]=meta
        enabled=not bool(meta.get("damage",False))
        if enabled:meta["damage"]=True
        else:
            meta.pop("damage",None)
            if not meta:rows.pop(key,None)
        if self.category[0]=="weapon":self._mark_current_effect_authored()
        self.dirty=True;self.refresh_labels()
        self.labels["status"].text=(f"第 {self.animation_frame_index+1} 幀已設為 ★FX傷害幀｜實際判定會跟此幀同步" if enabled else f"第 {self.animation_frame_index+1} 幀已清除 FX 傷害標記")

    def _current_attack_state_for_vfx(self):
        if self.category[0]=="creature":
            return "special" if str(self.animation_state)=="special" else "attack"
        attack,_effect=self._paired_weapon_states();return attack

    def _external_effect_binding(self,create=True):
        if self.category[0] not in ("weapon","creature"):return None
        attack=self._current_attack_state_for_vfx()
        raw=self.combat_bindings.get(attack) if isinstance(self.combat_bindings,dict) else None
        if not isinstance(raw,dict) and create:
            raw={"effect_asset":"","effect_state":"effect","trigger_event":"effect","anchor":"actor" if self.category[0]=="creature" else "weapon",
                 "offset_x":0.0,"offset_y":0.0,"scale":1.0,"render_mode":"authored","motion":"follow"}
            self.combat_bindings.setdefault(attack,raw)
        return raw if isinstance(raw,dict) else None

    def _ensure_creature_effect_asset(self):
        if self.category[0]!="creature":return None
        attack=self._current_attack_state_for_vfx();binding=self._external_effect_binding(True)
        existing=str(binding.get("effect_asset","") or "")
        if existing:return existing
        slug=str(self.asset_id).split(".",1)[-1]
        suffix="heavy" if attack=="special" else "normal"
        aid="effect.%s_%s"%(slug,suffix)
        # FIX119: creature/boss effects are authored as native FX canvases,
        # not tiny placeholders.  Normal attacks default to 96², specials to
        # 128², so artists can keep pixel density aligned with 96² boss bodies
        # while still getting a larger local workspace for wide attacks.
        effect_size=128 if attack=="special" else 96
        try:
            if aid not in self.model.catalog.get("effect",[]):
                self.model.create_pixel_asset("effect",aid,"%s・%s攻擊特效"%(self.model.display_name(self.asset_id),"重" if attack=="special" else "一般"),size=effect_size)
        except Exception as exc:
            self.labels["status"].text="建立外部特效素材失敗："+str(exc);return None
        binding["effect_asset"]=aid;binding["effect_state"]="effect";binding["render_mode"]="authored"
        return aid

    def _select_asset_id(self,asset_id):
        aid=str(asset_id or "")
        if not aid:return False
        category=aid.split(".",1)[0]
        ids=[r[0] for r in self.categories]
        if category not in ids:return False
        self._commit_current_frame();self.category_index=ids.index(category)
        rows=self.asset_ids
        if aid not in rows:return False
        self.asset_index=rows.index(aid);self._load_selected();return True

    def _paired_weapon_states(self):
        st=str(self.animation_state)
        if st in ("heavy_attack","heavy_effect"):return "heavy_attack","heavy_effect"
        return "normal_attack","normal_effect"

    def _weapon_binding(self, create=True):
        if self.category[0]!="weapon":return None
        attack,effect=self._paired_weapon_states()
        raw=self.combat_bindings.get(attack) if isinstance(self.combat_bindings,dict) else None
        if not isinstance(raw,dict) and create:
            defaults=self.model._default_combat_bindings(self.asset_id); raw=copy.deepcopy(defaults.get(attack,{"effect_state":effect,"trigger_event":"effect","anchor":"weapon","offset_x":0.0,"offset_y":0.0,"scale":1.0}))
            self.combat_bindings.setdefault(attack,raw)
        return raw if isinstance(raw,dict) else None

    def _mark_current_effect_authored(self,attack_state=None):
        """Switch only the edited VFX pair away from the FIX54 default.

        Packaged weapons intentionally keep the exact FIX54 procedural effects.
        The moment an artist changes an effect frame, its timing, or its anchor,
        that one pair becomes data-driven and the runtime uses the saved VFX.
        """
        if self.category[0]!="weapon":return
        if attack_state is None and str(self.animation_state) not in ("normal_effect","heavy_effect"):
            return
        attack=str(attack_state or self._paired_weapon_states()[0])
        if attack not in ("normal_attack","heavy_attack"):
            attack="heavy_attack" if str(self.animation_state)=="heavy_effect" else "normal_attack"
        raw=self.combat_bindings.get(attack) if isinstance(self.combat_bindings,dict) else None
        if not isinstance(raw,dict):
            defaults=self.model._default_combat_bindings(self.asset_id)
            raw=copy.deepcopy(defaults.get(attack,{})); self.combat_bindings.setdefault(attack,raw)
        raw["render_mode"]="authored"

    def bind_effect_here(self):
        if self.category[0] not in ("weapon","creature") or not self.animation_mode:return
        state=str(self.animation_state)
        valid=(self.category[0]=="weapon" and state in ("normal_attack","heavy_attack","normal_effect","heavy_effect")) or (self.category[0]=="creature" and state in ("attack","special"))
        if not valid:
            self.labels["status"].text="請先切到攻擊動畫再綁定 VFX";return
        if self.category[0]=="creature":
            raw=self._current_animation();events=raw.setdefault("events",{}) if isinstance(raw,dict) else {}
            events["effect"]=int(self.animation_frame_index)
            aid=self._ensure_creature_effect_asset()
            binding=self._external_effect_binding(True)
            if aid:binding["effect_asset"]=aid
            binding["effect_state"]="effect";binding["trigger_event"]="effect";binding["render_mode"]="authored"
            self.dirty=True;self.refresh_labels()
            self.labels["status"].text=f"BOSS/生物第 {self.animation_frame_index+1} 幀 → {aid or '外部特效'}｜可切到外部 FX 逐幀編輯｜身體與特效已分離"
            return
        # Weapon: external shared-effect bindings stay external; legacy weapons
        # retain the old in-asset VFX track path.
        attack,effect=self._paired_weapon_states();binding=self._weapon_binding(True)
        ext=str(binding.get("effect_asset","") or "") if isinstance(binding,dict) else ""
        if ext:
            raw=self.animations.get(attack,{}) if isinstance(self.animations,dict) else {};events=raw.setdefault("events",{}) if isinstance(raw,dict) else {}
            events["effect"]=int(self.animation_frame_index);binding["trigger_event"]="effect";binding["render_mode"]="authored"
            self.dirty=True;self.refresh_labels();self.labels["status"].text=f"武器第 {self.animation_frame_index+1} 幀 → 共用 {ext}"
            return
        if state==effect:
            self.animation_state=attack;raw=self._ensure_animation_state(attack);self.animation_frame_index=min(self.animation_frame_index,len(raw.get("frames",[]))-1)
            self.pixels=copy.deepcopy(raw["frames"][self.animation_frame_index]);self._sync_active_canvas(recenter=True);self.refresh_pixels();self.refresh_labels()
        raw=self._current_animation();events=raw.setdefault("events",{}) if isinstance(raw,dict) else {};events["effect"]=int(self.animation_frame_index)
        binding=self._weapon_binding(True);binding["effect_state"]=effect;binding["trigger_event"]="effect";binding["render_mode"]="authored"
        self.dirty=True;self.refresh_labels()

    def toggle_paired_weapon_clip(self):
        if self.category[0] not in ("weapon","creature") or not self.animation_mode:return
        binding=self._external_effect_binding(False)
        ext=str((binding or {}).get("effect_asset","") or "")
        if self.category[0]=="creature" and not ext:ext=str(self._ensure_creature_effect_asset() or "")
        if ext:
            if self._select_asset_id(ext):
                self.labels["status"].text="已開啟遊戲實際特效來源："+ext
            return
        if self.category[0]=="weapon":
            self._commit_current_frame();attack,effect=self._paired_weapon_states();target=effect if str(self.animation_state)==attack else attack
            raw=self._ensure_animation_state(target);self.animation_state=target;self.animation_frame_index=0
            self.pixels=copy.deepcopy(raw.get("frames",[self.base_pixels])[0]);self.selection=set();self._reset_selection_gesture();self._sync_active_canvas(recenter=True);self.refresh_pixels();self.refresh_labels()

    def cycle_vfx_anchor(self):
        if self.category[0] not in ("weapon","creature"):return
        binding=self._external_effect_binding(True) if self.category[0]=="creature" else (self._weapon_binding(True) or self._external_effect_binding(True))
        rows=("actor","ground_forward","target","weapon","projectile") if self.category[0]=="creature" else ("weapon","hand","player","ground","projectile")
        cur=str(binding.get("anchor",rows[0]));idx=(rows.index(cur)+1)%len(rows) if cur in rows else 0
        binding["anchor"]=rows[idx];binding["render_mode"]="authored";self.dirty=True;self.refresh_labels()
        self.labels["status"].text="VFX 附著點已改為："+rows[idx]

    def toggle_combat_preview(self):
        self.combat_preview=not bool(self.combat_preview); self.refresh_labels(); self.refresh_preview(True)
        self.labels["status"].text="攻擊＋VFX 合成預覽："+("開" if self.combat_preview else "關")

    def _combat_preview_pixels(self):
        if not (self.combat_preview and self.category[0]=="weapon" and self.animation_mode):return self.pixels
        attack,effect=self._paired_weapon_states()
        binding=self._weapon_binding(True) or {}

        def blank(n):return [[TRANSPARENT for _x in range(int(n))] for _y in range(int(n))]

        def paste(stage,source,center_x,center_y):
            if not isinstance(source,list) or not source:return
            sh=len(source); sw=max((len(row) for row in source if isinstance(row,list)),default=0)
            ox=int(round(float(center_x)-(sw-1)*0.5)); oy=int(round(float(center_y)-(sh-1)*0.5))
            for sy,row in enumerate(source):
                if not isinstance(row,list):continue
                ty=oy+sy
                if not (0<=ty<len(stage)):continue
                for sx,c in enumerate(row):
                    tx=ox+sx; raw=str(c)
                    if 0<=tx<len(stage[ty]) and not raw.upper().endswith("00"):stage[ty][tx]=raw

        def stage_line(stage,x0,y0,x1,y1,color):
            x0=int(round(x0));y0=int(round(y0));x1=int(round(x1));y1=int(round(y1))
            dx=abs(x1-x0);sx=1 if x0<x1 else -1;dy=-abs(y1-y0);sy=1 if y0<y1 else -1;err=dx+dy
            while True:
                if 0<=y0<len(stage) and 0<=x0<len(stage[y0]):stage[y0][x0]=str(color)
                if x0==x1 and y0==y1:break
                e2=2*err
                if e2>=dy:err+=dy;x0+=sx
                if e2<=dx:err+=dx;y0+=sy

        # A projectile effect owns only its local flipbook. The editor stage
        # previews the whole authoritative path separately, so a 343-world-px
        # greatsword flight is visible without baking a huge mostly-empty image.
        if str(self.animation_state)==effect and str(binding.get("motion","follow"))=="projectile":
            wid=str(self.asset_id).split(".",1)[1] if "." in str(self.asset_id) else str(self.asset_id)
            weapon=WEAPON_DEFS.get(wid,{})
            raw=self._current_animation() or {}
            try:fps=max(1.0,float(raw.get("fps",12.0) or 12.0))
            except Exception:fps=12.0
            frame_count=max(1,len(raw.get("frames",[]) or []));phase=float(self.animation_frame_index)/float(max(1,frame_count-1))
            heavy_pair=(attack=="heavy_attack")
            stage_size=208 if wid in ("energy_bow","laser_gun","flying_drone") else 160
            stage=blank(stage_size);gy=stage_size//2
            for gx in range(10,stage_size-10,2):stage[gy][gx]="#31506A66"
            if wid!="flying_drone":paste(stage,self.base_pixels,16,gy)
            if wid=="energy_bow" and heavy_pair:
                # Explicit upward launch -> apex -> falling arrow-rain preview.
                points=[]
                for pi in range(49):
                    t=pi/48.0;x=18+t*(stage_size-38);y=gy-math.sin(t*math.pi)*stage_size*.34+t*18.0
                    points.append((x,y))
                for a,b in zip(points,points[1:]):stage_line(stage,a[0],a[1],b[0],b[1],"#43D9FF88")
                idx=min(len(points)-1,int(round(phase*(len(points)-1))));px,py=points[idx]
                paste(stage,self.pixels,px,py)
                # Staggered ghost paths communicate a dense volley, not one arrow.
                for offset in (8,16,24):
                    q=max(0,idx-offset);qx,qy=points[q]
                    if 0<=int(qy)<stage_size and 0<=int(qx)<stage_size:stage[int(qy)][int(qx)]="#A8F3FFFF"
            elif wid=="flying_drone":
                # Industry-style separation: the editable bitmap supplies the
                # local trail/explosion, while this stage previews the world
                # orbit -> dive -> impact/return motion without baking that
                # long path into a 24px or 64px source.
                origin=(24.0,float(gy-42));target=(float(stage_size-34),float(gy+25))
                stage_line(stage,origin[0],origin[1],target[0],target[1],"#277D9A66")
                for ti in range(7):
                    t=ti/6.0
                    tx=origin[0]+(target[0]-origin[0])*t;ty=origin[1]+(target[1]-origin[1])*t
                    if 0<=int(ty)<stage_size and 0<=int(tx)<stage_size:stage[int(ty)][int(tx)]="#45DDF588"
                if heavy_pair:
                    impact_at=.42
                    if phase<impact_at:
                        t=phase/impact_at
                        px=origin[0]+(target[0]-origin[0])*t;py=origin[1]+(target[1]-origin[1])*t
                        paste(stage,self.base_pixels,px,py)
                    else:
                        paste(stage,self.pixels,target[0],target[1])
                else:
                    if phase<=.58:
                        t=phase/.58
                    else:
                        t=max(0.0,1.0-(phase-.58)/.42)
                    px=origin[0]+(target[0]-origin[0])*t;py=origin[1]+(target[1]-origin[1])*t
                    paste(stage,self.base_pixels,px,py)
                    paste(stage,self.pixels,px,py)
            elif wid=="laser_gun":
                bounces=10 if heavy_pair else max(1,int(weapon.get("projectile_reflections",3) or 3))
                points=[(18.0,float(gy))]
                for bi in range(bounces+1):
                    x=float(stage_size-18 if bi%2==0 else 18)
                    y=float(18+((bi*37+23)%(stage_size-36)))
                    points.append((x,y))
                for a,b in zip(points,points[1:]):stage_line(stage,a[0],a[1],b[0],b[1],"#43E8FFAA")
                travel=phase*float(len(points)-1);seg=min(len(points)-2,int(travel));local=travel-seg
                a=points[seg];b=points[seg+1];px=a[0]+(b[0]-a[0])*local;py=a[1]+(b[1]-a[1])*local
                paste(stage,self.pixels,px,py)
            else:
                prefix="heavy_" if heavy_pair else ""
                speed=max(0.0,float(weapon.get(prefix+"projectile_speed",weapon.get("projectile_speed",0.0)) or 0.0))
                life=max(0.01,float(weapon.get(prefix+"projectile_life",weapon.get("projectile_life",.88)) or .88))
                spawn=max(0.0,float(weapon.get(prefix+"projectile_spawn_offset",weapon.get("projectile_spawn_offset",30.0)) or 30.0))
                scale=max(1.0,float(PIXEL_WORLD_SCALE));total=max(1.0,(spawn+speed*life)/scale)
                px=16.0+phase*min(stage_size-34.0,total)
                stage_line(stage,16,gy,stage_size-18,gy,"#6EB9D888")
                paste(stage,self.pixels,px,gy)
            return stage

        if str(self.animation_state)!=attack:return self.pixels
        araw=self.animations.get(attack,{}); eraw=self.animations.get(effect,{})
        aframes=araw.get("frames",[]) if isinstance(araw,dict) else []; eframes=eraw.get("frames",[]) if isinstance(eraw,dict) else []
        if not aframes or not eframes:return self.pixels
        events=araw.get("events",{}) if isinstance(araw.get("events",{}),dict) else {}
        trigger=events.get("effect",events.get("action",0))
        try:trigger=int(trigger)
        except Exception:trigger=0
        effect_size=max(1,int(eraw.get("canvas_size",len(eframes[0]) if eframes else self.canvas_size) or self.canvas_size))
        stage_size=max(len(self.pixels),effect_size); out=blank(stage_size)
        paste(out,self.pixels,(stage_size-1)*0.5,(stage_size-1)*0.5)
        if self.animation_frame_index<trigger:return out
        eidx=min(len(eframes)-1,max(0,self.animation_frame_index-trigger))
        eg=eframes[eidx]; ox=float(binding.get("offset_x",0.0) or 0.0)/max(1.0,float(PIXEL_WORLD_SCALE)); oy=float(binding.get("offset_y",0.0) or 0.0)/max(1.0,float(PIXEL_WORLD_SCALE))
        paste(out,eg,(stage_size-1)*0.5+ox,(stage_size-1)*0.5+oy)
        return out

    def toggle_animation_loop(self):
        if not self.animation_mode:return
        raw=self._current_animation()
        if not raw:return
        self._push_state_undo(); raw=self._current_animation()
        raw["loop"]=not bool(raw.get("loop",self.animation_state not in ("jump","roll","normal_attack","heavy_attack","normal_effect","heavy_effect")))
        if str(self.animation_state) in ("normal_effect","heavy_effect"):self._mark_current_effect_authored()
        self.dirty=True; self.refresh_labels()
        self.labels["status"].text="動畫循環："+("開" if raw["loop"] else "關")

    @staticmethod
    def _shift_events_after_insert(raw,index):
        events=raw.get("events",{}) if isinstance(raw,dict) else {}
        if isinstance(events,dict):
            for name,value in list(events.items()):
                try:value=int(value)
                except Exception:continue
                if value>=int(index):events[name]=value+1
        frame_events=raw.get("frame_events",{}) if isinstance(raw,dict) else {}
        if isinstance(frame_events,dict):
            shifted={}
            for key,meta in frame_events.items():
                try:k=int(key)
                except Exception:continue
                shifted[str(k+1 if k>=int(index) else k)]=meta
            raw["frame_events"]=shifted

    @staticmethod
    def _shift_events_after_delete(raw,index,new_count):
        events=raw.get("events",{}) if isinstance(raw,dict) else {}
        if isinstance(events,dict):
            for name,value in list(events.items()):
                try:value=int(value)
                except Exception:
                    events.pop(name,None); continue
                if value==int(index):
                    if new_count>0:events[name]=max(0,min(new_count-1,int(index)))
                    else:events.pop(name,None)
                elif value>int(index):events[name]=value-1
        frame_events=raw.get("frame_events",{}) if isinstance(raw,dict) else {}
        if isinstance(frame_events,dict):
            shifted={}
            for key,meta in frame_events.items():
                try:k=int(key)
                except Exception:continue
                if k==int(index):continue
                nk=k-1 if k>int(index) else k
                if 0<=nk<int(new_count):shifted[str(nk)]=meta
            raw["frame_events"]=shifted

    def toggle_animation_mode(self):
        # FIX124: effect assets use a real, explicit mode switch. Do not route
        # them through the generic inspector animation layout.
        if self.category[0]=="effect":
            if self.fx_editor_active:self.close_fx_frame_editor()
            else:self.open_fx_frame_editor()
            return
        self._stop_animation_preview(); self._commit_current_frame()
        if not self._animation_supported():
            self.labels["status"].text="此類別目前維持靜態素材；動畫模式提供給角色、生物、植物、武器"
            return
        if self.animation_mode:
            self.animation_mode=False; self.pixels=copy.deepcopy(self.base_pixels); self.animation_state=""; self.animation_frame_index=0
            self.labels["status"].text="已回到 Base 素材；Base 是沒有動畫狀態時的安全回退圖"
        else:
            states=self._animation_states()
            # FIX121 effect assets are real flipbooks.  Enter their authoritative
            # `effect` state directly instead of treating the representative Base
            # image as another animation/source sheet.
            self.animation_state=("effect" if self.category[0]=="effect" and "effect" in states else (states[0] if states else "idle"))
            raw=self._ensure_animation_state(self.animation_state); self.animation_mode=True; self.animation_frame_index=0
            self.pixels=copy.deepcopy(raw["frames"][0])
            if self.category[0]=="effect":
                self.labels["status"].text=f"FX 逐幀編輯：第 1/{len(raw.get('frames',[]))} 幀｜這裡就是遊戲實際使用的特效來源，可逐幀繪製／複製／刪除"
            else:
                self.labels["status"].text=f"動畫模式：{self.animation_state}｜可複製幀後用洋蔥皮調整"
        self.undo_stack=[]; self.selection=set(); self._sync_active_canvas(recenter=True); self.refresh_pixels(); self.refresh_labels()
        try:self._layout()
        except Exception:pass

    def cycle_animation_state(self):
        if not self._animation_supported():return
        self._stop_animation_preview(); self._commit_current_frame()
        states=self._animation_states()
        if not states:return
        current=self.animation_state if self.animation_state in states else states[0]
        idx=(states.index(current)+1)%len(states)
        self.animation_state=states[idx]; raw=self._ensure_animation_state(self.animation_state)
        self.animation_mode=True; self.animation_frame_index=0; self.pixels=copy.deepcopy(raw["frames"][0])
        self.undo_stack=[]; self.selection=set(); self._sync_active_canvas(recenter=True); self.refresh_pixels(); self.refresh_labels()
        # FIX59 inspector controls depend on the selected state: attack clips
        # show the dedicated damage-frame button, while effect clips show the
        # VFX viewport controls. Same-sized pose clips do not trigger a canvas
        # resize, so refresh their visibility explicitly after every state tap.
        try:self._layout()
        except Exception:pass
        self.labels["status"].text=f"動畫狀態：{self._animation_state_label(self.animation_state)}（{self.animation_state}）｜{len(raw['frames'])} 幀"

    def change_animation_frame(self,delta):
        if not self.animation_mode:return
        self._stop_animation_preview(); self._commit_current_frame(); raw=self._current_animation()
        frames=raw.get("frames",[]) if raw else []
        if not frames:return
        self.animation_frame_index=(self.animation_frame_index+int(delta))%len(frames)
        self.pixels=copy.deepcopy(frames[self.animation_frame_index]); self.undo_stack=[]; self.selection=set()
        self._sync_active_canvas(recenter=False); self.refresh_pixels(); self.refresh_labels()

    def add_animation_frame(self,duplicate=True):
        if not self.animation_mode:
            self.toggle_animation_mode()
            if not self.animation_mode:return
        self._stop_animation_preview(); self._commit_current_frame(); self._push_state_undo(); raw=self._current_animation(); frames=raw["frames"]
        frame=copy.deepcopy(self.pixels) if duplicate else [[TRANSPARENT for _x in range(self.canvas_size)] for _y in range(self.canvas_size)]
        insert_at=self.animation_frame_index+1
        self._shift_events_after_insert(raw,insert_at)
        frames.insert(insert_at,frame); self.animation_frame_index=insert_at; self.pixels=copy.deepcopy(frame)
        if str(self.animation_state) in ("normal_effect","heavy_effect"):self._mark_current_effect_authored()
        # First jump duplicate gets a sensible launch marker automatically. It
        # remains fully editable: tap 『起跳幀』 on any frame to move the marker.
        if self.animation_state=="jump" and len(frames)==2 and "takeoff" not in raw.setdefault("events",{}):
            raw["events"]["takeoff"]=1
        self.selection=set(); self.dirty=True; self.refresh_pixels(); self.refresh_labels()
        self.labels["status"].text=("已複製目前幀" if duplicate else "已新增空白幀")+f"｜第 {insert_at+1}/{len(frames)} 幀"

    def delete_animation_frame(self):
        if not self.animation_mode:return
        self._stop_animation_preview(); self._commit_current_frame(); raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
        if len(frames)<=1:
            self.labels["status"].text="每個動畫狀態至少保留 1 幀；可改畫或切回 Base"
            return
        self._push_state_undo(); raw=self._current_animation(); frames=raw.get("frames",[])
        deleted=int(self.animation_frame_index); frames.pop(deleted)
        self._shift_events_after_delete(raw,deleted,len(frames))
        self.animation_frame_index=min(deleted,len(frames)-1)
        self.pixels=copy.deepcopy(frames[self.animation_frame_index]); self.selection=set(); self.dirty=True
        if str(self.animation_state) in ("normal_effect","heavy_effect"):self._mark_current_effect_authored()
        self.refresh_pixels(); self.refresh_labels(); self.labels["status"].text="已刪除動畫幀"

    def cycle_onion_skin(self):
        if not self.animation_mode:return
        self.onion_mode=(int(self.onion_mode)+1)%3
        self.refresh_pixels(); self.refresh_labels()

    def adjust_animation_fps(self,delta):
        if not self.animation_mode:return
        raw=self._current_animation()
        if not raw:return
        self._push_state_undo(); raw=self._current_animation()
        try:fps=float(raw.get("fps",6.0))
        except Exception:fps=6.0
        raw["fps"]=max(1.0,min(30.0,fps+float(delta))); self.dirty=True; self.refresh_labels()
        if str(self.animation_state) in ("normal_effect","heavy_effect"):self._mark_current_effect_authored()

    def _stop_animation_preview(self):
        self.animation_playing=False
        self._animation_preview_generation+=1
        self._animation_ui_pending=False
        if "anim_play" in self.buttons:self.buttons["anim_play"].title="▶ 播放"

    def toggle_animation_preview(self):
        if not self.animation_mode:return
        self._commit_current_frame(); raw=self._current_animation()
        if not raw or len(raw.get("frames",[]))<2:
            self.labels["status"].text="至少需要 2 幀才能播放預覽"
            return
        if self.animation_playing:
            self._stop_animation_preview(); return
        self.animation_playing=True; self._animation_preview_generation+=1; self._animation_ui_pending=False
        self.buttons["anim_play"].title="■ 停止"
        generation=int(self._animation_preview_generation)
        self._animation_stop=False
        # A timing thread is cheap and safe as long as it NEVER touches UIKit.
        # Every pixel/button update is marshalled back through mainthread.
        self._animation_thread=threading.Thread(target=self._animation_preview_loop,args=(generation,),daemon=True)
        self._animation_thread.start()

    def _apply_animation_preview_frame(self,index,generation):
        self._animation_ui_pending=False
        if generation!=self._animation_preview_generation or not self.animation_playing or not self.animation_mode:return
        raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
        if not frames:return
        index=max(0,min(len(frames)-1,int(index)))
        if index==self.animation_frame_index:return
        self.animation_frame_index=index; self.pixels=copy.deepcopy(frames[index]); self.selection=set()
        self.refresh_pixels(); self.refresh_labels()

    def _animation_preview_loop(self,generation):
        # Background thread: timing/math only. No pyto_ui / UIKit calls here.
        started=time.monotonic(); last_sent=-1; last_dispatch=0.0
        # Full authored frames remain at their real FPS in the game. The editor
        # caps only UIKit redraw frequency for large canvases to avoid saturating
        # Pyto's main thread while still sampling the correct animation time.
        ui_hz=8.0 if self.canvas_size>=32 else (12.0 if self.canvas_size>=24 else 16.0)
        min_dispatch=1.0/ui_hz
        while not self._animation_stop and generation==self._animation_preview_generation:
            time.sleep(0.015)
            if not self.animation_playing or not self.animation_mode:continue
            raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
            if len(frames)<2:continue
            try:fps=max(1.0,float(raw.get("fps",6.0) or 6.0))
            except Exception:fps=6.0
            loop=bool(raw.get("loop",self.animation_state not in ("jump","roll")))
            events=raw.get("events",{}) if isinstance(raw.get("events",{}),dict) else {}
            link=events.get("link")
            try:loop_start=int(events.get("loop_start",link if link is not None else 0))
            except Exception:loop_start=0
            try:loop_end=int(events.get("loop_end",len(frames)-1))
            except Exception:loop_end=len(frames)-1
            loop_start=max(0,min(len(frames)-1,loop_start)); loop_end=max(loop_start,min(len(frames)-1,loop_end))
            elapsed=max(0.0,time.monotonic()-started)
            raw_index=int(elapsed*fps)
            if loop and raw_index>loop_end:
                span=max(1,loop_end-loop_start+1)
                index=loop_start+((raw_index-(loop_end+1))%span)
            else:index=min(raw_index,len(frames)-1)
            if index==last_sent:continue
            now=time.monotonic()
            if now-last_dispatch<min_dispatch:continue
            last_sent=index; last_dispatch=now
            if self._animation_ui_pending:continue
            self._animation_ui_pending=True
            try:
                mainthread.run_async(lambda idx=index,gen=generation:self._apply_animation_preview_frame(idx,gen))
            except Exception:
                self._animation_ui_pending=False
            if (not loop) and index>=len(frames)-1:
                # Leave the last frame visible, then stop from the main thread.
                try:mainthread.run_async(lambda gen=generation:self._finish_nonloop_preview(gen))
                except Exception:pass
                return

    def _finish_nonloop_preview(self,generation):
        if generation!=self._animation_preview_generation:return
        raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
        if frames:
            last=len(frames)-1
            if self.animation_frame_index!=last:
                self.animation_frame_index=last; self.pixels=copy.deepcopy(frames[last]); self.selection=set()
                self.refresh_pixels(); self.refresh_labels()
        self._animation_ui_pending=False
        self.animation_playing=False; self.buttons["anim_play"].title="▶ 播放"

    def _onion_color(self,x,y):
        if not self.animation_mode or self.onion_mode<=0:return None
        raw=self._current_animation(); frames=raw.get("frames",[]) if raw else []
        if len(frames)<2:return None
        indices=[(self.animation_frame_index-1)%len(frames)]
        if self.onion_mode>=2:indices.append((self.animation_frame_index+1)%len(frames))
        cols=[]
        for idx in indices:
            try:c=str(frames[idx][y][x])
            except Exception:continue
            if not c.upper().endswith("00"):cols.append(c)
        if not cols:return None
        c=_blend_hex(cols[0],"#18212A",0.38)
        if len(cols)>1:c=_blend_hex(cols[1],c,0.25)
        return c

    def _root_owner_view_controller(self):
        try: responder=self.root.__py_view__.managed
        except Exception:return None
        for _ in range(16):
            try:responder=responder.nextResponder
            except Exception:responder=None
            if responder is None:return None
            try:
                getattr(responder,"presentingViewController")
                getattr(responder,"dismissViewControllerAnimated")
                getattr(responder,"view")
                return responder
            except Exception:pass
        return None

    def _top_presented_view_controller(self):
        try:
            native=self.root.__py_view__.managed; window=native.window
            if window is None:return None
            controller=window.rootViewController
            if controller is None:return None
            for _ in range(16):
                try:presented=controller.presentedViewController
                except Exception:presented=None
                if presented is None:break
                controller=presented
            return controller
        except Exception:return None

    def _dismiss_exact_controller_on_main(self,allow_top_fallback=False):
        try:
            owner=self._root_owner_view_controller()
            if owner is not None:
                try:presenter=owner.presentingViewController
                except Exception:presenter=None
                if presenter is not None:
                    presenter.dismissViewControllerAnimated(False,completion=None); return True
                owner.dismissViewControllerAnimated(False,completion=None); return True
            if not allow_top_fallback:return False
            controller=self._top_presented_view_controller()
            if controller is None:return False
            try:presenter=controller.presentingViewController
            except Exception:presenter=None
            if presenter is None:return False
            presenter.dismissViewControllerAnimated(False,completion=None); return True
        except Exception as exc:
            print("ASSET EDITOR CLOSE ERROR:",repr(exc)); return False

    def _close_retry_worker(self):
        if self._close_completed.wait(0.35):return
        try:mainthread.run_async(lambda:self._dismiss_exact_controller_on_main(False))
        except Exception:pass
        if self._close_completed.wait(0.55):return
        try:mainthread.run_async(lambda:self._dismiss_exact_controller_on_main(True))
        except Exception:pass
        if self._close_completed.wait(0.80):return
        self._closing=False
        print("ASSET EDITOR CLOSE: not confirmed; X re-armed")

    def close_editor(self):
        """Use the same non-blocking UIKit dismissal strategy as main.py."""
        if self._closing:return True
        self._closing=True; self._close_completed.clear()
        self._animation_stop=True; self.animation_playing=False; self._animation_preview_generation+=1; self._animation_ui_pending=False
        try:
            if self.metal_canvas is not None:self.metal_canvas.close()
        except Exception:pass
        self.close_color_picker()
        self.close_state_creator()
        self.close_rotation_editor(False)
        try:mainthread.run_async(lambda:self._dismiss_exact_controller_on_main(False))
        except Exception as exc:
            self._closing=False; print("ASSET EDITOR CLOSE SCHEDULE ERROR:",repr(exc)); return False
        try:
            threading.Thread(target=self._close_retry_worker,name="AssetEditorCloseRetry",daemon=True).start()
        except Exception:pass
        return True

    def reload_builtin(self):
        self._stop_animation_preview()
        try:
            if not self.animation_mode:
                # Parse and measure before touching the active frame. A bad or
                # oversized source cannot erase the already-open drawing.
                imported=self.model.make_builtin_template(self.asset_id)
                n=len(imported)
                self._push_full_undo()
                self.base_pixels=copy.deepcopy(imported);self.pixels=copy.deepcopy(imported)
                self.asset_canvas_size=n
                # Existing animation states / effects are preserved independently.
                self.labels["status"].text=f"已匯入完整來源 {n}×{n}｜原像素 1:1 保留；不是套入上一個素材的畫布"
            else:
                self._push_state_undo()
                if self.category[0]=="weapon":
                    defaults=self.model._default_weapon_animations(self.asset_id,self.base_pixels)
                    raw=defaults.get(str(self.animation_state)) if isinstance(defaults,dict) else None
                    if isinstance(raw,dict) and raw.get("frames"):
                        self.animations[str(self.animation_state)]=copy.deepcopy(raw)
                        self.animation_frame_index=0;self.pixels=copy.deepcopy(raw["frames"][0])
                        self.combat_bindings=self.model._default_combat_bindings(self.asset_id)
                    else:self.pixels=copy.deepcopy(self.base_pixels)
                else:self.pixels=copy.deepcopy(self.base_pixels)
                self.labels["status"].text="已重新匯入目前動畫幀；畫布依來源尺寸調整，其他狀態保留"
            self.selection=set();self._reset_selection_gesture()
            self._sync_active_canvas(recenter=True)
            self.dirty=True;self.refresh_pixels();self.refresh_labels()
        except (CanvasImportError,OSError,ValueError) as exc:
            self.labels["status"].text="匯入失敗，未改動原稿："+str(exc)

    # ------------------------------------------------------------
    # FIX5/FIX15: selection, move, cut/copy/paste + non-occluding frame
    # ------------------------------------------------------------
    def toggle_selection_occlusion(self):
        self.selection_transparent_mode=not bool(self.selection_transparent_mode)
        self.refresh_labels()
        if self.selection_transparent_mode:
            self.labels["status"].text="選區模式：不遮擋｜透明像素不會清除下方內容"
        else:
            self.labels["status"].text="選區模式：遮擋｜透明像素也會覆蓋／清除下方內容"

    # Old internal name kept harmlessly for any already-open Pyto callback.
    def toggle_selection_overlay(self):
        return self.toggle_selection_occlusion()

    def _update_selection_outline(self):
        edges=list(getattr(self,"selection_outline_edges",()) or ())
        handles=list(getattr(self,"selection_outline_handles",()) or ())
        if len(edges)<4:return
        if not self.selection:
            for view in edges+handles:
                try:view.hidden=True
                except Exception:pass
            return
        box=self._selection_bbox()
        if box is None:
            for view in edges+handles:
                try:view.hidden=True
                except Exception:pass
            return
        try:
            x0,y0,x1,y1=box
            ow=max(1.0,float(getattr(self.overlay,"width",1.0))); oh=max(1.0,float(getattr(self.overlay,"height",1.0)))
            grid=self._display_grid_size(); vx=int(self.canvas_view_x); vy=int(self.canvas_view_y)
            ix0=max(int(x0),vx); iy0=max(int(y0),vy)
            ix1=min(int(x1),vx+grid-1); iy1=min(int(y1),vy+grid-1)
            if ix0>ix1 or iy0>iy1:
                for view in edges+handles:view.hidden=True
                return
            cw=ow/max(1,grid); ch=oh/max(1,grid)
            # Edges are children of overlay, therefore these are deliberately
            # local coordinates. This remains aligned in fullscreen, rotation
            # and Stage Manager without relying on Pyto's root x/y wrappers.
            left=(ix0-vx)*cw; top=(iy0-vy)*ch
            right=(ix1-vx+1)*cw; bottom=(iy1-vy+1)*ch
            thickness=max(2.0,min(3.0,min(cw,ch)*0.18)); width=max(thickness,right-left); height=max(thickness,bottom-top)
            frames=(
                (left,top,width,thickness),
                (right-thickness,top,thickness,height),
                (left,bottom-thickness,width,thickness),
                (left,top,thickness,height),
            )
            for edge,frame in zip(edges,frames):
                edge.frame=frame; edge.background_color=GOLD; edge.hidden=False
                self.overlay.bring_subview_to_front(edge)
            handle_size=max(6.0,min(10.0,min(cw,ch)*0.72))
            corners=((left,top),(right,top),(left,bottom),(right,bottom))
            for handle,(hx,hy) in zip(handles,corners):
                handle.frame=(max(0.0,min(ow-handle_size,hx-handle_size*0.5)),max(0.0,min(oh-handle_size,hy-handle_size*0.5)),handle_size,handle_size)
                handle.hidden=False; self.overlay.bring_subview_to_front(handle)
        except Exception:
            pass

    def _reset_selection_gesture(self):
        self._selection_gesture_mode=None
        self._selection_anchor=None
        self._selection_brush_last=None
        self._selection_move_source=set()
        self._selection_move_base=None
        self._selection_move_last_delta=(0,0)
        self._gesture_original_selection=None
        self._gesture_original_dirty=self.dirty

    @staticmethod
    def _gesture_finished(kind,state):
        return kind=="tap" or int(state) in (3,4,5)

    @staticmethod
    def _line_cells(x0,y0,x1,y1):
        """Integer Bresenham so fast finger drags never leave gaps."""
        x0=int(x0); y0=int(y0); x1=int(x1); y1=int(y1)
        out=[]; dx=abs(x1-x0); sx=1 if x0<x1 else -1
        dy=-abs(y1-y0); sy=1 if y0<y1 else -1; err=dx+dy
        while True:
            out.append((x0,y0))
            if x0==x1 and y0==y1:break
            e2=2*err
            if e2>=dy:err+=dy; x0+=sx
            if e2<=dx:err+=dx; y0+=sy
        return out

    def _refresh_selection_cells(self,old_selection=None):
        # FIX98: one publish/draw, not up to 16,384 UIKit/Metal dispatches.
        self.refresh_pixels()

    def clear_selection(self):
        if not self.selection:
            self.labels["status"].text="目前沒有選取範圍"
            return
        old=set(self.selection); self.selection=set(); self._reset_selection_gesture()
        self._refresh_selection_cells(old); self.refresh_labels()
        self.labels["status"].text="已取消選取"

    def _selection_bbox(self,selection=None):
        sel=set(self.selection if selection is None else selection)
        if not sel:return None
        xs=[p[0] for p in sel]; ys=[p[1] for p in sel]
        return min(xs),min(ys),max(xs),max(ys)

    def _clipboard_payload(self):
        box=self._selection_bbox()
        if box is None:return None
        x0,y0,x1,y1=box; w=x1-x0+1; h=y1-y0+1
        mask=[]; pixels=[]
        for yy in range(h):
            mrow=[]; prow=[]
            for xx in range(w):
                sx=x0+xx; sy=y0+yy; chosen=(sx,sy) in self.selection
                mrow.append(bool(chosen))
                prow.append(str(self.pixels[sy][sx]) if chosen else TRANSPARENT)
            mask.append(mrow); pixels.append(prow)
        return {"w":w,"h":h,"mask":mask,"pixels":pixels}

    def copy_selection(self):
        payload=self._clipboard_payload()
        if payload is None:
            self.labels["status"].text="請先用矩形選取或畫筆選取圈出像素"
            return
        self.clipboard=copy.deepcopy(payload)
        self.labels["status"].text=f"已複製 {len(self.selection)} 個像素｜剪貼簿 {payload['w']}×{payload['h']}"
        haptic_light()

    def cut_selection(self):
        payload=self._clipboard_payload()
        if payload is None:
            self.labels["status"].text="請先選取要剪下的內容"
            return
        self.clipboard=copy.deepcopy(payload); self._push_undo()
        self._mark_current_effect_authored()
        for x,y in self.selection:self.pixels[y][x]=TRANSPARENT
        self.dirty=True; self.refresh_pixels(); self.refresh_labels()
        self.labels["status"].text=f"已剪下 {len(self.selection)} 個像素；可按『貼上』後拖動"
        haptic_light()

    def paste_clipboard(self):
        if not self.clipboard:
            self.labels["status"].text="剪貼簿目前是空的；請先複製或剪下"
            return
        data=self.clipboard; cw=int(data.get("w",0)); ch=int(data.get("h",0))
        if cw<=0 or ch<=0:return
        expanded=max(cw,ch)>self.canvas_size
        if expanded:
            if max(cw,ch)>128:
                self.labels["status"].text="剪貼簿超過 128 像素，未裁切或貼上"
                return
            self._push_full_undo()
            n=max(cw,ch)
            if self.animation_mode:
                raw=self._current_animation()
                raw["frames"]=[self.model.resize_canvas_pixels(f,n,self.category[0]) for f in raw.get("frames",[])]
                raw["canvas_size"]=n
                self.pixels=copy.deepcopy(raw["frames"][self.animation_frame_index])
            else:
                self.base_pixels=self.model.resize_canvas_pixels(self.base_pixels,n,self.category[0])
                self.asset_canvas_size=n;self.pixels=copy.deepcopy(self.base_pixels)
            self.canvas_size=n;self.selection=set();self._sync_active_canvas(recenter=True)
        box=self._selection_bbox()
        if box is not None:
            ox=box[0]+1; oy=box[1]+1
        else:
            ox=(self.canvas_size-cw)//2; oy=(self.canvas_size-ch)//2
        ox=max(0,min(max(0,self.canvas_size-cw),ox)); oy=max(0,min(max(0,self.canvas_size-ch),oy))
        if not expanded:self._push_undo()
        old=set(self.selection); new_sel=set()
        self._mark_current_effect_authored()
        mask=data.get("mask",[]); cp=data.get("pixels",[])
        for yy in range(ch):
            for xx in range(cw):
                if yy>=len(mask) or xx>=len(mask[yy]) or not mask[yy][xx]:continue
                tx=ox+xx; ty=oy+yy
                if not (0<=tx<self.canvas_size and 0<=ty<self.canvas_size):continue
                c=TRANSPARENT
                if yy<len(cp) and xx<len(cp[yy]):c=str(cp[yy][xx])
                # 不遮擋: transparent source is a hole; preserve destination.
                # 遮擋: retain legacy opaque selection and clear destination.
                if not (self.selection_transparent_mode and c.upper().endswith("00")):
                    self.pixels[ty][tx]=c
                new_sel.add((tx,ty))
        self.selection=new_sel; self._reset_selection_gesture(); self.dirty=True
        self.refresh_pixels(); self.refresh_labels()
        self.labels["status"].text=f"已貼上 {len(new_sel)} 個像素；從選區內按住拖曳即可移動"
        haptic_light()

    def _apply_selection_payload(self, payload, origin_x, origin_y, keep_original=False):
        if not payload:return False
        w=int(payload.get("w",0)); h=int(payload.get("h",0)); mask=payload.get("mask",[]); cp=payload.get("pixels",[])
        if w<=0 or h<=0:return False
        old=set(self.selection); self._push_undo()
        self._mark_current_effect_authored()
        if not keep_original:
            for x,y in old:
                if 0<=y<self.canvas_size and 0<=x<self.canvas_size:self.pixels[y][x]=TRANSPARENT
        new_sel=set(old if keep_original else ())
        for yy in range(h):
            for xx in range(w):
                if yy>=len(mask) or xx>=len(mask[yy]) or not mask[yy][xx]:continue
                tx=int(origin_x)+xx; ty=int(origin_y)+yy
                if not (0<=tx<self.canvas_size and 0<=ty<self.canvas_size):continue
                c=str(cp[yy][xx]) if yy<len(cp) and xx<len(cp[yy]) else TRANSPARENT
                if not (self.selection_transparent_mode and c.upper().endswith("00")):
                    self.pixels[ty][tx]=c
                new_sel.add((tx,ty))
        self.selection=new_sel; self._reset_selection_gesture(); self.dirty=True
        self.refresh_pixels(); self.refresh_labels(); return True

    @staticmethod
    def _transform_payload(payload, mode):
        if not payload:return None
        w=int(payload.get("w",0)); h=int(payload.get("h",0)); mask=payload.get("mask",[]); pix=payload.get("pixels",[])
        if mode=="rotate":nw,nh=h,w
        else:nw,nh=w,h
        out_mask=[[False for _x in range(nw)] for _y in range(nh)]
        out_pix=[[TRANSPARENT for _x in range(nw)] for _y in range(nh)]
        for y in range(h):
            for x in range(w):
                if y>=len(mask) or x>=len(mask[y]) or not mask[y][x]:continue
                if mode=="rotate":tx,ty=h-1-y,x
                elif mode=="flip_h":tx,ty=w-1-x,y
                elif mode=="flip_v":tx,ty=x,h-1-y
                else:tx,ty=x,y
                out_mask[ty][tx]=True
                out_pix[ty][tx]=str(pix[y][x]) if y<len(pix) and x<len(pix[y]) else TRANSPARENT
        return {"w":nw,"h":nh,"mask":out_mask,"pixels":out_pix}

    def rotate_selection(self):
        payload=self._clipboard_payload(); box=self._selection_bbox()
        if payload is None or box is None:
            self.labels["status"].text="請先選取要旋轉的繪圖框"; return
        out=self._transform_payload(payload,"rotate"); cx=(box[0]+box[2])*0.5; cy=(box[1]+box[3])*0.5
        ox=int(round(cx-(int(out["w"])-1)*0.5)); oy=int(round(cy-(int(out["h"])-1)*0.5))
        ox=max(0,min(self.canvas_size-int(out["w"]),ox)); oy=max(0,min(self.canvas_size-int(out["h"]),oy))
        self._apply_selection_payload(out,ox,oy,False); self.labels["status"].text="選取框已順時針旋轉 90°"

    def _flip_selection(self, axis):
        payload=self._clipboard_payload(); box=self._selection_bbox()
        if payload is None or box is None:
            self.labels["status"].text="請先選取要翻轉的繪圖框"; return
        mode="flip_v" if str(axis)=="v" else "flip_h"
        out=self._transform_payload(payload,mode); self._apply_selection_payload(out,box[0],box[1],False)
        self.labels["status"].text="選取框已%s翻轉｜同一顆『翻轉 H/V』：單按=水平、快速連按2次=垂直" % ("垂直" if axis=="v" else "水平")

    def request_flip_selection(self):
        """One key for both axes: single tap H, quick double tap V."""
        now=time.monotonic(); self._flip_tap_generation+=1; gen=self._flip_tap_generation
        if now-self._flip_last_tap<=0.32:
            self._flip_last_tap=0.0; self._flip_tap_generation+=1
            self._flip_selection("v"); return
        self._flip_last_tap=now
        def worker():
            time.sleep(0.34)
            if gen!=self._flip_tap_generation:return
            try:mainthread.run_async(lambda:self._flip_selection("h"))
            except Exception:pass
        threading.Thread(target=worker,name="AssetFlipKey",daemon=True).start()
        self.labels["status"].text="翻轉 H/V：單按＝水平翻轉；快速連按2次＝垂直翻轉"

    def mirror_selection(self):
        """Mirror-copy the selection across the canvas vertical centre line."""
        payload=self._clipboard_payload(); box=self._selection_bbox()
        if payload is None or box is None:
            self.labels["status"].text="請先選取要鏡射的繪圖框"; return
        self._push_undo(); self._mark_current_effect_authored(); old=set(self.selection); new=set(old)
        source={(x,y):str(self.pixels[y][x]) for x,y in old}
        for (x,y),c in source.items():
            tx=self.canvas_size-1-int(x); ty=int(y)
            if not (self.selection_transparent_mode and c.upper().endswith("00")):
                self.pixels[ty][tx]=c
            new.add((tx,ty))
        self.selection=new; self._reset_selection_gesture(); self.dirty=True; self.refresh_pixels(); self.refresh_labels()
        self.labels["status"].text="已鏡射複製到畫布另一側｜原圖保留；若要原地反向請用『翻轉 H/V』"

    def _start_selection_move(self,col,row):
        if not self.selection:return False
        self._push_undo()
        self._selection_gesture_mode="move"; self._selection_anchor=(int(col),int(row))
        self._selection_move_source=set(self.selection)
        self._selection_move_base=copy.deepcopy(self.pixels)
        self._selection_move_last_delta=(0,0)
        return True

    def _move_selection_to(self,col,row):
        if self._selection_gesture_mode!="move" or not self._selection_move_source or self._selection_move_base is None:return
        ax,ay=self._selection_anchor; dx=int(col)-ax; dy=int(row)-ay
        box=self._selection_bbox(self._selection_move_source)
        if box is None:return
        x0,y0,x1,y1=box
        dx=max(-x0,min(self.canvas_size-1-x1,dx)); dy=max(-y0,min(self.canvas_size-1-y1,dy))
        if (dx,dy)==self._selection_move_last_delta:return
        self._mark_current_effect_authored()
        pdx,pdy=self._selection_move_last_delta
        old_targets={(x+pdx,y+pdy) for x,y in self._selection_move_source}
        new_targets={(x+dx,y+dy) for x,y in self._selection_move_source}
        affected=set(self._selection_move_source)|old_targets|new_targets
        base=self._selection_move_base
        for x,y in affected:self.pixels[y][x]=base[y][x]
        for x,y in self._selection_move_source:self.pixels[y][x]=TRANSPARENT
        for sx,sy in self._selection_move_source:
            tx=sx+dx; ty=sy+dy; source_color=str(base[sy][sx])
            if self.selection_transparent_mode and source_color.upper().endswith("00"):
                continue
            self.pixels[ty][tx]=source_color
        old=set(self.selection); self.selection=new_targets
        self._selection_move_last_delta=(dx,dy); self.dirty=True
        self.refresh_pixels();self.refresh_labels()

    def _selection_gesture(self,col,row,kind,state):
        key=(int(col),int(row)); began=(kind=="tap" or int(state)==1)
        finished=self._gesture_finished(kind,state)
        if began:
            # Touching inside an existing selection means "move". Starting
            # outside means "make a fresh selection".
            if key in self.selection and self._start_selection_move(col,row):
                if kind=="tap":
                    # A simple tap inside the selection keeps it selected; only
                    # a drag should become a pixel edit / undoable move.
                    if self.undo_stack:self.undo_stack.pop()
                    self._reset_selection_gesture()
                    self.labels["status"].text=f"目前選取 {len(self.selection)} 個像素；按住拖曳即可移動"
                return
            old=set(self.selection); self.selection=set()
            self._selection_anchor=key
            if self.tool=="select_rect":
                self._selection_gesture_mode="rect"; self.selection={key}
            else:
                self._selection_gesture_mode="brush"; self._selection_brush_last=key; self.selection={key}
            self._refresh_selection_cells(old); self.refresh_labels()
            if kind=="tap":
                self._reset_selection_gesture(); self.labels["status"].text="已選取 1 個像素"
            return

        if self._selection_gesture_mode=="move":
            self._move_selection_to(col,row)
            if finished:
                moved=self._selection_move_last_delta!=(0,0)
                self._reset_selection_gesture()
                if moved:self.labels["status"].text=f"已移動選取內容｜目前選取 {len(self.selection)} 個像素"
                else:
                    # A zero-distance move made an undo snapshot but no edit;
                    # discard it so one tap does not create a fake undo step.
                    if self.undo_stack:self.undo_stack.pop()
            return

        if self._selection_gesture_mode=="rect":
            ax,ay=self._selection_anchor; x0=min(ax,col); x1=max(ax,col); y0=min(ay,row); y1=max(ay,row)
            old=set(self.selection); self.selection={(x,y) for y in range(y0,y1+1) for x in range(x0,x1+1)}
            self._refresh_selection_cells(old); self.refresh_labels()
        elif self._selection_gesture_mode=="brush":
            old=set(self.selection); last=self._selection_brush_last or key
            for x,y in self._line_cells(last[0],last[1],col,row):
                if 0<=x<self.canvas_size and 0<=y<self.canvas_size:self.selection.add((x,y))
            self._selection_brush_last=key; self._refresh_selection_cells(old); self.refresh_labels()
        if finished:
            count=len(self.selection); self._reset_selection_gesture()
            self.labels["status"].text=f"選取完成：{count} 個像素｜從選區內拖曳可移動"

    def _paint_gesture(self,x,y,kind,state):
        if int(state) in (4,5):
            self._cancel_canvas_gesture();return
        if kind=="pan" and int(state)==1:
            self._gesture_original_selection=set(self.selection)
            self._gesture_original_dirty=self.dirty
        w=max(1.0,float(self.overlay.width)); h=max(1.0,float(self.overlay.height))
        grid=self._display_grid_size()
        col=self.canvas_view_x+max(0,min(grid-1,int(x/w*grid)))
        row=self.canvas_view_y+max(0,min(grid-1,int(y/h*grid)))
        col=max(0,min(self.canvas_size-1,col)); row=max(0,min(self.canvas_size-1,row))
        key=(col,row)
        if self.animation_playing:self._stop_animation_preview()
        if self.tool in ("select_rect","select_brush"):
            self._selection_gesture(col,row,kind,int(state))
            if kind=="tap" or int(state)==3:self._gesture_original_selection=None
            return
        if self.tool=="picker":
            sampled=str(self.pixels[row][col])
            self.selected_color=sampled
            if self.hex_field is not None:
                self.hex_field.text=sampled[:7] if not sampled.upper().endswith("00") else "#000000"
            if sampled.upper().endswith("00"):
                msg="已吸取透明像素；放開後切換為橡皮"
            else:
                msg="已吸取顏色："+sampled[:7]+"；放開後切換為畫筆"
            self.labels["status"].text=msg
            # Dragging while the eyedropper is held samples continuously.  Only
            # switch tools after the gesture ends so the same drag can never
            # accidentally start painting.
            if kind=="tap" or int(state) in (3,4,5):
                self.tool="erase" if sampled.upper().endswith("00") else "pen"
                self._sync_overlay_drag_mode()
            self.refresh_labels()
            return
        if kind=="pan":
            if state==1: self._pan_snapshot_taken=False; self._last_painted=None
            if not self._pan_snapshot_taken:self._push_undo(); self._pan_snapshot_taken=True
            if key==self._last_painted:
                if int(state)==3:
                    self._pan_snapshot_taken=False;self._gesture_original_selection=None;self._last_painted=None
                return
        else:
            self._push_undo(); self._last_painted=None
        self._mark_current_effect_authored()
        self._last_painted=key
        if self.tool=="fill":
            self.model.flood_fill_pixels(self.pixels,col,row,self.selected_color); self.refresh_pixels()
        else:
            self.pixels[row][col]=TRANSPARENT if self.tool=="erase" else self.selected_color
            self._refresh_one(col,row)
            self.refresh_preview(kind=="tap" or self._gesture_finished(kind,state))
        self.dirty=True
        if kind=="tap" or int(state)==3:
            self._pan_snapshot_taken=False;self._gesture_original_selection=None;self._last_painted=None

    def _refresh_one(self,x,y):
        x=int(x); y=int(y); grid=self._display_grid_size(); vx=int(self.canvas_view_x); vy=int(self.canvas_view_y)
        if not (vx<=x<vx+grid and vy<=y<vy+grid):return
        idx=(y-vy)*grid+(x-vx)
        c=self.pixels[y][x]
        if str(c).upper().endswith("00"):
            onion=self._onion_color(int(x),int(y))
            display_color=onion if onion else "#18212A"
        else:display_color=str(c)[:7]
        # Native-cell contour is a fallback beneath the retained gizmo. Even if
        # a particular Pyto build delays overlay subview composition, selected
        # targets still have an unmistakable gold boundary.
        selected=(x,y) in self.selection
        boundary=selected and any((x+dx,y+dy) not in self.selection for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)))
        if self.metal_canvas is not None and self.metal_canvas.available:
            self.metal_canvas.set_cell(x-vx,y-vy,display_color,boundary)
            return
        if not (0<=idx<len(self.pixel_views)):return
        self.pixel_views[idx].background_color=color(display_color)
        self.pixel_views[idx].border_width=2.0 if boundary else 0.25
        self.pixel_views[idx].border_color=GOLD if boundary else color("#28323C")

        self._refresh_fx_frame_editor_panel()

    def refresh_preview(self,force=False):
        """Refresh only changed cells in the lightweight game-scale preview.

        FIX13 called the full root layout from every paint drag and rewrote every
        preview view. On 32x32 artwork that meant thousands of UIKit property
        writes per second. FIX14 keeps the authored canvas untouched, caps only
        the small preview to 16x16, and updates cells whose sampled colour changed.
        """
        now=time.monotonic()
        if (not force) and now-self._preview_last<0.08:
            return
        self._preview_last=now
        if not self.pixels:
            return
        source_pixels=self._combat_preview_pixels()
        source_size=len(source_pixels) if isinstance(source_pixels,list) and source_pixels else self.canvas_size
        try:self._preview_world=(40.0,40.0) if self.category[0]=="tile" else self.model.canvas_world_size(source_size)
        except Exception:self._preview_world=(float(source_size)*float(PIXEL_WORLD_SCALE),)*2
        target=min(self.canvas_size,PREVIEW_MAX_GRID)
        rebuilt=False
        if self.preview_grid != target or len(self.preview_cells) != target*target:
            self._rebuild_preview_views(); rebuilt=True
        try:grid=self.model.resample_preview_pixels(source_pixels,self.preview_grid)
        except Exception:return
        flat=[str(grid[yy][xx]) for yy in range(self.preview_grid) for xx in range(self.preview_grid)]
        old=self._preview_color_cache
        for idx,c in enumerate(flat):
            if (not force) and idx<len(old) and old[idx]==c:
                continue
            v=self.preview_cells[idx]; hidden=c.upper().endswith("00")
            try:v.hidden=hidden
            except Exception:pass
            if not hidden:
                v.background_color=color(c[:7])
        self._preview_color_cache=flat
        # Layout is deliberately NOT called on ordinary paint refreshes. New
        # preview cells only need one geometry pass after a resolution rebuild.
        if rebuilt:
            try:self._layout()
            except Exception:pass

    def refresh_pixels(self):
        self._clamp_canvas_view()
        grid=self._display_grid_size(); vx=int(self.canvas_view_x); vy=int(self.canvas_view_y)
        if self.metal_canvas is not None and self.metal_canvas.available:
            colors=[]; boundaries=[]
            for local_y in range(grid):
                y=vy+local_y
                for local_x in range(grid):
                    x=vx+local_x; c=self.pixels[y][x]
                    if str(c).upper().endswith("00"):
                        onion=self._onion_color(x,y); colors.append(onion if onion else "#18212A")
                    else:colors.append(str(c)[:7])
                    selected=(x,y) in self.selection
                    boundaries.append(selected and any((x+dx,y+dy) not in self.selection for dx,dy in ((-1,0),(1,0),(0,-1),(0,1))))
            self.metal_canvas.set_grid(colors,boundaries,grid)
        else:
            for y in range(vy,min(self.canvas_size,vy+grid)):
                for x in range(vx,min(self.canvas_size,vx+grid)):self._refresh_one(x,y)
        self._update_selection_outline()
        self.refresh_preview(True)

    def refresh_labels(self):
        # FIX125: `_raw` must exist before any animation/VFX label branch uses
        # it. FIX124 assigned it later in this function, so entering the FX
        # editor crashed at the frame-counter/export-label code with
        # UnboundLocalError and the dedicated frame panel never became visible.
        _raw=self._current_animation() or {}
        zh=self.model.display_name(self.asset_id)
        self.labels["title"].text=f"{self.category[1]}｜{zh}"+(" ＊未存檔" if self.dirty else "")+f"\nID：{self.asset_id}"
        tool_name={"pen":"畫筆","erase":"橡皮","fill":"填滿","picker":"吸取顏色","select_rect":"矩形選取","select_brush":"畫筆選取"}.get(self.tool,self.tool)
        color_text="透明" if self.selected_color.upper().endswith("00") else self.selected_color[:7]
        sel_text=(f"｜選取：{len(self.selection)}｜{'不遮擋' if self.selection_transparent_mode else '遮擋'}" if self.selection else "")
        mode_text="Base"
        if self.animation_mode:
            raw=self._current_animation() or {}; frames=raw.get("frames",[])
            loop_text="循環" if bool(raw.get("loop",self.animation_state not in ("jump","roll"))) else "單次"
            events=raw.get("events",{}) if isinstance(raw.get("events",{}),dict) else {}
            marks=[]
            action_mark=(
                "攻" if self.category[0]=="weapon" and
                str(self.animation_state) in ("normal_attack","heavy_attack") else "動"
            )
            for key,label in (("link","銜"),("loop_start","起"),("loop_end","終"),("action",action_mark),("takeoff","跳")):
                if key in events:
                    try:marks.append(f"{label}★{int(events[key])+1}")
                    except Exception:pass
            event_text=("｜"+" ".join(marks)) if marks else ""
            state_label=self._animation_state_label(self.animation_state)
            mode_text=f"動畫 {state_label}（{self.animation_state}）｜幀 {self.animation_frame_index+1}/{max(1,len(frames))}｜{float(raw.get('fps',6.0)):.0f} FPS｜{loop_text}{event_text}"
            if self._is_vfx_state():
                grid=self._display_grid_size()
                mode_text+=f"｜VFX {self.canvas_size}²／視窗 X{self.canvas_view_x+1}-{self.canvas_view_x+grid} Y{self.canvas_view_y+1}-{self.canvas_view_y+grid}"
        view_n=self._display_grid_size()
        view_text=("全圖" if view_n==self.canvas_size else f"局部 X{self.canvas_view_x+1}–{self.canvas_view_x+view_n} Y{self.canvas_view_y+1}–{self.canvas_view_y+view_n}")
        self.labels["tool"].text=f"{mode_text}｜工具：{tool_name}｜色：{color_text}{sel_text}\n檢視 {self.canvas_zoom:.2f}×｜{view_text}"
        _export_now=self.export_canvas_size
        if self.animation_mode and self._is_vfx_state():
            _raw_export=(_raw.get("export_size") if isinstance(_raw,dict) else None)
            try:_export_now=int(_raw_export or self.export_canvas_size)
            except Exception:_export_now=self.export_canvas_size
        self.buttons["size"].title=(f"VFX{self.canvas_size}" if self._is_vfx_state() else f"畫布{self.asset_canvas_size}")
        self.buttons["sel_overlay"].title="不遮擋✓" if self.selection_transparent_mode else "遮擋✓"
        if self.category[0]=="effect":
            self.buttons["anim_toggle"].title="完成逐幀" if self.fx_editor_active else "逐幀編輯"
        else:
            self.buttons["anim_toggle"].title="回 Base" if self.animation_mode else ("動畫模式" if self._animation_supported() else "靜態素材")
        _state_name=self.animation_state or (self._animation_states()[0] if self._animation_states() else "—")
        self.buttons["anim_state"].title="狀態："+self._animation_state_label(_state_name)
        try:
            _fc=len((_raw.get("frames",[]) if isinstance(_raw,dict) else []) or [])
            self.buttons["fx_frame_counter"].title=f"幀 {int(self.animation_frame_index)+1}/{max(1,_fc)}"
        except Exception:
            self.buttons["fx_frame_counter"].title="幀 —"
        self.buttons["onion"].title=("洋蔥：關","洋蔥：前","洋蔥：前後")[int(self.onion_mode)%3]
        self.buttons["fps_down"].title="FPS－"; self.buttons["fps_up"].title="FPS＋"
        self.buttons["anim_loop"].title="循環✓" if bool(_raw.get("loop",self.animation_state not in ("jump","roll"))) else "單次"
        if self.category[0]=="weapon":
            _binding=self._weapon_binding(True) or {}; _anchor=str(_binding.get("anchor","weapon"))
            _anchor_zh={"weapon":"武器","hand":"手部","player":"角色","ground":"地面","projectile":"投射物"}.get(_anchor,_anchor)
            self.buttons["vfx_anchor"].title="附著："+_anchor_zh
            self.buttons["vfx_preview"].title="合成預覽✓" if self.combat_preview else "合成預覽"
            _attack,_effect=self._paired_weapon_states()
            _araw=self.animations.get(_attack,{}) if isinstance(self.animations,dict) else {}; _ev=_araw.get("events",{}) if isinstance(_araw,dict) else {}
            try:_vf=int(_ev.get("effect",_ev.get("action",0)))+1
            except Exception:_vf=1
            self.buttons["vfx_bind"].title=f"VFX綁定@{_vf}"
            if str(self.animation_state) in ("normal_attack","heavy_attack"):
                _af=self._current_event_frame("action")
                if _af is None:self.buttons["attack_frame"].title="設攻擊幀"
                elif int(_af)==int(self.animation_frame_index):self.buttons["attack_frame"].title=f"★攻擊幀@{int(_af)+1}"
                else:self.buttons["attack_frame"].title=f"攻擊幀@{int(_af)+1}"
        if self.category[0]=="creature" and str(self.animation_state) in ("attack","special"):
            _binding=self._external_effect_binding(True) or {};_anchor=str(_binding.get("anchor","actor"));_ext=str(_binding.get("effect_asset","") or "尚未建立")
            self.buttons["vfx_anchor"].title="附著："+_anchor
            self.buttons["vfx_pair"].title="開啟特效"
            _rawc=self._current_animation() or {};_evc=_rawc.get("events",{}) if isinstance(_rawc,dict) else {}
            try:_vf=int(_evc.get("effect",_evc.get("action",0)))+1
            except Exception:_vf=1
            self.buttons["vfx_bind"].title=f"VFX@{_vf}"
            _af=self._current_event_frame("action")
            self.buttons["attack_frame"].title=("設攻擊幀" if _af is None else (f"★攻擊幀@{int(_af)+1}" if int(_af)==int(self.animation_frame_index) else f"攻擊幀@{int(_af)+1}"))
        if self.animation_mode and self._is_vfx_state():
            _fm=self._current_fx_frame_meta(False) or {}
            self.buttons["fx_damage_frame"].title=("★FX傷害幀" if bool(_fm.get("damage",False)) else "FX傷害幀")
        marker=self._ensure_keyframe_type() if self.animation_mode else "link"
        marker_label=self._keyframe_label(marker)
        self.buttons["key_type"].title="關鍵："+marker_label
        marker_frame=self._current_event_frame(marker) if self.animation_mode else None
        if marker_frame is None:self.buttons["anim_event"].title="設"+marker_label
        elif int(marker_frame)==int(self.animation_frame_index):self.buttons["anim_event"].title="★"+marker_label
        else:self.buttons["anim_event"].title=f"{marker_label}:{int(marker_frame)+1}"
        canvas_w,canvas_h=self.model.canvas_world_size(self.canvas_size)
        visible_w,visible_h=self.model.visible_world_size(self.pixels)
        density=float(PIXEL_WORLD_SCALE)
        if self.category[0]=="tile":
            density=40.0/max(1,self.canvas_size)
            visible_w*=density/float(PIXEL_WORLD_SCALE);visible_h*=density/float(PIXEL_WORLD_SCALE)
            canvas_w=canvas_h=40.0
        _export_now=int(self.export_canvas_size or self.canvas_size)
        if self.animation_mode and isinstance(_raw,dict):
            try:_export_now=int(_raw.get("export_size",_export_now) or _export_now)
            except Exception:pass
        _scale_now=float(_export_now)/max(1,self.canvas_size)
        if self.animation_mode and self._is_vfx_state():
            _motion=str((self._weapon_binding(True) or {}).get("motion","follow"))
            _motion_zh="投射物軌跡" if _motion=="projectile" else "附著點跟隨"
            self.labels["preview_title"].text=(
                f"邏輯 VFX {self.canvas_size}×{self.canvas_size}｜輸出 {_export_now}×{_export_now}（{_scale_now:.1f}× nearest）｜{_motion_zh}\n"
                "一格就是一個真正特效像素；遊戲範圍由攻擊 hitbox 決定，不由圖片解析度放大"
            )
        elif self.pixel_mapping=="fit_world_box" or self.world_fit=="actor_bounds":
            self.labels["preview_title"].text=(
                f"邏輯原稿 {self.canvas_size}×{self.canvas_size}｜輸出 {_export_now}×{_export_now}（{_scale_now:.1f}× nearest）\n"
                "遊戲顯示尺寸＝角色/生物碰撞框；圖片解析度不再決定放大倍率"
            )
        elif self.animation_mode:
            self.labels["preview_title"].text=(
                f"動畫｜{self._animation_state_label(self.animation_state)}｜邏輯原稿 {self.canvas_size}×{self.canvas_size}｜輸出 {_export_now}×{_export_now}\n"
                f"固定密度 1格={density:.2f} 世界px｜畫布檢視縮放不會拆分邏輯像素"
            )
        else:
            self.labels["preview_title"].text=(
                f"邏輯原稿 {self.canvas_size}×{self.canvas_size}｜輸出 {_export_now}×{_export_now}（{_scale_now:.1f}× nearest）\n"
                f"固定密度 1格={density:.2f} 世界px｜放大只改變視圖，不增加可編輯像素格"
            )
        self._refresh_current_color_swatch()
        for k in ("pen","erase","fill","picker","select_rect","select_brush"):
            self.buttons[k].border_width=3 if k==self.tool else 0; self.buttons[k].border_color=WHITE

    def save_asset(self):
        try:
            self._stop_animation_preview(); self._commit_current_frame()
            path,row=self.model.save_pixel_asset(self.asset_id,self.category[0],self.base_pixels,self.animations,self.combat_bindings,export_size_override=self._native_export_override); self.dirty=False
            self.export_canvas_size=int(row.get("export_size",self.asset_canvas_size) or self.asset_canvas_size); self.export_scale=float(row.get("export_scale",1.0) or 1.0); self._native_export_override=None
            self.refresh_labels(); zh=self.model.display_name(self.asset_id)
            anim_count=sum(len(v.get("frames",[])) for v in self.animations.values() if isinstance(v,dict))
            bind_count=len(self.combat_bindings) if isinstance(self.combat_bindings,dict) else 0
            self.labels["status"].text=f"已存檔：{zh}（{self.asset_id}）\nBase PNG + 可編輯 JSON｜動畫 {len(self.animations)} 組／{anim_count} 幀｜攻擊VFX綁定 {bind_count} 組\n{row.get('file','')}"
            haptic_light()
        except Exception as exc:self.labels["status"].text="存檔失敗："+repr(exc)

    def _editor_safe_insets(self):
        """Return conservative content insets for fullscreen and Stage Manager.

        iPad keeps a visible system/status/window-control strip even when the
        Pyto view itself reports y=0.  Read UIKit safeAreaInsets when available
        and keep a conservative fallback so the first toolbar row is tappable.
        """
        ipad=is_ipad()
        top=34.0 if ipad else 8.0
        left=24.0 if ipad else 38.0
        right=24.0
        bottom=8.0
        try:
            native=self.root.__py_view__.managed
            insets=native.safeAreaInsets
            top=max(top,float(insets.top)+6.0)
            left=max(left,float(insets.left)+8.0)
            right=max(right,float(insets.right)+8.0)
            bottom=max(bottom,float(insets.bottom)+4.0)
        except Exception:
            pass
        return top,left,right,bottom

    def _layout(self,_sender=None):
        actual_w=max(1.0,float(self.root.width)); actual_h=max(1.0,float(self.root.height))
        # FIX33: prevent re-entrant Stage Manager layouts from mixing two
        # viewport geometries across the 256/576/1024 native pixel cells.
        if self._layout_in_progress:
            tw,th=self._layout_target_size
            if abs(actual_w-tw)>1.0 or abs(actual_h-th)>1.0:
                self._layout_deferred=True
            return
        self._layout_in_progress=True
        self._layout_target_size=(actual_w,actual_h)
        self._layout_deferred=False
        try:
            self._layout_impl(actual_w,actual_h)
        finally:
            self._layout_in_progress=False
            if self._layout_deferred:
                self._layout_deferred=False
                try:mainthread.run_async(lambda:self._layout())
                except Exception:pass

    def _layout_impl(self,actual_w,actual_h):
        # FIX13: the editor uses the same landscape-only session policy as main.
        # Never rebuild controls in portrait coordinates and never dismiss merely
        # because the device was physically rotated.  UIKit is asked to keep the
        # exact fullscreen owner landscape-only; a stray portrait layout is simply
        # ignored while the lock is reasserted.
        if actual_h>actual_w:
            if self.layout_locked:
                try:reassert_landscape(self.root)
                except Exception:pass
            return
        first=not self.layout_locked
        if first or is_ipad():
            self.landscape_w=actual_w; self.landscape_h=actual_h; self.layout_locked=True
            if first:
                try:lock_landscape(self.root)
                except Exception as exc:print("ASSET EDITOR LANDSCAPE LOCK warning:",repr(exc))
        try:lock_landscape(self.root)
        except Exception:pass
        w=self.landscape_w; h=self.landscape_h

        # FIX6: reserve the left column before laying out selection controls.
        # FIX5 used one very long second toolbar row; on iPhone landscape that
        # row crossed into the right inspector and was hidden underneath its
        # labels.  The selection/edit rows now live strictly inside the canvas
        # column, while save/reload use the free space on the first row.
        # FIX13 iPhone landscape safe-area: keep the middle-height drawing
        # canvas away from the Dynamic Island.  The corresponding shift also
        # narrows the inspector instead of squeezing the pixel grid.
        safe_top,safe_left,safe_right,safe_bottom=self._editor_safe_insets()
        panel_w=max(190.0,min(235.0,w*0.25)); gap=10.0
        canvas_x=safe_left
        # Three toolbar rows start at safe_top.  Keep the canvas below them on
        # both fullscreen iPad and Stage Manager windows.
        canvas_y=safe_top+128.0
        canvas_side=min(h-canvas_y-safe_bottom,w-panel_w-safe_left-safe_right-12.0)
        # Compact Stage Manager windows must never force a canvas larger than
        # the available height; the old hard 210 pt minimum caused clipping.
        canvas_side=max(120.0,canvas_side)
        left_right=canvas_x+canvas_side

        def layout_left_row(items, y, desired_gap=4.0):
            # Scale widths only when a smaller landscape window needs it.
            # This guarantees the row never intrudes into the right panel.
            usable=max(120.0,left_right-canvas_x)
            gap_px=float(desired_gap)
            total=sum(float(bw) for _key,bw in items)+gap_px*max(0,len(items)-1)
            scale=min(1.0,usable/max(1.0,total))
            x=canvas_x
            for key,bw in items:
                ww=max(30.0,float(bw)*scale)
                if x+ww>left_right:
                    ww=max(18.0,left_right-x)
                self.buttons[key].frame=(x,y,ww,28)
                x+=ww+gap_px*scale

        # Global/navigation row can use the full screen width above the
        # inspector. Keep a safety margin before the close button.
        top=safe_top; x=safe_left
        toolbar1=(("cat_prev",58),("cat_next",58),("asset_prev",56),("asset_next",56),("asset_add",72),("size",52),("pen",42),("erase",42),("fill",42),("picker",42),("gesture_mode",72),("reload",78),("save",66))
        row1_right=max(120.0,w-safe_right-48.0)
        desired=sum(float(bw) for _key,bw in toolbar1)+4.0*max(0,len(toolbar1)-1)
        row1_scale=min(1.0,max(0.68,(row1_right-x)/max(1.0,desired)))
        for key,bw in toolbar1:
            ww=max(34.0,float(bw)*row1_scale)
            self.buttons[key].frame=(x,top,ww,28); x+=ww+4.0*row1_scale

        # FIX55 three compact rows: transform controls apply to the current
        # selection frame. H/V share one key as requested.
        layout_left_row((("select_rect",70),("select_brush",70),("cut",44),("copy",44)),top+32.0)
        layout_left_row((("paste",44),("rotate_sel",66),("mirror_sel",72),("flip_sel",66)),top+64.0)
        layout_left_row((("select_all",42),("clear_sel",58),("sel_overlay",62),("undo",38),("fit_canvas",42)),top+96.0)

        self.buttons["close"].frame=(w-safe_right-38,top-1.0,38,32)
        self.canvas_bg.frame=(canvas_x,canvas_y,canvas_side,canvas_side)
        self._canvas_workspace_frame=(canvas_x,canvas_y,canvas_side)
        self._layout_canvas_view()
        px=canvas_x+canvas_side+gap; pw=max(150.0,w-px-safe_right); y=top+44.0
        self.labels["title"].frame=(px,y,pw,52); y+=56
        show_properties=(self.category[0]=='tile')
        self.buttons['tile_properties'].hidden=not show_properties
        prop_w=min(88.0, pw*0.32) if show_properties else 0.0
        self.labels["tool"].frame=(px,y,pw-prop_w-(4 if show_properties else 0),32)
        self.buttons['tile_properties'].frame=(px+pw-prop_w,y,prop_w,32)
        y+=36
        show_anim=self._animation_supported()
        anim_keys=("anim_state","fx_frame_counter","frame_prev","frame_next","frame_add","frame_dup","frame_del","onion","anim_play","fps_down","fps_up","anim_loop","key_type","anim_event","state_add","attack_frame","fx_damage_frame","fx_motion","vfx_bind","vfx_pair","vfx_anchor","vfx_preview","view_left","view_up","view_center","view_down","view_right")
        for key in anim_keys:
            try:self.buttons[key].hidden=not (show_anim and self.animation_mode)
            except Exception:pass
        try:self.buttons["anim_toggle"].hidden=False
        except Exception:pass
        _show_vfx=bool(show_anim and self.animation_mode and ((self.category[0]=="weapon" and str(self.animation_state) in ("normal_attack","normal_effect","heavy_attack","heavy_effect")) or (self.category[0]=="creature" and str(self.animation_state) in ("attack","special"))))
        for _key in ("vfx_bind","vfx_pair","vfx_anchor","vfx_preview"):
            try:self.buttons[_key].hidden=not _show_vfx
            except Exception:pass
        _show_attack_frame=bool(show_anim and self.animation_mode and ((self.category[0]=="weapon" and str(self.animation_state) in ("normal_attack","heavy_attack")) or (self.category[0]=="creature" and str(self.animation_state) in ("attack","special"))))
        try:self.buttons["attack_frame"].hidden=not _show_attack_frame
        except Exception:pass
        _show_fx_damage=bool(show_anim and self.animation_mode and self._is_vfx_state())
        try:self.buttons["fx_damage_frame"].hidden=not _show_fx_damage
        except Exception:pass
        _show_fx_motion=bool(show_anim and self.animation_mode and self.category[0]=="effect")
        try:self.buttons["fx_motion"].hidden=not _show_fx_motion
        except Exception:pass
        # Reuse the custom-state slot on attack/FX timing controls. This
        # keeps four readable inspector columns on narrow iPhone landscape.
        try:self.buttons["state_add"].hidden=(_show_attack_frame or _show_fx_damage)
        except Exception:pass
        _show_vfx_nav=bool(_show_vfx and self._is_vfx_state() and self.canvas_size>self._display_grid_size())
        for _key in ("view_left","view_up","view_center","view_down","view_right"):
            try:self.buttons[_key].hidden=not _show_vfx_nav
            except Exception:pass
        try:self.buttons["auto_anim"].hidden=(self.category[0] != "creature")
        except Exception:pass
        # FIX62 exposes all gameplay weapon IDs from WEAPON_DEFS.
        # Hide arbitrary "new weapon" creation until combat stats/catalog data
        # is also authorable, preventing an editor-only weapon that cannot equip.
        try:self.buttons["asset_add"].hidden=(self.category[0] == "weapon")
        except Exception:pass

        # FIX7: in animation mode the inspector itself becomes the timeline.
        # This avoids adding another global toolbar row and remains usable on the
        # short (~400 pt) iPhone landscape viewport.
        if show_anim and self.animation_mode:
            if self.category[0] == "effect":
                # FIX123 dedicated FX timeline.  The old generic inspector rows
                # could overlap the later-created preview view and disappear on
                # short iPhone landscape screens. Keep all frame operations in
                # two compact rows above the preview and explicitly bring them
                # to the front.
                fifth=max(28.0,(pw-16.0)/5.0)
                for _key,_i in (("anim_toggle",0),("frame_prev",1),("fx_frame_counter",2),("frame_next",3),("anim_play",4)):
                    self.buttons[_key].hidden=False
                    self.buttons[_key].frame=(px+_i*(fifth+4),y,fifth,28)
                # State cycling is not useful for direct effect assets; the
                # authoritative state is always `effect`.
                self.buttons["anim_state"].hidden=True
                y+=30
                fifth2=max(28.0,(pw-16.0)/5.0)
                for _key,_i in (("frame_dup",0),("frame_add",1),("frame_del",2),("onion",3),("fx_damage_frame",4)):
                    self.buttons[_key].hidden=False
                    self.buttons[_key].frame=(px+_i*(fifth2+4),y,fifth2,28)
                y+=30
                quarter=max(28.0,(pw-12.0)/4.0)
                for _key,_i in (("fps_down",0),("anim_loop",1),("fps_up",2),("fx_motion",3)):
                    self.buttons[_key].hidden=False
                    self.buttons[_key].frame=(px+_i*(quarter+4),y,quarter,28)
                # Generic state/key/VFX-link controls are intentionally hidden
                # here; this screen edits the already-linked gameplay FX itself.
                for _key in ("key_type","anim_event","state_add","attack_frame","vfx_bind","vfx_pair","vfx_anchor","vfx_preview","view_left","view_up","view_center","view_down","view_right"):
                    try:self.buttons[_key].hidden=True
                    except Exception:pass
                y+=32
                self.labels["preview_title"].frame=(px,y,pw,24); y+=26
                preview_h=max(42.0,min(56.0,h-y-84.0))
                # Preview/grid views were created after toolbar buttons, so
                # force timeline controls to the top of the UIKit z-order.
                for _key in ("anim_toggle","frame_prev","fx_frame_counter","frame_next","anim_play","frame_dup","frame_add","frame_del","onion","fx_damage_frame","fps_down","anim_loop","fps_up","fx_motion"):
                    try:self.root.bring_subview_to_front(self.buttons[_key])
                    except Exception:pass
            elif self.category[0] == "creature":
                first_w=max(28.0,(pw-12.0)/4.0)
                self.buttons["anim_toggle"].frame=(px,y,first_w,28)
                self.buttons["anim_state"].frame=(px+first_w+4,y,first_w,28)
                self.buttons["auto_anim"].frame=(px+2*(first_w+4),y,first_w,28)
                self.buttons["anim_play"].frame=(px+3*(first_w+4),y,first_w,28)
            else:
                third=max(34.0,(pw-8.0)/3.0)
                self.buttons["anim_toggle"].frame=(px,y,third,28)
                self.buttons["anim_state"].frame=(px+third+4,y,third,28)
                self.buttons["anim_play"].frame=(px+2*(third+4),y,third,28)
            if self.category[0] != "effect":
                y+=30
                quarter=max(28.0,(pw-12.0)/4.0)
                for key,i in (("frame_prev",0),("frame_next",1),("frame_dup",2),("frame_add",3)):
                    self.buttons[key].frame=(px+i*(quarter+4),y,quarter,28)
                y+=30
                for key,i in (("frame_del",0),("onion",1),("anim_loop",2),("key_type",3)):
                    self.buttons[key].frame=(px+i*(quarter+4),y,quarter,28)
                y+=30
                quarter2=max(28.0,(pw-12.0)/4.0)
                timing_key=("attack_frame" if ((self.category[0]=="weapon" and str(self.animation_state) in ("normal_attack","heavy_attack")) or (self.category[0]=="creature" and str(self.animation_state) in ("attack","special"))) else ("fx_damage_frame" if self._is_vfx_state() else "state_add"))
                self.buttons[timing_key].frame=(px,y,quarter2,28)
                self.buttons["anim_event"].frame=(px+quarter2+4,y,quarter2,28)
                self.buttons["fps_down"].frame=(px+2*(quarter2+4),y,quarter2,28)
                self.buttons["fps_up"].frame=(px+3*(quarter2+4),y,quarter2,28)
                y+=32
                if (self.category[0]=="weapon" and str(self.animation_state) in ("normal_attack","normal_effect","heavy_attack","heavy_effect")) or (self.category[0]=="creature" and str(self.animation_state) in ("attack","special")):
                    for _key,_i in (("vfx_bind",0),("vfx_pair",1),("vfx_anchor",2),("vfx_preview",3)):
                        self.buttons[_key].frame=(px+_i*(quarter2+4),y,quarter2,28)
                    y+=30
                    if self._is_vfx_state() and self.canvas_size>self._display_grid_size():
                        fifth=max(24.0,(pw-16.0)/5.0)
                        for _key,_i in (("view_left",0),("view_up",1),("view_center",2),("view_down",3),("view_right",4)):
                            self.buttons[_key].frame=(px+_i*(fifth+4),y,fifth,28)
                        y+=30
                self.labels["preview_title"].frame=(px,y,pw,24); y+=26
                preview_h=max(42.0,min(56.0,h-y-84.0))
        else:
            # Base/static mode keeps automatic generation one tap away for
            # creatures. Other categories retain the original single switch.
            toggle_w=min(92.0,max(66.0,pw*0.22))
            if self.category[0] == "creature":
                auto_w=min(92.0,max(66.0,pw*0.22))
                self.labels["preview_title"].frame=(px,y,max(44.0,pw-toggle_w-auto_w-8),38)
                self.buttons["auto_anim"].frame=(px+pw-toggle_w-auto_w-4,y,auto_w,32)
                self.buttons["anim_toggle"].frame=(px+pw-toggle_w,y,toggle_w,32)
            else:
                self.labels["preview_title"].frame=(px,y,pw-toggle_w-4,38)
                self.buttons["anim_toggle"].frame=(px+pw-toggle_w,y,toggle_w,32)
            y+=42
            preview_h=max(82.0,min(112.0,h-y-142.0))

        self.preview_bg.frame=(px,y,pw,preview_h)
        # FIX20: preview geometry is driven only by logical pixel coordinates.
        # Authored canvas resolution and world-space coverage are independent; 96px bosses keep native detail.
        canvas_screen=max(24.0,min(preview_h-12.0,92.0,pw*0.34))
        ox=px+pw*0.5-canvas_screen*0.5
        oy=y+(preview_h-canvas_screen)*0.5
        self.preview_ref.frame=(ox,oy,canvas_screen,canvas_screen)

        category=str(self.category[0])
        # Feet-anchored art and terrain both meet their world ground at the
        # bottom edge of the logical canvas. Center-anchored future categories
        # keep the line at the middle for reference.
        ground_y=oy+canvas_screen if category in ("tile","creature","plant","player") else oy+canvas_screen*0.5
        self.preview_ground.frame=(px+6,ground_y,pw-12,1.5)

        pcw=canvas_screen/self.preview_grid; pch=pcw
        for i,v in enumerate(self.preview_cells):
            yy=i//self.preview_grid; xx=i%self.preview_grid
            v.frame=(ox+xx*pcw,oy+yy*pch,pcw+0.15,pch+0.15)
        try:
            self.root.bring_subview_to_front(self.preview_ref)
            self.root.bring_subview_to_front(self.preview_ground)
            for v in self.preview_cells:self.root.bring_subview_to_front(v)
        except Exception:pass
        y+=preview_h+6

        # FIX13: persistent large current-colour swatch occupies the formerly
        # unused palette area.  It behaves like common paint apps: tap the swatch
        # to reopen the picker, and every colour source updates it immediately.
        swatch_w=min(112.0,max(68.0,pw*0.22))
        if show_anim and self.animation_mode:
            pal_w=max(150.0,pw-swatch_w-10.0)
            sw=max(16.0,min(26.0,(pal_w-28.0)/8.0))
            total=sw*8+4*7; startx=px
            for i,b in enumerate(self.palette_buttons):b.frame=(startx+i*(sw+4),y,sw,sw)
            self.buttons["current_color"].frame=(px+pw-swatch_w,y,swatch_w,sw)
            y+=sw+5
            field_h=28.0
        else:
            sw=max(22.0,min(30.0,(pw-12)/4.0))
            pal_block=sw*4+4*3
            for i,b in enumerate(self.palette_buttons):b.frame=(px+(i%4)*(sw+4),y+(i//4)*(sw+4),sw,sw)
            swatch_x=px+pal_block+12.0
            swatch_space=max(58.0,px+pw-swatch_x)
            self.buttons["current_color"].frame=(swatch_x,y,min(swatch_w,swatch_space),sw*2+4)
            y+=sw*2+9; field_h=30.0
        apply_w=min(72.0,max(56.0,pw*0.18)); pick_w=min(62.0,max(50.0,pw*0.15))
        field_w=max(64.0,pw-apply_w-pick_w-8)
        if self.hex_field is not None:self.hex_field.frame=(px,y,field_w,field_h)
        self.buttons["apply_hex"].frame=(px+field_w+4,y,apply_w,field_h)
        self.buttons["select_color"].frame=(px+field_w+apply_w+8,y,pick_w,field_h); y+=field_h+4
        self.labels["status"].frame=(px,y,pw,max(18.0,h-y-safe_bottom))
        # FIX124 panel is positioned after every ordinary editor view so its
        # dedicated controls always win the z-order when FX frame mode is active.
        self._layout_fx_frame_editor_panel()
        if self.color_picker_visible:
            self._layout_color_picker()
            try:self.root.bring_subview_to_front(self.color_picker_overlay)
            except Exception:pass
        if self.state_creator_visible:
            self._layout_state_creator()
            try:self.root.bring_subview_to_front(self.state_creator_overlay)
            except Exception:pass
        if self.asset_creator_visible:
            self._layout_asset_creator()
            try:self.root.bring_subview_to_front(self.asset_creator_overlay)
            except Exception:pass
        if self.rotation_visible:
            self._layout_rotation_editor()
            try:self.root.bring_subview_to_front(self.rotation_overlay)
            except Exception:pass
        if self.canvas_size_visible:
            self._layout_canvas_size_picker()
            try:self.root.bring_subview_to_front(self.canvas_size_overlay)
            except Exception:pass
        if self.fx_motion_visible:
            self._layout_fx_motion_editor()
            try:self.root.bring_subview_to_front(self.fx_motion_overlay)
            except Exception:pass

    def present(self):
        try:
            ui.show_view(self.root,PRESENT_FULLSCREEN)
        finally:
            self._animation_stop=True; self.animation_playing=False; self._animation_preview_generation+=1; self._animation_ui_pending=False
            self.fx_motion_preview_playing=False; self.fx_motion_preview_generation+=1; self.fx_motion_preview_pending=False
            try:
                if self.metal_canvas is not None:self.metal_canvas.close()
            except Exception:pass
            self._close_completed.set(); self._closing=False
