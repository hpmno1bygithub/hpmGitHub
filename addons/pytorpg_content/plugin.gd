@tool
extends EditorPlugin
var dock: VBoxContainer
var dialog: EditorFileDialog
var status: Label
func _enter_tree() -> void:
	dock=VBoxContainer.new()
	dock.name="PytoRPG"
	var title:=Label.new()
	title.text="遊戲內容工作流程"
	dock.add_child(title)
	var import_button:=Button.new()
	import_button.text="匯入遊戲編輯器內容包"
	import_button.pressed.connect(func(): dialog.popup_centered(Vector2i(900,550)))
	dock.add_child(import_button)
	status=Label.new()
	status.custom_minimum_size=Vector2(260,130)
	status.autowrap_mode=TextServer.AUTOWRAP_WORD_SMART
	status.text="F5 開啟遊戲 → 素材／地圖編輯 → 匯出內容包 → 在這裡匯入。內容以 JSON 保存，可納入 Git 版本控制。"
	dock.add_child(status)
	dialog=EditorFileDialog.new()
	dialog.access=EditorFileDialog.ACCESS_FILESYSTEM
	dialog.file_mode=EditorFileDialog.FILE_MODE_OPEN_FILE
	dialog.add_filter("*.json","PytoRPG 內容包")
	dialog.file_selected.connect(import_pack)
	dock.add_child(dialog)
	add_control_to_dock(DOCK_SLOT_RIGHT_UL,dock)
func import_pack(file: String) -> void:
	var parser:=JSON.new()
	var error:=parser.parse(FileAccess.get_file_as_string(file))
	if error!=OK or not parser.data is Dictionary or int(parser.data.get("schema",0))!=1:
		status.text="無效的內容包；未改動專案。"
		return
	var data: Dictionary=parser.data
	var errors:=preload("res://scripts/content_validate.gd").validate(data)
	if not errors.is_empty():
		status.text="驗證失敗："+str(errors[0])
		return
	var target:="res://data/content_pack.json"
	var current: Dictionary={}
	if FileAccess.file_exists(target):
		var old=JSON.parse_string(FileAccess.get_file_as_string(target))
		if old is Dictionary: current=old
	current.schema=1
	for section in ["maps","assets","creatures","weapons","actions","custom"]:
		if not current.has(section): current[section]={}
		current[section].merge(data.get(section,{}),true)
	var result:=preload("res://scripts/progress_store.gd").save_data(target,current)
	status.text="已匯入 data/content_pack.json；同名舊內容已備份為 .bak。F5 測試後將檔案提交至 Git。" if result==OK else "寫入失敗："+error_string(result)
	get_editor_interface().get_resource_filesystem().scan()
func _exit_tree() -> void:
	remove_control_from_docks(dock)
	dock.queue_free()
