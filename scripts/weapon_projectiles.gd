extends RefCounted
const VFX=preload("res://scripts/source_vfx.gd")
var combat: Node2D
var explosions: Array=[]
func spawn(row: Dictionary, heavy: bool, aim: Vector2, id: String) -> void:
	var prefix: String="heavy_" if heavy else ""
	var kind:=str(row.get("delivery","melee"))
	var count:=1
	if heavy:
		if kind=="energy_arrow": count=int(row.get("heavy_arrow_count",12))
		elif kind in ["laser","rocket"]: count=int(row.get("heavy_projectile_count",3))
	var speed:=float(row.get(prefix+"projectile_speed",row.get("projectile_speed",430)))
	var life:=float(row.get(prefix+"projectile_life",row.get("projectile_life",2)))
	for i in range(count):
		if combat.projectiles.size()>=160: break
		var spread:=deg_to_rad(float(row.get("heavy_projectile_spread_degrees",0)))
		var dir:=aim.rotated((-0.5+i/float(maxi(1,count-1)))*spread if count>1 else 0.0)
		var at: Vector2=combat.game.actions.origin()+dir*22
		var shot: Dictionary={"at":at,"start":at,"motion":dir*speed,"damage":float(row.get(prefix+"projectile_damage",row.get("projectile_damage",row.damage))),"life":life,"max_life":life,"age":0.0,"kind":kind,"weapon":id,"base_weapon":row.get("base_weapon",id),"heavy":heavy,"radius":float(row.get(prefix+"projectile_radius",row.get("projectile_radius",8))),"gravity":float(row.get(prefix+"projectile_gravity",row.get("projectile_gravity",0))),"bounces":int(row.get(prefix+"projectile_reflections",row.get("projectile_reflections",row.get(prefix+"projectile_bounces",row.get("projectile_bounces",0))))),"hit_ids":[],"hit_rids":[],"max_hits":int(row.get(prefix+"max_targets",row.max_targets)),"delay":0.0,"phase":"outbound","row":row.duplicate(true)}
		if kind=="energy_arrow" and heavy:
			# Original rain starts upward, then falls in staggered parabolas.
			var t:=i/float(maxi(1,count-1))
			shot.motion=Vector2(signf(aim.x)*speed*(.52+.86*t),-(430+(i%4)*24+36*sin(t*PI)))
			shot.gravity=float(row.get("heavy_projectile_gravity",720))
			shot.delay=(i%6)*.032
		elif kind=="yoyo" and heavy: shot.phase="orbit"
		elif kind=="companion_drone":
			shot.life=4.0
			shot.max_life=4.0
			shot.target=nearest(at,float(row.get("drone_acquire_radius",280)))
		combat.projectiles.append(shot)
func nearest(at: Vector2,radius: float) -> CharacterBody2D:
	var best: CharacterBody2D=null
	var distance:=radius
	for actor in combat.actors.get_children():
		if actor.dead or actor.config.get("background_only",false): continue
		var d: float=at.distance_to(actor.position)
		if d<distance and combat.game.world.clear_sight(at,actor.position):
			best=actor
			distance=d
	return best
func explode(shot: Dictionary) -> void:
	var row: Dictionary=shot.row
	var prefix: String="heavy_" if shot.heavy else ""
	var radius:=float(row.get(prefix+"explosion_radius",row.get("explosion_radius",70)))
	var damage:=float(row.get(prefix+"explosion_damage",row.get("explosion_damage",shot.damage)))
	explosions.append({"at":shot.at,"radius":radius,"age":0.0,"life":.5})
	for actor in combat.actors.get_children():
		if not actor.dead and actor.position.distance_to(shot.at)<=radius+actor.body_size.x*.3 and combat.game.world.clear_sight(shot.at,actor.position):
			actor.take_hit(damage,signf(actor.position.x-shot.at.x)*float(row.knockback))
	if shot.kind=="tnt":
		var width:=int(row.get(prefix+"blast_width",1))
		var center:=Vector2i(Vector2(shot.at)/40)
		for x in range(center.x-floori(width/2.0),center.x+floori(width/2.0)+1):
			for y in range(center.y-floori(width/2.0),center.y+floori(width/2.0)+1):
				var cell:=Vector2i(x,y)
				if combat.game.tool_system.protected_cell(cell): continue
				var tile: Vector2i=combat.game.world.terrain.get_cell_atlas_coords(cell)
				if tile.x>0:
					combat.game.world.edit_cell(cell,0,0)
					combat.game.tool_system.collect_tile(tile.x,cell)
