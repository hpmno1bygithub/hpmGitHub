extends Node2D

const TILE := 40
var terrain := TileMapLayer.new()
var definitions: Dictionary
var map_data: Dictionary
var map_id := ""
var dimensions := Vector2i.ZERO
var scenery: Node2D
var water: Dictionary = {}
var walls := TileMapLayer.new()
var edits: Dictionary = {}

func _ready() -> void:
	definitions = JSON.parse_string(FileAccess.get_file_as_string("res://data/tiles.json"))
	var tiles := TileSet.new()
	tiles.tile_size = Vector2i(TILE, TILE)
	tiles.add_physics_layer()
	tiles.set_physics_layer_collision_layer(0, 1)
	tiles.add_physics_layer()
	tiles.set_physics_layer_collision_layer(1, 8)
	var atlas := TileSetAtlasSource.new()
	atlas.texture = load("res://assets/converted/terrain_atlas.png")
	atlas.texture_region_size = Vector2i(TILE, TILE)
	tiles.add_source(atlas, 0)
	for key in definitions:
		var definition: Dictionary = definitions[key]
		for mask in range(1, 8):
			var coord := Vector2i(int(key), mask)
			atlas.create_tile(coord)
			if not definition.get("solid", false):
				continue
			var td := atlas.get_tile_data(coord, 0)
			var layer := 1 if definition.get("one_way_platform",false) else 0
			for segment in range(3):
				if (mask & (4 >> segment)) == 0:
					continue
				var top := -20.0 + segment * (40.0 / 3.0)
				var bottom := top + 40.0 / 3.0
				var index := td.get_collision_polygons_count(layer)
				td.add_collision_polygon(layer)
				td.set_collision_polygon_points(layer, index, PackedVector2Array([
					Vector2(-20, top), Vector2(20, top), Vector2(20, bottom), Vector2(-20, bottom)]))
				td.set_collision_polygon_one_way(layer, index, definition.get("one_way_platform", false))
	terrain.tile_set = tiles
	walls.tile_set = tiles
	walls.collision_enabled = false
	walls.modulate = Color(.53,.53,.53,1)
	walls.z_index = -10
	add_child(walls)
	add_child(terrain)
	refresh_tile_art()

func load_map(id: String) -> void:
	map_id = id
	map_data = preload("res://scripts/content_db.gd").map_data(id)
	dimensions = Vector2i(int(map_data.size[0]), int(map_data.size[1]))
	terrain.clear()
	walls.clear()
	edits.clear()
	water.clear()
	for cell in map_data.layers.get("water",[]):
		water[Vector2i(int(cell[0]),int(cell[1]))] = float(cell[2])
	for cell in map_data.cells:
		if int(cell[3]) != 0:
			terrain.set_cell(Vector2i(int(cell[0]), int(cell[1])), 0, Vector2i(int(cell[2]), int(cell[3])))
	terrain.update_internals()
	build_walls()
	queue_redraw()

func build_walls() -> void:
	walls.clear()
	for cell in terrain.get_used_cells():
		var tile := terrain.get_cell_atlas_coords(cell)
		if not definitions[str(tile.x)].get("solid",false) or definitions[str(tile.x)].get("one_way_platform",false):
			continue
		# Exposed surface keeps its exact three-slice outline; interior slices get a backing wall.
		var above := terrain.get_cell_atlas_coords(cell+Vector2i.UP)
		var mask := 7 if above.x>0 and definitions[str(above.x)].get("solid",false) else tile.y
		walls.set_cell(cell,0,Vector2i(tile.x,mask))
	for row in map_data.layers.get("background_wall",[]):
		walls.set_cell(Vector2i(int(row[0]),int(row[1])),0,Vector2i(int(row[2]),int(row[3])))

func edit_cell(cell: Vector2i, id: int, mask: int = 7, background: bool = false) -> void:
	if cell.x<0 or cell.y<0 or cell.x>=dimensions.x or cell.y>=dimensions.y:
		return
	var target := walls if background else terrain
	if id<=0 or mask<=0:
		target.erase_cell(cell)
	else:
		target.set_cell(cell,0,Vector2i(id,mask))
	if not background:
		edits["%d,%d" % [cell.x,cell.y]] = [cell.x,cell.y,id,mask]
	target.update_internals()
	queue_redraw()

func snapshot_edits() -> Array:
	return edits.values().duplicate(true)

func restore_edits(rows: Array) -> void:
	for row in rows:
		edit_cell(Vector2i(int(row[0]),int(row[1])),int(row[2]),int(row[3]))

func authored_snapshot() -> Dictionary:
	var result := map_data.duplicate(true)
	result.cells = []
	for cell in terrain.get_used_cells():
		var tile := terrain.get_cell_atlas_coords(cell)
		result.cells.append([cell.x,cell.y,tile.x,tile.y])
	result.layers.background_wall = []
	for cell in walls.get_used_cells():
		var tile := walls.get_cell_atlas_coords(cell)
		result.layers.background_wall.append([cell.x,cell.y,tile.x,tile.y])
	return result

func spawn_point() -> Vector2:
	return Vector2((float(map_data.spawn[0]) + 0.5) * TILE, float(map_data.spawn[1]) * TILE - 21)

func is_ladder(at: Vector2) -> bool:
	if scenery != null and scenery.ladder_cells.has(Vector2i(floori(at.x/40),floori(at.y/40))):
		return true
	var tile := terrain.get_cell_atlas_coords(terrain.local_to_map(at))
	return tile.x >= 0 and definitions[str(tile.x)].get("ladder", false)

func has_water(at: Vector2) -> bool:
	return float(water.get(Vector2i(floori(at.x/40),floori(at.y/40)),0))>.15

func find_floor(at: Vector2, body_height: float) -> Vector2:
	var x := clampi(floori(at.x/40),0,dimensions.x-1)
	for y in range(maxi(0,floori((at.y+body_height*.5)/40)-2),mini(dimensions.y,floori(at.y/40)+15)):
		var tile := terrain.get_cell_atlas_coords(Vector2i(x,y))
		if tile.x>0 and definitions[str(tile.x)].get("solid",false):
			var top := 0.0 if (tile.y & 4) else (40.0/3 if (tile.y & 2) else 80.0/3)
			return Vector2(at.x,y*40.0+top-body_height*.5-.5)
	return at

func clear_sight(from: Vector2, to: Vector2) -> bool:
	var ray := PhysicsRayQueryParameters2D.create(from,to,9)
	return get_world_2d().direct_space_state.intersect_ray(ray).is_empty()

func _draw() -> void:
	pass

func refresh_tile_art() -> void:
	var db = preload("res://scripts/content_db.gd")
	db.initialize()
	var atlas: TileSetAtlasSource = terrain.tile_set.get_source(0)
	var image: Image = load("res://assets/converted/terrain_atlas.png").get_image()
	image.convert(Image.FORMAT_RGBA8)
	for key in definitions:
		var id := "tile."+str(definitions[key].name)
		if not db.pack.assets.has(id): continue
		var source: Dictionary = db.pack.assets[id]
		var tile_image := db.grid_texture(source.pixels).get_image()
		tile_image.resize(40,40,Image.INTERPOLATE_NEAREST)
		for mask_value in range(1,8):
			for y in range(40):
				var segment := mini(2,floori(y*3.0/40))
				for x in range(40):
					image.set_pixel(int(key)*40+x,mask_value*40+y,tile_image.get_pixel(x,y) if mask_value&(4>>segment) else Color.TRANSPARENT)
	atlas.texture = ImageTexture.create_from_image(image)
