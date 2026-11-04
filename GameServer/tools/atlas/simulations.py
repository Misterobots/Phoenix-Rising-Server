"""Versioned simulation templates and explicit Phoenix bundle staging/apply.

This module intentionally treats Phoenix as an input/output checkout. Atlas is not
imported by the running server; apply copies validated server-owned JSON assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


FORMAT = "phoenix-simulation-bundle"
VERSION = 1


def _json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(value: str) -> str:
    p = PurePosixPath(value.replace("\\", "/"))
    wp = PureWindowsPath(value)
    if p.is_absolute() or wp.is_absolute() or wp.drive or not p.parts or any(x in ("", ".", "..") for x in p.parts):
        raise ValueError(f"unsafe bundle path: {value!r}")
    return p.as_posix()


def validate_template(template: dict) -> list[str]:
    errors = []
    if template.get("format") != "phoenix-simulation-template":
        errors.append("format must be phoenix-simulation-template")
    if template.get("version") != 1:
        errors.append("unsupported template version")
    for key in ("id", "huntId", "mapId"):
        if not isinstance(template.get(key), str) or not template[key].strip():
            errors.append(f"{key} is required")
    kind = template.get("kind", "hunt")
    if kind not in ("hunt", "map_preview"):
        errors.append("kind must be hunt or map_preview")
    encounter = template.get("encounter")
    if kind == "map_preview":
        if encounter is not None:
            errors.append("map_preview templates must not contain a behemoth encounter")
    else:
        if not isinstance(encounter, dict):
            errors.append("encounter object is required for hunt templates")
            encounter = {}
        for key in ("speciesId", "variantId", "behaviorTreeId"):
            if not isinstance(encounter.get(key), str) or not encounter[key].strip():
                errors.append(f"encounter.{key} is required")
        if not isinstance(encounter.get("placement"), dict):
            errors.append("encounter.placement must describe a placement source")
        elif not encounter["placement"].get("sourceId"):
            errors.append("encounter.placement.sourceId is required")
    if not isinstance(template.get("loadout"), dict) or not template["loadout"].get("id"):
        errors.append("loadout.id is required (use an explicit default loadout ID)")
    if not isinstance(template.get("modifiers"), list):
        errors.append("modifiers must be a list of supported modifier IDs")
    elif not all(isinstance(x, str) for x in template["modifiers"]):
        errors.append("modifiers must contain string IDs")
    elif not isinstance(template.get("supportedModifiers", []), list) or not all(
            isinstance(x, str) for x in template.get("supportedModifiers", [])):
        errors.append("supportedModifiers must contain string IDs")
    elif not set(template["modifiers"]).issubset(set(template.get("supportedModifiers", []))):
        errors.append("modifiers include an ID absent from supportedModifiers")
    for i, dep in enumerate(template.get("dependencies", [])):
        if not isinstance(dep, dict) or not dep.get("id") or not dep.get("target"):
            errors.append(f"dependencies[{i}] requires id and target")
        else:
            try:
                _safe_rel(dep["target"])
            except ValueError as e:
                errors.append(str(e))
    return errors


def template_from_contract(contract_path: Path, out: Path, species: str, variant: str,
                           tree: str, loadout: str = "phoenix.default", phoenix_root: Path | None = None) -> dict:
    contract_path = contract_path.resolve()
    contract = _json(contract_path)
    encounter = contract.get("encounter") or {}
    centers = encounter.get("centers", [])
    map_preview = not encounter
    if not contract.get("huntId") or not contract.get("map"):
        raise ValueError("contract lacks huntId/map identity")
    if not map_preview:
        if not all((species, variant, tree)):
            raise ValueError("species, variant and tree IDs are required for encounter templates")
        authored_variant = encounter.get("behemoth")
        choices = (encounter.get("testSelection") or {}).get("behemothChoices", [])
        authored_ids = {str(x.get("row", "")).lower() for x in choices}
        if authored_variant:
            authored_ids.add(str(authored_variant).lower())
        if authored_ids and variant.lower() not in authored_ids:
            raise ValueError(f"selected variant {variant} is not in the authored encounter pool for {contract['huntId']}")
    else:
        species = variant = tree = None
    unresolved = []
    evidence = {"dataPresent": [], "behaviorUnderstood": [], "consumerImplemented": [],
                "offlineChecksPassed": [], "clientAccepted": []}
    acquisition_provenance = {}
    if phoenix_root and not map_preview:
        report_path = phoenix_root.resolve() / "GameServer" / "world" / "behemoths" / f"{variant}_acquisition.json"
        if report_path.is_file():
            report = _json(report_path)
            acquisition_provenance = {"reportSha256": _sha(report_path), "sourceHashes": report.get("sourceHashes", {}),
                                      "generatorVersions": report.get("generatorVersions", {})}
            got = report.get("acquired", {})
            if got.get("actions") and not report.get("gaps", {}).get("actionsMissingAttackOrMontage"):
                evidence["dataPresent"].extend(["action-set", "attack defaults", "montage defaults"])
            if got.get("damageRows"):
                evidence["dataPresent"].append("damage rows")
            if got.get("schemaClasses"):
                evidence["dataPresent"].append("replication schema")
            if got.get("trees") and not report.get("gaps", {}).get("treePackagesMissing"):
                evidence["dataPresent"].append("reachable behavior trees")
            if got.get("bodyBones") and not report.get("gaps", {}).get("bodyResources"):
                evidence["dataPresent"].append("body and joint resources")
            if not report.get("gaps", {}).get("rootMotion"):
                evidence["dataPresent"].append("root-motion curves")
            for key in ("actionsMissingAttackOrMontage", "treePackagesMissing", "bodyResources", "rootMotion"):
                for gap in report.get("gaps", {}).get(key, []):
                    unresolved.append(f"{key}: {gap}")
            terrain = report.get("terrainValidation") or {}
            if terrain.get("valid"):
                evidence["offlineChecksPassed"].append("terrain centers/components/channel validation")
            else:
                unresolved.extend(terrain.get("issues", ["terrain validation was not run"]))
            trace = report.get("behaviorTrace") or {}
            if trace.get("attacks", 0) > 0 and not trace.get("unhandled"):
                evidence["offlineChecksPassed"].append(
                    f"reachable behavior trace emitted {trace['attacks']} attacks")
            elif trace.get("attacks", 0) <= 0:
                unresolved.append("offline behavior trace did not establish a handled attack path")
            if trace.get("unhandled"):
                unresolved.append("unhandled behavior nodes: " + ", ".join(x.get("type", "?") for x in trace["unhandled"]))
            # Atlas source mappings add understanding/consumer evidence without
            # converting an unimplemented reached node into a supported one.
            evidence_dir = Path(__file__).resolve().parents[2] / "data" / "atlas" / "simulations" / "evidence"
            if evidence_dir.is_dir():
                for mapping_path in sorted(evidence_dir.glob("*.json")):
                    mapping = _json(mapping_path)
                    if mapping.get("huntId") != contract.get("huntId") or mapping.get("variantId") != variant:
                        continue
                    evidence["behaviorUnderstood"].extend(mapping.get("sourceMappedSemantics", []))
                    evidence["consumerImplemented"].extend(mapping.get("phoenixConsumersImplemented", []))
                    unresolved.extend(mapping.get("unresolvedRequirements", []))
        else:
            unresolved.append("targeted acquisition report is missing")
    elif not map_preview:
        unresolved = ["targeted acquisition report and terrain validation are not linked to this template",
                      "offline behavior trace and selected Phoenix consumer remain unverified"]
    # Applying source data is separate from final user-visible acceptance.
    acceptance_gaps = (["map-only client acceptance: arrival and map preview"] if map_preview else
                       ["client acceptance: arrival, grounded spawn, attacks, weapon/items, no disconnect and expected state changes"])
    chosen = next((x for x in (encounter.get("testSelection") or {}).get("behemothChoices", [])
                   if str(x.get("row", "")).lower() == str(variant or "").lower()), None)
    selected_is_default = map_preview or str(encounter.get("behemoth", "")).lower() == str(variant or "").lower()
    defaults = ({k: encounter[k] for k in (
        "level", "baseHealth", "montagePlayRate", "capsuleOffset", "enterCombatRange", "walkSpeed"
    ) if k in encounter} if selected_is_default else {})
    template = {
        "format": "phoenix-simulation-template", "version": 1,
        "kind": "map_preview" if map_preview else "hunt",
        "id": f"{contract['huntId']}.map_preview" if map_preview else f"{contract['huntId']}.{variant}",
        "huntId": contract["huntId"], "mapId": contract["worldObject"],
        "mapPackageId": contract["map"],
        "encounter": None if map_preview else {
            "speciesId": species, "variantId": variant, "behaviorTreeId": tree,
            "selectionId": f"standard-behemoth.{variant}",
            "selection": ({"pool": (encounter.get("testSelection") or {}).get("pool"),
                           "level": chosen.get("level"), "weight": chosen.get("weight")} if chosen else None),
            "placement": {"sourceId": f"{contract['huntId']}.encounter.centers", "centers": centers},
            "authoredDefaults": defaults,
        },
        "loadout": {"id": loadout}, "modifiers": [],
        "sourceProvenance": {
            "contractFormat": contract.get("format"), "contractVersion": contract.get("version"),
            "contractSha256": _sha(contract_path),
            "contractSource": "phoenix-island-preview-contract",
            **({"phoenixAcquisitionReportSha256": acquisition_provenance["reportSha256"],
                "acquisitionSourceHashes": acquisition_provenance["sourceHashes"]} if acquisition_provenance else {}),
        },
        "generatorVersions": {"atlasSimulationBundle": VERSION, "previewContract": contract.get("version"),
                              **acquisition_provenance.get("generatorVersions", {})},
        "supportedModifiers": [],
        "exclusions": ["PublicEvents: documented and intentionally bypassed in Phoenix"],
        "readinessEvidence": evidence,
        "unresolvedRequirements": sorted(set(unresolved)),
        "acceptanceGaps": acceptance_gaps,
        "dependencies": [],
    }
    errors = validate_template(template)
    if errors:
        raise ValueError("invalid generated template: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return template


def _paths(template: dict, server_root: Path):
    """Resolve dependency IDs from the selected contract and known runtime naming."""
    gs = server_root / "GameServer"
    hunt = template["huntId"]
    required = [
        ("hunt.world", gs / "world" / "islands" / f"{hunt}.json", f"world/islands/{hunt}.json"),
        ("hunt.contract", gs / "world" / "islands" / f"{hunt}.contract.json", f"world/islands/{hunt}.contract.json"),
    ]
    if template.get("kind", "hunt") == "map_preview":
        return [{"id": i, "source": p, "target": t, "required": True} for i, p, t in required]
    variant = template["encounter"]["variantId"]
    required.append(("hunt.engine_manifest", gs / "data" / "2.1.1" / "islands" / f"{hunt}.engine_data.json", f"data/2.1.1/islands/{hunt}.engine_data.json"))
    required.append(("behemoth.bodies", gs / "world" / "behemoths" / f"{variant}_bodies.json", f"world/behemoths/{variant}_bodies.json"))
    dependencies = [{"id": i, "source": p, "target": t, "required": True} for i, p, t in required]
    manifest_path = gs / "data" / "2.1.1" / "islands" / f"{hunt}.engine_data.json"
    if manifest_path.is_file():
        manifest = _json(manifest_path)
        if manifest.get("hunt") != hunt or manifest.get("variant") != variant:
            raise ValueError(f"engine manifest identity mismatch for {hunt}/{variant}")
        if manifest.get("behaviourTree") != template["encounter"]["behaviorTreeId"] or manifest.get("terrain") != f"{template['mapId']}_height":
            raise ValueError(f"engine manifest tree/terrain identity mismatch for {hunt}/{variant}")
        manifest_files = {_safe_rel(k): v for k, v in manifest.get("files", {}).items()}
        required_paths = {
            f"world/behemoths/{variant}.json", f"world/behemoths/{variant}_schema.json",
            f"world/behemoths/{variant}_damage.json", "world/behemoths/damage_model.json",
            f"world/behemoths/{variant}_bodies.json",
            f"world/maps/{template['mapId']}_height.json", f"world/maps/{template['mapId']}_height.bin",
            f"world/dumps/bt/{template['encounter']['behaviorTreeId']}.json",
        }
        if hunt == "Adventure_IslandP_D24" and manifest.get("navmesh"):
            required_paths.add(manifest["navmesh"])
        action_path = gs / "world" / "behemoths" / f"{variant}.json"
        if action_path.is_file():
            action_data = _json(action_path)
            if set(manifest.get("actions", [])) != set(action_data.get("actions", {})):
                raise ValueError("engine manifest action identities do not match selected action-set file")
            incomplete_actions = [name for name, row in action_data.get("actions", {}).items()
                                  if not row.get("class") or not row.get("attack") or not row.get("montage_data")]
            if incomplete_actions:
                dependencies.append({"id": "action-set-incomplete", "source": action_path,
                                     "target": f"world/behemoths/{variant}.json", "required": True,
                                     "validationIssue": "action rows lack attack/montage data: " + ", ".join(incomplete_actions)})
            for action in action_data.get("actions", {}).values():
                if action.get("montage"):
                    required_paths.add(f"world/dumps/montages/{action['montage']}.json")
        for rel in sorted(required_paths - set(manifest_files)):
            dependencies.append({"id": f"manifest-required:{rel}",
                                 "source": gs / Path(*PurePosixPath(rel).parts), "target": rel,
                                 "required": True, "validationIssue": "required input is omitted from engine manifest"})
        for problem in manifest.get("problems", []):
            dependencies.append({"id": "manifest-problem:" + str(problem), "source": gs / ".atlas_cache" / "manifest-problem",
                                 "target": f"world/.atlas_manifest_gaps/{len(dependencies)}.json", "required": True,
                                 "validationIssue": str(problem)})
        # Ensure the manifest's claimed reachable-tree closure is a real transitive closure.
        tree_dir = gs / "world" / "dumps" / "bt"
        visited, pending = set(), [template["encounter"]["behaviorTreeId"]]
        generic_action_required = False
        while pending:
            tree = pending.pop()
            if tree in visited:
                continue
            visited.add(tree)
            tree_path = tree_dir / f"{tree}.json"
            rel_tree = f"world/dumps/bt/{tree}.json"
            if not tree_path.is_file() or rel_tree not in manifest_files:
                dependencies.append({"id": f"reachable-tree:{tree}", "source": tree_path, "target": rel_tree,
                                     "required": True, "validationIssue": "reachable behavior tree is absent from the engine manifest"})
                continue
            try:
                exports = _json(tree_path)
            except (OSError, json.JSONDecodeError):
                dependencies.append({"id": f"reachable-tree:{tree}", "source": tree_path, "target": rel_tree,
                                     "required": True, "validationIssue": "reachable behavior tree export cannot be parsed"})
                continue
            for entry in exports if isinstance(exports, list) else []:
                if entry.get("Type") == "generic_action_bttask_C":
                    generic_action_required = True
                if entry.get("Type") != "BTTask_RunBehavior":
                    continue
                ref = (entry.get("Properties") or {}).get("BehaviorAsset") or {}
                path = str(ref.get("ObjectPath", "")).split(".", 1)[0]
                name = path.rsplit("/", 1)[-1] if path else ""
                if name:
                    pending.append(name)
        generic_path = f"world/behemoths/{variant}_generic_actions.json"
        if generic_action_required and generic_path not in manifest_files:
            dependencies.append({"id": "generic-action-lookup", "source": gs / generic_path,
                                 "target": generic_path, "required": True,
                                 "validationIssue": "reachable generic action tasks require their resolved montage lookup"})
        if set(manifest.get("trees", [])) != visited:
            dependencies.append({"id": "reachable-tree-closure", "source": tree_dir / f"{template['encounter']['behaviorTreeId']}.json",
                                 "target": f"world/dumps/bt/{template['encounter']['behaviorTreeId']}.json", "required": True,
                                 "validationIssue": "engine manifest tree list differs from the reachable tree closure"})
        terrain_header = gs / "world" / "maps" / f"{template['mapId']}_height.json"
        terrain_samples = gs / "world" / "maps" / f"{template['mapId']}_height.bin"
        if terrain_header.is_file() and terrain_samples.is_file():
            header = _json(terrain_header)
            expected_bytes = int(header.get("width", 0)) * int(header.get("height", 0)) * 2
            if expected_bytes <= 0 or terrain_samples.stat().st_size != expected_bytes:
                dependencies.append({"id": "terrain-grid-shape", "source": terrain_samples,
                                     "target": f"world/maps/{template['mapId']}_height.bin", "required": True,
                                     "validationIssue": "terrain header dimensions do not match height sample bytes"})
        for rel, expected_hash in manifest_files.items():
            safe = rel
            src = gs / Path(*PurePosixPath(safe).parts)
            if src.is_file() and _sha(src) != expected_hash:
                raise ValueError(f"engine manifest hash mismatch: {safe}")
            dependencies.append({"id": f"engine-data:{safe}", "source": src, "target": safe, "required": True})
    else:
        # Add known required runtime payloads so the research-plan gap list is actionable.
        for ident, rel in (
            ("behemoth.action_set", f"world/behemoths/{variant}.json"),
            ("behemoth.schema", f"world/behemoths/{variant}_schema.json"),
            ("behemoth.damage", f"world/behemoths/{variant}_damage.json"),
            ("behemoth.bodies", f"world/behemoths/{variant}_bodies.json"),
            ("behemoth.damage_model", "world/behemoths/damage_model.json"),
            ("terrain.height", f"world/maps/{template['mapId']}_height.json"),
            ("terrain.samples", f"world/maps/{template['mapId']}_height.bin"),
        ):
            dependencies.append({"id": ident, "source": gs / Path(*PurePosixPath(rel).parts), "target": rel, "required": True})
    # Keep deterministic order while removing the manifest's self-reference.
    unique = {}
    for dep in dependencies:
        unique[dep["target"]] = dep
    return list(unique.values())


def stage(template_path: Path, phoenix_root: Path, stage_dir: Path, allow_incomplete: bool = False) -> dict:
    template = _json(template_path)
    errors = validate_template(template)
    if errors:
        raise ValueError("invalid template: " + "; ".join(errors))
    phoenix_root = phoenix_root.resolve()
    gs = phoenix_root / "GameServer"
    if not gs.is_dir():
        raise ValueError(f"Phoenix checkout has no GameServer directory: {phoenix_root}")
    for marker in ("data/2.1.1/class_layouts.json", "data/2.1.1/layouts.jsonl"):
        if not (gs / marker).is_file():
            raise ValueError(f"Phoenix target build marker is missing: GameServer/{marker}")
    selected_contract = gs / "world" / "islands" / f"{template['huntId']}.contract.json"
    if selected_contract.is_file():
        c = _json(selected_contract)
        if c.get("huntId") != template["huntId"] or c.get("worldObject") != template["mapId"]:
            raise ValueError("selected Phoenix contract hunt/map identity does not match template")
        got = (c.get("encounter") or {}).get("behemoth")
        selected_variant = (template.get("encounter") or {}).get("variantId")
        if got and selected_variant and got != selected_variant:
            raise ValueError(f"selected Phoenix contract species {got} does not match template variant {template['encounter']['variantId']}")
    entries, missing = [], []
    for dep in _paths(template, phoenix_root):
        src = dep["source"]
        if dep.get("validationIssue"):
            missing.append({"id": dep["id"], "target": dep["target"], "reason": dep["validationIssue"]})
            continue
        if not src.is_file():
            missing.append({"id": dep["id"], "target": dep["target"], "reason": "source artifact missing"})
            continue
        entries.append({"id": dep["id"], "target": dep["target"], "sha256": _sha(src), "sourceName": src.name})
    runnable = not missing and not template.get("unresolvedRequirements")
    bundle = {
        "format": FORMAT, "version": VERSION, "template": template,
        "sourceBuild": "Dauntless-2.1.1", "targetBuild": "Phoenix-2.1.1",
        "targetBuildFingerprint": {rel: _sha(gs / rel) for rel in
                                   ("data/2.1.1/class_layouts.json", "data/2.1.1/layouts.jsonl")},
        "runnable": runnable, "dependencies": entries, "missing": missing,
        "unresolvedRequirements": template.get("unresolvedRequirements", []),
        "exclusions": template.get("exclusions", []),
        "evidence": {**template.get("readinessEvidence", {}),
                     "dataPresent": sorted(set(template.get("readinessEvidence", {}).get("dataPresent", []) +
                                               [e["id"] for e in entries]))},
        "acceptanceGaps": template.get("acceptanceGaps", []),
    }
    if not runnable and not allow_incomplete:
        raise ValueError("bundle is incomplete; pass --allow-incomplete only to stage a research plan")
    stage_dir = stage_dir.resolve()
    stage_dir.mkdir(parents=True, exist_ok=True)
    data_root = stage_dir / "payload"
    for dep in _paths(template, phoenix_root):
        if dep["source"].is_file() and not dep.get("validationIssue"):
            dst = data_root / dep["target"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dep["source"], dst)
    (stage_dir / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return bundle


def apply(stage_dir: Path, phoenix_root: Path) -> dict:
    stage_dir, phoenix_root = stage_dir.resolve(), phoenix_root.resolve()
    bundle_path = stage_dir / "bundle.json"
    bundle = _json(bundle_path)
    if bundle.get("format") != FORMAT or bundle.get("version") != VERSION:
        raise ValueError("unsupported simulation bundle")
    if not bundle.get("runnable") or bundle.get("missing") or bundle.get("unresolvedRequirements"):
        raise ValueError("refusing apply: bundle is incomplete or has unresolved requirements")
    if bundle.get("sourceBuild") != "Dauntless-2.1.1" or bundle.get("targetBuild") != "Phoenix-2.1.1":
        raise ValueError("bundle source/target build identity mismatch")
    gs = phoenix_root / "GameServer"
    if not gs.is_dir() or not (gs / "data" / "2.1.1" / "class_layouts.json").is_file():
        raise ValueError("target is not a supported Phoenix 2.1.1 GameServer checkout")
    fingerprint = bundle.get("targetBuildFingerprint", {})
    if set(fingerprint) != {"data/2.1.1/class_layouts.json", "data/2.1.1/layouts.jsonl"}:
        raise ValueError("bundle lacks the required Phoenix build fingerprints")
    for rel, expected in fingerprint.items():
        safe = _safe_rel(rel)
        marker = gs / Path(*PurePosixPath(safe).parts)
        if not marker.is_file() or _sha(marker) != expected:
            raise ValueError(f"target Phoenix build fingerprint mismatch: {safe}")
    payload = stage_dir / "payload"
    template_errors = validate_template(bundle.get("template", {}))
    if template_errors:
        raise ValueError("bundle template is invalid: " + "; ".join(template_errors))
    _validate_staged_content(bundle, payload)
    files = []
    for dep in bundle["dependencies"]:
        rel = _safe_rel(dep["target"])
        src = payload / rel
        if not src.is_file() or _sha(src) != dep["sha256"]:
            raise ValueError(f"staged dependency missing or changed: {dep['id']}")
        if not src.resolve().is_relative_to(payload.resolve()):
            raise ValueError(f"staged dependency escapes bundle payload: {rel}")
        target = (gs / rel).resolve()
        if not target.is_relative_to(gs.resolve()):
            raise ValueError(f"target escapes Phoenix GameServer: {rel}")
        files.append((src, target, dep))
    apply_path = stage_dir / "apply.json"
    if apply_path.is_file():
        previous = _json(apply_path)
        if previous.get("status") == "applied" and all(dst.is_file() and _sha(dst) == dep["sha256"] for _, dst, dep in files):
            return {**previous, "status": "already-applied"}
    # All validation happens before writing. Journal and back up all targets before the first replacement.
    rollback = stage_dir / "rollback"
    transaction = []
    for _, dst, dep in files:
        if dst.exists() and _sha(dst) == dep["sha256"]:
            continue
        if dst.exists():
            backup = rollback / dep["target"]
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
            transaction.append({"target": dep["target"], "backup": str(backup.relative_to(stage_dir)), "created": False})
        else:
            transaction.append({"target": dep["target"], "backup": None, "created": True})
    apply_record = {"format": "phoenix-simulation-apply", "bundle": bundle_path.name,
                    "status": "prepared", "files": transaction, "rollbackDirectory": "rollback"}
    apply_path.write_text(json.dumps(apply_record, indent=2) + "\n", encoding="utf-8")
    try:
        for src, dst, dep in files:
            if dst.exists() and _sha(dst) == dep["sha256"]:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp", dir=dst.parent)
            os.close(fd)
            shutil.copy2(src, tmp_name)
            os.replace(tmp_name, dst)
    except Exception:
        _rollback_records(transaction, stage_dir, gs)
        apply_record["status"] = "rolled-back-after-error"
        apply_path.write_text(json.dumps(apply_record, indent=2) + "\n", encoding="utf-8")
        raise
    apply_record["status"] = "applied"
    apply_path.write_text(json.dumps(apply_record, indent=2) + "\n", encoding="utf-8")
    return apply_record


def _validate_staged_content(bundle: dict, payload: Path):
    """Recheck identities and required inputs from staged bytes before touching Phoenix."""
    template = bundle["template"]
    hunt, map_id = template["huntId"], template["mapId"]
    files = {d["target"]: d["sha256"] for d in bundle["dependencies"]}

    def read(rel):
        path = payload / _safe_rel(rel)
        if not path.is_file():
            raise ValueError(f"required staged content is missing: {rel}")
        return _json(path)

    world = read(f"world/islands/{hunt}.json")
    contract = read(f"world/islands/{hunt}.contract.json")
    if contract.get("huntId") != hunt or contract.get("worldObject") != map_id:
        raise ValueError("staged island contract hunt/map identity mismatch")
    if template.get("kind", "hunt") == "map_preview":
        if any("engine_data.json" in x or "world/behemoths/" in x for x in files):
            raise ValueError("map-only preview bundle contains behemoth engine requirements")
        return

    encounter = template["encounter"]
    manifest = read(f"data/2.1.1/islands/{hunt}.engine_data.json")
    if manifest.get("hunt") != hunt or manifest.get("variant") != encounter["variantId"]:
        raise ValueError("staged engine manifest hunt/species identity mismatch")
    if manifest.get("terrain") != f"{map_id}_height" or manifest.get("behaviourTree") != encounter["behaviorTreeId"]:
        raise ValueError("staged engine manifest map/tree identity mismatch")
    if manifest.get("problems"):
        raise ValueError("staged engine manifest records unresolved inputs: " + "; ".join(manifest["problems"]))
    required = {
        f"world/behemoths/{encounter['variantId']}.json",
        f"world/behemoths/{encounter['variantId']}_schema.json",
        f"world/behemoths/{encounter['variantId']}_damage.json",
        f"world/behemoths/{encounter['variantId']}_bodies.json",
        "world/behemoths/damage_model.json",
        f"world/maps/{map_id}_height.json", f"world/maps/{map_id}_height.bin",
    }
    missing_required = required - set(manifest.get("files", {}))
    if missing_required:
        raise ValueError("engine manifest omits required files: " + ", ".join(sorted(missing_required)))
    for rel, expected_hash in manifest["files"].items():
        rel = _safe_rel(rel)
        if files.get(rel) != expected_hash:
            raise ValueError(f"bundle dependency does not match engine manifest hash: {rel}")
        path = payload / rel
        if not path.is_file() or _sha(path) != expected_hash:
            raise ValueError(f"staged manifest input missing or changed: {rel}")
    action_data = read(f"world/behemoths/{encounter['variantId']}.json")
    if set(manifest.get("actions", [])) != set(action_data.get("actions", {})):
        raise ValueError("action set does not match engine manifest")
    for name, action in action_data["actions"].items():
        if not action.get("class") or not action.get("attack") or not action.get("montage_data"):
            raise ValueError(f"action {name} lacks attack or montage data")
        montage_rel = f"world/dumps/montages/{action['montage']}.json"
        if montage_rel not in files:
            raise ValueError(f"action {name} montage is absent from the manifest: {montage_rel}")
    schema = read(f"world/behemoths/{encounter['variantId']}_schema.json")
    damage = read(f"world/behemoths/{encounter['variantId']}_damage.json")
    bodies = read(f"world/behemoths/{encounter['variantId']}_bodies.json")
    if not schema.get("classes") or not damage.get("rows") or not bodies.get("bodies"):
        raise ValueError("species schema, damage, or body resources are empty")
    terrain_header = read(f"world/maps/{map_id}_height.json")
    samples = payload / f"world/maps/{map_id}_height.bin"
    if samples.stat().st_size != int(terrain_header.get("width", 0)) * int(terrain_header.get("height", 0)) * 2:
        raise ValueError("terrain height sample size does not match its header")
    if encounter["behaviorTreeId"] not in manifest.get("trees", []):
        raise ValueError("selected behavior tree is absent from the engine manifest")


def _rollback_records(records, stage_dir: Path, gs: Path):
    for item in reversed(records):
        target = (gs / _safe_rel(item["target"])).resolve()
        if not target.is_relative_to(gs.resolve()):
            raise ValueError(f"rollback target escapes Phoenix GameServer: {item['target']}")
        if item["created"]:
            target.unlink(missing_ok=True)
        else:
            backup = (stage_dir / _safe_rel(item["backup"])).resolve()
            if not backup.is_file():
                raise ValueError(f"rollback backup missing: {item['backup']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)


def rollback(stage_dir: Path, phoenix_root: Path) -> dict:
    stage_dir, gs = stage_dir.resolve(), phoenix_root.resolve() / "GameServer"
    record_path = stage_dir / "apply.json"
    record = _json(record_path)
    if record.get("format") != "phoenix-simulation-apply" or record.get("status") not in ("prepared", "applied"):
        raise ValueError("no pending or completed apply transaction to roll back")
    _rollback_records(record.get("files", []), stage_dir, gs)
    record["status"] = "rolled-back"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def acquire(contract_path: Path, phoenix_root: Path, species: str, variant: str, tree: str,
    tree_package: str, template_path: Path, bundle_path: Path,
            exporter_args: list[str] | None = None, allow_incomplete: bool = False):
    """Run Phoenix's targeted exporter, regenerate the selected template, and stage it."""
    phoenix_root, contract_path = phoenix_root.resolve(), contract_path.resolve()
    hunt = _json(contract_path).get("huntId")
    if not isinstance(hunt, str) or not hunt:
        raise ValueError("selected Phoenix contract has no huntId")
    if not all((species, variant, tree, tree_package)):
        raise ValueError("species, variant, tree ID, and full tree package are required")
    normalized_tree_package = tree_package.replace("\\", "/").rstrip("/")
    if not normalized_tree_package.startswith("Archon/Content/") or normalized_tree_package.rsplit("/", 1)[-1].split(".", 1)[0] != tree:
        raise ValueError("tree package must be an AssetDump Archon/Content path whose package name matches --tree")
    exporter = phoenix_root / "GameServer" / "tools" / "export_simulation_inputs.py"
    if not exporter.is_file():
        raise ValueError(f"Phoenix targeted exporter is missing: {exporter}")
    extra = list(exporter_args or [])
    protected = {"--hunt", "--variant", "--species", "--contract", "--tree-package", "--out"}
    if any(arg.split("=", 1)[0] in protected for arg in extra):
        raise ValueError("exporter pass-through cannot override selected hunt/species/tree/output identity")
    command = [sys.executable, str(exporter), "--hunt", hunt, "--variant", variant,
               "--species", species, "--contract", str(contract_path),
               "--tree-package", tree_package, "--out", str(phoenix_root / "GameServer" / "world" / "behemoths"), *extra]
    subprocess.run(command, cwd=phoenix_root / "GameServer", check=True)
    template_from_contract(contract_path, template_path, species, variant, tree, phoenix_root=phoenix_root)
    bundle = stage(template_path, phoenix_root, bundle_path, allow_incomplete=allow_incomplete)
    return {"template": str(template_path), "runnable": bundle["runnable"], "bundle": str(bundle_path),
            "dependencies": len(bundle["dependencies"]), "missing": bundle["missing"],
            "unresolvedRequirements": bundle["unresolvedRequirements"]}


