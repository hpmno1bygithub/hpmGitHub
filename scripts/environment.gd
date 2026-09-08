extends Node2D
## Conserved ice overlays share a cell with the original three-layer terrain.
const VFX=preload("res://scripts/source_vfx.gd")
var game: Node2D
var frozen: Dictionary={}
var ice_bodies: Dictionary={}
var melt_progress: Dictionary={}
var fires: Dictionary={}
var steam:=0.0
var clock:=0.0
var plumes: Array=[]
var updraft: Dictionary={}
var vapor_credit: Dictionary={}
var rules: Dictionary
var elapsed:=0.0
func _ready() -> void:
	z_index=4
	rules=JSON.parse_string(FileAccess.get_file_as_string("res://data/thermal_rules.json"))
func snapshot() -> Dictionary:
	var ice: Array=[]
	for cell in frozen: ice.append([cell.x,cell.y,frozen[cell],melt_progress.get(cell,0)])
	var burning: Array=[]
	for key in fires: burning.append([key,fires[key]])
	return {"frozen":ice,"fires":burning,"steam":steam}
func restore(state: Dictionary) -> void:
	for body in ice_bodies.values(): body.free()
	ice_bodies.clear()
	frozen.clear()
	melt_progress.clear()
	fires.clear()
	plumes.clear()
	updraft.clear()
	vapor_credit.clear()
	steam=float(state.get("steam",0))
	for row in state.get("frozen",[]):
		var cell:=Vector2i(int(row[0]),int(row[1]))
		frozen[cell]=float(row[2])
		melt_progress[cell]=float(row[3]) if row.size()>3 else 0.0
	# Upgrade Phase 5 saves and authored ice without destroying underlying partial terrain.
	for cell in game.world.terrain.get_used_cells():
		var tile: Vector2i=game.world.terrain.get_cell_atlas_coords(cell)
		if tile.x!=6: continue
		if not frozen.has(cell): frozen[cell]=float((tile.y&1)+((tile.y>>1)&1)+((tile.y>>2)&1))/3.0
		game.world.edit_cell(cell,0,0)
	for cell in frozen: sync_ice(cell)
	for row in state.get("fires",[]): fires[str(row[0])]=float(row[1])
	queue_redraw()
func ice_rect(cell: Vector2i, mass: float=-1) -> Rect2:
	if mass<0: mass=float(frozen.get(cell,0))
	var cap: float=game.liquids.terrain_capacity(cell)
	var height:=0.0
	if mass>=float(rules.ICE_STAGE_MIN_MASS):
		height=float(rules.ICE_STAGE_LOW_HEIGHT_RATIO) if mass<float(rules.ICE_STAGE_LOW_MAX_MASS) else (float(rules.ICE_STAGE_MID_HEIGHT_RATIO) if mass<float(rules.ICE_STAGE_MID_MAX_MASS) else 1.0)
	height=minf(cap,height)
	return Rect2(Vector2(cell)*40+Vector2(0,(cap-height)*40),Vector2(40,height*40))
func ice_at(point: Vector2) -> bool:
	var cell:=Vector2i(floori(point.x/40),floori(point.y/40))
	return frozen.has(cell) and ice_rect(cell).has_point(point)
func sync_ice(cell: Vector2i) -> void:
	if not frozen.has(cell): return
	var rect:=ice_rect(cell)
	if not ice_bodies.has(cell):
		var new_body:=StaticBody2D.new()
		new_body.collision_layer=1
		new_body.collision_mask=0
		new_body.add_child(CollisionShape2D.new())
		add_child(new_body)
		ice_bodies[cell]=new_body
	var body: StaticBody2D=ice_bodies[cell]
	body.position=rect.get_center()
	var collider: CollisionShape2D=body.get_child(0)
	var shape:=RectangleShape2D.new()
	shape.size=rect.size
	collider.shape=shape
	collider.disabled=rect.size.y<=0
func can_freeze(cell: Vector2i, mass: float=1) -> bool:
	if frozen.has(cell) or game.tool_system.protected_cell(cell) or game.liquids.terrain_capacity(cell)<.04: return false
	var rect:=ice_rect(cell,mass)
	var shape:=RectangleShape2D.new()
	shape.size=rect.size-Vector2(.1,.1)
	var query:=PhysicsShapeQueryParameters2D.new()
	query.shape=shape
	query.transform=Transform2D(0,rect.get_center())
	query.collision_mask=2|4
	return get_world_2d().direct_space_state.intersect_shape(query).is_empty()
