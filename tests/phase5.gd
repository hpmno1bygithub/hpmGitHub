extends SceneTree
const DB=preload("res://scripts/content_db.gd")
var game
var failures:=0
func _initialize() -> void: call_deferred("run")
func check(ok: bool,label: String) -> void:
	if not ok:
		failures+=1
		push_error(label)
func frames(n: int) -> void:
	for i in range(n): await physics_frame
func tap(action: String) -> void:
	Input.action_press(action)
	await frames(2)
	Input.action_release(action)
	await frames(2)
func clear_shots() -> void:
	game.gameplay.pending_attack.clear()
	game.gameplay.projectiles.clear()
	game.gameplay.attack_clock=0
	game.player.swing_left=0
	game.magic.clear()
func run() -> void:
	DB.path="user://phase5_content_test.json"
	DB.pack={"schema":1,"maps":{},"assets":{},"creatures":{},"weapons":{},"actions":{},"custom":{}}
	game=load("res://scenes/main.tscn").instantiate()
	game.test_mode=true
	root.add_child(game)
	game.progress_path="user://phase5_isolated_save.json"
	game.study.model.persistence=false
	game.change_map("control_lab")
	game.gameplay.give_item("tool_grapple","鉤爪",1)
	for actor in game.gameplay.actors.get_children(): actor.simulation_enabled=false
	await frames(12)
	for action in ["mode_weapon","mode_magic","mode_tool","mode_cycle","tool_cycle","magic_cycle","weapon_cycle","aim_left","aim_right","aim_up","aim_down"]:
		check(not InputMap.action_get_events(action).is_empty(),"Mapped keyboard action "+action)
	await tap("mode_tool")
	check(game.actions.mode=="tool","3 selects tool mode")
	await tap("tool_cycle")
	check(game.tool_system.selected=="axe","T selects axe")
	await tap("tool_cycle")
	check(game.tool_system.selected=="grapple","T selects grapple")
	await tap("mode_magic")
	await tap("magic_cycle")
	check(game.magic.selected=="waterball" and game.actions.mode=="magic","2 / R select magic")
	await tap("mode_weapon")
	await tap("crouch")
	await frames(35)
	await tap("move_right")
	Input.action_press("move_right")
	await frames(20)
	Input.action_release("move_right")
	for i in range(40):
		await frames(1)
		check(game.player.posture=="crouch","Stopped crouch walk retains posture")
		if game.player.sprite.animation=="crouch": check(game.player.sprite.frame==1,"No standing transition frame after crouch run")
	await tap("climb")
	check(game.player.posture=="stand","Up explicitly stands")
	game.actions.select_mode("tool")
	game.player.position=Vector2(420,779)
	game.player.velocity=Vector2.ZERO
	game.actions.set_direction(Vector2.RIGHT)
	game.tool_system.selected="axe"
	game.tool_system.clock=0
	game.tool_system.use_selected()
	check(game.scenery.removed.has("vegetation:12:20"),"Aimed axe chops original tree layer")
	check(game.gameplay.drops.any(func(d): return d.id=="wood"),"Chopped tree emits source wood drop")
	game.world.edit_cell(Vector2i(12,19),1,7)
	await frames(2)
	game.tool_system.selected="pickaxe"
	for i in range(3):
		game.tool_system.clock=0
		game.tool_system.mine()
	check(game.world.terrain.get_cell_source_id(Vector2i(12,19))<0,"Horizontal pickaxe digs complete tile")
	check(game.gameplay.drops.any(func(d): return d.id=="soil"),"Mining uses source soil item ID")
	game.world.edit_cell(Vector2i(12,19),3,7)
	game.tool_system.clock=0
	game.tool_system.mine()
	check(game.world.terrain.get_cell_atlas_coords(Vector2i(12,19)).y==7,"Stone hardness needs two hits")
	game.tool_system.clock=0
	game.tool_system.mine()
	check(game.world.terrain.get_cell_source_id(Vector2i(12,19))<0,"Second horizontal stone hit removes tile")
	game.world.edit_cell(Vector2i(12,19),0,0)
	game.world.edit_cell(Vector2i(16,17),3,7)
	await frames(2)
	game.actions.set_direction(Vector2(1,-.4))
	game.tool_system.grapple()
	check(game.tool_system.hook_state=="extending","Grapple begins visible flight")
	await frames(30)
	check(game.tool_system.tethered,"Aimed flying hook latches terrain")
	game.actions.select_mode("magic")
	check(game.tool_system.hook_state=="idle","Mode change detaches hook")
	game.world.edit_cell(Vector2i(16,17),0,0)
	game.player.position=Vector2(420,779)
	game.player.velocity=Vector2.ZERO
	game.actions.select_mode("weapon")
	game.gameplay.give_item("weapon_energy_bow","能量弓",1)
	game.gameplay.equip("energy_bow")
	game.actions.set_direction(Vector2(1,-1))
	game.player.heavy_attack=false
	clear_shots()
	game.gameplay.attack()
	game.actions.set_direction(Vector2.LEFT)
	game.gameplay.update_effects(.4)
	check(game.gameplay.projectiles.size()==1,"Normal arrow produced")
	if game.gameplay.projectiles.size()>0:
		check(game.gameplay.projectiles[0].motion.x>0 and game.gameplay.projectiles[0].motion.y<0,"Release aim captured; arrow can shoot diagonally up")
	clear_shots()
	game.actions.select_mode("magic")
	Input.action_press("attack")
	await frames(12)
	game.actions.select_mode("weapon")
	Input.action_release("attack")
	await frames(3)
	check(game.gameplay.projectiles.is_empty() and game.magic.shots.is_empty(),"Changing mode cancels old charge without firing on release")
	check(game.magic.level_for_charge(.49)==1 and game.magic.level_for_charge(.5)==2 and game.magic.level_for_charge(1.0)==3,"Source magic charge levels")
	for kind in game.magic.ORDER:
		game.magic.selected=kind
		game.magic.mana=100
		game.magic.clock=0
		check(game.magic.cast(.1,Vector2(1,-.3)),"Cast "+kind)
		check(game.magic.shots.back().motion.y<0,"Magic respects aim "+kind)
		check(game.magic.mana<100,"Magic spends mana "+kind)
		game.magic.clear()
	game.magic.mana=0
	check(not game.magic.cast(1.2,Vector2.RIGHT),"Insufficient mana cannot create projectiles")
	var wolf=game.gameplay.actors.get_child(0)
	wolf.position=Vector2(590,765)
	wolf.max_hp=500
	wolf.hp=500
	game.magic.selected="iceball"
	game.magic.mana=100
	game.magic.clock=0
	game.magic.cast(.1,Vector2.RIGHT)
	await frames(32)
	check(wolf.hp<500 and wolf.slow_left>0,"Ice collision deals damage and slows creature")
	game.magic.clear()
	var water_cell:=Vector2i(28,18)
	game.liquids.enabled=false
	game.liquids.pools.water[water_cell]=.5
	game.environment.element_impact("iceball",water_cell,1,1)
	check(game.environment.frozen.has(water_cell) and not game.liquids.pools.water.has(water_cell),"Ice magic freezes local water")
	game.environment.element_impact("fireball",water_cell,1,1)
	check(is_equal_approx(float(game.liquids.pools.water.get(water_cell,0)),.5),"Fire thaws frozen volume without creating water")
	for id in ["laser_gun","yoyo","battle_top","rpg_launcher","tnt","flying_drone"]:
		clear_shots()
		game.gameplay.give_item("weapon_"+id,id,1)
		check(game.gameplay.equip(id),"Equip newly supported "+id)
		game.gameplay.resolve_attack(game.gameplay.weapons[id],false,Vector2(1,-.4))
		check(game.gameplay.projectiles.size()==1,"Delivery for "+id)
		for i in range(240): game.gameplay.update_effects(1.0/60)
		check(game.gameplay.projectiles.is_empty(),"Bounded projectile expiry / return "+id)
	clear_shots()
	var wall:=StaticBody2D.new()
	wall.collision_layer=1
	var shape:=CollisionShape2D.new()
	shape.shape=RectangleShape2D.new()
	shape.shape.size=Vector2(4,180)
	wall.position=Vector2(560,720)
	wall.add_child(shape)
	game.add_child(wall)
	await frames(2)
	game.gameplay.equip("laser_gun")
	game.gameplay.resolve_attack(game.gameplay.weapons.laser_gun,false,Vector2.RIGHT)
	for i in range(20): game.gameplay.update_effects(.02)
	check(game.gameplay.projectiles.size()==1 and game.gameplay.projectiles[0].motion.x<0,"Laser reflects from thin terrain collider")
	wall.queue_free()
	clear_shots()
	var before: int=game.boss_combat.pulses_fired
	for id in game.boss_combat.rules.patterns:
		game.boss_combat.start(game.player,id,true,1)
	game.boss_combat.update(3.2)
	check(game.boss_combat.effects.is_empty(),"All six native boss timelines finish")
	check(game.boss_combat.pulses_fired-before==36,"All original heavy beats survive a long update exactly once")
	before=game.boss_combat.pulses_fired
	game.boss_combat.update(.5)
	check(game.boss_combat.pulses_fired==before,"Finished boss pulses never duplicate")
	var boss=game.gameplay.actors.get_child(1)
	game.boss_combat.creature_effect(boss,{"effect":"fix103_lava_heavy","damage":12},game.player.position)
	check(game.boss_combat.effects.size()==1,"Boss uses same native choreography as reward weapon")
	game.boss_combat.authored(boss,"creature.lava_beetle_emperor","special")
	check(game.boss_combat.visuals.any(func(v): return v.asset=="effect.lava_beetle_emperor_special"),"Monster uses original bound effect asset")
	game.boss_combat.creature_effect(boss,{"effect":"triple_breath","damage":1},game.player.position)
	check(game.gameplay.enemy_shots.size()==9,"Three-head breath emits nine original elemental bolts")
	game.gameplay.capture_map()
	game.change_map("wuxia_world")
	game.change_map("control_lab")
	check(game.scenery.removed.has("vegetation:12:20"),"Chopped tree stays removed across maps")
	# An authored damage marker must change the native boss timeline through the same content pack.
	game.asset_editor.open()
	game.asset_editor.select_asset("effect.magma_core_hammer_heavy")
	game.asset_editor.frame=0
	game.asset_editor.show_frame()
	game.asset_editor.damage_marker.button_pressed=true
	game.asset_editor.save_asset()
	check(DB.asset("effect.magma_core_hammer_heavy").animations.effect.frame_events["0"].damage,"Editor stores frame damage marker")
	game.asset_editor.request_close()
	game.boss_combat.effects.clear()
	game.boss_combat.start(game.player,"magma_core_hammer",true,1)
	check(game.boss_combat.effects[0].beats[0]==0,"Edited effect marker drives first authoritative damage beat")
	# Touch stick retains direction and a second finger cannot steal its owner.
	var touch:=InputEventScreenTouch.new()
	touch.index=7
	touch.position=game.aim_pad.get_global_transform_with_canvas()*(game.aim_pad.size*Vector2(.875,.22))
	touch.pressed=true
	game.aim_pad._input(touch)
	check(game.aim_pad.finger==7 and game.actions.direction().y<0,"Touch aim pad supports upward diagonal")
	touch.index=8
	game.aim_pad._input(touch)
	check(game.aim_pad.finger==7,"Second finger does not steal active aiming")
	touch.index=7
	touch.pressed=false
	game.aim_pad._input(touch)
	check(game.aim_pad.finger==-1 and game.actions.direction().y<0,"Releasing aim stick preserves direction")
	game.free()
	await process_frame
	if failures==0: print("PHASE5_OK input modes, crouch locomotion, source tools, hook flight, release aim, four spells, native weapon delivery, six boss timelines, persistence")
	quit(1 if failures else 0)
