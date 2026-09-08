extends Node2D
## One trigger owner. Switching selection cancels the old charge; release never fires a new mode.
const MODES = ["weapon","magic","tool"]
var game: Node2D
var mode := "weapon"
var aim := Vector2.RIGHT
var explicit_aim := false
var blocked_until_release := false
var trigger_down := false
var mode_label: Label
var last_direction := 1.0

func direction() -> Vector2:
	return aim.normalized() if explicit_aim else Vector2(game.player.facing,0)
func origin() -> Vector2:
	return game.player.position+Vector2(0,-3)
func target(distance := 130.0) -> Vector2:
	return origin()+direction()*distance
func point_at(point: Vector2) -> void:
	if point.distance_to(origin())<5: return
	set_direction(point-origin())
func set_direction(value: Vector2) -> void:
	if value.length()<.15: return
	aim=value.normalized()
	explicit_aim=true
func cancel() -> void:
	game.player.charge=0
	trigger_down=false
	blocked_until_release=Input.is_action_pressed("attack")
func select_mode(value: String) -> void:
	if not value in MODES: return
	cancel()
	game.tool_system.detach()
	mode=value
	game.status.text=label()
func label() -> String:
	return "武器・"+str(game.gameplay.weapons[game.player.equipped].name) if mode=="weapon" else ("魔法・"+game.magic.selected_name() if mode=="magic" else "工具・"+game.tool_system.selected_name())
func cycle_weapon() -> void:
	cancel()
	var owned: Array=[]
	for id in game.gameplay.weapons:
		if game.gameplay.inventory.has(str(game.gameplay.weapons[id].item_id)) and str(game.gameplay.weapons[id].get("base_weapon",id)) in game.gameplay.SUPPORTED:
			owned.append(id)
	if owned.is_empty(): return
	game.gameplay.equip(owned[posmod(owned.find(game.player.equipped)+1,owned.size())])
	select_mode("weapon")
func tick(delta: float) -> void:
	if Input.is_action_just_pressed("mode_cycle"): select_mode(MODES[(MODES.find(mode)+1)%3])
	for key in MODES:
		if Input.is_action_just_pressed("mode_"+key): select_mode(key)
	if Input.is_action_just_pressed("tool_cycle"):
		game.tool_system.cycle()
		select_mode("tool")
	if Input.is_action_just_pressed("magic_cycle"):
		game.magic.cycle()
		select_mode("magic")
	if Input.is_action_just_pressed("weapon_cycle"): cycle_weapon()
	var axis := Input.get_vector("aim_left","aim_right","aim_up","aim_down")
	if axis.length()>.1: set_direction(axis)
	var pressed := Input.is_action_pressed("attack")
	if blocked_until_release:
		if not pressed: blocked_until_release=false
		return
	if game.player.roll_left>0:
		cancel()
		return
	if pressed:
		if mode=="tool":
			if game.tool_system.selected!="grapple" or not trigger_down: game.tool_system.use_selected()
		else: game.player.charge+=delta
	elif trigger_down:
		var held: float=game.player.charge
		game.player.charge=0
		if mode=="weapon":
			game.player.heavy_attack=held>=float(game.gameplay.weapons[game.player.equipped].get("heavy_charge_seconds",.55))
			game.player.request_attack()
		elif mode=="magic": game.magic.cast(held,direction())
	trigger_down=pressed
	queue_redraw()
func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion and not get_tree().paused:
		point_at(get_global_mouse_position())
func _process(_delta: float) -> void:
	queue_redraw()
func _draw() -> void:
	if game==null or not game.session_started: return
	var at:=target(54)
	# Original Metal reticle: 22 x 2 cross with a dark 5 x 5 centre.
	draw_rect(Rect2(at-Vector2(11,1),Vector2(22,2)),Color(1,1,1,.92))
	draw_rect(Rect2(at-Vector2(1,11),Vector2(2,22)),Color(1,1,1,.92))
	draw_rect(Rect2(at-Vector2(2.5,2.5),Vector2(5,5)),Color(.1,.12,.15,.92))
	if mode=="tool" and game.tool_system.selected!="grapple":
		var cell: Vector2i=game.tool_system.target_cell()
		if cell.x>=0: draw_rect(Rect2(Vector2(cell)*40,Vector2(40,40)),Color(1,.84,.3,.65),false,2)
