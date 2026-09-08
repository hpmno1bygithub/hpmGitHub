extends SceneTree

func _initialize() -> void:
	call_deferred("run")

func check(condition: bool, message: String) -> void:
	if not condition:
		push_error(message)
		quit(1)
		assert(condition, message)

func run() -> void:
	var scene = load("res://scenes/main.tscn").instantiate()
	scene.test_mode = true
	root.add_child(scene)
	await process_frame
	for map_id in scene.maps:
		scene.change_map(map_id)
		var expected := 0
		for cell in scene.world.map_data.cells:
			if int(cell[3]) != 0:
				expected += 1
		check(scene.world.terrain.get_used_cells().size() == expected, "Terrain cell mismatch: " + map_id)
		await physics_frame
		print("MAP_OK ", map_id, " cells=", expected)
	scene.change_map("editor_map")
	# Controlled flat platform verifies real CharacterBody2D collision and jumping.
	for x in range(5, 17):
		scene.world.terrain.set_cell(Vector2i(x, 10), 0, Vector2i(3, 7))
	scene.world.terrain.update_internals()
	scene.player.position = Vector2(420, 300)
	scene.player.velocity = Vector2.ZERO
	for i in range(60):
		await physics_frame
	check(scene.player.is_on_floor(), "Player must land on solid terrain")
	var before: Vector2 = scene.player.position
	Input.action_press("move_right")
	for i in range(15):
		await physics_frame
	Input.action_release("move_right")
	check(scene.player.position.x > before.x + 10, "Movement failed")
	Input.action_press("jump")
	for i in range(4):
		await physics_frame
	Input.action_release("jump")
	check(scene.player.position.y < before.y - 5, "Jump failed")
	var store = load("res://scripts/save_store.gd")
	var primary := "user://smoke_test_save.json"
	var backup := "user://smoke_test_save.backup.json"
	check(store.write_save("editor_map",Vector2(100,200),primary,backup) == OK,"Save failed")
	check(store.write_save("wuxia_world",Vector2(150,250),primary,backup) == OK,"Replacement save failed")
	check(store.read_save(primary,backup).map == "wuxia_world","Save roundtrip failed")
	var file := FileAccess.open(primary,FileAccess.WRITE)
	file.store_string("broken"); file.close()
	check(store.read_save(primary,backup).map == "editor_map","Backup recovery failed")
	check(store.write_save("boss_dungeon",Vector2(150,250),primary,backup) == OK,"Recovery write failed")
	check(store.read_save(backup,backup).map == "editor_map","Valid backup must survive corrupt primary")
	DirAccess.remove_absolute(primary)
	DirAccess.remove_absolute(backup)
	scene.player.position = scene.world.spawn_point()
	scene.save_game()
	print("SMOKE_OK maps, collision, movement, jump, save replacement, backup recovery")
	quit(0)
