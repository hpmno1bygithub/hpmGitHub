# -*- coding: utf-8 -*-
"""Hand-authored low-resolution default creature artwork.

FIX26 further strengthens reptile silhouettes while retaining editable pixel sources.
FIX21 replaces the old rectangle placeholders with editable pixel silhouettes.
Every colored cell is one authored pixel; no per-creature image scaling exists.
The renderer/editor therefore keep the global fixed 1-cell = 2.5-world-pixel
mapping introduced in FIX20.
"""
import math

TRANSPARENT = "#00000000"

BASE = {
    "slime":"#63C56E", "boar":"#9B6845", "wolf":"#777B82", "deer":"#A8794E",
    "gull":"#E6EAEE", "shore_crab":"#C36F49", "sea_turtle":"#5C8D56",
    "heron":"#B8C7CF", "otter":"#8C6A4C", "mangrove_crab":"#A85E3F",
    "desert_scorpion":"#8A6139", "sand_lizard":"#A28C51", "desert_snake":"#C2A55E",
    "desert_beetle":"#72543B", "swamp_slime":"#65894B", "crocodile":"#496B3E",
    "swamp_snake":"#607A42", "swamp_frog":"#73A85C", "village_rat":"#7B7169",
    "bandit":"#8B4E45", "village_dog":"#A97843", "villager":"#B88963",
    "jungle_spider":"#493B4F", "jaguar":"#C38A39", "jungle_snake":"#4F8A46",
    "capybara":"#8C6A4C", "snow_wolf":"#CED7DE", "mountain_goat":"#D8CCAE",
    "ice_slime":"#8FD6E7", "snow_hare":"#E3E8EB", "sardine":"#8BB8C7",
    "mackerel":"#5D8FA5", "sea_bass":"#6FA09A", "carp":"#B69058",
    "crucian_carp":"#A99C70", "freshwater_bass":"#6F8B63", "mallard":"#667B52",
    "kingfisher":"#4C8FA8", "dragonfly":"#77A7A3", "damselfly":"#7F90B8",
    "cave_bat":"#665A78", "cave_spider":"#403846", "zombie":"#73836A",
    "giant_worm":"#8C6257", "dungeon_bandit_mage":"#57456D",
    "creeper":"#53A947", "blue_poop":"#4E8FCB", "giant_bago_bird":"#D05AC6",
    "fire_zombie":"#7E624E", "lightning_zombie":"#66788C", "ghost":"#A7D9E8",
    "toilet_man":"#D9E1E5", "speaker_man":"#333844", "abyss_colossus":"#3A3946",
    "giant_spider_monster":"#2E2437", "cave_snorble":"#79614F", "plane_monster":"#596779",
    "flying_imp":"#A83A35", "infernal_goat":"#342C31", "burning_slime":"#F05A24",
    "flame_turtle":"#3A3030", "exploding_wisp":"#F04B38", "crystal_knight":"#315785",
    "crystal_slime":"#5CC7E8", "crystal_skeleton":"#607C9A", "crystal_lizard":"#397A91",
}

# FIX116: creature sprites choose a canvas by body type. Ordinary fauna still
# use 16/24/32px sources, while giant bosses are authored directly on native
# 96x96 canvases. A 96px boss is never produced by nearest-neighbour enlargement
# of a 32px source; the extra pixels are real editable detail.
PREFERRED_CANVAS = {
    "slime":16, "swamp_slime":16, "ice_slime":16,
    "sardine":16, "mackerel":16, "crucian_carp":16,
    "dragonfly":16, "damselfly":16, "desert_beetle":16,
    "village_rat":16,

    "boar":24, "wolf":24, "deer":24, "gull":24,
    "shore_crab":24, "sea_bass":24, "carp":24, "freshwater_bass":24,
    "otter":24, "mangrove_crab":24, "desert_scorpion":24,
    "sand_lizard":24, "desert_snake":24, "swamp_snake":24,
    "jungle_snake":24, "swamp_frog":24, "jungle_spider":24,
    "jaguar":24, "capybara":24, "snow_wolf":24,
    "mountain_goat":24, "snow_hare":24, "village_dog":24,
    "kingfisher":24, "mallard":24,

    "sea_turtle":32, "heron":32, "crocodile":32,
    "bandit":32, "villager":32, "zombie":32, "giant_worm":32,
    "dungeon_bandit_mage":32, "fire_zombie":32, "lightning_zombie":32,
    "giant_bago_bird":32,
    "toilet_man":32, "speaker_man":32, "abyss_colossus":32,
    "giant_spider_monster":32, "cave_snorble":32, "plane_monster":32,
    "cave_bat":24, "cave_spider":24, "creeper":24, "ghost":24,
    "blue_poop":16,
    "flying_imp":32, "infernal_goat":32, "burning_slime":32, "flame_turtle":32,
    "exploding_wisp":32, "crystal_knight":32, "crystal_slime":32,
    "crystal_skeleton":32, "crystal_lizard":32,

    # FIX116 native boss authoring canvases.
    "lava_beetle_emperor":96, "crystal_nine_tail":96,
    "abyss_crab_king":96, "thunder_roc":96, "drill_worm":96,
    "ancient_tree_demon":96,
}

# Art boxes are in authored source pixels, not collision size. This lets
# larger canvas creatures actually use extra pixel detail instead of merely
# adding transparent margins.
ART_BOX = {
    "boar":(19,12), "wolf":(20,13), "snow_wolf":(20,13), "village_dog":(19,12),
    "deer":(19,15), "mountain_goat":(18,15), "capybara":(20,13), "otter":(20,10),
    "jaguar":(20,13), "snow_hare":(15,14), "gull":(17,10), "mallard":(18,10),
    "kingfisher":(16,10), "shore_crab":(16,10), "mangrove_crab":(16,10),
    "sea_bass":(18,8), "carp":(18,9), "freshwater_bass":(18,8),
    "desert_scorpion":(18,12), "sand_lizard":(18,8),
    "desert_snake":(22,11), "swamp_snake":(22,11), "jungle_snake":(22,11),
    "swamp_frog":(16,10), "jungle_spider":(16,12),
    "sea_turtle":(22,14), "crocodile":(28,14), "heron":(18,26),
    "bandit":(12,24), "villager":(12,24),
    "cave_bat":(22,13), "cave_spider":(18,12), "zombie":(13,24),
    "giant_worm":(28,15), "dungeon_bandit_mage":(14,24),
    "creeper":(14,21), "blue_poop":(13,12), "giant_bago_bird":(28,22),
    "fire_zombie":(14,24), "lightning_zombie":(14,24), "ghost":(17,20),
    "toilet_man":(18,25), "speaker_man":(16,25), "abyss_colossus":(30,30),
    "giant_spider_monster":(30,18), "cave_snorble":(26,20), "plane_monster":(30,16),
    "flying_imp":(28,24), "infernal_goat":(28,20), "burning_slime":(22,19),
    "flame_turtle":(29,18), "exploding_wisp":(20,24), "crystal_knight":(22,30),
    "crystal_slime":(23,20), "crystal_skeleton":(21,30), "crystal_lizard":(30,18),
}


def recommended_canvas_size(species):
    return int(PREFERRED_CANVAS.get(str(species), 16))


def art_box_size(species, size, archetypes=None):
    sp=str(species)
    if sp in ART_BOX:
        w,h=ART_BOX[sp]
        n=max(1,int(size))
        return max(3,min(n,int(w))), max(3,min(n,int(h)))
    cfg=(archetypes or {}).get(sp,{}) if isinstance(archetypes,dict) else {}
    w=float(cfg.get("w",32) or 32); h=float(cfg.get("h",24) or 24)
    base=max(5,min(int(size),int(round(w/2.5))))
    tall=max(5,min(int(size),int(round(h/2.5))))
    pref=int(PREFERRED_CANVAS.get(sp, size))
    # If the canvas itself is larger than the collision silhouette, let the art
    # breathe a little so artists actually gain detail from 24/32 canvases.
    if int(size) >= 24 and pref >= 24:
        base=min(int(size)-2, max(base + 3, int(size*0.75)))
        tall=min(int(size)-2, max(tall + 2, int(size*0.50)))
    if int(size) >= 32 and pref >= 32:
        base=min(int(size)-2, max(base + 4, int(size*0.72)))
        tall=min(int(size)-2, max(tall + 4, int(size*0.60)))
    return base,tall


def _hex(value):
    s=str(value or "#000000").strip().lstrip("#")
    if len(s)==8:return "#"+s.upper()
    if len(s)!=6:s="FF00FF"
    return "#"+s.upper()+"FF"


