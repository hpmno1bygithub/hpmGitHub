extends Node2D

const Creature = preload("res://scripts/creature.gd")
const Art = preload("res://scripts/art.gd")
var projectiles_system := preload("res://scripts/weapon_projectiles.gd").new()
var game: Node2D
var actors := Node2D.new()
var definitions: Dictionary
var weapons: Dictionary
var bag := preload("res://scripts/inventory_model.gd").new()
var inventory: Dictionary:
	get:
		return bag.aggregate()
	set(value):
		bag.import_legacy(value)
var sessions: Dictionary = {}
var opened: Dictionary = {}
var drops: Array = []
var attack_clock := 0.0
var interaction_hint := ""
var current_map := ""

func _ready() -> void:
	projectiles_system.combat=self
	definitions = JSON.parse_string(FileAccess.get_file_as_string("res://data/creatures.json"))
	weapons = JSON.parse_string(FileAccess.get_file_as_string("res://data/weapons.json"))
	definitions = preload("res://scripts/content_db.gd").resolve("creatures",definitions)
	weapons = preload("res://scripts/content_db.gd").resolve("weapons",weapons)
	bag.add("weapon_sword","鐵劍",1)
	add_child(actors)

func capture_map() -> void:
	if current_map.is_empty() or current_map!=game.world.map_id:
		return
	var states := {}
	for actor in actors.get_children():
		states[str(actor.placement.entity_id)] = actor.snapshot()
	sessions[current_map] = {"actors":states,"opened":opened.duplicate(),"drops":drops.duplicate(true),"terrain":game.world.snapshot_edits(),"liquids":game.liquids.snapshot(),"removed_plants":game.scenery.removed.duplicate(),"environment":game.environment.snapshot()}

func load_map(restore_state: bool = true) -> void:
	projectiles.clear()
	projectiles_system.explosions.clear()
	enemy_shots.clear()
	pending_attack.clear()
	sparks.clear()
	for actor in actors.get_children():
		actors.remove_child(actor)
		actor.queue_free()
	current_map = game.world.map_id
	var state: Dictionary = sessions.get(current_map,{})
	if restore_state:
		game.world.restore_edits(state.get("terrain",[]))
	opened = state.get("opened",{}).duplicate()
	drops = state.get("drops",[]).duplicate(true)
	var population: Array = JSON.parse_string(FileAccess.get_file_as_string("res://data/populations/%s.json" % current_map))
	population = game.world.map_data.get("population",population)
	for row in population:
		var actor := Creature.new()
		actor.placement = row
		actor.config = definitions[row.species]
		actor.world = game.world
		actor.target = game.player
		actor.simulation_enabled = not game.test_mode
		actor.defeated.connect(on_defeated)
		actors.add_child(actor)
		if state.get("actors",{}).has(str(row.entity_id)):
			actor.restore(state.actors[str(row.entity_id)])
	queue_redraw()

const SUPPORTED = ["sword","dagger","spear","battle_axe","war_hammer","greatsword","whip","energy_bow","laser_gun","yoyo","battle_top","rpg_launcher","tnt","flying_drone","magma_core_hammer","nine_tail_fan","abyss_claw","thunder_longbow","earth_drill_lance","world_tree_staff"]
var projectiles: Array = []
var sparks: Array = []
var pending_attack: Dictionary = {}
var enemy_shots: Array = []

func equip(id: String) -> bool:
	if not pending_attack.is_empty():
		return false
	if not weapons.has(id) or not str(weapons[id].get("base_weapon",id)) in SUPPORTED:
		game.status.text = "這把武器的特殊機制尚在移植中。"
		return false
	if not inventory.has(str(weapons[id].item_id)):
		game.status.text = "尚未持有這把武器；可從寶箱取得，或在背包領取測試武器。"
		return false
	game.actions.cancel()
	game.player.equipped = id
	game.player.charge = 0
	return true

