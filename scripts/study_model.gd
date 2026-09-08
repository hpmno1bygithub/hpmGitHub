extends RefCounted

const Store = preload("res://scripts/progress_store.gd")
const SYMBOLS := "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦㄧㄨㄩ"
const COUNTS := [2,5,10]
const INTERVALS := [180.0,900.0,1800.0]
var bank: Dictionary = {}
var catalog: Dictionary = {}
var pools: Dictionary = {}
var issued: Dictionary = {}
var recent_math: Dictionary = {}
var zhuyin_deck: Array = []
var recent_zhuyin: Array = []
var symbol_counts: Dictionary = {}
var tone_counts: Dictionary = {}
var profile := 0
var round_profile := 0
var active := false
var stage := 0
var passed: Array = [0,0,0]
var tasks: Array = []
var question: Dictionary = {}
var wrong_choices: Array = []
var elapsed := 0.0
var reason := "entry"
var feedback := ""
var token := 0
var persistence := true
var path := "user://study_v1.json"
var save_error := OK

func _init() -> void:
	var data: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://assets/study_questions.json"))
	for row in data.questions:
		bank[str(row.id)] = row
	for key in ["multiply","single_add","single_subtract","mixed_add","mixed_subtract"]:
		var mixed: bool = key.begins_with("mixed")
		var op := "×" if key == "multiply" else ("−" if key.ends_with("subtract") else "+")
		catalog[key] = {}
		pools[key] = []
		issued[key] = 0
		recent_math[key] = []
		for a in range(10 if mixed else 1,100 if mixed else 10):
			for b in range(1,10):
				if (op == "−" and a<b) or (op == "+" and a+b>99):
					continue
				if key in ["multiply","single_add"] and a>b:
					continue
				var id := "%d:%s:%d" % [a,op,b]
				catalog[key][id] = {"a":a,"b":b,"op":op,"pool":key,"id":id}

func draw_math(key: String) -> Dictionary:
	if pools[key].is_empty():
		pools[key] = catalog[key].keys()
		pools[key].shuffle()
	var index: int = pools[key].size()-1
	if recent_math[key].has(pools[key][index]):
		for i in range(index,-1,-1):
			if not recent_math[key].has(pools[key][i]):
				index = i
				break
	var id: String = pools[key][index]
	pools[key].remove_at(index)
	issued[key] = int(issued[key])+1
	recent_math[key].append(id)
	if recent_math[key].size()>24:
		recent_math[key].pop_front()
	return catalog[key][id].duplicate()

func start_round(trigger: String) -> bool:
	if active:
		return false
	active = true
	round_profile = profile
	reason = trigger
	stage = 0
	passed = [0,0,0]
	tasks = []
	wrong_choices = []
	for i in range(COUNTS[round_profile]):
		tasks.append(draw_math("multiply"))
	var first_op := "+"
	for i in range(COUNTS[round_profile]):
		if i%2 == 0:
			first_op = "+" if int(issued.single_add)<int(issued.single_subtract) else "−"
			if int(issued.single_add)==int(issued.single_subtract):
				first_op = "+" if randi()%2 == 0 else "−"
		var op := first_op if i%2==0 else ("−" if first_op=="+" else "+")
		tasks.append(draw_math(("single" if i%2==0 else "mixed")+("_add" if op=="+" else "_subtract")))
	question = tasks[0].duplicate()
	token += 1
	feedback = "先完成乘法，再完成加減法與注音。"
	checkpoint()
	return true

func tone(answer: String) -> String:
	if answer.begins_with("˙"):
		return "5"
	for pair in [["ˊ","2"],["ˇ","3"],["ˋ","4"]]:
		if answer.ends_with(pair[0]):
			return pair[1]
	return "1"

func next_zhuyin() -> void:
	if zhuyin_deck.is_empty():
		zhuyin_deck = bank.keys()
	var candidates: Array = zhuyin_deck.duplicate()
	var fresh: Array = candidates.filter(func(id): return not recent_zhuyin.has(id))
	if not fresh.is_empty():
		candidates = fresh
	if not recent_zhuyin.is_empty():
		var last: Dictionary = bank[recent_zhuyin.back()]
		for field in ["answer","category","question_type"]:
			var varied: Array = candidates.filter(func(id): return bank[id].get(field,"") != last.get(field,""))
			if not varied.is_empty():
				candidates = varied
	var best := str(candidates[0])
	var best_score := -INF
	for id in candidates:
		var answer := str(bank[id].answer)
		var score := randf()
		for symbol in SYMBOLS:
			if answer.contains(symbol):
				score += 100.0 if int(symbol_counts.get(symbol,0))==0 else -float(symbol_counts[symbol])*.02
		if int(tone_counts.get(tone(answer),0))==0:
			score += 10
		if score>best_score:
			best = str(id)
			best_score = score
	zhuyin_deck.erase(best)
	recent_zhuyin.append(best)
	if recent_zhuyin.size()>12:
		recent_zhuyin.pop_front()
	question = bank[best].duplicate(true)
	question.kind = "zhuyin"
	# Keep character + syllable paired while shuffling.
	var indices := [0,1,2,3]
	indices.shuffle()
	question.choices = []
	question.choice_characters = []
	for index in indices:
		question.choices.append(bank[best].choices[index])
		question.choice_characters.append(bank[best].choice_characters[index])
	for symbol in SYMBOLS:
		if str(question.answer).contains(symbol):
			symbol_counts[symbol] = int(symbol_counts.get(symbol,0))+1
	var t := tone(str(question.answer))
	tone_counts[t] = int(tone_counts.get(t,0))+1
	wrong_choices = []
	token += 1

