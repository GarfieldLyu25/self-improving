import unittest

from service import ROOT, build_session_launch


class SessionLaunchTests(unittest.TestCase):
    def test_restart_keeps_unbound_conversation_without_repeating_initial_prompt(self):
        saved = {'session_id': 'original-session', 'confirmed': True, 'project': None}
        session_id, resumed, arguments = build_session_launch(saved, None, 'interactive instructions')
        self.assertEqual(session_id, 'original-session')
        self.assertTrue(resumed)
        self.assertEqual(arguments[:2], ['--resume', 'original-session'])
        self.assertEqual(arguments[2:], ['--append-system-prompt', 'interactive instructions'])

    def test_restart_keeps_existing_project_conversation(self):
        project = ROOT / 'existing-project'
        saved = {'session_id': 'original-session', 'confirmed': True, 'project': str(project)}
        session_id, resumed, _ = build_session_launch(saved, project, 'instructions')
        self.assertEqual(session_id, 'original-session')
        self.assertTrue(resumed)

    def test_first_start_initializes_unbound_interactive_conversation(self):
        session_id, resumed, arguments = build_session_launch({}, None, 'custom instructions')
        self.assertFalse(resumed)
        self.assertEqual(arguments, ['--session-id', session_id, 'custom instructions'])

    def test_explicit_different_startup_project_gets_its_own_conversation(self):
        saved = {'session_id': 'original-session', 'confirmed': True, 'project': str(ROOT / 'old')}
        session_id, resumed, _ = build_session_launch(saved, ROOT / 'new', 'instructions')
        self.assertFalse(resumed)
        self.assertNotEqual(session_id, 'original-session')
