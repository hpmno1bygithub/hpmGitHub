# -*- coding: utf-8 -*-
"""FIX79 platform-independent Chinese + inter-character vertical Bopomofo.

One Han character, up to three upright phonetic letters, and a separate tone
mark. No browser ruby support or bundled font is required. Tone placement:
MOE 國語注音符號手冊, 四、調號說明表. Neutral dot is ABOVE the column;
other marks are at the upper right of the LAST phonetic letter.

All returned rectangles are local to the requested text area. Rendering uses a
fixed pool of native labels only while the world is paused.
"""
import math

PUNCTUATION = frozenset('，。！？、')
MAX_CELLS = 32


def split_syllable(syllable):
    """Return (letters, tone); never rotate an entire syllable as one glyph."""
    value = str(syllable or '')
    if not value:
        return (), ''
    tone = ''
    if value.startswith('˙'):
        tone, value = '˙', value[1:]
    elif value[-1:] in ('ˊ', 'ˇ', 'ˋ'):
        tone, value = value[-1], value[:-1]
    if not 1 <= len(value) <= 3 or any(not 'ㄅ' <= c <= 'ㄩ' for c in value):
        raise ValueError('無效的單字注音：' + str(syllable))
    return tuple(value), tone


def cell_width(character, base_size):
    size = float(base_size)
    if character in PUNCTUATION:
        return size * .82
    if character == '__':
        return size * 1.25
    return size * 1.68


def ruby_cell(character, syllable, base_size, x=0.0, y=0.0):
    """Glyph specifications for one 1.38-em-high character cell.

    Five slots are stable: Han/base, Bopomofo 1..3, tone. Unused slots are None.
    The last slot uses a centered middle dot glyph for a neutral circular mark
    so that an accent font's high baseline cannot push it off screen.
    """
    base = float(base_size)
    x, y = float(x), float(y)
    h = base * 1.38
    w = cell_width(character, base)
    glyphs = [None] * 5
    blank = character == '__'
    glyphs[0] = {'text': '＿' if blank else character,
                 'frame': (x, y + base*.06, (w-base*.08) if blank else base*1.07, base*1.25),
                 'size': base*.78 if blank else base,
                 'role': 'blank' if blank else 'base'}
    if blank or character in PUNCTUATION:
        # In particular, the missing Hanzi AND its pronunciation stay hidden.
        if character in PUNCTUATION:
            glyphs[0]['frame'] = (x, y+base*.06, w, base*1.25)
        return {'character': character, 'syllable': '', 'frame': (x,y,w,h),
                'glyphs': glyphs, 'base_size': base}
    letters, tone = split_syllable(syllable)
    symbol_size = base*.31
    advance = base*.34
    symbol_h = base*.40  # Extra font ascender/descender room, not extra spacing.
    neutral_h = base*.14 if tone == '˙' else 0.0
    column_h = len(letters)*advance + neutral_h
    column_y = y+(h-column_h)*.5
    symbol_y = column_y+neutral_h
    symbol_x = x+base*1.08
    for i,letter in enumerate(letters):
        glyphs[i+1] = {'text':letter,
                       'frame':(symbol_x, symbol_y+i*advance-(symbol_h-advance)*.5,
                                base*.34,symbol_h),
                       'size':symbol_size, 'role':'phonetic'}
    if tone == '˙':
        glyphs[4] = {'text':'·', 'frame':(symbol_x,column_y+neutral_h*.5-base*.17,base*.34,base*.34),
                     'size':base*.25, 'role':'neutral'}
    elif tone:
        last_y = glyphs[len(letters)]['frame'][1]
        glyphs[4] = {'text':tone,
                     'frame':(symbol_x+base*.30,last_y-base*.14,base*.27,base*.40),
                     'size':symbol_size, 'role':'tone'}
    return {'character':character, 'syllable':syllable, 'frame':(x,y,w,h),
            'glyphs':glyphs, 'base_size':base}


def _wrap(characters, size, width):
    # Attach punctuation to its preceding character before fitting, so a comma
    # or full stop is never orphaned at the start of a new line.
    groups = []
    for i,char in enumerate(characters):
        if char in PUNCTUATION and groups:
            groups[-1].append(i)
        else:
            groups.append([i])
    rows, line, used = [], [], 0.0
    for group in groups:
        needed = sum(cell_width(characters[i],size) for i in group)
        if line and used+needed > width+1e-6:
            rows.append(line)
            line, used = [], 0.0
        line.extend(group)
        used += needed
    if line:
        rows.append(line)
    # For short two-clause exercises, prefer an existing comma over splitting
    # a phrase such as "床上休息", but only when both complete clauses fit.
    if len(rows) == 2:
        alternatives = []
        for i,char in enumerate(characters[:-1]):
            if char not in ('，','。','！','？'):
                continue
            first,second = list(range(i+1)),list(range(i+1,len(characters)))
            a = sum(cell_width(characters[j],size) for j in first)
            b = sum(cell_width(characters[j],size) for j in second)
            if max(a,b) <= width+1e-6:
                alternatives.append((abs(a-b),first,second))
        if alternatives:
            _,first,second = min(alternatives)
            rows = [first,second]
    return rows


def layout_ruby_reading(characters, syllables, width, height, preferred_size=40.0):
    """Fit complete Hanzi+Zhuyin cells; never split a word's tone/phonetics."""
    characters, syllables = tuple(characters), tuple(syllables)
    if len(characters) != len(syllables) or not 1 <= len(characters) <= MAX_CELLS:
        raise ValueError('國字與注音數量不符')
    width,height = float(width),float(height)
    if not all(math.isfinite(v) and v > 0 for v in (width,height)):
        raise ValueError('無效的注音排版範圍')
    size = min(48.0,max(18.0,float(preferred_size)))
    while True:
        rows = _wrap(characters,size,width)
        total_h = len(rows)*size*1.44
        row_widths = [sum(cell_width(characters[i],size) for i in row) for row in rows]
        if total_h <= height+1e-6 and max(row_widths,default=0) <= width+1e-6:
            break
        size -= .5
        if size < 12.0:
            raise ValueError('螢幕可用區域不足以顯示完整習題')
    cells = []
    y = (height-total_h)*.5 + size*.03
    for row,rw in zip(rows,row_widths):
        x = (width-rw)*.5
        for i in row:
            cell = ruby_cell(characters[i],syllables[i],size,x,y)
            cell['index'] = i
            cells.append(cell)
            x += cell_width(characters[i],size)
        y += size*1.44
    return {'cells':cells, 'size':size, 'line_count':len(rows), 'rows':rows,
            'width':width, 'height':height}


def layout_ruby_choice(character, syllable, width, height):
    # Large touch target stays the whole original button; labels ignore touches.
    base = min(43.0,(float(height)-4.0)/1.38,(float(width)-16.0)/1.68)
    if base <= 0:
        raise ValueError('選項按鍵範圍不足')
    w = cell_width(character,base)
    return ruby_cell(character,syllable,base,(width-w)*.5,(height-base*1.38)*.5)
