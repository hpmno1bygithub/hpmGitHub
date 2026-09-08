extends RefCounted
const DB=preload("res://scripts/content_db.gd")
const EFFECTS=["damage_knockback","magic_bolt","lightning_bolt","fire_column","explosion","none","seed_volley","wind_volley","vine_whip","ceiling_bite","triple_breath","kong_combo","python_tail","python_tornado","poison_puff","fix103_lava","fix103_lava_heavy","fix103_ice","fix103_ice_heavy","fix103_water","fix103_water_heavy","fix103_thunder","fix103_thunder_heavy","fix103_drill","fix103_drill_heavy","fix103_roots","fix103_roots_heavy"]
static var base: Dictionary={}
static var resolved: Dictionary={}
static var revision := -1
var actor: CharacterBody2D
var profile: Dictionary={}
var active: Dictionary={}
var cooldowns: Dictionary={}
var elapsed:=0.0
var duration:=0.0
var trigger:=0.5
var hit:=false
var vfx_done:=false
var vfx_trigger:=0.0
var locked_target:=Vector2.ZERO
func configure(owner: CharacterBody2D) -> void:
	actor=owner
	if base.is_empty(): base=JSON.parse_string(FileAccess.get_file_as_string("res://assets/creature_actions.json")).creatures
	if revision!=DB.revision or resolved.is_empty():
		resolved=DB.resolve("actions",base)
		revision=DB.revision
	profile=resolved.get("creature."+str(actor.placement.species),{})
func tick(delta: float, chase: bool) -> bool:
	if not actor.placement.get("actions_enabled",true): return false
	for key in cooldowns: cooldowns[key]=maxf(0,float(cooldowns[key])-delta)
	if active.is_empty() and chase:
		var candidates: Array=[]
		var distance: Vector2=actor.target.position-actor.position
		for key in profile.get("actions",{}):
			var row: Dictionary=profile.actions[key]
			var attack_mode:=str(actor.placement.get("attack_mode","default"))
			if attack_mode in ["none","contact"]: continue
			if attack_mode=="normal" and key!="attack": continue
			if attack_mode=="special" and key!="special": continue
			if not row.get("enabled",true) or not row.get("effect","none") in EFFECTS or float(cooldowns.get(key,0))>0: continue
			if absf(distance.x)<float(row.get("range_min",0)) or absf(distance.x)>float(row.get("range_max",80)) or absf(distance.y)>float(row.get("vertical_range",80)): continue
			if row.get("requires_ground",false) and not actor.is_on_floor(): continue
			if randf()>float(row.get("chance",1)): continue
			candidates.append([key,row])
		candidates.sort_custom(func(a,b): return float(a[1].get("priority",0))>float(b[1].get("priority",0)))
		if not candidates.is_empty():
			var key: String=candidates[0][0]
			active=candidates[0][1].duplicate(true)
			active.action_id=key
			locked_target=actor.target.position
			cooldowns[key]=float(active.get("cooldown",1))
			var animation:=str(active.get("animation_state","attack"))
			var source:=DB.asset("creature."+str(actor.placement.species))
			var info: Dictionary=source.get("animations",{}).get(animation,{})
			duration=float(active.get("duration",0))
			if duration<=0: duration=maxf(.2,float(info.get("frames",[]).size())/maxf(1,float(info.get("fps",8))))
			trigger=clampf(float(info.get("events",{}).get("action",duration*.5*float(info.get("fps",8))))/maxf(1,float(info.get("fps",8)))/duration,0,1)
			vfx_trigger=clampf(float(info.get("events",{}).get("effect",info.get("events",{}).get("action",trigger*duration*float(info.get("fps",8)))))/maxf(1,float(info.get("fps",8)))/duration,0,1)
			vfx_done=false
			elapsed=0
			hit=false
			actor.direction=signf(distance.x)
			if active.get("motion","")=="leap": actor.velocity.y=-float(active.get("lift_speed",300))
			if actor.sprite.sprite_frames.has_animation(animation):
				actor.sprite.play(animation)
				actor.sprite.frame=0
	if active.is_empty(): return false
	elapsed+=delta
	var motion:=str(active.get("motion","stationary"))
	actor.velocity.x=actor.direction*float(active.get("speed",0)) if motion in ["charge","leap"] else 0
	if motion=="dive":
		actor.velocity=(actor.target.position-actor.position).normalized()*float(active.get("speed",180))
	elif actor.locomotion() in ["ground","swim"]: actor.velocity.y+=1250*delta
	else: actor.velocity.y=0
	actor.move_and_slide()
	actor.sprite.flip_h=actor.direction<0
	if not vfx_done and elapsed>=duration*vfx_trigger:
		vfx_done=true
		actor.target.get_parent().boss_combat.authored(actor,"creature."+str(actor.placement.species),str(active.get("animation_state","attack")))
	if not hit and elapsed>=duration*trigger:
		hit=true
		actor.target.get_parent().boss_combat.creature_effect(actor,active,locked_target)
	actor.queue_redraw()
	if elapsed>=duration: active.clear()
	return true
