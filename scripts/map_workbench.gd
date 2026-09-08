extends CanvasLayer
const DB = preload("res://scripts/content_db.gd")
var game: Node2D
var panel := PanelContainer.new()
var kind := OptionButton.new()
var choice := OptionButton.new()
var mask := SpinBox.new()
var status := Label.new()
var active := false
var dirty := false
var history: Array = []
var future: Array = []
var dragging := false
var last_cell := Vector2i(-999,-999)
var camera: Camera2D
var previous_offset := Vector2.ZERO
var kinds := ["terrain","background","custom","water","lava","honey","swamp","creature","erase_creature"]
var ids: Array = []
var return_to_menu := false
var editing_map := ""

func _ready() -> void:
	layer=42
	process_mode=Node.PROCESS_MODE_ALWAYS
	var grid:=preload("res://scripts/map_grid.gd").new()
	grid.editor=self
	add_child(grid)
	add_child(panel)
	panel.position=Vector2(12,12)
	panel.custom_minimum_size=Vector2(305,680)
	panel.theme=game.safe_ui.theme
	var style:=StyleBoxFlat.new()
	style.bg_color=Color(.035,.055,.075,.96)
	style.content_margin_left=12
	style.content_margin_right=12
	style.content_margin_top=8
	panel.add_theme_stylebox_override("panel",style)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation",10)
	panel.add_child(column)
	var title := Label.new()
	title.text="地圖編輯器"
	title.add_theme_font_size_override("font_size",26)
	column.add_child(title)
	for text in ["前景圖塊","背景材質牆","自訂素材／碰撞","水","岩漿","蜂蜜","沼澤危害","放置生物／魔物","移除生物／魔物"]:
		kind.add_item(text)
	kind.item_selected.connect(func(_i): populate())
	column.add_child(kind)
	choice.clip_text=true
	column.add_child(choice)
	var info := Label.new()
	info.text="分層遮罩 1–7（7 完整格）\n液體使用量 = 遮罩 ÷ 7"
	column.add_child(info)
	mask.min_value=1
	mask.max_value=7
	mask.value=7
	column.add_child(mask)
	button(column,"復原",undo)
	button(column,"重做",redo)
	button(column,"保存地圖並套用",save_map)
	button(column,"素材／動畫／特性",func(): save_map(); close(); game.asset_editor.open())
	button(column,"回到角色位置",func(): camera.offset=Vector2.ZERO)
	var arrows := HBoxContainer.new()
	column.add_child(arrows)
	for direction in [Vector2.LEFT,Vector2.UP,Vector2.DOWN,Vector2.RIGHT]:
		button(arrows,str(["←","↑","↓","→"][[Vector2.LEFT,Vector2.UP,Vector2.DOWN,Vector2.RIGHT].find(direction)]),func(): camera.offset+=direction*240)
	button(column,"結束編輯",request_close)
	status.autowrap_mode=TextServer.AUTOWRAP_WORD_SMART
	status.custom_minimum_size=Vector2(285,100)
	column.add_child(status)
	panel.hide()
func button(parent: Node, text: String, callback: Callable) -> void:
	var b := Button.new()
	b.text=text
	b.custom_minimum_size.y=38
	b.pressed.connect(callback)
	parent.add_child(b)
func open() -> void:
	if editing_map!=game.world.map_id:
		history.clear()
		future.clear()
		dirty=false
		editing_map=game.world.map_id
	game.release_inputs()
	return_to_menu=game.menu.overlay.visible
	game.menu.overlay.hide()
	get_tree().paused=true
	game.safe_ui.hide()
	camera=game.player.get_node("Camera2D")
	previous_offset=camera.offset
	camera.position_smoothing_enabled=false
	camera.process_mode=Node.PROCESS_MODE_ALWAYS
	active=true
	panel.show()
	if not game.world.map_data.has("population"):
		game.world.map_data.population=[]
		for actor in game.gameplay.actors.get_children():
			game.world.map_data.population.append(actor.placement.duplicate(true))
	populate()
	status.text="左鍵／觸控繪製，右鍵清除；方向按鈕移動畫面。\n原圖與使用者修改分開保存。"
