extends Node2D

const Art = preload("res://scripts/art.gd")
var world: Node2D
var custom := Node2D.new()
var ladder_cells: Dictionary = {}
var custom_count := 0
var solids: Dictionary = {}
var removed: Dictionary={}
func plant_key(layer: String,row: Array) -> String:
	return "%s:%d:%d" % [layer,int(row[0]),int(row[1])]

func solid_at(cell: Vector2i) -> bool:
	return solids.has(cell)

func _ready() -> void:
	z_index = 1
	add_child(custom)

func load_map() -> void:
	for child in custom.get_children():
		custom.remove_child(child)
		child.queue_free()
	ladder_cells.clear()
	solids.clear()
	custom_count = 0
	var definitions: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://assets/custom_map_assets.json")).assets
	definitions = preload("res://scripts/content_db.gd").resolve("custom",definitions)
	for cell in world.map_data.layers.get("custom_map",[]):
		var id := str(cell[2])
		var cfg: Dictionary = definitions.get(id,{})
		var role := str(cfg.get("role","passable"))
		var tile := Vector2i(int(cell[0]),int(cell[1]))
		var texture := Art.texture(id)
		if texture != null:
			var sprite := Art.make_sprite(id,Vector2(40,40))
			sprite.position = Vector2(tile)*40+Vector2(20,20)
			sprite.scale = Vector2(40,40)/texture.get_size() if id.begins_with("tile.") else Vector2(2.5,2.5)
			custom.add_child(sprite)
			custom_count += 1
		if role in ["ladder","climb_platform"]:
			ladder_cells[tile] = true
		if role=="solid":
			solids[tile] = true
		if role in ["solid","platform","climb_platform"]:
			var body := StaticBody2D.new()
			body.position = Vector2(tile)*40+Vector2(20,20)
			body.collision_layer = 8 if role != "solid" else 1
			body.collision_mask = 0
			var shape := CollisionShape2D.new()
			var rect := RectangleShape2D.new()
			rect.size = Vector2(40,40)
			shape.shape = rect
			shape.one_way_collision = role != "solid"
			body.add_child(shape)
			custom.add_child(body)
	queue_redraw()

func _process(_delta: float) -> void:
	queue_redraw()

func _draw() -> void:
	if world == null or world.map_data.is_empty():
		return
	var origin := -get_viewport().get_canvas_transform().origin
	var visible_rect := Rect2(origin-Vector2(200,250),get_viewport_rect().size+Vector2(400,500))
	for layer in ["vegetation","decoration"]:
		for row in world.map_data.layers.get(layer,[]):
			if removed.has(plant_key(layer,row)): continue
			var pos := Vector2((float(row[0])+.5)*40,float(row[1])*40)
			var tile: Vector2i = world.terrain.get_cell_atlas_coords(Vector2i(int(row[0]),int(row[1])))
			if tile.x>0 and world.definitions[str(tile.x)].get("solid",false):
				pos.y += 0.0 if (tile.y & 4) else (40.0/3 if (tile.y & 2) else 80.0/3)
			if not visible_rect.has_point(pos):
				continue
			var kind := str(row[2])
			var scale_factor := .8+float(row[3] if row.size()>3 else 1)*.25
			var texture := Art.texture("decoration."+kind)
			if texture == null:
				texture = Art.texture("plant."+kind)
			if texture != null:
				var size := texture.get_size()*2.5
				draw_texture_rect(texture,Rect2(pos-Vector2(size.x*.5,size.y),size),false)
				continue
			if kind in ["tree","pine","house"]:
				draw_rect(Rect2(pos+Vector2(-6,-95)*scale_factor,Vector2(12,95)*scale_factor),Color("#5d4837"))
				draw_rect(Rect2(pos+Vector2(-32,-125)*scale_factor,Vector2(64,60)*scale_factor),Color("#456f48") if kind!="house" else Color("#a78c69"))
			elif kind in ["grass","fern","reed","cactus"]:
				for i in range(3):
					draw_line(pos+Vector2(i*6-6,0),pos+Vector2(i*11-11,-18-i*5)*scale_factor,Color("#5d8c50"),4)
			elif kind == "stone":
				draw_rect(Rect2(pos+Vector2(-12,-13)*scale_factor,Vector2(24,13)*scale_factor),Color("#929792"))
			elif kind in ["crystal","glow_moss","lamp"]:
				draw_rect(Rect2(pos+Vector2(-4,-22),Vector2(8,22)),Color("#7de2d7"))
			elif kind == "fence":
				draw_rect(Rect2(pos+Vector2(-20,-20),Vector2(40,6)),Color("#927552"))
