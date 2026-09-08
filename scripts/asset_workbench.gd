extends CanvasLayer
const DB = preload("res://scripts/content_db.gd")
var game: Node2D
var overlay := Control.new()
var assets := OptionButton.new()
var states := OptionButton.new()
var canvas := preload("res://scripts/pixel_canvas.gd").new()
var message := Label.new()
var frame_label := Label.new()
var fps := SpinBox.new()
var loop := CheckButton.new()
var action_frame := SpinBox.new()
var damage_marker := CheckButton.new()
var effect_frame := SpinBox.new()
var binding := OptionButton.new()
var source: Dictionary = {}
var id := ""
var state := "idle"
var frame := 0
var playing := false
var clock := 0.0
var dirty := false
var numbers: Dictionary = {}
var properties := VBoxContainer.new()
var section := ""
var export_dialog := FileDialog.new()
var new_id := LineEdit.new()
var role := OptionButton.new()
var return_to_menu := false
var action_choice := OptionButton.new()
var action_profile: Dictionary = {}
var action_numbers: Dictionary = {}
var active_action := ""

func _ready() -> void:
	layer = 45
	process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(overlay)
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.theme = game.safe_ui.theme
	var dim := ColorRect.new()
	dim.color = Color(.035,.055,.075,.99)
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(dim)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left","right","top","bottom"]:
		margin.add_theme_constant_override("margin_"+side,20)
	overlay.add_child(margin)
	var column := VBoxContainer.new()
	margin.add_child(column)
	var title := Label.new()
	title.text = "素材／動畫／特性工作台"
	title.add_theme_font_size_override("font_size",28)
	column.add_child(title)
	var top := HBoxContainer.new()
	column.add_child(top)
	assets.custom_minimum_size.x = 310
	assets.item_selected.connect(func(i): select_asset(assets.get_item_text(i)))
	top.add_child(assets)
	states.custom_minimum_size.x = 200
	states.item_selected.connect(func(i): save_frame(); state=states.get_item_text(i); frame=0; show_frame())
	top.add_child(states)
	button(top,"儲存並套用",save_asset)
	button(top,"捨棄修改",func(): dirty=false; select_asset(id))
	button(top,"匯出內容包",func(): export_dialog.popup_centered(Vector2i(900,550)))
	button(top,"關閉",request_close)
	var split := HBoxContainer.new()
	var create_row := HBoxContainer.new()
	column.add_child(create_row)
	new_id.placeholder_text="新素材 ID，例如 creature.my_wolf、tile.my_brick"
	new_id.custom_minimum_size.x=570
	create_row.add_child(new_id)
	button(create_row,"複製成新素材",duplicate_asset)
	button(create_row,"領取並測試此武器",func():
		if not id.begins_with("weapon.") or dirty:
			message.text="請先選擇武器素材並儲存。"
			return
		var key:=id.get_slice(".",1)
		if not game.gameplay.inventory.has("weapon_"+key): game.gameplay.give_item("weapon_"+key,str(game.gameplay.weapons[key].name),1)
		game.gameplay.equip(key)
		message.text="已裝備；關閉工作台後按 J 測試。")
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	column.add_child(split)
	var paint_column := VBoxContainer.new()
	split.add_child(paint_column)
	paint_column.add_child(canvas)
	canvas.changed.connect(func(): dirty=true; save_frame())
	var palette := HBoxContainer.new()
	paint_column.add_child(palette)
	var picker := ColorPickerButton.new()
	picker.color = Color.WHITE
	picker.custom_minimum_size = Vector2(100,38)
	picker.color_changed.connect(func(value): canvas.color=value; canvas.eraser=false)
	palette.add_child(picker)
	button(palette,"橡皮擦",func(): canvas.eraser=not canvas.eraser)
	button(palette,"復原筆畫",canvas.undo)
	button(palette,"播放／暫停",func(): playing=not playing; canvas.editable=not playing)
	var frames := HBoxContainer.new()
	paint_column.add_child(frames)
	button(frames,"◀",func(): change_frame(-1))
	frames.add_child(frame_label)
	button(frames,"▶",func(): change_frame(1))
	button(frames,"複製影格",func():
		if source.is_empty(): return
		save_frame(); source.animations[state].frames.insert(frame+1,canvas.grid.duplicate(true)); frame+=1; dirty=true; show_frame())
	button(frames,"刪除影格",func():
		if source.is_empty() or source.animations[state].frames.size()<=1: return
		source.animations[state].frames.remove_at(frame); frame=maxi(0,frame-1); dirty=true; show_frame())
	var scroll := ScrollContainer.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(scroll)
	properties.custom_minimum_size.x = 680
	scroll.add_child(properties)
	var timeline := HBoxContainer.new()
	properties.add_child(timeline)
	add_spin(timeline,"FPS",fps,1,60)
	loop.text = "循環"
	timeline.add_child(loop)
	add_spin(properties,"動作事件影格（-1 不指定）",action_frame,-1,255)
	add_spin(properties,"特效事件影格（-1 不指定）",effect_frame,-1,255)
	damage_marker.text="目前影格觸發傷害（Boss 特效時間表）"
	properties.add_child(damage_marker)
	damage_marker.toggled.connect(func(_on): dirty=true; save_frame())
	binding.add_item("原版 FIX54 程序式特效")
	binding.add_item("使用素材 normal_effect／heavy_effect")
	properties.add_child(binding)
	for value in ["solid","platform","ladder","climb_platform","passable"]: role.add_item(value)
	properties.add_child(role)
	properties.add_child(action_choice)
	action_choice.item_selected.connect(func(i): save_action_fields(); active_action=action_choice.get_item_text(i); show_action_fields())
	role.item_selected.connect(func(_v): dirty=true)
	canvas.custom_minimum_size=Vector2(390,390)
	for control in [fps,action_frame,effect_frame]:
		control.value_changed.connect(func(_v): dirty=true)
	loop.toggled.connect(func(_v): dirty=true)
	binding.item_selected.connect(func(_v): dirty=true)
	message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	message.custom_minimum_size.y = 45
	column.add_child(message)
	export_dialog.access = FileDialog.ACCESS_FILESYSTEM
	export_dialog.file_mode = FileDialog.FILE_MODE_SAVE_FILE
	export_dialog.add_filter("*.json","PytoRPG 內容包")
	export_dialog.current_dir = OS.get_user_data_dir()
	export_dialog.current_file = "content-export.json"
	export_dialog.file_selected.connect(func(file): message.text="匯出："+file if DB.export_to(file)==OK else "匯出失敗")
	add_child(export_dialog)
	overlay.hide()

