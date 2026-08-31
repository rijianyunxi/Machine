"""
Machine Vision Unsafe Behavior Detection System

Main entry point. Connects to cameras, runs detection and analysis,
captures snapshots when violations are detected.

Active rules:
    Rule  1 - No safety helmet
    Rule 13 - Smoking in no-fire zone

Usage:
    python main.py                    # Run with default config
    python main.py --config path/     # Run with custom config directory
    python main.py --test camera_url  # Test single camera
"""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import yaml

# Ensure project root is in Python path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from core.analyzer import BehaviorAnalyzer
from core.capture import CameraManager, CameraConfig
from core.detector import MultiDetector
from core.snapshot import SnapshotManager
from rules.rules_engine import get_all_rules, get_rules_store
from infrastructure.persistence import AlertDatabase
from utils.logger import setup_logger


class MachineVisionSystem:
    """
    Main system orchestrator.

    Coordinates:
    1. Camera stream capture (multi-threaded)
    2. YOLOv8 object detection (multi-model)
    3. Behavior analysis (rules 1 and 13)
    4. Snapshot capture and alert storage
    """

    def __init__(self, config_dir: str = "config"):
        self._running = False
        self._shutdown = False
        self._config_dir = config_dir

        # Load configuration (fail fast on missing/invalid files)
        self._settings = self._load_config("settings.yaml")
        self._cameras_config = self._load_config("cameras.yaml")

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

        # Behavior analyzer (shares the rules store with the web panel)
        self._analyzer = BehaviorAnalyzer(self._settings,
                                          config_dir=self._config_dir)

        # Snapshot manager
        self._snapshot_manager = SnapshotManager(self._settings)

        # Database
        self._db = AlertDatabase(self._settings)

        # Statistics
        self._stats = {
            "frames_processed": 0,
            "violations_detected": 0,
            "snapshots_saved": 0,
            "start_time": 0,
        }
        self._next_stats_time = 0.0

    def _load_config(self, filename: str) -> dict:
        """Load a YAML configuration file, failing fast if missing/invalid."""
        filepath = os.path.join(self._config_dir, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Config file not found: {filepath}")
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Invalid config format (expected YAML map): {filepath}")
        return data

    def start(self):
        """Start the detection system."""
        self._running = True
        self._stats["start_time"] = time.time()
        self._next_stats_time = self._stats["start_time"] + 60

        # Web panel (daemon thread; failure must not take detection down)
        try:
            from webapp.server import PanelServer

            self._panel = PanelServer(system=self, config_dir=self._config_dir)
            self._panel.start()
        except Exception as e:
            self._logger.error(f"Web panel failed to start: {e}")
            self._panel = None

        # Print active rules
        all_rules = get_all_rules(self._config_dir)
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
                "No cameras configured! Edit config/cameras.yaml to add cameras."
            )
            self._logger.info("Running in demo mode - system idle.")
            self._idle_loop()
            return

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

        # Main processing loop
        self._processing_loop()

    def _processing_loop(self):
        """Main loop: capture frames -> detect -> analyze -> snapshot."""
        self._logger.info("Starting main processing loop...")
        self._logger.info("Press Ctrl+C to stop")
        self._logger.info("-" * 60)

        target_fps = self._settings.get("capture", {}).get("target_fps", 2)
        frame_interval = 1.0 / target_fps if target_fps > 0 else 0.5
        stale_after = max(5.0, 3 * frame_interval)

        rules_store = get_rules_store(self._config_dir)

        while self._running:
            loop_start = time.time()

            # Re-read each iteration so panel config edits hot-apply without restart
            cameras = self._cameras_config.get("cameras", [])

            for cam_cfg in cameras:
                if not self._running:
                    break

                cam_id = cam_cfg["id"]
                if not cam_cfg.get("enabled", True):
                    continue

                # Resolve enabled rules for this camera (rules.yaml driven)
                rule_defs = rules_store.get_rules_for_camera(cam_cfg.get("rules", []))
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
                detections = self._detector.detect_all(
                    frame_data.frame, model_names=sorted(models_needed) or None
                )

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

    def _idle_loop(self):
        """Idle loop when no cameras are configured."""
        while self._running:
            time.sleep(1)

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
    parser = argparse.ArgumentParser(
        description="Machine Vision Unsafe Behavior Detection System"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config",
        help="Path to configuration directory (default: config/)",
    )
    parser.add_argument(
        "--test",
        type=str,
        metavar="RTSP_URL",
        help="Test single camera connectivity",
    )

    args = parser.parse_args()

    # Test mode
    if args.test:
        from scripts.test_camera import test_camera

        success = test_camera(args.test)
        sys.exit(0 if success else 1)

    # Normal mode
    try:
        system = MachineVisionSystem(config_dir=args.config)
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