func attack() -> void:
	if attack_clock>0 or game.player.hp<=0 or game.player.roll_left>0 or not pending_attack.is_empty():
		return
	var player: CharacterBody2D = game.player
	var weapon: Dictionary = weapons[player.equipped]
	var heavy: bool = player.heavy_attack
	if not inventory.has(str(weapon.item_id)):
		game.status.text = "目前武器已不在背包，請重新裝備。"
		return
	if heavy:
		var result := bag.use_heavy(str(weapon.item_id))
		if not result.accepted: return
		if result.has("broken") and int(weapon.get("heavy_consume_count",0))<=0:
			# Persistent recovery drop retains this physical copy's full restored budget.
			var recovery: Dictionary = result.broken
			recovery.merge({"x":game.world.spawn_point().x,"y":game.world.spawn_point().y,"pickup_delay":1.0})
			drops.append(recovery)
			game.status.text="重擊耐久耗盡：武器已送回出生點回收。"
	var state := "heavy_attack" if heavy else "normal_attack"
	var source := preload("res://scripts/content_db.gd").asset("weapon."+player.equipped)
	var info: Dictionary = source.get("animations",{}).get(state,{})
	var duration := float(info.get("frames",[]).size())/maxf(1,float(info.get("fps",10)))
	if duration<=0: duration=float(weapon.get("heavy_attack_duration",weapon.attack_duration) if heavy else weapon.attack_duration)
	var trigger := float(info.get("events",{}).get("action",0))/maxf(1,info.get("frames",[]).size()-1)
	attack_clock = maxf(duration,float(weapon.get("heavy_cooldown",weapon.cooldown) if heavy else weapon.cooldown))
	player.swing_left = duration
	player.swing_duration = duration
	pending_attack = {"weapon":weapon.duplicate(true),"heavy":heavy,"time":0.0,"trigger":duration*trigger,"aim":game.actions.direction()}
	if trigger<=0:
		resolve_attack(weapon,heavy,game.actions.direction())
		pending_attack.clear()

func resolve_attack(weapon: Dictionary, heavy: bool, aim:=Vector2.ZERO) -> void:
	var player: CharacterBody2D = game.player
	if aim==Vector2.ZERO: aim=game.actions.direction()
	var base_weapon := str(weapon.get("base_weapon",player.equipped))
	var reach := float(weapon.get("heavy_reach_tiles",weapon.reach_tiles) if heavy else weapon.reach_tiles)*40
	player.weapon_reach = maxf(30,reach)
	if weapon.get("delivery","")=="boss_fix103":
		game.boss_combat.start(player,base_weapon,heavy,float(weapon.damage)*(.72 if heavy else 1.0))
		return
	if str(weapon.get("delivery","melee"))!="melee":
		projectiles_system.spawn(weapon,heavy,aim,player.equipped)
		return
	var hitbox := Rect2(player.position+Vector2(11.0 if player.facing>0 else -11-reach,-24),Vector2(reach,48))
	if heavy and base_weapon=="whip":
		hitbox = Rect2(player.position-Vector2(reach,24),Vector2(reach*2,48))
	var candidates: Array = []
	for actor in actors.get_children():
		if not actor.dead and hitbox.intersects(Rect2(actor.position-actor.body_size*.5,actor.body_size)) and game.world.clear_sight(player.position,actor.position):
			candidates.append(actor)
	candidates.sort_custom(func(a,b): return a.position.distance_squared_to(player.position)<b.position.distance_squared_to(player.position))
	var maximum := int(weapon.get("heavy_max_targets",weapon.max_targets) if heavy else weapon.max_targets)
	for i in range(mini(maximum,candidates.size())):
		var actor = candidates[i]
		actor.take_hit(float(weapon.damage)*(float(weapon.heavy_damage_mult) if heavy else 1.0),player.facing*float(weapon.knockback)*(float(weapon.heavy_knockback_mult) if heavy else 1.0))
		sparks.append({"at":actor.position,"life":.3})
	if heavy and base_weapon=="greatsword":
		spawn_projectile(player.position+Vector2(player.facing*30,0),Vector2(player.facing*float(weapon.heavy_projectile_speed),0),float(weapon.heavy_projectile_damage),float(weapon.heavy_projectile_life))
		projectiles.back().merge({"kind":"greatsword_crescent","radius":float(weapon.heavy_projectile_radius),"max_life":float(weapon.heavy_projectile_life),"visual_scale":float(weapon.heavy_projectile_visual_scale)})

