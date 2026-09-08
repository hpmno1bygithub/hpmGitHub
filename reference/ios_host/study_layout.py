# -*- coding: utf-8 -*-
"""Pure layout helpers, shared by PytoUI and the headless regression tests."""

def layout_geometry(width, height, safe=(72.0, 12.0, 72.0, 24.0)):
    w, h = float(width), float(height)
    left, top, right, bottom = (max(0.0, float(v)) for v in safe)
    pw = min(1000.0, max(280.0, w-left-right))
    ph = min(620.0, max(240.0, h-top-bottom))
    px = left + (w-left-right-pw)*0.5
    py = top + (h-top-bottom-ph)*0.5
    pad = max(12.0, min(24.0, pw*.025))
    gap = max(6.0, min(12.0, ph*.024))
    title_h = max(25.0, min(36.0, ph*.085))
    tabs_y = pad+title_h+4.0
    tabs_h = max(24.0, min(36.0, ph*.08))
    content_y = tabs_y+tabs_h+8.0
    feedback_h = max(34.0, min(48.0, ph*.12))
    feedback_y = ph-pad-feedback_h
    content_h = feedback_y-gap-content_y
    inner_w = pw-2*pad
    key_w = min(inner_w*.46, 390.0)
    math_w = inner_w-key_w-gap*2
    key_x = pw-pad-key_w
    bw, bh = (key_w-gap*2)/3, (content_h-gap*3)/4
    key_order = ("7", "8", "9", "4", "5", "6", "1", "2", "3", "erase", "0", "confirm")
    keys = {key: (key_x+(i%3)*(bw+gap), content_y+(i//3)*(bh+gap), bw, bh)
            for i,key in enumerate(key_order)}
    choice_h = max(36.0, min(68.0, content_h*.215))
    choices_y = feedback_y-gap-2*choice_h-gap
    reading_h = max(48.0, choices_y-content_y-8.0)
    cw = (inner_w-gap)/2
    choices = [(pad+(i%2)*(cw+gap), choices_y+(i//2)*(choice_h+gap), cw, choice_h)
               for i in range(4)]
    return {"screen": (0.0,0.0,w,h), "panel": (px,py,pw,ph),
            "title": (pad,pad,inner_w,title_h),
            "tabs": [(pad+i*(inner_w/3),tabs_y,inner_w/3-4,tabs_h) for i in range(3)],
            "math_title": (pad,content_y,math_w,30),
            "equation": (pad,content_y+34,math_w,max(34.0,content_h*.26)),
            "entry": (pad+math_w*.15,content_y+content_h*.47,math_w*.7,max(36.0,content_h*.25)),
            "math_hint": (pad,content_y+content_h*.78,math_w,content_h*.22),
            "reading": (pad,content_y,inner_w,reading_h), "keys": keys,
            "choices": choices, "feedback": (pad,feedback_y,inner_w,feedback_h),
            "complete_text": (pad,content_y,inner_w,content_h*.55),
            "resume": (pad+inner_w*.23,content_y+content_h*.62,inner_w*.54,min(68.0,content_h*.30)),
            "title_font": min(28.0,max(19.0,pw/38.0)),
            "label_font": min(18.0,max(12.0,pw/53.0)),
            "math_font": min(54.0,max(30.0,math_w/8.0)),
            "key_font": min(32.0,max(21.0,bh*.55)),
            "choice_font": min(32.0,max(21.0,choice_h*.55)),
            "reading_font": min(30.0,max(20.0,reading_h/3.7)),
            "feedback_font": min(17.0,max(12.0,pw/55.0))}


def _units(text):
    return sum(.42 if ch == ' ' else .52 if ch in '˙ˊˇˋ' else 1.0 for ch in text)


def wrap_zhuyin(tokens, width, font_size):
    """Keep syllables, tone marks and their trailing punctuation together."""
    cap = max(4.0, float(width)/max(1.0, float(font_size)))
    # Attach punctuation BEFORE measuring: otherwise a full line can overflow
    # when a comma or full stop is appended after the fit decision.
    words = []
    for token in tokens:
        word = '［＿］' if token == '__' else str(token)
        if word in ('，','。','！','？','、') and words:
            words[-1] += word
        else:
            words.append(word)
    lines, line = [], ''
    for word in words:
        candidate = line + (' ' if line else '') + word
        if line and _units(candidate) > cap:
            lines.append(line)
            line = word
        else:
            line = candidate
        if word.endswith(('。','！','？')):
            lines.append(line)
            line = ''
    if line:
        lines.append(line)
    return lines


def fit_reading(tokens, width, height, preferred_size):
    size = float(preferred_size)
    while size > 17.0:
        lines = wrap_zhuyin(tokens, width-4.0, size)
        if (len(lines) <= 4 and len(lines)*size*1.28 <= height
                and all(_units(line)*size <= width-4.0 for line in lines)):
            return lines, size
        size -= 1.0
    return wrap_zhuyin(tokens,width-4.0,size),size
