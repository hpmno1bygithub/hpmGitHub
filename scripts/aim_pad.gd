extends Control
var actions: Node2D
var movement:=false
var finger := -1
var stick := Vector2.ZERO
var had_direction:=false
var aim_seconds:=0.0
var aim_mode:=""
var axis_latches:=Vector2.ZERO
func _ready() -> void:
	mouse_filter=Control.MOUSE_FILTER_STOP
	size=Vector2(160,160)
func _process(delta: float) -> void:
	if finger!=-1: aim_seconds+=delta
func _input(event: InputEvent) -> void:
	if not is_visible_in_tree() or get_tree().paused: return
	if event is InputEventScreenTouch:
		var local: Vector2=get_global_transform_with_canvas().affine_inverse()*event.position
		if event.pressed and finger==-1 and Rect2(Vector2.ZERO,size).has_point(local):
			begin(event.index,local)
			get_viewport().set_input_as_handled()
		elif not event.pressed and event.index==finger:
			finish(not event.canceled)
			get_viewport().set_input_as_handled()
	elif event is InputEventScreenDrag and event.index==finger:
		set_stick(get_global_transform_with_canvas().affine_inverse()*event.position)
		get_viewport().set_input_as_handled()
func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index==MOUSE_BUTTON_LEFT:
		if event.pressed and finger==-1: begin(-2,event.position)
		elif not event.pressed and finger==-2: finish(true)
	elif event is InputEventMouseMotion and finger==-2: set_stick(event.position)
func begin(pointer: int, at: Vector2) -> void:
	finger=pointer
	had_direction=false
	aim_seconds=0
	aim_mode=actions.mode
	set_stick(at)
func finish(fire: bool) -> void:
	# Source FIX85: right-stick release can cast/shoot; cancel and centre taps do not.
	if not movement and fire and had_direction and aim_mode==actions.mode and not actions.trigger_down and not Input.is_action_pressed("attack"):
		if actions.mode=="magic": actions.game.magic.cast(aim_seconds,actions.direction())
		elif actions.mode=="weapon" and actions.game.player.equipped in ["energy_bow","laser_gun","rpg_launcher","thunder_longbow","flying_drone"]:
			actions.game.player.heavy_attack=aim_seconds>=.55
			actions.game.player.request_attack()
	reset()
func reset() -> void:
	finger=-1
	stick=Vector2.ZERO
	had_direction=false
	axis_latches=Vector2.ZERO
	if movement and actions!=null: actions.game.player.move_axis=Vector2.ZERO
	queue_redraw()
func set_stick(at: Vector2) -> void:
	var radius:=size.x*.42
	stick=(at-size*.5).limit_length(radius)
	var axis:=stick/radius
	if axis.length()<.15: axis=Vector2.ZERO
	else: had_direction=true
	if movement:
		actions.game.player.move_axis=axis
		# Posture changes are edge triggered, never repeated while holding down.
		if axis.y<-.55 and axis_latches.y>=-.55: actions.game.player.set_posture("stand")
		if axis.y>.55 and axis_latches.y<=.55:
			actions.game.player.touch_down_pressed=true
		axis_latches=axis
	else: actions.set_direction(axis)
	queue_redraw()
func _draw() -> void:
	var radius:=size.x*.48
	draw_circle(size*.5,radius,Color(.04,.09,.14,.48))
	draw_arc(size*.5,radius,0,TAU,48,Color(.8,.9,1,.6),2)
	draw_circle(size*.5+stick,size.x*.15,Color(.85,.92,1,.65))
