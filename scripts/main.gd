extends Node2D

const World = preload("res://scripts/world.gd")
const Player = preload("res://scripts/player.gd")
const Saves = preload("res://scripts/save_store.gd")
const Progress = preload("res://scripts/progress_store.gd")
const Gameplay = preload("res://scripts/gameplay.gd")
const Scenery = preload("res://scripts/scenery.gd")
const Background = preload("res://scripts/background.gd")
const Study = preload("res://scripts/study_ui.gd")
var gameplay := Gameplay.new()
var scenery := Scenery.new()
var liquids := preload("res://scripts/liquids.gd").new()
var backpack: CanvasLayer
var asset_editor: CanvasLayer
var map_editor: CanvasLayer
var boss_combat := preload("res://scripts/boss_combat.gd").new()
var actions := preload("res://scripts/action_controller.gd").new()
var magic := preload("res://scripts/magic.gd").new()
var environment := preload("res://scripts/environment.gd").new()
var aim_pad := preload("res://scripts/aim_pad.gd").new()
var tool_system := preload("res://scripts/tools.gd").new()
var backdrop := Background.new()
var study: CanvasLayer
var hud := Label.new()
var world := World.new()
var player := Player.new()
var maps: Array
var status := Label.new()
var controls := Node2D.new()
var safe_ui := Control.new()
var desktop_panel: PanelContainer
var mobile_hud:=preload("res://scripts/mobile_hud.gd").new()
var chooser := OptionButton.new()
var touch_buttons: Array[TouchScreenButton] = []
var test_mode := false
var session_started := false
var preferences: Dictionary = {}
var menu: CanvasLayer
var progress_path := "user://progress_v2.json"

func read_progress() -> Dictionary:
	var save := Progress.load_data(progress_path,2)
	return Saves.read_save() if save.is_empty() and not test_mode else save

func save_preferences() -> void:
	if not test_mode:
		Progress.save_data("user://settings_v1.json",preferences.merged({"schema":1},true))

func start_session(load_existing: bool) -> void:
	var save := read_progress() if load_existing else {}
	gameplay.current_map = ""
	gameplay.sessions = save.get("sessions",{})
	gameplay.inventory = save.get("inventory",{"weapon_sword":{"name":"鐵劍","count":1},"tool_grapple":{"name":"鉤爪","count":1}})
	if save.has("backpack"):
		gameplay.bag.restore(save.backpack)
	elif save.is_empty() and bool(preferences.get("starter_items",true)):
		var items: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://assets/equipment_catalog.json")).items
		for id in items:
			if id!="equipment.wind_god_wings":
				gameplay.give_item(id,str(items[id].display_name_zh),1)
	change_map(str(save.get("map","editor_map")) if maps.has(save.get("map","editor_map")) else "editor_map")
	if save.has("position"):
		var point := Vector2(float(save.position[0]),float(save.position[1]))
		if point.x>=12 and point.x<world.dimensions.x*40-12 and point.y>=0 and point.y<world.dimensions.y*40:
			player.position = point
	player.hp = clampf(float(save.get("hp",100)),0,100)
	player.set_posture("stand")
	player.equipped = "sword"
	gameplay.equip(str(save.get("equipped","sword")))
	actions.select_mode(str(save.get("action_mode","weapon")))
	tool_system.selected=str(save.get("tool","pickaxe")) if str(save.get("tool","pickaxe")) in tool_system.ORDER else "pickaxe"
	magic.selected=str(save.get("magic","fireball")) if str(save.get("magic","fireball")) in magic.ORDER else "fireball"
	magic.mana=clampf(float(save.get("mana",100)),0,100)
	session_started = true
	save_game()


