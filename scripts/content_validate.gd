extends RefCounted
static func validate(data: Dictionary) -> Array[String]:
	var errors: Array[String]=[]
	if int(data.get("schema",0))!=1: errors.append("schema 必須為 1")
	for section in ["maps","assets","creatures","weapons","actions","custom"]:
		if not data.get(section,{}) is Dictionary:
			errors.append(section+" 必須為物件")
	if not errors.is_empty(): return errors
	var creatures: Dictionary=JSON.parse_string(FileAccess.get_file_as_string("res://data/creatures.json"))
	creatures.merge(data.get("creatures",{}),true)
	var pixels: Dictionary=JSON.parse_string(FileAccess.get_file_as_string("res://data/pixel_manifest.json"))
	pixels.merge(data.get("assets",{}),true)
	var tiles: Dictionary=JSON.parse_string(FileAccess.get_file_as_string("res://data/tiles.json"))
	for id in data.get("assets",{}):
		var asset=data.assets[id]
		if not asset is Dictionary or not asset.get("pixels") is Array:
			errors.append(str(id)+" 缺少像素陣列")
			continue
		if not valid_grid(asset.pixels): errors.append(str(id)+" 像素尺寸或色碼無效")
		if not asset.get("animations",{}) is Dictionary:
			errors.append(str(id)+" 動畫表格式無效")
			continue
		for state in asset.get("animations",{}):
			var info=asset.animations[state]
			if not info is Dictionary or not info.get("frames") is Array or info.frames.is_empty() or info.frames.size()>256:
				errors.append(str(id)+" 動畫影格無效")
				continue
			if float(info.get("fps",0))<=0 or float(info.get("fps",0))>120: errors.append(str(id)+" FPS 無效")
			for grid in info.frames:
				if not grid is Array or not valid_grid(grid): errors.append(str(id)+" 動畫像素無效")
	for id in data.get("maps",{}):
		var map=data.maps[id]
		if not map is Dictionary or not map.get("cells") is Array or not map.get("layers") is Dictionary or not map.get("size") is Array or map.size.size()!=2:
			errors.append(str(id)+" 地圖格式無效")
			continue
		for cell in map.cells:
			if not cell is Array or cell.size()!=4:
				errors.append(str(id)+" 圖塊格式無效")
				break
			if int(cell[0])<0 or int(cell[1])<0 or int(cell[0])>=int(map.size[0]) or int(cell[1])>=int(map.size[1]) or int(cell[3])<0 or int(cell[3])>7:
				errors.append(str(id)+" 圖塊超出邊界")
				break
			if not tiles.has(str(int(cell[2]))):
				errors.append(str(id)+" 未知圖塊 ID")
				break
		for row in map.get("population",[]):
			if not row is Dictionary or not creatures.has(str(row.get("species",""))):
				errors.append(str(id)+" 生物 ID 無法解析")
				break
		for row in map.layers.get("custom_map",[]):
			if not row is Array or row.size()<3 or not pixels.has(str(row[2])):
				errors.append(str(id)+" 自訂素材 ID 無法解析")
				break
	for id in data.get("custom",{}):
		var row=data.custom[id]
		if not row is Dictionary or not row.get("role","passable") in ["solid","platform","ladder","climb_platform","passable"]:
			errors.append(str(id)+" 碰撞角色無效")
	return errors
static func valid_grid(grid: Array) -> bool:
	if grid.is_empty() or grid.size()>192 or not grid[0] is Array: return false
	var width: int=grid[0].size()
	if width<1 or width>192: return false
	for row in grid:
		if not row is Array or row.size()!=width: return false
		for pixel in row:
			if not pixel is String or not Color.html_is_valid(pixel): return false
	return true
