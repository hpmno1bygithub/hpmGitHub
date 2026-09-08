# -*- coding: utf-8 -*-
"""FIX80 bounded, retained PytoUI learning modal.

All UI work runs on the main thread. There are always 12 number keys, four
choice keys and one completion key; changing questions creates no new views.
The simulation and continuous Metal rendering are stopped by the host.
"""
import threading
import time
import mainthread
import pyto_ui as ui
from ios_host.orientation import is_ipad
from ios_host.study_layout import layout_geometry
from ios_host.study_ruby import MAX_CELLS, layout_ruby_reading, layout_ruby_choice


class _RubyCell:
    """Five retained glyph labels; no view allocation on answer/rotation."""
    def __init__(self, overlay):
        self.overlay = overlay
        self.labels = [overlay._label(16) for _ in range(5)]
        self.visible = False
        self.signature = None
        for label in self.labels:
            label.hidden = True

    def hide(self):
        if self.visible:
            for label in self.labels:
                label.hidden = True
                label.text = ''
            self.visible = False
            self.signature = None

    def apply(self, cell, origin=(0.0,0.0), disabled=False):
        signature = (cell['character'],cell['syllable'],cell['frame'],
                     cell['base_size'],tuple(origin),bool(disabled))
        if self.visible and signature == self.signature:
            return
        ox,oy = origin
        color = self.overlay._ruby_wrong if disabled else self.overlay._ruby_color
        for label,glyph in zip(self.labels,cell['glyphs']):
            label.hidden = glyph is None
            if glyph is None:
                label.text = ''
                continue
            x,y,w,h = glyph['frame']
            label.frame = (ox+x,oy+y,w,h)
            label.text = glyph['text']
            label.font = self.overlay._font(glyph['size'])
            label.text_color = color
            label.border_width = 2 if glyph['role'] == 'blank' else 0
            if glyph['role'] == 'blank':
                label.border_color = self.overlay._blank_color
                label.text_color = self.overlay._blank_color
        self.visible = True
        self.signature = signature


