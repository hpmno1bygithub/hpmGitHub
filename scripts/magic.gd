extends Node2D
const ORDER=["fireball","waterball","iceball","electricball"]
const NAMES=["火球","水球","冰球","電球"]
const VFX=preload("res://scripts/source_vfx.gd")
var game: Node2D
var selected := "fireball"
var mana:=100.0
var clock:=0.0
var shots: Array=[]
var impacts: Array=[]
var cfg: Dictionary
func _ready() -> void:
	z_index=13
	cfg=JSON.parse_string(FileAccess.get_file_as_string("res://data/phase5_rules.json")).constants
func cycle() -> void: selected=ORDER[(ORDER.find(selected)+1)%4]
func selected_name() -> String: return NAMES[ORDER.find(selected)]
func level_for_charge(seconds: float) -> int:
	return 1 if seconds<float(cfg.MAGIC_CHARGE_LEVEL1_MAX) else (2 if seconds<float(cfg.MAGIC_CHARGE_LEVEL2_MAX) else 3)
func cast(seconds: float, direction: Vector2) -> bool:
	if clock>0 or game.player.hp<=0 or shots.size()>=96: return false
	var level:=level_for_charge(seconds)
	var prefix:=selected.to_upper()
	var multiplier:=float([1,2,6][level-1])
	if selected=="iceball": multiplier=float([1,3,5][level-1])
	var cost:=float(cfg[prefix+"_MANA_COST"])*multiplier
	if mana<cost:
		game.status.text="魔力不足。"
		return false
	mana-=cost
	clock=.2
	var radius:=float(cfg["MAGIC_RADIUS_LEVEL"+str(level)])
	var row: Dictionary={"at":game.actions.origin()+direction.normalized()*16,"motion":direction.normalized()*float(cfg[prefix+"_SPEED"]),"life":float(cfg[prefix+"_LIFETIME"]),"age":0.0,"element":selected,"level":level,"radius":radius,"fragment":false,"mass":multiplier}
	# Original aim target is six tiles away; solve its ballistic arc using nominal speed.
	var delta_to_target: Vector2=game.actions.origin()+direction.normalized()*240-Vector2(row.at)
	var flight:=clampf(delta_to_target.length()/maxf(80,float(cfg[prefix+"_SPEED"])),.12,1.35)
	row.motion=Vector2(delta_to_target.x/flight,(delta_to_target.y-.5*float(cfg[prefix+"_GRAVITY"])*flight*flight)/flight)
	shots.append(row)
	game.status.text="%s・第 %d 級" % [selected_name(),level]
	return true
func hit_creature(shot: Dictionary, actor: CharacterBody2D) -> void:
	var element:=str(shot.element).trim_suffix("ball")
	var level:=int(shot.level)
	var damage:=float(cfg["MAGIC_CREATURE_DAMAGE_"+element.to_upper()][level-1])
	actor.take_hit(damage,signf(shot.motion.x)*float(cfg.MAGIC_CREATURE_KNOCKBACK))
	if element=="ice": actor.slow_left=maxf(actor.slow_left,float(cfg.MAGIC_CREATURE_ICE_SLOW_SECONDS[level-1]))
	if element=="electric": actor.stun_left=maxf(actor.stun_left,float(cfg.MAGIC_CREATURE_ELECTRIC_STUN_SECONDS[level-1]))
func impact(shot: Dictionary, normal:=Vector2.ZERO) -> void:
	impacts.append({"at":shot.at,"element":shot.element,"life":.24,"radius":shot.radius})
	var inside: Vector2=Vector2(shot.at)-normal*.5
	var cell:=Vector2i(floori(inside.x/40),floori(inside.y/40))
	# Melting consumes this impact even for L3; fragments must not erase the resulting water.
	if shot.element=="fireball" and game.environment.melt(cell): return
	if normal==Vector2.ZERO: normal=Vector2.UP
	if int(shot.level)==3 and not bool(shot.fragment) and shot.element in ["fireball","waterball"]:
		# Source L3 splits into six L1 fragments; no recursive splitting.
		for i in range(6):
			if shots.size()>=96: break
			var child: Dictionary=shot.duplicate(true)
			child.at=shot.at+normal*6
			child.level=1
			child.radius=8.0
			child.fragment=true
			child.mass=1.0
			child.age=0.0
			child.life=float(cfg[str(shot.element).to_upper()+"_LIFETIME"])*float(cfg.MAGIC_FRAGMENT_LIFETIME_SCALE)
			child.motion=normal.rotated(lerpf(-1.2,1.2,i/5.0))*float(cfg[str(shot.element).to_upper()+"_SPEED"])*float(cfg.MAGIC_FRAGMENT_SPEED_SCALE)
			shots.append(child)
		return
	game.environment.element_impact(str(shot.element),cell,int(shot.level),float(shot.mass))
func update_shots(delta: float) -> void:
	for i in range(shots.size()-1,-1,-1):
		var shot: Dictionary=shots[i]
		shot.age+=delta
		shot.life-=delta
		var prefix:=str(shot.element).to_upper()
		shot.motion.y=minf(float(cfg[prefix+"_MAX_FALL_SPEED"]),shot.motion.y+float(cfg[prefix+"_GRAVITY"])*delta)
		var next: Vector2=shot.at+shot.motion*delta
		var query:=PhysicsRayQueryParameters2D.create(shot.at,next,1|8|4)
		var hit:=get_world_2d().direct_space_state.intersect_ray(query)
		if not hit.is_empty():
			shot.at=hit.position
			if hit.collider.has_method("take_hit"): hit_creature(shot,hit.collider)
			shots.remove_at(i)
			impact(shot,hit.normal)
		else:
			shot.at=next
			if shot.life<=0 or game.liquids.at(next)=="water":
				shots.remove_at(i)
				impact(shot)
	for i in range(impacts.size()-1,-1,-1):
		impacts[i].life-=delta
		if impacts[i].life<=0: impacts.remove_at(i)
func _physics_process(delta: float) -> void:
	clock=maxf(0,clock-delta)
	mana=minf(100,mana+float(cfg.MANA_RECOVERY_PER_SECOND)*delta)
	update_shots(delta)
	queue_redraw()
func clear() -> void:
	shots.clear()
	impacts.clear()
	clock=0
func _draw() -> void:
	for shot in shots: VFX.bolt(self,shot)
	for fx in impacts:
		var radius:=float(fx.radius)*(2-float(fx.life)/.24)
		var row: Dictionary=fx.duplicate()
		row.radius=radius
		VFX.bolt(self,row)
