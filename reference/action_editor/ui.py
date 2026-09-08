# -*- coding: utf-8 -*-
"""Landscape action editor: player movement + data-driven creature AI actions."""
import pyto_ui as ui

from action_editor.model import (
    ActionEditorModel, NUMERIC_ROWS, TOGGLE_ROWS, CREATURE_NUMERIC_ROWS,
    MOTION_LABELS, EFFECT_LABELS,
)
from ios_host.colors import DARK, DARK2, WHITE, BLUE, GREEN, GOLD, RED
from ios_host.haptics import selection as haptic_selection, light as haptic_light
from ios_host.orientation import lock_landscape, reassert_landscape, is_ipad

try:PRESENT_FULLSCREEN=ui.PresentationMode.FULLSCREEN
except Exception:PRESENT_FULLSCREEN=ui.PRESENTATION_MODE_FULLSCREEN

def _center():
    try:return ui.TextAlignment.CENTER
    except Exception:return ui.TEXT_ALIGNMENT_CENTER


class ActionEditorUI:
    def __init__(self,project_root):
        self.model=ActionEditorModel(project_root)
        self.root=ui.View(); self.root.title="Pyto RPG 動作編輯器 FIX62"; self.root.background_color=DARK; self.root.layout=self._layout
        self.buttons={}; self.labels={}; self.mode="player"
        self.layout_locked=False; self.landscape_w=0.0; self.landscape_h=0.0
        self.creature_index=0; self.creature_state_index=0
        self.player_keys=[]; self.creature_keys=[]
        self._build(); self.refresh()

    def _button(self,key,title,action,bg=DARK2,font=11,group=None):
        def wrapped(sender):
            try:haptic_selection()
            except Exception:pass
            return action(sender)
        b=ui.Button(title=title); b.background_color=bg; b.title_color=WHITE; b.font=ui.Font.bold_system_font_of_size(font); b.corner_radius=7; b.action=wrapped
        self.root.add_subview(b); self.buttons[key]=b
        if group=="player":self.player_keys.append(("b",key))
        elif group=="creature":self.creature_keys.append(("b",key))
        return b

    def _label(self,key,text="",font=10,lines=2,bg=DARK2,group=None):
        l=ui.Label(text); l.text_color=WHITE; l.font=ui.Font.system_font_of_size(font); l.text_alignment=_center(); l.number_of_lines=lines
        if bg is not None:l.background_color=bg;l.corner_radius=6
        self.root.add_subview(l); self.labels[key]=l
        if group=="player":self.player_keys.append(("l",key))
        elif group=="creature":self.creature_keys.append(("l",key))
        return l

    def _build(self):
        self._label("title","動作編輯器｜動畫由素材編輯器製作；這裡定義遊戲功能與 AI 呼叫條件",13,1,None)
        self._button("mode_player","玩家動作",lambda _s:self.set_mode("player"),BLUE,10)
        self._button("mode_creature","生物 AI",lambda _s:self.set_mode("creature"),DARK2,10)
        self._button("save","存檔",lambda _s:self.save(),BLUE,11)
        self._button("reload","重讀",lambda _s:self.reload(),DARK2,10)
        self._button("defaults","玩家預設",lambda _s:self.defaults(),GOLD,9)
        self._button("close","×",lambda _s:self.close(),RED,16)

        # Player panel.
        for key,label,_step,_digits in NUMERIC_ROWS:
            self._label("name_"+key,label,10,1,group="player")
            self._button("minus_"+key,"－",lambda _s,k=key:self.adjust(k,-1),DARK2,14,"player")
            self._label("value_"+key,"",11,1,group="player")
            self._button("plus_"+key,"＋",lambda _s,k=key:self.adjust(k,+1),GREEN,14,"player")
        for key,label,desc in TOGGLE_ROWS:
            self._label("toggle_name_"+key,label+"\n"+desc,9,2,group="player")
            self._button("toggle_"+key,"",lambda _s,k=key:self.toggle(k),BLUE,10,"player")
        self._label("player_note","玩家：操作／物理參數。像素動畫、FPS、銜接／循環／起跳關鍵幀仍在素材編輯器。",9,2,DARK2,"player")

        # Creature AI panel. Newly named animation states appear here after the
        # asset is saved; no Python/model regeneration is required.
        self._button("creature_prev","◀ 生物",lambda _s:self.change_creature(-1),DARK2,9,"creature")
        self._label("creature_name","",10,1,DARK2,"creature")
        self._button("creature_next","生物 ▶",lambda _s:self.change_creature(1),DARK2,9,"creature")
        self._button("state_prev","◀ 狀態",lambda _s:self.change_creature_state(-1),DARK2,9,"creature")
        self._label("creature_state","",10,1,DARK2,"creature")
        self._button("state_next","狀態 ▶",lambda _s:self.change_creature_state(1),DARK2,9,"creature")

        self._button("action_enabled","AI 可用",lambda _s:self.toggle_creature_enabled(),GREEN,9,"creature")
        self._button("motion","動作：原地",lambda _s:self.cycle_motion(),BLUE,9,"creature")
        self._button("effect","效果：傷害＋擊退",lambda _s:self.cycle_effect(),GOLD,9,"creature")
        self._button("requires_ground","需站地面",lambda _s:self.toggle_creature_flag("requires_ground"),DARK2,9,"creature")
        self._button("legacy_contact","取代舊碰撞攻擊",lambda _s:self.toggle_creature_flag("override_legacy_contact"),DARK2,8,"creature")
        self._button("delete_action","刪除綁定",lambda _s:self.delete_creature_action(),RED,8,"creature")

        for key,label,_step,_digits in CREATURE_NUMERIC_ROWS:
            self._label("cname_"+key,label,9,1,group="creature")
            self._button("cminus_"+key,"－",lambda _s,k=key:self.adjust_creature(k,-1),DARK2,13,"creature")
            self._label("cvalue_"+key,"",10,1,group="creature")
            self._button("cplus_"+key,"＋",lambda _s,k=key:self.adjust_creature(k,+1),GREEN,13,"creature")

        self._label("creature_note","AI 會依距離／冷卻選擇已啟用動作；素材動畫的 ★動作 關鍵幀就是傷害／擊退生效點。持續=0 時自動採用動畫長度。",8,3,DARK2,"creature")
        self._label("status","",8,2,DARK2)

    def _set_group_hidden(self,group,hidden):
        rows=self.player_keys if group=="player" else self.creature_keys
        for kind,key in rows:
            obj=self.buttons.get(key) if kind=="b" else self.labels.get(key)
            if obj is not None:
                try:obj.hidden=bool(hidden)
                except Exception:pass

    def set_mode(self,mode):
        self.mode="creature" if mode=="creature" else "player"
        self.refresh()
        try:self._layout()
        except Exception:pass

    def _current_creature(self):
        rows=self.model.creature_ids(); self.creature_index%=max(1,len(rows)); return rows[self.creature_index]

    def _current_state(self):
        aid=self._current_creature(); rows=self.model.animation_states(aid)
        self.creature_state_index%=max(1,len(rows)); return rows[self.creature_state_index]

    # Player callbacks.
    def adjust(self,key,direction):
        row=next((r for r in NUMERIC_ROWS if r[0]==key),None)
        if row:self.model.adjust(key,float(row[2])*(1 if direction>0 else -1)); self.refresh()
    def toggle(self,key):self.model.toggle(key); self.refresh()

    # Creature callbacks.
    def change_creature(self,delta):
        rows=self.model.creature_ids(); self.creature_index=(self.creature_index+int(delta))%max(1,len(rows)); self.creature_state_index=0; self.refresh()
    def change_creature_state(self,delta):
        rows=self.model.animation_states(self._current_creature()); self.creature_state_index=(self.creature_state_index+int(delta))%max(1,len(rows)); self.refresh()
    def toggle_creature_enabled(self):
        aid,state=self._current_creature(),self._current_state(); raw=self.model.creature_action(aid,state,create=False)
        self.model.set_creature_enabled(aid,state,not bool(raw and raw.get("enabled",False))); self.refresh()
    def cycle_motion(self):self.model.cycle_motion(self._current_creature(),self._current_state(),1); self.refresh()
    def cycle_effect(self):self.model.cycle_effect(self._current_creature(),self._current_state(),1); self.refresh()
    def toggle_creature_flag(self,key):self.model.toggle_creature_flag(self._current_creature(),self._current_state(),key); self.refresh()
    def adjust_creature(self,key,direction):
        row=next((r for r in CREATURE_NUMERIC_ROWS if r[0]==key),None)
        if row:self.model.adjust_creature(self._current_creature(),self._current_state(),key,float(row[2])*(1 if direction>0 else -1)); self.refresh()
    def delete_creature_action(self):
        aid,state=self._current_creature(),self._current_state()
        if self.model.delete_creature_action(aid,state):self.labels["status"].text="已刪除這個 AI 動作綁定；按存檔寫入"
        else:self.labels["status"].text="目前狀態尚未建立 AI 動作綁定"
        self.refresh(keep_status=True)

    def save(self):
        try:
            p,c=self.model.save(); self.labels["status"].text="已存檔｜重新開遊戲後套用\naction_settings.json + creature_actions.json"
            try:haptic_light()
            except Exception:pass
        except Exception as exc:self.labels["status"].text="存檔失敗："+repr(exc)
    def reload(self):self.model.reload(); self.creature_index=0; self.creature_state_index=0; self.refresh(); self.labels["status"].text="已重新讀取玩家與生物動作設定"
    def defaults(self):self.model.reset_defaults(); self.refresh(); self.labels["status"].text="已載入玩家 FIX19 預設值；按存檔才會寫入"

    def refresh(self,keep_status=False):
        self._set_group_hidden("player",self.mode!="player"); self._set_group_hidden("creature",self.mode!="creature")
        self.buttons["mode_player"].background_color=GREEN if self.mode=="player" else DARK2
        self.buttons["mode_creature"].background_color=GREEN if self.mode=="creature" else DARK2
        try:self.buttons["defaults"].hidden=self.mode!="player"
        except Exception:pass
        if self.mode=="player":
            data=self.model.data
            for key,_label,_step,digits in NUMERIC_ROWS:self.labels["value_"+key].text=f"{float(data.get(key,0.0)):.{digits}f}"
            for key,_label,_desc in TOGGLE_ROWS:
                on=bool(data.get(key,False)); b=self.buttons["toggle_"+key]; b.title="開 ✓" if on else "關"; b.background_color=GREEN if on else DARK2
        else:
            aid,state=self._current_creature(),self._current_state(); entry=self.model.creature_entry(aid,create=False); action=self.model.creature_action(aid,state,create=False)
            self.labels["creature_name"].text=self.model.creature_display_name(aid)+"\n"+aid
            self.labels["creature_state"].text="動畫狀態\n"+state
            enabled=bool(action and action.get("enabled",False)); self.buttons["action_enabled"].title="AI 可用 ✓" if enabled else "＋建立 AI 動作"; self.buttons["action_enabled"].background_color=GREEN if enabled else BLUE
            motion=str((action or {}).get("motion","stationary")); effect=str((action or {}).get("effect","damage_knockback"))
            self.buttons["motion"].title="動作："+MOTION_LABELS.get(motion,motion); self.buttons["effect"].title="效果："+EFFECT_LABELS.get(effect,effect)
            ground=bool((action or {}).get("requires_ground",False)); self.buttons["requires_ground"].title="需站地面 ✓" if ground else "需站地面：否"
            legacy=bool(entry and entry.get("override_legacy_contact",False)); self.buttons["legacy_contact"].title="取代舊碰撞 ✓" if legacy else "保留舊碰撞"
            for key,_label,_step,digits in CREATURE_NUMERIC_ROWS:
                val=float((action or {}).get(key,0.0)); self.labels["cvalue_"+key].text=f"{val:.{digits}f}" if action else "—"
            if not keep_status:
                self.labels["status"].text=("已綁定：AI 可呼叫此動畫狀態" if action else "尚未綁定｜按『＋建立 AI 動作』")

    def _layout_player(self,w,h,sx,top):
        bottom=80.0; gap=10.0; col_w=(w-sx-28.0-gap)/2.0
        combined=[("num",r) for r in NUMERIC_ROWS]+[("toggle",r) for r in TOGGLE_ROWS]
        rows_per_col=max(1,(len(combined)+1)//2)
        row_h=max(38.0,min(58.0,(h-top-bottom)/rows_per_col))
        for i,(kind,row) in enumerate(combined):
            col=0 if i<rows_per_col else 1; rr=i if col==0 else i-rows_per_col; x=sx+col*(col_w+gap); y=top+rr*row_h
            if kind=="num":
                key=row[0]; name_w=col_w*0.46; bw=40.0; value_w=max(48.0,col_w-name_w-bw*2-12.0)
                self.labels["name_"+key].frame=(x,y,name_w,row_h-5); bx=x+name_w+4
                self.buttons["minus_"+key].frame=(bx,y,bw,row_h-5); self.labels["value_"+key].frame=(bx+bw+4,y,value_w,row_h-5); self.buttons["plus_"+key].frame=(bx+bw+value_w+8,y,bw,row_h-5)
            else:
                key=row[0]; tw=64.0; self.labels["toggle_name_"+key].frame=(x,y,col_w-tw-5,row_h-5); self.buttons["toggle_"+key].frame=(x+col_w-tw,y,tw,row_h-5)
        self.labels["player_note"].frame=(sx,h-66,w-sx-300,52)

    def _layout_creature(self,w,h,sx,top):
        usable=w-sx-28.0; gap=6.0
        # Selector row 1.
        y=top; half=(usable-gap)/2.0; btn=66.0
        self.buttons["creature_prev"].frame=(sx,y,btn,30); self.labels["creature_name"].frame=(sx+btn+4,y,half-btn*2-8,30); self.buttons["creature_next"].frame=(sx+half-btn,y,btn,30)
        x2=sx+half+gap; self.buttons["state_prev"].frame=(x2,y,btn,30); self.labels["creature_state"].frame=(x2+btn+4,y,half-btn*2-8,30); self.buttons["state_next"].frame=(x2+half-btn,y,btn,30)
        # Selector row 2/3.
        y+=34; third=(usable-2*gap)/3.0
        self.buttons["action_enabled"].frame=(sx,y,third,29); self.buttons["motion"].frame=(sx+third+gap,y,third,29); self.buttons["effect"].frame=(sx+2*(third+gap),y,third,29)
        y+=33; self.buttons["requires_ground"].frame=(sx,y,third,28); self.buttons["legacy_contact"].frame=(sx+third+gap,y,third,28); self.buttons["delete_action"].frame=(sx+2*(third+gap),y,third,28)
        y+=34
        # Data rows are split evenly across two columns.
        per_col=max(1,(len(CREATURE_NUMERIC_ROWS)+1)//2)
        col_w=(usable-gap)/2.0; row_h=max(28.0,min(40.0,(h-y-63.0)/per_col))
        for i,row in enumerate(CREATURE_NUMERIC_ROWS):
            col=0 if i<per_col else 1; rr=i if col==0 else i-per_col; x=sx+col*(col_w+gap); yy=y+rr*row_h; key=row[0]
            name_w=col_w*0.46; bw=36.0; value_w=max(46.0,col_w-name_w-bw*2-12.0)
            self.labels["cname_"+key].frame=(x,yy,name_w,row_h-4); bx=x+name_w+4
            self.buttons["cminus_"+key].frame=(bx,yy,bw,row_h-4); self.labels["cvalue_"+key].frame=(bx+bw+4,yy,value_w,row_h-4); self.buttons["cplus_"+key].frame=(bx+bw+value_w+8,yy,bw,row_h-4)
        self.labels["creature_note"].frame=(sx,h-58,w-sx-300,46)

    def _layout(self,_sender=None):
        actual_w=max(1.0,float(self.root.width)); actual_h=max(1.0,float(self.root.height))
        if actual_h>actual_w:
            if self.layout_locked:
                try:reassert_landscape(self.root)
                except Exception:pass
            return
        if (not self.layout_locked) or is_ipad():self.landscape_w=actual_w; self.landscape_h=actual_h; self.layout_locked=True
        try:lock_landscape(self.root)
        except Exception:pass
        w,h=self.landscape_w,self.landscape_h; sx=(24.0 if is_ipad() else 38.0); sy=8.0
        self.labels["title"].frame=(sx,sy,max(220,w-490),30)
        x=w-440; self.buttons["mode_player"].frame=(x,sy,78,30); self.buttons["mode_creature"].frame=(x+82,sy,78,30); self.buttons["save"].frame=(x+164,sy,58,30); self.buttons["reload"].frame=(x+226,sy,54,30); self.buttons["defaults"].frame=(x+284,sy,72,30); self.buttons["close"].frame=(w-66,sy,34,30)
        top=44.0
        if self.mode=="player":self._layout_player(w,h,sx,top)
        else:self._layout_creature(w,h,sx,top)
        self.labels["status"].frame=(w-286,h-58,254,46)

    def close(self):
        try:self.root.close()
        except Exception:pass
    def present(self):ui.show_view(self.root,PRESENT_FULLSCREEN)
