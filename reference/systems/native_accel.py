# -*- coding: utf-8 -*-
"""Optional Cython/C++ acceleration bridge with a zero-risk fallback.

The compiled module lives next to ``main.py`` rather than inside
``rpg_runtime.zip`` because extension modules cannot be imported from a ZIP.
Every public kernel returns ``None`` when the accelerator is unavailable, so
callers can execute the existing NumPy/Python implementation unchanged.
"""
from __future__ import absolute_import

import importlib
import json
import os
import sys

try:
    from config import NATIVE_ACCELERATION_ENABLED
except Exception:
    NATIVE_ACCELERATION_ENABLED = True

try:
    import numpy as np
except Exception:
    np = None

_MODULE_NAME = "_rpg_accel_kernels"
_REQUIRED_API = 1
_NATIVE = None
_NATIVE_ERROR = "尚未載入"
_NATIVE_DISABLED = False
_POLICY = None


def _project_root():
    root = os.environ.get("PYTO_RPG_PROJECT_ROOT", "")
    if root:
        return os.path.abspath(root)
    # Source-tree validation path: systems/ -> runtime_src/ -> project root.
    here = os.path.abspath(os.path.dirname(__file__))
    parent = os.path.dirname(os.path.dirname(here))
    return parent



def _policy_path():
    return os.path.join(_project_root(), "native_accel_policy.json")


def _load_policy(refresh=False):
    global _POLICY
    if refresh:
        _POLICY = None
    if isinstance(_POLICY, dict):
        return _POLICY
    defaults = {
        "horizontal_water_pass": True,
        "bridge_neighbor_surface_rows": True,
        "equalize_floating_ice_pressure": True,
    }
    path = _policy_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if int(payload.get("api_version", 0)) != _REQUIRED_API:
            raise ValueError("policy API mismatch")
        rows = payload.get("kernels", {})
        if not isinstance(rows, dict):
            raise ValueError("policy kernels missing")
        kernels = {name: bool(rows.get(name, False)) for name in defaults}
        _POLICY = {
            "path": path,
            "source": "device-benchmark",
            "kernels": kernels,
            "benchmarks": payload.get("benchmarks", {}),
        }
    except FileNotFoundError:
        _POLICY = {
            "path": path,
            "source": "default",
            "kernels": defaults,
            "benchmarks": {},
        }
    except Exception as exc:
        # A malformed policy must never block the game. Disable native kernels
        # until build_acceleration.py writes a fresh device-verified policy.
        _POLICY = {
            "path": path,
            "source": "invalid: %s" % exc,
            "kernels": {name: False for name in defaults},
            "benchmarks": {},
        }
    return _POLICY


def _kernel_enabled(name):
    return bool(_load_policy().get("kernels", {}).get(str(name), False))

def _ensure_project_path():
    root = _project_root()
    if root and root not in sys.path:
        # Keep rpg_runtime.zip first; the extension has a unique top-level name.
        sys.path.append(root)
    return root