func button(row: Node, text: String, action: Callable) -> void:
	var b := Button.new()
	b.text = text
	b.custom_minimum_size.y = 38
	b.pressed.connect(action)
	row.add_child(b)
func add_spin(row: Node, text: String, control: SpinBox, minimum: float, maximum: float) -> void:
	var label := Label.new()
	label.text = text
	row.add_child(label)
	control.min_value = minimum
	control.max_value = maximum
	control.step = .01 if text=="FPS" else 1.0
	row.add_child(control)
func open() -> void:
	game.release_inputs()
	return_to_menu=game.menu.overlay.visible
	game.menu.overlay.hide()
	get_tree().paused = true
	assets.clear()
	var ids: Array = preload("res://scripts/art.gd").pixels.keys()
	for key in DB.pack.assets:
		if not ids.has(key): ids.append(key)
	ids.sort()
	for key in ids: assets.add_item(key)
	overlay.show()
	if id=="": select_asset("player.default")

func select_asset(next: String) -> void:
	if dirty:
		for i in range(assets.item_count):
			if assets.get_item_text(i)==id: assets.select(i)
		message.text = "請先儲存目前素材，再切換；未存內容不會被丟棄。"
		return
	source = DB.asset(next).duplicate(true)
	if source.is_empty():
		message.text = "此素材沒有像素來源。"
		return
	id = next
	for i in range(assets.item_count):
		if assets.get_item_text(i)==id: assets.select(i)
	if source.get("animations",{}).is_empty():
		source.animations = {"idle":{"frames":[source.pixels.duplicate(true)],"fps":8,"loop":true,"events":{}}}
	states.clear()
	for key in source.animations: states.add_item(key)
	state = str(source.animations.keys()[0])
	frame = 0
	show_properties()
	show_frame()
	dirty = false
	message.text = "修改素材 → 儲存套用 → 所有相同 ID 的生物／地圖／武器同步更新。"

func save_frame() -> void:
	if not source.is_empty() and not playing:
		source.animations[state].frames[frame] = canvas.grid.duplicate(true)
		source.animations[state].fps=fps.value
		source.animations[state].loop=loop.button_pressed
		var events: Dictionary=source.animations[state].get("events",{})
		for pair in [["action",action_frame],["effect",effect_frame]]:
			if pair[1].value<0: events.erase(pair[0])
			else: events[pair[0]]=mini(int(pair[1].value),source.animations[state].frames.size()-1)
		source.animations[state].events=events
		var markers: Dictionary=source.animations[state].get("frame_events",{})
		var marker: Dictionary=markers.get(str(frame),{})
		marker.damage=damage_marker.button_pressed
		markers[str(frame)]=marker
		source.animations[state].frame_events=markers
