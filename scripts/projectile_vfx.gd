extends RefCounted
## Port of metal_renderer.py:7503–7666, preserving its bounded pixel cores and flight history.
const V=preload("res://scripts/source_vfx.gd")
static func trail(canvas: Node2D,shot: Dictionary,hand: Vector2) -> void:
	var kind:=str(shot.get("kind","energy_arrow"))
	var at: Vector2=shot.at
	var dir: Vector2=Vector2(shot.motion).normalized()
	var history: Array=shot.get("trail",[])
	var count:=mini(history.size(),14 if kind=="laser" else 8)
	for i in range(count):
		var point: Vector2=history[history.size()-count+i]
		var fade: float=(i+1)/float(maxi(1,count))
		var color:=Color(.15,.76,1,.14+.52*fade)
		var size:=Vector2.ONE*(2.5+i*.18)
		if kind=="laser":
			if i%2==1 and i<count-2: continue
			color=Color(.12,.78,1,.16+.62*fade)
			size=Vector2.ONE*(4.6 if shot.get("heavy",false) else 3.4)
		elif kind=="rocket":
			point-=dir*3
			size=Vector2.ONE*(3+fade*2.2)
			color=Color(1,.24+.45*fade,.06,.18+.56*fade)
		elif kind=="tnt":
			point-=dir*2
			size=Vector2.ONE*(3.2+fade*2)
			color=Color(.3+.12*fade,.27+.1*fade,.24+.08*fade,.12+.36*fade)
		elif kind=="battle_top":
			size=Vector2(4+i*.25,2.6)
			color=Color(1,.42,.12,.1+i*.07)
		V.quad(canvas,point,size,color)
	if kind=="yoyo":
		var steps:=clampi(int(hand.distance_to(at)/8)+1,2,18)
		for i in range(1,steps): V.quad(canvas,hand.lerp(at,i/float(steps)),Vector2.ONE*2.2,Color(.72,.86,.92,.78))
static func core(canvas: Node2D,shot: Dictionary) -> void:
	var kind:=str(shot.get("kind","energy_arrow"))
	var at: Vector2=shot.at
	var dir: Vector2=Vector2(shot.motion).normalized()
	var radius:=float(shot.get("radius",4))
	var age:=float(shot.get("age",0))/maxf(.001,float(shot.get("max_life",1)))
	if kind=="energy_arrow":
		for i in range(5): V.quad(canvas,at-dir*i*4,Vector2.ONE*(4.4-i*.38),Color(.18,.78,1,maxf(.16,.82-i*.14)))
		V.quad(canvas,at+dir*4,Vector2.ONE*5,Color(.9,1,1,.98))
	elif kind=="laser":
		var length:=clampf(float(shot.get("row",{}).get("projectile_visual_length",18)),10,36)
		var steps:=clampi(int(length/3)+1,4,9)
		var thickness:=4.8 if shot.get("heavy",false) else 3.6
		for i in range(steps):
			var t:=i/float(maxi(1,steps-1))
			var point:=at-dir*length*t
			V.quad(canvas,point,Vector2.ONE*(thickness+3.2),Color(.1,.7,1,.18+.14*(1-t)))
			V.quad(canvas,point,Vector2.ONE*thickness,Color(.88,1,1,.98-.36*t))
	elif kind=="yoyo":
		V.quad(canvas,at,Vector2.ONE*radius*2.1,Color(.05,.16,.28,.92))
		V.quad(canvas,at,Vector2.ONE*radius*1.55,Color(.18,.78,1,.98))
		V.quad(canvas,at+Vector2.from_angle(age*PI*12)*radius*.45,Vector2.ONE*radius*.48,Color(.9,1,1))
	elif kind=="battle_top":
		var spin:=age*PI*16
		V.quad(canvas,at,Vector2(radius*2.2,radius*.9),Color(.16,.12,.22,.96))
		V.quad(canvas,at+Vector2(cos(spin)*radius*.32,sin(spin)*radius*.16),Vector2(radius*1.45,radius*.62),Color(1,.46,.12,.98))
		V.quad(canvas,at+Vector2(0,radius*.55),Vector2(3,5),Color(.92,.94,1,.95))
	elif kind=="rocket":
		for i in range(6): V.quad(canvas,at-dir*(i/5.0*18-4),Vector2.ONE*5.4,Color(.9,.94,1,.98) if i<3 else Color(.28,.34,.4,.98))
		V.quad(canvas,at+dir*6,Vector2.ONE*4.2,Color(1,.28,.08))
	elif kind=="tnt":
		V.quad(canvas,at-Vector2(5,0),Vector2(6.2,15),Color(.72,.07,.04,.98))
		V.quad(canvas,at,Vector2(6.2,16.5),Color(.91,.1,.05,.99))
		V.quad(canvas,at+Vector2(5,0),Vector2(6.2,15),Color(.72,.07,.04,.98))
		V.quad(canvas,at,Vector2(17,3.8),Color(.12,.1,.1,.99))
		V.quad(canvas,at+Vector2(3,-10),Vector2(3.2,6.2),Color(.66,.48,.22,.96))
		V.quad(canvas,at+Vector2(5.5,-13),Vector2.ONE*4.2,Color(1,.86,.25))
	else:
		var scale:=float(shot.get("visual_scale",1.5))
		var facing:=1 if dir.x>=0 else -1
		for i in range(3):
			for y in [-.92,-.64,-.3,.08,.44,.76,.98]:
				var point:=at+Vector2(facing*((1-absf(y))*7-i*(7+age*4))*scale,y*maxf(9,radius)*scale)
				V.quad(canvas,point,Vector2.ONE*(5.8 if i==0 else 4.2)*scale,Color(245.0/255,247.0/255,250.0/255,maxf(.18,.98-i*.28)))