func freeze_cell(cell: Vector2i, mass: float) -> bool:
	mass=minf(mass,game.liquids.terrain_capacity(cell))
	if mass<.04 or not can_freeze(cell,mass): return false
	frozen[cell]=mass
	game.liquids.pools.water.erase(cell)
	sync_ice(cell)
	queue_redraw()
	return true
func remove_ice(cell: Vector2i) -> float:
	var mass:=float(frozen.get(cell,0))
	frozen.erase(cell)
	melt_progress.erase(cell)
	if ice_bodies.has(cell):
		var body: StaticBody2D=ice_bodies[cell]
		body.collision_layer=0
		body.queue_free()
		ice_bodies.erase(cell)
	queue_redraw()
	return mass
func melt(cell: Vector2i) -> bool:
	var tile: Vector2i=game.world.terrain.get_cell_atlas_coords(cell)
	if not frozen.has(cell) and tile.x==6:
		frozen[cell]=float((tile.y&1)+((tile.y>>1)&1)+((tile.y>>2)&1))/3.0
		game.world.edit_cell(cell,0,0)
	if not frozen.has(cell) or game.tool_system.protected_cell(cell): return false
	var remaining:=remove_ice(cell)
	for offset in [Vector2i.ZERO,Vector2i.UP,Vector2i.LEFT,Vector2i.RIGHT]:
		remaining-=game.liquids.deposit("water",cell+offset,remaining)
		if remaining<.000001: break
	if remaining>0: emit_vapor(cell,remaining)
	# A fireball melts ice into water; only a later hit evaporates that water (FIX131).
	return true
func element_impact(kind: String, cell: Vector2i, level: int, amount: float) -> void:
	if kind=="waterball":
		var remaining:=amount
		for offset in [Vector2i.ZERO,Vector2i.UP,Vector2i.LEFT,Vector2i.RIGHT,Vector2i(-1,-1),Vector2i(1,-1)]:
			remaining-=game.liquids.deposit("water",cell+offset,remaining)
			if remaining<=.00001: break
		if remaining>0: emit_vapor(cell,remaining)
		for key in fires.keys():
			if Vector2(fire_cell(key)-cell).length()<3: fires.erase(key)
	elif kind=="iceball":
		var limit:=int([1,3,7][level-1])
		var queue: Array=[cell]
		var seen: Dictionary={}
		var count:=0
		while not queue.is_empty() and seen.size()<32 and count<limit:
			var current: Vector2i=queue.pop_front()
			if seen.has(current): continue
			seen[current]=true
			var volume:=float(game.liquids.pools.water.get(current,0))
			if volume>=.08 and freeze_cell(current,volume):
				count+=1
				queue.append_array([current+Vector2i.LEFT,current+Vector2i.RIGHT])
			elif current==cell:
				queue.append_array([cell+Vector2i.UP,cell+Vector2i.LEFT,cell+Vector2i.RIGHT])
		if count==0:
			var length:=int([1,3,5][level-1])
			var origin:=cell+Vector2i.UP if game.liquids.terrain_capacity(cell)<.04 else cell
			for x in range(length):
				var current:=origin+Vector2i(x-floori(length/2.0),0)
				if float(game.liquids.pools.lava.get(current,0))+float(game.liquids.pools.honey.get(current,0))<=0:
					freeze_cell(current,game.liquids.terrain_capacity(current))
	elif kind=="fireball":
		if not melt(cell):
			var volume:=float(game.liquids.pools.water.get(cell,0))
			var evaporated:=minf(volume,.18*amount)
			if evaporated>0:
				game.liquids.pools.water[cell]=volume-evaporated
				emit_vapor(cell,evaporated)
			for layer in ["vegetation","decoration"]:
				for row in game.world.map_data.layers.get(layer,[]):
					if Vector2(float(row[0]),float(row[1])).distance_to(Vector2(cell))<=2 and str(row[2]) in ["tree","pine","jungle_tree","grass","fern","reed"]:
						var key: String=game.scenery.plant_key(layer,row)
						if not game.scenery.removed.has(key): fires[key]=3.0
	elif kind=="electricball":
		var visited: Dictionary={}
		var queue: Array=[cell]
		while not queue.is_empty() and visited.size()<96:
			var current: Vector2i=queue.pop_front()
			if visited.has(current): continue
			if current!=cell and float(game.liquids.pools.water.get(current,0))<.08: continue
			visited[current]=true
			for direction in [Vector2i.UP,Vector2i.DOWN,Vector2i.LEFT,Vector2i.RIGHT]:
				if not visited.has(current+direction): queue.append(current+direction)
		for actor in game.gameplay.actors.get_children():
			if not actor.dead and visited.has(Vector2i(actor.position/40)):
				actor.take_hit(float([14,24,38][level-1])*1.35,0)
				actor.stun_left=3

	queue_redraw()