func show_frame() -> void:
	var info: Dictionary = source.animations[state]
	frame = clampi(frame,0,info.frames.size()-1)
	canvas.set_grid(info.frames[frame])
	damage_marker.set_pressed_no_signal(bool(info.get("frame_events",{}).get(str(frame),{}).get("damage",false)))
	frame_label.text = "%d / %d" % [frame+1,info.frames.size()]
	fps.set_value_no_signal(float(info.get("fps",8)))
	loop.set_pressed_no_signal(bool(info.get("loop",true)))
	action_frame.set_value_no_signal(int(info.get("events",{}).get("action",-1)))
	effect_frame.set_value_no_signal(int(info.get("events",{}).get("effect",-1)))
	var b: Dictionary = source.get("combat_bindings",{}).get(state,{})
	binding.select(1 if b.get("render_mode","fix54")=="authored" else 0)
func change_frame(amount: int) -> void:
	if source.is_empty(): return
	save_frame()
	frame = posmod(frame+amount,source.animations[state].frames.size())
	show_frame()
func _process(delta: float) -> void:
	if playing and overlay.visible and not source.is_empty():
		clock += delta
		if clock>=1.0/maxf(1,fps.value):
			frame = posmod(frame+1,source.animations[state].frames.size())
			show_frame()
			clock=0
func show_properties() -> void:
	for control in action_numbers.values(): control.get_parent().queue_free()
	action_numbers.clear()
	action_choice.clear()
	action_choice.visible=id.begins_with("creature.")
	active_action=""
	if action_choice.visible:
		var original: Dictionary=JSON.parse_string(FileAccess.get_file_as_string("res://assets/creature_actions.json")).creatures
		action_profile=DB.resolve("actions",original).get(id,{"actions":{}}).duplicate(true)
		for key in action_profile.get("actions",{}): action_choice.add_item(key)
		if action_choice.item_count>0: active_action=action_choice.get_item_text(0)
	role.visible=id.begins_with("tile.")
	if role.visible:
		var defs: Dictionary=DB.resolve("custom",JSON.parse_string(FileAccess.get_file_as_string("res://assets/custom_map_assets.json")).assets)
		role.select(maxi(0,["solid","platform","ladder","climb_platform","passable"].find(str(defs.get(id,{}).get("role","solid")))))
	for control in numbers.values():
		control.get_parent().queue_free()
	numbers.clear()
	section = "creatures" if id.begins_with("creature.") else ("weapons" if id.begins_with("weapon.") else "")
	if section=="": return
	var key := id.get_slice(".",1)
	var data: Dictionary = game.gameplay.definitions.get(key,{}) if section=="creatures" else game.gameplay.weapons.get(key,{})
	var fields := ["hp","attack","speed","w","h","hostile"] if section=="creatures" else ["damage","reach_tiles","cooldown","heavy_charge_seconds","heavy_damage_mult","heavy_reach_tiles","heavy_max_targets","projectile_speed"]
	for field in fields:
		var row := HBoxContainer.new()
		properties.add_child(row)
		var number := SpinBox.new()
		var names: Dictionary={"hp":"生命上限","attack":"攻擊傷害","speed":"移動速度","w":"碰撞寬度","h":"碰撞高度","hostile":"主動敵對 0／1","damage":"武器傷害","reach_tiles":"攻擊距離（格）","cooldown":"冷卻（秒）","heavy_charge_seconds":"蓄力時間（秒）","heavy_damage_mult":"重擊傷害倍率","heavy_reach_tiles":"重擊距離（格）","heavy_max_targets":"重擊目標上限","projectile_speed":"投射物速度"}
		add_spin(row,str(names.get(field,field)),number,1 if field in ["hp","w","h"] else 0,1 if field=="hostile" else 10000)
		number.step = .01 if field!="hostile" else 1.0
		number.set_value_no_signal(float(data.get(field,0)))
		number.value_changed.connect(func(_v): dirty=true)
		numbers[field] = number
	show_action_fields()

func show_action_fields() -> void:
	for control in action_numbers.values(): control.get_parent().queue_free()
	action_numbers.clear()
	if active_action=="": return
	var action: Dictionary=action_profile.actions[active_action]
	for key in ["enabled","damage","cooldown","range_min","range_max","vertical_range","speed","lift_speed","duration","hit_radius","priority","chance"]:
		var row:=HBoxContainer.new()
		properties.add_child(row)
		var value:=SpinBox.new()
		add_spin(row,"動作 · "+key,value,0,1 if key in ["enabled","chance"] else 10000)
		value.step=1.0 if key=="enabled" else .05
		value.set_value_no_signal(float(action.get(key,0)))
		value.value_changed.connect(func(_v): dirty=true)
		action_numbers[key]=value
