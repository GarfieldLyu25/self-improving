import unittest
from service import ROOT, resolve_project_path


class ProjectPathTests(unittest.TestCase):
    def test_restart_reuses_configured_project(self):
        project = ROOT / 'existing-project'
        self.assertEqual(resolve_project_path(saved_project=project), project)

    def test_first_start_allows_selecting_repository_in_conversation(self):
        self.assertIsNone(resolve_project_path())

    def test_clone_uses_repository_name(self):
        for repository in ('https://example.com/team/my-project.git',
                           'git@example.com:team/my-project.git',
                           'C:\\repos\\my-project.git',
                           'https://example.com/team/my-project.git/'):
            with self.subTest(repository=repository):
                self.assertEqual(resolve_project_path(repository=repository), ROOT / 'my-project')

    def test_explicit_path_overrides_repository_name(self):
        project = ROOT / 'custom-project'
        self.assertEqual(resolve_project_path(project, 'https://example.com/other.git'), project)

    def test_repository_without_directory_name_requires_explicit_path(self):
        for repository in ('https://example.com', 'https://example.com/..',
                           'https://example.com/.git'):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                resolve_project_path(repository=repository)
