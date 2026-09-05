import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from infrastructure.persistence import AlertDatabase, MachineDatabase
from webapp.api.alerts import router
from webapp.state import RuntimeState


class AlertBatchDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'snapshots'
        self.base.mkdir()
        self.machine = MachineDatabase(self.root / 'machine.db')
        self.db = AlertDatabase({}, database=self.machine)

    def image(self, name='one.jpg'):
        p = self.base / '2026-09-05' / 'rule' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'jpeg')
        return p

    def alert(self, path=None):
        with self.machine.transaction() as c:
            return c.execute("INSERT INTO alerts(camera_id,rule_id,rule_name,timestamp,snapshot_path) VALUES ('cam',1,'rule',1,?)", (str(path) if path else None,)).lastrowid

    def test_delete_removes_gallery_original_and_all_thumbnail_sizes(self):
        p = self.image()
        thumbs = [self.base / '.thumbs' / f'w{w}' / p.relative_to(self.base) for w in [420,960]]
        for t in thumbs:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(b'thumb')
        selected = self.alert(p)
        untouched = self.image('keep.jpg')
        keep = self.alert(untouched)
        result = self.db.delete_alerts([selected, selected, 999], self.base)
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['snapshots_deleted'], 1)
        self.assertFalse(result['cleanup_pending'])
        self.assertFalse(p.exists())
        self.assertTrue(all(not t.exists() for t in thumbs))
        self.assertTrue(untouched.exists())
        self.assertEqual([i['id'] for i in self.db.get_alerts()['items']], [keep])
        state = SimpleNamespace(snapshots_dir=lambda: self.base, _snapshot_day_dirs=RuntimeState._snapshot_day_dirs)
        gallery = RuntimeState.list_snapshots(state)
        self.assertEqual([f['name'] for f in gallery['files']], ['keep.jpg'])

    def test_shared_snapshot_preserved_until_last_reference_deleted(self):
        p = self.image(); a, b = self.alert(p), self.alert(p)
        self.assertEqual(self.db.delete_alerts([a], self.base)['shared_snapshots_kept'], 1)
        self.assertTrue(p.exists())
        self.db.delete_alerts([b], self.base)
        self.assertFalse(p.exists())

    def test_missing_and_no_snapshot_are_deletable_and_idempotent(self):
        ids = [self.alert(self.base / 'missing.jpg'), self.alert()]
        self.assertEqual(self.db.delete_alerts(ids, self.base)['deleted'], 2)
        self.assertEqual(self.db.delete_alerts(ids, self.base)['deleted'], 0)

    def test_outside_path_and_symlink_escape_reject_entire_batch(self):
        outside = self.root / 'secret.jpg'; outside.write_bytes(b'secret')
        for path in [outside, self.base / 'link.jpg']:
            if path != outside: path.symlink_to(outside)
            a = self.alert(path)
            with self.assertRaises(ValueError): self.db.delete_alerts([a], self.base)
            self.assertTrue(outside.exists())
            self.assertEqual(self.db.get_alert_count(), 1 if path == outside else 2)

    def test_filesystem_failure_restores_staged_files_and_records(self):
        p, q = self.image('a.jpg'), self.image('b.jpg')
        ids = [self.alert(p), self.alert(q)]
        real = os.replace
        def fail_second(src, dst):
            if src == q.resolve(): raise PermissionError('test')
            return real(src,dst)
        with patch('infrastructure.persistence.alert_database.os.replace', side_effect=fail_second):
            with self.assertRaises(PermissionError): self.db.delete_alerts(ids, self.base)
        self.assertTrue(p.exists() and q.exists())
        self.assertEqual(self.db.get_alert_count(), 2)

    def test_sql_failure_restores_files(self):
        p = self.image(); a = self.alert(p)
        with self.machine.transaction() as c:
            c.execute("CREATE TRIGGER prevent_delete BEFORE DELETE ON alerts BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(Exception): self.db.delete_alerts([a], self.base)
        self.assertTrue(p.exists())
        self.assertEqual(self.db.get_alert_count(), 1)

    def test_thumbnail_symlink_escape_does_not_delete_outside_file(self):
        p = self.image(); a = self.alert(p)
        outside = self.root / 'outside-cache'
        cache = outside / p.relative_to(self.base)
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b'private')
        (self.base / '.thumbs').mkdir()
        (self.base / '.thumbs' / 'w420').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): self.db.delete_alerts([a], self.base)
        self.assertTrue(cache.exists() and p.exists())
        self.assertEqual(self.db.get_alert_count(), 1)

    def test_cleanup_failure_is_reported_not_silently_successful(self):
        p = self.image(); a = self.alert(p)
        with patch('infrastructure.persistence.alert_database.shutil.rmtree', side_effect=PermissionError('test')):
            with self.assertLogs('database', level='ERROR'):
                result = self.db.delete_alerts([a], self.base)
        self.assertTrue(result['cleanup_pending'])
        self.assertFalse(p.exists())
        self.assertEqual(self.db.get_alert_count(), 0)

    def test_recent_snapshots_latest_three_skip_invalid_and_duplicate_images(self):
        # Records are ordered by timestamp, then ID, not filesystem modification time.
        oldest = self.alert(self.image('oldest.jpg'))
        second = self.alert(self.image('second.jpg'))
        shared_path = self.image('shared.jpg')
        self.alert(shared_path)
        shared = self.alert(shared_path)
        latest = self.alert(self.image('latest.jpg'))
        false_positive = self.alert(self.image('false.jpg'))
        cleaned = self.alert(self.image('cleaned.jpg'))
        self.alert(self.base / 'missing.jpg')
        outside = self.root / 'outside.jpg'; outside.write_bytes(b'private')
        self.alert(outside)
        self.db.update_alert_status(false_positive, 'false_positive')
        with self.machine.transaction() as c:
            c.execute('UPDATE alerts SET snapshot_cleaned_at=1 WHERE id=?', (cleaned,))
        result = self.db.get_recent_snapshot_alerts(self.base)
        self.assertEqual([x['id'] for x in result], [latest,shared,second])
        self.assertNotIn(oldest, [x['id'] for x in result])

    def test_recent_snapshot_api_empty_and_encoded_url(self):
        app = FastAPI(); app.include_router(router)
        app.state.state = SimpleNamespace(db=self.db, snapshots_dir=lambda:self.base)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/alerts/recent-snapshots').json(), {'items':[]})
            self.alert(self.image('违规 快照.jpg'))
            result = client.get('/api/alerts/recent-snapshots').json()['items']
            self.assertEqual(len(result),1)
            self.assertTrue(result[0]['snapshot_url'].startswith('/snapshots/'))
            self.assertIn('%20', result[0]['snapshot_url'])
            self.assertNotIn(str(self.base), result[0]['snapshot_url'])

    def test_strict_validation_and_api(self):
        app = FastAPI(); app.include_router(router)
        app.state.state = SimpleNamespace(db=self.db, snapshots_dir=lambda:self.base)
        with TestClient(app) as client:
            for ids in [None, [], [True], ['1'], [-1], [1.0], list(range(1,502))]:
                self.assertEqual(client.post('/api/alerts/batch-delete', json={'ids':ids}).status_code,400)
            a = self.alert()
            result = client.post('/api/alerts/batch-delete', json={'ids':[a]})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['deleted'],1)

if __name__ == '__main__': unittest.main()