func fire_cell(key: String) -> Vector2i:
	var parts:=key.split(":")
	return Vector2i(int(parts[1]),int(parts[2]))
func climate(cell: Vector2i) -> String:
	for span in game.world.map_data.metadata.get("biome_spans",[]):
		if cell.x>=int(span[0]) and cell.x<int(span[1]):
			return str({"snow_mountain":"alpine","polar":"polar","desert":"desert","rainforest":"rainforest"}.get(str(span[2]),"temperate"))
	return "temperate"
func heat_at(cell: Vector2i) -> float:
	var heat:=0.0
	for offset in [Vector2i.ZERO,Vector2i.UP,Vector2i.DOWN,Vector2i.LEFT,Vector2i.RIGHT]:
		if float(game.liquids.pools.lava.get(cell+offset,0))>.01: heat=160.0
	for key in fires:
		if Vector2(fire_cell(key)-cell).length()<3: heat=maxf(heat,80.0)
	return heat
func thermal_step(dt: float) -> void:
	# Sparse dynamic ice continues to thaw off-screen; sub-zero climate needs explicit heat.
	var active_ice: Array=frozen.keys()
	var center:=Vector2i(game.player.position/40)
	var visible_region:=Rect2i(center-Vector2i(28,18),Vector2i(56,36))
	# Authored native ice remains editable terrain until it melts.
	for y in range(visible_region.position.y,visible_region.end.y):
		for x in range(visible_region.position.x,visible_region.end.x):
			var cell:=Vector2i(x,y)
			if game.world.terrain.get_cell_atlas_coords(cell).x==6 and not frozen.has(cell): active_ice.append(cell)
	for cell in active_ice:
		var tile: Vector2i=game.world.terrain.get_cell_atlas_coords(cell)
		var mass:=float(frozen.get(cell,float((tile.y&1)+((tile.y>>1)&1)+((tile.y>>2)&1))/3.0))
		if frozen.has(cell):
			if game.liquids.terrain_capacity(cell)<mass-.00001:
				melt(cell)
				continue
			var rect:=ice_rect(cell)
			if ice_bodies.has(cell) and ice_bodies[cell].position!=rect.get_center(): sync_ice(cell)
		var profile:=climate(cell)
		var heat:=heat_at(cell)
		var temperature:=float(rules.CLIMATE_PROFILES[profile].base_temp_c)
		var multiplier:=float(rules.CLIMATE_ICE_MELT_MULTIPLIER[profile])
		if heat>0: temperature=maxf(temperature,heat)
		if temperature<=0:
			melt_progress.erase(cell)
			continue
		melt_progress[cell]=float(melt_progress.get(cell,0))+temperature*float(rules.ICE_MELT_PROGRESS_PER_C_PER_SEC)*multiplier*dt
		if float(melt_progress[cell])>=maxf(.18,mass): melt(cell)
	for cell in game.liquids.pools.water.keys():
		if not visible_region.has_point(cell) or frozen.has(cell): continue
		var volume:=float(game.liquids.pools.water.get(cell,0))
		if volume<=.000001: continue
		if float(game.liquids.pools.water.get(cell+Vector2i.UP,0))>.01: continue
		# Only an exposed surface evaporates, not sealed underground water.
		if game.liquids.capacity(cell+Vector2i.UP)<=0: continue
		var hot:=heat_at(cell)>0
		var rate:=.30 if hot else float(rules.BASE_EVAPORATION)*float(rules.CLIMATE_WATER_EVAP_MULTIPLIER[climate(cell)])
		var consumed:=minf(volume,rate*dt)
		game.liquids.pools.water[cell]=volume-consumed
		emit_vapor(cell,consumed,"steam" if hot else "haze")
func emit_vapor(cell: Vector2i, mass: float, kind: String="steam") -> void:
	if mass<=0: return
	steam+=mass
	updraft[cell]=minf(float(rules.EVAP_UPDRAFT_MAX_SPEED),float(updraft.get(cell,0))+mass*float(rules.WATER_MASS_UNITS_PER_TILE)*float(rules.EVAP_UPDRAFT_PER_WATER_UNIT))
	vapor_credit[cell]=float(vapor_credit.get(cell,0))+mass
	if float(vapor_credit[cell])<.003: return
	var count:=clampi(ceili(float(vapor_credit[cell])*32),1,10)
	vapor_credit[cell]=0.0
	var surface: float=(cell.y+game.liquids.capacity(cell)-float(game.liquids.pools.water.get(cell,0)))*40
	for i in range(count): add_plume(Vector2(cell.x*40+5+fmod(elapsed*29+i*13,30),surface),kind)
