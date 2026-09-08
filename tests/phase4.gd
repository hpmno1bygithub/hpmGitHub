extends SceneTree
const DB=preload("res://scripts/content_db.gd")
const Bag=preload("res://scripts/inventory_model.gd")
var game
var failures:=0
func _initialize() -> void:
	call_deferred("run")
func check(ok: bool, text: String) -> void:
	if not ok:
		failures+=1
		push_error(text)
func frames(count: int) -> void:
	for i in range(count): await physics_frame
func total(pool: Dictionary) -> float:
	var amount:=0.0
	for value in pool.values(): amount+=float(value)
	return amount
func run() -> void:
	DB.path="user://phase4_content_test.json"
	DB.pack={"schema":1,"maps":{},"assets":{},"creatures":{},"weapons":{},"actions":{},"custom":{}}
	var bag:=Bag.new()
	check(bag.add("material_2","土",205)==205,"Stack add")
	check(bag.slots[0].count==99 and bag.slots[2].count==7,"99 item stack cap")
	bag.move(0,3,20)
	check(bag.slots[0].count==79 and bag.slots[3].count==20,"Split stack")
	bag.move(3,0,20)
	check(bag.slots[0].count==99 and bag.slots[3]==null,"Merge stack")
	bag.add("weapon_sword","劍",2,[3,7])
	var result:=bag.use_heavy("weapon_sword")
	check(result.accepted,"Consume heavy use")
	var restored:=Bag.new()
	restored.restore(bag.snapshot())
	check(restored.snapshot()==bag.snapshot(),"Per-copy durability roundtrip")
	bag.add("equipment.double_jump_shoes","鞋",1)
	bag.worn.foot="equipment.double_jump_shoes"
	var index:=-1
	for i in range(72):
		if bag.slots[i]!=null and bag.slots[i].id==bag.worn.foot: index=i
	bag.move(index,71,1)
	check(bag.worn.foot=="equipment.double_jump_shoes","Moving equipped item cannot unequip")
	var full:=Bag.new()
	for i in range(72): full.add("item"+str(i),"item",99)
	check(full.add("overflow","滿",1)==0,"Full bag rejects without destroying world loot")
	game=load("res://scenes/main.tscn").instantiate()
	game.test_mode=true
	root.add_child(game)
	game.study.model.persistence=false
	game.world.terrain.clear()
	game.world.walls.clear()
	for x in range(5,25): game.world.edit_cell(Vector2i(x,14),2,7)
	game.world.build_walls()
	game.player.position=Vector2(740,539)
	await frames(10)
	Input.action_press("crouch")
	await frames(120)
	Input.action_release("crouch")
	check(game.player.posture=="crouch","Holding crouch cannot toggle posture")
	check(game.player.sprite.animation=="crouch" and game.player.sprite.frame==1 and not game.player.sprite.is_playing(),"Non-loop crouch holds final frame after two seconds")
	game.world.edit_cell(Vector2i(6,14),0)
	check(game.world.walls.get_cell_source_id(Vector2i(6,14))==0,"Mining leaves material backing wall")
	check(not game.world.walls.collision_enabled,"Backing wall has no collision")
	game.preferences.death_handling=false
	game.player.hp=5
	game.player.invulnerable=0
	game.player.take_damage(20,1)
	check(game.player.hp==1,"Death disabled protects last hp against creatures")
	game.player.environment_damage(20)
	check(game.player.hp==1,"Death disabled protects last hp against fluids")
	game.preferences.starter_items=false
	game.start_session(false)
	check(game.gameplay.inventory.size()==2 and game.gameplay.inventory.has("tool_grapple"),"Original starter-off still retains sword and hook")
	game.preferences.starter_items=true
	game.start_session(false)
	check(game.gameplay.inventory.size()==8,"Starter-on grants six original wearables")
	game.world.terrain.clear()
	for x in range(5,15):
		game.world.edit_cell(Vector2i(x,15),2)
	for y in range(5,15):
		game.world.edit_cell(Vector2i(5,y),2)
		game.world.edit_cell(Vector2i(14,y),2)
	game.liquids.load_map({"water":[],"lava":[],"honey":[]})
	check(is_equal_approx(game.liquids.deposit("water",Vector2i(10,10),1),1),"Water deposit")
	for i in range(120): game.liquids.step(1.0/30,Rect2i(0,0,20,20))
	check(absf(total(game.liquids.pools.water)-1)<.00001,"Closed water basin conserves volume")
	check(float(game.liquids.pools.water.get(Vector2i(10,14),0))>0,"Water flows down")
	game.world.edit_cell(Vector2i(8,8),2,1)
	check(absf(game.liquids.capacity(Vector2i(8,8))-2.0/3)<.001,"Liquid capacity follows partial terrain")
	check(game.liquids.deposit("honey",Vector2i(10,14),1)==0,"Different liquids do not overlap")
	game.liquids.load_map({"water":[],"lava":[],"honey":[]})
	game.liquids.deposit("honey",Vector2i(10,10),1)
	game.liquids.step(1.0/30,Rect2i(0,0,20,20))
	check(float(game.liquids.pools.honey.get(Vector2i(10,10),0))>.8,"Honey falls slower than water")
	var state: Dictionary=game.liquids.snapshot()
	game.liquids.load_map(state)
	check(absf(total(game.liquids.pools.honey)-1)<.00001,"Liquid save roundtrip")
	game.liquids.load_map({"water":[],"lava":[],"honey":[]})
	for x in range(9,12): game.world.edit_cell(Vector2i(x,13),2)
	game.liquids.deposit("lava",Vector2i(10,12),.02)
	game.liquids.deposit("water",Vector2i(10,11),1)
	for i in range(60): game.liquids.step(1.0/30,Rect2i(10,11,1,2))
	check(game.liquids.cooled>0 and game.liquids.steam>0,"Water cools lava and records consumed steam")
	game.change_map("wuxia_world")
	game.map_editor.open()
	var cell:=Vector2i(35,20)
	game.map_editor.kind.select(0)
	game.map_editor.populate()
	game.map_editor.choice.select(game.map_editor.ids.find(2))
	game.map_editor.paint(cell)
	check(game.world.terrain.get_cell_atlas_coords(cell).x==2,"Map editor paint")
	game.map_editor.undo()
	check(game.world.terrain.get_cell_atlas_coords(cell).x!=2,"Map editor undo")
	game.map_editor.redo()
	check(game.world.terrain.get_cell_atlas_coords(cell).x==2,"Map editor redo")
	game.map_editor.save_map()
	game.map_editor.close()
	game.change_map("crystal_mine")
	game.change_map("wuxia_world")
	check(game.world.terrain.get_cell_atlas_coords(cell).x==2,"Authored edit survives map change")
	game.asset_editor.open()
	game.asset_editor.dirty=false
	game.asset_editor.select_asset("creature.wolf")
	game.asset_editor.numbers.hp.value=222
	game.asset_editor.canvas.grid[0][0]="#FF0000FF"
	game.asset_editor.save_asset()
	check(game.gameplay.definitions.wolf.hp==222,"Creature property editor affects runtime registry")
	check(DB.asset("creature.wolf").animations.idle.frames[0][0][0]=="#FF0000FF","Pixel editor commits editable source")
	check(DB.export_to("user://phase4_export_test.json")==OK,"Export authored content")
	var exported=JSON.parse_string(FileAccess.get_file_as_string("user://phase4_export_test.json"))
	check(exported.assets.has("creature.wolf") and exported.maps.has("wuxia_world"),"Export retains linked asset and map data")
	var validation:=preload("res://scripts/content_validate.gd").validate(exported)
	check(validation.is_empty(),"Exported content validates for Godot import")
	check(not preload("res://scripts/content_validate.gd").validate({"schema":1,"assets":{"bad":{"pixels":[["invalid"]]}}}).is_empty(),"Malformed pixel source must be rejected")
	game.asset_editor.dirty=false
	game.asset_editor.select_asset("creature.wolf")
	game.asset_editor.new_id.text="creature.phase4_test_wolf"
	game.asset_editor.duplicate_asset()
	check(game.gameplay.definitions.has("phase4_test_wolf"),"Cloned asset gets a playable creature definition")
	check(DB.asset("creature.phase4_test_wolf").asset_id=="creature.phase4_test_wolf","Cloned asset ID remains stable")
	game.asset_editor.select_asset("weapon.sword")
	game.asset_editor.new_id.text="weapon.phase4_test_sword"
	game.asset_editor.duplicate_asset()
	game.gameplay.give_item("weapon_phase4_test_sword","測試劍",1)
	check(game.gameplay.equip("phase4_test_sword"),"Cloned weapon remains equippable")
	check(game.gameplay.weapons.phase4_test_sword.base_weapon=="sword","Cloned weapon retains its original effect/behavior family")
	game.asset_editor.request_close()
	var actor=preload("res://scripts/creature.gd").new()
	actor.config=game.gameplay.definitions.wolf
	actor.placement={"species":"wolf","entity_id":"action_test","x":1,"y":1}
	actor.target=game.player
	actor.world=game.world
	actor.simulation_enabled=false
	game.gameplay.actors.add_child(actor)
	actor.position=game.player.position+Vector2(30,0)
	actor.actions.profile={"actions":{"cast":{"enabled":true,"chance":1,"priority":1,"range_min":0,"range_max":100,"requires_ground":false,"animation_state":"idle","effect":"magic_bolt","motion":"stationary","duration":.2,"speed":200,"damage":12}}}
	game.gameplay.enemy_shots.clear()
	check(actor.actions.tick(.01,true),"Configured action becomes active")
	check(game.gameplay.enemy_shots.is_empty(),"Missing action marker uses original half-duration fallback")
	actor.actions.tick(.11,false)
	check(game.gameplay.enemy_shots.size()==1,"Creature action event spawns gameplay projectile")
	check(actor.actions.cooldowns.has("cast"),"Authored action cooldown is enforced")
	if failures==0: print("PHASE4_OK persistent crouch, material walls, settings, 72-slot inventory/durability, fluids, linked map and pixel editors")
	quit(1 if failures else 0)