func close() -> void:
	active=false
	dragging=false
	panel.hide()
	camera.offset=previous_offset
	camera.position_smoothing_enabled=true
	camera.process_mode=Node.PROCESS_MODE_INHERIT
	game.safe_ui.show()
	game.release_inputs()
	get_tree().paused=false
	if return_to_menu: game.menu.open()
func request_close() -> void:
	if dirty:
		status.text="請先保存，或復原變更；目前編輯保留在畫面中。"
		return
	close()
func populate() -> void:
	choice.clear()
	ids.clear()
	var mode: String=kinds[kind.selected]
	if mode in ["terrain","background"]:
		ids.append(0)
		choice.add_item("空白／擦除")
		for id in game.world.definitions:
			if int(id)>0:
				ids.append(int(id))
				choice.add_item(str(game.world.definitions[id].name))
	elif mode=="custom":
		var defs: Dictionary=DB.resolve("custom",JSON.parse_string(FileAccess.get_file_as_string("res://assets/custom_map_assets.json")).assets)
		for id in defs:
			ids.append(id)
			choice.add_item(id)
	elif mode=="creature":
		for id in game.gameplay.definitions:
			ids.append(id)
			choice.add_item(str(game.gameplay.definitions[id].get("name",id))+" · "+id)
	else:
		ids.append(mode)
		choice.add_item("繪製 "+mode)
	if choice.item_count>0: choice.select(0)

func read_cell(mode: String, cell: Vector2i):
	if mode in ["terrain","background"]:
		var target: TileMapLayer=game.world.walls if mode=="background" else game.world.terrain
		var tile:=target.get_cell_atlas_coords(cell)
		return [tile.x,tile.y]
	if mode in ["water","lava","honey"]:
		return float(game.liquids.pools[mode].get(cell,0))
	if mode=="swamp": return str(game.liquids.hazards.get(cell,""))
	if mode=="custom":
		for row in game.world.map_data.layers.get("custom_map",[]):
			if int(row[0])==cell.x and int(row[1])==cell.y: return row.duplicate(true)
		return []
	var rows: Array=[]
	for row in game.world.map_data.get("population",[]):
		if floori(float(row.x))==cell.x and floori(float(row.y)-.01)==cell.y:
			rows.append(row.duplicate(true))
	return rows

func write_cell(mode: String, cell: Vector2i, value) -> void:
	if mode in ["terrain","background"]:
		game.world.edit_cell(cell,int(value[0]),int(value[1]),mode=="background")
	elif mode in ["water","lava","honey"]:
		game.liquids.pools[mode].erase(cell)
		game.liquids.deposit(mode,cell,float(value))
	elif mode=="swamp":
		game.liquids.hazards.erase(cell)
		if value!="": game.liquids.hazards[cell]=value
	elif mode=="custom":
		var rows: Array=game.world.map_data.layers.get("custom_map",[])
		rows=rows.filter(func(row): return not (int(row[0])==cell.x and int(row[1])==cell.y))
		if not value.is_empty(): rows.append(value.duplicate(true))
		game.world.map_data.layers.custom_map=rows
		game.scenery.load_map()
	else:
		var rows: Array=game.world.map_data.get("population",[])
		rows=rows.filter(func(row): return not (floori(float(row.x))==cell.x and floori(float(row.y)-.01)==cell.y))
		rows.append_array(value.duplicate(true))
		game.world.map_data.population=rows
		game.gameplay.load_map(false)
	game.liquids.queue_redraw()
	dirty=true

