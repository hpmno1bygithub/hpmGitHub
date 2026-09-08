extends SceneTree
var failures := 0
var game
func _initialize() -> void:
	call_deferred("run")
func check(ok: bool, message: String) -> void:
	if not ok:
		failures += 1
		push_error(message)
func frames(count: int) -> void:
	for i in range(count):
		await physics_frame
func run() -> void:
	game = load("res://scenes/main.tscn").instantiate()
	game.test_mode = true
	root.add_child(game)
	game.study.model.persistence = false
	game.world.terrain.clear()
	game.scenery.ladder_cells.clear()
	for x in range(5,25):
		game.world.terrain.set_cell(Vector2i(x,14),0,Vector2i(2,7))
	for y in range(10,14):
		game.scenery.ladder_cells[Vector2i(10,y)] = true
	for x in range(9,13):
		game.world.terrain.set_cell(Vector2i(x,10),0,Vector2i(22,4))
	game.world.terrain.update_internals()
	game.player.position = Vector2(420,539)
	await frames(10)
	Input.action_press("climb")
	await frames(100)
	Input.action_release("climb")
	await frames(15)
	check(game.player.position.y<382,"Ladder must clear platform top with feet")
	Input.action_press("move_right")
	await frames(15)
	Input.action_release("move_right")
	await frames(15)
	check(game.player.is_on_floor(),"Must stand on platform after leaving ladder")
	Input.action_press("down")
	await frames(25)
	Input.action_release("down")
	check(game.player.position.y>410,"Down must pass native one-way platform")
	game.player.position = Vector2(420,379)
	game.player.velocity = Vector2.ZERO
	Input.action_press("down")
	await frames(20)
	Input.action_release("down")
	check(game.player.position.y>395,"Down must enter ladder from platform above")
	game.scenery.ladder_cells.clear()
	for y in range(10,14):
		game.world.terrain.set_cell(Vector2i(10,y),0,Vector2i(4,7))
	game.world.terrain.set_cell(Vector2i(9,10),0,Vector2i(2,7))
	game.world.terrain.set_cell(Vector2i(11,10),0,Vector2i(2,7))
	game.world.terrain.update_internals()
	game.player.position = Vector2(433,539)
	game.player.velocity = Vector2.ZERO
	Input.action_press("climb")
	await frames(100)
	Input.action_release("climb")
	check(game.player.position.y<382,"Off-center native ladder must clear adjacent solid ledges")
	game.player.climbing = false
	game.player.position = Vector2(720,539)
	await frames(15)
	Input.action_press("move_right")
	await frames(12)
	check(is_equal_approx(game.player.velocity.x,235),"Default movement must reach original run speed")
	Input.action_release("move_right")
	await frames(12)
	check(absf(game.player.velocity.x)<1,"Movement must decelerate to rest")
	check(game.player.set_posture("prone"),"Prone transition")
	check(game.player.is_hidden(),"Prone must hide player")
	check(game.player.body_shape.shape.size==Vector2(46,22),"Prone collider")
	Input.action_press("climb")
	await frames(2)
	Input.action_release("climb")
	check(game.player.posture=="stand","Up must restore stand")
	Input.action_press("roll")
	await frames(5)
	Input.action_release("roll")
	check(game.player.posture=="roll" and absf(game.player.velocity.x)==355,"Roll speed and collider")
	await frames(25)
	check(game.player.posture=="stand","Roll must restore previous posture")
	game.player.position = Vector2(720,539)
	game.player.velocity = Vector2.ZERO
	await frames(5)
	game.player.set_posture("prone")
	var roof := StaticBody2D.new()
	roof.collision_layer = 1
	var shape := CollisionShape2D.new()
	shape.shape = RectangleShape2D.new()
	shape.shape.size = Vector2(100,10)
	roof.position = Vector2(720,525)
	roof.add_child(shape)
	game.add_child(roof)
	await frames(2)
	check(not game.player.set_posture("stand"),"Low ceiling must prevent standing into terrain")
	roof.queue_free()
	await frames(2)
	game.player.set_posture("stand")
	for id in game.gameplay.SUPPORTED:
		game.gameplay.give_item(str(game.gameplay.weapons[id].item_id),id,1)
		check(game.gameplay.equip(id),"Owned supported weapon must equip")
	game.gameplay.equip("energy_bow")
	game.player.heavy_attack = false
	game.gameplay.attack_clock = 0
	game.gameplay.attack()
	while not game.gameplay.pending_attack.is_empty():
		await frames(1)
	check(game.gameplay.projectiles.size()==1,"Bow normal shot")
	game.gameplay.projectiles.clear()
	game.player.heavy_attack = true
	game.gameplay.attack_clock = 0
	game.gameplay.attack()
	while not game.gameplay.pending_attack.is_empty():
		await frames(1)
	check(game.gameplay.projectiles.size()==12,"Charged bow arrow rain")
	game.gameplay.projectiles.clear()
	var wall := StaticBody2D.new()
	wall.collision_layer = 1
	var wall_shape := CollisionShape2D.new()
	wall_shape.shape = RectangleShape2D.new()
	wall_shape.shape.size = Vector2(4,100)
	wall.position = Vector2(790,510)
	wall.add_child(wall_shape)
	game.add_child(wall)
	await frames(2)
	game.gameplay.spawn_projectile(Vector2(750,510),Vector2(520,0),30,1)
	await frames(12)
	check(game.gameplay.projectiles.is_empty(),"Projectile sweep must hit thin wall")
	wall.queue_free()
	await frames(2)
	var enemy = preload("res://scripts/creature.gd").new()
	enemy.config = game.gameplay.definitions.wolf.duplicate(true)
	enemy.placement = {"entity_id":"phase3-wolf","species":"wolf","x":20,"y":14,"movement_mode":"chase","behavior_mode":"hostile"}
	enemy.world = game.world
	enemy.target = game.player
	enemy.simulation_enabled = false
	game.gameplay.actors.add_child(enemy)
	enemy.position = Vector2(810,540)
	await frames(2)
	var hp_before: float = enemy.hp
	game.gameplay.spawn_projectile(Vector2(760,540),Vector2(520,0),30,1)
	await frames(10)
	check(enemy.hp==hp_before-30,"Arrow must damage creature")
	game.player.swing_left = 0
	game.player.set_posture("prone")
	check(game.player.is_hidden(),"Idle prone must enable stealth")
	game.player.invulnerable = 0
	enemy.position = game.player.position+Vector2(25,0)
	enemy.attack_cooldown = 0
	enemy.simulation_enabled = true
	var player_hp: float = game.player.hp
	await frames(10)
	check(game.player.hp==player_hp,"Prone must prevent enemy contact targeting")
	enemy.simulation_enabled = false
	game.progress_path = "user://phase3_load_test.json"
	preload("res://scripts/progress_store.gd").save_data(game.progress_path,{"schema":2,"map":"wuxia_world","position":[500,500],"hp":63,"inventory":{"weapon_sword":{"name":"鐵劍","count":1},"weapon_spear":{"name":"長矛","count":1}},"equipped":"spear","sessions":{}})
	game.start_session(true)
	check(game.world.map_id=="wuxia_world" and game.player.hp==63 and game.player.equipped=="spear","Load must restore map, hp and equipped weapon")
	game.menu.open()
	check(paused and game.menu.overlay.visible,"Menu must pause game")
	game.menu.close()
	check(not paused,"Resume menu")
	game.preferences.starter_items=false
	game.start_session(false)
	check(game.player.hp==100 and game.gameplay.inventory.size()==2,"New game resets adventure inventory and hp")
	check(game.gameplay.sessions.size()<=1,"New game clears old map sessions")
	if failures==0:
		print("PHASE3_OK ladder exit/entry, platform drop, acceleration, stances, headroom, roll, weapons, projectiles, menu/new game")
	quit(1 if failures else 0)
