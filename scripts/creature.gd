extends CharacterBody2D

const Art = preload("res://scripts/art.gd")
signal defeated(actor)
var config: Dictionary
var placement: Dictionary
var world: Node2D
var target: CharacterBody2D
var sprite: AnimatedSprite2D
var home := Vector2.ZERO
var hp := 1.0
var max_hp := 1.0
var body_size := Vector2(32,32)
var direction := 1.0
var attack_cooldown := 1.5
var respawn_left := 0.0
var hurt_flash := 0.0
var slow_left:=0.0
var stun_left:=0.0
var provoked := false
var dead := false
var clock := 0.0
var simulation_enabled := true
var name_label := Label.new()
var actions := preload("res://scripts/creature_actions.gd").new()

func _ready() -> void:
	actions.configure(self)
	z_index = 5
	body_size = Vector2(float(config.get("w",36)),float(config.get("h",32)))
	max_hp = float(config.get("hp",80))
	hp = max_hp
	home = Vector2(float(placement.x)*40,float(placement.y)*40-body_size.y*.5)
	var loc := locomotion()
	if placement.get("resolve_floor",false) and loc == "ground":
		home = world.find_floor(home,body_size.y)
	position = home
	direction = -1.0 if posmod(hash(str(placement.entity_id)),2) else 1.0
	collision_layer = 0 if config.get("background_only",false) else 4
	collision_mask = 9
	var shape := CollisionShape2D.new()
	var rectangle := RectangleShape2D.new()
	rectangle.size = body_size
	shape.shape = rectangle
	add_child(shape)
	sprite = Art.make_sprite("creature."+str(placement.species),body_size)
	add_child(sprite)
	name_label.text = str(placement.get("name",config.get("name",placement.species)))
	name_label.add_theme_font_size_override("font_size",14)
	name_label.position = Vector2(-body_size.x*.5,-body_size.y*.5-27)
	name_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(name_label)

func locomotion() -> String:
	var mode := str(placement.get("habitat_mode","default"))
	if mode == "fly_rest":
		return "fly"
	return str(placement.get("locomotion",config.get("locomotion","ground")))

func hostile() -> bool:
	var behavior := str(placement.get("behavior_mode","default"))
	if behavior == "passive":
		return false
	if behavior == "hostile":
		return true
	if behavior == "retaliate":
		return provoked
	return bool(config.get("hostile",false)) or provoked

func _physics_process(delta: float) -> void:
	if not simulation_enabled:
		return
	if dead:
		if placement.get("respawn",config.get("respawn_enabled",true)):
			respawn_left -= delta
			if respawn_left <= 0:
				dead = false
				hp = max_hp
				position = home
				provoked = false
				attack_cooldown = 1.5
		return
	var near := position.distance_squared_to(target.position) < 1500.0*1500.0
	visible = near
	if not near:
		return
	slow_left=maxf(0,slow_left-delta)
	stun_left=maxf(0,stun_left-delta)
	if stun_left>0:
		velocity.x=0
		sprite.modulate=Color(1,1,.3)
		return
	clock += delta
	attack_cooldown = maxf(0,attack_cooldown-delta)
	hurt_flash = maxf(0,hurt_flash-delta)
	sprite.modulate = Color(1,.4,.4) if hurt_flash>0 else Color.WHITE
	var difference := target.position-position
	name_label.visible = difference.length()<220 or hp<max_hp
	var detect := float(placement.get("detect_tiles",0))*40
	if detect <= 0:
		detect = 280
	var vertical_detect:=180.0
	for row in actions.profile.get("actions",{}).values():
		detect=maxf(detect,float(row.get("range_max",0)))
		vertical_detect=maxf(vertical_detect,float(row.get("vertical_range",0)))
	var chase: bool = not target.is_hidden() and hostile() and absf(difference.x)<detect and absf(difference.y)<vertical_detect
	if actions.tick(delta,chase):
		return
	var movement := str(placement.get("move_mode","default"))
	if movement == "patrol":
		chase = false
	if chase:
		direction = signf(difference.x)
	elif absf(position.x-home.x)>float(placement.get("patrol",3))*40 or is_on_wall():
		direction = signf(home.x-position.x)
	var speed := float(config.get("speed",48))*float(placement.get("speed_scale",1))
	speed*=.45 if slow_left>0 else 1.0
	if movement == "hold" or (movement == "chase" and not chase):
		speed = 0
	var loc := locomotion()
	if loc == "ground" or loc == "swim":
		velocity.y += 1250*delta
		velocity.x = move_toward(velocity.x,direction*speed,500*delta)
		if is_on_wall() and is_on_floor() and speed>0:
			velocity.y = -320
	elif loc in ["stationary","plant","ceiling","rooted"] or float(config.get("speed",48))<=0:
		velocity = Vector2.ZERO
	else:
		velocity.x = direction*speed
		velocity.y = clampf((home.y+sin(clock*1.6)*18-position.y)*2,-45,45)
		if loc in ["fish","water","waterbird"]:
			var next := position+Vector2(direction*(body_size.x*.5+12),0)
			if not world.has_water(next):
				direction *= -1
				velocity.x = direction*speed
	move_and_slide()
	if position.y > world.dimensions.y*40+150:
		position = home
		velocity = Vector2.ZERO
	sprite.flip_h = direction < 0
	Art.play(sprite,"move" if absf(velocity.x)>2 else "idle")
	if chase and not actions.profile.get("override_legacy_contact",false) and str(placement.get("attack_mode","default")) != "none" and attack_cooldown<=0:
		if absf(difference.x) < body_size.x*.5+25 and absf(difference.y) < body_size.y*.5+25:
			if world.clear_sight(position,target.position):
				target.take_damage(float(config.get("attack",8)),direction)
				attack_cooldown = maxf(.25,float(placement.get("attack_cooldown_scale",1)))
	queue_redraw()

func take_hit(damage: float, knockback: float) -> void:
	if dead or bool(config.get("background_only",false)):
		return
	hp = maxf(0,hp-damage)
	provoked = true
	hurt_flash = .18
	velocity.x += knockback
	if hp<=0:
		actions.active.clear()
		dead = true
		visible = false
		respawn_left = float(placement.get("respawn_seconds",config.get("respawn_seconds",120)))
		defeated.emit(self)
	queue_redraw()

func snapshot() -> Dictionary:
	return {"hp":hp,"x":position.x,"y":position.y,"dead":dead,"respawn_left":respawn_left}

func restore(data: Dictionary) -> void:
	hp = clampf(float(data.get("hp",max_hp)),0,max_hp)
	dead = bool(data.get("dead",hp<=0))
	respawn_left = maxf(0,float(data.get("respawn_left",120)))
	position = Vector2(float(data.get("x",home.x)),float(data.get("y",home.y)))
	visible = not dead

func _draw() -> void:
	if dead:
		return
	if not actions.active.is_empty():
		var radius:=float(actions.active.get("hit_radius",30))
		draw_arc(Vector2.ZERO,radius,0,TAU*clampf(actions.elapsed/maxf(.01,actions.duration),0,1),24,Color(1,.4,.2,.4),2)
	var top := -body_size.y*.5-7
	draw_rect(Rect2(-body_size.x*.5,top,body_size.x,4),Color("#332a31"))
	draw_rect(Rect2(-body_size.x*.5,top,body_size.x*hp/max_hp,4),Color("#dd786a") if hostile() else Color("#85ba80"))
