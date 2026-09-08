extends Node2D
# Sparse volume simulation; values use the original fraction-of-one-tile unit.
const COLORS = {"water":Color(.12,.53,.85,.64),"lava":Color(1,.28,.045,.94),"honey":Color(.95,.64,.12,.85)}
var game: Node2D
var pools := {"water":{},"lava":{},"honey":{}}
var hazards: Dictionary = {}
var clock := 0.0
var step_index := 0
var burn_left := 0.0
var breath := 12.0
var enabled := true
var cooled := 0.0
var steam := 0.0

func _ready() -> void:
	z_index = 3

func _process(_delta: float) -> void:
	queue_redraw()

func load_map(state: Dictionary = {}) -> void:
	for kind in pools:
		pools[kind] = {}
		for row in state.get(kind,game.world.map_data.layers.get(kind,[])):
			pools[kind][Vector2i(int(row[0]),int(row[1]))] = clampf(float(row[2]),0,1)
	game.world.water = pools.water
	hazards.clear()
	for row in game.world.map_data.layers.get("hazard",[]):
		hazards[Vector2i(int(row[0]),int(row[1]))] = str(row[2])
	burn_left = 0
	breath = 12
	clock = 0
	queue_redraw()

func snapshot() -> Dictionary:
	var result := {}
	for kind in pools:
		result[kind] = []
		for cell in pools[kind]:
			if float(pools[kind][cell])>0:
				result[kind].append([cell.x,cell.y,pools[kind][cell]])
	return result

func capacity(cell: Vector2i) -> float:
	var cap:=terrain_capacity(cell)
	if game.environment!=null and game.environment.frozen.has(cell):
		cap=maxf(0,cap-game.environment.ice_rect(cell).size.y/40)
	return cap

func terrain_capacity(cell: Vector2i) -> float:
	if cell.x<0 or cell.y<0 or cell.x>=game.world.dimensions.x or cell.y>=game.world.dimensions.y:
		return 0
	if game.scenery.has_method("solid_at") and game.scenery.solid_at(cell):
		return 0
	var tile: Vector2i = game.world.terrain.get_cell_atlas_coords(cell)
	if tile.x<0:
		return 1
	var definition: Dictionary = game.world.definitions[str(tile.x)]
	if not definition.get("solid",false) or definition.get("one_way_platform",false):
		return 1
	return 0.0 if tile.y&4 else (1.0/3 if tile.y&2 else 2.0/3)

func room(kind: String, cell: Vector2i) -> float:
	var used := 0.0
	for other in pools:
		if other!=kind and float(pools[other].get(cell,0))>.00000001:
			return 0
		used += float(pools[other].get(cell,0))
	return maxf(0,capacity(cell)-used)

func deposit(kind: String, cell: Vector2i, amount: float) -> float:
	if not pools.has(kind):
		return 0
	var accepted := minf(maxf(0,amount),room(kind,cell))
	if accepted>0:
		pools[kind][cell] = float(pools[kind].get(cell,0))+accepted
	queue_redraw()
	return accepted

func transfer(kind: String, from: Vector2i, to: Vector2i, amount: float) -> float:
	var moved := minf(float(pools[kind].get(from,0)),minf(room(kind,to),maxf(0,amount)))
	if moved>0:
		pools[kind][from] -= moved
		pools[kind][to] = float(pools[kind].get(to,0))+moved
		if float(pools[kind][from])<.00000001:
			pools[kind].erase(from)
	return moved

