extends Node2D
const DB=preload("res://scripts/content_db.gd")
const VFX=preload("res://scripts/source_vfx.gd")
var game: Node2D
var rules: Dictionary
var effects: Array=[]
var visuals: Array=[]
var pulses_fired:=0
var specials: Array=[]
func _ready() -> void:
	z_index=14
	rules=JSON.parse_string(FileAccess.get_file_as_string("res://data/boss_choreography.json"))
func base(caster: CharacterBody2D) -> Vector2:
	return caster.position+Vector2(0,20.0 if caster==game.player else caster.body_size.y*.5)
func facing(caster: CharacterBody2D) -> float:
	return caster.facing if caster==game.player else caster.direction
func start(caster: CharacterBody2D,weapon: String,heavy: bool,damage: float) -> bool:
	if effects.size()>=18 or not rules.patterns.has(weapon): return false
	var pattern: Dictionary=rules.patterns[weapon]
	var row: Dictionary={"owner":caster,"weapon":weapon,"origin":base(caster),"facing":facing(caster),"age":0.0,"damage":damage,"heavy":heavy,"pulse":0,"life":pattern.life if heavy else .82}
	if not heavy:
		row.origin.x+=row.facing*(11.0 if caster==game.player else caster.body_size.x*.5)
		var hit_ids: Dictionary={}
		for box in pattern.normal: hurt_box(row,world_box(row,box),damage,hit_ids)
	else:
		var beats: Array=[]
		var source:=DB.asset(str(pattern.effect))
		var info: Dictionary=source.get("animations",{}).get("effect",{})
		for key in info.get("frame_events",{}):
			if info.frame_events[key].get("damage",false): beats.append(float(key)/maxf(1,float(info.get("fps",8))))
		beats.sort()
		row.beats=beats if not beats.is_empty() else pattern.beats
		effects.append(row)
	if caster==game.player:
		var box: Array=pattern.envelope if heavy else envelope(pattern.normal)
		var binding: Dictionary=DB.asset("weapon."+weapon).get("combat_bindings",{}).get("heavy_attack" if heavy else "normal_attack",{})
		visuals.append({"owner":caster,"at":world_box(row,box),"age":0.0,"life":row.life,"asset":str(binding.get("effect_asset","effect."+weapon+("_heavy" if heavy else "_normal"))),"state":str(binding.get("effect_state","effect")),"facing":row.facing})
	return true
func envelope(boxes: Array) -> Array:
	var out: Array=boxes[0].duplicate()
	for box in boxes:
		out=[minf(out[0],box[0]),minf(out[1],box[1]),maxf(out[2],box[2]),maxf(out[3],box[3])]
	return out
func world_box(row: Dictionary,raw: Array) -> Rect2:
	var left:=float(raw[0])*float(row.facing)
	var right:=float(raw[2])*float(row.facing)
	return Rect2(row.origin+Vector2(minf(left,right),float(raw[1])),Vector2(absf(right-left),float(raw[3])-float(raw[1])))
func hurt_box(row: Dictionary,box: Rect2,damage: float,seen: Dictionary) -> void:
	var targets: Array=game.gameplay.actors.get_children() if row.owner==game.player else [game.player]
	for target in targets:
		if target.hp<=0 or seen.has(target.get_instance_id()): continue
		var size: Vector2=target.body_shape.shape.size if target==game.player else target.body_size
		var at: Vector2=target.position+(target.body_shape.position if target==game.player else Vector2.ZERO)
		if box.intersects(Rect2(at-size*.5,size)):
			seen[target.get_instance_id()]=true
			if target==game.player:
				if target.roll_left<=0: target.take_damage(damage,float(row.facing))
			else: target.take_hit(damage,float(row.facing)*80)
func authored(caster: CharacterBody2D,id: String,state: String) -> void:
	var source:=DB.asset(id)
	var binding: Dictionary=source.get("combat_bindings",{}).get(state,{})
	if binding.get("render_mode","")!="authored": return
	var aid:=str(binding.get("effect_asset",id))
	var effect_state:=str(binding.get("effect_state","effect"))
	var info: Dictionary=DB.asset(aid).get("animations",{}).get(effect_state,{})
	var life:=maxf(.1,float(info.get("frames",[]).size())/maxf(1,float(info.get("fps",8))))
	var width:=float(binding.get("world_width_tiles",3))*40
	var height:=float(binding.get("world_height_tiles",2))*40
	var f:=facing(caster)
	var origin:=base(caster)+Vector2(f*float(binding.get("forward_tiles",0))*40,0)
	var segments: Array=binding.get("segments",[])
	if segments.is_empty(): segments=[{"delay":0.0,"dx_tiles":0.0,"dy_tiles":0.0}]
	for segment in segments:
		if visuals.size()>=96: break
		var delay:=float(segment.get("delay",0))
		var duration:=life
		if binding.get("segment_playback","")=="clip": duration=float(binding.get("segment_clip_frames",8))/maxf(1,float(binding.get("segment_source_fps",16)))
		elif binding.get("segment_playback","")=="phase": duration=float(segment.get("hold",.16))
		var pos:=origin+Vector2(f*(float(segment.get("dx_tiles",0))*40+float(binding.get("offset_x",0))),float(segment.get("dy_tiles",0))*40+float(binding.get("offset_y",0)))
		var rect:=Rect2(pos+Vector2(0.0 if f>0 else -width,-height),Vector2(width,height))
		visuals.append({"owner":caster,"at":rect,"age":-delay,"life":duration,"asset":aid,"state":effect_state,"facing":f,"follow":bool(binding.get("follow_owner",false)),"owner_at":caster.position,"binding":binding,"segment":segment})
