"""Focused safety and selection tests for simulation bundle staging/apply."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.atlas import simulations


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def template(hunt="Adventure_Test_D24", map_id="test_map", variant="test_species", tree="test_bt"):
    return {
        "format": "phoenix-simulation-template", "version": 1, "kind": "hunt",
        "id": f"{hunt}.{variant}", "huntId": hunt, "mapId": map_id,
        "encounter": {"speciesId": variant.split("_")[0], "variantId": variant,
                      "behaviorTreeId": tree, "placement": {"sourceId": f"{hunt}.centers"}},
        "loadout": {"id": "phoenix.default"}, "modifiers": [], "supportedModifiers": [],
        "sourceProvenance": {"contractSha256": "test"}, "generatorVersions": {"test": 1},
        "readinessEvidence": {"dataPresent": [], "behaviorUnderstood": [], "consumerImplemented": [],
                              "offlineChecksPassed": [], "clientAccepted": []},
        "exclusions": ["PublicEvents: documented and intentionally bypassed in Phoenix"],
        "unresolvedRequirements": [], "acceptanceGaps": [], "dependencies": [],
    }


def make_checkout(root, hunt="Adventure_Test_D24", map_id="test_map", variant="test_species", tree="test_bt"):
    gs = root / "GameServer"
    write_json(gs / "data/2.1.1/class_layouts.json", {})
    (gs / "data/2.1.1/layouts.jsonl").write_text("{}\n", encoding="utf-8")
    write_json(gs / f"world/islands/{hunt}.json", {"entries": []})
    write_json(gs / f"world/islands/{hunt}.contract.json", {
        "huntId": hunt, "worldObject": map_id,
        "encounter": {"behemoth": variant, "centers": []},
    })
    payloads = {
        f"world/behemoths/{variant}.json": {"actions": {"hit": {
            "class": "test_hit_C", "attack": {"boxes": []}, "montage": "test_montage",
            "montage_data": {"length": 1, "events": []},
        }}},
        f"world/behemoths/{variant}_schema.json": {"classes": [{"class": "test_species_C"}]},
        f"world/behemoths/{variant}_damage.json": {"rows": {"hit": {"row": {"BaseDamage": 1}}}},
        f"world/behemoths/{variant}_bodies.json": {"bodies": {"root": []}, "joints": {}},
        "world/behemoths/damage_model.json": {"rows": []},
        "world/maps/test_map_height.json": {"width": 1, "height": 1, "quad_origin": [0, 0],
                                            "landscape_location": [0, 0, 0], "landscape_scale": [1, 1, 1],
                                            "quad_world": [1, 1], "z_world": {"base": 0, "scale": 1}},
        f"world/dumps/bt/{tree}.json": [{"Type": "BehaviorTree", "Name": tree, "Properties": {}}],
        "world/dumps/montages/test_montage.json": [{"Type": "AnimMontage", "Name": "test_montage"}],
    }
    for rel, data in payloads.items():
        write_json(gs / rel, data)
    (gs / "world/maps/test_map_height.bin").write_bytes(b"\x00\x00")
    files = {rel: sha(gs / rel) for rel in payloads}
    files["world/maps/test_map_height.bin"] = sha(gs / "world/maps/test_map_height.bin")
    write_json(gs / f"data/2.1.1/islands/{hunt}.engine_data.json", {
        "hunt": hunt, "variant": variant, "behaviourTree": tree, "terrain": f"{map_id}_height",
        "actions": ["hit"], "trees": [tree], "problems": [], "files": files,
    })
    return gs


class SimulationBundles(unittest.TestCase):
    def test_acquire_cannot_override_the_selected_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "phoenix"
            exporter = root / "GameServer/tools/export_simulation_inputs.py"
            exporter.parent.mkdir(parents=True)
            exporter.write_text("# test exporter\n", encoding="utf-8")
            contract = write_json(Path(d) / "contract.json", {"huntId": "Adventure_Test_D24"})
            with self.assertRaisesRegex(ValueError, "cannot override selected"):
                simulations.acquire(contract, root, "test", "test_species", "test_bt", "Archon/Content/test_bt",
                                    Path(d) / "template.json", Path(d) / "bundle",
                                    ["--variant=wrong_species"])

    def test_authored_alternate_species_uses_its_own_identity(self):
        with tempfile.TemporaryDirectory() as d:
            contract = write_json(Path(d) / "contract.json", {
                "format": "phoenix-island-contract", "version": 1, "huntId": "Adventure_Test_D24",
                "map": "/Game/test_map", "worldObject": "test_map", "encounter": {
                    "behemoth": "sally_normal", "baseHealth": 37400,
                    "centers": [], "testSelection": {"pool": "StandardBehemoth", "behemothChoices": [
                        {"row": "sally_normal", "level": 20, "weight": 50},
                        {"row": "beaver_blaze", "level": 20, "weight": 50},
                    ]},
                },
            })
            out = Path(d) / "template.json"
            result = simulations.template_from_contract(contract, out, "beaver", "beaver_blaze", "beaver_bt")
            self.assertEqual(result["encounter"]["variantId"], "beaver_blaze")
            self.assertEqual(result["encounter"]["selection"]["weight"], 50)
            self.assertEqual(result["encounter"]["authoredDefaults"], {})

    def test_trace_with_unhandled_nodes_is_not_marked_as_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "phoenix"
            contract = write_json(Path(d) / "contract.json", {
                "format": "phoenix-island-contract", "version": 1, "huntId": "Adventure_Test_D24",
                "map": "/Game/test_map", "worldObject": "test_map", "encounter": {
                    "behemoth": "test_species", "centers": [],
                },
            })
            write_json(root / "GameServer/world/behemoths/test_species_acquisition.json", {
                "acquired": {"actions": 1, "trees": ["test_bt"]},
                "gaps": {"behaviorTrace": []},
                "behaviorTrace": {"attacks": 1, "unhandled": [{"type": "unknown_task"}]},
            })
            result = simulations.template_from_contract(contract, Path(d) / "template.json",
                                                        "test", "test_species", "test_bt", phoenix_root=root)
            self.assertEqual(result["readinessEvidence"]["offlineChecksPassed"], [])
            self.assertTrue(any("unknown_task" in x for x in result["unresolvedRequirements"]))

    def test_rejects_unknown_modifier_and_path_escape(self):
        t = template()
        t["modifiers"] = ["modifier.unregistered"]
        self.assertTrue(any("supportedModifiers" in e for e in simulations.validate_template(t)))
        for value in ("../outside", "C:\\outside", "a/../../b"):
            with self.assertRaises(ValueError):
                simulations._safe_rel(value)

    def test_map_only_public_event_bypass_needs_no_behemoth_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "source"
            gs = root / "GameServer"
            write_json(gs / "data/2.1.1/class_layouts.json", {})
            (gs / "data/2.1.1/layouts.jsonl").write_text("{}\n", encoding="utf-8")
            hunt = "PublicEvent_Test_D24"
            write_json(gs / f"world/islands/{hunt}.json", {"entries": []})
            write_json(gs / f"world/islands/{hunt}.contract.json", {
                "huntId": hunt, "worldObject": "event_map", "encounter": None,
            })
            contract = write_json(Path(d) / "event.contract.json", {
                "format": "phoenix-island-contract", "version": 1, "huntId": hunt,
                "map": "/Game/event_map", "worldObject": "event_map", "encounter": None,
            })
            t = simulations.template_from_contract(contract, Path(d) / "event.template.json", None, None, None)
            self.assertEqual(t["kind"], "map_preview")
            self.assertEqual(t["id"], f"{hunt}.map_preview")
            self.assertIsNone(t["encounter"])
            self.assertTrue(any("PublicEvents" in x for x in t["exclusions"]))
            result = simulations.stage(Path(d) / "event.template.json", root, Path(d) / "event.bundle")
            self.assertTrue(result["runnable"])
            self.assertEqual({x["id"] for x in result["dependencies"]}, {"hunt.world", "hunt.contract"})

    def test_missing_dependency_is_research_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "source"
            make_checkout(root)
            (root / "GameServer/world/behemoths/test_species_damage.json").unlink()
            t = template()
            path = write_json(Path(d) / "template.json", t)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                simulations.stage(path, root, Path(d) / "strict")
            bundle = simulations.stage(path, root, Path(d) / "research", allow_incomplete=True)
            self.assertFalse(bundle["runnable"])
            self.assertTrue(any(x["target"] == "world/behemoths/test_species_damage.json" for x in bundle["missing"]))

    def test_staged_bundle_is_deterministic_and_portable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "phoenix"
            make_checkout(root)
            template_path = write_json(Path(d) / "template.json", template())
            left, right = Path(d) / "left", Path(d) / "right"
            simulations.stage(template_path, root, left)
            simulations.stage(template_path, root, right)
            left_bytes = (left / "bundle.json").read_bytes()
            self.assertEqual(left_bytes, (right / "bundle.json").read_bytes())
            encoded = left_bytes.decode("utf-8")
            self.assertNotIn(str(root), encoded)
            self.assertNotIn(str(Path(d)), encoded)

    def test_changed_source_and_wrong_hunt_species_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "source"
            gs = make_checkout(root)
            t_path = write_json(Path(d) / "template.json", template())
            # An output whose contract points at another hunt cannot satisfy the template identity.
            bad_contract = gs / "world/islands/Adventure_Test_D24.contract.json"
            write_json(bad_contract, {"huntId": "Other_Hunt", "worldObject": "test_map", "encounter": {"behemoth": "test_species"}})
            with self.assertRaisesRegex(ValueError, "identity"):
                simulations.stage(t_path, root, Path(d) / "wrong-hunt")
            write_json(bad_contract, {"huntId": "Adventure_Test_D24", "worldObject": "test_map", "encounter": {"behemoth": "wrong_species"}})
            with self.assertRaisesRegex(ValueError, "species"):
                simulations.stage(t_path, root, Path(d) / "wrong-species")
            make_checkout(root)
            write_json(gs / "world/behemoths/test_species.json", {"actions": {"hit": {"class": "changed"}}})
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                simulations.stage(t_path, root, Path(d) / "changed-source")

    def test_apply_repeat_rollback_and_interrupted_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "source"
            target = Path(d) / "target"
            source_gs = make_checkout(root)
            target_gs = target / "GameServer"
            write_json(target_gs / "data/2.1.1/class_layouts.json", {})
            (target_gs / "data/2.1.1/layouts.jsonl").write_text("{}\n", encoding="utf-8")
            write_json(target_gs / "world/behemoths/test_species.json", {"previous": True})
            template_path = write_json(Path(d) / "template.json", template())
            bundle_path = Path(d) / "bundle"
            simulations.stage(template_path, root, bundle_path)
            original_action = (target_gs / "world/behemoths/test_species.json").read_bytes()
            calls = 0
            real_replace = simulations.os.replace

            def fail_second(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated interrupted copy")
                return real_replace(src, dst)

            with mock.patch.object(simulations.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    simulations.apply(bundle_path, target)
            self.assertEqual((target_gs / "world/behemoths/test_species.json").read_bytes(), original_action)
            self.assertEqual(json.loads((bundle_path / "apply.json").read_text())["status"], "rolled-back-after-error")

            simulations.apply(bundle_path, target)
            repeated = simulations.apply(bundle_path, target)
            self.assertEqual(repeated["status"], "already-applied")
            simulations.rollback(bundle_path, target)
            self.assertEqual((target_gs / "world/behemoths/test_species.json").read_bytes(), original_action)
            self.assertEqual(json.loads((bundle_path / "apply.json").read_text())["status"], "rolled-back")


if __name__ == "__main__":
    unittest.main()
