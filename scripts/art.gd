extends RefCounted

static var animations: Dictionary = {}
static var pixels: Dictionary = {}
static var frames_cache: Dictionary = {}
static var anchors: Dictionary = {}
static var texture_cache: Dictionary = {}
static var revision := -1
const Content = preload("res://scripts/content_db.gd")

static func initialize() -> void:
	Content.initialize()
	if revision!=Content.revision:
		revision = Content.revision
		frames_cache.clear()
		texture_cache.clear()
	if pixels.is_empty():
		pixels = JSON.parse_string(FileAccess.get_file_as_string("res://data/pixel_manifest.json"))
		animations = JSON.parse_string(FileAccess.get_file_as_string("res://data/animations.json"))
		anchors = JSON.parse_string(FileAccess.get_file_as_string("res://data/anchors.json"))

static func texture(id: String) -> Texture2D:
	initialize()
	if not texture_cache.has(id) and pixels.has(id):
		texture_cache[id] = Content.grid_texture(Content.pack.assets[id].pixels) if Content.pack.assets.has(id) else load(pixels[id])
	elif not texture_cache.has(id) and Content.pack.assets.has(id):
		texture_cache[id] = Content.grid_texture(Content.pack.assets[id].pixels)
	return texture_cache.get(id)

static func make_sprite(id: String, body_size := Vector2(40,40)) -> AnimatedSprite2D:
	initialize()
	var sprite := AnimatedSprite2D.new()
	if not frames_cache.has(id):
		var frames := SpriteFrames.new()
		frames.remove_animation("default")
		if Content.pack.assets.has(id):
			var source: Dictionary = Content.pack.assets[id]
			for state in source.get("animations",{}):
				var info: Dictionary = source.animations[state]
				frames.add_animation(state)
				frames.set_animation_speed(state,float(info.get("fps",8)))
				frames.set_animation_loop(state,bool(info.get("loop",true)))
				for grid in info.frames:
					frames.add_frame(state,Content.grid_texture(grid))
			if not frames.has_animation("idle"):
				frames.add_animation("idle")
				frames.add_frame("idle",texture(id))
		elif animations.has(id):
			var entry: Dictionary = animations[id]
			var sheet: Texture2D = load(entry.texture)
			for state in entry.states:
				var config: Dictionary = entry.states[state]
				frames.add_animation(state)
				frames.set_animation_speed(state, float(config.fps))
				frames.set_animation_loop(state, bool(config.loop))
				for raw_index in config.indices:
					var index := int(raw_index)
					var region := AtlasTexture.new()
					region.atlas = sheet
					region.region = Rect2((index % int(entry.columns))*int(entry.width),
						floori(float(index)/float(entry.columns))*int(entry.height),int(entry.width),int(entry.height))
					frames.add_frame(state,region)
		else:
			frames.add_animation("idle")
			var base := texture(id)
			if base != null:
				frames.add_frame("idle",base)
		frames_cache[id] = frames
	sprite.sprite_frames = frames_cache[id]
	if animations.has(id):
		var entry: Dictionary = animations[id]
		sprite.scale = Vector2.ONE * float(entry.scale)
		if entry.mapping == "fit_world_box":
			sprite.scale = body_size / Vector2(float(entry.width),float(entry.height))
	else:
		sprite.scale = Vector2(2.5,2.5)
	if Content.pack.assets.has(id):
		var source: Dictionary=Content.pack.assets[id]
		var grid: Array=source.pixels
		var foot:=0
		for y in range(grid.size()):
			for pixel in grid[y]:
				if Color(str(pixel)).a>0: foot=y+1
		sprite.scale=Vector2.ONE*float(source.get("pixel_world_scale",2.5))
		if source.get("pixel_mapping","")=="fit_world_box":
			sprite.scale=body_size/Vector2(grid[0].size(),grid.size())
		sprite.position.y=body_size.y*.5-(foot-grid.size()*.5)*sprite.scale.y
		play(sprite,"idle")
		return sprite
	if anchors.has(id):
		var anchor: Dictionary = anchors[id]
		sprite.position.y = body_size.y*.5-(float(anchor.foot_y)-float(anchor.height)*.5)*sprite.scale.y
	play(sprite,"idle")
	return sprite

static func play(sprite: AnimatedSprite2D, state: String) -> void:
	var options := [state, "move" if state in ["walk","run"] else "idle", "idle"]
	for option in options:
		if sprite.sprite_frames.has_animation(option):
			# A non-looping posture clip must hold its final frame, not restart.
			if sprite.animation != option:
				sprite.play(option)
			return
