extends CanvasLayer
var game: Node2D
var overlay := Control.new()
var column := VBoxContainer.new()
var settings := VBoxContainer.new()
var resume_button := Button.new()
var load_button := Button.new()
var new_confirm:=ConfirmationDialog.new()

func _ready() -> void:
	layer = 30
	process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(overlay)
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.theme = game.safe_ui.theme
	var dim := ColorRect.new()
	dim.color = Color(.025,.055,.08,.97)
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(dim)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(center)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(550,640)
	center.add_child(scroll)
	scroll.add_child(column)
	column.custom_minimum_size.x = 520
	column.add_theme_constant_override("separation",16)
	var title := Label.new()
	title.text = "PytoRPG\n冒險旅程"
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title.add_theme_font_size_override("font_size",46)
	column.add_child(title)
	resume_button.text = "繼續冒險"
	resume_button.pressed.connect(close)
	column.add_child(resume_button)
	var new_button := Button.new()
	new_button.text = "新開遊戲"
	column.add_child(new_button)
	var confirm := new_confirm
	confirm.dialog_text = "開始新的冒險並取代遊戲存檔？學習進度會保留。"
	confirm.title = "新開遊戲"
	confirm.confirmed.connect(func(): start(false))
	column.add_child(confirm)
	new_button.pressed.connect(request_new)
	load_button.text = "載入遊戲"
	load_button.pressed.connect(func(): start(true))
	column.add_child(load_button)
	var options := Button.new()
	options.text = "遊戲設定"
	options.pressed.connect(func(): settings.visible = not settings.visible)
	column.add_child(options)
	var map_button := Button.new()
	map_button.text = "地圖編輯器"
	map_button.pressed.connect(func(): game.map_editor.open())
	column.add_child(map_button)
	var developer_button:=Button.new()
	developer_button.text="開發工具列（地圖／測試／武器）"
	developer_button.pressed.connect(func(): game.desktop_panel.visible=not game.desktop_panel.visible; close())
	column.add_child(developer_button)
	var asset_button := Button.new()
	asset_button.text = "素材／動畫／特性編輯器"
	asset_button.pressed.connect(func(): game.asset_editor.open())
	column.add_child(asset_button)
	column.add_child(settings)
	settings.hide()
	var touch := CheckButton.new()
	touch.text = "顯示觸控按鈕"
	touch.button_pressed = bool(game.preferences.get("touch",true))
	game.controls.visible = touch.button_pressed
	touch.toggled.connect(func(on: bool): game.controls.visible=on; game.preferences.touch=on; game.save_preferences())
	settings.add_child(touch)
	for key in ["death_handling","starter_items"]:
		var toggle := CheckButton.new()
		toggle.text = "死亡懲罰（關閉保留最後 1 HP）" if key=="death_handling" else "預設物品（新遊戲贈送穿戴裝備）"
		toggle.button_pressed = bool(game.preferences.get(key,true))
		toggle.toggled.connect(func(on: bool):
			game.preferences[key]=on
			if key=="death_handling" and not on and game.player.hp<=0:
				game.player.hp=1
			game.save_preferences())
		settings.add_child(toggle)
	var practice := OptionButton.new()
	practice.add_item("習題：每類 2 題／3 分鐘")
	practice.add_item("習題：每類 5 題／15 分鐘")
	practice.add_item("習題：每類 10 題／30 分鐘")
	practice.select(game.study.model.profile)
	practice.item_selected.connect(func(i: int): game.study.model.profile=i; game.study.model.checkpoint())
	settings.add_child(practice)
	for child in column.get_children():
		if child is Button:
			child.custom_minimum_size.y = 56
			child.add_theme_font_size_override("font_size",24)
	if game.test_mode:
		overlay.hide()
	else:
		open()

func open() -> void:
	game.release_inputs()
	resume_button.visible = game.session_started
	load_button.disabled = game.read_progress().is_empty()
	overlay.show()
	get_tree().paused = true

func close() -> void:
	overlay.hide()
	game.release_inputs()
	get_tree().paused = false

func start(load_existing: bool) -> void:
	game.start_session(load_existing)
	overlay.hide()
	game.study.open_round("entry")

func request_new() -> void:
	open()
	if not game.read_progress().is_empty() or game.session_started:
		new_confirm.popup_centered(Vector2i(560,160))
	else: start(false)