func _ready() -> void:
	register_inputs()
	maps = JSON.parse_string(FileAccess.get_file_as_string("res://data/maps.json"))
	maps.append_array(["control_lab","thermal_lab"])
	add_child(world)
	player.world = world
	add_child(player)
	scenery.world = world
	world.scenery = scenery
	add_child(scenery)
	backdrop.world = world
	backdrop.player = player
	add_child(backdrop)
	gameplay.game = self
	add_child(gameplay)
	liquids.game = self
	add_child(liquids)
	tool_system.game=self
	add_child(tool_system)
	actions.game=self
	add_child(actions)
	magic.game=self
	add_child(magic)
	environment.game=self
	add_child(environment)
	boss_combat.game=self
	add_child(boss_combat)
	build_ui()
	backpack = preload("res://scripts/inventory_ui.gd").new()
	backpack.game = self
	add_child(backpack)
	session_started = test_mode
	change_map("editor_map")
	player.attack_requested.connect(gameplay.attack)
	player.interact_requested.connect(func(): gameplay.call_deferred("interact"))
	player.died.connect(func(): save_game(); study.call_deferred("open_round","death"))
	study = Study.new()
	study.game = self
	add_child(study)
	preferences = Progress.load_data("user://settings_v1.json",1) if not test_mode else {}
	menu = preload("res://scripts/title_menu.gd").new()
	menu.game = self
	add_child(menu)
	asset_editor = preload("res://scripts/asset_workbench.gd").new()
	asset_editor.game = self
	add_child(asset_editor)
	map_editor = preload("res://scripts/map_workbench.gd").new()
	map_editor.game = self
	add_child(map_editor)
	var timer := Timer.new()
	timer.wait_time = 20
	timer.autostart = true
	timer.timeout.connect(save_game)
	add_child(timer)
	get_viewport().size_changed.connect(layout_ui)
	layout_ui()

func register_inputs() -> void:
	var keys := {"move_left":[KEY_A,KEY_LEFT],"move_right":[KEY_D,KEY_RIGHT],"jump":[KEY_SPACE],
		"run":[],"roll":[KEY_SHIFT],"crouch":[KEY_C],"prone":[KEY_Z],"climb":[KEY_W,KEY_UP],"down":[KEY_S,KEY_DOWN],"attack":[KEY_J],"interact":[KEY_E],"mine":[KEY_F],"grapple":[KEY_Q],"mode_cycle":[KEY_TAB],"mode_weapon":[KEY_1],"mode_magic":[KEY_2],"mode_tool":[KEY_3],"tool_cycle":[KEY_T],"magic_cycle":[KEY_R],"weapon_cycle":[KEY_V],"aim_left":[KEY_U,KEY_KP_4],"aim_right":[KEY_O,KEY_KP_6],"aim_up":[KEY_I,KEY_KP_8],"aim_down":[KEY_K,KEY_KP_2]}
	for action in keys:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
		for key in keys[action]:
			var event := InputEventKey.new()
			event.physical_keycode = key
			InputMap.action_add_event(action,event)

func change_map(id: String) -> void:
	actions.cancel()
	actions.explicit_aim=false
	magic.clear()
	boss_combat.effects.clear()
	boss_combat.visuals.clear()
	boss_combat.specials.clear()
	tool_system.damage.clear()
	tool_system.detach()
	for action in ["move_left","move_right","jump","run","climb","down"]:
		Input.action_release(action)
	gameplay.capture_map()
	world.load_map(id)
	scenery.load_map()
	gameplay.load_map()
	liquids.load_map(gameplay.sessions.get(id,{}).get("liquids",{}))
	scenery.removed=gameplay.sessions.get(id,{}).get("removed_plants",{}).duplicate()
	environment.restore(gameplay.sessions.get(id,{}).get("environment",world.map_data.metadata.get("initial_environment",{})))
	player.position = world.spawn_point()
	player.velocity = Vector2.ZERO
	player.climbing = false
	player.drop_left = 0
	player.roll_left = 0
	player.charge = 0
	player.swing_left = 0
	player.set_posture("stand")
	chooser.select(maps.find(id))
	status.text = "已進入 "+id+" · J 揮劍／E 互動"

