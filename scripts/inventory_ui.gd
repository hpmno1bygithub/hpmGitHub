extends CanvasLayer
var game: Node2D
var overlay := Control.new()
var grid := GridContainer.new()
var details := Label.new()
var pages := OptionButton.new()
var quantity := SpinBox.new()
var selected := 0
var moving := -1
var page := 0
var equipment: Dictionary
func _ready() -> void:
	layer = 35
	process_mode = Node.PROCESS_MODE_ALWAYS
	equipment = JSON.parse_string(FileAccess.get_file_as_string("res://assets/equipment_catalog.json")).items
	add_child(overlay)
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.theme = game.safe_ui.theme
	var dim := ColorRect.new()
	dim.color = Color(.025,.04,.06,.97)
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(dim)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(center)
	var column := VBoxContainer.new()
	column.custom_minimum_size = Vector2(1120,620)
	column.add_theme_constant_override("separation",12)
	center.add_child(column)
	var heading := Label.new()
	heading.text = "背包　72 格・物品／武器／穿戴裝備"
	heading.add_theme_font_size_override("font_size",28)
	column.add_child(heading)
	for i in range(3):
		pages.add_item("第 %d 頁" % (i+1))
	pages.item_selected.connect(func(i): page=i; refresh())
	column.add_child(pages)
	grid.columns = 6
	column.add_child(grid)
	details.custom_minimum_size.y = 65
	details.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	column.add_child(details)
	var row := HBoxContainer.new()
	column.add_child(row)
	quantity.min_value = 1
	quantity.max_value = 999
	quantity.custom_minimum_size.x = 110
	row.add_child(quantity)
	button(row,"裝備／卸下",equip)
	button(row,"移動／分堆",func(): moving=selected; details.text="請點選目的格；同物品合併，整堆可互換。")
	button(row,"丟出",drop)
	button(row,"放置素材",place)
	button(row,"開發測試武器",func():
		for id in game.gameplay.SUPPORTED:
			var weapon: Dictionary = game.gameplay.weapons[id]
			if not game.gameplay.inventory.has(str(weapon.item_id)):
				game.gameplay.give_item(str(weapon.item_id),str(weapon.name),1)
		refresh())
	button(row,"關閉",close)
	overlay.hide()
func button(row: Node, text: String, callback: Callable) -> void:
	var b := Button.new()
	b.text = text
	b.custom_minimum_size.y = 48
	b.pressed.connect(callback)
	row.add_child(b)
func open() -> void:
	game.release_inputs()
	get_tree().paused = true
	overlay.show()
	refresh()
func close() -> void:
	moving = -1
	overlay.hide()
	game.save_game()
	game.release_inputs()
	get_tree().paused = false
func refresh() -> void:
	for child in grid.get_children():
		grid.remove_child(child)
		child.queue_free()
	for i in range(page*24,page*24+24):
		var slot_item = game.gameplay.bag.slots[i]
		var b := Button.new()
		b.custom_minimum_size = Vector2(182,78)
		b.text = "%02d　—" % (i+1) if slot_item==null else "%02d　%s\n× %d" % [i+1,slot_item.name,slot_item.count]
		b.clip_text = true
		if slot_item!=null:
			var asset_id:=str(slot_item.id).replace("weapon_","weapon.")
			var icon:=preload("res://scripts/art.gd").texture(asset_id)
			if icon!=null:
				b.icon=icon
				b.add_theme_constant_override("icon_max_width",32)
		b.modulate = Color(1,.84,.48) if i==selected else Color.WHITE
		b.pressed.connect(func():
			if moving>=0:
				game.gameplay.bag.move(moving,i,int(quantity.value))
				moving = -1
			selected=i
			refresh())
		grid.add_child(b)
	var item = game.gameplay.bag.slots[selected]
	details.text = "選擇物品。裝備不消耗數量；重擊耐久依每一把武器分開保存。"
	if item!=null:
		quantity.max_value = int(item.count)
		details.text = str(item.name)
		if not item.uses.is_empty():
			details.text += "　目前這把剩餘重擊 %d 次" % int(item.uses[0])
		if equipment.has(item.id):
			details.text += "\n"+str(equipment[item.id].description_zh)
	details.text += "\n穿戴："
	for slot in ["head","body","foot"]:
		var id: String=game.gameplay.bag.worn.get(slot,"")
		details.text += str(equipment.get(id,{}).get("display_name_zh","未裝備"))+"　"
	if game.gameplay.bag.worn.body=="equipment.shield_armor":
		details.text += "護盾 %d/200" % game.gameplay.bag.shield_hp
func equip() -> void:
	var item = game.gameplay.bag.slots[selected]
	if item==null:
		return
	if str(item.id).begins_with("weapon_"):
		if not game.gameplay.equip(str(item.id).trim_prefix("weapon_")):
			details.text=game.status.text
			return
	elif equipment.has(item.id):
		var slot: String = equipment[item.id].slot
		game.gameplay.bag.worn[slot] = "" if game.gameplay.bag.worn.get(slot,"")==item.id else item.id
	refresh()
func drop() -> void:
	var item: Dictionary = game.gameplay.bag.remove(selected,int(quantity.value))
	if not item.is_empty():
		item.merge({"x":game.player.position.x+game.player.facing*65,"y":game.player.position.y,"pickup_delay":1.0})
		game.gameplay.drops.append(item)
	refresh()
func place() -> void:
	var item = game.gameplay.bag.slots[selected]
	if item==null: return
	var tile_id: int=int(str(item.id).trim_prefix("material_")) if str(item.id).begins_with("material_") else -1
	if tile_id<0:
		for key in game.tool_system.rules.drops:
			if str(game.tool_system.rules.drops[key][0])==str(item.id):
				tile_id=int(key)
				break
	if tile_id<0:
		details.text = "請選擇土壤、木材、石塊或可放置的圖塊素材。"
		return
	var cell := Vector2i((game.player.position+Vector2(game.player.facing*55,0))/40)
	if game.world.terrain.get_cell_source_id(cell)>=0 or game.liquids.room("water",cell)<.999:
		details.text = "前方空間已被圖塊或液體占用。"
		return
	game.world.edit_cell(cell,tile_id,7)
	game.gameplay.bag.remove(selected,1)
	refresh()
