extends Control
var editor: CanvasLayer
func _ready() -> void:
	mouse_filter=Control.MOUSE_FILTER_IGNORE
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
func _process(_delta: float) -> void:
	visible=editor.active
	if visible:
		queue_redraw()
		editor.game.backdrop.queue_redraw()
		editor.game.scenery.queue_redraw()
		editor.game.liquids.queue_redraw()
func _draw() -> void:
	var transform:=get_viewport().get_canvas_transform()
	var origin:=transform.origin
	var step:=40.0*transform.get_scale().x
	for x in range(ceili(330/step),ceili(size.x/step)+1):
		var xx:=x*step+fmod(origin.x,step)
		if xx>=330: draw_line(Vector2(xx,0),Vector2(xx,size.y),Color(1,1,1,.13))
	for y in range(ceili(size.y/step)+1):
		var yy:=y*step+fmod(origin.y,step)
		draw_line(Vector2(330,yy),Vector2(size.x,yy),Color(1,1,1,.13))
	var mouse:=get_global_mouse_position()
	if mouse.x>=330:
		var world:=transform.affine_inverse()*mouse
		var cell:=Vector2(floorf(world.x/40),floorf(world.y/40))*40
		draw_rect(Rect2(transform*cell,Vector2(step,step)),Color(1,.85,.3,.9),false,2)