func spawn_projectile(at: Vector2, motion: Vector2, damage: float, life: float) -> void:
	projectiles.append({"at":at,"motion":motion,"damage":damage,"life":life})

func update_effects(delta: float) -> void:
	for i in range(enemy_shots.size()-1,-1,-1):
		var shot: Dictionary=enemy_shots[i]
		var old: Vector2=shot.at
		var next: Vector2=old+shot.motion*delta
		shot.life-=delta
		shot.age=float(shot.get("age",0))+delta
		if shot.get("element","")=="fire_column":
			var box:=Rect2(old-Vector2(float(shot.width),float(shot.height))*.5,Vector2(float(shot.width),float(shot.height)))
			if box.intersects(Rect2(game.player.position-Vector2(11,20),Vector2(22,40))) and game.player.roll_left<=0:
				game.player.take_damage(float(shot.damage),signf(game.player.position.x-old.x))
				shot.life=0
			if shot.life<=0: enemy_shots.remove_at(i)
			continue
		var query:=PhysicsRayQueryParameters2D.create(old,next,1|8|2)
		if game.player.roll_left>0: query.exclude=[game.player.get_rid()]
		var collision:=get_world_2d().direct_space_state.intersect_ray(query)
		if not collision.is_empty():
			shot.at=collision.position
			if collision.collider==game.player:
				game.player.take_damage(float(shot.damage),signf(shot.motion.x))
			shot.life=0
		else: shot.at=next
		if shot.life<=0: enemy_shots.remove_at(i)
	if not pending_attack.is_empty():
		pending_attack.time += delta
		if float(pending_attack.time)>=float(pending_attack.trigger):
			if game.player.hp>0:
				resolve_attack(pending_attack.weapon,bool(pending_attack.heavy),pending_attack.aim)
			pending_attack.clear()
	projectiles_system.update(delta)
	for i in range(projectiles.size()-1,-1,-1):
		if projectiles_system.advance(projectiles[i],delta):
			sparks.append({"at":projectiles[i].at,"life":.3})
			projectiles.remove_at(i)
	for i in range(sparks.size()-1,-1,-1):
		sparks[i].life -= delta
		if sparks[i].life<=0:
			sparks.remove_at(i)
	queue_redraw()

func on_defeated(actor) -> void:
	var reward: String=str(game.boss_combat.rules.rewards.get(str(actor.placement.species),""))
	if reward!="":
		var row: Dictionary=weapons[reward]
		drops.append({"id":row.item_id,"name":row.name,"count":1,"x":actor.position.x,"y":actor.position.y})
	for loot in actor.config.get("loot",[]):
		drops.append({"id":str(loot[0]),"name":str(loot[1]),"count":int(loot[2]),"x":actor.position.x,"y":actor.position.y})
	game.status.text = "擊敗 "+str(actor.config.get("name",actor.placement.species))
	game.save_game()

func give_item(id: String, label: String, count: int, uses: Array = []) -> int:
	var added := bag.add(id,label,count,uses)
	game.status.text = "獲得 %s × %d" % [label,added] if added>0 else "背包已滿"
	return added

