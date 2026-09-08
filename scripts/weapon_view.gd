extends Node2D
const DB = preload("res://scripts/content_db.gd")
const Art = preload("res://scripts/art.gd")
var player: CharacterBody2D
var game: Node2D
var quads: Dictionary
var cache: Dictionary = {}
var revision := -1

func _ready() -> void:
	quads=JSON.parse_string(FileAccess.get_file_as_string("res://data/weapon_vfx.json")).weapons
	z_index=1
func texture(state: String, progress: float) -> Texture2D:
	if revision!=DB.revision:
		revision=DB.revision
		cache.clear()
	var id: String="weapon."+player.equipped
	var source:=DB.asset(id)
	var info: Dictionary=source.get("animations",{}).get(state,{})
	if info.is_empty(): return Art.texture(id)
	var count: int=info.frames.size()
	var frame:=clampi(floori(progress*count),0,count-1)
	var key:=id+state+str(frame)
	if not cache.has(key): cache[key]=DB.grid_texture(info.frames[frame])
	return cache[key]
func _process(_delta: float) -> void:
	queue_redraw()
func _draw() -> void:
	if player==null or game.actions.mode!="weapon" or player.posture=="prone" or player.roll_left>0: return
	var swinging: bool=player.swing_left>0
	var progress:=clampf(1-player.swing_left/maxf(.001,player.swing_duration),0,1)
	var state: String=("heavy_attack" if player.heavy_attack else "normal_attack") if swinging else ("heavy_charge" if player.charge>0 else "idle")
	var source:=DB.asset("weapon."+player.equipped)
	var row: Dictionary=game.gameplay.weapons[player.equipped]
	var base_weapon:=str(row.get("base_weapon",player.equipped))
	var phase: float=progress if swinging else (minf(player.charge/float(row.heavy_charge_seconds),.999) if player.charge>0 else 0.0)
	var body:=texture(state,phase)
	if body:
		var facing: float=player.facing
		var angle:=0.0
		if row.get("delivery","melee") in ["energy_arrow","laser","rocket","tnt","yoyo","battle_top","companion_drone"]:
			var aim: Vector2=game.actions.direction()
			facing=1.0 if aim.x>=0 else -1.0
			angle=aim.angle()-(PI if facing<0 else 0.0)
		draw_set_transform(Vector2(facing*7,-1.2),angle,Vector2(facing,1))
		draw_texture_rect(body,Rect2(-body.get_size()*1.25,body.get_size()*2.5),false)
		draw_set_transform(Vector2.ZERO)
	if player.charge>=float(row.heavy_charge_seconds):
		for i in range(3):
			var angle:=Time.get_ticks_msec()*.006+i*2.1
			draw_rect(Rect2(Vector2(player.facing*7,-1.2)+Vector2.from_angle(angle)*(10+i*5),Vector2(3,3)),Color("#f5f7fa"))
	if not swinging or row.get("delivery","")=="boss_fix103": return
	var binding: Dictionary=source.get("combat_bindings",{}).get(state,{})
	if binding.get("render_mode","fix54")=="authored":
		if row.get("delivery","melee")!="melee": return
		var info: Dictionary=source.get("animations",{}).get(state,{})
		var trigger:=float(info.get("events",{}).get(binding.get("trigger_event","effect"),0))/maxf(1,info.get("frames",[]).size()-1)
		if progress<trigger: return
		var effect_state:=str(binding.get("effect_state","heavy_effect" if player.heavy_attack else "normal_effect"))
		var effect: Texture2D
		if str(binding.get("effect_asset",""))!="":
			effect=preload("res://scripts/source_vfx.gd").frame(str(binding.effect_asset),effect_state,(progress-trigger)*player.swing_duration)
		else: effect=texture(effect_state,(progress-trigger)/maxf(.001,1-trigger))
		if effect:
			var scale_factor:=2.5*float(binding.get("scale",1))
			draw_set_transform(Vector2(player.facing*float(binding.get("offset_x",0)),float(binding.get("offset_y",0))),0,Vector2(player.facing,1))
			draw_texture_rect(effect,Rect2(-effect.get_size()*scale_factor*.5,effect.get_size()*scale_factor),false)
			draw_set_transform(Vector2.ZERO)
	elif player.heavy_attack and quads.has(base_weapon):
		for quad in quads[base_weapon][clampi(roundi(progress*60),0,60)]:
			var color: Array=quad[4]
			draw_rect(Rect2(Vector2(float(quad[0])*player.facing,float(quad[1]))-Vector2(float(quad[2]),float(quad[3]))*.5,Vector2(float(quad[2]),float(quad[3]))),Color(color[0],color[1],color[2],color[3]))
	elif base_weapon=="whip":
		var reach:=float(row.reach_tiles)*40
		var pulse:=sin(progress*PI)
		for i in range(28):
			var t:=i/27.0
			var distance:=10+(reach*(.44+.56*pow(pulse,.55))-10)*t
			var bend:=sin(t*PI)*(5+15*(1-pulse))*sin(progress*PI*1.7+t*1.1)
			draw_rect(Rect2(player.facing*distance,bend,3,3),Color(.78,.51,.27,.9))