func correct() -> void:
	passed[stage] = int(passed[stage])+1
	if int(passed[stage])>=COUNTS[round_profile]:
		stage += 1
	wrong_choices = []
	token += 1
	if stage==3:
		question = {}
		feedback = "全部完成！按「繼續遊戲」出發。"
	elif stage==2:
		next_zhuyin()
		feedback = "答對了！接著練習注音。"
	else:
		question = tasks[int(passed[0]) if stage==0 else COUNTS[round_profile]+int(passed[1])].duplicate()
		feedback = "答對了！請回答下一題。"
	checkpoint()

func submit_math(text: String, expected_token: int) -> bool:
	if not active or stage>=2 or expected_token!=token:
		return false
	var cleaned := text.strip_edges()
	if not cleaned.is_valid_int():
		feedback = "請輸入數字。"
		return false
	var result := int(question.a)*int(question.b) if question.op=="×" else (int(question.a)+int(question.b) if question.op=="+" else int(question.a)-int(question.b))
	if int(cleaned)==result:
		correct()
		return true
	feedback = "再算一次，這題還沒有答對。"
	checkpoint()
	return false

func choose(index: int, expected_token: int) -> bool:
	if not active or stage!=2 or expected_token!=token or index<0 or index>3 or wrong_choices.has(index):
		return false
	if question.choices[index]==question.answer:
		correct()
		return true
	wrong_choices.append(index)
	feedback = "再讀一次，試試其他選項。"
	if wrong_choices.size()>=3:
		next_zhuyin()
		feedback = "換一道題目再練習；答對才會計分。"
	checkpoint()
	return false

func continue_game() -> bool:
	if not active or stage!=3:
		return false
	active = false
	elapsed = 0
	checkpoint()
	return true

func export_state() -> Dictionary:
	return {"schema":1,"profile":profile,"round_profile":round_profile,"active":active,"stage":stage,
		"passed":passed,"tasks":tasks,"question":question,"wrong_choices":wrong_choices,"elapsed":elapsed,
		"reason":reason,"token":token,"pools":pools,"issued":issued,"recent_math":recent_math,
		"zhuyin_deck":zhuyin_deck,"recent_zhuyin":recent_zhuyin,"symbol_counts":symbol_counts,"tone_counts":tone_counts}

func checkpoint() -> void:
	if persistence:
		save_error = Store.save_data(path,export_state())

func restore() -> void:
	var data := Store.load_data(path,1)
	if data.is_empty():
		return
	profile = clampi(int(data.get("profile",0)),0,2)
	for key in catalog:
		var saved = data.get("pools",{}).get(key,[])
		if saved is Array:
			for id in saved:
				if catalog[key].has(id) and not pools[key].has(id):
					pools[key].append(id)
		issued[key] = maxi(0,int(data.get("issued",{}).get(key,0)))
		recent_math[key] = data.get("recent_math",{}).get(key,[])
	zhuyin_deck = data.get("zhuyin_deck",[]).filter(func(id): return bank.has(id))
	recent_zhuyin = data.get("recent_zhuyin",[]).filter(func(id): return bank.has(id))
	symbol_counts = data.get("symbol_counts",{})
	tone_counts = data.get("tone_counts",{})
	elapsed = clampf(float(data.get("elapsed",0)),0,INTERVALS[profile])
	var s := int(data.get("stage",0))
	var p = data.get("passed",[])
	var q = data.get("question",{})
	var ts = data.get("tasks",[])
	var rp := clampi(int(data.get("round_profile",profile)),0,2)
	if not (p is Array and p.size()==3 and q is Dictionary and ts is Array):
		return
	if not bool(data.get("active",false)) or s<0 or s>3 or ts.size()!=COUNTS[rp]*2:
		return
	for i in range(3):
		if int(p[i])<0 or int(p[i])>COUNTS[rp] or (i<s and int(p[i])!=COUNTS[rp]) or (i>s and int(p[i])!=0):
			return
	if s<3 and int(p[s])>=COUNTS[rp]:
		return
	for task in ts:
		if not task is Dictionary or not catalog.has(task.get("pool")) or not catalog[task.pool].has(task.get("id")):
			return
	if s==2 and not bank.has(q.get("id")):
		return
	round_profile = rp
	active = true
	stage = s
	passed = p
	tasks = ts
	question = q
	wrong_choices = data.get("wrong_choices",[])
	reason = str(data.get("reason","entry"))
	token = int(data.get("token",0))+1
	feedback = "繼續上次尚未完成的習題。"