func step(dt: float, region: Rect2i) -> void:
	step_index += 1
	for kind in pools:
		var cells: Array = pools[kind].keys().filter(func(c): return region.has_point(c))
		cells.sort_custom(func(a,b): return a.y>b.y if a.y!=b.y else (a.x<b.x if step_index%2==0 else a.x>b.x))
		var fall := 1.0 if kind=="water" else (.58 if kind=="lava" else .42)
		var side := .5 if kind=="water" else (.16 if kind=="lava" else .10)
		var rate := 30.0 if kind=="water" else (12.0 if kind=="lava" else 5.0)
		for cell in cells:
			var available := float(pools[kind].get(cell,0))
			if available<=0:
				continue
			if transfer(kind,cell,cell+Vector2i.DOWN,minf(available,fall*rate*dt))>.00001:
				continue
			for direction in [-1,1] if (cell.x+cell.y+step_index)%2==0 else [1,-1]:
				var to: Vector2i = cell+Vector2i(direction,0)
				# Equalize physical surface heights, including partial ground cells.
				var difference := float(pools[kind].get(cell,0))-float(pools[kind].get(to,0))+capacity(to)-capacity(cell)
				if difference>.003:
					transfer(kind,cell,to,minf(difference*.5,side*rate*dt))
	# Original lava contact progressively consumes water and cools into igneous rock.
	for cell in pools.lava.keys():
		if not region.has_point(cell):
			continue
		for offset in [Vector2i.UP,Vector2i.LEFT,Vector2i.RIGHT,Vector2i.DOWN]:
			var other: Vector2i = cell+offset
			var water := float(pools.water.get(other,0))
			if water<=0 or not pools.lava.has(cell):
				continue
			var cooled_amount := minf(float(pools.lava[cell]),.72*water*dt)
			var consumed := minf(water,.30*dt)
			pools.lava[cell] -= cooled_amount
			pools.water[other] -= consumed
			cooled += cooled_amount
			steam += consumed
			game.environment.emit_vapor(other,consumed)
			if float(pools.lava[cell])<.015:
				cooled += float(pools.lava[cell])
				pools.lava.erase(cell)
				for id in game.world.definitions:
					if game.world.definitions[id].name=="igneous_rock":
						game.world.edit_cell(cell,int(id),7)
						break
	queue_redraw()

func at(point: Vector2) -> String:
	var cell := Vector2i(floori(point.x/40),floori(point.y/40))
	var cap := capacity(cell)
	for kind in ["lava","honey","water"]:
		var amount := minf(cap,float(pools[kind].get(cell,0)))
		if amount>.005 and point.y>=(cell.y+cap-amount)*40 and point.y<(cell.y+cap)*40:
			return kind
	return ""

func player_effects(delta: float) -> void:
	var player: CharacterBody2D = game.player
	var feet := at(player.position+Vector2(0,19))
	var head := at(player.position+Vector2(0,-12))
	player.liquid = at(player.position+Vector2(0,8))
	if head in ["water","honey"]:
		breath = maxf(0,breath-delta)
		if breath<=0:
			player.environment_damage(8*delta)
	else:
		breath = minf(12,breath+delta*3)
	if feet=="lava":
		player.environment_damage(28*delta)
		burn_left = 3
	elif feet=="water":
		burn_left = 0
	elif burn_left>0:
		burn_left = maxf(0,burn_left-delta)
		player.environment_damage(5*delta)
	var foot_cell := Vector2i(floori(player.position.x/40),floori((player.position.y+22)/40))
	player.in_swamp = str(hazards.get(foot_cell,""))=="swamp"
	if player.in_swamp:
		player.environment_damage(8*delta)

func _physics_process(delta: float) -> void:
	if game==null or game.test_mode or not enabled:
		return
	player_effects(delta)
	clock += delta
	if clock>=1.0/30:
		var center := Vector2i(game.player.position/40)
		step(1.0/30,Rect2i(center-Vector2i(28,18),Vector2i(56,36)))
		clock = fmod(clock,1.0/30)
		for source in game.world.map_data.metadata.get("lava_sources",[]):
			var cell := Vector2i(int(source[0]),int(source[1]))
			if Vector2(cell-center).length()<32:
				deposit("lava",cell,float(source[2])/30)
	queue_redraw()

func _draw() -> void:
	if game==null:
		return
	var origin := -get_viewport().get_canvas_transform().origin
	var rect := Rect2(origin-Vector2(80,80),get_viewport_rect().size+Vector2(160,160))
	for kind in pools:
		for cell in pools[kind]:
			if not rect.has_point(Vector2(cell)*40):
				continue
			var cap := capacity(cell)
			var amount := minf(cap,float(pools[kind][cell]))
			if amount<=.005:
				continue
			var surface := (float(cell.y)+cap-amount)*40
			draw_rect(Rect2(cell.x*40,surface,40,amount*40),COLORS[kind])
			if float(pools[kind].get(cell+Vector2i.UP,0))<.01:
				draw_line(Vector2(cell.x*40,surface),Vector2(cell.x*40+40,surface),COLORS[kind].lightened(.3),2)
	for cell in hazards:
		if rect.has_point(Vector2(cell)*40) and hazards[cell]=="swamp":
			draw_rect(Rect2(Vector2(cell)*40,Vector2(40,7)),Color(.32,.45,.15,.7))
