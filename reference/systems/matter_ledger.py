# -*- coding: utf-8 -*-
from config import MATTER_AUDIT_TOLERANCE
from world.tile_registry import (
    tile_def,
    ICE,
)


class MatterLedger:
    """Runtime world-inventory diagnostic.

    V0.4.15 IMPORTANT:
    The entire game world is an OPEN system, so this ledger is no longer a
    rule saying "global delta must always be zero".

    Examples of legitimate external/boundary sources:
        mana -> spell-created water/ice
        sunlight -> heat
        climate boundary exchange
        future world-generation / rain sources

    Individual TRANSFERS still conserve the moved amount:
        water -> soil -> vapor -> ice -> water
        terrain -> ash/smoke
        etc.

    Magic-created matter/energy is audited separately by MagicLedger.
    """

    def __init__(self, world, env):
        self.world = world
        self.env = env

        self.baseline_water = None
        self.baseline_material = None

    @staticmethod
    def plant_mass(plant):
        return max(0.0, float(getattr(plant, "biomass", 0.035)))

    def water_components(self, game=None):
        free = sum(
            float(v)
            for v in self.env.water.values()
        )

        soil = sum(
            float(
                cell.moisture
            )
            for cell in self.env.soil.values()
        )

        vapor = sum(
            float(v)
            for v in self.env.vapor.values()
        )

        cloud = sum(
            float(v)
            for v in getattr(self.env, "cloud_water", {}).values()
        )

        steam = sum(
            float(v)
            for v in getattr(self.env, "steam", {}).values()
        )

        groundwater = sum(
            float(v)
            for v in self.env.groundwater.values()
        )

        cold_surface = sum(
            float(v)
            for v in getattr(self.env, "cold_surface_water", {}).values()
        )

        # Solid water phase.
        ice = 0.0

        for tx, ty, tile_id in self.world.iter_non_air_tiles():
            if tile_id != ICE:
                continue
            ice += float(self.env.ice_mass.get((tx, ty), tile_def(ICE).water_mass))

        projectile = 0.0

        if (
            game is not None
            and hasattr(
                game,
                "magic",
            )
        ):
            projectile = sum(
                float(
                    getattr(
                        wb,
                        "water_mass",
                        0.0,
                    )
                )
                for wb in game.magic.waterballs
                if getattr(
                    wb,
                    "active",
                    False,
                )
            )

            projectile += sum(
                float(
                    getattr(
                        ib,
                        "water_mass",
                        0.0,
                    )
                )
                for ib in getattr(
                    game.magic,
                    "iceballs",
                    (),
                )
                if getattr(
                    ib,
                    "active",
                    False,
                )
            )

        total = (
            free
            + soil
            + vapor
            + cloud
            + steam
            + groundwater
            + cold_surface
            + ice
            + projectile
        )

        return {
            "free": free,
            "soil": soil,
            "vapor": vapor,
            "cloud": cloud,
            "steam": steam,
            "groundwater": groundwater,
            "cold_surface": cold_surface,
            "ice": ice,
            "projectile": projectile,
            "total": total,
        }

    def material_components(self, game=None):
        terrain = 0.0
        for _tx, _ty, tile_id in self.world.iter_non_air_tiles():
            terrain += float(tile_def(tile_id).material_mass)

        plants = sum(
            self.plant_mass(
                plant
            )
            for plant
            in self.env.plants.values()
        )

        ash = sum(
            float(v)
            for v in self.env.ash.values()
        )

        smoke = sum(
            float(v)
            for v in self.env.smoke.values()
        )

        organic = sum(
            float(v)
            for v in self.env.organic.values()
        )

        rigid = 0.0

        if (
            game is not None
            and hasattr(
                game,
                "physics_objects",
            )
        ):
            rigid = sum(
                float(
                    getattr(
                        obj,
                        "mass",
                        0.0,
                    )
                )
                for obj in game.physics_objects
                if getattr(
                    obj,
                    "active",
                    True,
                )
            )

        total = (
            terrain
            + plants
            + ash
            + smoke
            + organic
            + rigid
        )

        return {
            "terrain": terrain,
            "plants": plants,
            "ash": ash,
            "smoke": smoke,
            "organic": organic,
            "rigid": rigid,
            "total": total,
        }

    def capture_baseline(self, game=None):
        self.baseline_water = (
            self.water_components(
                game
            )["total"]
        )

        self.baseline_material = (
            self.material_components(
                game
            )["total"]
        )

    def audit(self, game=None):
        water = self.water_components(
            game
        )

        material = self.material_components(
            game
        )

        water_delta = (
            0.0
            if self.baseline_water is None
            else water["total"]
            - self.baseline_water
        )

        material_delta = (
            0.0
            if self.baseline_material is None
            else material["total"]
            - self.baseline_material
        )

        return {
            "water": water,
            "material": material,
            "water_delta": water_delta,
            "material_delta": material_delta,
            "water_ok": (
                abs(
                    water_delta
                )
                <= MATTER_AUDIT_TOLERANCE
            ),
            "material_ok": (
                abs(
                    material_delta
                )
                <= MATTER_AUDIT_TOLERANCE
            ),
        }