def _shade(value, delta):
    s=_hex(value)[1:7]
    rgb=[int(s[i:i+2],16) for i in (0,2,4)]
    d=int(round(float(delta)*255.0))
    rgb=[max(0,min(255,v+d)) for v in rgb]
    return "#%02X%02X%02XFF" % tuple(rgb)


class C:
    def __init__(self, size, box_w=None, box_h=None):
        self.n=max(1,int(size))
        self.g=[[TRANSPARENT for _ in range(self.n)] for _ in range(self.n)]
        self.w=max(1,min(self.n,int(box_w or self.n)))
        self.h=max(1,min(self.n,int(box_h or self.n)))
        self.ox=(self.n-self.w)//2
        self.oy=self.n-self.h

    def set(self,x,y,color,local=True):
        if local:x+=self.ox;y+=self.oy
        x=int(round(x));y=int(round(y))
        if 0<=x<self.n and 0<=y<self.n:self.g[y][x]=_hex(color)

    def rect(self,x0,y0,x1,y1,color):
        for y in range(int(math.floor(y0)),int(math.ceil(y1))):
            for x in range(int(math.floor(x0)),int(math.ceil(x1))):self.set(x,y,color)

    def line(self,x0,y0,x1,y1,color,thick=1):
        x0=float(x0);y0=float(y0);x1=float(x1);y1=float(y1)
        steps=max(1,int(max(abs(x1-x0),abs(y1-y0))))
        r=max(0,int(thick)//2)
        for i in range(steps+1):
            t=i/float(steps);x=round(x0+(x1-x0)*t);y=round(y0+(y1-y0)*t)
            for yy in range(int(y)-r,int(y)+r+1):
                for xx in range(int(x)-r,int(x)+r+1):self.set(xx,yy,color)

    def poly(self,pts,color):
        pts=[(float(x),float(y)) for x,y in pts]
        if not pts:return
        minx=int(math.floor(min(x for x,_ in pts)));maxx=int(math.ceil(max(x for x,_ in pts)))
        miny=int(math.floor(min(y for _,y in pts)));maxy=int(math.ceil(max(y for _,y in pts)))
        for y in range(miny,maxy+1):
            py=y+0.5
            for x in range(minx,maxx+1):
                px=x+0.5;inside=False;j=len(pts)-1
                for i in range(len(pts)):
                    xi,yi=pts[i];xj,yj=pts[j]
                    if ((yi>py)!=(yj>py)) and (px < (xj-xi)*(py-yi)/(yj-yi+1e-12)+xi):inside=not inside
                    j=i
                if inside:self.set(x,y,color)

    def ellipse(self,cx,cy,rx,ry,color):
        rx=max(.5,float(rx));ry=max(.5,float(ry))
        for y in range(int(math.floor(cy-ry)),int(math.ceil(cy+ry))+1):
            for x in range(int(math.floor(cx-rx)),int(math.ceil(cx+rx))+1):
                if ((x+0.5-cx)/rx)**2+((y+0.5-cy)/ry)**2<=1.0:self.set(x,y,color)


def _dims(species,size,archetypes):
    return art_box_size(species,size,archetypes)


def _face(c,x,y,eye="#171A1FFF",muzzle=None):
    c.set(x,y,eye)
    if muzzle:c.set(x+1,y+1,muzzle)


def _slime(c,base,kind):
    dark=_shade(base,-.18);light=_shade(base,.18);w,h=c.w,c.h
    c.poly([(1,h-1),(1,h-5),(3,h-8),(w//2,h-9),(w-3,h-8),(w-1,h-5),(w-1,h-1)],dark)
    c.poly([(2,h-1),(2,h-5),(4,h-7),(w//2,h-8),(w-4,h-7),(w-2,h-5),(w-2,h-1)],base)
    c.rect(4,h-7,6,h-5,light);c.set(w-5,h-4,"#152019");c.set(w-8,h-4,"#152019")
    if kind=="swamp_slime":
        c.set(3,h-3,"#819A45");c.set(w-4,h-6,"#3C5C38");c.set(w//2,h-8,"#9BAB5C")
    elif kind=="ice_slime":
        c.line(w//2-2,h-8,w//2,h-11,"#DDF7FFFF");c.line(w//2,h-11,w//2+2,h-8,"#BDEBFFFF")
        c.set(3,h-3,"#DDF7FFFF")


def _canine(c,base,kind):
    dark=_shade(base,-.24);deep=_shade(base,-.38);light=_shade(base,.20);cream="#EBD8B7FF" if kind=="village_dog" else _shade(base,.28)
    w,h=c.w,c.h;ground=h-1
    c.poly([(2,ground-6),(4,ground-8),(10,ground-8),(12,ground-6),(11,ground-3),(4,ground-3)],base)
    c.poly([(10,ground-8),(13,ground-10),(w-2,ground-9),(w-1,ground-6),(13,ground-5)],base)
    # ears / muzzle
    c.poly([(12,ground-10),(13,ground-13),(14,ground-10)],dark);c.poly([(14,ground-10),(15,ground-13),(16,ground-9)],dark)
    c.rect(w-3,ground-8,w,ground-6,light);c.set(w-1,ground-7,deep);c.set(w-4,ground-9,deep)
    # legs
    for x in (4,8,11,14):c.rect(x,ground-3,x+2,ground+1,dark)
    # tail
    if kind=="village_dog":
        c.line(3,ground-6,1,ground-8,dark);c.line(1,ground-8,2,ground-10,dark);c.line(2,ground-10,4,ground-9,cream)
        c.rect(11,ground-6,13,ground-3,cream);c.rect(13,ground-7,15,ground-5,cream)
    else:
        c.poly([(3,ground-7),(0,ground-9),(1,ground-5),(4,ground-3)],dark)
        c.rect(5,ground-5,10,ground-3,light)
        if kind=="snow_wolf":c.rect(2,ground-6,5,ground-4,"#F3F7FAFF")


def _boar(c,base):
    dark=_shade(base,-.25);deep=_shade(base,-.40);light=_shade(base,.15);ivory="#F1E3B8FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.45,g-5,w*.38,4,base);c.poly([(w*.70,g-8),(w-2,g-7),(w-1,g-4),(w*.70,g-4)],base)
    c.rect(w-3,g-6,w,g-4,light);c.set(w-1,g-5,deep);c.set(w-4,g-7,deep)
    c.poly([(w-3,g-4),(w-2,g-2),(w-1,g-4)],ivory);c.poly([(w*.65,g-8),(w*.70,g-10),(w*.75,g-8)],dark)
    for x in (3,7,11,14):c.rect(x,g-3,x+2,g+1,dark)
    for x in range(3,w-5,2):c.set(x,g-9,deep)
    c.line(2,g-6,0,g-7,dark)


def _deer(c,base):
    dark=_shade(base,-.28);light=_shade(base,.18);cream="#E8D5B3FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.42,g-6,w*.30,3.2,base);c.rect(int(w*.63),g-10,int(w*.72),g-5,base)
    c.poly([(w*.68,g-10),(w*.80,g-12),(w-2,g-11),(w-1,g-9),(w*.72,g-8)],base)
    c.set(w-2,g-10,dark);c.rect(w-3,g-9,w,g-8,cream)
    for x in (4,7,10,12):c.rect(x,g-4,x+1,g+1,dark)
    c.set(2,g-6,cream);c.line(2,g-7,0,g-8,dark)
    # antlers
    c.line(int(w*.77),g-12,int(w*.75),g-15,dark);c.line(int(w*.75),g-14,int(w*.62),g-15,dark)
    c.line(int(w*.82),g-12,int(w*.86),g-15,dark);c.line(int(w*.86),g-14,int(w*.96),g-15,dark)


def _goat(c,base):
    dark=_shade(base,-.28);deep="#5A4638FF";light=_shade(base,.16);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.43,g-6,w*.32,3.5,base);c.rect(int(w*.62),g-9,int(w*.72),g-5,base)
    c.poly([(w*.68,g-10),(w*.80,g-12),(w-2,g-10),(w-1,g-8),(w*.72,g-7)],light)
    c.set(w-2,g-9,deep);c.rect(w-3,g-8,w,g-7,_shade(base,.25))
    for x in (4,7,10,12):c.rect(x,g-4,x+2,g+1,dark)
    # curved horn + beard
    c.line(int(w*.76),g-12,int(w*.68),g-15,deep);c.line(int(w*.68),g-15,int(w*.57),g-14,deep)
    c.line(int(w*.82),g-12,int(w*.78),g-15,_shade(deep,.10))
    c.line(int(w*.80),g-7,int(w*.78),g-4,deep)


def _capybara(c,base):
    dark=_shade(base,-.24);light=_shade(base,.12);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.42,g-5,w*.36,4,base);c.poly([(w*.66,g-7),(w-1,g-7),(w-1,g-4),(w*.66,g-3)],light)
    c.set(w-2,g-6,"#211B18FF");c.set(w-1,g-5,"#30251FFF")
    c.set(int(w*.68),g-8,dark)
    for x in (4,8,11,14):c.rect(x,g-3,x+2,g+1,dark)


def _otter(c,base):
    dark=_shade(base,-.26);light=_shade(base,.20);cream="#D3B78FFF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.50,g-4,w*.34,3,base);c.ellipse(w*.77,g-5,2.6,2.5,light)
    c.line(int(w*.20),g-4,0,g-2,dark);c.line(int(w*.20),g-5,0,g-4,dark)
    c.rect(int(w*.70),g-4,w-2,g-2,cream);c.set(w-1,g-5,"#241D19FF");c.set(w-3,g-6,"#241D19FF")
    c.rect(5,g-2,7,g+1,dark);c.rect(11,g-2,13,g+1,dark)


def _jaguar(c,base):
    dark="#4B3121FF";cream="#E1B767FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.45,g-6,w*.34,3.4,base);c.ellipse(w*.78,g-7,3,2.8,base)
    c.poly([(w*.73,g-9),(w*.75,g-11),(w*.80,g-9)],dark);c.poly([(w*.84,g-9),(w*.88,g-11),(w*.90,g-9)],dark)
    c.rect(w-3,g-7,w,g-6,cream);c.set(w-2,g-8,"#151311FF")
    for x in (4,7,11,14):c.rect(x,g-4,x+2,g+1,dark)
    c.line(3,g-6,0,g-5,dark);c.line(0,g-5,1,g-3,dark)
    for x,y in ((5,g-7),(8,g-5),(10,g-7),(12,g-6)):c.set(x,y,dark)


def _hare(c,base):
    dark=_shade(base,-.25);light=_shade(base,.20);pink="#D6A0A3FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.45,g-4,w*.27,3.2,base);c.ellipse(w*.68,g-7,2.3,2.6,light)
    c.rect(int(w*.60),g-12,int(w*.64),g-7,base);c.rect(int(w*.70),g-13,int(w*.74),g-7,base)
    c.set(int(w*.62),g-11,pink);c.set(int(w*.72),g-12,pink);c.set(int(w*.76),g-8,dark)
    c.rect(3,g-2,8,g+1,light);c.set(1,g-5,"#F5F5F0FF")


def _rat(c,base):
    dark=_shade(base,-.28);light=_shade(base,.18);pink="#C7847EFF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.48,g-3,w*.32,2.7,base);c.poly([(w*.68,g-5),(w-1,g-4),(w-1,g-2),(w*.68,g-2)],light)
    c.set(w-2,g-4,"#1C1715FF");c.set(int(w*.70),g-6,pink)
    c.line(3,g-3,0,g-2,pink);c.line(0,g-2,1,g,pink)
    c.rect(5,g-1,8,g+1,dark);c.rect(10,g-1,13,g+1,dark)


def _fish(c,base,kind):
    dark=_shade(base,-.28);light=_shade(base,.20);w,h=c.w,c.h;mid=h-5
    c.poly([(3,mid),(5,mid-3),(w-4,mid-3),(w-1,mid),(w-4,mid+3),(5,mid+3)],base)
    c.poly([(3,mid),(0,mid-3),(1,mid),(0,mid+3)],dark)
    c.rect(w-4,mid-2,w-2,mid+2,light);c.set(w-3,mid-2,"#101820FF")
    c.poly([(8,mid-3),(10,mid-5),(12,mid-3)],dark)
    if kind=="mackerel":
        for x in (6,8,10,12):c.set(x,mid-2,"#315D79FF")
    elif kind=="sea_bass":
        c.line(5,mid+2,w-5,mid+2,"#355E58FF")
    elif kind=="carp":
        c.set(w-1,mid,"#E6D3A2FF");c.set(7,mid,"#7D5B37FF");c.set(10,mid+1,"#7D5B37FF")
    elif kind=="crucian_carp":
        c.rect(6,mid-2,7,mid+2,_shade(base,-.12));c.rect(10,mid-2,11,mid+2,_shade(base,-.12))
    elif kind=="freshwater_bass":
        c.line(5,mid,w-5,mid,"#38563BFF");c.set(w-1,mid,"#253326FF")
    elif kind=="sardine":
        c.line(5,mid+1,w-5,mid+1,"#E2EEF2FF")


def _turtle(c,base):
    """Sea turtle: domed scute shell + beaked head + four paddle flippers."""
    dark=_shade(base,-.30);mid=_shade(base,-.10);light=_shade(base,.18)
    shell="#6D5A36FF";shell2="#9A7746FF";scute="#4E4730FF";w,h=c.w,c.h;g=h-1
    # Flat plastron/body under a very obvious domed shell.
    c.ellipse(w*.48,g-6,w*.31,4.2,shell)
    c.ellipse(w*.48,g-6,w*.25,3.3,shell2)
    c.line(5,g-4,15,g-4,mid,2)
    # Shell scutes: central spine plus diagonal plates.
    c.line(int(w*.48),g-9,int(w*.48),g-3,scute)
    c.line(6,g-7,10,g-4,scute);c.line(10,g-4,14,g-7,scute)
    c.line(6,g-5,9,g-8,scute);c.line(12,g-8,15,g-5,scute)
    # Distinct neck + small beaked head to the right.
    c.rect(w-7,g-7,w-4,g-4,base)
    c.ellipse(w-3.0,g-6.2,2.4,2.0,light)
    c.poly([(w-2,g-6),(w,g-5),(w-2,g-4)],light)
    c.set(w-3,g-7,"#172116FF")
    # Four broad sea flippers, front pair much longer than rear pair.
    c.poly([(14,g-5),(18,g-1),(14,g),(11,g-4)],base)
    c.poly([(13,g-8),(17,g-11),(15,g-6)],light)
    c.poly([(6,g-4),(2,g-1),(6,g),(8,g-4)],base)
    c.poly([(6,g-8),(2,g-10),(5,g-5)],mid)
    # Tiny tail behind the shell.
    c.poly([(4,g-5),(1,g-6),(4,g-3)],dark)


def _croc(c,base):
    """Crocodile: armored long body, heavy tail and unmistakable long jaw."""
    dark=_shade(base,-.32);deep=_shade(base,-.42);light=_shade(base,.14);w,h=c.w,c.h;g=h-1
    # Thick tapering tail occupies the left third.
    c.poly([(0,g-5),(5,g-8),(10,g-7),(10,g-3),(5,g-2)],dark)
    # Low armored torso.
    c.poly([(7,g-8),(11,g-10),(19,g-9),(22,g-7),(22,g-3),(8,g-2),(5,g-4)],base)
    # Long rectangular crocodilian muzzle (not a lizard head).
    c.rect(19,g-7,w,g-3,light)
    c.rect(21,g-8,w-3,g-6,base)
    c.set(22,g-9,"#10160FFF")  # raised eye
    c.set(w-2,g-6,"#20331CFF") # nostril
    c.line(20,g-4,w-1,g-4,deep)
    for x in range(22,w-1,2):
        c.set(x,g-3,"#E7E2C9FF")
    # Dorsal scutes create the saw-tooth armored silhouette.
    for x in (9,12,15,18):
        c.poly([(x-1,g-9),(x,g-11),(x+1,g-9)],dark)
    # Four short splayed legs.
    c.poly([(9,g-3),(7,g),(10,g-1),(11,g-3)],dark)
    c.poly([(14,g-3),(13,g),(16,g-1),(17,g-3)],dark)
    c.poly([(18,g-3),(18,g),(21,g-1),(20,g-3)],deep)


def _lizard(c,base):
    dark=_shade(base,-.30);light=_shade(base,.16);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.52,g-4,w*.27,2.2,base);c.ellipse(w*.78,g-5,2.2,1.8,light);c.set(w-2,g-6,"#1C1A12FF")
    c.line(int(w*.28),g-4,0,g-2,dark);c.line(6,g-3,3,g-1,dark);c.line(8,g-3,10,g-1,dark);c.line(11,g-3,9,g-1,dark);c.line(13,g-3,15,g-1,dark)
    c.set(8,g-5,dark);c.set(10,g-4,dark)


def _snake(c,base,kind):
    """Continuous S-bodied snake with triangular head, neck and forked tongue."""
    dark=_shade(base,-.32);light=_shade(base,.18);w,h=c.w,c.h;g=h-1
    # A thick continuous S curve makes the body read as one long limbless animal.
    pts=[(1,g-2),(4,g-5),(8,g-6),(11,g-4),(14,g-2),(17,g-4),(18,g-6)]
    for a,b in zip(pts,pts[1:]):
        c.line(a[0],a[1],b[0],b[1],base,3)
    # Narrow neck into a broad triangular/arrow-shaped head.
    c.line(17,g-5,19,g-6,base,2)
    c.poly([(18,g-8),(w-1,g-7),(w-1,g-4),(18,g-5)],light)
    c.set(w-3,g-7,"#11170FFF")
    # Forked tongue.
    c.set(w-1,g-5,"#B8333AFF");c.set(w,g-6,"#B8333AFF");c.set(w,g-4,"#B8333AFF")
    accent={"desert_snake":"#7F6434FF","swamp_snake":"#B0B74DFF","jungle_snake":"#D6D84CFF"}.get(kind,dark)
    if kind=="desert_snake":
        # Sand snake: dark dorsal diamonds.
        for x,y in ((5,g-5),(9,g-5),(13,g-3),(17,g-5)):
            c.set(x,y,accent);c.set(x+1,y+1,accent)
    elif kind=="swamp_snake":
        # Swamp snake: broken yellow-green bands.
        for x,y in ((4,g-4),(8,g-6),(12,g-3),(16,g-4)):
            c.line(x,y,x+1,y,accent,2)
    else:
        # Jungle snake: bright dorsal spots on vivid green.
        for x,y in ((4,g-5),(7,g-6),(11,g-4),(15,g-3),(18,g-6)):
            c.set(x,y,accent)


def _frog(c,base):
    dark=_shade(base,-.28);light=_shade(base,.18);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.5,g-4,w*.28,3.0,base);c.ellipse(w*.65,g-6,2.0,2.1,light)
    c.set(int(w*.58),g-7,"#182016FF");c.set(int(w*.72),g-7,"#182016FF")
    c.poly([(5,g-3),(1,g),(7,g)],dark);c.poly([(10,g-3),(w-1,g),(9,g)],dark)
    c.rect(6,g-3,10,g-2,_shade(base,.12))


def _crab(c,base,kind):
    dark=_shade(base,-.30);light=_shade(base,.18);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.5,g-4,w*.23,2.7,base);c.set(int(w*.43),g-7,"#151414FF");c.set(int(w*.57),g-7,"#151414FF")
    for x0,x1 in ((6,2),(8,3),(10,14),(9,15)):c.line(x0,g-3,x1,g-1,dark)
    c.line(5,g-5,2,g-7,base);c.ellipse(1,g-8,1.7,1.6,light);c.line(w-6,g-5,w-3,g-7,base);c.ellipse(w-2,g-8,1.7,1.6,light)
    if kind=="mangrove_crab":c.set(1,g-8,"#E08A55FF");c.set(w-2,g-8,"#E08A55FF")


def _scorpion(c,base):
    dark=_shade(base,-.30);light=_shade(base,.18);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.48,g-4,2.7,2.4,base);c.ellipse(w*.65,g-4,2,1.8,light)
    for x in (5,7,9,11):c.line(x,g-3,x-3,g-1,dark)
    c.line(5,g-4,2,g-6,base);c.ellipse(1,g-7,1.5,1.4,light);c.line(10,g-4,13,g-6,base);c.ellipse(14,g-7,1.5,1.4,light)
    # raised segmented tail
    c.line(10,g-5,12,g-8,dark);c.line(12,g-8,11,g-11,dark);c.line(11,g-11,9,g-12,dark);c.set(8,g-12,"#3A2617FF")


def _spider(c,base):
    dark=_shade(base,-.30);mark="#B55235FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.58,g-5,3.2,3,base);c.ellipse(w*.37,g-5,2.1,2.0,dark)
    for dx,dy in ((-4,-3),(-5,-1),(-5,1),(-4,3),(4,-3),(5,-1),(5,1),(4,3)):
        c.line(int(w*.5),g-5,int(w*.5)+dx,g-5+dy,dark)
    c.set(int(w*.58),g-6,mark);c.set(int(w*.62),g-4,mark);c.set(int(w*.30),g-6,"#E46B48FF")


def _beetle(c,base):
    dark=_shade(base,-.32);light=_shade(base,.12);w,h=c.w,c.h;g=h-1
    c.ellipse(w*.52,g-5,3.6,4.1,base);c.line(int(w*.52),g-9,int(w*.52),g-1,dark);c.ellipse(w*.52,g-9,2.0,1.5,dark)
    for y in (g-7,g-5,g-3):c.line(int(w*.42),y,int(w*.18),y-1,dark);c.line(int(w*.62),y,int(w*.86),y-1,dark)
    c.line(int(w*.47),g-10,int(w*.35),g-12,dark);c.line(int(w*.57),g-10,int(w*.70),g-12,dark);c.set(int(w*.62),g-7,light)


def _dragonfly(c,base,kind):
    dark=_shade(base,-.30);wing="#C7DFE8B8";w,h=c.w,c.h;cy=h-5
    c.line(3,cy,w-2,cy,base,1);c.ellipse(3,cy,1.5,1.5,dark);c.set(2,cy-1,"#12191AFF")
    if kind=="dragonfly":
        c.line(6,cy-1,3,cy-5,wing,2);c.line(6,cy+1,3,cy+4,wing,2);c.line(8,cy-1,11,cy-5,wing,2);c.line(8,cy+1,11,cy+4,wing,2)
    else:
        c.line(6,cy-1,5,cy-5,wing,1);c.line(7,cy-1,9,cy-5,wing,1);c.line(6,cy+1,5,cy+4,wing,1);c.line(7,cy+1,9,cy+4,wing,1)
    for x in range(7,w-2,2):c.set(x,cy,_shade(base,.15))


def _gull(c,base):
    gray="#87949FFF";dark="#303840FF";yellow="#E5B83EFF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.45,g-5,w*.30,2.8,base);c.poly([(4,g-6),(8,g-9),(11,g-6),(8,g-4)],gray)
    c.ellipse(w*.73,g-7,2.2,2.0,base);c.rect(w-3,g-7,w,g-6,yellow);c.set(w-4,g-8,dark)
    c.line(6,g-3,6,g,yellow);c.line(9,g-3,9,g,yellow);c.line(2,g-5,0,g-6,dark)


def _heron(c,base):
    gray="#6F8190FF";dark="#29343EFF";yellow="#D4A52EFF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.40,g-5,3.4,3.0,gray);c.line(int(w*.55),g-6,int(w*.66),g-12,base,2);c.ellipse(w*.70,g-13,2.0,1.7,base)
    c.line(int(w*.75),g-13,w-1,g-13,yellow);c.set(int(w*.73),g-14,dark)
    c.line(5,g-3,5,g,yellow);c.line(8,g-3,8,g,yellow);c.line(3,g-6,0,g-7,dark)


def _mallard(c,base):
    brown="#7A5A3BFF";cream="#D9D2B7FF";green="#2F6A4AFF";yellow="#D9A62AFF";dark="#1B2821FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.42,g-4,w*.30,2.8,brown);c.rect(5,g-5,10,g-3,cream);c.ellipse(w*.72,g-6,2.3,2.5,green)
    c.rect(w-3,g-6,w,g-5,yellow);c.set(w-4,g-7,dark);c.rect(int(w*.63),g-5,int(w*.67),g-4,"#F2F2E9FF")
    c.line(6,g-2,6,g,yellow);c.line(9,g-2,9,g,yellow);c.line(2,g-4,0,g-5,dark)


def _kingfisher(c,base):
    blue="#2E89B8FF";deep="#205C88FF";orange="#D97A35FF";white="#E7EFEAFF";dark="#152333FF";w,h=c.w,c.h;g=h-1
    c.ellipse(w*.43,g-5,3.0,2.7,blue);c.rect(5,g-4,9,g-2,orange);c.ellipse(w*.68,g-7,2.2,2.3,deep)
    c.line(w-4,g-7,w-1,g-8,dark);c.set(w-5,g-8,white);c.set(w-4,g-8,dark)
    c.line(6,g-3,6,g,"#9B6538FF");c.line(2,g-5,0,g-6,deep)


def _cave_bat(c,base):
    """Side-view cave bat: huge membrane wings, pointed ears and tiny body."""
    dark=_shade(base,-.34);deep=_shade(base,-.46);light=_shade(base,.16);membrane=_shade(base,-.12)
    w,h=c.w,c.h;g=h-1
    # Rear wing dominates the silhouette so it never reads as a small bird.
    c.poly([(10,g-7),(5,g-12),(0,g-10),(3,g-6),(0,g-2),(6,g-4),(10,g-3)],membrane)
    c.line(9,g-7,3,g-10,dark,1);c.line(9,g-6,3,g-5,dark,1)
    # Compact furry body and large triangular ear pair.
    c.ellipse(12,g-6,2.5,3.2,base)
    c.poly([(11,g-9),(11,g-13),(13,g-10)],dark)
    c.poly([(13,g-10),(15,g-13),(15,g-9)],dark)
    c.ellipse(15,g-7,2.1,2.0,light);c.set(16,g-8,"#E7C37BFF")
    c.set(16,g-7,deep);c.set(17,g-6,"#D7B8B0FF")
    # Small near wing under the body.
    c.poly([(12,g-6),(16,g-3),(14,g-1),(11,g-4)],dark)


def _cave_spider(c,base):
    """Heavy cave spider with broad abdomen and visible multiple eyes."""
    dark=_shade(base,-.32);deep=_shade(base,-.46);mark="#8E584FFF";eye="#D8C98EFF"
    w,h=c.w,c.h;g=h-1
    c.ellipse(11,g-5,4.4,3.5,base);c.ellipse(6,g-5,2.4,2.3,dark)
    # Eight long bent legs, exaggerated for readability underground.
    roots=(7,8,10,11)
    for i,x in enumerate(roots):
        c.line(x,g-5,x-3-i//2,g-9+i,dark,1);c.line(x-3-i//2,g-9+i,x-6,g-8+i,dark,1)
        c.line(x,g-4,x-2-i//2,g-1-i%2,dark,1);c.line(x-2-i//2,g-1-i%2,x-5,g,dark,1)
    for x in (5,6,7):c.set(x,g-6,eye)
    c.set(10,g-6,mark);c.set(12,g-4,mark);c.set(13,g-6,mark)



def _giant_spider_monster(c,base):
    """Oversized underground spider monster with a crystal-backed abdomen."""
    dark=_shade(base,-.30);deep=_shade(base,-.48);edge="#695273FF";mark="#B24862FF";eye="#F2D56CFF";fang="#DED6C7FF"
    w,h=c.w,c.h;g=h-1
    # Huge abdomen + armored thorax/head.
    c.ellipse(w*.61,g-7,w*.22,5.8,base)
    c.ellipse(w*.36,g-7,w*.14,4.3,dark)
    c.ellipse(w*.19,g-7,w*.10,3.5,deep)
    # Eight long angular legs spread almost the full 32px canvas.
    roots=(int(w*.28),int(w*.37),int(w*.48),int(w*.57))
    for i,x in enumerate(roots):
        lift=5+(i%2)*2
        c.line(x,g-7,x-5-i,g-12-lift//2,edge,1)
        c.line(x-5-i,g-12-lift//2,max(0,x-10-i*2),g-4-i%2,dark,2)
        c.line(x,g-5,x-4-i,g-1+i%2,edge,1)
        c.line(x-4-i,g-1+i%2,max(0,x-9-i*2),g,dark,2)
    # Near-side legs on the opposite half.
    for i,x in enumerate((int(w*.54),int(w*.63),int(w*.70),int(w*.76))):
        c.line(x,g-7,min(w-1,x+4+i),g-12-(i%2)*2,edge,1)
        c.line(min(w-1,x+4+i),g-12-(i%2)*2,min(w-1,x+9+i*2),g-4,dark,2)
        c.line(x,g-5,min(w-1,x+4+i),g-1+(i%2),edge,1)
        c.line(min(w-1,x+4+i),g-1+(i%2),min(w-1,x+9+i*2),g,dark,2)
    # Multiple glowing eyes and obvious fangs.
    for x in (int(w*.12),int(w*.17),int(w*.22),int(w*.27)):
        c.set(x,g-9,eye)
    c.set(int(w*.16),g-6,fang);c.set(int(w*.23),g-6,fang)
    # Red crystal/poison markings distinguish it from the existing cave spider.
    c.poly([(int(w*.57),g-11),(int(w*.63),g-15),(int(w*.68),g-11)],mark)
    c.set(int(w*.70),g-7,mark);c.set(int(w*.57),g-5,mark)


def _cave_snorble(c,base):
    """洞洞傻呼嚕: a squat cave beast with huge snout, ears and tiny feet."""
    dark=_shade(base,-.28);deep=_shade(base,-.44);light=_shade(base,.20);belly="#B99D7FFF";nose="#382B29FF";eye="#E8D28AFF"
    w,h=c.w,c.h;g=h-1
    # Round shaggy body.
    c.ellipse(w*.47,g-7,w*.28,6.0,base)
    c.ellipse(w*.69,g-8,w*.18,5.0,light)
    c.ellipse(w*.78,g-7,w*.12,3.4,belly)
    # Huge snout gives the intentionally silly "呼嚕" silhouette.
    c.ellipse(w*.83,g-5,w*.12,2.5,belly)
    c.ellipse(w*.91,g-5,w*.06,1.7,nose)
    c.set(int(w*.70),g-10,eye);c.set(int(w*.72),g-10,deep)
    # Ears and back tufts.
    c.poly([(int(w*.63),g-11),(int(w*.64),g-16),(int(w*.70),g-12)],dark)
    c.poly([(int(w*.75),g-12),(int(w*.79),g-16),(int(w*.82),g-11)],dark)
    for x in (int(w*.28),int(w*.38),int(w*.48)):
        c.poly([(x,g-12),(x+2,g-15),(x+4,g-12)],dark)
    # Four tiny feet and curled tail.
    for x in (int(w*.26),int(w*.41),int(w*.55),int(w*.67)):
        c.rect(x,g-4,x+3,g,deep)
    c.line(int(w*.20),g-7,int(w*.10),g-10,dark,2);c.line(int(w*.10),g-10,int(w*.06),g-7,dark,2)
    c.set(int(w*.86),g-5,"#E7D7C3FF")


def _plane_monster(c,base):
    """Flying cave monster shaped like a living propeller aircraft."""
    body="#596779FF";dark="#2D3745FF";edge="#8B98A8FF";wing="#6F7F91FF";eye="#F16A4FFF";mouth="#231F25FF";prop="#C3CBD3FF"
    w,h=c.w,c.h;g=h-1
    cy=g-8
    # Fuselage with organic eye/mouth in the nose.
    c.poly([(3,cy),(8,cy-3),(22,cy-3),(27,cy-1),(29,cy+1),(22,cy+3),(8,cy+3)],body)
    c.rect(12,cy-4,21,cy+4,dark)
    c.ellipse(24,cy,3.4,3.0,body)
    c.set(25,cy-1,eye);c.line(24,cy+1,28,cy+1,mouth,1)
    # Main wings, tail plane and fin.
    c.poly([(12,cy-2),(18,cy-10),(21,cy-10),(19,cy-2)],wing)
    c.poly([(12,cy+2),(18,cy+9),(21,cy+9),(19,cy+2)],wing)
    c.poly([(5,cy-2),(2,cy-7),(8,cy-5),(10,cy-2)],edge)
    c.poly([(5,cy+2),(2,cy+6),(8,cy+5),(10,cy+2)],edge)
    c.poly([(6,cy-3),(4,cy-8),(8,cy-4)],dark)
    # Propeller at the nose, deliberately cross-shaped in pixel art.
    c.line(30,cy-5,30,cy+5,prop,1);c.line(27,cy,31,cy,prop,1);c.set(30,cy,dark)
    # Small claw undercarriage marks it as a creature, not a vehicle item.
    c.line(15,cy+3,13,cy+6,dark,1);c.line(18,cy+3,20,cy+6,dark,1)


def _zombie(c,base):
    """Tall hunched undead with torn clothes and forward-reaching arms."""
    w,h=c.w,c.h;g=h-1;skin="#849579FF";deep="#394238FF";cloth="#514C45FF";tear="#80705CFF";eye="#D7D86AFF"
    c.rect(5,g-18,9,g-14,skin);c.rect(4,g-19,9,g-17,deep);c.set(8,g-17,eye)
    c.rect(4,g-14,10,g-8,cloth);c.set(5,g-11,tear);c.set(8,g-9,tear)
    # Both arms reach forward asymmetrically.
    c.line(9,g-13,13,g-11,skin,2);c.line(8,g-11,12,g-9,skin,1)
    c.line(4,g-12,2,g-9,skin,2)
    # Uneven dragging legs.
    c.rect(5,g-8,7,g-1,deep);c.rect(8,g-8,10,g-2,deep)
    c.line(5,g-1,3,g,deep,2);c.line(9,g-2,11,g,deep,2)


def _giant_worm(c,base):
    """Large segmented dungeon worm with plated head and circular maw."""
    dark=_shade(base,-.28);deep=_shade(base,-.44);light=_shade(base,.18);mouth="#351D22FF";tooth="#E4D5B7FF"
    w,h=c.w,c.h;g=h-1
    pts=[(1,g-3),(5,g-7),(10,g-6),(14,g-3),(18,g-5),(22,g-8),(25,g-7)]
    for a,b in zip(pts,pts[1:]):c.line(a[0],a[1],b[0],b[1],base,5)
    # Segment bands.
    for x,y in ((5,g-7),(10,g-6),(15,g-3),(20,g-6)):
        c.line(x,y-2,x,y+2,dark,2)
    # Broad armored head and ring mouth on the right.
    c.ellipse(26,g-7,3.0,3.4,light);c.ellipse(28,g-7,1.8,2.2,mouth)
    for yy in (g-8,g-6):c.set(27,yy,tooth)
    c.set(25,g-9,deep)


def _dungeon_mage(c,base):
    """Hooded underground bandit mage with glowing staff/crystal."""
    w,h=c.w,c.h;g=h-1;skin="#B98269FF";robe="#42364FFF";robe2="#69507DFF";boot="#2C2930FF";glow="#B56BFFFF";staff="#8B6A45FF"
    # Hood/head.
    c.poly([(5,g-20),(9,g-20),(11,g-17),(10,g-14),(4,g-14),(3,g-17)],robe2)
    c.rect(5,g-18,9,g-14,skin);c.set(8,g-17,"#ECE3D8FF")
    # Robe body and split hem.
    c.poly([(4,g-14),(10,g-14),(11,g-5),(9,g-1),(7,g-5),(5,g-1),(3,g-5)],robe)
    c.rect(5,g-12,9,g-10,robe2);c.set(6,g-8,glow)
    # Staff held forward, crystal deliberately oversized for magic identity.
    c.line(10,g-12,13,g-2,staff,2);c.ellipse(13,g-14,2.0,2.0,glow);c.set(13,g-15,"#E6C2FFFF")
    c.rect(4,g-1,6,g+1,boot);c.rect(8,g-1,10,g+1,boot)



def _creeper(c,base):
    """Original green cave bomber silhouette: tall fungal/crystal body with four feet."""
    w,h=c.w,c.h;g=h-1;dark="#183C22FF";deep="#102A19FF";light="#75C764FF";spot="#2F7D3DFF"
    # Rounded organic head/body rather than copying a block texture.
    c.ellipse(w*.50,g-15,w*.34,4.8,base)
    c.rect(int(w*.30),g-15,int(w*.72),g-5,base)
    c.rect(int(w*.24),g-7,int(w*.40),g,spot);c.rect(int(w*.60),g-7,int(w*.76),g,spot)
    c.rect(int(w*.34),g-2,int(w*.48),g+1,dark);c.rect(int(w*.55),g-2,int(w*.69),g+1,dark)
    # Iconic hostile face, but with diagonal organic mouth markings.
    c.rect(int(w*.34),g-17,int(w*.44),g-15,deep);c.rect(int(w*.58),g-17,int(w*.68),g-15,deep)
    c.line(int(w*.43),g-13,int(w*.56),g-10,deep,2);c.line(int(w*.56),g-10,int(w*.64),g-12,deep,1)
    c.set(int(w*.28),g-12,light);c.set(int(w*.70),g-9,light);c.set(int(w*.46),g-6,spot)


def _blue_poop(c,base):
    """Blue slime-like pile with a spiral top; kept as one connected mass."""
    w,h=c.w,c.h;g=h-1;dark="#25577EFF";deep="#173B5AFF";light="#7CC7F0FF";shine="#C6EEFFFF"
    c.ellipse(w*.50,g-3,w*.38,3.4,base)
    c.ellipse(w*.50,g-7,w*.29,2.8,base)
    c.ellipse(w*.52,g-10,w*.18,2.2,base)
    c.poly([(w*.49,g-12),(w*.60,g-14),(w*.65,g-12),(w*.58,g-10)],base)
    c.set(int(w*.39),g-5,deep);c.set(int(w*.61),g-5,deep)
    c.line(int(w*.43),g-3,int(w*.57),g-3,dark,1)
    c.set(int(w*.43),g-10,light);c.set(int(w*.53),g-12,shine)


def _giant_bago_bird(c,base):
    """Large underground fantasy bird with exaggerated multicolour plumage."""
    w,h=c.w,c.h;g=h-1
    purple="#B84EC6FF";blue="#4EA6D8FF";green="#59BE78FF";yellow="#E6C74EFF";orange="#E88745FF";red="#CB4C62FF";dark="#35233EFF";cream="#F1E1A5FF"
    # Huge rear wing and compact body/head.
    c.poly([(2,g-8),(7,g-16),(13,g-19),(17,g-14),(13,g-8),(8,g-4)],purple)
    c.poly([(4,g-9),(9,g-15),(14,g-16),(12,g-10)],blue)
    c.poly([(6,g-8),(10,g-12),(15,g-12),(12,g-7)],green)
    c.ellipse(18,g-8,5.2,4.4,orange)
    c.ellipse(23,g-11,3.0,3.0,red)
    c.poly([(25,g-11),(w-1,g-10),(25,g-9)],yellow)
    c.set(24,g-12,dark);c.set(23,g-13,cream)
    # Fan tail with different colour feathers.
    c.line(14,g-7,8,g-2,purple,2);c.line(15,g-7,11,g-1,blue,2);c.line(16,g-7,14,g-1,green,2)
    # Feet tucked close to body in the authored base.
    c.line(18,g-5,18,g-2,yellow,1);c.line(20,g-5,20,g-2,yellow,1)


def _elemental_zombie(c,kind):
    base="#7A806B" if kind=="fire_zombie" else "#718091"
    _zombie(c,base)
    w,h=c.w,c.h;g=h-1
    if kind=="fire_zombie":
        fire="#FF6A2CFF";hot="#FFD35AFF";ember="#C6321FFF"
        c.poly([(2,g-19),(4,g-23),(5,g-19),(7,g-22),(8,g-17)],fire)
        c.set(4,g-21,hot);c.set(7,g-20,hot);c.set(10,g-11,ember);c.set(6,g-7,fire)
    else:
        elec="#FFD94AFF";white="#FFF5A0FF";deep="#3457B1FF"
        c.line(2,g-20,5,g-16,elec,1);c.line(5,g-16,3,g-13,white,1)
        c.line(10,g-14,12,g-11,elec,1);c.line(12,g-11,10,g-8,deep,1)
        c.set(8,g-17,white);c.set(5,g-10,elec)


def _ghost(c,base):
    """Floating translucent ghost with a ragged lower body and long arms."""
    w,h=c.w,c.h;g=h-1;body="#BDEBFAAA";light="#E8FBFFFF";deep="#4F7390CC";eye="#18314CFF"
    c.ellipse(w*.50,g-12,w*.32,5.3,body)
    c.poly([(w*.23,g-12),(w*.77,g-12),(w*.82,g-4),(w*.70,g-1),(w*.58,g-4),(w*.48,g-1),(w*.36,g-4),(w*.22,g-1)],body)
    c.line(int(w*.30),g-10,1,g-6,body,2);c.line(int(w*.70),g-10,w-2,g-6,body,2)
    c.set(int(w*.40),g-14,eye);c.set(int(w*.62),g-14,eye)
    c.line(int(w*.43),g-10,int(w*.58),g-10,deep,1);c.set(int(w*.50),g-17,light)


def _toilet_man(c,base):
    """Original dungeon mutant: humanoid emerging from a ceramic toilet body."""
    w,h=c.w,c.h;g=h-1
    white="#DDE5E8FF";light="#F5FAFCFF";shade="#98A8B0FF";dark="#42515AFF";skin="#A97B62FF";eye="#14191DFF"
    # ceramic tank and bowl make the silhouette unmistakable
    c.rect(2,g-12,7,g-5,white);c.rect(3,g-11,6,g-6,light)
    c.ellipse(9,g-6,6.5,4.0,shade);c.ellipse(9,g-7,5.7,2.8,white)
    c.ellipse(9,g-8,4.3,1.5,dark);c.ellipse(9,g-8,3.1,.8,"#151C22FF")
    # neck/head rising from the bowl
    c.rect(8,g-14,10,g-9,skin);c.ellipse(9,g-16,3.1,3.0,skin)
    c.rect(7,g-18,11,g-17,"#4B332AFF");c.set(8,g-16,eye);c.set(10,g-16,eye);c.line(8,g-14,10,g-14,"#633E35FF",1)
    # human arms on either side
    c.line(5,g-12,1,g-8,skin,2);c.line(12,g-12,16,g-9,skin,2)
    # short feet under the bowl
    c.rect(5,g-3,7,g,shade);c.rect(11,g-3,13,g,shade)


def _speaker_man(c,base):
    """Original speaker-headed underground raider with a compact suit body."""
    w,h=c.w,c.h;g=h-1
    suit="#2B303AFF";edge="#4D5867FF";shirt="#D7DCE0FF";boot="#171A1FFF";box="#242933FF";rim="#707C8CFF";cone="#B8C1CAFF";core="#E76755FF"
    # square speaker head
    c.rect(4,g-23,12,g-16,box);c.rect(5,g-22,11,g-17,rim)
    c.ellipse(8,g-19.5,2.6,2.4,cone);c.ellipse(8,g-19.5,1.1,1.0,core)
    c.set(5,g-22,"#A9B5C1FF");c.set(10,g-22,"#A9B5C1FF")
    # suit torso/arms
    c.rect(5,g-16,11,g-7,suit);c.line(8,g-15,8,g-8,shirt,1);c.set(8,g-13,core)
    c.line(5,g-14,1,g-8,edge,2);c.line(11,g-14,15,g-8,edge,2)
    # legs
    c.rect(5,g-7,7,g-1,suit);c.rect(9,g-7,11,g-1,suit);c.rect(4,g-2,7,g+1,boot);c.rect(9,g-2,12,g+1,boot)


def _abyss_colossus(c,base):
    """32x32 giant boss silhouette: armored stone giant with glowing abyss core."""
    w,h=c.w,c.h;g=h-1
    stone="#3A3946FF";deep="#24232DFF";edge="#5A586BFF";plate="#777388FF";glow="#C95BFFFF";hot="#F2A2FFFF";eye="#F5D5FFFF"
    # horns / crown
    c.poly([(6,g-24),(3,g-29),(8,g-27),(10,g-23)],edge)
    c.poly([(w-7,g-24),(w-4,g-29),(w-9,g-27),(w-11,g-23)],edge)
    # giant head and eyes
    c.rect(9,g-26,w-9,g-19,stone);c.rect(11,g-25,w-11,g-20,deep)
    c.set(13,g-22,eye);c.set(w-14,g-22,eye)
    # huge shoulders, torso and armor
    c.ellipse(w*.50,g-13,w*.42,9.0,deep)
    c.rect(5,g-18,w-5,g-7,stone);c.rect(8,g-17,w-8,g-8,plate)
    c.poly([(w//2-4,g-16),(w//2+4,g-16),(w//2+6,g-10),(w//2,g-6),(w//2-6,g-10)],deep)
    c.ellipse(w*.50,g-12,3.4,3.2,glow);c.ellipse(w*.50,g-12,1.5,1.4,hot)
    # massive arms/fists
    c.rect(1,g-17,7,g-7,stone);c.rect(w-7,g-17,w-1,g-7,stone)
    c.ellipse(3,g-5,3.0,3.0,deep);c.ellipse(w-4,g-5,3.0,3.0,deep)
    # thick legs / feet
    c.rect(8,g-8,13,g,stone);c.rect(w-13,g-8,w-8,g,stone)
    c.rect(6,g-2,14,g+1,deep);c.rect(w-14,g-2,w-6,g+1,deep)
    # crack accents
    c.line(10,g-15,8,g-12,glow,1);c.line(w-11,g-16,w-9,g-13,glow,1)


def _human(c,kind):
    w,h=c.w,c.h;g=h-1;skin="#C68D63FF";dark="#2C2B2AFF";boot="#49382DFF";hair="#5B3B2AFF"
    if kind=="bandit":shirt="#313640FF";pants="#3B3330FF";accent="#A5453CFF"
    else:shirt="#577449FF";pants="#6A5137FF";accent="#D0B38CFF"
    # head, hair, torso, arms, legs
    c.rect(int(w*.40),g-17,int(w*.63),g-13,skin);c.rect(int(w*.39),g-18,int(w*.64),g-16,hair)
    c.set(int(w*.59),g-16,"#171717FF")
    c.rect(int(w*.34),g-13,int(w*.68),g-7,shirt);c.rect(int(w*.26),g-12,int(w*.34),g-7,skin);c.rect(int(w*.68),g-12,int(w*.76),g-7,skin)
    c.rect(int(w*.37),g-7,int(w*.48),g-1,pants);c.rect(int(w*.55),g-7,int(w*.66),g-1,pants);c.rect(int(w*.34),g-1,int(w*.49),g+1,boot);c.rect(int(w*.54),g-1,int(w*.69),g+1,boot)
    if kind=="bandit":
        c.rect(int(w*.39),g-15,int(w*.66),g-14,accent);c.line(int(w*.75),g-8,w-1,g-13,"#C8D0D4FF",1);c.set(w-1,g-13,"#E6EEF0FF")
    else:
        c.rect(int(w*.40),g-10,int(w*.62),g-9,accent)


def _fix62_monster(c, kind):
    """Nine strongly themed 32px silhouettes added for FIX62."""
    w,h=c.w,c.h;g=h-1
    if kind=="flying_imp":
        red="#B54138FF";dark="#3A2027FF";wing="#6D2634FF";hot="#FFD45AFF"
        c.poly([(w*.42,g-10),(w*.18,g-18),(1,g-16),(5,g-10),(1,g-5),(w*.34,g-7)],wing)
        c.poly([(w*.58,g-10),(w*.82,g-18),(w-2,g-15),(w-6,g-10),(w-2,g-5),(w*.66,g-7)],wing)
        c.ellipse(w*.50,g-9,4.2,6.0,red);c.ellipse(w*.50,g-15,4.0,3.6,red)
        c.poly([(w*.42,g-17),(w*.35,g-23),(w*.48,g-18)],dark);c.poly([(w*.58,g-17),(w*.67,g-23),(w*.53,g-18)],dark)
        c.set(int(w*.43),g-16,hot);c.set(int(w*.57),g-16,hot)
        c.line(int(w*.50),g-4,int(w*.68),g-1,dark,1);c.set(int(w*.70),g-2,hot)
    elif kind=="infernal_goat":
        rock="#342D32FF";edge="#5B4650FF";lava="#FF5A24FF";hot="#FFD15AFF"
        c.ellipse(w*.43,g-7,w*.32,5.2,rock);c.poly([(w*.66,g-11),(w*.84,g-12),(w-2,g-8),(w*.78,g-5)],edge)
        for x in (5,10,16,21):c.rect(x,g-5,x+3,g+1,rock);c.set(x+1,g,hot)
        c.line(int(w*.77),g-11,int(w*.67),g-17,edge,2);c.line(int(w*.67),g-17,int(w*.53),g-15,edge,2)
        c.line(int(w*.84),g-11,int(w*.88),g-17,edge,2);c.line(int(w*.88),g-17,w-2,g-14,edge,2)
        c.set(int(w*.84),g-10,hot);c.line(6,g-9,12,g-6,lava,1);c.line(14,g-10,18,g-6,lava,1)
    elif kind in ("burning_slime","crystal_slime"):
        crystal=kind=="crystal_slime";base="#56C8E9FF" if crystal else "#F15A24FF"
        dark="#246981FF" if crystal else "#8E241BFF";light="#D8FAFFFF" if crystal else "#FFD45AFF"
        c.poly([(2,g),(2,g-7),(5,g-12),(w//2,g-15),(w-5,g-12),(w-2,g-7),(w-2,g)],dark)
        c.poly([(4,g),(4,g-7),(7,g-10),(w//2,g-12),(w-7,g-10),(w-4,g-6),(w-4,g)],base)
        c.set(8,g-6,dark);c.set(w-9,g-6,dark);c.ellipse(w*.50,g-7,2.6,2.6,light)
        if crystal:
            for x,top in ((6,g-16),(w//2,g-20),(w-7,g-17)):
                c.poly([(x-2,g-11),(x,top),(x+2,g-11)],light)
        else:
            c.poly([(6,g-11),(8,g-19),(11,g-13),(14,g-22),(17,g-12),(20,g-18),(w-5,g-10)],"#FF7A22FF")
            c.set(14,g-18,light);c.set(8,g-15,light)
    elif kind=="flame_turtle":
        shell="#332E34FF";rim="#71514AFF";lava="#FF6022FF";hot="#FFD45AFF";skin="#6C4737FF"
        c.ellipse(w*.46,g-8,w*.33,6.4,shell);c.ellipse(w*.47,g-8,w*.25,4.8,rim)
        c.ellipse(w*.82,g-6,4.0,3.5,skin);c.set(w-4,g-7,hot)
        for x in (5,10,18,23):c.rect(x,g-4,x+3,g+1,skin)
        c.line(8,g-10,14,g-5,lava,1);c.line(15,g-12,18,g-6,lava,1);c.line(21,g-10,24,g-7,lava,1)
        c.poly([(11,g-13),(13,g-20),(16,g-14),(19,g-19),(21,g-12)],lava);c.set(14,g-17,hot)
    elif kind=="exploding_wisp":
        outer="#A22C45CC";fire="#FF4A27EE";hot="#FFD45AFF";core="#FFF6C8FF";eye="#40132AFF"
        c.poly([(w//2,g),(3,g-7),(7,g-11),(4,g-16),(10,g-14),(w//2,g-23),(w-10,g-14),(w-4,g-18),(w-7,g-10),(w-3,g-6)],outer)
        c.poly([(w//2,g-3),(7,g-8),(11,g-12),(9,g-17),(w//2,g-14),(w-9,g-17),(w-11,g-10),(w-6,g-7)],fire)
        c.ellipse(w*.50,g-10,5.0,5.0,hot);c.ellipse(w*.50,g-10,2.7,2.7,core)
        c.set(int(w*.44),g-11,eye);c.set(int(w*.57),g-11,eye)
    elif kind=="crystal_knight":
        armor="#29476FFF";edge="#5C85ADFF";crystal="#62D7F4FF";light="#D9FBFFFF";dark="#162638FF"
        c.poly([(6,g-22),(w//2,g-29),(w-6,g-22),(w-7,g-15),(7,g-15)],armor)
        c.rect(8,g-20,w-8,g-16,dark);c.set(w-11,g-18,light)
        c.rect(7,g-15,w-8,g-6,armor);c.rect(9,g-13,w-10,g-8,edge)
        c.rect(8,g-6,11,g+1,dark);c.rect(w-12,g-6,w-9,g+1,dark)
        c.ellipse(4,g-11,4.0,5.5,edge);c.poly([(2,g-12),(4,g-17),(7,g-12),(4,g-6)],crystal)
        c.line(w-6,g-14,w-2,g-1,light,2);c.line(w-7,g-12,w-1,g-12,crystal,1)
        c.poly([(w//2,g-25),(w//2+3,g-31),(w//2+5,g-24)],crystal)
    elif kind=="crystal_skeleton":
        bone="#C5DAE2FF";shade="#66839BFF";crystal="#4FD0F1FF";light="#D8FBFFFF";dark="#193044FF"
        c.ellipse(w*.50,g-25,5.2,4.8,bone);c.set(int(w*.43),g-26,dark);c.set(int(w*.57),g-26,dark)
        c.line(w//2,g-21,w//2,g-8,bone,2)
        for yy,span in ((g-19,6),(g-16,7),(g-13,6)):c.line(w//2-span,yy,w//2+span,yy,bone,1)
        c.line(w//2-1,g-9,6,g,bone,2);c.line(w//2+1,g-9,w-7,g,bone,2)
        c.line(w//2-4,g-18,3,g-8,bone,2);c.line(w//2+4,g-18,w-3,g-9,bone,2)
        c.poly([(w//2-5,g-17),(w//2-2,g-24),(w//2+1,g-17)],crystal)
        c.poly([(w-7,g-16),(w-3,g-23),(w-2,g-14)],crystal);c.set(w-4,g-20,light)
    elif kind=="crystal_lizard":
        body="#36758BFF";deep="#1D4356FF";crystal="#61D5F2FF";light="#D8FBFFFF"
        c.ellipse(w*.48,g-6,w*.30,4.1,body);c.poly([(w*.69,g-8),(w-2,g-7),(w-1,g-4),(w*.68,g-3)],body)
        c.set(w-4,g-7,light);c.line(int(w*.20),g-6,0,g-2,deep,2)
        for x in (7,12,18,23):c.line(x,g-4,x-2,g,deep,2)
        for x,top in ((8,g-13),(13,g-16),(18,g-14),(23,g-12)):
            c.poly([(x-2,g-8),(x,top),(x+2,g-8)],crystal)
        c.poly([(1,g-3),(5,g-9),(8,g-4)],crystal)


def creature_default_grid(species, size=16, archetypes=None):
    """Return hand-authored editable default pixels for one creature species."""
    sp=str(species);n=max(1,int(size));cw,ch=_dims(sp,n,archetypes)
    # Tall human sprites need the larger canvas chosen by recommended_canvas_size.
    if sp in ("bandit","villager","zombie","dungeon_bandit_mage","fire_zombie","lightning_zombie","creeper","toilet_man","speaker_man","abyss_colossus"):
        cw=max(cw,min(n,12));ch=max(ch,min(n,21))
    c=C(n,cw,ch);base=BASE.get(sp,"#9A7C64")

    if sp in ("flying_imp","infernal_goat","burning_slime","flame_turtle","exploding_wisp","crystal_knight","crystal_slime","crystal_skeleton","crystal_lizard"):_fix62_monster(c,sp)
    elif sp in ("slime","swamp_slime","ice_slime"):_slime(c,base,sp)
    elif sp in ("wolf","snow_wolf","village_dog"):_canine(c,base,sp)
    elif sp=="boar":_boar(c,base)
    elif sp=="deer":_deer(c,base)
    elif sp=="mountain_goat":_goat(c,base)
    elif sp=="capybara":_capybara(c,base)
    elif sp=="otter":_otter(c,base)
    elif sp=="jaguar":_jaguar(c,base)
    elif sp=="snow_hare":_hare(c,base)
    elif sp=="village_rat":_rat(c,base)
    elif sp in ("sardine","mackerel","sea_bass","carp","crucian_carp","freshwater_bass"):_fish(c,base,sp)
    elif sp=="sea_turtle":_turtle(c,base)
    elif sp=="crocodile":_croc(c,base)
    elif sp=="sand_lizard":_lizard(c,base)
    elif sp in ("desert_snake","swamp_snake","jungle_snake"):_snake(c,base,sp)
    elif sp=="swamp_frog":_frog(c,base)
    elif sp in ("shore_crab","mangrove_crab"):_crab(c,base,sp)
    elif sp=="desert_scorpion":_scorpion(c,base)
    elif sp=="jungle_spider":_spider(c,base)
    elif sp=="cave_spider":_cave_spider(c,base)
    elif sp=="giant_spider_monster":_giant_spider_monster(c,base)
    elif sp=="cave_snorble":_cave_snorble(c,base)
    elif sp=="plane_monster":_plane_monster(c,base)
    elif sp=="cave_bat":_cave_bat(c,base)
    elif sp=="zombie":_zombie(c,base)
    elif sp=="giant_worm":_giant_worm(c,base)
    elif sp=="dungeon_bandit_mage":_dungeon_mage(c,base)
    elif sp=="creeper":_creeper(c,base)
    elif sp=="blue_poop":_blue_poop(c,base)
    elif sp=="giant_bago_bird":_giant_bago_bird(c,base)
    elif sp in ("fire_zombie","lightning_zombie"):_elemental_zombie(c,sp)
    elif sp=="ghost":_ghost(c,base)
    elif sp=="toilet_man":_toilet_man(c,base)
    elif sp=="speaker_man":_speaker_man(c,base)
    elif sp=="abyss_colossus":_abyss_colossus(c,base)
    elif sp=="desert_beetle":_beetle(c,base)
    elif sp in ("dragonfly","damselfly"):_dragonfly(c,base,sp)
    elif sp=="gull":_gull(c,base)
    elif sp=="heron":_heron(c,base)
    elif sp=="mallard":_mallard(c,base)
    elif sp=="kingfisher":_kingfisher(c,base)
    elif sp in ("bandit","villager"):_human(c,sp)
    else:
        # Fallback remains recognizable as a small quadruped rather than a block.
        _canine(c,base,"wolf")
    return c.g
