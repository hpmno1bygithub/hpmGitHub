extends Control
## Layout formulas ported from FIX131 UIKitControls.layout / control_overlay.py.
var game: Node2D
var move_pad:=preload("res://scripts/aim_pad.gd").new()
var buttons: Dictionary={}
var frames: Dictionary={}
var top_buttons: Dictionary={}
var health:=ProgressBar.new()
var mana:=ProgressBar.new()
var info:=Label.new()
var toast:=Label.new()
var toast_seconds:=0.0
var last_status:=""
var logical_size:=Vector2.ZERO
var ipad:=false
func _ready() -> void:
	mouse_filter=Control.MOUSE_FILTER_IGNORE
	move_pad.actions=game.actions
	move_pad.movement=true
	game.controls.add_child(move_pad)
	for key in ["jump","attack","crouch","roll","interact","magic","tool","action_mode"]:
		var button:=TouchScreenButton.new()
		button.action=str({"magic":"magic_cycle","tool":"tool_cycle","action_mode":"mode_cycle"}.get(key,key))
		var panel:=Polygon2D.new()
		panel.name="Panel"
		panel.color=Color(.06,.11,.17,.78)
		button.add_child(panel)
		var icon:=Sprite2D.new()
		icon.name="Icon"
		button.add_child(icon)
		game.controls.add_child(button)
		buttons[key]=button
		game.touch_buttons.append(button)
	for entry in [["backpack","背包",game.show_inventory],["new_game","新開遊戲",func(): game.menu.request_new()],["save","儲存",func(): game.save_game(); game.status.text="已儲存"],["load","載入遊戲",func():
		if game.read_progress().is_empty(): game.status.text="尚無遊戲存檔"
		else: game.release_inputs(); game.menu.start(true)],["close","選單／設定",func(): game.save_game(); game.menu.open()]]:
		var button:=Button.new()
		button.tooltip_text=entry[1]
		button.icon=load("res://assets/control_icons/control_"+str(entry[0])+".png")
		button.expand_icon=true
		button.focus_mode=Control.FOCUS_NONE
		button.pressed.connect(entry[2])
		add_child(button)
		top_buttons[entry[0]]=button
	for bar in [health,mana]:
		bar.max_value=100
		bar.show_percentage=false
		bar.mouse_filter=Control.MOUSE_FILTER_IGNORE
		var bg:=StyleBoxFlat.new()
		bg.bg_color=Color(.08,.10,.13,.92)
		bar.add_theme_stylebox_override("background",bg)
		var fill:=StyleBoxFlat.new()
		fill.bg_color=Color(.8,.16,.2) if bar==health else Color(.12,.46,.85)
		bar.add_theme_stylebox_override("fill",fill)
		add_child(bar)
	add_child(info)
	info.add_theme_font_size_override("font_size",9)
	info.mouse_filter=Control.MOUSE_FILTER_IGNORE
	add_child(toast)
	toast.add_theme_font_size_override("font_size",11)
	toast.horizontal_alignment=HORIZONTAL_ALIGNMENT_CENTER
	toast.mouse_filter=Control.MOUSE_FILTER_IGNORE
func rect_for(key: String, rect: Rect2) -> void:
	frames[key]=rect
	var button: TouchScreenButton=buttons[key]
	button.position=rect.get_center()
	var shape:=RectangleShape2D.new()
	shape.size=rect.size
	button.shape=shape
	var half:=rect.size*.5
	button.get_node("Panel").polygon=PackedVector2Array([Vector2(-half.x,-half.y),Vector2(half.x,-half.y),half,Vector2(-half.x,half.y)])
func layout(area: Vector2, force_ipad: int=-1) -> void:
	ipad=area.x/area.y<1.6 if force_ipad<0 else bool(force_ipad)
	var factor:=area.y/(768.0 if ipad else 393.0)
	logical_size=area/factor
	scale=Vector2.ONE*factor
	game.controls.scale=scale
	game.aim_pad.scale=scale
	var w:=logical_size.x
	var h:=logical_size.y
	var diameter:=minf(178 if ipad else 152,h*.40)
	move_pad.position=Vector2(30 if ipad else 24,h-diameter-22)
	move_pad.size=Vector2.ONE*diameter
	frames.move=Rect2(move_pad.position,move_pad.size)
	var b:=minf(84 if ipad else 72,h*(.15 if ipad else .18))
	var right:=w-(28 if ipad else 18)
	var small:=Vector2(b*.92,b*.62)
	rect_for("jump",Rect2(right-b*2-10,h-b*2.15-10,b,b))
	rect_for("attack",Rect2(right-b,h-b*2.15-10,b,b))
	rect_for("crouch",Rect2(right-b,h-b-10,b,b))
	rect_for("roll",Rect2(Vector2(right-b*3.1-24,h-b-8),small))
	rect_for("interact",Rect2(Vector2(right-b*3.1-24,h-b*1.75-12),small))
	rect_for("magic",Rect2(Vector2(right-b*3.1-24,h-b*2.38-14),small))
	rect_for("tool",Rect2(Vector2(right-b*4.05-28,h-b*1.75-12),small))
	rect_for("action_mode",Rect2(Vector2(right-b*4.05-28,h-b*1.75-12+small.y+4),small))
	var aim_size:=minf(104 if ipad else 88,maxf(78 if ipad else 70,h*(.18 if ipad else .21)))
	game.aim_pad.size=Vector2.ONE*aim_size
	var aim_position:=Vector2(right-aim_size-6,maxf(62,h-b*3.45-aim_size*.20))
	game.aim_pad.position=aim_position*factor
	frames.aim=Rect2(aim_position,game.aim_pad.size)
	var x:=w-44
	for key in ["close","load","save","new_game","backpack"]:
		var width:=44.0 if key=="close" else 38.0
		var rect:=Rect2(x,0,width,38)
		top_buttons[key].position=rect.position
		top_buttons[key].size=rect.size
		frames[key]=rect
		x-=45
	health.position=Vector2(12,8)
	health.size=Vector2(125,10)
	mana.position=Vector2(12,22)
	mana.size=Vector2(125,10)
	info.position=Vector2(12,36)
	toast.position=Vector2(140,55)
	toast.size=Vector2(maxf(120,w-280),30)
	toast.clip_text=true
func _process(delta: float) -> void:
	health.value=game.player.hp
	mana.value=game.magic.mana
	info.text="HP %d  MP %d · %s" % [game.player.hp,game.magic.mana,{"stand":"站","crouch":"蹲","prone":"趴","roll":"滾"}[game.player.posture]]
	if game.status.text!=last_status:
		last_status=game.status.text
		toast.text=last_status
		toast_seconds=3.0
	toast_seconds=maxf(0,toast_seconds-delta)
	toast.visible=toast_seconds>0
	for key in buttons:
		var id: String="control_"+key
		if key=="attack": id="control_action"
		elif key=="magic": id="magic_"+game.magic.selected
		elif key=="tool": id="tool_"+game.tool_system.selected
		elif key=="action_mode": id="control_action" if game.actions.mode=="weapon" else ("magic_"+game.magic.selected if game.actions.mode=="magic" else "tool_"+game.tool_system.selected)
		var icon: Sprite2D=buttons[key].get_node("Icon")
		if icon.get_meta("icon_id","")!=id:
			icon.texture=load("res://assets/control_icons/"+id+".png")
			icon.set_meta("icon_id",id)
		if icon.texture!=null:
			var rect: Rect2=frames.get(key,Rect2(0,0,40,40))
			icon.scale=Vector2.ONE*minf(rect.size.x,rect.size.y)*.72/maxf(icon.texture.get_width(),icon.texture.get_height())
