extends CanvasLayer

const Model = preload("res://scripts/study_model.gd")
var model := Model.new()
var game: Node2D
var overlay := Control.new()
var column := VBoxContainer.new()
var title_label := Label.new()
var progress := Label.new()
var question_area := VBoxContainer.new()
var feedback := Label.new()
var input: LineEdit
var foreground := true
var checkpoint_clock := 0.0

func _ready() -> void:
	layer = 40
	process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(overlay)
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.theme = game.safe_ui.theme
	var dim := ColorRect.new()
	dim.color = Color(.025,.045,.07,.96)
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(dim)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.add_child(center)
	var panel := PanelContainer.new()
	panel.custom_minimum_size = Vector2(1040,650)
	center.add_child(panel)
	var margin := MarginContainer.new()
	for side in ["left","right","top","bottom"]:
		margin.add_theme_constant_override("margin_"+side,24)
	panel.add_child(margin)
	margin.add_child(column)
	column.add_theme_constant_override("separation",12)
	title_label.add_theme_font_size_override("font_size",28)
	column.add_child(title_label)
	column.add_child(progress)
	question_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	column.add_child(question_area)
	feedback.add_theme_font_size_override("font_size",20)
	column.add_child(feedback)
	var options := OptionButton.new()
	options.add_item("下回合：預設，每類 2 題／3 分鐘")
	options.add_item("下回合：1 階，每類 5 題／15 分鐘")
	options.add_item("下回合：2 階，每類 10 題／30 分鐘")
	options.item_selected.connect(func(i: int): model.profile=i; model.checkpoint())
	column.add_child(options)
	if not game.test_mode:
		model.restore()
	options.select(model.profile)
	overlay.hide()


func open_round(reason: String) -> void:
	model.start_round(reason)
	game.release_inputs()
	get_tree().paused = true
	overlay.show()
	render_question()

func _process(delta: float) -> void:
	if game.test_mode or not foreground or get_tree().paused or delta>1:
		return
	model.elapsed += delta
	checkpoint_clock += delta
	if checkpoint_clock>=10:
		model.checkpoint()
		checkpoint_clock = 0
	if model.elapsed>=Model.INTERVALS[model.profile]:
		open_round("timer")

func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT or what == NOTIFICATION_APPLICATION_PAUSED:
		foreground = false
		if is_instance_valid(game) and not game.test_mode:
			model.checkpoint()
	elif what == NOTIFICATION_APPLICATION_FOCUS_IN or what == NOTIFICATION_APPLICATION_RESUMED:
		foreground = true

func render_question() -> void:
	for child in question_area.get_children():
		question_area.remove_child(child)
		child.queue_free()
	input = null
	var reason_label := {"entry":"出發前暖身","timer":"休息一下，練習時間","death":"再挑戰前練習","manual":"主動練習"}
	title_label.text = str(reason_label.get(model.reason,"練習時間"))
	progress.text = "乘法 %d/%d　加減法 %d/%d　注音 %d/%d" % [model.passed[0],Model.COUNTS[model.round_profile],model.passed[1],Model.COUNTS[model.round_profile],model.passed[2],Model.COUNTS[model.round_profile]]
	feedback.text = model.feedback
	if model.save_error != OK:
		feedback.text += "（學習進度儲存失敗）"
	if model.stage==3:
		var done := Label.new()
		done.text = "三種題型都完成了！"
		done.add_theme_font_size_override("font_size",36)
		question_area.add_child(done)
		var resume := Button.new()
		resume.text = "繼續遊戲"
		resume.custom_minimum_size.y = 70
		resume.pressed.connect(continue_game)
		question_area.add_child(resume)
		resume.grab_focus()
	elif model.stage<2:
		build_math()
	else:
		build_zhuyin()

func build_math() -> void:
	var equation := Label.new()
	equation.text = "%d  %s  %d  =  ?" % [model.question.a,model.question.op,model.question.b]
	equation.add_theme_font_size_override("font_size",42)
	question_area.add_child(equation)
	input = LineEdit.new()
	input.placeholder_text = "輸入答案，按 Enter 或確認"
	input.max_length = 3
	input.virtual_keyboard_enabled = false
	input.add_theme_font_size_override("font_size",30)
	input.custom_minimum_size.y = 52
	input.text_submitted.connect(func(_text: String): submit_math())
	question_area.add_child(input)
	var grid := GridContainer.new()
	grid.columns = 6
	grid.add_theme_constant_override("h_separation",8)
	grid.add_theme_constant_override("v_separation",8)
	question_area.add_child(grid)
	for text in ["1","2","3","4","5","⌫","6","7","8","9","0","確認"]:
		var button := Button.new()
		button.text = text
		button.custom_minimum_size = Vector2(148,62)
		button.add_theme_font_size_override("font_size",28)
		button.focus_mode = Control.FOCUS_NONE
		button.pressed.connect(func(): keypad(text))
		grid.add_child(button)
	input.grab_focus()

func keypad(text: String) -> void:
	if not is_instance_valid(input):
		return
	if text == "確認":
		submit_math()
	elif text == "⌫":
		input.text = input.text.left(maxi(0,input.text.length()-1))
	elif input.text.length()<3:
		input.text += text

func submit_math() -> void:
	if not is_instance_valid(input):
		return
	model.submit_math(input.text,model.token)
	render_question()

func build_zhuyin() -> void:
	var prompt := Label.new()
	prompt.text = "讀一讀，選出空格裡正確的國字與注音。"
	prompt.add_theme_font_size_override("font_size",23)
	question_area.add_child(prompt)
	var flow := HFlowContainer.new()
	flow.add_theme_constant_override("h_separation",8)
	flow.add_theme_constant_override("v_separation",8)
	question_area.add_child(flow)
	var q: Dictionary = model.question
	for i in range(q.tokens.size()):
		var blank: bool = q.tokens[i] == "__"
		flow.add_child(preload("res://scripts/ruby_word.gd").make("□" if blank else str(q.reference_text)[i], "" if blank else str(q.tokens[i])))
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation",12)
	grid.add_theme_constant_override("v_separation",10)
	question_area.add_child(grid)
	var current_token := model.token
	for index in range(4):
		var button := Button.new()
		var center := CenterContainer.new()
		center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		center.mouse_filter = Control.MOUSE_FILTER_IGNORE
		button.add_child(center)
		center.add_child(preload("res://scripts/ruby_word.gd").make(str(q.choice_characters[index]),str(q.choices[index])))
		button.custom_minimum_size = Vector2(460,94)
		button.add_theme_font_size_override("font_size",28)
		button.disabled = model.wrong_choices.has(index)
		button.pressed.connect(func(): model.choose(index,current_token); render_question())
		grid.add_child(button)

func continue_game() -> void:
	if model.continue_game():
		overlay.hide()
		game.release_inputs()
		if game.player.hp<=0:
			game.player.revive()
		game.save_game()
		get_tree().paused = false