func save_game() -> void:
	if test_mode or not session_started or world.map_id.is_empty():
		return
	gameplay.capture_map()
	var result := Progress.save_data(progress_path,{"schema":2,"map":world.map_id,
		"position":[player.position.x,player.position.y],"hp":player.hp,"equipped":player.equipped,"inventory":gameplay.inventory,"backpack":gameplay.bag.snapshot(),"action_mode":actions.mode,"tool":tool_system.selected,"magic":magic.selected,"mana":magic.mana,"sessions":gameplay.sessions})
	if result!=OK:
		status.text = "存檔失敗："+error_string(result)

func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_PAUSED or what == NOTIFICATION_WM_CLOSE_REQUEST:
		save_game()
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		release_inputs()

func release_inputs() -> void:
	actions.cancel()
	aim_pad.reset()
	mobile_hud.move_pad.reset()
	player.touch_down_pressed=false
	for action in ["move_left","move_right","jump","run","climb","down","attack","interact","roll","crouch","prone","mine","grapple","mode_cycle","tool_cycle","magic_cycle","weapon_cycle","mode_weapon","mode_magic","mode_tool","aim_left","aim_right","aim_up","aim_down"]:
		player.charge = 0
		Input.action_release(action)

func _process(_delta: float) -> void:
	if gameplay.current_map.is_empty():
		return
	aim_pad.visible=controls.visible
	hud.text = "生命 %d/100　魔力 %d/100　%s　背包 %d 種　%s" % [int(player.hp),int(magic.mana),actions.label(),gameplay.inventory.size(),gameplay.interaction_hint]
	if actions.mode_label!=null: actions.mode_label.text="動作\n"+{"weapon":"武","magic":"法","tool":"具"}[actions.mode]

func show_inventory() -> void:
	backpack.open()

