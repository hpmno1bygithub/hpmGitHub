# -*- coding: utf-8 -*-
"""FIX101 bounded, local learning-transport diagnostics.

No questions, answers, text input or password buffers are logged. File I/O is
never performed on the UIKit/game command path: one lazy daemon consumes only
the latest requested report. Two bounded files are kept, not one per question.
"""
from collections import deque
import json
import math
import os
import threading
import time

_FIELDS = frozenset(('modal_kind', 'active', 'stage', 'passed', 'required', 'token',
    'revision', 'ack_id', 'pending_request', 'pending_seconds', 'dispatch_seconds', 'apply_seconds', 'answer_pending', 'ui_pending',
    'ui_applying', 'dispatch_recoveries', 'error_type', 'study_elapsed',
    'play_elapsed', 'completed_rounds', 'pending_commands'))


def safe_fields(details):
    out = {}
    for key,value in (details or {}).items():
        if key not in _FIELDS:
            continue
        if type(value) in (bool,int):
            out[key] = value
        elif type(value) is float and math.isfinite(value):
            out[key] = round(value,3)
        elif type(value) is str:
            out[key] = value[:64]
        elif key in ('passed','required') and isinstance(value,(tuple,list)):
            out[key] = [v for v in value[:3] if type(v) is int]
    return out


class StudyDiagnostics:
    def __init__(self, save_dir=None):
        self.path = os.path.join(str(save_dir),'study_diagnostics_fix101.json') if save_dir else None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._events = deque(maxlen=48)
        self._pending = None
        self._thread = None
        self._closed = False
        self._last_signature = None
        self._last_report_at = -1000.0
        self.write_error = ''
        self.write_count = 0

    def report(self, event, details=None):
        # Event names are our fixed identifiers; discard caller-supplied prose.
        allowed = frozenset(('manual_resync','ui_dispatch_rescue','ui_dispatch_error',
            'ui_apply_error','ui_apply_stalled','ui_layout_error','modal_command_error','modal_cancel_error',
            'modal_snapshot_error','modal_entry_error','modal_bridge_error'))
        event = event if event in allowed else 'modal_bridge_error'
        details = safe_fields(details)
        now = time.monotonic()
        signature = (event,details.get('error_type'),details.get('token'))
        with self._lock:
            if self._closed:
                return False
            if signature == self._last_signature and now-self._last_report_at < 2.0:
                return False
            self._last_signature = signature
            self._last_report_at = now
            self._events.append(dict(event=event,monotonic_seconds=round(now,3),state=details))
            if not self.path:
                return True
            self._pending = {'version':'FIX101','event_count':len(self._events),
                             'events':list(self._events)}
            if self._thread is None:
                self._thread = threading.Thread(target=self._run,name='StudyDiagnosticWriter',daemon=True)
                self._thread.start()
            self._wake.set()
        return True

    def _run(self):
        while True:
            self._wake.wait()
            with self._lock:
                data = self._pending
                self._pending = None
                self._wake.clear()
                closed = self._closed
            if data is not None:
                try:
                    self._write(data)
                    self.write_error = ''
                except Exception as exc:
                    self.write_error = type(exc).__name__
            if closed:
                return

    def _write(self, data):
        raw = (json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        if len(raw)>65536:
            raise ValueError('diagnostic report too large')
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory,exist_ok=True)
        temp = self.path+'.tmp'
        try:
            with open(temp,'wb') as f:
                f.write(raw)
            # Preserve the preceding useful fault report; both are bounded.
            if os.path.isfile(self.path):
                os.replace(self.path,self.path+'.previous')
            os.replace(temp,self.path)
            self.write_count += 1
        finally:
            try:
                if os.path.isfile(temp):os.remove(temp)
            except OSError:
                pass

    def close(self):
        # Never join a storage worker from UIKit; it drains its final report.
        with self._lock:
            self._closed = True
            self._wake.set()


def pump_learning_modal(game, overlay, diagnostics=None):
    """One recoverable modal frame. Exceptions cannot kill the game worker.

    The caller still owns the hard simulation/hide barrier. Recovery does NOT
    resume the world, call start_round(), discard a score or change the timer.
    """
    ok = True
    steps = (('modal_command_error',lambda:game._process_ui_commands()),
             ('modal_cancel_error',lambda:game._cancel_study_input()),
             ('modal_snapshot_error',lambda:overlay.publish(game.learning_overlay_snapshot())))
    for event,operation in steps:
        try:
            operation()
        except Exception as exc:
            ok = False
            if diagnostics is not None:
                try:
                    study = game.study
                    diagnostics.report(event,{'error_type':type(exc).__name__,
                        'active':bool(study.active),'stage':study.stage,
                        'token':study.token,'revision':study.revision,
                        'passed':list(study.passed),'required':list(study.required),
                        'study_elapsed':study.elapsed,'completed_rounds':study.completed_rounds,
                        'play_elapsed':game.playtime_guard.elapsed,
                        'pending_commands':game.ui_commands.pending})
                except Exception:
                    pass
    return ok