def _load_native(refresh=False):
    global _NATIVE, _NATIVE_ERROR, _NATIVE_DISABLED
    disabled_by_env = str(os.environ.get("PYTO_RPG_DISABLE_NATIVE_ACCEL", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )
    if not bool(NATIVE_ACCELERATION_ENABLED) or disabled_by_env:
        _NATIVE = None
        _NATIVE_ERROR = "已由設定停用"
        _NATIVE_DISABLED = True
        return None
    if refresh:
        _NATIVE = None
        _NATIVE_ERROR = "重新偵測中"
        _NATIVE_DISABLED = False
        _load_policy(refresh=True)
        sys.modules.pop(_MODULE_NAME, None)
        importlib.invalidate_caches()
    if _NATIVE is not None:
        return _NATIVE
    if _NATIVE_DISABLED:
        return None
    _ensure_project_path()
    try:
        module = importlib.import_module(_MODULE_NAME)
        api = int(getattr(module, "ACCEL_API_VERSION", 0))
        if api != _REQUIRED_API:
            raise RuntimeError(
                "加速核心 API 不相容：需要 %d，實際 %d" % (_REQUIRED_API, api)
            )
        for name in (
            "horizontal_water_pass",
            "bridge_neighbor_surface_rows",
            "equalize_floating_ice_pressure",
        ):
            if not callable(getattr(module, name, None)):
                raise RuntimeError("加速核心缺少函式：" + name)
        _NATIVE = module
        _NATIVE_ERROR = ""
        return module
    except Exception as exc:
        _NATIVE = None
        _NATIVE_ERROR = "%s: %s" % (type(exc).__name__, exc)
        # Import is attempted once per launch. build_acceleration.py/status can
        # explicitly refresh after a new extension has been compiled.
        _NATIVE_DISABLED = True
        return None


def _disable_after_runtime_error(exc):
    global _NATIVE, _NATIVE_ERROR, _NATIVE_DISABLED
    _NATIVE = None
    _NATIVE_ERROR = "執行失敗，已退回 NumPy：%s: %s" % (type(exc).__name__, exc)
    _NATIVE_DISABLED = True


def acceleration_status(refresh=False):
    policy = _load_policy(refresh=bool(refresh))
    module = _load_native(refresh=bool(refresh))
    configured = dict(policy.get("kernels", {}) or {})
    # ``kernels`` is the effective state, not merely the requested policy. This
    # keeps the launcher/status screen honest when no extension has been built.
    kernels = {
        name: bool(module is not None and enabled)
        for name, enabled in configured.items()
    }
    enabled_count = sum(1 for value in kernels.values() if value)
    return {
        "available": enabled_count > 0,
        "module_loaded": module is not None,
        "backend": "cython-c++" if enabled_count > 0 else "numpy-python",
        "module_path": str(getattr(module, "__file__", "") or "") if module else "",
        "api_version": int(getattr(module, "ACCEL_API_VERSION", 0) or 0) if module else 0,
        "kernels": kernels,
        "configured_kernels": configured,
        "policy_source": str(policy.get("source", "default")),
        "policy_path": str(policy.get("path", "")),
        "benchmarks": dict(policy.get("benchmarks", {}) or {}),
        "error": "" if module is not None else str(_NATIVE_ERROR or "未編譯"),
    }


def _float32_c(array):
    return (
        np is not None
        and isinstance(array, np.ndarray)
        and array.dtype == np.float32
        and bool(array.flags.c_contiguous)
        and array.ndim == 2
    )


def _bool_c(array):
    return (
        np is not None
        and isinstance(array, np.ndarray)
        and array.dtype == np.bool_
        and bool(array.flags.c_contiguous)
        and array.ndim == 2
    )


def horizontal_water_pass(
    water, solid, capacity, supported,
    rate_scale, epsilon, level_relax, side_flow,
):
    if not _kernel_enabled("horizontal_water_pass"):
        return None
    module = _load_native()
    if module is None:
        return None
    if not (
        _float32_c(water)
        and _bool_c(solid)
        and _float32_c(capacity)
        and _bool_c(supported)
    ):
        return None
    try:
        return float(module.horizontal_water_pass(
            water,
            solid.view(np.uint8),
            capacity,
            supported.view(np.uint8),
            float(rate_scale),
            float(epsilon),
            float(level_relax),
            float(side_flow),
        ))
    except Exception as exc:
        _disable_after_runtime_error(exc)
        return None


def bridge_neighbor_surface_rows(
    water, solid, capacity, rate_scale,
    flow_epsilon, bridge_epsilon, bridge_relax, bridge_max_flow,
):
    if not _kernel_enabled("bridge_neighbor_surface_rows"):
        return None
    module = _load_native()
    if module is None:
        return None
    if not (_float32_c(water) and _bool_c(solid) and _float32_c(capacity)):
        return None
    try:
        return float(module.bridge_neighbor_surface_rows(
            water,
            solid.view(np.uint8),
            capacity,
            float(rate_scale),
            float(flow_epsilon),
            float(bridge_epsilon),
            float(bridge_relax),
            float(bridge_max_flow),
        ))
    except Exception as exc:
        _disable_after_runtime_error(exc)
        return None


def equalize_floating_ice_pressure(
    water, solid, ice, rate_scale,
    flow_epsilon, pressure_min_column, pressure_relax, pressure_max_flow,
):
    if not _kernel_enabled("equalize_floating_ice_pressure"):
        return None
    module = _load_native()
    if module is None:
        return None
    if not (_float32_c(water) and _bool_c(solid) and _bool_c(ice)):
        return None
    try:
        return float(module.equalize_floating_ice_pressure(
            water,
            solid.view(np.uint8),
            ice.view(np.uint8),
            float(rate_scale),
            float(flow_epsilon),
            float(pressure_min_column),
            float(pressure_relax),
            float(pressure_max_flow),
        ))
    except Exception as exc:
        _disable_after_runtime_error(exc)
        return None


def _python_horizontal_reference(
    water, solid, capacity, supported,
    rate_scale, epsilon, level_relax, side_flow,
):
    """Small reference used only by acceleration_status.py/validation."""
    for parity in (0, 1):
        left = water[:, parity:-1:2]
        right = water[:, parity + 1::2]
        cap_l = capacity[:, parity:-1:2]
        cap_r = capacity[:, parity + 1::2]
        sup_l = supported[:, parity:-1:2]
        sup_r = supported[:, parity + 1::2]
        shared = np.minimum(cap_l, cap_r)
        surf_l = cap_l - left
        surf_r = cap_r - right
        head = (left - cap_l) - (right - cap_r)
        raw = head * (0.5 * float(level_relax) * float(rate_scale))
        raw = np.clip(
            raw,
            -float(side_flow) * float(rate_scale),
            float(side_flow) * float(rate_scale),
        )
        open_edge = (
            (shared > 1e-7)
            & (~solid[:, parity:-1:2])
            & (~solid[:, parity + 1::2])
        )
        can_lr = surf_l < shared - 1e-6
        can_rl = surf_r < shared - 1e-6
        pos = (raw > float(epsilon)) & sup_l & open_edge & can_lr
        neg = (raw < -float(epsilon)) & sup_r & open_edge & can_rl
        flux = np.zeros_like(raw, dtype=np.float32)
        if np.any(pos):
            flux[pos] = np.minimum(raw[pos], left[pos])
            flux[pos] = np.minimum(
                flux[pos], np.maximum(0.0, cap_r[pos] - right[pos])
            )
        if np.any(neg):
            q = np.minimum(-raw[neg], right[neg])
            q = np.minimum(q, np.maximum(0.0, cap_l[neg] - left[neg]))
            flux[neg] = -q
        left -= flux
        right += flux
    return water


def run_self_test():
    """Compare native horizontal flow with the authoritative NumPy formula."""
    if np is None:
        return {"ok": False, "mass_error": None, "max_cell_error": None, "reason": "NumPy unavailable"}
    rng = np.random.default_rng(770077)
    water = rng.random((9, 18), dtype=np.float32)
    capacity = rng.uniform(0.34, 1.0, (9, 18)).astype(np.float32)
    water = np.minimum(water, capacity).astype(np.float32, copy=False)
    solid = rng.random((9, 18)) < 0.12
    water[solid] = 0.0
    capacity[solid] = 0.0
    supported = rng.random((9, 18)) > 0.25
    supported &= ~solid

    expected = water.copy()
    _python_horizontal_reference(expected, solid, capacity, supported, 0.5, 1e-4, 0.55, 0.18)
    actual = water.copy()
    status = acceleration_status()
    use_native = bool(status.get("kernels", {}).get("horizontal_water_pass", False))
    if use_native:
        moved = horizontal_water_pass(actual, solid, capacity, supported, 0.5, 1e-4, 0.55, 0.18)
        if moved is None:
            return {"ok": False, "mass_error": None, "max_cell_error": None, "reason": _NATIVE_ERROR}
    else:
        # Fallback is the same vectorized reference, so status still verifies
        # that the package is healthy before native compilation or when the
        # device benchmark intentionally disables this individual kernel.
        _python_horizontal_reference(actual, solid, capacity, supported, 0.5, 1e-4, 0.55, 0.18)

    max_error = float(np.max(np.abs(actual - expected)))
    mass_error = abs(float(np.sum(actual, dtype=np.float64)) - float(np.sum(water, dtype=np.float64)))
    ok = bool(np.all(np.isfinite(actual)) and max_error <= 2.5e-6 and mass_error <= 2.5e-5)
    return {
        "ok": ok,
        "mass_error": mass_error,
        "max_cell_error": max_error,
        "backend": "cython-c++" if use_native else "numpy-python",
    }
