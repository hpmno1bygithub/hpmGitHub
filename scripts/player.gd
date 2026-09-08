extends CharacterBody2D

const Art = preload("res://scripts/art.gd")
signal died
signal attack_requested
signal interact_requested

var world: Node2D
var facing := 1.0
var sprite: AnimatedSprite2D
var hp := 100.0
var invulnerable := 0.0
var swing_left := 0.0
var swing_duration := .4
var posture := "stand"
var roll_left := 0.0
var roll_cooldown := 0.0
var before_roll := "stand"
var climbing := false
var drop_left := 0.0
var charge := 0.0
var heavy_attack := false
var equipped := "sword"
var weapon_reach := 40.0
var body_shape := CollisionShape2D.new()
var liquid := ""
var in_swamp := false
var air_jump_used := false
var air_dash_used := false
var hovering := false
var move_axis := Vector2.ZERO
var touch_down_pressed:=false
const STEP_HEIGHT := 40.0/3.0

func request_attack() -> void:
	attack_requested.emit()
func worn(slot: String) -> String:
	return str(get_parent().gameplay.bag.worn.get(slot,""))
func environment_damage(amount: float) -> void:
	if hp<=0:
		return
	var minimum := 0.0 if bool(get_parent().preferences.get("death_handling",true)) else 1.0
	hp = maxf(minimum,hp-maxf(0,amount))
	if hp<=0:
		died.emit()

func _ready() -> void:
	z_index = 10
	collision_layer = 2
	collision_mask = 9
	var shape := body_shape
	var rectangle := RectangleShape2D.new()
	rectangle.size = Vector2(22, 40)
	shape.shape = rectangle
	add_child(shape)
	sprite = Art.make_sprite("player.default",Vector2(22,40))
	add_child(sprite)
	var weapon_view := preload("res://scripts/weapon_view.gd").new()
	weapon_view.player=self
	weapon_view.game=get_parent()
	add_child(weapon_view)
	var camera := Camera2D.new()
	camera.name="Camera2D"
	camera.position_smoothing_enabled = true
	camera.position_smoothing_speed = 8
	add_child(camera)

func _physics_process(delta: float) -> void:
	if hp<=0:
		return
	get_parent().actions.tick(delta)
	if is_on_floor():
		air_jump_used = false
		air_dash_used = false
		hovering = false
	if worn("foot")!="equipment.hover_shoes" or liquid!="":
		hovering = false
	invulnerable = maxf(0,invulnerable-delta)
	swing_left = maxf(0,swing_left-delta)
	roll_cooldown = maxf(0,roll_cooldown-delta)
	drop_left = maxf(0,drop_left-delta)
	if Input.is_action_just_pressed("climb") and posture in ["crouch","prone"]:
		set_posture("stand")
	if Input.is_action_just_pressed("crouch") and is_on_floor():
		set_posture("prone" if posture=="crouch" else "crouch")
	if Input.is_action_just_pressed("prone") and is_on_floor():
		set_posture("stand" if posture=="prone" else "prone")
	if Input.is_action_just_pressed("roll") and (is_on_floor() or (worn("foot")=="equipment.air_dash_shoes" and not air_dash_used)) and roll_cooldown<=0:
		air_dash_used = not is_on_floor()
		before_roll = posture
		if set_posture("roll"):
			roll_left = .38
			roll_cooldown = .65
	sprite.modulate = Color(1,.5,.5) if invulnerable>.4 else Color.WHITE
	var axis := clampf(Input.get_axis("move_left", "move_right")+move_axis.x,-1,1)
	var speed := 235.0 * (.55 if posture=="crouch" else (.35 if posture=="prone" else 1.0))
	speed *= .4 if liquid=="honey" else (.62 if liquid!="" else (.65 if in_swamp else 1.0))
	velocity.x = move_toward(velocity.x, axis * speed, (1800.0 if axis else 2600.0) * delta)
	if roll_left>0:
		roll_left = maxf(0,roll_left-delta)
		velocity.x = facing*355
		if roll_left==0 and not set_posture(before_roll):
			set_posture("prone")
	elif axis:
		facing = signf(axis)
		sprite.flip_h = facing < 0
	var ladder := ladder_available()
	var vertical := clampf(Input.get_axis("climb","down")+move_axis.y,-1,1)
	if vertical!=0 and ladder and roll_left<=0:
		climbing = true
	if not ladder or Input.is_action_just_pressed("jump") or axis!=0:
		climbing = false
	if (Input.is_action_just_pressed("down") or touch_down_pressed) and is_on_floor() and not ladder:
		var ray := PhysicsRayQueryParameters2D.create(position+Vector2(0,17),position+Vector2(0,26),8)
		if not get_world_2d().direct_space_state.intersect_ray(ray).is_empty():
			drop_left = .32
			velocity.y = 105
		else:
			set_posture("prone" if posture=="crouch" else "crouch")
	touch_down_pressed=false
	if climbing:
		# Align within the ladder lane so shoulders cannot catch adjacent solid ledges.
		var center_x := floorf(position.x/40)*40+20
		velocity.x = clampf((center_x-position.x)*12,-105,105)
		velocity.y = vertical * 105.0
		set_collision_mask_value(4,false)
	else:
		velocity.y += 1250.0 * delta
	set_collision_mask_value(4,not climbing and drop_left<=0)
	if Input.is_action_just_pressed("jump") and (is_on_floor() or world.is_ladder(position)):
		velocity.y = -455.0
	if Input.is_action_just_pressed("jump") and not is_on_floor() and liquid=="":
		if worn("foot")=="equipment.double_jump_shoes" and not air_jump_used:
			velocity.y = -455
			air_jump_used = true
		elif worn("foot")=="equipment.hover_shoes":
			hovering = not hovering
	if liquid in ["water","honey"]:
		velocity.y -= 1250*.78*delta
		velocity.y = clampf(velocity.y*(1-2.6*delta),-265,150)
		if Input.is_action_pressed("jump") or Input.is_action_pressed("climb") or move_axis.y<-.55:
			velocity.y = -225 if liquid=="water" else -100
	elif liquid=="lava":
		velocity.y = minf(velocity.y,-35)
	if hovering:
		velocity.y = 0
	get_parent().tool_system.apply_pull(delta)
	try_step_up(delta)
	move_and_slide()
	if bool(world.map_data.get("metadata",{}).get("horizontal_wrap",false)):
		position.x = wrapf(position.x,12.0,world.dimensions.x*40.0-12.0)
	else:
		position.x = clampf(position.x, 12.0, world.dimensions.x * 40.0 - 12.0)
	var state := "idle"
	if roll_left>0:
		state = "roll"
	elif climbing:
		state = "climb"
	elif not is_on_floor():
		state = "jump" if velocity.y<0 else "fall"
	elif posture=="prone":
		state = "crawl" if absf(velocity.x)>2 else "prone"
	elif posture=="crouch":
		state = "crouch_run" if absf(velocity.x)>2 else "crouch"
	elif absf(velocity.x)>2:
		state = "run" if speed>150 else "walk"
	# Returning from crouch locomotion is already crouched: never replay the stand-to-crouch frame.
	var crouched_transition: bool=state=="crouch" and sprite.animation in ["crouch_walk","crouch_run","crawl","prone","roll"]
	Art.play(sprite,state)
	if crouched_transition and sprite.sprite_frames.has_animation("crouch"):
		sprite.set_frame_and_progress(sprite.sprite_frames.get_frame_count("crouch")-1,1)
		sprite.pause()
	if Input.is_action_just_pressed("interact"):
		interact_requested.emit()
	if position.y > world.dimensions.y * 40.0 + 200:
		position = world.spawn_point()
		velocity = Vector2.ZERO
	queue_redraw()

