extends Node2D

var world: Node2D
var player: CharacterBody2D
var catalog: Dictionary
var profile_id := ""
var biome := "plains"
var clock := 0.0

func _ready() -> void:
	z_index = -100
	catalog = JSON.parse_string(FileAccess.get_file_as_string("res://data/backgrounds.json"))

func _process(delta: float) -> void:
	clock += delta
	queue_redraw()

func select_profile() -> String:
	var meta: Dictionary = world.map_data.get("metadata",{})
	var tile := player.position / 40.0
	biome = "plains"
	for span in meta.get("biome_spans",[]):
		if tile.x >= float(span[0]) and tile.x < float(span[1]):
			biome = str(span[2])
	if world.map_id == "editor_map":
		for row in meta.get("underground_biomes",[]):
			var b: Array = row.bounds
			if tile.x >= b[0] and tile.x <= b[2] and tile.y >= b[1] and tile.y <= b[3]:
				return str(catalog.theme_aliases.get(row.get("background_theme",row.get("type","")),""))
		if tile.y > float(meta.get("underground_start_row",122)):
			return "underground.underground_castle"
	if world.map_id == "wuxia_world":
		return ["wuxia_bamboo","wuxia_karst","wuxia_stalactite","wuxia_graveyard"][clampi(int(tile.x/96),0,3)]
	if world.map_id == "biolume_sky_world":
		return "biolume.sky_depth"
	return str(meta.get("background_profile",catalog.map_bindings.get(world.map_id,"")))

func _draw() -> void:
	if world == null or world.map_data.is_empty():
		return
	var view := get_viewport_rect().size
	var origin := -get_viewport().get_canvas_transform().origin
	profile_id = select_profile()
	if catalog.profiles.has(profile_id):
		var profile: Dictionary = catalog.profiles[profile_id]
		draw_rect(Rect2(origin,view),Color(profile.base_color))
		var remaining := int(profile.get("quad_budget",120))
		for layer in profile.layers:
			var spacing := float(layer.get("spacing_px",180))
			var phase := origin.x*float(layer.get("parallax",0.1))
			var first := floori(phase/spacing)-1
			var limit := mini(remaining,int(layer.get("max_quads",40)))
			var used := 0
			for slot in range(first,first+ceili(view.x/spacing)+3):
				var rng := RandomNumberGenerator.new()
				rng.seed = hash(profile_id+str(layer.id)+str(slot))
				var motif: Dictionary = layer.motifs[rng.randi_range(0,layer.motifs.size()-1)]
				var anchor := origin+Vector2(slot*spacing-phase+spacing*.5,view.y*float(layer.get("baseline_ratio",0.7)))
				anchor.y += rng.randf_range(-18,18)
				var scale_factor := float(motif.get("scale",1.0))*rng.randf_range(.85,1.3)
				var variants: Array = motif.get("parts_variants",[])
				if variants.is_empty():
					continue
				for part in variants[rng.randi_range(0,variants.size()-1)]:
					if used >= limit:
						break
					var rgba: Array = part[4]
					var color := Color(rgba[0],rgba[1],rgba[2],minf(.86,float(layer.get("alpha",.6))*float(rgba[3])))
					var size := Vector2(float(part[2]),float(part[3]))*scale_factor
					draw_rect(Rect2(anchor+Vector2(float(part[0]),float(part[1]))*scale_factor-size*.5,size),color)
					used += 1
			remaining -= used
	else:
		draw_surface(origin,view)

func draw_surface(origin: Vector2, view: Vector2) -> void:
	var colors := {"plains":Color("#7196ae"),"rainforest":Color("#466e70"),"swamp":Color("#657b75"),
		"ocean":Color("#759fb8"),"lake":Color("#80a8bc"),"desert":Color("#c9af86"),"snow_mountain":Color("#99b4c4"),"village":Color("#86a5ad")}
	draw_rect(Rect2(origin,view),colors.get(biome,Color("#7196ae")))
	draw_circle(origin+Vector2(view.x*.78,view.y*.2),28,Color("#f8dda4"))
	for layer in range(3):
		var spacing := 200.0+layer*45
		var phase := origin.x*(.025+.04*layer)
		var first := floori(phase/spacing)-1
		for slot in range(first,first+ceili(view.x/spacing)+3):
			var x := origin.x+slot*spacing-phase
			var y := origin.y+view.y*(.53+layer*.13)
			var peak := 75.0+float(posmod(slot*71,120))
			var tint := Color(.2+layer*.035,.34+layer*.025,.36+layer*.02,.5)
			draw_colored_polygon(PackedVector2Array([Vector2(x-80,y+140),Vector2(x+110,y-peak),Vector2(x+320,y+140)]),tint)
