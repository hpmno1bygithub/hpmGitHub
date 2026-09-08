# -*- coding: utf-8 -*-
from dataclasses import dataclass


@dataclass
class FireCell:
    intensity: float = 1.0
    fuel: float = 1.0

    # Legacy normalized thermal intensity, kept for renderer compatibility.
    temperature: float = 1.0

    # Absolute temperature used by ThermalSystem.
    temperature_c: float = 620.0

    penetration_left: int = 0
    source: str = "environment"
    stack_level: int = 1


@dataclass
class SoilCell:
    moisture: float = 0.20
    fertility: float = 0.80
    plant_cap: int = 3


@dataclass
class PlantCell:
    species: str = "grass"
    growth: float = 0.0
    stage: int = 0
    fruit: int = 0
    alive: bool = True
    biomass: float = 0.035

    # Absolute ecology time state (game seconds).
    age_game_seconds: float = 0.0
    wet_game_seconds: float = 0.0
    fruit_clock_game_seconds: float = 0.0

    # V0.4.20 ecology state.
    max_stage: int = 3
    state: str = "normal"
    recovery_game_seconds: float = 0.0
    regrow_total_game_seconds: float = 0.0
    regrow_progress: float = 0.0
    burn_progress: float = 0.0
    burn_stack: int = 0


@dataclass
class WeatherCell:
    profile: str = "clear"
    rain: float = 0.0
    cloud: float = 0.15
    sunlight: float = 0.85
    temperature: float = 24.0
    wind: float = 0.15
    wind_x: float = 0.0
    wind_y: float = 0.0


