# -*- coding: utf-8 -*-
"""Readable fullscreen title/settings/death menus for PytoUI."""
import pyto_ui as ui
from systems.study_profiles import profile_summary


class GameMenuOverlay:
    def __init__(self, root, game, on_start, on_close):
        self.root = root
        self.game = game
        self.on_start = on_start
        self.on_close = on_close
        self.mode = "title"
        self.settings_open = False
        self.viewport = (852.0, 393.0)

        self.panel = ui.View()
        self.panel.background_color = ui.Color.rgb(0.035, 0.055, 0.075, 0.985)
        self.panel.border_width = 3
        self.panel.border_color = ui.Color.rgb(0.42, 0.65, 0.76, 1.0)
        self.panel.corner_radius = 8
        self.root.add_subview(self.panel)

        self.title = ui.Label("PYTO RPG")
        self.title.text_color = ui.Color.rgb(0.95, 0.91, 0.66, 1.0)
        self.title.text_alignment = ui.TextAlignment.CENTER
        self.title.font = ui.Font.bold_system_font_of_size(24)
        self.title.user_interaction_enabled = False
        self.panel.add_subview(self.title)

        self.subtitle = ui.Label("主選單")
        self.subtitle.text_color = ui.Color.rgb(0.78, 0.88, 0.92, 1.0)
        self.subtitle.text_alignment = ui.TextAlignment.CENTER
        self.subtitle.font = ui.Font.bold_system_font_of_size(11)
        self.subtitle.user_interaction_enabled = False
        self.panel.add_subview(self.subtitle)

        self.buttons = {}
        self._add_button("new", "新開遊戲", self._new_game)
        self._add_button("load", "載入遊戲", self._load_game)
        self._add_button("settings", "遊戲設定", self._show_settings)
        self._add_button("death", "死亡處理：開啟（會死亡）", self._toggle_death)
        self._add_button("starter", "新手道具：開啟", self._toggle_starter)
        self._add_button("intensity", "學習強度：預設", self._toggle_intensity)
        self._add_button("playtime", "遊玩限制：30 分鐘（密碼解鎖）", self._show_playtime_info)
        self._add_button("back", "返回主選單", self._hide_settings)
        self._add_button("death_new", "新開遊戲", self._death_new)
        self._add_button("death_load", "載入遊戲", self._death_load)
        self._add_button("close", "關閉遊戲", self._close)
        self.show_title()

    def _add_button(self, key, title, action):
        button = ui.Button(title=str(title))
        button.background_color = ui.Color.rgb(0.10, 0.18, 0.23, 1.0)
        button.title_color = ui.Color.rgb(0.96, 0.98, 1.0, 1.0)
        button.font = ui.Font.bold_system_font_of_size(15)
        button.border_width = 2
        button.border_color = ui.Color.rgb(0.34, 0.62, 0.72, 1.0)
        button.corner_radius = 5
        button.action = action
        self.panel.add_subview(button)
        self.buttons[str(key)] = button
        return button

    def _set_visible(self, keys):
        keys = set(keys)
        for key, button in self.buttons.items():
            button.hidden = key not in keys

    def _refresh_settings(self):
        state = self.game.settings_snapshot()
        self.buttons["death"].title = "死亡處理：%s" % (
            "開啟（會死亡）" if state.get("death_handling", True) else "關閉（不會死亡）"
        )
        self.buttons["starter"].title = "新手道具：%s" % (
            "開啟" if state.get("starter_items", True) else "關閉"
        )
        self.buttons["intensity"].title = "學習強度：" + profile_summary(state.get("study_intensity", 0))
        self.subtitle.text = (
            "死亡後先答題；強度點擊切換，時數獨立累積"
            if state.get("death_handling", True)
            else "最低保留 1 HP；強度點擊切換，時數獨立累積"
        )

    def show_title(self):
        self.mode = "title"
        self.settings_open = False
        self.title.text = "PYTO RPG"
        self.subtitle.text = "主選單"
        self._set_visible(("new", "load", "settings"))
        self.panel.hidden = False
        self.layout(*self.viewport)

    def _show_settings(self, _sender=None):
        self.settings_open = True
        self.title.text = "遊戲設定"
        self.subtitle.text = "遊戲設定（點擊切換）"
        self._refresh_settings()
        self._set_visible(("death", "starter", "intensity", "playtime", "back"))
        self.layout(*self.viewport)

    def _hide_settings(self, _sender=None):
        self.show_title()

    def show_death(self):
        self.mode = "death"
        self.settings_open = False
        self.title.text = "角色死亡"
        self.subtitle.text = "選擇接下來的處理方式"
        self._set_visible(("death_new", "death_load", "close"))
        self.panel.hidden = False
        self.layout(*self.viewport)

    def hide(self):
        self.panel.hidden = True

    def _begin(self, command):
        self.game.enqueue_ui_command(str(command))
        self.hide()
        if self.on_start is not None:
            self.on_start()

    def _new_game(self, _sender=None):
        self._begin("title_new_game")

    def _load_game(self, _sender=None):
        self._begin("load")

    def _death_new(self, _sender=None):
        self._begin("death_new_game")

    def _death_load(self, _sender=None):
        self._begin("death_load_game")

    def _toggle_death(self, _sender=None):
        self.game.toggle_game_setting("death_handling")
        self._refresh_settings()

    def _toggle_starter(self, _sender=None):
        self.game.toggle_game_setting("starter_items")
        self._refresh_settings()

    def _toggle_intensity(self, _sender=None):
        try:
            self.game.toggle_game_setting("study_intensity")
            self._refresh_settings()
            self.subtitle.text = "進場／死亡／定時都套用；未完成的舊習題保留"
        except Exception:
            self.subtitle.text = "設定未能保存，請檢查儲存空間後重試"

    def _show_playtime_info(self, _sender=None):
        # Deliberately not a toggle: changing tier or death policy cannot turn
        # off the independent time limit or grant a fresh allowance.
        self.subtitle.text = "答題／背包不計時；重啟不清零；解鎖再開放 30 分鐘"

    def _close(self, _sender=None):
        if self.on_close is not None:
            self.on_close()

    def layout(self, width, height):
        width = max(320.0, float(width)); height = max(240.0, float(height))
        self.viewport = (width, height)
        panel_w = min(620.0, width - (144.0 if width >= 600.0 else 44.0)) if self.settings_open else min(430.0, width - 44.0)
        panel_h = min(366.0 if self.settings_open else 330.0, height - 34.0)
        px = (width - panel_w) * 0.5
        py = (height - panel_h) * 0.5
        self.panel.frame = (px, py, panel_w, panel_h)
        self.title.frame = (16.0, 18.0, panel_w - 32.0, 36.0)
        self.subtitle.frame = (16.0, 55.0, panel_w - 32.0, 24.0)
        active = [button for button in self.buttons.values() if not bool(button.hidden)]
        # FIX87: keep all three settings/death buttons inside short landscape
        # viewports as well; the final row must not extend beyond the panel.
        gap = 7.0 if self.settings_open else 10.0
        available = max(0.0, panel_h - 88.0 - 14.0)
        button_h = min(48.0, max(14.0, (available - max(0, len(active)-1)*gap) / max(1, len(active))))
        total = len(active) * button_h + max(0, len(active) - 1) * gap
        y = max(88.0, (panel_h - total + 70.0) * 0.5)
        for button in active:
            margin = 20.0 if self.settings_open else 48.0
            button.frame = (margin, y, panel_w - 2*margin, button_h)
            button.font = ui.Font.bold_system_font_of_size(min(15.0, max(10.0, button_h * .45)))
            y += button_h + gap
