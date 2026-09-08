extends SceneTree

const Model = preload("res://scripts/study_model.gd")
const Store = preload("res://scripts/progress_store.gd")
const Creature = preload("res://scripts/creature.gd")
var failures := 0

func _initialize() -> void:
	call_deferred("run")

func check(ok: bool, message: String) -> void:
	if not ok:
		failures += 1
		push_error(message)

func answer(model) -> String:
	var q: Dictionary = model.question
	return str(int(q.a)*int(q.b) if q.op=="×" else (int(q.a)+int(q.b) if q.op=="+" else int(q.a)-int(q.b)))

func run() -> void:
	var model := Model.new()
	model.persistence = false
	check(model.catalog.multiply.size()==45,"Original multiplication pool must have 45 canonical facts")
	var seen := {}
	for i in range(45):
		var q := model.draw_math("multiply")
		check(not seen.has(q.id),"Math bag repeated before exhaustion")
		seen[q.id] = true
	check(model.pools.multiply.is_empty(),"Math bag should exhaust")
	model.start_round("entry")
	check(not model.start_round("death"),"Active round must not be replaced by death")
	var before := model.token
	model.submit_math("999",before)
	check(model.passed[0]==0 and model.token==before,"Wrong math answer must keep the same question")
	model.submit_math(answer(model),before-1)
	check(model.passed[0]==0,"Stale token must not award progress")
	while model.stage<2:
		model.submit_math(answer(model),model.token)
	check(model.tasks[2].pool.begins_with("single") and model.tasks[3].pool.begins_with("mixed"),"Original single/mixed arithmetic order")
	check(model.tasks[2].op != model.tasks[3].op,"Arithmetic pair must have opposite operators")
	var first_id: String = model.question.id
	var initial_token := model.token
	for i in range(4):
		if model.question.choices[i] != model.question.answer:
			model.choose(i,initial_token)
	check(model.question.id != first_id and model.passed[2]==0,"Third distinct wrong Zhuyin choice replaces question without credit")
	model.path = "user://study_test_only.json"
	model.persistence = true
	model.checkpoint()
	var restored := Model.new()
	restored.path = model.path
	restored.restore()
	check(restored.question.id==model.question.id and restored.active,"Pending study question must survive restart")
	check(restored.zhuyin_deck==model.zhuyin_deck and restored.pools==model.pools,"Question bags must survive restart")
	restored.persistence = false
	while restored.stage<3:
		restored.choose(restored.question.choices.find(restored.question.answer),restored.token)
	check(restored.active,"Completing questions must still wait for Continue")
	check(restored.continue_game() and not restored.active,"Continue should unlock gameplay")
	DirAccess.remove_absolute(model.path)
	DirAccess.remove_absolute(model.path+".bak")
	print("STUDY_OK finite bags, answer checking, stale tokens, replacement, persistence, continue gate")
	var game = load("res://scenes/main.tscn").instantiate()
	game.test_mode = true
	root.add_child(game)
	await process_frame
	for id in game.maps:
		game.change_map(id)
		var population: Array = JSON.parse_string(FileAccess.get_file_as_string("res://data/populations/%s.json" % id))
		check(game.gameplay.actors.get_child_count()==population.size(),"Population mismatch "+id)
		for actor in game.gameplay.actors.get_children():
			check(actor.sprite.sprite_frames.get_frame_count(actor.sprite.animation)>0,"Missing actor animation "+str(actor.placement.species))
		await physics_frame
	game.change_map("editor_map")
	check(game.scenery.custom_count==53,"Main world custom artwork missing")
	check(game.world.is_ladder(Vector2(256*40+20,158*40+20)),"Custom ladder role missing")
	for x in range(5,18):
		game.world.terrain.set_cell(Vector2i(x,10),0,Vector2i(3,7))
	game.world.terrain.update_internals()
	game.player.position = Vector2(420,370)
	game.player.hp = 100
	var actor := Creature.new()
	actor.config = game.gameplay.definitions.wolf
	actor.placement = {"species":"wolf","entity_id":"test_wolf","x":11.6,"y":10,"patrol":3,"respawn":true,"respawn_seconds":.1}
	actor.world = game.world
	actor.target = game.player
	actor.simulation_enabled = false
	actor.defeated.connect(game.gameplay.on_defeated)
	game.gameplay.actors.add_child(actor)
	actor.position = Vector2(464,370)
	await physics_frame
	game.gameplay.attack()
	check(is_equal_approx(actor.hp,74),"Sword must wait for authored action frame")
	game.gameplay.update_effects(.28)
	check(is_equal_approx(actor.hp,40),"Original sword should deal 34 damage")
	game.gameplay.attack()
	check(is_equal_approx(actor.hp,40),"Attack cooldown failed")
	game.gameplay.attack_clock = 0
	game.gameplay.attack()
	game.gameplay.update_effects(.28)
	game.gameplay.attack_clock = 0
	game.gameplay.attack()
	game.gameplay.update_effects(.28)
	check(actor.dead and not game.gameplay.drops.is_empty(),"Death should create authored loot")
	actor.simulation_enabled = true
	for i in range(12):
		await physics_frame
	check(not actor.dead and actor.hp==actor.max_hp,"Respawn failed")
	actor.position = game.player.position+Vector2(25,0)
	actor.attack_cooldown = 0
	game.player.invulnerable = 0
	for i in range(3):
		await physics_frame
	check(game.player.hp<100,"Hostile actor must damage player")
	actor.simulation_enabled = false
	var chest: Dictionary = {}
	for row in game.world.map_data.metadata.editor_objects:
		if row.get("kind")=="chest":
			chest = row
			break
	game.player.position = game.gameplay.chest_position(chest)
	game.gameplay.interact()
	check(game.gameplay.opened.has(str(chest.id)),"Chest didn't open")
	var item: String = chest.loot[0].id
	var count: int = game.gameplay.inventory[item].count
	game.gameplay.interact()
	check(int(game.gameplay.inventory[item].count)==count,"Chest loot duplicated")
	game.change_map("crystal_mine")
	game.change_map("editor_map")
	check(game.gameplay.opened.has(str(chest.id)),"World transition lost chest state")
	game.gameplay.capture_map()
	var save_path := "user://gameplay_test_only.json"
	check(Store.save_data(save_path,{"schema":2,"inventory":game.gameplay.inventory,"sessions":game.gameplay.sessions})==OK,"Progress write failed")
	var loaded := Store.load_data(save_path,2)
	check(loaded.sessions.editor_map.opened.has(str(chest.id)),"Disk save lost chest state")
	check(int(loaded.inventory[item].count)==count,"Disk save lost inventory")
	DirAccess.remove_absolute(save_path)
	DirAccess.remove_absolute(save_path+".bak")
	var portal: Dictionary = game.world.map_data.metadata.portals[0]
	game.player.position = game.gameplay.portal_rect(portal).get_center()
	game.gameplay.interact()
	check(game.world.map_id==str(portal.target_map).get_basename(),"Portal destination mismatch")
	game.study.model.persistence = false
	game.study.open_round("manual")
	var fixed_position: Vector2 = game.player.position
	Input.action_press("move_right")
	for i in range(10):
		await process_frame
	check(game.player.position==fixed_position and paused,"Quiz must pause game simulation")
	game.release_inputs()
	paused = false
	game.study.overlay.hide()
	print("GAMEPLAY_OK populations, custom ladders, combat, cooldown, loot, respawn, chests, portals, pause")
	print("PHASE2_OK" if failures==0 else "PHASE2_FAILED %d" % failures)
	quit(0 if failures==0 else 1)
