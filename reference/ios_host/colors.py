# -*- coding: utf-8 -*-
import pyto_ui as ui


def color(hex_string, alpha=1.0):
    s = hex_string.strip().lstrip("#")
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return ui.Color.rgb(r, g, b, alpha)


SKY = color("#8FC8E8")
WHITE = color("#FFFFFF")
BLACK = color("#1D2329")
DARK = color("#24313E")
DARK2 = color("#34485C")
BLUE = color("#3479A1")
GREEN = color("#408854")
PURPLE = color("#745A8C")
RED = color("#995050")
GOLD = color("#8A7038")

PLAYER = color("#F1CD57")
PLAYER_CLIMB = color("#58B99A")
PLAYER_ATTACK = color("#ED8256")
NPC_COLOR = color("#88528B")
ITEM_COLOR = color("#E39A38")

HUD_BG = color("#1F2933", 0.88)
BAR_BG = color("#182028")
HP_COLOR = color("#C95454")
ST_COLOR = color("#5BAF67")
MP_COLOR = color("#4F7DCC")
