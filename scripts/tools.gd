extends Node2D
const ORDER=["pickaxe","axe","grapple"]
const NAMES={"pickaxe":"鐵鎬","axe":"斧頭","grapple":"鉤爪"}
var game: Node2D
var selected := "pickaxe"
var clock:=0.0
var hook_state := "idle"
var anchor:=Vector2.ZERO
var hook_motion:=Vector2.ZERO
var travel:=0.0
var rope_length:=0.0
var swing:=0.0
var damage: Dictionary={}
var rules: Dictionary
var tethered: bool:
	get: return hook_state=="latched"
	set(value):
		if not value: detach()
func _ready() -> void:
	z_index=12
	rules=JSON.parse_string(FileAccess.get_file_as_string("res://data/phase5_rules.json")).tools
func selected_name() -> String: return NAMES[selected]
func cycle() -> void:
	detach()
	selected=ORDER[(ORDER.find(selected)+1)%ORDER.size()]
func detach() -> void:
	hook_state="idle"
func protected_cell(cell: Vector2i) -> bool:
	for row in game.world.map_data.metadata.get("indestructible_terrain_cells",[]):
		if int(row[0])==cell.x and int(row[1])==cell.y: return true
	return false
func tool_direction() -> Vector2:
	if game.actions.explicit_aim: return game.actions.direction()
	if Input.is_action_pressed("down"): return Vector2.DOWN
	if Input.is_action_pressed("climb"): return Vector2.UP
	return Vector2(game.player.facing,0)
func target_cell() -> Vector2i:
	var from: Vector2=game.actions.origin()
	var dir:=tool_direction()
	for distance in range(25,131,4):
		var at: Vector2=from+dir*distance
		var cell:=Vector2i(floori(at.x/40),floori(at.y/40))
		var tile: Vector2i=game.world.terrain.get_cell_atlas_coords(cell)
		if game.environment.ice_at(at): return cell
		if tile.x<=0: continue
		# Source tool ray selects the first occupied cell, even after removing its top slice.
		return cell
	return Vector2i(-1,-1)
func use_selected() -> void:
	if game.player.hp<=0: return
	if selected=="grapple": grapple()
	else: mine()
func drop_item(id: String, label: String, count: int, at: Vector2) -> void:
	game.gameplay.drops.append({"id":id,"name":label,"count":count,"x":at.x,"y":at.y})
func collect_tile(tile: int, cell: Vector2i) -> void:
	var row: Array=rules.drops.get(str(tile),["material_"+str(tile),str(game.world.definitions[str(tile)].name),1])
	drop_item(str(row[0]),str(row[1]),int(row[2]),Vector2(cell)*40+Vector2(20,18))
func chop_plant() -> bool:
	var from: Vector2=game.actions.origin()
	var dir:=tool_direction()
	var candidates: Array=[]
	for layer in ["vegetation","decoration"]:
		for row in game.world.map_data.layers.get(layer,[]):
			var kind:=str(row[2])
			if not kind in ["tree","pine","jungle_tree","grass","fern","reed","cactus","berry","moss"]: continue
			var key: String=game.scenery.plant_key(layer,row)
			if game.scenery.removed.has(key): continue
			var pos:=Vector2((float(row[0])+.5)*40,float(row[1])*40)
			if pos.distance_to(from)>270: continue
			var tree: bool=kind in ["tree","pine","jungle_tree"]
			if tree and selected!="axe": continue
			var box:=Rect2(pos-Vector2(22,130 if tree else 34),Vector2(44,130 if tree else 34))
			for distance in range(6,131,4):
				var point: Vector2=from+dir*distance
				if box.has_point(point) and game.world.clear_sight(from,point):
					candidates.append({"key":key,"row":row,"tree":tree,"kind":kind,"at":pos,"distance":distance})
					break
	if candidates.is_empty(): return false
	candidates.sort_custom(func(a,b): return a.distance<b.distance)
	var found: Dictionary=candidates[0]
	var stage:=maxi(1,int(found.row[3]) if found.row.size()>3 else 1)
	game.scenery.removed[found.key]=true
	if found.tree:
		drop_item("wood","木材",maxi(2,stage*2),found.at)
		drop_item("leaf","樹葉",maxi(1,stage-1),found.at+Vector2(6,0))
		if found.kind=="pine": drop_item("pine_cone","松果",maxi(1,stage-1),found.at-Vector2(5,0))
	else: drop_item("reed" if found.kind=="reed" else "leaf","蘆葦" if found.kind=="reed" else "樹葉",stage,found.at)
	game.status.text="砍除樹木" if found.tree else "清除植物"
	game.scenery.queue_redraw()
	game.save_game()
	return true