func _physics_process(delta: float) -> void:
	attack_clock = maxf(0,attack_clock-delta)
	update_effects(delta)
	if game.test_mode:
		return
	for i in range(drops.size()-1,-1,-1):
		var drop: Dictionary = drops[i]
		drop.pickup_delay = maxf(0,float(drop.get("pickup_delay",0))-delta)
		if float(drop.pickup_delay)<=0 and game.player.position.distance_to(Vector2(float(drop.x),float(drop.y)))<44:
			var accepted := give_item(str(drop.id),str(drop.name),int(drop.count),drop.get("uses",[]))
			drop.count -= accepted
			drop.uses = drop.get("uses",[]).slice(accepted)
			if int(drop.count)<=0:
				drops.remove_at(i)
			game.save_game()
	interaction_hint = ""
	for row in game.world.map_data.metadata.get("editor_objects",[]):
		if row.get("kind") == "chest" and row.get("enabled",true) and not opened.has(str(row.id)):
			if chest_position(row).distance_to(game.player.position)<95:
				interaction_hint = "E／互動：開啟 "+str(row.get("name","寶箱"))
	for portal in game.world.map_data.metadata.get("portals",[]):
		if portal_rect(portal).grow(35).has_point(game.player.position):
			interaction_hint = "E／互動：前往 "+str(portal.target_map).get_basename()
	queue_redraw()

func interact() -> void:
	for row in game.world.map_data.metadata.get("editor_objects",[]):
		if row.get("kind") != "chest" or not row.get("enabled",true) or opened.has(str(row.id)):
			continue
		if chest_position(row).distance_to(game.player.position)<95:
			opened[str(row.id)] = true
			for item in row.get("loot",[]):
				var accepted := give_item(str(item.id),str(item.get("name",item.id)),int(item.get("count",1)))
				if accepted<int(item.get("count",1)):
					drops.append({"id":item.id,"name":item.get("name",item.id),"count":int(item.get("count",1))-accepted,"x":chest_position(row).x,"y":chest_position(row).y})
			game.save_game()
			return
	for portal in game.world.map_data.metadata.get("portals",[]):
		if not portal_rect(portal).grow(35).has_point(game.player.position):
			continue
		var destination := str(portal.target_map).get_file().get_basename()
		if not game.maps.has(destination):
			return
		var target_id := str(portal.get("target_portal",""))
		game.change_map(destination)
		for other in game.world.map_data.metadata.get("portals",[]):
			if str(other.id)==target_id:
				var arrival: Array = other.get("arrival",game.world.map_data.spawn)
				game.player.position = Vector2((float(arrival[0])+.5)*40,float(arrival[1])*40-21)
		game.save_game()
		return

func portal_rect(portal: Dictionary) -> Rect2:
	var r: Array = portal.rect
	return Rect2(float(r[0])*40,float(r[1])*40,float(r[2])*40,float(r[3])*40)

func chest_position(row: Dictionary) -> Vector2:
	var feet: Array = row.get("preview_feet",[float(row.x)+.5,float(row.y)+1])
	return Vector2(float(feet[0])*40,float(feet[1])*40-15)

func _draw() -> void:
	for shot in enemy_shots:
		preload("res://scripts/source_vfx.gd").bolt(self,shot)
	for shot in projectiles:
		projectiles_system.draw(self,shot)
	projectiles_system.draw_explosions(self)
	for spark in sparks:
		var radius := (1-float(spark.life)/.3)*22
		for i in range(8):
			var direction := Vector2.from_angle(i*TAU/8)
			draw_line(spark.at+direction*radius*.4,spark.at+direction*radius,Color(1,.8,.35,float(spark.life)/.3),2)
	if game == null or game.world.map_data.is_empty():
		return
	for portal in game.world.map_data.metadata.get("portals",[]):
		var r := portal_rect(portal)
		draw_rect(r,Color(.4,.6,.9,.18))
		draw_rect(r.grow(-5),Color(.5,.8,1,.8),false,3)
	for row in game.world.map_data.metadata.get("editor_objects",[]):
		if row.get("kind") == "chest" and row.get("enabled",true):
			var pos := chest_position(row)
			var is_open := opened.has(str(row.id))
			draw_rect(Rect2(pos-Vector2(19,14),Vector2(38,28)),Color("#605544") if is_open else Color("#b38546"))
			draw_rect(Rect2(pos-Vector2(19,3),Vector2(38,5)),Color("#e6c77f"))
	for drop in drops:
		draw_circle(Vector2(float(drop.x),float(drop.y)),7,Color("#eccc73"))
