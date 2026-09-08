extends RefCounted
# Shared authoring schema. Both runtime editors and Godot project content use it.
static var pack: Dictionary = {}
static var path := "user://content_pack.json"
static var source_cache: Dictionary = {}
static var revision := 0

static func initialize() -> void:
	if not pack.is_empty():
		return
	pack = {"schema":1,"maps":{},"assets":{},"creatures":{},"weapons":{},"actions":{},"custom":{}}
	for file in ["res://data/content_pack.json",path]:
		if FileAccess.file_exists(file):
			var data = preload("res://scripts/progress_store.gd").load_data(file,1)
			if data is Dictionary and int(data.get("schema",0))==1:
				for section in ["maps","assets","creatures","weapons","actions","custom"]:
					if data.get(section) is Dictionary:
						pack[section].merge(data[section],true)

static func resolve(section: String, base: Dictionary) -> Dictionary:
	initialize()
	var result := base.duplicate(true)
	for id in pack[section]:
		if result.get(id) is Dictionary and pack[section][id] is Dictionary:
			result[id].merge(pack[section][id],true)
		else:
			result[id] = pack[section][id].duplicate(true)
	return result

static func map_data(id: String) -> Dictionary:
	initialize()
	if pack.maps.has(id):
		return pack.maps[id].duplicate(true)
	return JSON.parse_string(FileAccess.get_file_as_string("res://data/maps/%s.json" % id))

static func asset(id: String) -> Dictionary:
	initialize()
	if pack.assets.has(id):
		return pack.assets[id]
	if not source_cache.has(id):
		var file := "res://assets/pixel_sources/"+id.replace(".","_")+".json"
		source_cache[id] = JSON.parse_string(FileAccess.get_file_as_string(file)) if FileAccess.file_exists(file) else {}
	return source_cache[id]

static func commit(section: String, id: String, data: Dictionary) -> Error:
	initialize()
	var previous = pack[section].get(id)
	pack[section][id] = data.duplicate(true)
	var result := preload("res://scripts/progress_store.gd").save_data(path,pack)
	if result!=OK:
		if previous==null:
			pack[section].erase(id)
		else:
			pack[section][id] = previous
	else:
		revision += 1
	return result

static func export_to(file: String) -> Error:
	initialize()
	return preload("res://scripts/progress_store.gd").save_data(file,pack)

static func commit_many(changes: Array) -> Error:
	initialize()
	var next := pack.duplicate(true)
	for change in changes:
		next[str(change.section)][str(change.id)] = change.data.duplicate(true)
	var result := preload("res://scripts/progress_store.gd").save_data(path,next)
	if result==OK:
		pack=next
		revision+=1
	return result

static func grid_texture(grid: Array) -> ImageTexture:
	var height := grid.size()
	var width := (grid[0] as Array).size() if height else 1
	var image := Image.create(width,maxi(1,height),false,Image.FORMAT_RGBA8)
	for y in range(height):
		for x in range(mini(width,grid[y].size())):
			image.set_pixel(x,y,Color(str(grid[y][x])))
	return ImageTexture.create_from_image(image)
