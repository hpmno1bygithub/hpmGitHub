# -*- coding: utf-8 -*-
"""FIX86: persistent math cycles plus entry/death/timed learning intermission.

Only the simulation thread mutates this object. The UI submits token-tagged
commands and receives detached snapshots. No network, keyboard, UIKit, or world
simulation is involved in checking answers. Game saves cannot rewind this clock.
"""
import json
import copy
import time
from collections import deque
import math
import os
import random
import re
import tempfile

INTERVAL_SECONDS = 180.0
REQUIRED_CORRECT = (2, 2, 2)
MAX_ZHUYIN_WRONG = 2  # The third distinct wrong choice replaces the question.
STATE_VERSION = 4
_SYLLABLE = re.compile(r"(?:˙[ㄅ-ㄩ]{1,3}|[ㄅ-ㄩ]{1,3}[ˊˇˋ]?)\Z")
from systems.study_fallback import FALLBACK_QUESTIONS
from systems.study_selection import QuestionSelection, TYPE_LABELS
from systems.study_math_decks import MathQuestionDecks
from systems.study_profiles import PROFILES, validate_profile


def validate_bank(rows):
    """Accept only unique four-choice, single-syllable cloze questions."""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("注音題庫不可為空")
    clean, ids, references = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("題目須為物件")
        qid = str(row.get("id", ""))
        tokens = row.get("tokens", ())
        choices = row.get("choices", ())
        answer = row.get("answer", "")
        if not qid or qid in ids:
            raise ValueError("注音題目 id 重複或遺失")
        if not isinstance(tokens, (list, tuple)) or not 3 <= len(tokens) <= 32:
            raise ValueError("注音短句須為 3–32 個音節／標點")
        if list(tokens).count("__") != 1:
            raise ValueError("每題須恰好挖除一個字")
        if any(not isinstance(t, str) or (t not in ("__", "，", "。", "！", "？", "、")
                   and not _SYLLABLE.fullmatch(t)) for t in tokens):
            raise ValueError("短句只能包含注音、聲調、標點及 __")
        if not isinstance(choices, (list, tuple)) or len(choices) != 4:
            raise ValueError("每題須有四個選項")
        if any(not isinstance(c, str) or not _SYLLABLE.fullmatch(c) for c in choices):
            raise ValueError("每個選項須是一個字的注音")
        if len(set(choices)) != 4 or choices.count(answer) != 1:
            raise ValueError("注音選項須不同，且恰有一個正確答案")
        reference = row.get("reference_text", "")
        letters = row.get("choice_characters", ())
        if not isinstance(reference, str) or len(reference) != len(tokens):
            raise ValueError("國字與注音須逐字對位")
        punctuation = ("，", "。", "！", "？", "、")
        for char, token in zip(reference, tokens):
            if token in punctuation:
                if char != token:
                    raise ValueError("國字與注音的標點位置不符")
            elif not "\u3400" <= char <= "\u9fff":
                raise ValueError("短句须使用國字，每字對應一組注音")
        if (not isinstance(letters, (list, tuple)) or len(letters) != 4
                or any(not isinstance(c, str) or len(c) != 1
                       or not "\u3400" <= c <= "\u9fff" for c in letters)
                or len(set(letters)) != 4):
            raise ValueError("四個選項須各有一個不同的國字")
        blank_index = list(tokens).index("__")
        if letters[list(choices).index(answer)] != reference[blank_index]:
            raise ValueError("正確選項的國字與挖空位置不符")
        if reference in references:
            raise ValueError("題庫不可有重複短句")
        category = row.get("category", "日常閱讀")
        question_type = row.get("question_type", "reading")
        if not isinstance(category, str) or not 1 <= len(category) <= 16 or question_type not in TYPE_LABELS:
            raise ValueError("題目分類無效")
        references.add(reference)
        characters = list(reference)
        characters[blank_index] = "__"  # Never send the missing character to UI.
        clean.append({"id": qid, "tokens": list(tokens), "choices": list(choices),
                      "answer": answer, "reference_text": reference,
                      "characters": characters, "choice_characters": list(letters),
                      "category": category, "question_type": question_type})
        ids.add(qid)
    return clean


