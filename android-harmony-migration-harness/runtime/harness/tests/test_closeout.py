import copy
import io
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

import test_engine as fixtures
from closeout import record, state, same_payload
from continuation import advice
import foundation as base
from handoff import write_handoff
from test_handoff import example


class ScopedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.e = self.case.execute_suite()
        self.document = self.case.root / 'closeout.html'
        self.document.write_text('<html>Reviewed limited scope, evidence and exclusions</html>')
        all_ids = list(self.e.acceptance)
        self.value = {'action': 'prepare', 'build_task_id': 'build',
            'authorization': {'actor_type': 'human', 'instruction': 'Only main paths and selected mocks', 'evidence': [str(self.document)]},
            'checks': [{'id': 'main', 'status': 'passed', 'summary': 'Selected main path verified',
                        'acceptance_ids': all_ids[:1], 'evidence': [str(self.document)]}],
            'excluded_acceptance_ids': all_ids[1:], 'exclusion_reason': 'Explicit human scope limit',
            'agent_review': {'findings': [], 'summary': 'Code and evidence reviewed', 'evidence': [str(self.document)]},
            'cleanup': 'Test module removed, installed package retained', 'limitations': ['Not full acceptance'],
            'candidate': self.e.receipt('build')['package']['path'],
            'report': write_handoff(self.e, example())['html_path']}

    def test_preparation_preserves_core_gates_and_requires_final_decision(self):
        before = (self.e.root / 'harness-state.json').read_bytes()
        coverage = self.e.status()['coverage']
        result = record(self.e, 'prepare', self.value)
        self.assertEqual(result['status'], 'awaiting_human')
        self.assertEqual(advice(self.e)['next_action']['action'], 'scoped_final_review')
        self.assertEqual(self.e.status()['coverage'], coverage)
        self.assertEqual((self.e.root / 'harness-state.json').read_bytes(), before)
        self.assertNotEqual(self.e.verify()['status'], 'delivery_ready')

    def test_missing_authorization_incomplete_checks_and_hidden_exclusions_rejected(self):
        for field, replacement in [('authorization', {}), ('checks', []), ('excluded_acceptance_ids', []),
                                   ('agent_review', {'findings': ['unfinished']}), ('cleanup', '')]:
            value = copy.deepcopy(self.value)
            value[field] = replacement
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                record(self.e, 'bad-'+field, value)
        self.assertIsNone(state(self.e))

    def test_evidence_and_source_changes_reopen_agent_work(self):
        record(self.e, 'prepare', self.value)
        self.document.write_text('changed')
        self.assertEqual(state(self.e)['status'], 'stale')
        self.assertFalse(advice(self.e)['can_end_turn'])
        self.document.write_text('<html>Reviewed limited scope, evidence and exclusions</html>')
        record(self.e, 'prepare-new', self.value)
        source = next(iter((self.case.root / 'harmony').rglob('*.ets')), None)
        if source is None:
            source = next(p for p in (self.case.root / 'harmony').rglob('*') if p.is_file())
        source.write_text(source.read_text() + '\nchanged')
        self.assertEqual(state(self.e)['status'], 'stale')

    def test_wrong_package_and_nonhuman_or_stale_decision_rejected(self):
        wrong = self.case.root / 'wrong.app'
        wrong.write_text('wrong package')
        value = {**self.value, 'candidate': str(wrong)}
        with self.assertRaisesRegex(ValueError, 'package'):
            record(self.e, 'wrong', value)
        result = record(self.e, 'prepare', self.value)
        decision = {'action': 'decide', 'actor_type': 'agent', 'reviewer': 'user', 'summary': 'approved',
                    'input_digest': result['input_digest'], 'decision': 'approved', 'evidence': [str(self.document)]}
        with self.assertRaisesRegex(ValueError, 'human'):
            record(self.e, 'agent-decision', decision)
        decision.update(actor_type='demo', input_digest='stale')
        with self.assertRaisesRegex(ValueError, 'digest'):
            record(self.e, 'stale-decision', decision)
        decision['input_digest'] = result['input_digest']
        self.assertEqual(record(self.e, 'decision', decision)['status'], 'demo_scoped_complete')
        self.assertNotEqual(self.e.verify()['status'], 'delivery_ready')

    def test_idempotence_conflicts_and_cli_preserve_core_status(self):
        first = record(self.e, 'once', self.value)
        self.assertEqual(record(self.e, 'once', self.value), first)
        with self.assertRaisesRegex(ValueError, 'different input'):
            record(self.e, 'once', {**self.value, 'cleanup': 'different'})
        result = subprocess.run([sys.executable, str(fixtures.ROOT / 'migration.py'), 'verify', '--run-dir', str(self.e.root)],
                                capture_output=True, text=True)
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['delivery']['status'], 'awaiting_human')
        self.assertTrue(data['continuation']['can_end_turn'])

    def test_equivalence_compares_all_module_entries(self):
        def package(name, content, comment=b''):
            module = io.BytesIO()
            with zipfile.ZipFile(module, 'w') as archive:
                archive.writestr('ets/modules.abc', content)
                archive.comment = comment
            target = self.case.root / name
            with zipfile.ZipFile(target, 'w') as archive:
                archive.writestr('entry.hap', module.getvalue())
            return base.file_record(target)
        a = package('a.app', b'code')
        b = package('b.app', b'code', b'metadata')
        c = package('c.app', b'changed')
        self.assertTrue(same_payload(a, b))
        self.assertFalse(same_payload(a, c))

    def test_arbitrary_report_cannot_replace_developer_handoff(self):
        with self.assertRaisesRegex(ValueError, 'developer handoff'):
            record(self.e, 'arbitrary-report', {**self.value, 'report': str(self.document)})

    def test_changed_developer_document_requires_scoped_delivery_rebinding(self):
        record(self.e, 'prepare', self.value)
        content = {**example(), 'title': 'Updated developer handoff'}
        updated = write_handoff(self.e, content)
        self.assertFalse(advice(self.e)['can_end_turn'])
        self.assertEqual(advice(self.e)['next_action']['action'], 'prepare_scoped_delivery')
        value = {**self.value, 'report': updated['html_path']}
        self.assertEqual(record(self.e, 'rebind', value)['status'], 'awaiting_human')
        self.assertEqual(advice(self.e)['next_action']['action'], 'scoped_final_review')


if __name__ == '__main__':
    unittest.main()