func update(delta: float) -> void:
	for i in range(effects.size()-1,-1,-1):
		var row: Dictionary=effects[i]
		if not is_instance_valid(row.owner) or row.owner.hp<=0:
			effects.remove_at(i)
			continue
		row.age+=delta
		var pattern: Dictionary=rules.patterns[row.weapon]
		while int(row.pulse)<row.beats.size() and float(row.beats[int(row.pulse)])<=float(row.age):
			var pulse: Array=pattern.pulses[mini(int(row.pulse),pattern.pulses.size()-1)]
			for pair in pulse: hurt_box(row,world_box(row,pair[0]),float(row.damage)*float(pair[1]),{})
			row.pulse+=1
			pulses_fired+=1
		if row.age>=row.life: effects.remove_at(i)
	for i in range(visuals.size()-1,-1,-1):
		visuals[i].age+=delta
		if visuals[i].age>=visuals[i].life or not is_instance_valid(visuals[i].owner): visuals.remove_at(i)
	queue_redraw()
func _physics_process(delta: float) -> void:
	update(delta)
	update_specials(delta)
func emit_bolt(caster: CharacterBody2D,action: Dictionary,element: String,at: Vector2,motion: Vector2) -> void:
	if game.gameplay.enemy_shots.size()>=64: return
	game.gameplay.enemy_shots.append({"at":at,"motion":motion,"damage":float(action.get("damage",10)),"life":float(action.get("life",2.4)),"age":0.0,"radius":float(action.get("radius",7)),"element":element,"owner":caster})
func creature_effect(caster: CharacterBody2D,action: Dictionary,target: Vector2) -> void:
	var kind:=str(action.get("effect","none"))
	var damage:=float(action.get("damage",10))
	var f:=facing(caster)
	if kind.begins_with("fix103_"):
		start(caster,str(rules.rewards.get(str(caster.placement.species),"")),kind.ends_with("_heavy"),damage)
		return
	var at: Vector2=caster.position+Vector2(f*8,-caster.body_size.y*.12)
	var dir: Vector2=(target-at).normalized()
	if kind in ["magic_bolt","lightning_bolt","seed_volley","wind_volley","triple_breath"]:
		if kind=="triple_breath":
			for i in range(3):
				var element: String=["waterball","fireball","electricball"][i]
				var muzzle: Vector2=base(caster)+Vector2(f*([48,67,85][i]+8-48)*2.5,-(96-([44,28,16][i]+6))*2.5)
				for angle in [-.23,0,.23]: emit_bolt(caster,action,element,muzzle,(target-muzzle).normalized().rotated(angle)*300)
		else:
			var element:=str(action.get("element","arcane"))
			if kind=="lightning_bolt": element="lightning"
			if kind=="seed_volley": element="seed"
			if kind=="wind_volley": element="wind"
			for angle in [-.12,0,.12] if kind in ["seed_volley","wind_volley"] else [0]:
				emit_bolt(caster,action,element,at,dir.rotated(angle)*maxf(90,float(action.get("speed",280))))
	elif kind=="fire_column":
		var width:=maxf(10,float(action.get("column_width",22)))
		var height:=maxf(28,float(action.get("column_height",64)))
		game.gameplay.enemy_shots.append({"at":base(caster)+Vector2(f*maxf(18,float(action.get("column_distance",34))),-height*.5),"motion":Vector2.ZERO,"damage":damage,"life":.72,"age":0.0,"element":"fire_column","width":width,"height":height,"owner":caster})
	elif kind in ["kong_combo","python_tail","python_tornado","poison_puff"]:
		if specials.size()>=16: return
		var heavy: bool=action.get("action_id","")=="barrage"
		var life:=3.0 if kind=="python_tornado" or heavy else (.45 if kind=="kong_combo" else (.35 if kind=="python_tail" else 4.0))
		specials.append({"owner":caster,"origin":base(caster),"age":0.0,"life":life,"kind":kind,"facing":f,"damage":damage,"next":0.0,"hits":15 if heavy else (2 if kind=="kong_combo" else 1),"count":0,"at":Rect2()})
	elif kind!="none":
		var radius:=float(action.get("hit_radius",40))
		if at.distance_to(target)<=radius+20 and game.world.clear_sight(at,target) and game.player.roll_left<=0:
			game.player.take_damage(damage,f)
		if kind=="explosion": game.gameplay.projectiles_system.explosions.append({"at":at,"radius":radius,"age":0.0,"life":.5})