func take_damage(amount: float, direction: float) -> void:
	if invulnerable>0 or hp<=0 or roll_left>0:
		return
	if worn("body")=="equipment.shield_armor":
		var bag = get_parent().gameplay.bag
		bag.shield_hp = maxf(0,bag.shield_hp-amount)
		invulnerable = .3
		if bag.shield_hp<=0:
			for i in range(bag.slots.size()):
				if bag.slots[i]!=null and bag.slots[i].id=="equipment.shield_armor":
					bag.remove(i,1)
					break
			bag.worn.body=""
			bag.shield_hp=200
		return
	var minimum := 0.0 if bool(get_parent().preferences.get("death_handling",true)) else 1.0
	hp = maxf(minimum,hp-amount)
	invulnerable = .8
	velocity += Vector2(direction*120,-100)
	if hp<=0:
		died.emit()

func revive() -> void:
	hp = 100
	invulnerable = 2
	position = world.spawn_point()
	velocity = Vector2.ZERO

func _draw() -> void:
	if charge>0:
		var threshold := 1.0 if get_parent().actions.mode=="magic" else float(get_parent().gameplay.weapons[equipped].get("heavy_charge_seconds",.55))
		draw_arc(Vector2.ZERO,29,-PI/2,-PI/2+TAU*minf(charge/threshold,1),24,Color.CYAN,2)
	if worn("body")=="equipment.shield_armor":
		for side in [-1,1]:
			draw_line(Vector2(side*25,-22),Vector2(side*25,20),Color(.3,.85,1,.7),3)

func ladder_available() -> bool:
	return world.is_ladder(position) or world.is_ladder(position+Vector2(0,19)) or world.is_ladder(position+Vector2(0,24))

func is_hidden() -> bool:
	return posture=="prone" and swing_left<=0 and charge<=0

func set_posture(next: String) -> bool:
	var sizes := {"stand":Vector2(22,40),"crouch":Vector2(30,36),"prone":Vector2(46,22),"roll":Vector2(42,24)}
	var size: Vector2 = sizes[next]
	var candidate := RectangleShape2D.new()
	candidate.size = size-Vector2.ONE
	var query := PhysicsShapeQueryParameters2D.new()
	query.shape = candidate
	query.transform = Transform2D(0,global_position+Vector2(0,(40-size.y)/2))
	query.collision_mask = 1
	query.exclude = [get_rid()]
	if not get_world_2d().direct_space_state.intersect_shape(query).is_empty():
		return false
	posture = next
	body_shape.shape.size = size
	body_shape.position.y = (40-size.y)/2
	return true

func try_step_up(delta: float) -> bool:
	if not is_on_floor() or velocity.y<0 or climbing or drop_left>0 or absf(velocity.x)<1:
		return false
	var across:=Vector2(velocity.x*delta,0)
	var contact:=KinematicCollision2D.new()
	if not test_move(global_transform,across,contact) or absf(contact.get_normal().x)<.5:
		return false
	# Sweep up, across and down with the CURRENT posture collider; a two-layer wall
	# still blocks the across sweep, and a low ceiling blocks the upward sweep.
	var raised:=global_transform
	var lift:=Vector2(0,-STEP_HEIGHT-.12)
	if test_move(raised,lift): return false
	raised.origin+=lift
	if test_move(raised,across): return false
	raised.origin+=across
	var landing:=KinematicCollision2D.new()
	if not test_move(raised,-lift+Vector2(0,.12),landing): return false
	if landing.get_normal().y>-.7: return false
	var rise:=STEP_HEIGHT+.12-landing.get_travel().y
	if rise<=.05 or rise>STEP_HEIGHT+.2: return false
	global_position.y-=rise
	return true
