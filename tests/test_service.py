import json
import sys
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service import NativeState, handler_for, validate_task, load_initial_prompt, prepare_project


class ServiceTests(unittest.TestCase):
    def test_resumed_manual_session_is_ready_without_another_initialization_turn(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            state = NativeState(Path(folder), 'same-session', resumed=True)
            state.accept(b'manual conversation target')
            self.assertIsNone(state.next_task())
            state.on_event({'session_id': 'same-session', 'hook_event_name': 'SessionStart'})
            self.assertEqual(state.snapshot()['status'], 'ready')
            self.assertIsNone(state.snapshot()['project'])
            self.assertIsNotNone(state.next_task())

    def test_empty_workspace_becomes_independent_git_repository(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            project = Path(folder) / 'workspace'
            prepare_project(project)
            actual = subprocess.run(['git', '-C', str(project), 'rev-parse', '--show-toplevel'], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(Path(actual).resolve(), project.resolve())

    def test_existing_project_code_is_preserved_on_initialization(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            project = Path(folder) / 'workspace'
            project.mkdir()
            (project / 'app.py').write_text('print(42)', encoding='utf-8')
            prepare_project(project)
            prepare_project(project)
            self.assertEqual((project / 'app.py').read_text(encoding='utf-8'), 'print(42)')

    def test_clone_only_initializes_empty_workspace(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            source = Path(folder) / 'source.git'
            subprocess.run(['git', 'init', '--bare', str(source)], check=True, capture_output=True)
            project = Path(folder) / 'workspace'
            prepare_project(project, str(source))
            self.assertTrue((project / '.git').exists())
            (project / 'keep.txt').write_text('keep', encoding='utf-8')
            prepare_project(project, str(source))
            self.assertEqual((project / 'keep.txt').read_text(encoding='utf-8'), 'keep')
            blocked = Path(folder) / 'occupied'
            blocked.mkdir()
            (blocked / 'user.txt').write_text('data', encoding='utf-8')
            with self.assertRaises(ValueError):
                prepare_project(blocked, str(source))
            self.assertEqual((blocked / 'user.txt').read_text(encoding='utf-8'), 'data')

    def test_custom_initial_prompt_file_is_loaded_as_context(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path = Path(folder) / 'my-prompt.txt'
            path.write_bytes(b'\xef\xbb\xbf' + '项目自定义初始化上下文\n请等待任务。'.encode('utf-8'))
            self.assertEqual(load_initial_prompt(path), '项目自定义初始化上下文\n请等待任务。')

    def test_http_file_is_saved_exactly_and_automatically_dispatched(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            state = NativeState(Path(folder), 'same-session')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(state))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            body = '把 a+b 改成 a-b\n'.encode('utf-8')
            url = f'http://127.0.0.1:{server.server_port}/tasks'
            try:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(url, data=body, method='POST'))
                self.assertEqual(denied.exception.code, 403)
                with urlopen(Request(url, data=body, method='POST', headers={'X-CC-Token': state.token})) as response:
                    self.assertEqual(response.status, 202)
                    received = json.load(response)
                self.assertIsNone(state.next_task())
                state.on_event({'session_id': 'same-session', 'hook_event_name': 'Stop'})
                task = state.next_task()
                self.assertEqual(task['id'], received['task_id'])
                self.assertEqual(Path(task['file']).read_bytes(), body)
                self.assertEqual(task['status'], 'running')
                self.assertIsNone(state.next_task())
                state.on_event({'session_id': 'same-session', 'hook_event_name': 'Stop', 'last_assistant_message': '已修改'})
                self.assertEqual(task['status'], 'completed')
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_invalid_uploads_rejected(self):
        for data in (b'', b' ', b'\xff', b'x' * 65537):
            with self.assertRaises(ValueError):
                validate_task(data)


if __name__ == '__main__':
    unittest.main()