func update_specials(delta: float) -> void:
	for i in range(specials.size()-1,-1,-1):
		var row: Dictionary=specials[i]
		if not is_instance_valid(row.owner) or row.owner.hp<=0:
			specials.remove_at(i)
			continue
		row.age+=delta
		var kind:=str(row.kind)
		var from: Vector2=row.origin
		var width: float=row.owner.body_size.x
		var f:=float(row.facing)
		var box: Rect2
		if kind=="python_tornado":
			var progress:=clampf(float(row.age)/3,0,1)
			var x:=from.x+f*(width*.5+40+160*progress)
			box=Rect2(x-20,from.y-40*(2+3*progress),40,40*(2+3*progress))
			var old_x:=x-f*160*delta/3
			if not game.world.clear_sight(Vector2(old_x,from.y-14),Vector2(x,from.y-14)):
				specials.remove_at(i)
				continue
			# Clip visible height and damage at the same ceiling.
			var hit:=get_world_2d().direct_space_state.intersect_ray(PhysicsRayQueryParameters2D.create(Vector2(x,from.y-1),Vector2(x,box.position.y),9))
			if not hit.is_empty():
				box.size.y=from.y-float(hit.position.y)
				box.position.y=hit.position.y
		elif kind=="python_tail":
			var x:=from.x+f*width*.5
			var y:=from.y-minf(row.owner.body_size.y*.55,42)
			var end:=Vector2(x+f*120,y)
			var hit:=get_world_2d().direct_space_state.intersect_ray(PhysicsRayQueryParameters2D.create(Vector2(x,y),end,9))
			if not hit.is_empty(): end=hit.position
			box=Rect2(minf(x,end.x),y-19.2,absf(end.x-x),38.4)
		elif kind=="kong_combo":
			from=base(row.owner)
			var strike_start:=from.x+f*maxf(12,width*.38)
			box=Rect2(minf(strike_start,strike_start+f*80),from.y-120,80,120)
		else: box=Rect2(from-Vector2(70,100),Vector2(140,100))
		row.at=box
		while row.next<=row.age and (kind in ["python_tornado","poison_puff"] or row.count<row.hits):
			var player_box:=Rect2(game.player.position-Vector2(11,20),Vector2(22,40))
			if box.intersects(player_box) and game.world.clear_sight(box.get_center(),game.player.position) and game.player.roll_left<=0:
				game.player.take_damage(float(row.damage),f)
			row.count+=1
			row.next+=.25 if kind=="python_tornado" else .2
		if row.age>=row.life: specials.remove_at(i)
	queue_redraw()
func _draw() -> void:
	for row in specials:
		var id: String="weapon.python_whip" if str(row.kind).begins_with("python_") else ("weapon.kong_gauntlets" if row.kind=="kong_combo" else "effect.corrosive_splash")
		var state: String="heavy_effect" if row.kind=="python_tornado" else "normal_effect"
		var image:=VFX.frame(id,state,float(row.age))
		var rect: Rect2=row.at
		if image and rect.has_area():
			draw_set_transform(rect.get_center(),0,Vector2(float(row.facing),1))
			draw_texture_rect(image,Rect2(-rect.size*.5,rect.size),false)
			draw_set_transform(Vector2.ZERO)
	for fx in visuals:
		if fx.age<0: continue
		var age:=float(fx.age)
		var binding: Dictionary=fx.get("binding",{})
		if binding.get("segment_playback","")=="phase":
			var info: Dictionary=DB.asset(fx.asset).get("animations",{}).get(fx.state,{})
			age=float(fx.get("segment",{}).get("phase",0))*maxi(0,info.get("frames",[]).size()-1)/maxf(1,float(info.get("fps",8)))
		elif binding.get("segment_playback","")=="clip": age+=float(binding.get("segment_start_frame",0))/maxf(1,float(binding.get("segment_source_fps",16)))
		var rect: Rect2=fx.at
		if fx.get("follow",false) and is_instance_valid(fx.owner): rect.position+=fx.owner.position-fx.owner_at
		if binding.get("world_motion_mode","")=="distance":
			var progress:=clampf((float(fx.age)-float(binding.get("travel_start_delay",0)))/maxf(.01,float(binding.get("travel_duration",1))),0,1)
			rect.position+=Vector2(float(fx.facing)*40*(float(binding.get("travel_start_x_tiles",0))+float(binding.get("travel_distance_tiles",0))*progress),40*(float(binding.get("travel_start_y_tiles",0))+float(binding.get("travel_y_tiles",0))*progress))
		var image:=VFX.frame(fx.asset,fx.state,age)
		if image:
			draw_set_transform(rect.get_center(),0,Vector2(float(fx.facing),1))
			draw_texture_rect(image,Rect2(-rect.size*.5,rect.size),false)
			draw_set_transform(Vector2.ZERO)
