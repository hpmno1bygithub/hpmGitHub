# -*- coding: utf-8 -*-
"""Universal iPhone / iPad landscape helpers for Pyto/iOS.

FIX31.1 Universal iOS keeps one project for both device families.

Phone path
----------
The existing strict landscape controller policy is retained because it is the
proven path on the iPhone build this project has been developed on.

iPad path
---------
iPad / iPad Pro intentionally avoids private-style controller class mutation
(`object_setClass`) and the legacy UIDevice KVC orientation write.  Pyto can be
hosted by a different controller hierarchy on iPadOS (including Stage Manager
and multitasking); mutating that hierarchy can terminate the entire Pyto
process instead of producing a catchable Python exception.  iPad uses the
public UIWindowScene geometry request only, and layout is driven by the actual
presented viewport.
"""
import ctypes
import time

LANDSCAPE_MASK = 24  # UIInterfaceOrientationMaskLandscapeLeft | Right
LANDSCAPE_RIGHT = 3

_LOCKED_OBJECT_IDS = set()
_RUNTIME_CALLBACKS = []
_RUNTIME_CLASSES = {}
_IPAD_GEOMETRY_REQUESTED = set()


def device_family():
    """Return ``'ipad'``, ``'iphone'`` or ``'unknown'`` safely."""
    try:
        from UIKit import UIDevice
        value = getattr(UIDevice.currentDevice, "userInterfaceIdiom", 0)
        if callable(value):
            value = value()
        value = int(value)
        if value == 1:  # UIUserInterfaceIdiomPad
            return "ipad"
        if value == 0:  # UIUserInterfaceIdiomPhone
            return "iphone"
    except Exception:
        pass
    return "unknown"


def is_ipad():
    return device_family() == "ipad"


def is_iphone():
    return device_family() == "iphone"


def _orientation_value(value):
    try:
        return int(value)
    except Exception:
        try:
            return int(str(value))
        except Exception:
            return None


def wait_for_landscape():
    """Startup orientation gate shared by iPhone and iPad.

    On iPhone we preserve the existing physical-orientation wait before
    fullscreen creation.  On iPad we must not force native orientation before
    Pyto has presented its window.  The host layout gate will simply wait for
    width > height; if the iPad is portrait, rotate it after the view appears.
    """
    family = device_family()
    print("")
    print("================================================")
    print("Pyto RPG - Universal iOS landscape startup")
    print("================================================")
    print("device family:", family)

    if family == "ipad":
        print("iPad / iPad Pro：使用安全 UIWindowScene 橫屏策略。")
        print("不使用 object_setClass，也不使用 UIDevice KVC 強制旋轉。")
        print("請以橫向執行；若目前是直向，畫面出現後轉成橫向即可。")
        return

    try:
        from UIKit import UIDevice
        device = UIDevice.currentDevice
        try:
            device.beginGeneratingDeviceOrientationNotifications()
        except Exception:
            pass
    except Exception as exc:
        print("無法讀取 UIKit/UIDevice：", repr(exc))
        print("請先把 Pyto 轉成橫向，再重新執行。")
        raise SystemExit

    print("iPhone：請先將 Pyto 編輯器轉成橫向。")
    print("偵測到橫向後建立介面，之後維持既有 Landscape lock。")
    last_message = 0.0
    while True:
        try:
            orientation = _orientation_value(device.orientation)
        except Exception:
            orientation = None
        if orientation in (3, 4):
            print("已偵測到橫向，開始建立介面。")
            return
        now = time.monotonic()
        if now - last_message > 3.0:
            print("等待橫向中（目前方向：%s）" % ("unknown" if orientation is None else orientation))
            last_message = now
        time.sleep(0.12)


def _root_owner_view_controller(root_view):
    try:
        responder = root_view.__py_view__.managed
    except Exception:
        return None
    for _ in range(20):
        try:
            responder = responder.nextResponder
        except Exception:
            responder = None
        if responder is None:
            return None
        try:
            getattr(responder, "view")
            getattr(responder, "presentedViewController")
            getattr(responder, "dismissViewControllerAnimated")
            return responder
        except Exception:
            pass
    return None


def _objc_ptr(obj):
    for name in ("ptr", "_objc_ptr", "__objc_ptr__", "_as_parameter_"):
        try:
            value = getattr(obj, name)
            if callable(value):
                value = value()
            if isinstance(value, ctypes.c_void_p):
                return int(value.value or 0)
            if hasattr(value, "value"):
                value = value.value
            iv = int(value)
            if iv:
                return iv
        except Exception:
            pass
    try:
        from rubicon.objc import ObjCInstance
        wrapped = obj if isinstance(obj, ObjCInstance) else ObjCInstance(obj)
        value = wrapped.ptr
        if isinstance(value, ctypes.c_void_p):
            return int(value.value or 0)
        if hasattr(value, "value"):
            return int(value.value or 0)
        return int(value)
    except Exception:
        return 0


