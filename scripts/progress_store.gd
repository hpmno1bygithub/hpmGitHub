extends RefCounted

static func load_data(path: String, schema: int) -> Dictionary:
	for candidate in [path,path+".bak"]:
		var data := parse(candidate)
		if int(data.get("schema",-1)) == schema:
			return data
	return {}

static func parse(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var parser := JSON.new()
	if parser.parse(FileAccess.get_file_as_string(path)) == OK and parser.data is Dictionary:
		return parser.data
	return {}

static func save_data(path: String, data: Dictionary) -> Error:
	var file := FileAccess.open(path+".tmp",FileAccess.WRITE)
	if file == null:
		return FileAccess.get_open_error()
	file.store_string(JSON.stringify(data))
	file.flush()
	file.close()
	if not parse(path).is_empty():
		var copied := DirAccess.copy_absolute(path,path+".bak")
		if copied != OK:
			return copied
	return DirAccess.rename_absolute(path+".tmp",path)
