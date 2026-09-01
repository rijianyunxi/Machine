"""
Machine Vision Unsafe Behavior Detection System

Main entry point. Connects to cameras, runs detection and analysis,
captures snapshots when violations are detected.

Active rules:
    Rule  1 - No safety helmet
    Rule 13 - Smoking in no-fire zone

Usage:
    python main.py                    # Run with default config
    python main.py                    # Runtime config comes from storage/machine.db
"""

import copy
import signal
import sys
import time
from pathlib import Path

# Ensure project root is in Python path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from core.analyzer import BehaviorAnalyzer
from core.capture import CameraManager, CameraConfig
from core.detector import DetectionError, MultiDetector
from core.snapshot import SnapshotManager
from infrastructure.persistence import AlertDatabase, ConfigRepository, MachineDatabase
from application.config_manager import ConfigManager
from utils.logger import setup_logger
from rules.definitions import validate_rule_params


def _rules_for_camera(snapshot, cam_cfg):
    """Build the active rules for one camera from one immutable snapshot.

    Camera-level overrides are partial and are merged into a deep copy of the
    rule.  Validation happens again after the merge so an invalid override can
    never reach the analyzer.
    """
    active_ids = {int(value) for value in (cam_cfg.get("rules", []) or [])}
    raw_overrides = cam_cfg.get("rule_overrides", {}) or {}
    if not isinstance(raw_overrides, dict):
        raise ValueError(f"摄像头 {cam_cfg.get('id', '')} 的 rule_overrides 必须是对象")
    result = []
    for rule in snapshot.rules:
        if not rule.enabled or rule.id not in active_ids:
            continue
        template = snapshot.templates.get(rule.template)
        if not isinstance(template, dict):
            raise ValueError(f"规则 {rule.id} 引用了不存在的模板: {rule.template}")
        override = raw_overrides.get(str(rule.id), raw_overrides.get(rule.id, {})) or {}
        if not isinstance(override, dict):
            raise ValueError(f"摄像头 {cam_cfg.get('id', '')} 的规则 {rule.id} 覆盖必须是对象")
        merged = copy.deepcopy(rule)
        merged.params = validate_rule_params(
            template, {**(rule.params or {}), **copy.deepcopy(override)}
        )
        result.append(merged)
    return result