func save_action_fields() -> void:
	if active_action=="": return
	for key in action_numbers:
		if key=="enabled": action_profile.actions[active_action][key]=bool(action_numbers[key].value)
		else: action_profile.actions[active_action][key]=action_numbers[key].value
func save_asset() -> void:
	if source.is_empty(): return
	playing=false
	canvas.editable=true
	save_frame()
	var info: Dictionary = source.animations[state]
	info.fps = fps.value
	info.loop = loop.button_pressed
	info.events = info.get("events",{})
	for pair in [["action",action_frame],["effect",effect_frame]]:
		if pair[1].value<0: info.events.erase(pair[0])
		else: info.events[pair[0]] = mini(int(pair[1].value),info.frames.size()-1)
	if state in ["normal_attack","heavy_attack"]:
		if not source.has("combat_bindings"): source.combat_bindings={}
		var original: Dictionary = source.combat_bindings.get(state,{})
		original.merge({"render_mode":"authored" if binding.selected==1 else "fix54","effect_state":original.get("effect_state","heavy_effect" if state=="heavy_attack" else "normal_effect"),"trigger_event":original.get("trigger_event","effect"),"scale":original.get("scale",1)},true)
		source.combat_bindings[state] = original
	if state=="idle": source.pixels=source.animations.idle.frames[0].duplicate(true)
	var changes: Array = [{"section":"assets","id":id,"data":source}]
	if section!="":
		var values: Dictionary = DB.pack[section].get(id.get_slice(".",1),{}).duplicate(true)
		for key in numbers: values[key] = bool(numbers[key].value) if key=="hostile" else numbers[key].value
		changes.append({"section":section,"id":id.get_slice(".",1),"data":values})
	if role.visible:
		changes.append({"section":"custom","id":id,"data":{"role":role.get_item_text(role.selected)}})
	if active_action!="":
		save_action_fields()
		changes.append({"section":"actions","id":id,"data":action_profile})
	var result := DB.commit_many(changes)
	if result==OK:
		dirty=false
		game.refresh_content()
		message.text="已儲存並套用；匯出內容包可帶回 Windows Godot／GitHub。"
	else:
		message.text="儲存失敗："+error_string(result)
func request_close() -> void:
	if dirty:
		message.text="請先儲存並套用；修改仍保留在工作台。"
		return
	playing=false
	overlay.hide()
	game.release_inputs()
	get_tree().paused=false
	if return_to_menu: game.menu.open()

func duplicate_asset() -> void:
	if dirty:
		message.text="請先儲存目前素材與特性，再複製成新 ID。"
		return
	var next := new_id.text.strip_edges()
	var regex := RegEx.new()
	regex.compile("^(creature|weapon|tile|player|decoration|effect)\\.[a-z][a-z0-9_]{1,47}$")
	if source.is_empty() or regex.search(next)==null or next.get_slice(".",0)!=id.get_slice(".",0):
		message.text="新 ID 需與來源同類別，使用英文小寫／數字／底線。"
		return
	if not DB.asset(next).is_empty():
		message.text="ID 已存在，請另取名稱。"
		return
	save_frame()
	var clone:=source.duplicate(true)
	clone.asset_id=next
	var changes: Array=[{"section":"assets","id":next,"data":clone}]
	var old_key:=id.get_slice(".",1)
	var key:=next.get_slice(".",1)
	if section!="":
		var defs: Dictionary=game.gameplay.definitions if section=="creatures" else game.gameplay.weapons
		var config: Dictionary=defs[old_key].duplicate(true)
		config.name=key
		if section=="weapons":
			config.base_weapon=config.get("base_weapon",old_key)
			config.item_id="weapon_"+key
		changes.append({"section":section,"id":key,"data":config})
		if section=="creatures" and not action_profile.is_empty():
			changes.append({"section":"actions","id":next,"data":action_profile.duplicate(true)})
	elif next.begins_with("tile."):
		changes.append({"section":"custom","id":next,"data":{"role":"solid","name":key}})
	if DB.commit_many(changes)==OK:
		dirty=false
		game.refresh_content()
		assets.add_item(next)
		select_asset(next)
		message.text="已建立新素材 ID；可在地圖編輯器放置並繼續編輯。"