class StudyOverlay:
    def __init__(self, root, post_command, on_modal_changed, diagnostic_sink=None):
        self.root = root
        self.post_command = post_command
        self.on_modal_changed = on_modal_changed
        self.diagnostic_sink = diagnostic_sink
        self._closed = False
        self._render_ready = False
        self._dispatch_serial = 0
        self._dispatch_ticket = 0
        self._dispatch_since = 0.0
        self._dispatch_rescued = False
        self._dispatch_recoveries = 0
        self._apply_started_at = 0.0
        self._apply_stall_reported = False
        self._layout_signature = None
        self._deferred_layout = None
        self._manual_sync_at = -1000.0
        self._published_snapshot = None
        self.viewport = (852.0,393.0)
        self.opened = False
        self.current = {}
        self._answer_pending = False
        self._applying = False
        self._request_serial = 0
        self._pending_request = 0
        self._pending_token = -1
        self._pending_since = 0.0
        self._last_sync_at = 0.0
        self._sync_count = 0
        self._rendered_math_key = None
        self._lock = threading.Lock()
        self._pending = False
        self._latest = None
        self._last_submitted = None
        self._publication_serial = 0
        self._applied_publication = 0
        self._guard_rendered = False
        self._hidden_ack = threading.Event()
        self._hidden_ack.set()
        self.last_error = ''
        self._views = []
        self._font_cache = {}
        self._ruby_color = ui.Color.rgb(.94,.97,1,1)
        self._ruby_wrong = ui.Color.rgb(.60,.40,.42,1)
        self._blank_color = ui.Color.rgb(.99,.87,.52,1)
        self.screen = ui.View()
        self.screen.background_color = ui.Color.rgb(.015,.025,.045,.97)
        self.screen.hidden = True
        self.screen.user_interaction_enabled = False
        self.root.add_subview(self.screen)
        self.panel = ui.View()
        self.panel.background_color = ui.Color.rgb(.045,.075,.115,1)
        self.panel.border_width = 3
        self.panel.border_color = ui.Color.rgb(.46,.75,.87,1)
        self.panel.corner_radius = 3
        self.screen.add_subview(self.panel)
        self.title = self._label(22)
        self.tabs = [self._label(14) for _ in range(3)]
        self.math_title = self._label(16)
        self.equation = self._label(40)
        self.entry = self._label(40)
        self.entry.background_color = ui.Color.rgb(.10,.18,.27,1)
        self.entry.border_color = ui.Color.rgb(.66,.82,.97,1)
        self.entry.border_width = 2
        self.math_hint = self._label(12,2)
        self.reading_cells = [_RubyCell(self) for _ in range(MAX_CELLS)]
        self.feedback = self._label(13,2)
        self.complete_text = self._label(24,3)
        self.keys = {}
        for key in ('7','8','9','4','5','6','1','2','3','erase','0','confirm'):
            title = '消除' if key == 'erase' else '確認' if key == 'confirm' else key
            self.keys[key] = self._button(title, lambda _sender,k=key:self._key(k))
        self.choices = [self._button('\u00a0',lambda _sender,i=i:self._choose(i),content_only=True) for i in range(4)]
        # Sibling labels are above the buttons but never intercept a touch.
        self.choice_cells = [_RubyCell(self) for _ in range(4)]
        self.resume = self._button('繼續遊戲',lambda _sender:self._post('study_resume',lock=True))
        self.recover = self._button('重新同步',lambda _sender:self.request_resync())
        self.keys['confirm'].background_color = ui.Color.rgb(.10,.36,.31,1)
        self.keys['erase'].background_color = ui.Color.rgb(.32,.20,.15,1)
        self.resume.background_color = ui.Color.rgb(.10,.36,.31,1)
        self.layout(*self.viewport)

    def _report(self, event, error=None):
        """Never include typed answers, correct answers or password input."""
        if self.diagnostic_sink is None:
            return
        try:
            s = self.current
            self.diagnostic_sink(event, {
                'modal_kind': s.get('modal_kind', 'study'),
                'active': bool(s.get('active')), 'stage': s.get('stage', 0),
                'passed': list(s.get('passed', ())), 'required': list(s.get('required', ())),
                'token': s.get('token', -1), 'revision': s.get('revision', -1),
                'ack_id': s.get('ack_id', 0), 'pending_request': self._pending_request,
                'answer_pending': self._answer_pending, 'ui_pending': self._pending,
                'ui_applying': self._applying, 'dispatch_recoveries': self._dispatch_recoveries,
                'pending_seconds': max(0.0,time.monotonic()-self._pending_since) if self._answer_pending else 0.0,
                'dispatch_seconds': max(0.0,time.monotonic()-self._dispatch_since) if self._pending else 0.0,
                'apply_seconds': max(0.0,time.monotonic()-self._apply_started_at) if self._applying else 0.0,
                'error_type': type(error).__name__ if error is not None else '',
            })
        except Exception:
            pass  # Diagnostics must not become another modal failure.

    def close(self):
        """Called only when the containing game view has already closed."""
        with self._lock:
            self._closed = True
            self._dispatch_ticket += 1
            self._latest = None
            self._pending = False
            self._answer_pending = False
            self._hidden_ack.set()

    @property
    def native_element_count(self):
        return 2+len(self._views)

    @property
    def settled_hidden(self):
        with self._lock:
            return self._hidden_ack.is_set() and not self._pending

    def _font(self, size, bold=False):
        key = (round(float(size),3),bool(bold))
        if key not in self._font_cache:
            # Font objects are retained by their labels. Bound this lookup too.
            if len(self._font_cache) >= 128:
                self._font_cache.clear()
            factory = ui.Font.bold_system_font_of_size if bold else ui.Font.system_font_of_size
            self._font_cache[key] = factory(key[0])
        return self._font_cache[key]

    def _label(self,size,lines=1):
        view = ui.Label('')
        view.font = self._font(size)
        view.text_color = ui.Color.rgb(.94,.97,1,1)
        view.text_alignment = ui.TextAlignment.CENTER
        view.number_of_lines = lines
        view.user_interaction_enabled = False
        self.panel.add_subview(view)
        self._views.append(view)
        return view

    def _button(self,title,action,content_only=False):
        # An empty title can fall back to Pyto's native "Button" caption.
        # A CUSTOM hit target + non-empty whitespace avoids that code path.
        custom = getattr(getattr(ui, 'ButtonType', None), 'CUSTOM', None)
        if content_only and custom is not None:
            view = ui.Button(type=custom, title='\u00a0')
        else:
            view = ui.Button(title=title)
        view.font = ui.Font.bold_system_font_of_size(22)
        view.title_color = ui.Color.rgb(.96,.98,1,1)
        view.background_color = ui.Color.rgb(.13,.23,.34,1)
        view.border_width = 2
        view.border_color = ui.Color.rgb(.43,.62,.76,1)
        view.corner_radius = 3
        view.action = action
        if content_only:
            self._silence_choice_title(view)
        self.panel.add_subview(view)
        self._views.append(view)
        return view

    @staticmethod
    def _silence_choice_title(button):
        """The answer is drawn ONCE by _RubyCell, never by UIButton.title.

        The public Pyto properties are sufficient even without UIKit access.
        The optional managed bridge also clears attributed/state titles left
        by system styling. No private subviews are removed or rebuilt.
        """
        button.title = '\u00a0'
        button.title_color = ui.Color.rgb(0,0,0,0)
        try:
            native = button.__py_view__.managed
        except Exception:
            return
        # Clear BOTH native title stores for all common UIButton states.
        # Different Pyto/Objective-C bridge builds expose one of two selector
        # spellings, so handle each call independently instead of letting one
        # missing selector abort the whole cleanup.
        for state in range(8):
            try:
                native.setTitle('\u00a0', forState=state)
            except Exception:
                try:
                    native.setTitle_forState_('\u00a0', state)
                except Exception:
                    pass
            try:
                native.setAttributedTitle(None, forState=state)
            except Exception:
                try:
                    native.setAttributedTitle_forState_(None, state)
                except Exception:
                    pass
        try:
            native.titleLabel.hidden = True
            try:
                native.titleLabel.alpha = 0.0
            except Exception:
                pass
        except Exception:
            pass

    def _ensure_choice_title_hidden(self, button):
        # Normal refresh: one visibility probe, no repeated state/style writes.
        # If iOS reapplied a style, restore the full FIX80 caption protection.
        try:
            label = button.__py_view__.managed.titleLabel
            hidden = label.hidden
            if callable(hidden):
                hidden = hidden()
            if bool(hidden):
                return
        except Exception:
            # Public-API bridge fallback remains safe and only touches the two
            # caption properties, not 8 native button states.
            button.title = '\u00a0'
            button.title_color = ui.Color.rgb(0,0,0,0)
            return
        self._silence_choice_title(button)

    def _post(self,name,lock=False,**payload):
        # Pyto button callbacks may arrive on different Python threads. This
        # lock covers only Python state; NEVER hold it across a UIKit call.
        with self._lock:
            if (self._closed or not self.opened or self._answer_pending
                    or self._applying or not self._render_ready):
                return
            self._request_serial = max(self._request_serial,
                                       int(self.current.get('ack_id', 0))) + 1
            request_id = self._request_serial
            payload['token'] = int(self.current.get('token', -1))
            payload['request_id'] = request_id
            if lock:
                self._answer_pending = True
                self._pending_request = request_id
                self._pending_token = payload['token']
                self._pending_since = time.monotonic()
            # Queue insertion is a bounded Python operation. Keeping it inside
            # this lock preserves key/confirm FIFO order across callback threads.
            try:
                result = self.post_command(name, **payload)
                if result is False:
                    raise RuntimeError('input queue rejected command')
            except Exception as exc:
                self._answer_pending = False
                self._pending_request = 0
                self.last_error = 'input enqueue: ' + repr(exc)
                self._last_submitted = None
                print('STUDY input warning:', repr(exc))

    def _key(self,key):
        if self.current.get('modal_kind') == 'playtime':
            if key == 'confirm':
                self._post('playtime_confirm',lock=True)
            elif key == 'erase':
                self._post('playtime_erase')
            else:
                self._post('playtime_digit',digit=key)
            return
        if int(self.current.get('stage',3)) >= 2:
            return
        if key == 'confirm':
            self._post('study_confirm',lock=True)
        elif key == 'erase':
            self._post('study_erase')
        else:
            self._post('study_digit',digit=key)

    def _choose(self,index):
        if index not in self.current.get('wrong_choices',()):
            self._post('study_choose',index=index,lock=True)

    def request_resync(self):
        """An independent, rate-limited recovery key, never a skip/reset key.

        This may run on a Pyto callback thread. It ONLY posts a FIFO barrier and
        requests a redraw; all UIKit work still uses the retained main-thread
        transaction. Pending/partially drawn answer keys cannot block recovery.
        """
        now = time.monotonic()
        with self._lock:
            if self._closed or not self.opened or now-self._manual_sync_at < 1.0:
                return False
            self._manual_sync_at = now
            s = self._published_snapshot or self.current
            self._request_serial = max(self._request_serial, int(s.get('ack_id',0)))+1
            name = 'playtime_sync' if s.get('modal_kind') == 'playtime' else 'study_sync'
            try:
                accepted = self.post_command(name, token=int(s.get('token',-1)),
                                             request_id=self._request_serial)
                if accepted is False:
                    raise RuntimeError('input queue rejected resync')
            except Exception as exc:
                self.last_error = 'resync enqueue: ' + repr(exc)
            # A manual redraw keeps the same token, buffer, quotas and scores.
            self._last_submitted = None
            self._rendered_math_key = None
            # A user tap may retry even if the one automatic rescue was also
            # lost. It still cannot re-enter an executing UIKit transaction.
            if self._pending and not self._applying:
                self._dispatch_rescued = False
                self._dispatch_since = now-2.01
        self._report('manual_resync')
        self.publish(dict(s))
        return True

    def publish(self,snapshot):
        """Latest-only mailbox with a bounded dispatch recovery watchdog.

        FIX81 covered a lost *command* but not a lost *UI delivery*. A pending
        UI callback that never began could latch _pending forever. After two
        seconds send ONE rescue callback. Both carry a generation; a delayed
        original cannot redraw stale content or run a second native transaction.
        Do not retry a callback already executing UIKit, or flood a blocked main
        queue. A pending cycle has at most its original + one rescue dispatch.
        """
        snap = dict(snapshot)
        now = time.monotonic()
        signature = (snap.get('modal_kind', 'study'), bool(snap.get('active')),
                     int(snap.get('revision',-1)), int(snap.get('ack_id',0)))
        schedule = False
        rescue = False
        stalled = False
        with self._lock:
            if self._closed:
                return
            self._published_snapshot = snap
            if (self._applying and not self._apply_stall_reported
                    and now-self._apply_started_at >= 3.0):
                self._apply_stall_reported = True
                stalled = True
            if (self._answer_pending and now-self._pending_since >= 1.5
                    and now-self._last_sync_at >= 1.5):
                self._last_sync_at = now
                self._sync_count += 1
                self._request_serial = max(self._request_serial,int(snap.get('ack_id',0)))+1
                try:
                    sync_name = 'playtime_sync' if snap.get('modal_kind') == 'playtime' else 'study_sync'
                    result = self.post_command(sync_name, token=int(snap.get('token',-1)),
                                               request_id=self._request_serial)
                    if result is False:
                        raise RuntimeError('input queue rejected sync')
                except Exception as exc:
                    self.last_error = 'sync enqueue: ' + repr(exc)
            changed = signature != self._last_submitted or bool(self.last_error)
            if changed:
                self._last_submitted = signature
                self._publication_serial += 1
                snap['_publication'] = self._publication_serial
                self._latest = snap
                if snap.get('active'):
                    self._hidden_ack.clear()
            if self._pending:
                if (not self._applying and not self._dispatch_rescued
                        and now-self._dispatch_since >= 2.0):
                    self._dispatch_rescued = True
                    self._dispatch_recoveries += 1
                    rescue = schedule = True
            elif (self._latest is not None or self._deferred_layout is not None) and not self._applying:
                self._pending = True
                self._dispatch_rescued = False
                schedule = True
        if schedule:
            self._schedule_apply()
        if rescue:
            self._report('ui_dispatch_rescue')
        if stalled:
            self._report('ui_apply_stalled')

    def _schedule_apply(self):
        with self._lock:
            if self._closed:
                self._pending = False
                return
            self._dispatch_serial += 1
            ticket = self._dispatch_ticket = self._dispatch_serial
            self._dispatch_since = time.monotonic()
        try:
            # Keep just a weak reference in the native dispatch queue so closing
            # the game cannot leave its entire world retained by a late callback.
            import weakref
            ref = weakref.ref(self)
            def apply():
                overlay = ref()
                if overlay is not None:
                    overlay._apply_latest(ticket)
            mainthread.run_async(apply)
        except Exception as exc:
            with self._lock:
                if ticket == self._dispatch_ticket:
                    self._pending = False
                    self._last_submitted = None
                    self.last_error = repr(exc)
            self._report('ui_dispatch_error', exc)

    def _apply_latest(self, ticket=None):
        with self._lock:
            if (self._closed or (ticket is not None and ticket != self._dispatch_ticket)
                    or self._applying):
                return
            snap = self._latest
            self._latest = None
            resize = self._deferred_layout
            self._deferred_layout = None
            self._applying = True
            self._apply_started_at = time.monotonic()
            self._apply_stall_reported = False
        failed = False
        try:
            if resize is not None:
                self._layout_native(*resize)
            if snap is not None:
                self._apply(snap)
            elif resize is not None and self.current:
                self._refresh(force=True)
                self._render_ready = True
            self.last_error = ''
        except Exception as exc:
            failed = True
            with self._lock:
                self.last_error = repr(exc)
                self._last_submitted = None
                self._rendered_math_key = None
                self._guard_rendered = False
                self._render_ready = False
                self._layout_signature = None
            self._report('ui_apply_error', exc)
            print('STUDY UI apply warning:',repr(exc))
        finally:
            with self._lock:
                self._applying = False
                # On failure wait for the worker heartbeat, not an immediate
                # recursive retry storm on the native main thread.
                more = not failed and (self._latest is not None or self._deferred_layout is not None)
                self._pending = bool(more)
                self._dispatch_rescued = False
            if more:
                self._schedule_apply()

    def _apply(self,snap):
        active = bool(snap.get('active'))
        with self._lock:
            publication = int(snap.get('_publication', 0))
            if publication and publication < self._applied_publication:
                return
            source_changed = snap.get('modal_kind', 'study') != self.current.get('modal_kind', 'study')
            if not source_changed and int(snap.get('revision',-1)) < int(self.current.get('revision',-1)):
                return  # An old render must not revive an already answered key.
            self._applied_publication = max(self._applied_publication, publication)
            self.current = snap
            if (source_changed or not active or int(snap.get('ack_id',0)) >= self._pending_request
                    or int(snap.get('token',-1)) != self._pending_token):
                self._answer_pending = False
                self._pending_request = 0
        self._render_ready = False
        if active:
            if not self.opened:
                self.on_modal_changed(True)
                self.opened = True
                self._rendered_math_key = None
            self.screen.user_interaction_enabled = True
            self.screen.hidden = False
        else:
            self.screen.hidden = True
            self.screen.user_interaction_enabled = False
            if self.opened:
                self.opened = False
                self.on_modal_changed(False)
            self._hidden_ack.set()
            return
        self._refresh()
        self._render_ready = True

    def _refresh(self, force=False):
        s = self.current
        if s.get('modal_kind') == 'playtime':
            self._refresh_playtime(force=force)
            return
        if self._guard_rendered:
            force = True
            self._guard_rendered = False
        stage = int(s.get('stage',0))
        complete = bool(s.get('complete'))
        math = stage < 2 and not complete
        zy = stage == 2 and not complete
        required = s.get('required', (2,2,2))
        math_key = (stage, tuple(s.get('passed',())), tuple(required), s.get('token'),
                    s.get('equation'), s.get('trigger_reason'), complete)
        if math and not force and self._rendered_math_key == math_key:
            # Number keys update only the entry/feedback, not 200 native views.
            self.entry.text = s.get('buffer') or '＿'
            self.feedback.text = s.get('feedback','')
            kind = s.get('feedback_kind')
            self.feedback.text_color = (ui.Color.rgb(.50,.94,.72,1) if kind == 'success' else
                ui.Color.rgb(1,.81,.52,1) if kind == 'retry' else ui.Color.rgb(.78,.87,.94,1))
            return
        self._rendered_math_key = math_key if math else None
        self.keys['confirm'].title = '確認'
        self.entry.font = self._font(self.geometry['math_font'],True)
        reason = s.get('trigger_reason','timer')
        headings = {'entry':'進入遊戲 · 先完成習題',
                    'death':'角色死亡 · 先完成習題',
                    'timer':'學習時間 · 遊戲已暫停'}
        self.title.text = '全部答對！' if complete else headings.get(reason,headings['timer'])
        self.resume.title = '完成，繼續' if reason == 'death' else '繼續遊戲'
        self.title.text_color = ui.Color.rgb(.99,.89,.57,1)
        passed = s.get('passed',(0,0,0))
        for i,(label,name) in enumerate(zip(self.tabs,('乘法','加減法','國語注音'))):
            label.hidden = False
            label.text = '%s  %d / %d' % (name,passed[i],required[i])
            label.text_color = ui.Color.rgb(.48,.92,.69,1) if passed[i] == required[i] else ui.Color.rgb(.91,.95,1,1)
            label.background_color = ui.Color.rgb(.12,.23,.33,1) if stage == i else ui.Color.rgb(.065,.10,.15,1)
        for view in [self.math_title,self.equation,self.entry,self.math_hint]+list(self.keys.values()):
            view.hidden = not math
        for view in self.choices:
            view.hidden = not zy
        if not zy:
            for cell in self.reading_cells+self.choice_cells:
                cell.hide()
        self.complete_text.hidden = not complete
        self.resume.hidden = not complete
        if math:
            name = s.get('math_label') or ('九九乘法' if stage == 0 else '個位數加減法')
            self.math_title.text = '%s　第 %d / %d 題' % (name,passed[stage]+1,required[stage])
            self.equation.text = s.get('equation','')
            self.entry.text = s.get('buffer') or '＿'
            self.math_hint.text = '按數字填答案，再按確認。\n「消除」刪除最後一個數字。'
        if zy:
            g = self.geometry
            x,y,w,h = g['reading']
            plan = layout_ruby_reading(s.get('characters',()),s.get('tokens',()),
                                       w,h,preferred_size=44.0)
            self.reading_plan = plan
            for i,cell in enumerate(self.reading_cells):
                if i < len(plan['cells']):
                    cell.apply(plan['cells'][i],origin=(x,y))
                else:
                    cell.hide()
            wrong = s.get('wrong_choices',())
            for i,(button,cell) in enumerate(zip(self.choices,self.choice_cells)):
                self._ensure_choice_title_hidden(button)
                button.user_interaction_enabled = i not in wrong
                button.background_color = ui.Color.rgb(.22,.10,.11,1) if i in wrong else ui.Color.rgb(.13,.23,.34,1)
                bx,by,bw,bh = g['choices'][i]
                plan = layout_ruby_choice(s['choice_characters'][i],s['choices'][i],bw,bh)
                cell.apply(plan,origin=(bx,by),disabled=i in wrong)
        self.complete_text.text = '乘法 %d 題　加減法 %d 題\n國語注音 %d 題\n全部完成！' % tuple(required)
        self.feedback.text = s.get('feedback','')
        kind = s.get('feedback_kind')
        self.feedback.text_color = (ui.Color.rgb(.50,.94,.72,1) if kind == 'success' else
                                    ui.Color.rgb(1,.81,.52,1) if kind == 'retry' else
                                    ui.Color.rgb(.78,.87,.94,1))

    def _refresh_playtime(self, force=False):
        """Reuse the 12 retained keys. Never expose the actual password text."""
        s = self.current
        if not self._guard_rendered or force:
            self._rendered_math_key = None
            self.title.text = '遊玩時間已達上限 · 遊戲已暫停'
            self.title.text_color = ui.Color.rgb(1,.80,.42,1)
            for label in self.tabs:
                label.hidden = True
            for cell in self.reading_cells + self.choice_cells:
                cell.hide()
            for button in self.choices:
                button.hidden = True
            self.resume.hidden = True
            self.complete_text.hidden = True
            for view in [self.math_title,self.equation,self.entry,self.math_hint] + list(self.keys.values()):
                view.hidden = False
            self.math_title.text = '請先休息，交由家長解鎖'
            self.equation.text = '30 分鐘'
            self.entry.font = self._font(min(self.geometry['math_font'], self.geometry['entry'][2]/9.0),True)
            self.math_hint.text = '輸入 8 位密碼，再按解鎖。\n答題、讀檔或重啟不會解除鎖定。'
            self.keys['confirm'].title = '解鎖'
            self._guard_rendered = True
        self.entry.text = s.get('buffer') or '＿'
        self.feedback.text = s.get('feedback','')
        self.feedback.text_color = (ui.Color.rgb(1,.81,.52,1) if s.get('feedback_kind') == 'retry'
                                    else ui.Color.rgb(.78,.87,.94,1))

    def layout(self,width,height):
        # Pyto layout callbacks can occur while a native property is being set.
        # Coalesce these instead of recursively repainting the whole quiz.
        requested = (float(width),float(height))
        with self._lock:
            if self._closed:
                return
            if self._applying:
                if requested != self.viewport:
                    self._deferred_layout = requested
                return
            self._applying = True
            self._apply_started_at = time.monotonic()
            self._apply_stall_reported = False
        try:
            changed = self._layout_native(*requested)
            if changed and self.current:
                self._refresh(force=True)
                self._render_ready = True
        except Exception as exc:
            self._layout_signature = None
            self._rendered_math_key = None
            self._last_submitted = None
            self._render_ready = False
            self.last_error = repr(exc)
            self._report('ui_layout_error', exc)
        finally:
            with self._lock:
                self._applying = False
                more = (self._latest is not None or self._deferred_layout is not None) and not self._pending
                if more:
                    self._pending = True
                    self._dispatch_rescued = False
            if more:
                self._schedule_apply()

    def _layout_native(self,width,height):
        self.viewport = (float(width),float(height))
        side = 24.0 if is_ipad() else 72.0
        left,top,right,bottom = side,12.0,side,24.0
        try:
            insets = self.root.__py_view__.managed.safeAreaInsets
            left=max(left,float(insets.left)+8.0)
            right=max(right,float(insets.right)+8.0)
            top=max(top,float(insets.top)+8.0)
            bottom=max(bottom,float(insets.bottom)+8.0)
        except Exception:
            pass
        signature = tuple(round(v,2) for v in (float(width),float(height),left,top,right,bottom))
        if signature == self._layout_signature:
            return False
        g = self.geometry = layout_geometry(width,height,(left,top,right,bottom))
        self.screen.frame = g['screen']
        self.panel.frame = g['panel']
        for name in ('title','math_title','equation','entry','math_hint','feedback','complete_text','resume'):
            getattr(self,name).frame = g[name]
        self.title.font = self._font(g['title_font'],True)
        for label,frame in zip(self.tabs,g['tabs']):
            label.frame = frame
            label.font = self._font(g['label_font'],True)
        self.math_title.font = self._font(g['label_font'],True)
        self.equation.font = self._font(g['math_font'],True)
        self.entry.font = self._font(g['math_font'],True)
        self.math_hint.font = self._font(max(11,g['label_font']-2))
        self.feedback.font = self._font(g['feedback_font'])
        for key,button in self.keys.items():
            button.frame = g['keys'][key]
            button.font = self._font(min(g['key_font'],20) if key in ('erase','confirm') else g['key_font'],True)
        for button,frame in zip(self.choices,g['choices']):
            button.frame = frame
            button.font = self._font(g['choice_font'])
        # Recovery uses part of the existing footer, never covers an answer.
        fx,fy,fw,fh = g['feedback']
        rw = min(106.0, max(76.0, fw*.18))
        self.feedback.frame = (fx,fy,max(40.0,fw-rw-8.0),fh)
        self.recover.frame = (fx+fw-rw,fy+max(0.0,(fh-32.0)*.5),rw,min(fh,36.0))
        self.recover.font = self._font(12.0,True)
        self._layout_signature = signature
        return True