func advance(shot: Dictionary,delta: float) -> bool:
	if float(shot.get("delay",0))>0:
		shot.delay-=delta
		return false
	shot.age=float(shot.get("age",0))+delta
	if not shot.has("trail"): shot.trail=[]
	shot.trail.append(shot.at)
	if shot.trail.size()>14: shot.trail.pop_front()
	shot.life-=delta
	var kind:=str(shot.get("kind","energy_arrow"))
	var player: CharacterBody2D=combat.game.player
	var old: Vector2=shot.at
	var next: Vector2
	if kind=="yoyo":
		var row: Dictionary=shot.row
		if shot.phase=="orbit":
			var angle:=float(shot.age)/float(shot.max_life)*TAU*float(row.get("heavy_orbit_turns",2.6))
			next=player.position+Vector2.from_angle(angle)*float(row.get("heavy_orbit_radius",104))
		else:
			if old.distance_to(shot.start)>=float(row.get("projectile_range",168)): shot.phase="return"
			if shot.phase=="return":
				shot.motion=(player.position-old).normalized()*float(row.get("projectile_return_speed",620))
				if old.distance_to(player.position)<20: return true
			next=old+shot.motion*delta
	elif kind=="companion_drone":
		var target=shot.get("target")
		if is_instance_valid(target) and not target.dead and shot.phase!="return":
			shot.motion=(target.position-old).normalized()*float(shot.row.get("drone_dive_speed",430))
		else:
			shot.phase="return"
			shot.motion=(player.position-old).normalized()*float(shot.row.get("drone_return_speed",350))
			if old.distance_to(player.position)<20: return true
		next=old+shot.motion*delta
	else:
		shot.motion.y+=float(shot.get("gravity",0))*delta
		next=old+shot.motion*delta
	var query:=PhysicsRayQueryParameters2D.create(old,next,1|8|4)
	var excluded: Array[RID]=[]
	for rid in shot.get("hit_rids",[]): excluded.append(rid)
	query.exclude=excluded
	var hit:=combat.get_world_2d().direct_space_state.intersect_ray(query)
	var consumed:=false
	if not hit.is_empty():
		shot.at=hit.position
		var target=hit.collider
		if target.has_method("take_hit"):
			if not target.dead:
				target.take_hit(float(shot.damage),signf(shot.motion.x)*92)
			if not shot.has("hit_ids"): shot.hit_ids=[]
			if not shot.has("hit_rids"): shot.hit_rids=[]
			shot.hit_ids.append(target.get_instance_id())
			shot.hit_rids.append(target.get_rid())
			if kind=="companion_drone": shot.phase="return"
			consumed=not kind in ["laser","yoyo","battle_top","companion_drone"] or shot.hit_ids.size()>=int(shot.get("max_hits",1))
			if kind=="companion_drone" and not shot.heavy: consumed=false
			shot.at=hit.position+Vector2(shot.motion).normalized()*2
		else:
			if kind in ["laser","battle_top"] and int(shot.get("bounces",0))>0:
				shot.bounces-=1
				shot.motion=Vector2(shot.motion).bounce(hit.normal)*(1.0 if kind=="laser" else .78)
				shot.at=hit.position+hit.normal*2
			elif kind=="tnt":
				shot.motion=Vector2(shot.motion).bounce(hit.normal)*.3
				shot.at=hit.position+hit.normal*2
			elif kind in ["yoyo","companion_drone"]:
				shot.phase="return"
				shot.at=hit.position+hit.normal*3
			else: consumed=true
	else: shot.at=next
	consumed=consumed or shot.life<=0
	if consumed and kind in ["rocket","tnt"]: explode(shot)
	if consumed and kind=="companion_drone" and shot.get("heavy",false): explode(shot)
	return consumed
func update(delta: float) -> void:
	for i in range(explosions.size()-1,-1,-1):
		explosions[i].age+=delta
		if explosions[i].age>=explosions[i].life: explosions.remove_at(i)
func draw(canvas: Node2D, shot: Dictionary) -> void:
	if float(shot.get("delay",0))>0: return
	var kind:=str(shot.get("kind","energy_arrow"))
	var at: Vector2=shot.at
	var direction: Vector2=Vector2(shot.motion).normalized()
	var age:=float(shot.get("age",0))
	var id:=str(shot.get("weapon",""))
	preload("res://scripts/projectile_vfx.gd").trail(canvas,shot,combat.game.player.position)
	if id!="":
		var source:=preload("res://scripts/content_db.gd").asset("weapon."+id)
		var state: String="heavy_attack" if shot.get("heavy",false) else "normal_attack"
		var binding: Dictionary=source.get("combat_bindings",{}).get(state,{})
		if binding.get("render_mode","")=="authored":
			if VFX.draw_asset(canvas,str(binding.get("effect_asset","weapon."+id)),str(binding.get("effect_state","heavy_effect" if shot.get("heavy",false) else "normal_effect")),age,at,Vector2.ONE*40*float(binding.get("scale",1)),direction.angle()): return
	if kind=="companion_drone":
		VFX.draw_asset(canvas,"weapon."+id,"idle",age,at,Vector2(32,32),direction.angle())
	else: preload("res://scripts/projectile_vfx.gd").core(canvas,shot)

func draw_explosions(canvas: Node2D) -> void:
	for fx in explosions:
		var phase:=float(fx.age)/float(fx.life)
		# Original fallback explosion packet uses sixteen expanding pixel sparks.
		for i in range(16):
			var at: Vector2=fx.at+Vector2.from_angle(i*TAU/16)*(8+phase*float(fx.radius))
			VFX.quad(canvas,at,Vector2.ONE*5.5,Color(1,.2+.35*(i%3)/2.0,.04,maxf(.05,(1-phase)*.85)))