def _install_runtime_orientation_subclass(owner):
    """iPhone-only strict landscape controller policy."""
    owner_ptr = _objc_ptr(owner)
    if not owner_ptr:
        return False
    if owner_ptr in _LOCKED_OBJECT_IDS:
        return True
    try:
        lib = ctypes.CDLL(None)
        object_getClass = lib.object_getClass
        object_getClass.argtypes = [ctypes.c_void_p]
        object_getClass.restype = ctypes.c_void_p
        class_getName = lib.class_getName
        class_getName.argtypes = [ctypes.c_void_p]
        class_getName.restype = ctypes.c_char_p
        objc_allocateClassPair = lib.objc_allocateClassPair
        objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        objc_allocateClassPair.restype = ctypes.c_void_p
        objc_registerClassPair = lib.objc_registerClassPair
        objc_registerClassPair.argtypes = [ctypes.c_void_p]
        class_addMethod = lib.class_addMethod
        class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        class_addMethod.restype = ctypes.c_bool
        sel_registerName = lib.sel_registerName
        sel_registerName.argtypes = [ctypes.c_char_p]
        sel_registerName.restype = ctypes.c_void_p
        object_setClass = lib.object_setClass
        object_setClass.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        object_setClass.restype = ctypes.c_void_p

        original = object_getClass(ctypes.c_void_p(owner_ptr))
        if not original:
            return False
        original_name = class_getName(original) or b"PytoVC"
        cache_key = int(original)
        subclass = _RUNTIME_CLASSES.get(cache_key)
        if not subclass:
            safe_name = original_name.decode("utf-8", "ignore").replace(".", "_")
            class_name = ("PytoRPGLandscapeLocked_%s_%x" % (safe_name[-50:], cache_key & 0xFFFFF)).encode("ascii", "ignore")
            subclass = objc_allocateClassPair(original, class_name, 0)
            created = bool(subclass)
            if not subclass:
                try:
                    objc_getClass = lib.objc_getClass
                    objc_getClass.argtypes = [ctypes.c_char_p]
                    objc_getClass.restype = ctypes.c_void_p
                    subclass = objc_getClass(class_name)
                except Exception:
                    subclass = None
            if not subclass:
                return False

            if created:
                RET_UINT = ctypes.CFUNCTYPE(ctypes.c_ulonglong, ctypes.c_void_p, ctypes.c_void_p)
                RET_BOOL = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                RET_INT = ctypes.CFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_void_p)
                supported_cb = RET_UINT(lambda _self, _cmd: LANDSCAPE_MASK)
                autorotate_cb = RET_BOOL(lambda _self, _cmd: False)
                preferred_cb = RET_INT(lambda _self, _cmd: LANDSCAPE_RIGHT)
                _RUNTIME_CALLBACKS.extend((supported_cb, autorotate_cb, preferred_cb))
                class_addMethod(subclass, sel_registerName(b"supportedInterfaceOrientations"), ctypes.cast(supported_cb, ctypes.c_void_p), b"Q@:")
                class_addMethod(subclass, sel_registerName(b"shouldAutorotate"), ctypes.cast(autorotate_cb, ctypes.c_void_p), b"B@:")
                class_addMethod(subclass, sel_registerName(b"preferredInterfaceOrientationForPresentation"), ctypes.cast(preferred_cb, ctypes.c_void_p), b"q@:")
                objc_registerClassPair(subclass)
            _RUNTIME_CLASSES[cache_key] = subclass

        object_setClass(ctypes.c_void_p(owner_ptr), subclass)
        _LOCKED_OBJECT_IDS.add(owner_ptr)
        try:
            owner.setNeedsUpdateOfSupportedInterfaceOrientations()
        except Exception:
            pass
        print("LANDSCAPE LOCK: iPhone Pyto controller pinned to Landscape")
        return True
    except Exception as exc:
        print("LANDSCAPE LOCK: iPhone runtime subclass unavailable:", repr(exc))
        return False


def _request_scene_landscape(root_view, allow_legacy_kvc=False):
    """Request landscape using public UIWindowScene API when available."""
    ok = False
    try:
        native = root_view.__py_view__.managed
        window = native.window
        scene = window.windowScene if window is not None else None
        if scene is not None:
            try:
                from UIKit import UIWindowSceneGeometryPreferencesIOS
                prefs = UIWindowSceneGeometryPreferencesIOS.alloc().initWithInterfaceOrientations_(LANDSCAPE_MASK)
                try:
                    scene.requestGeometryUpdateWithPreferences_errorHandler_(prefs, None)
                except Exception:
                    scene.requestGeometryUpdateWithPreferences_errorHandler(prefs, None)
                ok = True
            except Exception:
                pass
    except Exception:
        pass

    # Keep the old KVC fallback ONLY on the proven iPhone path.
    if allow_legacy_kvc:
        try:
            from UIKit import UIDevice
            device = UIDevice.currentDevice
            try:
                device.setValue_forKey_(LANDSCAPE_RIGHT, "orientation")
                ok = True
            except Exception:
                pass
        except Exception:
            pass
    return ok


def lock_landscape(root_view):
    """Apply the correct landscape strategy for the current device family."""
    if is_ipad():
        # No controller mutation and no KVC on iPad / iPad Pro.
        try:
            native = root_view.__py_view__.managed
            ptr = _objc_ptr(native)
        except Exception:
            ptr = 0
        if ptr and ptr in _IPAD_GEOMETRY_REQUESTED:
            return True
        geometry_ok = _request_scene_landscape(root_view, allow_legacy_kvc=False)
        if ptr and geometry_ok:
            _IPAD_GEOMETRY_REQUESTED.add(ptr)
        if geometry_ok:
            print("LANDSCAPE LOCK: iPad safe UIWindowScene geometry path")
        return bool(geometry_ok)

    owner = _root_owner_view_controller(root_view)
    native_ok = False
    if owner is not None:
        native_ok = _install_runtime_orientation_subclass(owner)
        try:
            owner.setNeedsUpdateOfSupportedInterfaceOrientations()
        except Exception:
            pass
    geometry_ok = _request_scene_landscape(root_view, allow_legacy_kvc=True)
    return bool(native_ok or geometry_ok)


def reassert_landscape(root_view):
    try:
        return lock_landscape(root_view)
    except Exception:
        return False
