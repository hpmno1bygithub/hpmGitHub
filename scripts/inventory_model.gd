extends RefCounted
const SIZE := 72
var slots: Array = []
var worn := {"foot":"","body":"","head":""}
var selected := 0
var shield_hp := 200.0
func _init() -> void:
	slots.resize(SIZE)
func limit(id: String) -> int:
	return 999 if id.begins_with("weapon_") else (1 if id=="tool_grapple" else 99)
func uses_limit(id: String) -> int:
	var rows: Dictionary=preload("res://scripts/content_db.gd").resolve("weapons",JSON.parse_string(FileAccess.get_file_as_string("res://data/weapons.json")))
	return int(rows.get(id.trim_prefix("weapon_"),{}).get("heavy_use_limit",10))
func add(id: String, label: String, count: int, uses: Array = []) -> int:
	var left := maxi(0,count)
	var offset := 0
	for empty in [false,true]:
		for i in range(SIZE):
			if left<=0:
				break
			if empty and slots[i]==null:
				slots[i] = {"id":id,"name":label,"count":0,"uses":[]}
			elif empty or slots[i]==null or slots[i].id!=id:
				continue
			var moved := mini(left,limit(id)-int(slots[i].count))
			slots[i].count += moved
			if id.begins_with("weapon_"):
				for j in range(moved):
					slots[i].uses.append(clampi(int(uses[offset+j]),1,uses_limit(id)) if offset+j<uses.size() else uses_limit(id))
			left -= moved
			offset += moved
	return count-left
func remove(index: int, count: int, reconcile_after: bool = true) -> Dictionary:
	if index<0 or index>=SIZE or slots[index]==null or count<=0:
		return {}
	var row: Dictionary = slots[index]
	var take := mini(count,int(row.count))
	var result := {"id":row.id,"name":row.name,"count":take,"uses":row.uses.slice(0,take)}
	row.uses = row.uses.slice(take)
	row.count -= take
	if row.count<=0:
		slots[index] = null
	if reconcile_after: reconcile()
	return result
func move(from: int, to: int, count: int) -> void:
	if from==to or from<0 or from>=SIZE or to<0 or to>=SIZE or slots[from]==null:
		return
	var source: Dictionary = slots[from]
	if slots[to]!=null and slots[to].id!=source.id:
		if count>=int(source.count):
			var other = slots[to]
			slots[to] = source
			slots[from] = other
		return
	var room := limit(str(source.id))-(int(slots[to].count) if slots[to]!=null else 0)
	var part := remove(from,mini(room,count),false)
	if part.is_empty():
		return
	if slots[to]==null:
		slots[to] = part
	else:
		slots[to].count += part.count
		slots[to].uses.append_array(part.uses)
	reconcile()
func aggregate() -> Dictionary:
	var result := {}
	for row in slots:
		if row!=null:
			if not result.has(row.id):
				result[row.id] = {"name":row.name,"count":0}
			result[row.id].count += int(row.count)
	return result
func import_legacy(data: Dictionary) -> void:
	slots.fill(null)
	worn = {"foot":"","body":"","head":""}
	shield_hp = 200
	for id in data:
		add(id,str(data[id].get("name",id)),int(data[id].get("count",0)))
	reconcile()
func restore(data: Dictionary) -> void:
	slots.fill(null)
	var rows: Array = data.get("slots",[])
	for i in range(mini(SIZE,rows.size())):
		var row = rows[i]
		if row is Dictionary and int(row.get("count",0))>0:
			var id := str(row.get("id",row.get("item_id","")))
			var count := clampi(int(row.count),1,limit(id))
			var uses: Array = []
			if id.begins_with("weapon_"):
				var old: Array = row.get("uses",row.get("heavy_uses",[]))
				for j in range(count):
					uses.append(clampi(int(old[j]),1,uses_limit(id)) if j<old.size() else uses_limit(id))
			slots[i] = {"id":id,"name":str(row.get("name",id)),"count":count,"uses":uses}
	worn = data.get("worn",{"foot":"","body":"","head":""}).duplicate()
	shield_hp = clampf(float(data.get("shield_hp",200)),0,200)
	reconcile()
func snapshot() -> Dictionary:
	return {"slots":slots.duplicate(true),"worn":worn.duplicate(),"shield_hp":shield_hp}
func reconcile() -> void:
	var owned := aggregate()
	for slot in worn:
		if not owned.has(worn[slot]):
			worn[slot] = ""
func use_heavy(id: String) -> Dictionary:
	for i in range(SIZE):
		if slots[i]!=null and slots[i].id==id:
			slots[i].uses[0] -= 1
			if int(slots[i].uses[0])<=0:
				var lost := remove(i,1)
				lost.uses = [uses_limit(id)]
				return {"accepted":true,"broken":lost}
			return {"accepted":true}
	return {"accepted":false}
