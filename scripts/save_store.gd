extends RefCounted

const PATH := "user://save_v1.json"
const BACKUP := "user://save_v1.backup.json"

static func read_save(primary: String = PATH, backup: String = BACKUP) -> Dictionary:
	for path in [primary, backup]:
		if not FileAccess.file_exists(path):
			continue
		var parser := JSON.new()
		if parser.parse(FileAccess.get_file_as_string(path)) != OK:
			continue
		var data = parser.data
		if data is Dictionary and data.get("schema") == 1 and data.get("map") is String and data.get("position") is Array:
			var pos: Array = data.position
			if pos.size() == 2 and (pos[0] is float or pos[0] is int) and (pos[1] is float or pos[1] is int):
				if is_finite(float(pos[0])) and is_finite(float(pos[1])):
					return data
	return {}

static func write_save(map_id: String, pos: Vector2, primary: String = PATH, backup: String = BACKUP) -> Error:
	var temp := primary + ".tmp"
	var file := FileAccess.open(temp, FileAccess.WRITE)
	if file == null:
		return FileAccess.get_open_error()
	file.store_string(JSON.stringify({"schema":1, "map":map_id, "position":[pos.x,pos.y]}))
	file.flush()
	file.close()
	# A corrupt primary must never replace the last known good backup.
	if not read_save(primary, primary).is_empty():
		var copied := DirAccess.copy_absolute(primary, backup)
		if copied != OK:
			return copied
	return DirAccess.rename_absolute(temp, primary)
