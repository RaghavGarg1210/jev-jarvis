import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from jarvis.actions import ActionError, ActionExecutor, load_config
from jarvis.engine import Engine
from jarvis.planner import Planner, PlanError
from jarvis.providers import Decider


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config()
        self.actions = ActionExecutor(self.root, self.config, False)
        self.planner = Planner(self.config, Decider())
        self.engine = Engine(self.planner, self.actions, self.root)

    def test_rehearsal_has_receipt_without_native_effect(self):
        with patch('jarvis.actions.subprocess.run') as run:
            plan = self.engine.plan('open Safari')
            receipt = self.engine.execute(plan['id'])
        run.assert_not_called()
        self.assertEqual(receipt['status'], 'simulated')
        self.assertFalse(receipt['can_undo'])
        self.assertEqual(len(json.loads((self.root/'history.json').read_text())), 1)

    def test_confirmation_does_not_trust_returned_plan(self):
        plan = self.engine.plan('open Safari')
        plan['actions'][0]['args']['app'] = 'Terminal'
        receipt = self.engine.execute(plan['id'])
        self.assertEqual(receipt['steps'][0]['title'], 'Open Safari')
        with self.assertRaises(PlanError):
            self.engine.execute(plan['id'])

    def test_expired_and_cancelled_never_execute(self):
        plan = self.engine.plan('open Safari')
        self.engine.plans[plan['id']]['expires_at'] = time.time()-1
        with self.assertRaises(PlanError):
            self.engine.execute(plan['id'])
        plan = self.engine.plan('open Safari')
        self.engine.cancel(plan['id'])
        with self.assertRaises(PlanError):
            self.engine.execute(plan['id'])
        self.assertEqual(self.engine.snapshot()['history'], [])

    def test_concurrent_confirmation_executes_only_once(self):
        plan = self.engine.plan('open Safari')
        results = []
        def confirm():
            try:
                results.append(self.engine.execute(plan['id'])['status'])
            except PlanError:
                results.append('rejected')
        with patch.object(self.actions, 'execute', return_value={'status':'simulated','summary':'Opened'}) as execute:
            threads = [threading.Thread(target=confirm) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(execute.call_count, 1)
        self.assertCountEqual(results, ['simulated', 'rejected'])

    def test_partial_plan_stops_at_first_failure(self):
        plan = self.engine.plan('routine focus')
        with patch.object(self.actions, 'execute', side_effect=[{'status':'done','summary':'Opened'}, ActionError('App failed')]):
            receipt = self.engine.execute(plan['id'])
        self.assertEqual(receipt['status'], 'partial')
        self.assertEqual(receipt['steps'][-1]['status'], 'failed')

    def test_full_validation_happens_before_any_action(self):
        with patch.object(self.planner, 'plan', return_value=([
            {'kind':'open_app','args':{'app':'Safari'}}, {'kind':'shell','args':{'cmd':'date'}}
        ], {'provider':'rules'})), patch.object(self.actions, 'execute') as execute:
            with self.assertRaises(ActionError):
                self.engine.plan('test')
        execute.assert_not_called()

    def test_local_note_undo_preserves_other_effects(self):
        self.actions.live = True
        plan = self.engine.plan('note Test: Keep this text local')
        receipt = self.engine.execute(plan['id'])
        self.assertTrue(receipt['can_undo'])
        self.assertNotIn('undo', receipt['steps'][0])
        path = Path(receipt['steps'][0]['path'])
        self.assertTrue(path.exists())
        self.assertNotIn('Keep this text local', (self.root/'history.json').read_text())
        result = self.engine.undo(receipt['id'])
        self.assertEqual(result['status'], 'undone')
        self.assertFalse(path.exists())

    def test_rehearsal_cannot_erase_live_undo_metadata(self):
        self.actions.live = True
        receipt = self.engine.execute(self.engine.plan('note Test: hello')['id'])
        self.actions.live = False
        with self.assertRaises(PlanError):
            self.engine.undo(receipt['id'])
        self.assertFalse(self.engine.snapshot()['history'][0]['can_undo'])
        self.assertTrue(self.engine.history[0]['can_undo'])

    def test_timer_cancel_removes_active_timer(self):
        self.actions.live = True
        receipt = self.engine.execute(self.engine.plan('timer for 5 minutes')['id'])
        self.assertEqual(len(self.engine.snapshot()['timers']), 1)
        self.engine.undo(receipt['id'])
        self.assertEqual(self.engine.snapshot()['timers'], [])

    def test_journal_failure_prevents_effect(self):
        plan = self.engine.plan('open Safari')
        with patch.object(self.engine, '_save', side_effect=OSError('disk full')), patch.object(self.actions, 'execute') as execute:
            with self.assertRaises(OSError):
                self.engine.execute(plan['id'])
        execute.assert_not_called()

    def test_interrupted_run_is_not_claimed_done(self):
        (self.root/'history.json').write_text(json.dumps([{'id':'x','status':'running','steps':[]}]))
        engine = Engine(self.planner, self.actions, self.root)
        self.assertEqual(engine.snapshot()['history'][0]['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