func build_ui() -> void:
	var canvas := CanvasLayer.new()
	add_child(canvas)
	canvas.add_child(safe_ui)
	var theme := Theme.new()
	var font := SystemFont.new()
	font.font_names = PackedStringArray(["Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC"])
	theme.default_font = font
	safe_ui.theme = theme
	safe_ui.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var panel := PanelContainer.new()
	desktop_panel=panel
	panel.visible=false
	panel.position = Vector2(12,12)
	safe_ui.add_child(panel)
	var column := VBoxContainer.new()
	panel.add_child(column)
	column.add_child(status)
	column.add_child(hud)
	var row := HBoxContainer.new()
	column.add_child(row)
	for id in maps:
		chooser.add_item(id)
	chooser.item_selected.connect(func(index: int): change_map(maps[index]); save_game())
	row.add_child(chooser)
	var save_button := Button.new()
	save_button.text = "儲存"
	save_button.pressed.connect(save_game)
	row.add_child(save_button)
	var reset := Button.new()
	reset.text = "回出生點"
	reset.pressed.connect(func(): player.position = world.spawn_point(); player.velocity = Vector2.ZERO)
	row.add_child(reset)
	var inventory_button := Button.new()
	inventory_button.text = "背包"
	inventory_button.pressed.connect(show_inventory)
	row.add_child(inventory_button)
	var practice := Button.new()
	practice.text = "習題"
	practice.pressed.connect(func(): study.open_round("manual"))
	row.add_child(practice)
	var menu_button := Button.new()
	menu_button.text = "選單／設定"
	menu_button.pressed.connect(func(): save_game(); menu.open())
	row.add_child(menu_button)
	var edit_map := Button.new()
	edit_map.text = "編輯"
	edit_map.pressed.connect(func(): map_editor.open())
	row.add_child(edit_map)
	var equipment := OptionButton.new()
	equipment.add_item("裝備武器…")
	for id in gameplay.weapons:
		equipment.add_item(str(gameplay.weapons[id].name))
	equipment.item_selected.connect(func(i: int):
		if i>0:
			gameplay.equip(str(gameplay.weapons.keys()[i-1]))
			save_game()
		equipment.select(0))
	row.add_child(equipment)
	var explore := OptionButton.new()
	explore.add_item("探索地點…")
	explore.add_item("主世界・草原生物")
	explore.add_item("主世界・自訂魔物與寶箱")
	explore.add_item("武俠・竹林")
	explore.add_item("工具與戰鬥試驗場")
	explore.add_item("熱流體／踏階試驗場")
	explore.item_selected.connect(func(i: int):
		if i==0:
			return
		if i==5:
			change_map("thermal_lab")
			status.text="試驗場：1/3 階直接走、2/3 階跳躍；右方冰池可用火球融化，樹木可燃燒。"
			explore.select(0)
			return
		if i==4:
			change_map("control_lab")
			status.text="試驗場：右側樹木可砍，土石可挖，上方可飛鉤；背包可領測試武器，遠處首領受擊後反擊。"
			explore.select(0)
			return
		change_map("wuxia_world" if i==3 else "editor_map")
		var point := Vector2(428.5*40,114*40-21)
		if i==2:
			point = Vector2(522.5*40,168*40-21)
		elif i==3:
			point = Vector2(26.5*40,49*40-21)
		player.position = world.find_floor(point,40)
		player.velocity = Vector2.ZERO
		explore.select(0))
	row.add_child(explore)
	var hint := Label.new()
	hint.text = "A/D 移動 · Space 跳 · Shift 滾 · C 蹲 · Z 趴 · ↑站起／爬 · J 動作／蓄力 · E 互動\n滑鼠／右搖桿瞄準 · U/I/O/K 左上右下瞄準 · Tab 換模式 · F 採集 · Q 飛鉤"
	column.add_child(hint)
	var selectors:=HBoxContainer.new()
	column.add_child(selectors)
	for entry in [["1 武器",func(): actions.select_mode("weapon")],["2 魔法",func(): actions.select_mode("magic")],["3 工具",func(): actions.select_mode("tool")],["T 換工具",func(): tool_system.cycle(); actions.select_mode("tool")],["R 換魔法",func(): magic.cycle(); actions.select_mode("magic")],["V 換武器",actions.cycle_weapon]]:
		var button:=Button.new()
		button.text=entry[0]
		button.focus_mode=Control.FOCUS_NONE
		button.pressed.connect(entry[1])
		selectors.add_child(button)
	aim_pad.actions=actions
	safe_ui.add_child(aim_pad)
	safe_ui.add_child(controls)
	mobile_hud.game=self
	safe_ui.add_child(mobile_hud)

func layout_ui() -> void:
	var viewport_size := get_viewport_rect().size
	var rect := Rect2(Vector2.ZERO,viewport_size)
	if OS.has_feature("ios"):
		var safe := DisplayServer.get_display_safe_area()
		var screen := Vector2(DisplayServer.screen_get_size())
		if screen.x > 0 and screen.y > 0:
			rect = Rect2(Vector2(safe.position)/screen*viewport_size, Vector2(safe.size)/screen*viewport_size)
	safe_ui.position = rect.position
	safe_ui.size = rect.size
	mobile_hud.layout(rect.size)
	desktop_panel.position=Vector2(12,90)
	# The optional development drawer remains reachable from the menu on small displays.
	desktop_panel.scale=Vector2.ONE*minf(1,(rect.size.x-24)/maxf(1,desktop_panel.size.x))

func refresh_content() -> void:
	var db = preload("res://scripts/content_db.gd")
	gameplay.definitions = db.resolve("creatures",JSON.parse_string(FileAccess.get_file_as_string("res://data/creatures.json")))
	gameplay.weapons = db.resolve("weapons",JSON.parse_string(FileAccess.get_file_as_string("res://data/weapons.json")))
	gameplay.capture_map()
	gameplay.load_map()
	scenery.load_map()
	var old := player.sprite
	player.remove_child(old)
	old.queue_free()
	player.sprite = preload("res://scripts/art.gd").make_sprite("player.default",Vector2(22,40))
	player.add_child(player.sprite)
	world.refresh_tile_art()
