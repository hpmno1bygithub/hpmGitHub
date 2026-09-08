extends RefCounted
## Shared original pixel animations for monsters, bosses, magic and weapons.
const DB=preload("res://scripts/content_db.gd")
const Art=preload("res://scripts/art.gd")
static var cache: Dictionary={}
static var revision: int=-1
static func frame(id: String, state: String, age: float) -> Texture2D:
	if revision!=DB.revision:
		revision=DB.revision
		cache.clear()
	var source:=DB.asset(id)
	var info: Dictionary=source.get("animations",{}).get(state,{})
	if info.is_empty(): info=source.get("animations",{}).get("idle",{})
	if info.is_empty(): return Art.texture(id)
	var count: int=info.get("frames",[]).size()
	if count==0: return Art.texture(id)
	var index:=maxi(0,floori(age*float(info.get("fps",8))))
	index=index%count if info.get("loop",false) else mini(count-1,index)
	var key:=id+":"+state+":"+str(index)
	if not cache.has(key): cache[key]=DB.grid_texture(info.frames[index])
	return cache[key]
static func draw_asset(canvas: Node2D,id: String,state: String,age: float,at: Vector2,size: Vector2,angle:=0.0) -> bool:
	var image:=frame(id,state,age)
	if image==null: return false
	canvas.draw_set_transform(at,angle)
	canvas.draw_texture_rect(image,Rect2(-size*.5,size),false)
	canvas.draw_set_transform(Vector2.ZERO)
	return true
static func quad(canvas: Node2D,at: Vector2,size: Vector2,color: Color) -> void:
	canvas.draw_rect(Rect2(at-size*.5,size),color)
static func bolt(canvas: Node2D,shot: Dictionary) -> void:
	var at: Vector2=shot.at
	var radius:=float(shot.get("radius",7))
	var kind:=str(shot.get("element","arcane"))
	if kind=="fire_column":
		var w:=float(shot.get("width",20))
		var h:=float(shot.get("height",60))
		quad(canvas,at,Vector2(w,h),Color(1,.28,.06,.84))
		quad(canvas,at+Vector2(0,2),Vector2(w*.48,h*.78),Color(1,.78,.16,.94))
		quad(canvas,at-Vector2(0,h*.32),Vector2(w*.28,h*.22),Color(1,.94,.48,.9))
		return
	var colors: Dictionary={"fire":[Color(1,.24,.04,.96),Color(1,.86,.18)],"fireball":[Color(1,.28,.04,.96),Color(1,.84,.18)],"waterball":[Color(.1,.5,.96,.91),Color(.65,.9,1,.95)],"iceball":[Color(.38,.8,1,.94),Color(.86,.97,1)],"electricball":[Color(1,.78,.12,.94),Color(1,.96,.58)],"lightning":[Color(1,.82,.12,.96),Color(1,.98,.66)],"arcane":[Color(.62,.26,.92,.96),Color(.91,.72,1)]}
	if kind in ["fireball","waterball","iceball","electricball"]:
		if draw_asset(canvas,"magic."+kind,"idle",float(shot.get("age",0)),at,Vector2.ONE*40*radius/8): return
	if kind in ["seed","wind","cloud","crystal"]:
		var diameter:=radius*2
		var v: Vector2=shot.get("motion",Vector2.ZERO)
		if kind=="wind":
			for k in range(3): quad(canvas,at-v*k*.014+Vector2(0,k*3),Vector2(diameter*(1-k*.19),3),Color(.62,.93,.94,.95-k*.2))
		elif kind=="seed":
			quad(canvas,at,Vector2(diameter*.8,diameter),Color(.62,.39,.18))
			quad(canvas,at-Vector2(1,2),Vector2(diameter*.4,diameter*.6),Color(.96,.8,.41))
		else:
			quad(canvas,at,Vector2(diameter*.72,diameter),Color(.22,.75,1,.95) if kind=="crystal" else Color(.62,.76,.81,.95))
			quad(canvas,at+Vector2(2,-2),Vector2(diameter*.3,diameter*.7),Color(.84,.98,1))
		return
	var palette: Array=colors.get(kind,colors.arcane)
	var d:=radius*(2.1 if kind=="fireball" else (2.15 if kind=="waterball" else (2.25 if kind in ["fire","electricball"] else 2.2)))
	quad(canvas,at,Vector2.ONE*d,palette[0])
	quad(canvas,at-Vector2.ONE,Vector2.ONE*d*(.48 if kind in ["fireball","electricball"] else .43),palette[1])
	if kind in ["lightning","electricball"]: quad(canvas,at+Vector2(2,-2),Vector2(d*.18,d*.62),Color(1,.88,.32,.86))