func add_plume(at: Vector2, kind: String) -> void:
	if plumes.size()>=256: plumes.pop_front()
	plumes.append({"at":at,"age":0.0,"life":3.0 if kind=="haze" else 2.2,"kind":kind,"phase":elapsed*3.0+plumes.size(),"speed":30.0 if kind=="haze" else 65.0})
func advance_plumes(dt: float) -> void:
	for i in range(plumes.size()-1,-1,-1):
		var plume: Dictionary=plumes[i]
		plume.age+=dt
		if float(plume.age)>=float(plume.life):
			plumes.remove_at(i)
			continue
		var current: Vector2=plume.at
		var cell:=Vector2i(floori(current.x/40),floori(current.y/40))
		var rise: float=float(plume.speed)+float(updraft.get(cell,0))*.35
		var next:=current+Vector2(sin(float(plume.phase)+elapsed*2)*12,-rise)*dt
		var hit:=get_world_2d().direct_space_state.intersect_ray(PhysicsRayQueryParameters2D.create(current,next,1))
		if not hit.is_empty():
			# Curl beneath a ceiling instead of drawing through solid rock.
			next=current+Vector2(cos(float(plume.phase))*20*dt,0)
		plume.at=next
	for cell in updraft.keys():
		updraft[cell]*=exp(-float(rules.EVAP_UPDRAFT_DECAY_PER_SECOND)*dt)
		if float(updraft[cell])<.01: updraft.erase(cell)
func _physics_process(delta: float) -> void:
	elapsed+=delta
	advance_plumes(delta)
	clock+=delta
	if clock>=.1:
		var dt:=clock
		clock=0
		if not game.test_mode: thermal_step(dt)
		for key in fires.keys():
			fires[key]-=dt
			var cell:=fire_cell(key)
			if float(game.liquids.pools.water.get(cell,0))>.2:
				fires.erase(key)
				emit_vapor(cell,.01)
				continue
			var at:=Vector2(cell)*40+Vector2(20,-28)
			add_plume(at,"smoke")
			add_plume(at,"ember")
			updraft[cell]=minf(120,float(updraft.get(cell,0))+6*dt)
			if fires[key]<=0:
				game.scenery.removed[key]=true
				game.tool_system.drop_item("ash","灰燼",1,Vector2(cell)*40)
				fires.erase(key)
	queue_redraw()
func _draw() -> void:
	var visible_rect:=Rect2(-get_viewport().get_canvas_transform().origin-Vector2(80,80),get_viewport_rect().size+Vector2(160,160))
	for placard in game.world.map_data.metadata.get("signs",[]):
		draw_string(game.safe_ui.get_theme_default_font(),Vector2(float(placard[0])*40,float(placard[1])*40),str(placard[2]),HORIZONTAL_ALIGNMENT_LEFT,-1,16,Color.WHITE)
	for cell in frozen:
		var rect:=ice_rect(cell)
		if not visible_rect.intersects(rect): continue
		draw_rect(rect,Color(.47,.81,.94,.94))
		draw_line(rect.position,rect.position+Vector2(40,0),Color(.87,.98,1),2)
		for x in [8,25]:
			draw_line(rect.position+Vector2(x,2),rect.position+Vector2(x+5,minf(rect.size.y,9)),Color(.7,.94,1,.75),2)
	for key in fires:
		var at:=Vector2(fire_cell(key))*40+Vector2(20,-15)
		VFX.bolt(self,{"at":at,"element":"fire_column","width":22,"height":36})
	for plume in plumes:
		var fraction:=float(plume.age)/float(plume.life)
		var opacity:=sin(fraction*PI)*.34
		var kind:=str(plume.kind)
		var color:=Color(.82,.90,.96,opacity)
		var size:=Vector2(18,12)*(1+fraction)
		if kind=="haze": size=Vector2(40,9)*(1+fraction)
		elif kind=="smoke": color=Color(.40,.43,.48,opacity)
		elif kind=="ember":
			color=Color(1,.66,.16,1-fraction)
			size=Vector2(3,5)
		draw_rect(Rect2(Vector2(plume.at)-size*.5,size),color)
		if kind!="ember": draw_line(Vector2(plume.at)+Vector2(0,14),Vector2(plume.at)+Vector2(0,25),Color(.8,.92,1,opacity*.65),1.5)