class MachineVisionSystem:
    """
    Main system orchestrator.

    Coordinates:
    1. Camera stream capture (multi-threaded)
    2. YOLOv8 object detection (multi-model)
    3. Behavior analysis (rules 1 and 13)
    4. Snapshot capture and alert storage
    """

    def __init__(self):
        self._running = False
        self._shutdown = False

        # Runtime configuration is read only from the unified machine.db.
        self._database = MachineDatabase(PROJECT_ROOT / "storage" / "machine.db")
        self._config_repository = ConfigRepository(self._database)
        self._config_manager = ConfigManager(self._config_repository)
        snapshot = self._config_manager.snapshot
        self._settings = snapshot.settings
        self._cameras_config = {"cameras": list(snapshot.cameras)}
        analyzer = getattr(self, "_analyzer", None)
        if analyzer is not None:
            analyzer.set_templates(snapshot.templates)
        self._config_manager.subscribe(self._on_config_snapshot)

        # Setup logging
        log_cfg = self._settings.get("logging", {})
        self._logger = setup_logger(
            name="machine_vision",
            level=log_cfg.get("level", "INFO"),
            log_file=log_cfg.get("file", ""),
            max_size_mb=log_cfg.get("max_size_mb", 50),
            backup_count=log_cfg.get("backup_count", 5),
        )

        self._logger.info("=" * 60)
        self._logger.info("Machine Vision Unsafe Behavior Detection System")
        self._logger.info("=" * 60)

        # Camera manager
        self._camera_manager = CameraManager(self._settings)

        # Detection models (multi-model)
        self._logger.info("Loading detection models...")
        self._detector = MultiDetector(self._settings)
        self._logger.info(f"Active models: {self._detector.loaded_models}")

        # Behavior analyzer uses the same immutable rule snapshot as the web panel.
        self._analyzer = BehaviorAnalyzer(
            self._settings,
            templates=snapshot.templates,
        )

        # Snapshot manager
        self._snapshot_manager = SnapshotManager(self._settings)

        # Database
        self._db = AlertDatabase(self._settings, database=self._database)

        # Statistics
        self._stats = {
            "frames_processed": 0,
            "violations_detected": 0,
            "snapshots_saved": 0,
            "start_time": 0,
        }
        self._next_stats_time = 0.0
        self._next_config_poll = 0.0

    def _on_config_snapshot(self, snapshot):
        """Publish a committed snapshot and reconcile live camera streams."""
        self._settings = snapshot.settings
        self._cameras_config = {"cameras": list(snapshot.cameras)}
        analyzer = getattr(self, "_analyzer", None)
        if analyzer is not None:
            analyzer.set_templates(snapshot.templates)
        self._sync_models_from_snapshot(snapshot)
        if getattr(self, "_running", False):
            self._sync_cameras_from_snapshot(snapshot.cameras)

    def _sync_models_from_snapshot(self, snapshot):
        """Reconcile loaded detectors with the committed model registry.

        Model registry writes are published through the same snapshot listener
        as cameras and rules.  A changed model is loaded into the detector
        registry before the next processing round; a failed reload leaves no
        stale detector for a changed path and is reported by the detector.
        """
        detector = getattr(self, "_detector", None)
        if detector is None:
            return
        model_settings = snapshot.settings.get("model", {}) or {}
        desired = {
            str(item.get("name", "")).strip(): item
            for item in (model_settings.get("models", []) or [])
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
        loaded = set(detector.loaded_models)
        for name in loaded - {
            name for name, item in desired.items() if item.get("enabled", True)
        }:
            detector.unload_model(name)

        for name, item in desired.items():
            if not item.get("enabled", True):
                continue
            path = str(item.get("path", "")).strip()
            if not path:
                detector.unload_model(name)
                continue
            current = getattr(detector, "_detectors", {}).get(name)
            confidence = item.get("confidence_override")
            if current is not None:
                configured_path = (PROJECT_ROOT / path).resolve()
                current_path = Path(str(getattr(current, "model_path", ""))).resolve()
                same_path = configured_path == current_path
                if same_path:
                    detector.set_thresholds(name, confidence=confidence)
                    continue
                # Do not let a changed registry entry keep using the old file.
                detector.unload_model(name)
            detector.load_model(name, path, confidence=confidence)

    def _sync_cameras_from_snapshot(self, cameras):
        """Apply camera additions, removals, and edits without a restart."""
        manager = getattr(self, "_camera_manager", None)
        if manager is None:
            return
        desired = {
            str(cfg.get("id")): cfg for cfg in cameras
            if cfg.get("id") and cfg.get("enabled", True)
        }
        for camera_id in list(manager._cameras):
            if camera_id not in desired:
                manager.remove_camera(camera_id)
        for camera_id, cfg in desired.items():
            running = manager._cameras.get(camera_id)
            unchanged = (
                running is not None
                and running.config.name == cfg.get("name", camera_id)
                and running.config.rtsp_url == cfg.get("rtsp_url", "")
                and list(running.config.rules) == list(cfg.get("rules", []))
            )
            if unchanged:
                continue
            if running is not None:
                manager.remove_camera(camera_id)
            manager.add_camera(CameraConfig(
                id=camera_id,
                name=cfg.get("name", camera_id),
                rtsp_url=cfg.get("rtsp_url", ""),
                enabled=True,
                rules=list(cfg.get("rules", [])),
            ))

    def start(self):
        """Start the detection system."""
        self._running = True
        self._stats["start_time"] = time.time()
        self._next_stats_time = self._stats["start_time"] + 60

        # Web panel (daemon thread; failure must not take detection down)
        try:
            from webapp.server import PanelServer

            self._panel = PanelServer(system=self)
            self._panel.start()
        except Exception as e:
            self._logger.error(f"Web panel failed to start: {e}")
            self._panel = None

        # Print active rules
        all_rules = list(self._config_manager.snapshot.rules)
        self._logger.info(f"Active rules: {len(all_rules)}")
        for rule in all_rules:
            self._logger.info(
                f"  R{rule.id:02d}: {rule.name} [{rule.category}] "
                f"severity={rule.severity}"
            )

        # Start cameras
        cameras = self._cameras_config.get("cameras", [])
        enabled_count = sum(1 for c in cameras if c.get("enabled", True))
        self._logger.info(
            f"Starting {enabled_count} enabled cameras "
            f"(total configured: {len(cameras)})"
        )

        if not cameras:
            self._logger.warning(
                "No cameras configured! Add cameras from the web panel or config import."
            )
            self._logger.info("Running in demo mode - waiting for cameras.")
        else:
            self._camera_manager.start_all(cameras)

            # Wait for cameras to connect
            self._logger.info("Waiting for camera connections...")
            time.sleep(3)

            # Print connection status
            status = self._camera_manager.get_status()
            connected = sum(1 for s in status.values() if s["connected"])
            self._logger.info(
                f"Camera connection status: {connected}/{len(status)} connected"
            )

        # Main processing loop also handles cameras added after startup.
        self._processing_loop()

    def _processing_loop(self):
        """Main loop: capture frames -> detect -> analyze -> snapshot."""
        self._logger.info("Starting main processing loop...")
        self._logger.info("Press Ctrl+C to stop")
        self._logger.info("-" * 60)

        while self._running:
            loop_start = time.time()

            # Embedded writes publish immediately; this low-frequency poll also
            # picks up commits made by a separate panel process.
            if time.time() >= self._next_config_poll:
                try:
                    self._config_manager.refresh_if_changed()
                except Exception as exc:
                    self._logger.error(f"Configuration refresh failed; keeping old snapshot: {exc}")
                self._next_config_poll = time.time() + 1.0

            # One coherent snapshot per processing round. No database/YAML
            # reads occur in the frame loop.
            snapshot = self._config_manager.snapshot
            settings = snapshot.settings
            cameras = snapshot.cameras
            target_fps = settings.get("capture", {}).get("target_fps", 2)
            frame_interval = 1.0 / target_fps if target_fps > 0 else 0.5
            stale_after = max(5.0, 3 * frame_interval)

            for cam_cfg in cameras:
                if not self._running:
                    break

                cam_id = cam_cfg["id"]
                if not cam_cfg.get("enabled", True):
                    continue

                # Resolve and validate camera-specific rules from this one
                # snapshot.  An invalid persisted configuration must not crash
                # the whole worker or produce a false alert.
                try:
                    rule_defs = _rules_for_camera(snapshot, cam_cfg)
                except (TypeError, ValueError) as exc:
                    self._logger.error(
                        f"Invalid rule configuration for camera {cam_id}: {exc}"
                    )
                    continue
                if not rule_defs:
                    continue

                # Get latest frame
                frame_data = self._camera_manager.get_frame(cam_id)
                if frame_data is None:
                    continue
                if time.time() - frame_data.timestamp > stale_after:
                    continue  # stale frozen frame (reconnecting camera)
                self._stats["frames_processed"] += 1

                # Skip detection entirely while every rule is in cooldown
                if self._analyzer.all_in_cooldown(
                    cam_id, rule_defs, frame_data.timestamp
                ):
                    continue

                # Only run models required by this camera's active rules:
                # bound models + any loaded model that supplies the rule's
                # cross-model classes (e.g. Person from the PPE model).
                models_needed = {m for r in rule_defs for m in r.models}
                person_classes = {
                    c for r in rule_defs
                    for c in (r.params or {}).get("person_classes", [])
                }
                if person_classes:
                    models_needed.update(
                        self._detector.models_providing(person_classes)
                    )
                try:
                    detections = self._detector.detect_all(
                        frame_data.frame, model_names=sorted(models_needed) or None
                    )
                except DetectionError as exc:
                    # A failed inference is not an empty detection result:
                    # skip this frame so absence rules cannot raise false alerts.
                    self._logger.error(
                        f"Detection unavailable for camera {cam_id}: {exc}"
                    )
                    continue

                # Analyze for violations
                h, w = frame_data.frame.shape[:2]
                violations = self._analyzer.analyze_frame(
                    cam_id, rule_defs, detections, frame_data.timestamp,
                    frame_size=(w, h),
                )

                # Process violations
                for violation in violations:
                    self._stats["violations_detected"] += 1

                    # Save snapshot
                    snapshot_path = self._snapshot_manager.save_snapshot(
                        frame_data.frame, violation, detections
                    )

                    if snapshot_path:
                        self._stats["snapshots_saved"] += 1

                    # Save to database
                    alert_id = self._db.insert_alert(violation, snapshot_path)

                    self._logger.warning(
                        f"VIOLATION [{cam_id}] R{violation.rule_id:02d} "
                        f"{violation.rule_name} "
                        f"(conf={violation.confidence:.2f}) "
                        f"-> Alert #{alert_id}, Snapshot: {snapshot_path}"
                    )

            # Maintain target frame rate
            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

            # Print stats once per minute
            now = time.time()
            if now >= self._next_stats_time:
                self._print_stats()
                self._next_stats_time = now + 60


    def _print_stats(self):
        """Print processing statistics."""
        uptime = time.time() - self._stats["start_time"]
        fps = self._stats["frames_processed"] / uptime if uptime > 0 else 0
        self._logger.info(
            f"Stats: frames={self._stats['frames_processed']}, "
            f"violations={self._stats['violations_detected']}, "
            f"snapshots={self._stats['snapshots_saved']}, "
            f"uptime={uptime:.0f}s, avg_fps={fps:.1f}"
        )

    def stop(self):
        """Stop the detection system (idempotent)."""
        if self._shutdown:
            return
        self._shutdown = True
        self._running = False
        self._logger.info("Shutting down...")

        panel = getattr(self, "_panel", None)
        if panel is not None:
            try:
                panel.stop()
            except Exception:
                pass

        self._camera_manager.stop_all()
        self._print_stats()

        total_alerts = self._db.get_alert_count()
        self._logger.info(f"Total alerts in database: {total_alerts}")
        self._logger.info("System stopped.")


def main():
    # Normal mode
    try:
        system = MachineVisionSystem()
    except Exception as e:
        print(f"[FATAL] Failed to initialize: {e}", file=sys.stderr)
        sys.exit(1)

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print()
        system.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        system.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        system._logger.error(f"Fatal error: {e}", exc_info=True)
        system.stop()
        sys.exit(1)
    finally:
        system.stop()


if __name__ == "__main__":
    main()
