import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from application.config_manager import ConfigManager
from core.analyzer import Violation
from infrastructure.persistence import (
    AlertDatabase,
    ConfigRepository,
    MachineDatabase,
    RevisionConflict,
)
from utils.passwords import hash_password, is_password_hash, verify_password
from webapp.server import create_app
from webapp.state import RuntimeState
from main import MachineVisionSystem, _rules_for_camera
from core.capture import CameraConfig


class UnifiedDatabaseTests(unittest.TestCase):
    def make_config(self, root: Path, *, invalid_rule_model: bool = False) -> Path:
        config = root / "config"
        config.mkdir()
        (config / "settings.yaml").write_text(
            """database:\n  path: storage/machine.db\npanel:\n  username: admin\n  password: secret\nllm:\n  api_key: sk-test-secret\n""",
            encoding="utf-8",
        )
        (config / "cameras.yaml").write_text(
            """cameras:\n  - id: CAM_1\n    name: Gate\n    rtsp_url: rtsp://user:pass@127.0.0.1:554/live\n    enabled: false\n    rules: [1]\n""",
            encoding="utf-8",
        )
        (config / "rule_templates.yaml").write_text(
            """templates:\n  generic_presence:\n    label: Generic presence\n    logic: presence\n    params:\n      - name: trigger_classes\n        type: classes\n        default: []\n        from_model: true\n""",
            encoding="utf-8",
        )
        model = "missing" if invalid_rule_model else "detector"
        (config / "rules.yaml").write_text(
            f"""rules:\n  - id: 1\n    name: Test rule\n    description: A test rule\n    category: safety\n    template: generic_presence\n    models: [{model}]\n    params:\n      trigger_classes: [person]\n    severity: 3\n    enabled: true\n""",
            encoding="utf-8",
        )
        # The model registry is intentionally kept in settings.yaml because
        # that is the legacy source format used by the importer.
        settings = (config / "settings.yaml").read_text(encoding="utf-8")
        settings += "model:\n  models:\n    - name: detector\n      path: models/detector.pt\n      enabled: true\n"
        (config / "settings.yaml").write_text(settings, encoding="utf-8")
        return config

    def test_empty_database_migrates_and_pragmas_are_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            db = MachineDatabase(Path(td) / "machine.db")
            with db.connection() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue({"schema_migrations", "settings_sections", "models",
                                 "cameras", "rule_templates", "rules", "camera_rules",
                                 "alerts"}.issubset(tables))
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
                self.assertEqual(
                    conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    2,
                )

    def test_failed_migration_rolls_back_schema(self):
        original = MachineDatabase._migration_v1

        def fail_after_partial_ddl(conn):
            conn.execute("CREATE TABLE partial_should_rollback(id INTEGER)")
            raise RuntimeError("migration failure")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "machine.db"
            with patch.object(MachineDatabase, "_migration_v1", staticmethod(fail_after_partial_ddl)):
                with self.assertRaises(RuntimeError):
                    MachineDatabase(path)
            # A new database can be opened after the failed migration and must
            # not see the partially-created table.
            db = MachineDatabase(path)
            with db.connection() as conn:
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='partial_should_rollback'"
                ).fetchone())
        self.assertIsNotNone(original)

    def test_yaml_import_is_atomic_and_reimport_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            revision = repo.import_yaml(config)
            self.assertGreaterEqual(revision, 2)
            self.assertTrue(is_password_hash(repo.get_section("panel")["password"]))
            self.assertEqual(repo.get_cameras()[0]["rules"], [1])
            self.assertFalse(repo.get_cameras()[0]["enabled"])
            self.assertEqual(repo.get_rules()[0].category, "safety")
            camera = repo.get_cameras()[0]
            camera["name"] = "Gate renamed"
            repo.save_cameras([camera])
            with db.connection() as conn:
                audit_json = "\n".join(
                    (row[0] or "") + "\n" + (row[1] or "")
                    for row in conn.execute(
                        "SELECT before_json, after_json FROM config_audit_log"
                    ).fetchall()
                )
            self.assertNotIn("sk-test-secret", audit_json)
            self.assertNotIn("pbkdf2_sha256$", audit_json)
            self.assertNotIn("rtsp://user:pass@", audit_json)
            self.assertIn("rtsp://user:****@", audit_json)
            with self.assertRaises(ValueError):
                repo.import_yaml(config)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root, invalid_rule_model=True)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            with self.assertRaises(ValueError):
                repo.import_yaml(config)
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM settings_sections").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM models").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0], 0)

    def test_yaml_import_validates_and_persists_camera_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            (config / "cameras.yaml").write_text(
                """cameras:
  - id: CAM_1
    name: Gate
    rtsp_url: rtsp://127.0.0.1/live
    enabled: false
    rules: [1]
    rule_overrides:
      "1":
        trigger_classes: [worker]
""",
                encoding="utf-8",
            )
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            camera = repo.get_cameras()[0]
            self.assertEqual(camera["rule_overrides"], {"1": {"trigger_classes": ["worker"]}})
            snapshot = repo.read_snapshot_data()
            self.assertEqual(snapshot[2][0]["rule_overrides"], camera["rule_overrides"])

    def test_yaml_import_rejects_duplicate_or_unassigned_camera_rules_atomically(self):
        cases = (
            "rules: [1, 1]",
            "rules: [1]\n    rule_overrides:\n      \"2\": {trigger_classes: [worker]}",
        )
        for camera_rules in cases:
            with self.subTest(camera_rules=camera_rules), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                config = self.make_config(root)
                camera_yaml = (
                    "cameras:\n"
                    "  - id: CAM_1\n"
                    "    name: Gate\n"
                    "    rtsp_url: rtsp://127.0.0.1/live\n"
                    "    " + camera_rules + "\n"
                )
                (config / "cameras.yaml").write_text(camera_yaml, encoding="utf-8")
                db = MachineDatabase(root / "machine.db")
                repo = ConfigRepository(db)
                with self.assertRaises(ValueError):
                    repo.import_yaml(config)
                with db.connection() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0], 0)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM camera_rules").fetchone()[0], 0)

    def test_deleting_referenced_objects_is_rejected_without_data_loss(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            model = repo.get_models()[0]
            template = repo.get_templates()["generic_presence"]
            rule = repo.get_rule_by_id(1)
            camera = repo.get_cameras()[0]

            with self.assertRaises(ValueError):
                repo.delete_model(model["name"], expected_revision=model["revision"])
            with self.assertRaises(ValueError):
                repo.delete_template("generic_presence", expected_revision=template["revision"])
            with self.assertRaises(ValueError):
                repo.delete_rule(rule.id, expected_revision=rule.revision)

            self.assertEqual(repo.get_models()[0]["name"], model["name"])
            self.assertIn("generic_presence", repo.get_templates())
            self.assertIsNotNone(repo.get_rule_by_id(1))
            self.assertEqual(repo.get_cameras()[0]["id"], camera["id"])

    def test_revisions_and_camera_associations_are_transactional(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            section_revision = repo.get_section_revisions()["llm"]
            repo.update_section("llm", {"model": "new-model"}, expected_revision=section_revision)
            with self.assertRaises(RevisionConflict):
                repo.update_section("llm", {"model": "stale"}, expected_revision=section_revision)

            cameras = repo.get_cameras()
            cameras[0]["enabled"] = True
            cameras[0]["rules"] = [1]
            repo.save_cameras(cameras)
            self.assertTrue(repo.get_cameras()[0]["enabled"])
            with self.assertRaises(ValueError):
                repo.save_cameras([{**cameras[0], "rules": [999]}])
            self.assertEqual(repo.get_cameras()[0]["rules"], [1])

            repo.save_cameras([])
            self.assertEqual(repo.get_cameras(), [])
            self.assertEqual(len(repo.get_cameras(include_deleted=True)), 1)

    def test_rules_and_templates_use_machine_database(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            rule, _ = repo.add_rule({
                "id": 7, "name": "Second", "template": "generic_presence",
                "models": ["detector"], "params": {"trigger_classes": ["person"]},
            })
            self.assertEqual(repo.get_rule_by_id(7).name, "Second")
            repo.update_rule(7, {"enabled": False, "severity": 4})
            self.assertFalse(repo.get_rule_by_id(7).enabled)
            with self.assertRaises(ValueError):
                repo.delete_template("generic_presence")
            repo.delete_rule(7)
            repo.save_cameras([])
            repo.delete_rule(1)
            repo.delete_template("generic_presence")
            self.assertNotIn("generic_presence", repo.get_templates())

    def test_alerts_share_database_and_keep_name_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            alert_db = AlertDatabase(repo.get_settings(), database=db)
            violation = Violation(
                camera_id="CAM_1", rule_id=1, rule_name="Test rule",
                description="desc", confidence=0.9, severity=3,
                timestamp=1700000000.0, extra={"token": "should-not-be-logged"},
            )
            alert_id = alert_db.insert_alert(violation, "storage/snapshots/a.jpg")
            self.assertIsNotNone(alert_id)
            item = alert_db.get_alerts()["items"][0]
            self.assertEqual(item["camera_name"], "Gate")
            self.assertEqual(item["rule_name"], "Test rule")
            self.assertEqual(alert_db.get_alert_count(), 1)
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 2)


    def test_object_revisions_and_independent_updates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)

            rule_revision = repo.get_rule_by_id(1).revision
            repo.update_rule(1, {"severity": 4}, expected_revision=rule_revision)
            with self.assertRaises(RevisionConflict):
                repo.update_rule(1, {"severity": 5}, expected_revision=rule_revision)

            model_revision = repo.get_models()[0]["revision"]
            repo.update_model("detector", {"enabled": False}, expected_revision=model_revision)
            with self.assertRaises(RevisionConflict):
                repo.update_model("detector", {"enabled": True}, expected_revision=model_revision)

            camera_revision = repo.get_cameras()[0]["revision"]
            current = repo.get_cameras()[0]
            current["name"] = "Gate 2"
            repo.save_cameras([current], expected_revisions={"CAM_1": camera_revision})
            with self.assertRaises(RevisionConflict):
                stale = dict(current)
                stale["name"] = "stale"
                repo.save_cameras([stale], expected_revisions={"CAM_1": camera_revision})

            self.assertEqual(repo.get_rule_by_id(1).severity, 4)
            self.assertFalse(repo.get_models()[0]["enabled"])
            self.assertEqual(repo.get_cameras()[0]["name"], "Gate 2")

    def test_camera_rule_overrides_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            camera = repo.get_cameras()[0]
            camera["rule_overrides"] = {"1": {"trigger_classes": ["forklift"]}}
            repo.save_cameras([camera])
            saved = repo.get_cameras()[0]
            self.assertEqual(saved["rule_overrides"], {"1": {"trigger_classes": ["forklift"]}})

            manager = ConfigManager(repo)
            runtime_camera = manager.snapshot.cameras[0]
            rules = _rules_for_camera(manager.snapshot, runtime_camera)
            self.assertEqual(rules[0].params["trigger_classes"], ["forklift"])

            invalid = dict(saved)
            invalid["rule_overrides"] = {"1": {"min_confidence": 2}}
            with self.assertRaises(ValueError):
                repo.save_cameras([invalid])
            self.assertEqual(repo.get_cameras()[0]["rule_overrides"], {"1": {"trigger_classes": ["forklift"]}})

    def test_invalid_rule_params_and_graphs_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)

            with self.assertRaises(ValueError):
                repo.add_rule({
                    "id": 20, "name": "bad", "template": "generic_presence",
                    "models": ["detector"], "params": {"unknown": 1},
                })
            repo.create_template("graph_rule", {"label": "Graph", "logic": "graph", "params": []})
            source = {"id": "source", "type": "class_present", "params": {"classes": ["person"]}}
            alert = {"id": "alert", "type": "alert", "params": {}}
            valid_graph = {"nodes": [source, alert], "edges": [{"from": "source", "to": "alert"}]}
            rule = {
                "id": 20, "name": "graph", "template": "graph_rule",
                "models": ["detector"], "params": {}, "graph": valid_graph,
            }
            added, _ = repo.add_rule(rule)
            self.assertEqual(added.graph["nodes"][0]["id"], "source")

            invalid_graphs = [
                {"nodes": [{"id": "x", "type": "unknown", "params": {}}], "edges": []},
                {"nodes": [source, {"id": "source", "type": "alert", "params": {}}], "edges": []},
                {"nodes": [source, alert], "edges": [{"from": "source", "to": "missing"}]},
                {"nodes": [source, alert], "edges": [{"from": "source", "to": "alert"}, {"from": "source", "to": "alert"}]},
                {"nodes": [source, alert], "edges": []},
                {"nodes": [source, {"id": "not", "type": "not", "params": {}}, alert],
                 "edges": [{"from": "source", "to": "not"}, {"from": "not", "to": "source"}, {"from": "not", "to": "alert"}]},
                {"nodes": [source], "edges": []},
            ]
            for index, graph in enumerate(invalid_graphs, start=30):
                with self.subTest(index=index):
                    with self.assertRaises(ValueError):
                        repo.add_rule({**rule, "id": index, "graph": graph})
            self.assertIsNotNone(repo.get_rule_by_id(20))

    def test_snapshot_data_is_coherent_and_template_update_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            revision, settings, cameras, rules, templates = repo.read_snapshot_data()
            self.assertEqual(revision, repo.current_revision())
            self.assertEqual(cameras[0]["id"], "CAM_1")
            self.assertEqual(rules[0].id, 1)
            self.assertIn("generic_presence", templates)
            self.assertEqual(settings["model"]["models"][0]["name"], "detector")

            before = repo.get_templates()["generic_presence"]
            with self.assertRaises(ValueError):
                repo.update_template(
                    "generic_presence",
                    {"label": "Broken", "logic": "presence", "params": []},
                    expected_revision=before["revision"],
                )
            after_failed = repo.get_templates()["generic_presence"]
            self.assertEqual(after_failed["label"], before["label"])
            self.assertEqual(after_failed["revision"], before["revision"])

            updated = repo.update_template(
                "generic_presence",
                {"label": "Generic presence v2", "logic": "presence",
                 "params": [{"name": "trigger_classes", "type": "classes",
                             "default": [], "from_model": True}]},
                expected_revision=before["revision"],
            )
            self.assertEqual(updated["label"], "Generic presence v2")
            self.assertGreater(updated["revision"], before["revision"])

    def test_alert_snapshot_states_and_historical_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            alert_db = AlertDatabase(repo.get_settings(), database=db)

            available = root / "available" / "a.jpg"
            available.parent.mkdir()
            available.write_bytes(b"jpeg")
            cleaned = root / "cleaned" / "c.jpg"
            cleaned.parent.mkdir()
            cleaned.write_bytes(b"jpeg")
            missing = root / "missing" / "m.jpg"
            common = dict(camera_id="CAM_1", rule_id=1, rule_name="Test rule",
                          description="desc", confidence=0.9, severity=3, extra={})
            for offset, path in enumerate((available, cleaned, missing, None)):
                alert_db.insert_alert(Violation(timestamp=1700000000.0 + offset, **common), str(path) if path else None)
            self.assertEqual(alert_db.mark_snapshots_cleaned([cleaned.parent]), 1)
            statuses = {item["snapshot_status"] for item in alert_db.get_alerts()["items"]}
            self.assertEqual(statuses, {"available", "cleaned", "missing", "none"})

            camera = repo.get_cameras()[0]
            camera["name"] = "Renamed gate"
            repo.save_cameras([camera])
            repo.save_cameras([])
            items = alert_db.get_alerts()["items"]
            self.assertTrue(all(item["camera_name"] == "Gate" for item in items))
            self.assertTrue(all(item["rule_name"] == "Test rule" for item in items))

    def test_import_rejects_invalid_camera_reference_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            cameras = config / "cameras.yaml"
            cameras.write_text("cameras:\n  - id: CAM_1\n    name: Gate\n    rtsp_url: rtsp://127.0.0.1/live\n    rules: [999]\n", encoding="utf-8")
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                repo.import_yaml(config)
            with db.connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM camera_rules").fetchone()[0], 0)

    def test_runtime_model_sync_applies_registry_changes(self):
        class FakeModel:
            def __init__(self, path):
                self.model_path = path

        class FakeDetector:
            def __init__(self):
                self._detectors = {
                    "same": FakeModel("models/same.pt"),
                    "removed": FakeModel("models/removed.pt"),
                    "changed": FakeModel("models/old.pt"),
                }
                self.unloaded = []
                self.thresholds = []
                self.loaded = []

            @property
            def loaded_models(self):
                return list(self._detectors)

            def unload_model(self, name):
                self.unloaded.append(name)
                self._detectors.pop(name, None)

            def set_thresholds(self, name, confidence=None):
                self.thresholds.append((name, confidence))

            def load_model(self, name, path, confidence=None):
                self.loaded.append((name, path, confidence))
                self._detectors[name] = FakeModel(path)
                return True

        system = MachineVisionSystem.__new__(MachineVisionSystem)
        detector = FakeDetector()
        system._detector = detector
        snapshot = type("Snapshot", (), {
            "settings": {"model": {"models": [
                {"name": "same", "path": "models/same.pt", "enabled": True,
                 "confidence_override": 0.7},
                {"name": "changed", "path": "models/new.pt", "enabled": True},
                {"name": "new", "path": "models/new-model.pt", "enabled": True},
                {"name": "disabled", "path": "models/disabled.pt", "enabled": False},
            ]}}
        })()
        system._sync_models_from_snapshot(snapshot)
        self.assertEqual(detector.unloaded, ["removed", "changed"])
        self.assertEqual(detector.thresholds, [("same", 0.7)])
        self.assertEqual(detector.loaded, [
            ("changed", "models/new.pt", None),
            ("new", "models/new-model.pt", None),
        ])

    def test_runtime_camera_sync_adds_edits_and_removes_without_restart(self):
        class FakeStream:
            def __init__(self, config):
                self.config = config

        class FakeManager:
            def __init__(self):
                self._cameras = {
                    "OLD": FakeStream(CameraConfig(
                        id="OLD", name="Old", rtsp_url="rtsp://old", enabled=True, rules=[1]
                    )),
                    "EDIT": FakeStream(CameraConfig(
                        id="EDIT", name="Before", rtsp_url="rtsp://before", enabled=True, rules=[1]
                    )),
                }
                self.removed = []
                self.added = []

            def remove_camera(self, camera_id):
                self.removed.append(camera_id)
                self._cameras.pop(camera_id, None)

            def add_camera(self, config):
                self.added.append(config)
                self._cameras[config.id] = FakeStream(config)

        system = MachineVisionSystem.__new__(MachineVisionSystem)
        manager = FakeManager()
        system._camera_manager = manager
        system._running = True
        system._sync_cameras_from_snapshot([
            {"id": "EDIT", "name": "After", "rtsp_url": "rtsp://after", "enabled": True, "rules": [2]},
            {"id": "NEW", "name": "New", "rtsp_url": "rtsp://new", "enabled": True, "rules": [1]},
            {"id": "DISABLED", "name": "Disabled", "rtsp_url": "rtsp://disabled", "enabled": False, "rules": [1]},
        ])
        self.assertEqual(manager.removed, ["OLD", "EDIT"])
        self.assertEqual([item.id for item in manager.added], ["EDIT", "NEW"])
        self.assertNotIn("OLD", manager._cameras)
        self.assertNotIn("DISABLED", manager._cameras)
        self.assertEqual(manager._cameras["EDIT"].config.rtsp_url, "rtsp://after")
        self.assertEqual(manager._cameras["EDIT"].config.rules, [2])

    def test_password_hash_and_api_redaction(self):
        password_hash = hash_password("secret")
        self.assertTrue(is_password_hash(password_hash))
        self.assertTrue(verify_password("secret", password_hash))
        self.assertFalse(verify_password("wrong", password_hash))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            manager = ConfigManager(repo)
            state = RuntimeState(system=type("System", (), {
                "_config_manager": manager,
            })())
            public = state.get_settings()
            public_json = json.dumps(public, ensure_ascii=False)
            self.assertNotIn("sk-test-secret", public_json)
            self.assertNotIn("pbkdf2_sha256$", public_json)
            response = state.update_settings("llm", {"api_key": "sk-new-secret"})
            self.assertNotIn("sk-new-secret", json.dumps(response))
            self.assertNotIn("pbkdf2_sha256$", json.dumps(response))
            llm_revision = state.get_settings()["llm"]["revision"]
            state.update_settings("llm", {"api_key": ""}, expected_revision=llm_revision)
            self.assertEqual(repo.get_section("llm")["api_key"], "sk-new-secret")
            llm_revision = state.get_settings()["llm"]["revision"]
            state.update_settings("llm", {"clear_api_key": True}, expected_revision=llm_revision)
            self.assertEqual(repo.get_section("llm")["api_key"], "")
            self.assertTrue(is_password_hash(repo.get_section("panel")["password"]))
            with db.connection() as conn:
                audit_json = "\n".join(
                    (row[0] or "") + "\n" + (row[1] or "")
                    for row in conn.execute(
                        "SELECT before_json, after_json FROM config_audit_log"
                    ).fetchall()
                )
            self.assertNotIn("sk-test-secret", audit_json)
            self.assertNotIn("sk-new-secret", audit_json)
            self.assertNotIn("pbkdf2_sha256$", audit_json)

    def test_fastapi_login_cookie_and_basic_auth(self):
        class FakeState:
            def settings(self):
                return {"panel": {"auth_enabled": True, "username": "admin",
                                   "password": hash_password("secret")},
                        "snapshot": {"save_dir": "storage/test_results"}}

            def snapshots_dir(self):
                path = Path(tempfile.gettempdir()) / "machine-panel-test-snapshots"
                path.mkdir(parents=True, exist_ok=True)
                return path

            def get_settings(self):
                return {"panel": {"keys": []}}

            def pending_restart(self):
                return {}

        client = TestClient(create_app(FakeState()))
        self.assertEqual(client.get("/api/settings").status_code, 401)
        self.assertEqual(client.post("/api/login", json={"username": "admin", "password": "bad"}).status_code, 401)
        self.assertEqual(client.post("/api/login", json={"username": "admin", "password": "secret"}).status_code, 200)
        self.assertEqual(client.get("/api/settings").status_code, 200)
        basic = base64.b64encode(b"admin:secret").decode()
        self.assertEqual(client.get("/api/settings", headers={"Authorization": f"Basic {basic}"}).status_code, 200)


    def test_public_export_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db_path = root / "machine.db"
            db = MachineDatabase(db_path)
            repo = ConfigRepository(db)
            repo.import_yaml(config)

            exported = repo.export_public_config()
            exported_json = json.dumps(exported, ensure_ascii=False)
            self.assertNotIn("sk-test-secret", exported_json)
            self.assertNotIn("password", exported["settings"].get("panel", {}))
            self.assertIn("api_key_configured", exported["settings"]["llm"])
            self.assertIn("****", exported["cameras"][0]["rtsp_url"])

            backup = db.backup_to(root / "backup.db")
            self.assertTrue(MachineDatabase(backup).validate()["ok"])
            restored = root / "restored.db"
            MachineDatabase.restore_from(backup, restored)
            restored_db = MachineDatabase(restored)
            self.assertTrue(restored_db.validate()["ok"])
            with restored_db.connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 1)

    def test_manager_refreshes_external_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self.make_config(root)
            db = MachineDatabase(root / "machine.db")
            repo = ConfigRepository(db)
            repo.import_yaml(config)
            manager = ConfigManager(repo)
            old_revision = manager.snapshot.revision
            repo.update_section("alert", {"cooldown_seconds": 99})
            refreshed = manager.refresh_if_changed()
            self.assertGreater(refreshed.revision, old_revision)
            self.assertEqual(refreshed.settings["alert"]["cooldown_seconds"], 99)
            restarted = ConfigManager(repo)
            self.assertEqual(restarted.snapshot.revision, refreshed.revision)
            self.assertEqual(restarted.snapshot.settings["alert"]["cooldown_seconds"], 99)


if __name__ == "__main__":
    unittest.main()