func mine() -> void:
	if clock>0: return
	clock=.1
	swing=.2
	if chop_plant(): return
	var cell:=target_cell()
	if cell.x<0: return
	if protected_cell(cell):
		game.status.text="這個圖塊受到保護。"
		return
	var tile: Vector2i=game.world.terrain.get_cell_atlas_coords(cell)
	if game.environment.frozen.has(cell) and selected=="pickaxe":
		game.environment.remove_ice(cell)
		collect_tile(6,cell)
		game.save_game()
		return
	var wood: bool=tile.x in rules.wood_tiles
	if (selected=="axe")!=wood:
		game.status.text="木材請使用斧頭；土石請使用鐵鎬。"
		return
	var key:=str(cell)+":"+str(tile)
	damage[key]=float(damage.get(key,0))+1
	if damage[key]<float(rules.hardness.get(str(tile.x),1)): return
	damage.erase(key)
	var offset: Vector2=Vector2(cell)*40+Vector2(20,20)-game.actions.origin()
	var vertical: bool=absf(offset.y)>absf(offset.x)*.88
	var exposed: bool=tile.y!=7 or game.world.terrain.get_cell_source_id(cell+Vector2i.UP)<0 or game.world.terrain.get_cell_source_id(cell+Vector2i.DOWN)<0
	var next_mask:=0
	if vertical and exposed and not wood:
		var bit:=1 if offset.y<0 and tile.y&1 else (2 if offset.y<0 and tile.y&2 else (4 if tile.y&4 else (2 if tile.y&2 else 1)))
		next_mask=tile.y & ~bit
	game.world.edit_cell(cell,1 if tile.x==5 and not next_mask&4 else tile.x,next_mask)
	if next_mask==0: collect_tile(tile.x,cell)
	game.save_game()
func grapple() -> void:
	if hook_state!="idle":
		hook_state="retracting"
		return
	if not game.gameplay.inventory.has("tool_grapple"):
		game.status.text="背包內需要鉤爪。"
		return
	var dir: Vector2=game.actions.direction()
	anchor=game.actions.origin()+dir*8
	hook_motion=dir*920
	travel=8
	hook_state="extending"
func apply_pull(delta: float) -> void:
	if not tethered: return
	var difference: Vector2=anchor-game.actions.origin()
	if difference.length()>432 or Input.is_action_just_pressed("jump") or game.player.hp<=0:
		detach()
		return
	var probe:=PhysicsRayQueryParameters2D.create(anchor-difference.normalized()*3,anchor+difference.normalized()*4,9)
	if get_world_2d().direct_space_state.intersect_ray(probe).is_empty():
		detach()
		return
	rope_length=maxf(44,rope_length-235*delta)
	var normal:=difference.normalized()
	if difference.length()>rope_length:
		var radial: float=game.player.velocity.dot(normal)
		game.player.velocity+=normal*(minf(520,maxf(0,radial)+2350*delta)-radial)
func _physics_process(delta: float) -> void:
	clock=maxf(0,clock-delta)
	swing=maxf(0,swing-delta)
	if game.player.hp<=0: detach()
	if Input.is_action_pressed("mine"):
		if game.actions.mode!="tool": game.actions.select_mode("tool")
		if selected=="grapple": selected="pickaxe"
		mine()
	if Input.is_action_just_pressed("grapple"):
		if game.actions.mode!="tool": game.actions.select_mode("tool")
		selected="grapple"
		grapple()
	if hook_state=="extending":
		var step: Vector2=hook_motion*minf(delta,(360-travel)/920)
		var query:=PhysicsRayQueryParameters2D.create(anchor,anchor+step,9)
		var result:=get_world_2d().direct_space_state.intersect_ray(query)
		if not result.is_empty():
			anchor=result.position
			hook_state="latched"
			rope_length=anchor.distance_to(game.actions.origin())
		else:
			anchor+=step
			travel+=step.length()
			if travel>=359.9: hook_state="retracting"
	elif hook_state=="retracting":
		anchor=anchor.move_toward(game.actions.origin(),1180*delta)
		if anchor.distance_to(game.actions.origin())<8: detach()
	queue_redraw()
func _draw() -> void:
	if hook_state!="idle":
		draw_line(game.actions.origin(),anchor,Color(.75,.72,.6),2)
		draw_arc(anchor,6,-.3,PI*1.5,10,Color.LIGHT_GRAY,3)
	if game.actions.mode=="tool" and selected!="grapple":
		var angle:=tool_direction().angle()+sin(swing/.2*PI)*1.2-.6
		var start: Vector2=game.player.position+Vector2(0,-3)
		var head:=start+Vector2.from_angle(angle)*30
		draw_line(start,head,Color("#92653c"),4)
		if selected=="pickaxe": draw_line(head+Vector2.from_angle(angle+PI/2)*13,head-Vector2.from_angle(angle+PI/2)*13,Color("#b7c6cf"),5)
		else: draw_rect(Rect2(head-Vector2(8,7),Vector2(16,14)),Color("#b7c6cf"))