def _cli(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template", help="derive a versioned template from a Phoenix island preview contract")
    t.add_argument("contract", type=Path); t.add_argument("--species"); t.add_argument("--variant")
    t.add_argument("--tree"); t.add_argument("--loadout", default="phoenix.default"); t.add_argument("--out", type=Path, required=True)
    t.add_argument("--phoenix-root", type=Path, help="link the selected Phoenix checkout's acquisition/readiness report")
    s = sub.add_parser("stage", help="stage server-owned payload files and a dependency manifest")
    s.add_argument("template", type=Path); s.add_argument("--phoenix", type=Path, required=True); s.add_argument("--out", type=Path, required=True)
    s.add_argument("--allow-incomplete", action="store_true")
    a = sub.add_parser("apply", help="apply only a complete staged bundle to a Phoenix checkout")
    a.add_argument("stage", type=Path); a.add_argument("--phoenix", type=Path, required=True)
    r = sub.add_parser("rollback", help="restore the pre-apply files recorded in a staged bundle")
    r.add_argument("stage", type=Path); r.add_argument("--phoenix", type=Path, required=True)
    ac = sub.add_parser("acquire", help="run Phoenix targeted acquisition, regenerate a template, and stage it")
    ac.add_argument("contract", type=Path); ac.add_argument("--phoenix", type=Path, required=True)
    ac.add_argument("--species", required=True); ac.add_argument("--variant", required=True)
    ac.add_argument("--tree", required=True, help="stable behavior-tree asset ID")
    ac.add_argument("--tree-package", required=True, help="full AssetDump package path, such as Archon/Content/Monsters/...")
    ac.add_argument("--template-out", type=Path, required=True); ac.add_argument("--stage-out", type=Path, required=True)
    ac.add_argument("--allow-incomplete", action="store_true")
    ac.add_argument("--exporter-arg", action="append", default=[], metavar="ARG",
                    help="one argument forwarded to the Phoenix exporter; repeat for each argument")
    args = p.parse_args(argv)
    try:
        if args.cmd == "template":
            template_from_contract(args.contract, args.out, args.species, args.variant, args.tree, args.loadout, args.phoenix_root)
            print(args.out)
        elif args.cmd == "stage":
            b = stage(args.template, args.phoenix, args.out, args.allow_incomplete)
            print(json.dumps({"runnable": b["runnable"], "dependencies": len(b["dependencies"]), "missing": b["missing"]}, indent=2))
        elif args.cmd == "apply":
            print(json.dumps(apply(args.stage, args.phoenix), indent=2))
        elif args.cmd == "acquire":
            result = acquire(args.contract, args.phoenix, args.species, args.variant, args.tree,
                             args.tree_package, args.template_out, args.stage_out,
                             args.exporter_arg, args.allow_incomplete)
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(rollback(args.stage, args.phoenix), indent=2))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as e:
        p.exit(2, f"simulation {args.cmd} failed: {e}\n")


if __name__ == "__main__":
    _cli()
