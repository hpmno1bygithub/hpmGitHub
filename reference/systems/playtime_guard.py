# -*- coding: utf-8 -*-
"""FIX97: a save-slot-independent active-play allowance and password gate.

No wall clock or physics multiplier is used. The host supplies the very same
foreground/playable duration used by StudyChallenge. A small write-ahead
reservation bounds I/O to once per five seconds and prevents force-relaunch
from restoring uncharged playtime. A clean pause/exit commits exact elapsed
seconds; a force kill can charge at most the unfinished reservation (5 s).

This is a local gameplay control, not OS-level parental control: someone with
access to edit Python source or delete its persistent data can alter it.
"""
import hashlib
import hmac
import json
import math
import os
import tempfile

LIMIT_SECONDS = 1800.0
RESERVATION_SECONDS = 5.0
FORMAT = 1
_PASSWORD_DIGEST = 'c7e1538a04f392bbe7ed8c0c2b31c80f47165c50037680fe8f9f7555238a226a'
_PASSWORD_DOMAIN = b'PytoRPG-time-limit-v1:'


class PlaytimeGuard:
    def __init__(self, state_path=None):
        self.state_path = str(state_path) if state_path else None
        self.ready = False
        self.elapsed = 0.0
        self.locked = False
        self.unlock_count = 0
        self.revision = 0
        self.token = 0
        self.ack_id = 0
        self.feedback = ''
        self.feedback_kind = 'info'
        self.persistence_error = ''
        self._buffer = ''  # session-only; NEVER exported/logged
        self._reserved_until = 0.0
        self._last_written = None

    @classmethod
    def from_save_dir(cls, save_dir):
        return cls(os.path.join(str(save_dir), 'playtime_guard_v1.json'))

    def _changed(self, new_token=False):
        self.revision += 1
        if new_token:
            self.token += 1

    def _engage(self, storage_error=False):
        if not self.locked:
            self._buffer = ''
            self._changed(new_token=True)
        self.locked = True
        self.feedback_kind = 'retry' if storage_error else 'info'
        self.feedback = ('無法保存遊玩時數，遊戲已暫停。請確認儲存空間後再輸入密碼。'
                         if storage_error else '已累積遊玩 30 分鐘，請由家長輸入密碼解鎖。')

    def ensure_restored(self):
        if self.ready:
            return
        self.ready = True
        if not self.state_path:
            return
        try:
            with open(self.state_path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or data.get('format') != FORMAT:
                raise ValueError('invalid playtime format')
            elapsed = data.get('charged_seconds')
            locked = data.get('locked')
            count = data.get('unlock_count', 0)
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                    or not 0 <= elapsed <= LIMIT_SECONDS or type(locked) is not bool
                    or type(count) is not int or count < 0):
                raise ValueError('invalid playtime record')
            self.elapsed = float(elapsed)
            self.unlock_count = count
            self._reserved_until = self.elapsed
            self._last_written = (self.elapsed, locked, self.unlock_count)
            if locked or self.elapsed + 1e-8 >= LIMIT_SECONDS:
                self._engage()
            self._changed(new_token=True)
        except FileNotFoundError:
            # First use after upgrading: historical playtime wasn't recorded.
            pass
        except Exception as exc:
            # A malformed record must not silently grant another 30 minutes.
            self.elapsed = LIMIT_SECONDS
            self.persistence_error = type(exc).__name__
            self._engage()
            self.feedback = '遊玩時數紀錄無法讀取，請輸入家長密碼重新授權。'
            self._write(self.elapsed, True, self.unlock_count)

    def _write(self, charged, locked, count):
        signature = (float(charged), bool(locked), int(count))
        if signature == self._last_written:
            return True
        if not self.state_path:
            self._last_written = signature
            return True
        tmp = None
        try:
            directory = os.path.dirname(os.path.abspath(self.state_path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix='.playtime-', suffix='.tmp', dir=directory)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump({'format': FORMAT, 'charged_seconds': signature[0],
                           'locked': signature[1], 'unlock_count': signature[2]},
                          handle, separators=(',', ':'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.state_path)
            self._last_written = signature
            self.persistence_error = ''
            return True
        except Exception as exc:
            # Never include input/password material in the diagnostic.
            self.persistence_error = type(exc).__name__
            return False
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def advance(self, seconds):
        self.ensure_restored()
        if self.locked:
            return False
        try:
            dt = float(seconds)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(dt) or dt <= 0.0:
            return False
        next_elapsed = min(LIMIT_SECONDS, self.elapsed + dt)
        due = next_elapsed + 1e-8 >= LIMIT_SECONDS
        if next_elapsed > self._reserved_until + 1e-8:
            reserved = min(LIMIT_SECONDS,
                           math.ceil((next_elapsed - 1e-9) / RESERVATION_SECONDS) * RESERVATION_SECONDS)
            if not self._write(reserved, due, self.unlock_count):
                self._engage(storage_error=True)
                return True
            self._reserved_until = reserved
        self.elapsed = next_elapsed
        if due:
            self.elapsed = LIMIT_SECONDS
            self._engage()
            self._write(self.elapsed, True, self.unlock_count)
            return True
        return False

    def checkpoint(self, force=False):
        """Clean pause/exit removes unused reservation; never resets the cycle."""
        if not self.ready or not force:
            return False
        ok = self._write(self.elapsed, self.locked, self.unlock_count)
        if ok:
            self._reserved_until = self.elapsed
        else:
            self._engage(storage_error=True)
        return ok

    def snapshot(self):
        return {'modal_kind': 'playtime', 'active': self.locked,
                'complete': False, 'token': self.token, 'revision': self.revision,
                'ack_id': self.ack_id, 'stage': 0, 'passed': (0, 0, 0),
                'required': (0, 0, 0), 'buffer': '●' * len(self._buffer),
                'feedback': self.feedback, 'feedback_kind': self.feedback_kind,
                'elapsed': self.elapsed, 'limit_seconds': LIMIT_SECONDS,
                'seconds_left': max(0, int(math.ceil(LIMIT_SECONDS-self.elapsed)))}

    def handle(self, command, payload):
        payload = payload if isinstance(payload, dict) else {}
        try:
            request = max(0, int(payload.get('request_id', 0)))
        except (TypeError, ValueError, OverflowError):
            request = 0
        try:
            if request and request <= self.ack_id:
                return False
            if command == 'playtime_sync':
                return False  # ack barrier only, never an unlock
            if not self.locked or payload.get('token') != self.token:
                return False
            if command == 'playtime_digit':
                digit = payload.get('digit')
                if not isinstance(digit, str) or len(digit) != 1 or digit not in '0123456789':
                    return False
                if len(self._buffer) >= 8:
                    return False
                self._buffer += digit
                self.feedback = '請輸入 8 位密碼，再按「解鎖」。'
                self.feedback_kind = 'info'
                return True
            if command == 'playtime_erase':
                self._buffer = self._buffer[:-1]
                return True
            if command != 'playtime_confirm':
                return False
            digest = hashlib.sha256(_PASSWORD_DOMAIN + self._buffer.encode('ascii')).hexdigest()
            valid = len(self._buffer) == 8 and hmac.compare_digest(digest, _PASSWORD_DIGEST)
            self._buffer = ''
            self._changed(new_token=True)
            if not valid:
                self.feedback = '密碼不正確，請重新輸入。遊戲仍維持暫停。'
                self.feedback_kind = 'retry'
                return True
            # Persist authorization BEFORE releasing the simulation gate. A
            # duplicate/late confirm cannot authorize a second allowance.
            if not self._write(0.0, False, self.unlock_count + 1):
                self._engage(storage_error=True)
                return True
            self.elapsed = 0.0
            self._reserved_until = 0.0
            self.unlock_count += 1
            self.locked = False
            self.feedback = ''
            self.feedback_kind = 'info'
            return True
        finally:
            self.ack_id = max(self.ack_id, request)
            self.revision += 1