class EnvironmentState:
    """Sparse environment state with explicit matter reservoirs.

    Water can exist in:
        free liquid
        soil moisture
        atmospheric vapor
        groundwater
        waterball projectiles (owned by MagicSystem)

    No transfer is allowed to silently delete water. Systems must move mass
    from one reservoir to another.
    """

    def __init__(self):
        self.water = {}
        # FIX35 dynamic magma. Lava uses the same sparse volume model as water
        # but has its own slow solver and cooling progress.
        self.lava = {}
        # FIX74 honey is an independent viscous liquid. It never participates
        # in water evaporation/freezing or lava cooling reactions.
        self.honey = {}
        self.lava_cooling = {}
        self.lava_bottom_sink_mass = 0.0
        # Optional runtime hooks. WaterSystem installs these when the NumPy
        # fast path is available so every external water mutation can update
        # the contiguous C-backed mirror without rescanning the world.
        self._water_change_callback = None
        self._water_reset_callback = None
        self.fire = {}
        self.soil = {}
        # Exposed DIRT/GRASS_DIRT three-segment occupancy. Missing = full.
        self.soil_layers = {}
        self.plants = {}
        self.weather = {}

        # Directional wind field by chunk, derived from temperature gradients.
        self.wind = {}

        # Vapor/evaporation plume state retained for atmosphere/thermal
        # diagnostics. V0.4.18 ambient WIND no longer tilts upward from this.
        self.updraft = {}

        # Transient local lift from moving fireballs / fire explosions.
        # Ambient wind stays horizontal; WindSystem adds vertical lift only
        # from actual fire sources and these fireball impulses.
        self.fireball_lift = {}

        # Reserved electric charge field for the lightning/electric stage.
        self.electric_charge = {}

        # V0.7.0 sparse reactive fields. Only cells that actually contain a
        # transient element allocate Python state; a huge quiet world costs 0.
        self.steam = {}
        self.poison_gas = {}
        # Remaining real seconds for poison-gas fade. Sparse like the gas field.
        self.poison_gas_ttl = {}
        self.acid = {}
        self.poison_liquid = {}
        self.electric_shock = {}
        # Local reaction-created airflow stored per tile; ambient weather wind
        # remains chunk-based in WindSystem.
        self.reaction_wind = {}
        # Magnetic sources are sparse and source-driven: (tx,ty)->(strength,radius).
        self.magnetic_sources = {}
        self._reactive_change_callback = None

        # ----------------------------------------------------
        # Thermal state
        # ----------------------------------------------------
        # Every world tile receives a corresponding ambient temperature.
        self.temperature = {}

        # Liquid temperature is separate from surrounding air/terrain.
        self.water_temperature = {}

        # Region profile assigned per chunk, e.g. polar/desert/rainforest.
        self.climate = {}
        # Authored biome per chunk. Kept separate because swamp and actual
        # rainforest share a humid climate but have different rain targets.
        self.biome = {}

        # Phase-change progress avoids instant freeze/melt from tiny crossings
        # around 0 C.
        self.ice_melt_progress = {}
        self.water_freeze_progress = {}

        # Actual conserved water mass represented by an ICE tile.
        # Map-authored ice without an entry falls back to TileDef.water_mass.
        self.ice_mass = {}

        # V0.7.4.2: sub-visible water/frost stored on naturally freezing
        # surfaces (snow/alpine/polar).  This mass is conserved but not drawn
        # as liquid until it accumulates enough to form visible low ice.
        self.cold_surface_water = {}

        # V0.7.5.2 cosmetic snow/frost cover on the currently exposed
        # surface tile. 0..1 is visual coverage; the underlying snow water
        # budget remains in cloud/rain/cold-surface reservoirs.
        self.snow_cover = {}

        # Conserved water reservoirs.
        self.vapor = {}
        # V0.7.4.0 real condensed atmospheric reservoir.  Keyed as (cx, 0)
        # so clouds are horizontal atmospheric columns independent of cave depth.
        self.cloud_water = {}
        # V0.7.5.5: cloud mass condensed from desert evaporation is tagged as
        # fresh while it remains over desert. Fresh desert cloud is visible and
        # advects normally, but it cannot precipitate until it first leaves the
        # desert biome. This prevents local evaporation -> immediate local rain.
        self.cloud_desert_fresh = {}
        # Visual-only diffuse haze produced by natural evaporation. This is
        # intentionally separate from conserved vapor so baseline humidity does
        # not paint the whole world as steam.
        self.evaporation_haze = {}
        self.groundwater = {}

        # Conserved solid-matter products.
        self.ash = {}
        self.ash_wet_time = {}

        # Dynamic grassland <-> swamp transition clocks.
        self.swamp_wet_time = {}
        self.swamp_dry_time = {}

        self.smoke = {}
        self.organic = {}

    def clear_dynamic(self):
        self.water.clear()
        self.lava.clear()
        self.honey.clear()
        self.lava_cooling.clear()
        self.lava_bottom_sink_mass = 0.0
        cb = getattr(self, "_water_reset_callback", None)
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
        self.fire.clear()
        self.soil.clear()
        self.soil_layers.clear()
        self.plants.clear()
        self.weather.clear()
        self.wind.clear()
        self.updraft.clear()
        self.fireball_lift.clear()
        self.electric_charge.clear()
        self.steam.clear()
        self.poison_gas.clear()
        self.poison_gas_ttl.clear()
        self.acid.clear()
        self.poison_liquid.clear()
        self.electric_shock.clear()
        self.reaction_wind.clear()
        self.magnetic_sources.clear()
        self.temperature.clear()
        self.water_temperature.clear()
        self.climate.clear()
        self.biome.clear()
        self.ice_melt_progress.clear()
        self.water_freeze_progress.clear()
        self.ice_mass.clear()
        self.cold_surface_water.clear()
        self.snow_cover.clear()
        self.vapor.clear()
        self.cloud_water.clear()
        self.cloud_desert_fresh.clear()
        self.evaporation_haze.clear()
        self.groundwater.clear()
        self.ash.clear()
        self.ash_wet_time.clear()
        self.swamp_wet_time.clear()
        self.swamp_dry_time.clear()
        self.smoke.clear()
        self.organic.clear()

    # --------------------------------------------------------
    # Free water
    # --------------------------------------------------------

    def water_amount(self, tx, ty):
        return float(
            self.water.get(
                (int(tx), int(ty)),
                0.0,
            )
        )

    def set_water(self, tx, ty, amount):
        key = (
            int(tx),
            int(ty),
        )

        value = max(
            0.0,
            min(
                1.0,
                float(amount),
            ),
        )

        if value <= 1e-12:
            self.water.pop(
                key,
                None,
            )
            self.water_temperature.pop(
                key,
                None,
            )
            self.water_freeze_progress.pop(
                key,
                None,
            )
        else:
            self.water[key] = value

        cb = getattr(self, "_water_change_callback", None)
        if cb is not None:
            try:
                cb(key[0], key[1], value)
            except Exception:
                pass

        self._notify_reactive_change(key[0], key[1], "water")

    # Kept for compatibility. New gameplay code should prefer
    # WaterSystem.deposit() so overflow is not clipped by one cell.
    def add_water(self, tx, ty, amount):
        self.set_water(
            tx,
            ty,
            self.water_amount(tx, ty)
            + amount,
        )


    # --------------------------------------------------------
    # Dynamic lava / magma
    # --------------------------------------------------------

    def lava_amount(self, tx, ty):
        return float(self.lava.get((int(tx), int(ty)), 0.0))

    def set_lava(self, tx, ty, amount):
        key=(int(tx), int(ty))
        value=max(0.0,min(1.0,float(amount)))
        if value <= 1e-12:
            self.lava.pop(key,None)
            self.lava_cooling.pop(key,None)
        else:
            self.lava[key]=value
        self._notify_reactive_change(key[0],key[1],"lava")
        return value

    def add_lava(self, tx, ty, amount):
        return self.set_lava(tx,ty,self.lava_amount(tx,ty)+float(amount))

    # --------------------------------------------------------
    # Persistent honey (no phase changes)
    # --------------------------------------------------------

    def honey_amount(self, tx, ty):
        return float(self.honey.get((int(tx), int(ty)), 0.0))

    def set_honey(self, tx, ty, amount):
        key = (int(tx), int(ty))
        value = max(0.0, min(1.0, float(amount)))
        if value <= 1e-12:
            self.honey.pop(key, None)
        else:
            self.honey[key] = value
        return value

    def add_honey(self, tx, ty, amount):
        return self.set_honey(tx, ty, self.honey_amount(tx, ty)+float(amount))

    def lava_cooling_amount(self, tx, ty):
        return float(self.lava_cooling.get((int(tx),int(ty)),0.0))

    def set_lava_cooling(self, tx, ty, amount):
        key=(int(tx),int(ty))
        value=max(0.0,min(1.0,float(amount)))
        if value <= 1e-9:
            self.lava_cooling.pop(key,None)
        else:
            self.lava_cooling[key]=value
        self._notify_reactive_change(key[0],key[1],"lava")
        return value

    # --------------------------------------------------------
    # Cold-surface hidden water / frost
    # --------------------------------------------------------

    def cold_surface_amount(self, tx, ty):
        return float(self.cold_surface_water.get((int(tx), int(ty)), 0.0))

    def set_cold_surface_water(self, tx, ty, amount):
        key = (int(tx), int(ty))
        value = max(0.0, float(amount))
        if value <= 1e-12:
            self.cold_surface_water.pop(key, None)
        else:
            self.cold_surface_water[key] = value

    def add_cold_surface_water(self, tx, ty, amount):
        self.set_cold_surface_water(
            tx, ty, self.cold_surface_amount(tx, ty) + float(amount)
        )

    # --------------------------------------------------------
    # V0.7.0 sparse reactive fields
    # --------------------------------------------------------

    def _notify_reactive_change(self, tx, ty, element=None):
        cb = getattr(self, "_reactive_change_callback", None)
        if cb is not None:
            try:
                cb(int(tx), int(ty), element)
            except Exception:
                pass

    def reactive_amount(self, field, tx, ty):
        table = getattr(self, str(field), None)
        if not isinstance(table, dict):
            return 0.0
        return float(table.get((int(tx), int(ty)), 0.0))

    def set_reactive(self, field, tx, ty, amount):
        table = getattr(self, str(field), None)
        if not isinstance(table, dict):
            raise KeyError("unknown reactive field: %s" % (field,))
        key = (int(tx), int(ty))
        value = max(0.0, min(4.0, float(amount)))
        if value <= 1e-9:
            table.pop(key, None)
            if str(field) == "poison_gas":
                self.poison_gas_ttl.pop(key, None)
        else:
            table[key] = value
        self._notify_reactive_change(key[0], key[1], str(field))
        return value

    def add_reactive(self, field, tx, ty, amount):
        return self.set_reactive(
            field, tx, ty, self.reactive_amount(field, tx, ty) + float(amount)
        )

    def set_magnetic_source(self, tx, ty, strength=1.0, radius=8):
        key = (int(tx), int(ty))
        strength = float(strength)
        radius = max(1, int(radius))
        if abs(strength) <= 1e-9:
            self.magnetic_sources.pop(key, None)
        else:
            self.magnetic_sources[key] = (strength, radius)
        self._notify_reactive_change(key[0], key[1], "magnetic")

    # --------------------------------------------------------
    # Thermal helpers
    # --------------------------------------------------------

    def temperature_at(self, tx, ty, default=20.0):
        return float(
            self.temperature.get(
                (int(tx), int(ty)),
                default,
            )
        )

    def set_temperature(self, tx, ty, temperature_c):
        self.temperature[
            (
                int(tx),
                int(ty),
            )
        ] = float(
            temperature_c
        )

    def water_temperature_at(self, tx, ty, default=None):
        key = (
            int(tx),
            int(ty),
        )

        if key in self.water_temperature:
            return float(
                self.water_temperature[
                    key
                ]
            )

        if default is None:
            default = self.temperature_at(
                tx,
                ty,
                20.0,
            )

        return float(
            default
        )

    def set_water_temperature(self, tx, ty, temperature_c):
        key = (
            int(tx),
            int(ty),
        )

        if self.water_amount(
            tx,
            ty,
        ) <= 1e-12:
            self.water_temperature.pop(
                key,
                None,
            )
            return

        self.water_temperature[
            key
        ] = float(
            temperature_c
        )

    def climate_at_chunk(self, cx, cy, default="temperate"):
        return str(
            self.climate.get(
                (
                    int(cx),
                    int(cy),
                ),
                default,
            )
        )

    def biome_at_chunk(self, cx, cy=0, default=""):
        return str(self.biome.get((int(cx), int(cy)), default))

    # --------------------------------------------------------
    # Atmosphere / groundwater
    # --------------------------------------------------------

    def vapor_amount(self, cx, cy):
        return float(
            self.vapor.get(
                (int(cx), int(cy)),
                0.0,
            )
        )

    def add_vapor(self, cx, cy, amount):
        amount = max(
            0.0,
            float(amount),
        )

        if amount <= 0.0:
            return 0.0

        key = (
            int(cx),
            int(cy),
        )

        self.vapor[key] = (
            self.vapor_amount(
                *key
            )
            + amount
        )
        return amount

    def take_vapor(self, cx, cy, amount):
        amount = max(
            0.0,
            float(amount),
        )

        key = (
            int(cx),
            int(cy),
        )

        available = self.vapor_amount(
            *key
        )

        taken = min(
            available,
            amount,
        )

        remaining = (
            available
            - taken
        )

        if remaining <= 1e-9:
            self.vapor.pop(
                key,
                None,
            )
        else:
            self.vapor[key] = remaining

        return taken

    def transfer_vapor(
        self,
        from_cx,
        from_cy,
        to_cx,
        to_cy,
        amount,
    ):
        """Move atmospheric water between chunks without creating/deleting mass."""
        moved = self.take_vapor(
            from_cx,
            from_cy,
            amount,
        )

        if moved > 0.0:
            self.add_vapor(
                to_cx,
                to_cy,
                moved,
            )

        return moved

    def cloud_amount(self, cx, cy=0):
        return float(self.cloud_water.get((int(cx), 0), 0.0))

    def cloud_desert_fresh_amount(self, cx, cy=0):
        key = (int(cx), 0)
        total = self.cloud_amount(cx, 0)
        return max(0.0, min(total, float(self.cloud_desert_fresh.get(key, 0.0))))

    def set_cloud_desert_fresh(self, cx, amount):
        key = (int(cx), 0)
        amount = max(0.0, min(self.cloud_amount(cx, 0), float(amount)))
        if amount <= 1e-9:
            self.cloud_desert_fresh.pop(key, None)
        else:
            self.cloud_desert_fresh[key] = amount
        return amount

    def add_cloud(self, cx, cy, amount, desert_fresh=False):
        amount = max(0.0, float(amount))
        if amount <= 0.0:
            return 0.0
        key = (int(cx), 0)
        self.cloud_water[key] = self.cloud_amount(cx, 0) + amount
        if desert_fresh:
            self.cloud_desert_fresh[key] = (
                self.cloud_desert_fresh_amount(cx, 0) + amount
            )
            self.set_cloud_desert_fresh(cx, self.cloud_desert_fresh[key])
        return amount

    def take_cloud(self, cx, cy, amount):
        """Take arbitrary cloud mass, preserving fresh-tag proportion."""
        amount = max(0.0, float(amount))
        key = (int(cx), 0)
        available = self.cloud_amount(cx, 0)
        taken = min(available, amount)
        if taken <= 0.0:
            return 0.0
        fresh = self.cloud_desert_fresh_amount(cx, 0)
        fresh_taken = taken * (fresh / available) if available > 1e-12 else 0.0
        remain = available - taken
        if remain <= 1e-9:
            self.cloud_water.pop(key, None)
            self.cloud_desert_fresh.pop(key, None)
        else:
            self.cloud_water[key] = remain
            self.set_cloud_desert_fresh(cx, max(0.0, fresh - fresh_taken))
        return taken

    def take_precipitable_cloud(self, cx, cy, amount):
        """Take only mature cloud mass; fresh desert cloud is protected."""
        amount = max(0.0, float(amount))
        key = (int(cx), 0)
        available = self.cloud_amount(cx, 0)
        fresh = self.cloud_desert_fresh_amount(cx, 0)
        mature = max(0.0, available - fresh)
        taken = min(mature, amount)
        if taken <= 0.0:
            return 0.0
        remain = available - taken
        if remain <= 1e-9:
            self.cloud_water.pop(key, None)
            self.cloud_desert_fresh.pop(key, None)
        else:
            self.cloud_water[key] = remain
            # Fresh mass was not consumed by precipitation.
            self.set_cloud_desert_fresh(cx, fresh)
        return taken

    def transfer_cloud(self, from_cx, to_cx, amount):
        moved = self.take_cloud(from_cx, 0, amount)
        if moved > 0.0:
            self.add_cloud(to_cx, 0, moved)
        return moved

    def groundwater_amount(self, cx, cy):
        return float(
            self.groundwater.get(
                (int(cx), int(cy)),
                0.0,
            )
        )

    def add_groundwater(self, cx, cy, amount):
        amount = max(
            0.0,
            float(amount),
        )

        if amount <= 0.0:
            return 0.0

        key = (
            int(cx),
            int(cy),
        )

        self.groundwater[key] = (
            self.groundwater_amount(
                *key
            )
            + amount
        )

        return amount

    # --------------------------------------------------------
    # Burn products
    # --------------------------------------------------------

    def add_ash(self, tx, ty, amount):
        amount = max(
            0.0,
            float(amount),
        )

        if amount <= 0.0:
            return 0.0

        key = (
            int(tx),
            int(ty),
        )

        self.ash[key] = (
            float(
                self.ash.get(
                    key,
                    0.0,
                )
            )
            + amount
        )

        return amount

    def add_smoke(self, cx, cy, amount):
        amount = max(
            0.0,
            float(amount),
        )

        if amount <= 0.0:
            return 0.0

        key = (
            int(cx),
            int(cy),
        )

        self.smoke[key] = (
            float(
                self.smoke.get(
                    key,
                    0.0,
                )
            )
            + amount
        )

        return amount

    def add_updraft(
        self,
        cx,
        cy,
        strength,
    ):
        strength = max(
            0.0,
            float(
                strength
            ),
        )

        if strength <= 0.0:
            return 0.0

        key = (
            int(cx),
            int(cy),
        )

        self.updraft[
            key
        ] = (
            float(
                self.updraft.get(
                    key,
                    0.0,
                )
            )
            + strength
        )

        return strength

    def updraft_amount(
        self,
        cx,
        cy,
    ):
        return float(
            self.updraft.get(
                (
                    int(cx),
                    int(cy),
                ),
                0.0,
            )
        )

    def pulse_fireball_lift(
        self,
        cx,
        cy,
        strength,
    ):
        key = (
            int(cx),
            int(cy),
        )

        strength = max(
            0.0,
            float(
                strength
            ),
        )

        if strength <= 0.0:
            return 0.0

        self.fireball_lift[
            key
        ] = max(
            strength,
            float(
                self.fireball_lift.get(
                    key,
                    0.0,
                )
            ),
        )

        return self.fireball_lift[
            key
        ]

    def fireball_lift_amount(
        self,
        cx,
        cy,
    ):
        return float(
            self.fireball_lift.get(
                (
                    int(cx),
                    int(cy),
                ),
                0.0,
            )
        )

    def organic_amount(self, cx, cy):
        return float(self.organic.get((int(cx), int(cy)), 0.0))

    def add_organic(self, cx, cy, amount):
        amount = max(0.0, float(amount))
        if amount <= 0.0:
            return 0.0
        key = (int(cx), int(cy))
        self.organic[key] = self.organic_amount(*key) + amount
        return amount

    def take_organic(self, cx, cy, amount):
        amount = max(0.0, float(amount))
        key = (int(cx), int(cy))
        available = self.organic_amount(*key)
        taken = min(available, amount)
        remaining = available - taken
        if remaining <= 1e-12:
            self.organic.pop(key, None)
        else:
            self.organic[key] = remaining
        return taken

    # --------------------------------------------------------
    # Fire
    # --------------------------------------------------------

    def ignite(
        self,
        tx,
        ty,
        fuel=1.0,
        intensity=1.0,
        penetration_left=0,
        source="environment",
        temperature_c=620.0,
        stack_add=0,
        stack_level=None,
    ):
        key = (
            int(tx),
            int(ty),
        )

        existing = self.fire.get(
            key
        )

        if stack_level is None:
            if existing is None:
                resolved_stack = 1
            else:
                resolved_stack = max(
                    1,
                    int(
                        getattr(
                            existing,
                            "stack_level",
                            1,
                        )
                    ),
                )

                if stack_add > 0:
                    resolved_stack = min(
                        3,
                        resolved_stack
                        + int(
                            stack_add
                        ),
                    )
        else:
            resolved_stack = max(
                1,
                min(
                    3,
                    int(
                        stack_level
                    ),
                ),
            )

        cell = FireCell(
            intensity=max(
                0.05,
                min(
                    1.0,
                    float(intensity),
                ),
            ),
            fuel=max(
                0.05,
                float(fuel),
            ),
            temperature=max(
                0.1,
                min(
                    1.5,
                    float(intensity),
                ),
            ),
            temperature_c=float(
                temperature_c
            ),
            penetration_left=max(
                0,
                int(
                    penetration_left
                ),
            ),
            source=str(
                source
            ),
            stack_level=resolved_stack,
        )

        if existing is not None:
            cell.intensity = max(
                cell.intensity,
                existing.intensity,
            )
            cell.fuel = max(
                cell.fuel,
                existing.fuel,
            )
            cell.temperature = max(
                cell.temperature,
                existing.temperature,
            )
            cell.temperature_c = max(
                cell.temperature_c,
                getattr(
                    existing,
                    "temperature_c",
                    cell.temperature_c,
                ),
            )
            cell.penetration_left = max(
                cell.penetration_left,
                getattr(
                    existing,
                    "penetration_left",
                    0,
                ),
            )
            cell.stack_level = max(
                cell.stack_level,
                int(
                    getattr(
                        existing,
                        "stack_level",
                        1,
                    )
                ),
            )

        self.fire[key] = cell
        self._notify_reactive_change(key[0], key[1], "fire")

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    def export_state(self):
        return {
            "water": [
                [tx, ty, a]
                for (
                    tx,
                    ty
                ), a
                in sorted(
                    self.water.items()
                )
            ],
            "lava": [
                [tx, ty, a, float(self.lava_cooling.get((tx,ty),0.0))]
                for (tx,ty), a in sorted(self.lava.items())
            ],
            "honey": [
                [tx, ty, amount] for (tx, ty), amount in sorted(self.honey.items())
            ],
            "lava_bottom_sink_mass": float(self.lava_bottom_sink_mass),

            "fire": [
                [
                    tx,
                    ty,
                    c.intensity,
                    c.fuel,
                    c.temperature,
                    getattr(
                        c,
                        "temperature_c",
                        620.0,
                    ),
                    getattr(
                        c,
                        "penetration_left",
                        0,
                    ),
                    getattr(
                        c,
                        "source",
                        "environment",
                    ),
                    int(
                        getattr(
                            c,
                            "stack_level",
                            1,
                        )
                    ),
                ]
                for (
                    tx,
                    ty
                ), c
                in sorted(
                    self.fire.items()
                )
            ],

            "soil": [
                [
                    tx,
                    ty,
                    c.moisture,
                    c.fertility,
                    int(getattr(c, "plant_cap", 3)),
                ]
                for (
                    tx,
                    ty
                ), c
                in sorted(
                    self.soil.items()
                )
            ],

            "soil_layers": [
                [tx, ty, int(mask)]
                for (tx, ty), mask in sorted(self.soil_layers.items())
                if int(mask) != 7
            ],

            "plants": [
                [
                    tx,
                    ty,
                    c.species,
                    c.growth,
                    c.stage,
                    c.fruit,
                    c.alive,
                    c.biomass,
                    getattr(c, "age_game_seconds", 0.0),
                    getattr(c, "wet_game_seconds", 0.0),
                    getattr(c, "fruit_clock_game_seconds", 0.0),
                    int(getattr(c, "max_stage", 3)),
                    getattr(c, "state", "normal"),
                    float(getattr(c, "recovery_game_seconds", 0.0)),
                    float(getattr(c, "regrow_total_game_seconds", 0.0)),
                    float(getattr(c, "regrow_progress", 0.0)),
                    float(getattr(c, "burn_progress", 0.0)),
                    int(getattr(c, "burn_stack", 0)),
                ]
                for (
                    tx,
                    ty
                ), c
                in sorted(
                    self.plants.items()
                )
            ],

            "steam": [[tx, ty, v] for (tx, ty), v in sorted(self.steam.items())],
            "poison_gas": [[tx, ty, v] for (tx, ty), v in sorted(self.poison_gas.items())],
            "poison_gas_ttl": [[tx, ty, v] for (tx, ty), v in sorted(self.poison_gas_ttl.items())],
            "acid": [[tx, ty, v] for (tx, ty), v in sorted(self.acid.items())],
            "poison_liquid": [[tx, ty, v] for (tx, ty), v in sorted(self.poison_liquid.items())],
            "electric_shock": [[tx, ty, v] for (tx, ty), v in sorted(self.electric_shock.items())],
            "reaction_wind": [[tx, ty, v] for (tx, ty), v in sorted(self.reaction_wind.items())],
            "magnetic_sources": [
                [tx, ty, float(v[0]), int(v[1])]
                for (tx, ty), v in sorted(self.magnetic_sources.items())
            ],

            "wind": [
                [cx, cy, float(v[0]), float(v[1])]
                for (cx, cy), v in sorted(self.wind.items())
            ],
            "updraft": [
                [cx, cy, float(value)]
                for (cx, cy), value in sorted(self.updraft.items())
            ],
            "fireball_lift": [
                [cx, cy, float(value)]
                for (cx, cy), value in sorted(self.fireball_lift.items())
            ],
            "electric_charge": [
                [tx, ty, float(value)]
                for (tx, ty), value in sorted(self.electric_charge.items())
            ],

            "temperature": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.temperature.items())
            ],
            "water_temperature": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.water_temperature.items())
            ],
            "climate": [
                [cx, cy, profile]
                for (cx, cy), profile in sorted(self.climate.items())
            ],
            "ice_mass": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.ice_mass.items())
            ],
            "cold_surface_water": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.cold_surface_water.items())
            ],
            "snow_cover": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.snow_cover.items())
            ],
            "ice_melt_progress": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.ice_melt_progress.items())
            ],
            "water_freeze_progress": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.water_freeze_progress.items())
            ],

            "vapor": [
                [
                    cx,
                    cy,
                    amount,
                ]
                for (
                    cx,
                    cy
                ), amount
                in sorted(
                    self.vapor.items()
                )
            ],

            "cloud_water": [
                [cx, 0, amount]
                for (cx, _cy), amount in sorted(self.cloud_water.items())
            ],
            "cloud_desert_fresh": [
                [cx, 0, amount]
                for (cx, _cy), amount in sorted(self.cloud_desert_fresh.items())
            ],

            "groundwater": [
                [
                    cx,
                    cy,
                    amount,
                ]
                for (
                    cx,
                    cy
                ), amount
                in sorted(
                    self.groundwater.items()
                )
            ],

            "ash": [
                [
                    tx,
                    ty,
                    amount,
                ]
                for (
                    tx,
                    ty
                ), amount
                in sorted(
                    self.ash.items()
                )
            ],
            "ash_wet_time": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.ash_wet_time.items())
            ],
            "swamp_wet_time": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.swamp_wet_time.items())
            ],
            "swamp_dry_time": [
                [tx, ty, value]
                for (tx, ty), value in sorted(self.swamp_dry_time.items())
            ],

            "smoke": [
                [cx, cy, amount]
                for (cx, cy), amount in sorted(self.smoke.items())
            ],
            "organic": [
                [cx, cy, amount]
                for (cx, cy), amount in sorted(self.organic.items())
            ],
        }

    def import_state(self, data):
        self.water.clear()
        self.lava.clear()
        self.honey.clear()
        self.lava_cooling.clear()
        self.lava_bottom_sink_mass=0.0
        self.fire.clear()
        self.soil.clear()
        self.soil_layers.clear()
        self.plants.clear()
        self.wind.clear()
        self.electric_charge.clear()
        self.steam.clear()
        self.poison_gas.clear()
        self.poison_gas_ttl.clear()
        self.acid.clear()
        self.poison_liquid.clear()
        self.electric_shock.clear()
        self.reaction_wind.clear()
        self.magnetic_sources.clear()
        self.vapor.clear()
        self.cloud_water.clear()
        self.cloud_desert_fresh.clear()
        self.evaporation_haze.clear()
        self.groundwater.clear()
        self.cold_surface_water.clear()
        self.snow_cover.clear()
        self.ash.clear()
        self.ash_wet_time.clear()
        self.smoke.clear()
        self.organic.clear()

        for item in data.get(
            "water",
            [],
        ):
            if len(item) >= 3:
                self.set_water(
                    item[0],
                    item[1],
                    item[2],
                )

        for item in data.get("lava", []):
            if len(item) >= 3:
                self.set_lava(item[0],item[1],item[2])
                if len(item) >= 4:
                    self.set_lava_cooling(item[0],item[1],item[3])
        for item in data.get("honey", []):
            if len(item) >= 3:
                self.set_honey(item[0], item[1], item[2])
        try:
            self.lava_bottom_sink_mass=max(0.0,float(data.get("lava_bottom_sink_mass",0.0)))
        except Exception:
            self.lava_bottom_sink_mass=0.0

        for item in data.get(
            "fire",
            [],
        ):
            if len(item) >= 5:
                if len(item) >= 8:
                    temperature_c = float(
                        item[5]
                    )
                    penetration = int(
                        item[6]
                    )
                    source = str(
                        item[7]
                    )
                    stack_level = (
                        int(
                            item[8]
                        )
                        if len(
                            item
                        ) >= 9
                        else 1
                    )
                else:
                    temperature_c = 620.0
                    penetration = (
                        int(item[5])
                        if len(item) >= 6
                        else 0
                    )
                    source = (
                        str(item[6])
                        if len(item) >= 7
                        else "environment"
                    )
                    stack_level = 1

                self.fire[
                    (
                        int(item[0]),
                        int(item[1]),
                    )
                ] = FireCell(
                    intensity=float(
                        item[2]
                    ),
                    fuel=float(
                        item[3]
                    ),
                    temperature=float(
                        item[4]
                    ),
                    temperature_c=temperature_c,
                    penetration_left=penetration,
                    source=source,
                    stack_level=max(
                        1,
                        min(
                            3,
                            stack_level,
                        ),
                    ),
                )

        for item in data.get(
            "soil",
            [],
        ):
            if len(item) >= 4:
                self.soil[
                    (
                        int(item[0]),
                        int(item[1]),
                    )
                ] = SoilCell(
                    float(
                        item[2]
                    ),
                    float(
                        item[3]
                    ),
                    int(item[4]) if len(item) >= 5 else 3,
                )

        for item in data.get("soil_layers", []):
            if len(item) >= 3:
                mask = int(item[2]) & 7
                if mask != 7:
                    self.soil_layers[(int(item[0]), int(item[1]))] = mask

        for item in data.get(
            "plants",
            [],
        ):
            if len(item) >= 7:
                self.plants[
                    (
                        int(item[0]),
                        int(item[1]),
                    )
                ] = PlantCell(
                    str(
                        item[2]
                    ),
                    float(
                        item[3]
                    ),
                    int(
                        item[4]
                    ),
                    int(
                        item[5]
                    ),
                    bool(
                        item[6]
                    ),
                    float(item[7]) if len(item) >= 8 else 0.035,
                    float(item[8]) if len(item) >= 9 else 0.0,
                    float(item[9]) if len(item) >= 10 else 0.0,
                    float(item[10]) if len(item) >= 11 else 0.0,
                    int(item[11]) if len(item) >= 12 else 3,
                    str(item[12]) if len(item) >= 13 else "normal",
                    float(item[13]) if len(item) >= 14 else 0.0,
                    float(item[14]) if len(item) >= 15 else 0.0,
                    float(item[15]) if len(item) >= 16 else 0.0,
                    float(item[16]) if len(item) >= 17 else 0.0,
                    int(item[17]) if len(item) >= 18 else 0,
                )


        for field in ("steam", "poison_gas", "acid", "poison_liquid", "electric_shock", "reaction_wind"):
            table = getattr(self, field)
            for item in data.get(field, []):
                if len(item) >= 3:
                    table[(int(item[0]), int(item[1]))] = float(item[2])

        for item in data.get("poison_gas_ttl", []):
            if len(item) >= 3:
                self.poison_gas_ttl[(int(item[0]), int(item[1]))] = max(0.0, float(item[2]))

        # Legacy saves had poison gas amounts but no explicit lifetime. Give
        # those cells a fresh default lifetime rather than deleting them.
        if self.poison_gas:
            try:
                from config import POISON_GAS_LIFETIME_SECONDS
                default_ttl = float(POISON_GAS_LIFETIME_SECONDS)
            except Exception:
                default_ttl = 8.0
            for key in self.poison_gas:
                self.poison_gas_ttl.setdefault(key, default_ttl)

        for item in data.get("magnetic_sources", []):
            if len(item) >= 4:
                self.magnetic_sources[(int(item[0]), int(item[1]))] = (float(item[2]), int(item[3]))

        for item in data.get("wind", []):
            if len(item) >= 4:
                self.wind[(int(item[0]), int(item[1]))] = (
                    float(item[2]),
                    float(item[3]),
                )

        for item in data.get("updraft", []):
            if len(item) >= 3:
                self.updraft[
                    (
                        int(item[0]),
                        int(item[1]),
                    )
                ] = float(
                    item[2]
                )

        for item in data.get("fireball_lift", []):
            if len(item) >= 3:
                self.fireball_lift[
                    (
                        int(item[0]),
                        int(item[1]),
                    )
                ] = float(
                    item[2]
                )

        for item in data.get("electric_charge", []):
            if len(item) >= 3:
                self.electric_charge[(int(item[0]), int(item[1]))] = float(item[2])

        for name, target, cast_value in (
            ("temperature", self.temperature, float),
            ("water_temperature", self.water_temperature, float),
            ("climate", self.climate, str),
            ("ice_mass", self.ice_mass, float),
            ("cold_surface_water", self.cold_surface_water, float),
            ("snow_cover", self.snow_cover, float),
            ("ice_melt_progress", self.ice_melt_progress, float),
            ("water_freeze_progress", self.water_freeze_progress, float),
        ):
            for item in data.get(
                name,
                [],
            ):
                if len(item) >= 3:
                    target[
                        (
                            int(item[0]),
                            int(item[1]),
                        )
                    ] = cast_value(
                        item[2]
                    )

        for name, target in (
            ("vapor", self.vapor),
            ("cloud_water", self.cloud_water),
            ("cloud_desert_fresh", self.cloud_desert_fresh),
            (
                "groundwater",
                self.groundwater,
            ),
            ("ash", self.ash),
            ("swamp_wet_time", self.swamp_wet_time),
            ("swamp_dry_time", self.swamp_dry_time),
            ("smoke", self.smoke),
            ("organic", self.organic),
        ):
            for item in data.get(
                name,
                [],
            ):
                if len(item) >= 3:
                    target[
                        (
                            int(item[0]),
                            int(item[1]),
                        )
                    ] = float(
                        item[2]
                    )

        # Fresh tags may come from an older/newer save with rounding noise.
        for (cx, _cy), value in tuple(self.cloud_desert_fresh.items()):
            self.set_cloud_desert_fresh(cx, value)
