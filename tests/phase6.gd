extends SceneTree
const DB=preload("res://scripts/content_db.gd")
var game
var failures:=0
func _initialize() -> void: call_deferred("run")
func check(ok: bool,label: String) -> void:
	if not ok:
		failures+=1
		push_error(label)
func frames(count: int) -> void:
	for i in range(count): await physics_frame
func run() -> void:
	DB.path="user://phase6_content_test.json"
	DB.pack={"schema":1,"maps":{},"assets":{},"creatures":{},"weapons":{},"actions":{},"custom":{}}
	game=load("res://scenes/main.tscn").instantiate()
	game.test_mode=true
	root.add_child(game)
	game.study.model.persistence=false
	game.change_map("control_lab")
	game.player.set_physics_process(false)
	for actor in game.gameplay.actors.get_children(): actor.simulation_enabled=false
	game.player.position=Vector2(100,100)
	game.world.terrain.clear()
	game.world.walls.clear()
	for pool in game.liquids.pools.values(): pool.clear()
	for x in range(2,50): game.world.edit_cell(Vector2i(x,20),2,7)
	await frames(3)
	# All partial support heights: water freezes flush to rock and returns exactly its mass.
	for mask in [0,1,3]:
		var cell:=Vector2i(12+mask*2,19)
		game.world.edit_cell(cell,2,mask)
		var cap: float=game.liquids.terrain_capacity(cell)
		game.liquids.deposit("water",cell,cap)
		game.environment.element_impact("iceball",cell,1,1)
		check(game.environment.frozen.has(cell),"Freeze water on support mask "+str(mask))
		var rect: Rect2=game.environment.ice_rect(cell)
		check(absf(rect.end.y-(cell.y+cap)*40)<.001,"No seam under fractional ice mask "+str(mask))
		check(game.world.terrain.get_cell_atlas_coords(cell).y==mask or mask==0,"Underlying material retained")
		await frames(2)
		var ray:=PhysicsRayQueryParameters2D.create(rect.get_center()-Vector2(0,60),rect.get_center(),1)
		check(not game.get_world_2d().direct_space_state.intersect_ray(ray).is_empty(),"Ice collision matches rendered surface")
		var before: float=game.environment.steam
		var shot: Dictionary={"at":Vector2(rect.end.x,rect.get_center().y),"radius":8.0,"element":"fireball","level":3,"fragment":false,"mass":6.0}
		game.magic.impact(shot,Vector2.RIGHT)
		check(not game.environment.frozen.has(cell),"Right face fireball thaws ice including L3")
		check(is_equal_approx(float(game.liquids.pools.water.get(cell,0)),cap),"Thaw conserves partial mass")
		check(game.environment.steam==before and game.magic.shots.is_empty(),"Same L3 hit cannot evaporate meltwater through fragments")
	# Repeat actual swept projectile impacts from all four faces, not only helper calls.
	var center:=Vector2i(25,14)
	for direction in [Vector2.LEFT,Vector2.RIGHT,Vector2.UP,Vector2.DOWN]:
		game.liquids.pools.water.erase(center)
		game.environment.freeze_cell(center,1)
		await frames(2)
		var at: Vector2=Vector2(center)*40+Vector2(20,20)-direction*35
		game.magic.shots=[{"at":at,"motion":direction*900,"life":1.0,"age":0.0,"element":"fireball","level":1,"radius":8.0,"fragment":false,"mass":1.0}]
		game.magic.update_shots(.05)
		check(not game.environment.frozen.has(center),"Swept fireball thaws face "+str(direction))
		game.magic.clear()
		await frames(2)
	# Warm air melts; natural alpine ice remains until an explicit heat source.
	game.liquids.pools.water.clear()
	game.environment.freeze_cell(center,.5)
	game.world.map_data.metadata.biome_spans=[[0,100,"snow_mountain"]]
	game.environment.thermal_step(20)
	check(game.environment.frozen.has(center),"Subzero climate preserves ice")
	game.liquids.pools.lava[center+Vector2i.RIGHT]=.5
	game.environment.thermal_step(2)
	check(not game.environment.frozen.has(center),"Nearby lava melts cold climate ice")
	game.liquids.pools.lava.clear()
	game.liquids.pools.water.clear()
	game.environment.freeze_cell(center,.5)
	game.world.map_data.metadata.biome_spans=[]
	game.environment.thermal_step(2)
	check(not game.environment.frozen.has(center),"Warm climate naturally melts ice")
	# Freeze / reload on a partial cell retains both support and mass.
	game.world.edit_cell(center,2,1)
	game.liquids.pools.water.clear()
	game.environment.freeze_cell(center,.6)
	var state: Dictionary=game.environment.snapshot()
	game.environment.restore(state)
	check(is_equal_approx(float(game.environment.frozen[center]),.6) and game.world.terrain.get_cell_atlas_coords(center)==Vector2i(2,1),"Partial ice save roundtrip preserves rock")
	game.environment.melt(center)
	game.world.edit_cell(center,0,0)
	# Evaporation and boiling create real vapor mass + bounded moving plumes + decaying updraft.
	game.liquids.pools.water.clear()
	game.liquids.pools.water[center]=1.0
	var old_steam: float=game.environment.steam
	game.environment.element_impact("fireball",center,1,1)
	check(is_equal_approx(float(game.liquids.pools.water[center])+game.environment.steam-old_steam,1),"Evaporation water/vapor mass balance")
	check(not game.environment.plumes.is_empty() and float(game.environment.updraft.get(center,0))>0,"Evaporation produces plumes and real updraft")
	game.environment.plumes.clear()
	game.environment.add_plume(Vector2(100,100),"steam")
	game.environment.advance_plumes(.2)
	check(game.environment.plumes[0].at.y<100,"Steam moves upward")
	game.environment.add_plume(Vector2(100,100),"smoke")
	game.environment.add_plume(Vector2(100,100),"ember")
	game.environment.advance_plumes(.2)
	check(game.environment.plumes[1].at.y<100 and game.environment.plumes[2].at.y<100,"Smoke and sparks rise")
	for i in range(400): game.environment.add_plume(Vector2.ZERO,"steam")
	check(game.environment.plumes.size()<=256,"VFX remains bounded on mobile")
	game.environment.advance_plumes(5)
	check(game.environment.plumes.is_empty(),"VFX expires without accumulation")
	# Step up one third in either direction / both postures, block 2 thirds and low ceiling.
	for posture in ["stand","crouch"]:
		for direction in [-1,1]:
			for mask in [1,3]:
				game.release_inputs()
				game.world.edit_cell(Vector2i(10,19),2,mask)
				game.player.position=Vector2(420-direction*55,779)
				game.player.velocity=Vector2.ZERO
				game.player.set_posture(posture)
				game.player.set_physics_process(true)
				await frames(10)
				Input.action_press("move_right" if direction>0 else "move_left")
				await frames(45 if posture=="stand" else 70)
				game.release_inputs()
				var crossed: bool=game.player.position.x>451 if direction>0 else game.player.position.x<389
				check(crossed==(mask==1),"Step mask %d posture %s direction %d x %.2f" % [mask,posture,direction,game.player.position.x])
				check(game.player.posture==posture,"Stepping never changes crouch posture")
				game.player.set_physics_process(false)
	game.world.edit_cell(Vector2i(10,19),2,1)
	game.player.position=Vector2(365,779)
	game.player.velocity=Vector2.ZERO
	check(game.player.set_posture("stand"),"Ceiling test starts standing")
	var ceiling:=StaticBody2D.new()
	ceiling.collision_layer=1
	var ceiling_shape:=CollisionShape2D.new()
	var ceiling_rect:=RectangleShape2D.new()
	ceiling_rect.size=Vector2(200,10)
	ceiling_shape.shape=ceiling_rect
	ceiling.add_child(ceiling_shape)
	ceiling.position=Vector2(420,746)
	game.add_child(ceiling)
	game.player.set_physics_process(true)
	await frames(10)
	Input.action_press("move_right")
	await frames(40)
	game.release_inputs()
	check(game.player.position.x<390,"Low ceiling blocks step sweep")
	game.player.set_physics_process(false)
	# Responsive source layout checks all touch boxes fit and do not overlap.
	for device in [[Vector2(1704,786),0],[Vector2(1280,720),0],[Vector2(2048,1536),1]]:
		game.mobile_hud.layout(device[0],device[1])
		var frames_by_id: Dictionary=game.mobile_hud.frames
		for key in frames_by_id:
			var rect: Rect2=frames_by_id[key]
			check(Rect2(Vector2.ZERO,game.mobile_hud.logical_size).encloses(rect),"Control inside safe area "+str(key))
			for other in frames_by_id:
				if key==other: continue
				check(not rect.intersects(frames_by_id[other]),"Source touch targets do not overlap: "+str(key)+" / "+str(other))
	game.layout_ui()
	# Independent move/aim fingers and focus loss cancel every held axis.
	var move=game.mobile_hud.move_pad
	move.begin(11,move.size*Vector2(.9,.5))
	game.aim_pad.begin(12,game.aim_pad.size*Vector2(.5,.1))
	check(game.player.move_axis.x>.5 and game.actions.direction().y<-.5,"Simultaneous move and aim")
	game.aim_pad.reset()
	check(game.player.move_axis.x>.5,"Aim release does not release movement")
	game.release_inputs()
	check(game.player.move_axis==Vector2.ZERO and move.finger==-1,"Focus loss releases move stick")
	if failures==0: print("PHASE6_OK fractional ice, four-face thaw, mass balance, heat, rising VFX, step collision, iOS layout and touch ownership")
	game.free()
	quit(1 if failures else 0)
