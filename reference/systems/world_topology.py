# -*- coding: utf-8 -*-
"""Data-driven, validated portal topology for authored worlds.

Map files continue to own geometry (``rect`` and ``arrival``).  This catalog
owns graph connectivity, so changing a destination never requires editing the
60 Hz game loop.  A malformed/missing catalog is non-fatal: authored map rows
remain usable, while every resolved target is still restricted to the project
``maps`` directory by :class:`GameApp`.
"""
import json
import os


class WorldTopology:
    CATALOG_RELATIVE_PATH = os.path.join("assets", "world_topology.json")

    def __init__(self, project_root="", payload=None):
        self.project_root = os.path.realpath(os.path.abspath(project_root or os.getcwd()))
        self.catalog_path = os.path.join(self.project_root, self.CATALOG_RELATIVE_PATH)
        self.payload = dict(payload or {})
        self.worlds = {}
        self._endpoint_routes = {}
        self.errors = []
        self._index()

    @classmethod
    def from_project_root(cls, project_root):
        root = os.path.realpath(os.path.abspath(project_root or os.getcwd()))
        path = os.path.join(root, cls.CATALOG_RELATIVE_PATH)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("topology root must be an object")
        except Exception as exc:
            topology = cls(root, {})
            topology.errors.append("catalog unavailable: " + repr(exc))
            return topology
        return cls(root, payload)

    @staticmethod
    def _map_name(value):
        """Return one safe catalog filename, never a directory traversal."""
        raw = str(value or "").strip().replace("\\", "/")
        name = raw.rsplit("/", 1)[-1]
        if not name or name in (".", "..") or not name.lower().endswith(".json"):
            return ""
        return name

    @classmethod
    def _endpoint(cls, row):
        if not isinstance(row, dict):
            return None
        map_name = cls._map_name(row.get("map"))
        portal_id = str(row.get("portal", "") or "").strip()
        if not map_name or not portal_id:
            return None
        return map_name, portal_id

    def _index(self):
        worlds = self.payload.get("worlds", ())
        if isinstance(worlds, dict):
            worlds = [dict(row, id=key) if isinstance(row, dict) else {"id": key}
                      for key, row in worlds.items()]
        if not isinstance(worlds, list):
            worlds = []
            self.errors.append("worlds must be a list or object")
        for row in worlds:
            if not isinstance(row, dict):
                continue
            world_id = str(row.get("id", "") or "").strip()
            entry_map = self._map_name(row.get("entry_map"))
            if not world_id or not entry_map:
                self.errors.append("world row missing id/entry_map")
                continue
            item = dict(row)
            item["id"] = world_id
            item["entry_map"] = entry_map
            item["display_name"] = str(row.get("display_name", world_id) or world_id)
            self.worlds[world_id] = item

        routes = self.payload.get("routes", ())
        if not isinstance(routes, list):
            routes = []
            self.errors.append("routes must be a list")
        seen_ids = set()
        for row in routes:
            if not isinstance(row, dict) or row.get("enabled", True) is False:
                continue
            route_id = str(row.get("id", "") or "").strip()
            a = self._endpoint(row.get("a"))
            b = self._endpoint(row.get("b"))
            if not route_id or not a or not b or a == b:
                self.errors.append("invalid route: " + str(route_id or "<unnamed>"))
                continue
            if route_id in seen_ids:
                self.errors.append("duplicate route id: " + route_id)
                continue
            seen_ids.add(route_id)
            for source, destination in ((a, b), (b, a)):
                if source in self._endpoint_routes:
                    self.errors.append("duplicate endpoint: %s:%s" % source)
                    continue
                self._endpoint_routes[source] = {
                    "route_id": route_id,
                    "target_map": destination[0],
                    "target_portal": destination[1],
                }

    def resolve(self, current_map, portal_row):
        """Return a copied portal row with catalog connectivity applied."""
        if not isinstance(portal_row, dict):
            return None
        out = dict(portal_row)
        source = (self._map_name(current_map), str(out.get("id", "") or "").strip())
        route = self._endpoint_routes.get(source)
        if route:
            out.update(route)
        else:
            # Legacy/custom maps remain compatible without a catalog route,
            # but normalize away directories here. GameApp repeats the realpath
            # containment check before touching the filesystem.
            out["target_map"] = self._map_name(out.get("target_map"))
            out["target_portal"] = str(out.get("target_portal", "") or "").strip()
        return out

    def destination_name(self, target_map):
        name = self._map_name(target_map)
        for world in self.worlds.values():
            if world.get("entry_map") == name:
                return str(world.get("display_name") or name)
        return name[:-5] if name.lower().endswith(".json") else name

    def validate_files(self):
        """Return non-mutating graph/file diagnostics for editors and tests."""
        errors = list(self.errors)
        maps_root = os.path.realpath(os.path.join(self.project_root, "maps"))
        portals_by_map = {}
        for filename in sorted({key[0] for key in self._endpoint_routes}):
            path = os.path.join(maps_root, filename)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                rows = dict(payload.get("metadata", {}) or {}).get("portals", ())
                portals_by_map[filename] = {
                    str(row.get("id", "") or ""): row
                    for row in rows if isinstance(row, dict)
                }
            except Exception as exc:
                errors.append("map unavailable %s: %r" % (filename, exc))
                portals_by_map[filename] = {}
        for endpoint in sorted(self._endpoint_routes):
            authored = portals_by_map.get(endpoint[0], {}).get(endpoint[1])
            if authored is None:
                errors.append("missing endpoint %s:%s" % endpoint)
                continue
            route = self._endpoint_routes[endpoint]
            authored_target = self._map_name(authored.get("target_map"))
            authored_portal = str(authored.get("target_portal", "") or "").strip()
            if (authored_target, authored_portal) != (route["target_map"], route["target_portal"]):
                errors.append(
                    "route mismatch %s:%s -> %s:%s (catalog %s:%s)" % (
                        endpoint[0], endpoint[1], authored_target, authored_portal,
                        route["target_map"], route["target_portal"],
                    )
                )
        return errors

    def audit_bidirectional_files(self):
        """Audit every authored portal as a directed graph edge.

        ``validate_files`` historically checked only whether catalog endpoints
        matched their source rows.  That allowed a valid-looking A->B edge
        whose B endpoint did not actually resolve back to A.  This pass reads
        every map in ``maps`` and proves both endpoint existence and the exact
        reverse edge after topology overrides are applied.
        """
        maps_root = os.path.realpath(os.path.join(self.project_root, "maps"))
        maps = {}
        errors = list(self.errors)
        try:
            filenames = sorted(
                name for name in os.listdir(maps_root)
                if self._map_name(name) == name and name.lower().endswith(".json")
            )
        except Exception as exc:
            filenames = []
            errors.append("maps directory unavailable: %r" % (exc,))

        for filename in filenames:
            path = os.path.join(maps_root, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                raw = dict(payload.get("metadata", {}) or {}).get("portals", ())
                if not isinstance(raw, list):
                    raise ValueError("metadata.portals must be a list")
                rows = {}
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    portal_id = str(row.get("id", "") or "").strip()
                    if not portal_id:
                        errors.append("unnamed portal in %s" % filename)
                        continue
                    if portal_id in rows:
                        errors.append("duplicate portal %s:%s" % (filename, portal_id))
                        continue
                    rows[portal_id] = row
                maps[filename] = rows
            except Exception as exc:
                errors.append("map unavailable %s: %r" % (filename, exc))

        links = []
        seen_edges = set()
        for source_map, rows in sorted(maps.items()):
            for source_portal, authored in sorted(rows.items()):
                resolved = self.resolve(source_map, authored) or {}
                target_map = self._map_name(resolved.get("target_map"))
                target_portal = str(resolved.get("target_portal", "") or "").strip()
                route_id = str(resolved.get("route_id", "") or "")
                edge = (source_map, source_portal, target_map, target_portal)
                if edge in seen_edges:
                    errors.append("duplicate resolved edge %s:%s" % (source_map, source_portal))
                    continue
                seen_edges.add(edge)
                link = {
                    "from_map": source_map,
                    "from_portal": source_portal,
                    "to_map": target_map,
                    "to_portal": target_portal,
                    "route_id": route_id,
                    "reciprocal": False,
                }
                links.append(link)
                if not target_map or not target_portal:
                    errors.append("missing target %s:%s" % (source_map, source_portal))
                    continue
                target_row = maps.get(target_map, {}).get(target_portal)
                if target_row is None:
                    errors.append(
                        "missing endpoint %s:%s -> %s:%s" % edge
                    )
                    continue
                reverse = self.resolve(target_map, target_row) or {}
                reverse_map = self._map_name(reverse.get("target_map"))
                reverse_portal = str(reverse.get("target_portal", "") or "").strip()
                if (reverse_map, reverse_portal) != (source_map, source_portal):
                    errors.append(
                        "one-way edge %s:%s -> %s:%s; reverse -> %s:%s" % (
                            source_map, source_portal, target_map, target_portal,
                            reverse_map or "<missing>", reverse_portal or "<missing>",
                        )
                    )
                    continue
                link["reciprocal"] = True

        # Catalog endpoints must also exist even when a map was omitted from a
        # malformed authored graph.
        for endpoint in sorted(self._endpoint_routes):
            if endpoint[1] not in maps.get(endpoint[0], {}):
                message = "catalog endpoint missing %s:%s" % endpoint
                if message not in errors:
                    errors.append(message)
        return {
            "ok": not errors,
            "map_count": len(maps),
            "portal_count": len(links),
            "pair_count": sum(1 for row in links if row["reciprocal"]) // 2,
            "links": links,
            "errors": errors,
        }

    def validate_transition(self, current_map, portal_row):
        """Validate one resolved edge against the last complete graph audit."""
        source_map = self._map_name(current_map)
        source_portal = str((portal_row or {}).get("id", "") or "").strip()
        resolved = self.resolve(source_map, portal_row) or {}
        target_map = self._map_name(resolved.get("target_map"))
        target_portal = str(resolved.get("target_portal", "") or "").strip()
        report = self.audit_bidirectional_files()
        match = next((
            row for row in report.get("links", ())
            if row.get("from_map") == source_map
            and row.get("from_portal") == source_portal
            and row.get("to_map") == target_map
            and row.get("to_portal") == target_portal
        ), None)
        if match is None:
            return False, "portal edge is not present in graph audit"
        if not bool(match.get("reciprocal", False)):
            return False, "portal edge has no exact reverse link"
        return True, ""