def load_bank(project_root):
    path = os.path.join(str(project_root), "assets", "study_questions.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return validate_bank(data["questions"])
    except Exception as exc:
        print("STUDY bank fallback:", repr(exc))
        return validate_bank(FALLBACK_QUESTIONS)


class ActivePlayClock:
    """Measure foreground PLAYING wall time, not game-time or physics ticks.

    Both ends of an interval must be playable. Long suspension gaps are not
    replayed; the host separately checks foreground state on the main thread.
    """
    def __init__(self, max_gap=1.0):
        self.max_gap = float(max_gap)
        self.last = None
        self.was_playing = False

    def sample(self, now, playing):
        now = float(now)
        if not math.isfinite(now):
            self.reset()
            return 0.0
        delta = 0.0 if self.last is None else now - self.last
        eligible = self.was_playing and bool(playing) and 0.0 <= delta <= self.max_gap
        self.last = now
        self.was_playing = bool(playing)
        return delta if eligible else 0.0

    def reset(self):
        self.last = None
        self.was_playing = False


class StudyChallenge:
    def __init__(self, bank=None, interval=None, rng=None, state_path=None, profile=0):
        self.bank = validate_bank(bank if bank is not None else FALLBACK_QUESTIONS)
        self.by_id = {q["id"]: q for q in self.bank}
        self.selection = QuestionSelection(self.bank)
        self.profile = validate_profile(profile)
        preset = PROFILES[self.profile]
        self.required = tuple(preset["required"])
        self._configured_required = self.required
        self.interval = float(preset["interval"] if interval is None else interval)
        self._configured_interval = self.interval
        if not math.isfinite(self.interval) or self.interval <= 0.0:
            raise ValueError("習題間隔必須為有限數值")
        self.rng = rng if rng is not None else random.Random()
        self.state_path = str(state_path) if state_path else None
        # Loading is deferred until the title New/Load operation has completed;
        # temporary GameApps built by map workers never write learning progress.
        self.ready = False
        self.active = False
        self.elapsed = 0.0
        self.passed = [0, 0, 0]
        self.stage = 0
        self.question = None
        self.math_tasks = []
        self.math_recent = []
        self.math_decks = MathQuestionDecks()
        self.buffer = ""
        self.wrong_choices = set()
        self.token = 0
        self.revision = 0
        self.feedback = ""
        self.feedback_kind = "info"
        self.completed_rounds = 0
        self.zhuyin_deck = []
        self.last_zhuyin_id = ""
        self._checkpoint_elapsed = 0.0
        self._dirty = False
        self.persistence_error = ""
        self.bank_warning = ""
        # These latches follow the StudyChallenge across map adoption, but not
        # process relaunch. Entry must be checked again after each new launch.
        self.entry_handled = False
        self.death_latched = False
        self.trigger_reason = "timer"
        # FIX81 transport state is session-local, never game/save progress.
        self.ack_id = 0
        self.last_command_error = ""
        self.command_history = deque(maxlen=48)

    @classmethod
    def from_project_root(cls, project_root, save_dir=None, profile=0):
        path = os.path.join(save_dir, "study_progress_v1.json") if save_dir else None
        return cls(load_bank(project_root), state_path=path, profile=profile)

    @property
    def complete(self):
        return self.active and tuple(self.passed) == tuple(self.required) and self.stage == 3

    def configure_profile(self, profile):
        """Select the NEXT round, without clearing a pending task or any deck.

        Persisted active rounds retain their original quota even after changing
        settings on the title screen. An inactive timer keeps elapsed seconds.
        """
        self.profile = validate_profile(profile)
        preset = PROFILES[self.profile]
        self._configured_required = tuple(preset["required"])
        self._configured_interval = float(preset["interval"])
        if not self.active:
            self.required = self._configured_required
            self.interval = self._configured_interval
            # Do not decrease elapsed on a settings change. advance() will
            # open a due gate before another playable interval is allowed.
        self._changed()

    def _changed(self, new_token=False):
        self.revision += 1
        if new_token:
            self.token += 1
        self._dirty = True

    def ensure_restored(self):
        if self.ready:
            return
        self.ready = True
        if not self.state_path or not os.path.isfile(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                original = fh.read()
            data = json.loads(original)
            if isinstance(data, dict) and data.get("version") in (1, 2, 3):
                # Keep the original learning record once, independently of world
                # saves. Never overwrite a previous pre-upgrade backup.
                try:
                    with open(self.state_path + (".pre_FIX86.bak" if data.get("version") in (1, 2) else ".pre_FIX97.bak"), "x", encoding="utf-8") as backup:
                        backup.write(original)
                except FileExistsError:
                    pass
                except OSError as exc:
                    print("STUDY migration backup unavailable:", repr(exc))
            self._restore(data)
            self._changed(new_token=True)  # No pre-relaunch touch token is valid.
            if data.get("version") != STATE_VERSION or not isinstance(data.get("math_decks"), dict):
                self.checkpoint(force=True)  # Commit migration before showing its pending task.
        except Exception as exc:
            # A corrupt state must not unlock a pending challenge or crash Pyto.
            print("STUDY state recovery:", repr(exc))
            self.elapsed = self.interval
            self.passed = [0, 0, 0]
            self.zhuyin_deck = []
            # Only this explicit invalid-record recovery may rebuild a round.
            # A late validation failure can have assigned self.active already.
            self.active = False
            self.start_round()
            self.feedback = "學習紀錄已重新建立，請完成這一輪習題。"
            self._changed()
            self.checkpoint(force=True)

    def advance(self, seconds):
        self.ensure_restored()
        if self.active:
            return False
        try:
            dt = float(seconds)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(dt) or dt <= 0.0:
            return False
        self.elapsed = min(self.interval, self.elapsed + dt)
        self._checkpoint_elapsed += dt
        self._dirty = True
        if self.elapsed + 1e-8 >= self.interval:
            self.start_round()
            return True
        self.checkpoint()
        return False

    def require_event(self, reason):
        """Coalesce mandatory triggers without erasing an unfinished round."""
        reason = str(reason)
        if reason not in ("entry", "death", "timer"):
            raise ValueError("無效的學習模式觸發原因")
        self.ensure_restored()
        self.entry_handled = True
        if self.active:
            # Death takes priority for the heading, never for clearing answers.
            if reason == "death" and self.trigger_reason != "death":
                self.trigger_reason = "death"
                self._changed()
                self.checkpoint(force=True)
            return False
        self.start_round(reason=reason)
        return True

    def enter_game(self, force=False):
        """Called only after the title's chosen world is ready, not by workers."""
        if self.entry_handled and not force:
            return False
        return self.require_event("entry")

    def clear_death_context(self):
        """Non-fatal mode cannot start a death gate or keep a death latch.

        A saved, unfinished death exercise still has to be completed. Re-label
        it as the entry/pending gate instead of discarding scores or reserving
        another set of questions. Do not force lazy restore before title start.
        """
        self.death_latched = False
        if self.ready and self.active and self.trigger_reason == "death":
            self.trigger_reason = "entry"
            self._changed()
            self.checkpoint(force=True)
            return True
        return False

    def observe_health(self, hp):
        """One death event per alive-to-dead episode, including loaded zero HP."""
        dead = float(hp) <= 0.0
        if not dead:
            self.death_latched = False
            return False
        if self.death_latched:
            return False
        self.death_latched = True
        self.require_event("death")
        return True

    def start_round(self, reason="timer"):
        """Start ONE selected preset; an active round is never replaced.

        FIX101: a final guard here also covers accidental direct/repeated calls,
        not only advance()/require_event(). Completing all answers still leaves
        active True until the player presses Continue.
        """
        if self.active:
            return False
        self.required = self._configured_required
        self.interval = self._configured_interval
        self.ready = True
        self.entry_handled = True
        self.trigger_reason = str(reason) if reason in ("entry", "death", "timer") else "timer"
        self.active = True
        self.elapsed = self.interval
        self.stage = 0
        self.passed = [0, 0, 0]
        self.buffer = ""
        self.wrong_choices.clear()
        # FIX86 bags live across gates, deaths, new games and process relaunch.
        # Reserving all four tasks is persisted atomically with this gate; a
        # wrong answer only retries the current task, never consumes another id.
        self.math_tasks = [self.math_decks.draw("multiply", self.rng) for _ in range(self.required[0])]
        self.math_tasks.extend(self._arithmetic_tasks(self.required[1]))
        self.question = dict(self.math_tasks[0])
        self.feedback = "先完成 %d 題乘法，再完成加減法與注音。" % self.required[0]
        self.feedback_kind = "info"
        self._changed(new_token=True)
        self.checkpoint(force=True)
        return True

    def _arithmetic_pair(self):
        """Compatibility API: one single-digit + one mixed task, opposite ops."""
        return self._arithmetic_tasks(2)

    def _arithmetic_tasks(self, count):
        """Alternate single / mixed. Never draw a discarded sixth task for 5.

        Each complete pair includes one + and one -. Odd quotas end with a
        single-digit task; the persistent pool scheduler balances +/- across
        rounds and still exhausts all 90 single-digit facts before repetition.
        """
        result = []
        first_op = None
        for index in range(int(count)):
            if index % 2 == 0:
                level = "single"
                first_op = self.math_decks.choose_single_operator(self.rng)
                op = first_op
            else:
                level = "mixed"
                op = "−" if first_op == "+" else "+"
            key = level + ("_add" if op == "+" else "_subtract")
            task = self.math_decks.draw(key, self.rng)
            result.append(task)
            self.math_recent.append("%s:%s:%s" % (task["a"], op, task["b"]))
            self.math_recent = self.math_recent[-48:]
        return result

    def _next_zhuyin(self):
        if not self.zhuyin_deck:
            self.zhuyin_deck = list(self.by_id)
        # Ranking only considers unused ids. It cannot reinsert a used question.
        qid = self.selection.choose(self.zhuyin_deck, self.last_zhuyin_id, self.rng)
        self.last_zhuyin_id = qid
        base = self.by_id[qid]
        choices = list(base["choices"])
        self.rng.shuffle(choices)
        self.question = {"kind": "zhuyin", "id": qid, "choices": choices}
        self.wrong_choices.clear()
        self.buffer = ""

    def _correct(self):
        if self.stage == 2:
            self.selection.record_solved(self.question["id"])
        self.passed[self.stage] += 1
        old_stage = self.stage
        if self.passed[self.stage] >= self.required[self.stage]:
            self.stage += 1
        self.buffer = ""
        self.wrong_choices.clear()
        if self.stage == 3:
            self.question = None
            self.feedback = "三種題型都完成了！按下「繼續遊戲」再出發。"
        elif self.stage == 2:
            self._next_zhuyin()
            self.feedback = ("答對了！接著完成注音題。" if old_stage != 2 else
                             "答對了！再答對 %d 題注音就完成。" % (self.required[2] - self.passed[2]))
        else:
            index = self.passed[0] if self.stage == 0 else self.required[0] + self.passed[1]
            self.question = dict(self.math_tasks[index])
            self.feedback = "答對了！接著完成加減法。" if old_stage != self.stage else "答對了！請回答下一題。"
        self.feedback_kind = "success"
        self._changed(new_token=True)
        self.checkpoint(force=True)

    @staticmethod
    def _math_answer(question):
        a, b = int(question["a"]), int(question["b"])
        return a * b if question["op"] == "×" else a + b if question["op"] == "+" else a - b

    def _write_command_diagnostics(self, reason):
        """Write only on recovery, not on number-key/UI hot paths."""
        if not self.state_path:
            return
        try:
            path = os.path.join(os.path.dirname(self.state_path), "study_recovery_fix81.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"reason": str(reason), "time": time.time(),
                           "revision": self.revision, "token": self.token,
                           "stage": self.stage, "passed": list(self.passed),
                           "active": self.active, "ack_id": self.ack_id,
                           "commands": list(self.command_history)}, fh,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    def handle(self, command, payload):
        """Acknowledge EVERY intent, including rejected/duplicate/stale taps.

        A confirm never leaves the UI waiting for a revision that will not come.
        Answer transitions are transactional: an exception during selection
        rolls back to the same question/score rather than killing the game loop.
        """
        command = str(command)
        payload = payload if isinstance(payload, dict) else {}
        try:
            request_id = max(0, int(payload.get("request_id", 0)))
        except (ValueError, TypeError, OverflowError):
            request_id = 0
        before = None
        accepted = False
        error = ""
        try:
            if request_id and request_id <= self.ack_id:
                return False
            if command == "study_sync":
                # An ordered no-op barrier: it cannot change a score or unlock.
                self._write_command_diagnostics("input acknowledgement resync")
                return False
            if self.active and command in ("study_confirm", "study_choose", "study_resume"):
                before = copy.deepcopy(self.export_state())
            accepted = self._handle(command, payload)
            self.last_command_error = ""
            return accepted
        except Exception as exc:
            error = repr(exc)
            if before is not None:
                self._restore(before)
            self.last_command_error = error
            self.feedback = "操作尚未完成，請再按一次；已答對的進度保留。"
            self.feedback_kind = "retry"
            self._changed(new_token=True)
            self._write_command_diagnostics(error)
            print("STUDY command recovered:", command, error)
            return False
        finally:
            self.ack_id = max(self.ack_id, request_id)
            # Presentation revision, NOT an earned answer or a disk checkpoint.
            self.revision += 1
            self.command_history.append({"id": request_id, "command": command,
                "token": payload.get("token"), "accepted": bool(accepted),
                "stage": self.stage, "passed": list(self.passed), "error": error})

    def _handle(self, command, payload):
        """No client-supplied score/answer key; validate the active question token."""
        if not self.active or not isinstance(payload, dict):
            return False
        try:
            if int(payload.get("token", -1)) != self.token:
                return False
        except (ValueError, TypeError, OverflowError):
            return False
        command = str(command)
        if command == "study_resume":
            if not self.complete:
                return False
            self.active = False
            self.interval = self._configured_interval
            self.required = self._configured_required
            self.elapsed = 0.0
            self.completed_rounds += 1
            self.stage = 0
            self.passed = [0, 0, 0]
            self.question = None
            self.buffer = ""
            self._checkpoint_elapsed = 0.0
            self.feedback = ""
            self._changed(new_token=True)
            self.checkpoint(force=True)
            return True
        if self.complete or self.question is None:
            return False
        if self.stage < 2:
            if command == "study_digit":
                digit = str(payload.get("digit", ""))
                if digit not in tuple("0123456789") or len(digit) != 1:
                    return False
                if self.buffer == "0":
                    self.buffer = digit
                elif len(self.buffer) < 2:
                    self.buffer += digit
                else:
                    return False
                self.feedback = "輸入後按確認。"
                self.feedback_kind = "info"
                self._changed()
                return True
            if command == "study_erase":
                self.buffer = self.buffer[:-1]
                self._changed()
                return True
            if command != "study_confirm":
                return False
            if self.buffer and int(self.buffer) == self._math_answer(self.question):
                self._correct()
            else:
                self.feedback = "請先輸入答案，再按確認。" if not self.buffer else "再想一想，答案還不對。請重新輸入。"
                self.feedback_kind = "retry"
                self.buffer = ""
                self._changed(new_token=True)
                self.checkpoint(force=True)
            return True
        if command != "study_choose":
            return False
        try:
            index = int(payload.get("index", -1))
        except (ValueError, TypeError, OverflowError):
            return False
        if index not in range(4) or index in self.wrong_choices:
            return False
        answer = self.by_id[self.question["id"]]["answer"]
        if self.question["choices"][index] == answer:
            self._correct()
        else:
            self.wrong_choices.add(index)
            count = len(self.wrong_choices)
            if count > MAX_ZHUYIN_WRONG:
                self._next_zhuyin()
                self.feedback = "上一題錯了 3 次，換一題試試；不計入答對題數。"
            else:
                self.feedback = "再讀一次短句。這題已錯 %d 次（第 3 次會換題）。" % count
            self.feedback_kind = "retry"
            self._changed(new_token=True)
            self.checkpoint(force=True)
        return True

    def snapshot(self):
        """Detached presentation state; never exposes the correct choice/key."""
        snap = {"modal_kind": "study", "required": tuple(self.required),
                "profile": self.profile, "interval": self.interval,
                "active": self.active, "complete": self.complete, "token": self.token,
                "revision": self.revision, "ack_id": self.ack_id, "stage": self.stage, "passed": tuple(self.passed),
                "trigger_reason": self.trigger_reason, "characters": (), "choice_characters": (),
                "buffer": self.buffer, "feedback": self.feedback,
                "feedback_kind": self.feedback_kind, "wrong_count": len(self.wrong_choices),
                "wrong_choices": tuple(sorted(self.wrong_choices)), "tokens": (), "choices": (),
                "question_type": "", "category": "", "math_label": "",
                "equation": "", "seconds_left": max(0, int(math.ceil(self.interval-self.elapsed)))}
        if self.question and self.stage < 2:
            q = self.question
            snap["equation"] = "%d %s %d =" % (q["a"], q["op"], q["b"])
            snap["math_label"] = ("九九乘法" if self.stage == 0 else
                                  "兩位數與個位數加減法" if q.get("level") == "mixed" else "個位數加減法")
        elif self.question and self.stage == 2:
            base = self.by_id[self.question["id"]]
            snap["question_type"] = base["question_type"]
            snap["category"] = base["category"]
            snap["tokens"] = tuple(base["tokens"])
            snap["characters"] = tuple(base["characters"])
            snap["choices"] = tuple(self.question["choices"])
            character_for = dict(zip(base["choices"], base["choice_characters"]))
            snap["choice_characters"] = tuple(character_for[c] for c in self.question["choices"])
        return snap

    def export_state(self):
        return {"version": STATE_VERSION, "elapsed": self.elapsed, "active": self.active,
                "required": list(self.required), "round_interval": self.interval,
                "trigger_reason": self.trigger_reason,
                "stage": self.stage, "passed": list(self.passed), "question": self.question,
                "math_tasks": self.math_tasks, "math_recent": list(self.math_recent),
                "math_decks": self.math_decks.export(), "buffer": self.buffer,
                "wrong_choices": sorted(self.wrong_choices), "token": self.token,
                "completed_rounds": self.completed_rounds, "zhuyin_deck": self.zhuyin_deck,
                "last_zhuyin_id": self.last_zhuyin_id, "selection": self.selection.export()}

    def _restore(self, data):
        if not isinstance(data, dict) or data.get("version") not in (1, 2, 3, STATE_VERSION):
            raise ValueError("不支援的學習紀錄")
        modern = data.get("version") == STATE_VERSION
        saved_required = data.get("required") if modern else list(REQUIRED_CORRECT)
        if (not isinstance(saved_required, (list, tuple)) or len(saved_required) != 3
                or any(type(n) is not int for n in saved_required)
                or tuple(saved_required) not in {tuple(p["required"]) for p in PROFILES.values()}):
            raise ValueError("無效的習題目標題數")
        required = tuple(saved_required)
        round_interval = float(data.get("round_interval", INTERVAL_SECONDS)) if modern else INTERVAL_SECONDS
        if not math.isfinite(round_interval) or round_interval <= 0.0:
            raise ValueError("無效的學習間隔")
        elapsed = float(data["elapsed"])
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("無效的學習時間")
        active = data["active"]
        if not isinstance(active, bool):
            raise ValueError("無效的暫停狀態")
        stage = int(data["stage"])
        passed = data["passed"]
        if stage not in range(4) or not isinstance(passed, list) or len(passed) != 3:
            raise ValueError("無效的題型進度")
        if any(type(n) is not int or not 0 <= n <= required[i] for i, n in enumerate(passed)):
            raise ValueError("無效的答對題數")
        for i in range(3):
            if active and ((i < stage and passed[i] != required[i]) or
                           (i == stage and passed[i] >= required[i]) or (i > stage and passed[i] != 0)):
                raise ValueError("題型進度不連續")
        tasks = data.get("math_tasks", [])
        if not isinstance(tasks, list):
            raise ValueError("數學題遺失")
        if active:
            if len(tasks) != required[0] + required[1]:
                raise ValueError("數學題遺失")
            legacy = data.get("version") == 1
            for i, q in enumerate(tasks):
                if not isinstance(q, dict):
                    raise ValueError("數學題型錯誤")
                multiply = i < required[0]
                arithmetic_index = i - required[0]
                mixed = not multiply and arithmetic_index % 2 == 1 and not legacy
                valid_ops = ("×",) if multiply else ((("+",) if arithmetic_index == 0 else ("−",)) if legacy else ("+", "−"))
                if q.get("op") not in valid_ops or q.get("kind") != ("multiply" if multiply else "arithmetic"):
                    raise ValueError("數學題型錯誤")
                a_max = 99 if mixed else 9
                a_min = 10 if mixed else (0 if legacy and not multiply else 1)
                b_min = 0 if legacy and not multiply else 1
                if (type(q.get("a")) is not int or not a_min <= q["a"] <= a_max or
                    type(q.get("b")) is not int or not b_min <= q["b"] <= 9):
                    raise ValueError("數學題數字錯誤")
                if not 0 <= self._math_answer(q) <= 99:
                    raise ValueError("答案超出有效範圍")
                if not legacy and not multiply and q.get("level") != ("mixed" if mixed else "single"):
                    raise ValueError("加減法難度順序錯誤")
                if mixed and tasks[i-1]["op"] == q["op"]:
                    raise ValueError("每組兩題須包含加法與減法")
        question = data.get("question")
        wrong = data.get("wrong_choices", [])
        if not isinstance(wrong, list) or any(type(i) is not int or i not in range(4) for i in wrong):
            raise ValueError("錯題紀錄無效")
        if len(set(wrong)) != len(wrong) or len(wrong) > 2:
            raise ValueError("錯題次數無效")
        if active and stage < 2:
            index = passed[0] if stage == 0 else required[0] + passed[1]
            if question != tasks[index] or wrong:
                raise ValueError("數學題進度不符")
        if active and stage == 2:
            if not isinstance(question, dict) or question.get("kind") != "zhuyin":
                raise ValueError("注音題遺失")
            base = self.by_id.get(question.get("id"))
            if base is None or len(question.get("choices", [])) != 4 or sorted(question["choices"]) != sorted(base["choices"]):
                raise ValueError("注音題已更動")
            if any(question["choices"][i] == base["answer"] for i in wrong):
                raise ValueError("正確答案不可被標為錯誤")
        if active and stage == 3 and (tuple(passed) != required or question is not None or wrong):
            raise ValueError("未完成全部習題")
        reason = data.get("trigger_reason", "timer")
        self.trigger_reason = reason if reason in ("entry", "death", "timer") else "timer"
        self.required = required if active else self._configured_required
        self.interval = round_interval if active else self._configured_interval
        self.elapsed = min(self.interval, elapsed)
        self.active, self.stage, self.passed = active, stage, passed
        self.question, self.math_tasks, self.wrong_choices = question, tasks, set(wrong)
        raw_recent = data.get("math_recent", [])
        self.math_recent = [x for x in raw_recent[-48:] if isinstance(x, str) and len(x) <= 12] if isinstance(raw_recent, list) else []
        self.math_decks.restore(
            data.get("math_decks") if data.get("version") in (3, STATE_VERSION) else None,
            self.rng, self.math_recent, tasks)
        # Upgrade a pending FIX81 round, without erasing solved math/Zhuyin or
        # its non-repetition history. Completed questions are not asked again.
        if active and data.get("version") == 1:
            self.math_tasks = list(tasks[:2]) + self._arithmetic_pair()
            if stage == 1:
                self.question = dict(self.math_tasks[2 + passed[1]])
                data = dict(data, buffer="")
        raw_buffer = str(data.get("buffer", ""))
        self.buffer = raw_buffer if len(raw_buffer) <= 2 and all(ch in "0123456789" for ch in raw_buffer) else ""
        self.token = max(0, int(data.get("token", 0)))
        self.completed_rounds = max(0, int(data.get("completed_rounds", 0)))
        deck = data.get("zhuyin_deck", [])
        self.zhuyin_deck = list(dict.fromkeys(q for q in deck if isinstance(q, str) and q in self.by_id))
        self.last_zhuyin_id = str(data.get("last_zhuyin_id", ""))
        # Do not show the pending question again if a stale deck included it.
        if active and stage == 2:
            self.zhuyin_deck = [q for q in self.zhuyin_deck if q != question["id"]]
        self.selection.restore(data.get("selection"), self.zhuyin_deck, self.last_zhuyin_id)
        if not self.active:
            self.stage, self.passed, self.question, self.wrong_choices = 0, [0, 0, 0], None, set()
            if self.elapsed + 1e-8 >= self.interval:
                self.start_round()
        self.feedback = "接續上次的習題，完成後就能繼續遊戲。" if self.active else ""
        self.feedback_kind = "info"

    def checkpoint(self, force=False):
        if not self.ready or not self.state_path or not self._dirty:
            return False
        if not force and self._checkpoint_elapsed < 5.0:
            return False
        tmp = None
        try:
            directory = os.path.dirname(os.path.abspath(self.state_path))
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".study-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.export_state(), fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.state_path)
            self._dirty = False
            self._checkpoint_elapsed = 0.0
            self.persistence_error = ""
            return True
        except Exception as exc:
            error = repr(exc)
            if error != self.persistence_error:
                print("STUDY progress could not be saved:", error)
            self.persistence_error = error
            self._checkpoint_elapsed = 0.0  # Bound retries; never write at 60 Hz.
            return False
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
