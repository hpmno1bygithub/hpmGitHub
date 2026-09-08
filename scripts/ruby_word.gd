extends Control
var character := ""
var phonetic := ""

static func make(character_text: String, sounds: String) -> Control:
	var word = load("res://scripts/ruby_word.gd").new()
	word.character = character_text
	word.phonetic = sounds
	word.custom_minimum_size = Vector2(62,88)
	word.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return word

func _draw() -> void:
	var font := get_theme_default_font()
	draw_string(font,Vector2(0,57),character,HORIZONTAL_ALIGNMENT_LEFT,-1,32)
	var letters := ""
	var tone := ""
	for symbol in phonetic:
		if symbol in ["ˊ","ˇ","ˋ","˙"]:
			tone = symbol
		elif symbol.strip_edges() != "":
			letters += symbol
	var top := 44.0-letters.length()*10.0
	for i in range(letters.length()):
		draw_string(font,Vector2(34,top+18+i*20),letters[i],HORIZONTAL_ALIGNMENT_LEFT,-1,20)
	if tone != "":
		draw_string(font,Vector2(36,top+2) if tone=="˙" else Vector2(51,50),tone,HORIZONTAL_ALIGNMENT_LEFT,-1,17)