func paint(cell: Vector2i, erase: bool=false) -> void:
	if cell.x<0 or cell.y<0 or cell.x>=game.world.dimensions.x or cell.y>=game.world.dimensions.y or ids.is_empty(): return
	var mode: String=kinds[kind.selected]
	var before=read_cell(mode,cell)
	var value
	var selected_id=ids[maxi(0,choice.selected)]
	if mode=="terrain" and not erase and int(selected_id)>0:
		var definition: Dictionary=game.world.definitions[str(selected_id)]
		if definition.get("solid",false) and not definition.get("one_way_platform",false):
			var bits:=int(mask.value)
			var capacity:=0.0 if bits&4 else (1.0/3 if bits&2 else 2.0/3)
			var amount:=0.0
			for fluid in game.liquids.pools: amount+=float(game.liquids.pools[fluid].get(cell,0))
			if amount>capacity+.00001:
				status.text="此格液體超過新圖塊可容納量；請先用液體工具清除。"
				return
	if mode in ["terrain","background"]:
		value=[0,0] if erase else [selected_id,int(mask.value)]
	elif mode in ["water","lava","honey"]:
		value=0.0 if erase else mask.value/7
	elif mode=="swamp": value="" if erase else "swamp"
	elif mode=="custom": value=[] if erase else [cell.x,cell.y,selected_id]
	else:
		value=[] if erase or mode=="erase_creature" else [{"entity_id":"author_"+str(Time.get_ticks_usec()),"species":selected_id,"x":cell.x+.5,"y":cell.y+1,"respawn":true,"resolve_floor":false}]
	write_cell(mode,cell,value)
	history.append({"mode":mode,"cell":cell,"before":before,"after":read_cell(mode,cell)})
	if history.size()>150: history.pop_front()
	future.clear()
	status.text="已編輯 (%d, %d)；復原 %d 筆" % [cell.x,cell.y,history.size()]
func undo() -> void:
	if history.is_empty(): return
	var action: Dictionary=history.pop_back()
	write_cell(action.mode,action.cell,action.before)
	future.append(action)
func redo() -> void:
	if future.is_empty(): return
	var action: Dictionary=future.pop_back()
	write_cell(action.mode,action.cell,action.after)
	history.append(action)
func save_map() -> void:
	var data: Dictionary=game.world.authored_snapshot()
	data.metadata["initial_environment"]=game.environment.snapshot()
	for key in game.liquids.pools:
		data.layers[key]=game.liquids.snapshot()[key]
	data.layers.hazard=[]
	for cell in game.liquids.hazards:
		data.layers.hazard.append([cell.x,cell.y,game.liquids.hazards[cell]])
	var error:=DB.commit("maps",game.world.map_id,data)
	if error==OK:
		dirty=false
		game.world.map_data=data
		game.gameplay.capture_map()
		game.save_game()
		status.text="已保存；所有世界傳送與重開遊戲會使用編輯後地圖。"
	else: status.text="保存失敗："+error_string(error)
func _input(event: InputEvent) -> void:
	if not active: return
	var point:=Vector2.ZERO
	var paint_now:=false
	var erase:=false
	if event is InputEventMouseButton:
		point=event.position
		if point.x<330: return
		if event.button_index in [MOUSE_BUTTON_LEFT,MOUSE_BUTTON_RIGHT]:
			dragging=event.pressed
			paint_now=event.pressed
			erase=event.button_index==MOUSE_BUTTON_RIGHT
	elif event is InputEventMouseMotion:
		point=event.position
		paint_now=dragging and point.x>=330
		erase=bool(event.button_mask & MOUSE_BUTTON_MASK_RIGHT)
	elif event is InputEventScreenTouch:
		point=event.position
		if point.x<330: return
		dragging=event.pressed
		paint_now=event.pressed
	elif event is InputEventScreenDrag:
		point=event.position
		paint_now=point.x>=330
	if paint_now:
		var position:=game.get_viewport().get_canvas_transform().affine_inverse()*point
		var cell:=Vector2i(floori(position.x/40),floori(position.y/40))
		if cell!=last_cell:
			paint(cell,erase)
			last_cell=cell
		get_viewport().set_input_as_handled()
	if not dragging: last_cell=Vector2i(-999,-999)
