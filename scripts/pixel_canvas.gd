extends Control
signal changed
var grid: Array = []
var color := Color.WHITE
var eraser := false
var history: Array = []
var dragging := false
var editable := true
func _ready() -> void:
	custom_minimum_size = Vector2(430,430)
	mouse_filter = Control.MOUSE_FILTER_STOP
func set_grid(value: Array) -> void:
	grid = value.duplicate(true)
	history.clear()
	queue_redraw()
func undo() -> void:
	if not history.is_empty():
		grid = history.pop_back()
		changed.emit()
		queue_redraw()
func paint(point: Vector2) -> void:
	if grid.is_empty():
		return
	var pitch := minf(size.x/float(grid[0].size()),size.y/float(grid.size()))
	var cell := Vector2i(point/pitch)
	if cell.y<0 or cell.y>=grid.size() or cell.x<0 or cell.x>=grid[0].size():
		return
	grid[cell.y][cell.x] = "#00000000" if eraser else "#"+color.to_html(true)
	changed.emit()
	queue_redraw()
func _gui_input(event: InputEvent) -> void:
	if not editable:
		return
	if event is InputEventMouseButton and event.button_index==MOUSE_BUTTON_LEFT:
		dragging = event.pressed
		if dragging:
			history.append(grid.duplicate(true))
			if history.size()>30:
				history.pop_front()
			paint(event.position)
		accept_event()
	elif event is InputEventMouseMotion and dragging:
		paint(event.position)
		accept_event()
	elif event is InputEventScreenTouch:
		dragging = event.pressed
		if dragging:
			history.append(grid.duplicate(true))
			paint(event.position)
		accept_event()
	elif event is InputEventScreenDrag and dragging:
		paint(event.position)
		accept_event()
func _draw() -> void:
	if grid.is_empty():
		return
	var pitch := minf(size.x/float(grid[0].size()),size.y/float(grid.size()))
	for y in range(grid.size()):
		for x in range(grid[y].size()):
			var rect := Rect2(x*pitch,y*pitch,pitch,pitch)
			draw_rect(rect,Color(.20,.24,.28) if (x+y)%2==0 else Color(.28,.32,.36))
			draw_rect(rect,Color(str(grid[y][x])))
