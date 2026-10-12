"""HTTP upload -> one actual interactive claude.exe in a visible terminal."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from native_console import ConsoleTaskWriter, launch_console

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 65536


def resolve_project_path(project=None, repository=None, saved_project=None):
    """Use the requested project, repository name, or last configured directory."""
    if project is not None:
        return Path(project).resolve()
    if not repository:
        if saved_project:
            return Path(saved_project).resolve()
        return None
    path = urlsplit(repository).path if '://' in repository else repository
    name = path.rstrip('/\\').replace('\\', '/').rsplit('/', 1)[-1].rsplit(':', 1)[-1]
    if name.endswith('.git'):
        name = name[:-4]
    if not name or name in ('.', '..') or name.endswith(('.', ' ')) or any(
            ord(char) < 32 or char in '<>:"/\\|?*' for char in name):
        raise ValueError('无法从仓库地址得到有效项目名，请用 --project 指定目录')
    return ROOT / name


def build_session_launch(saved_session, project, context):
    """Resume the conversation without submitting the initial user prompt again."""
    saved_project = saved_session.get('project')
    same_project = (project is None and saved_project is None) or (
        project is not None and saved_project is not None
        and Path(saved_project).resolve() == project.resolve())
    resumed = bool(saved_session.get('confirmed') and saved_session.get('session_id') and same_project)
    if resumed:
        session_id = saved_session['session_id']
        return session_id, True, ['--resume', session_id, '--append-system-prompt', context]
    session_id = str(uuid.uuid4())
    return session_id, False, ['--session-id', session_id, context]


def prepare_project(project, repository=None):
    """Reuse an existing repository, clone into an empty path, or git-init local code."""
    if project is None:
        return ROOT
    if project.exists() and not project.is_dir():
        raise ValueError('项目路径不是目录')
    if project.is_dir():
        probe = subprocess.run(['git', '-C', str(project), 'rev-parse', '--show-toplevel'],
                               capture_output=True, text=True)
        if probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == project.resolve():
            return project
        if (project / '.git').exists():
            raise ValueError('目标项目已有 Git 元数据但无法读取，请检查仓库')
    if repository:
        if project.exists() and any(project.iterdir()):
            raise ValueError('目标项目目录已有代码，不能覆盖克隆；请指定空目录或复用现有项目')
        command = ['git', 'clone', repository, str(project)]
        project.parent.mkdir(parents=True, exist_ok=True)
    else:
        project.mkdir(parents=True, exist_ok=True)
        command = ['git', 'init', str(project)]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise ValueError(f'git {command[1]} 失败，退出码 {completed.returncode}；请检查 Git 和仓库配置')
    return project


def load_initial_prompt(path):
    return validate_task(path.read_bytes())


def validate_task(data):
    if len(data) > MAX_UPLOAD:
        raise ValueError('任务文件最大 64 KB')
    try:
        text = data.decode('utf-8-sig').strip()
    except UnicodeDecodeError as exc:
        raise ValueError('请上传 UTF-8 文本文件') from exc
    if not text:
        raise ValueError('任务不能为空')
    return text


class NativeState:
    def __init__(self, root, session_id, project=None, task_dir=None, resumed=False):
        self.root = root
        self.session_id = session_id
        self.project = str(project) if project is not None else None
        self.status = 'initializing'
        self.active = None
        self.tasks = []
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.last_stop = 0
        self.context_ready = resumed
        self.resumed = resumed
        self.task_dir = task_dir if task_dir is not None else root / 'task'
        self.task_dir.mkdir(parents=True, exist_ok=True)
        for folder in ('results', 'hook-events'):
            (root / folder).mkdir(exist_ok=True)

    def accept(self, data):
        validate_task(data)
        with self.lock:
            if self.status == 'stopped':
                raise ValueError('CC 进程已退出，请重新启动服务')
            if sum(task['status'] == 'queued' for task in self.tasks) >= 20:
                raise ValueError('任务队列已满')
            task_id = uuid.uuid4().hex
            path = self.task_dir / (task_id + '.txt')
            path.write_bytes(data)
            task = {'id': task_id, 'file': str(path), 'status': 'queued', 'result': None}
            self.tasks.append(task)
            return task

    def next_task(self):
        with self.lock:
            if self.status != 'ready':
                return None
            for task in self.tasks:
                if task['status'] == 'queued':
                    self.active = task
                    task['status'] = 'running'
                    self.status = 'busy'
                    return task
            return None

    def on_event(self, data):
        with self.lock:
            if data.get('session_id') != self.session_id:
                return
            kind = data.get('hook_event_name')
            if kind == 'SessionStart':
                (self.root / 'native-session.json').write_text(json.dumps({
                    'session_id': self.session_id, 'confirmed': True,
                    'transcript_path': data.get('transcript_path'),
                    'project': self.project,
                }), encoding='utf-8')
                if self.resumed and self.status == 'initializing':
                    self.status = 'ready'
                    self.last_stop = time.monotonic()
            elif kind == 'UserPromptSubmit':
                self.status = 'busy'
            elif kind in ('Stop', 'StopFailure'):
                if self.active:
                    self.active['status'] = 'completed' if kind == 'Stop' else 'failed'
                    self.active['result'] = data.get('last_assistant_message', '')
                    (self.root / 'results' / (self.active['id'] + '.json')).write_text(
                        json.dumps(self.active, ensure_ascii=False, indent=2), encoding='utf-8')
                    self.active = None
                else:
                    (self.root / 'initialization.json').write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                if kind == 'Stop':
                    self.context_ready = True
                self.status = 'ready' if self.context_ready else 'initialization_failed'
                self.last_stop = time.monotonic()
            elif kind == 'SessionEnd':
                self.status = 'stopped'
                if self.active:
                    self.active['status'] = 'failed'
                    self.active = None

    def snapshot(self):
        with self.lock:
            return {'mode': 'native-claude-terminal', 'model': 'deepseek-flash',
                    'session_id': self.session_id, 'status': self.status,
                    'context_ready': self.context_ready,
                    'project': self.project,
                    'tasks': [dict(task) for task in self.tasks]}


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200, state.snapshot()) if self.path == '/state' else self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if not secrets.compare_digest(self.headers.get('X-CC-Token', ''), state.token):
                self.reply(403, {'error': '缺少或错误的 X-CC-Token'})
                return
            if self.path != '/tasks':
                self.reply(404, {'error': 'not found'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_UPLOAD:
                    raise ValueError('文件必须在 1 到 65536 字节之间')
                task = state.accept(self.rfile.read(size))
                self.reply(202, {'task_id': task['id'], 'session_id': state.session_id, 'status': task['status']})
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description='接收任务文件，交给同一个 Claude Code 会话执行')
    parser.add_argument('--project', type=Path, help='可选：预先指定项目；也可进入 CC 后在对话中指定仓库')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1', help='HTTP 监听地址')
    parser.add_argument('--repo', help='首次初始化时克隆的目标项目 Git URL')
    parser.add_argument('--prompt-file', type=Path, default=ROOT / 'initial_prompt.txt',
                        help='初始化上下文文件，默认 initial_prompt.txt')
    args = parser.parse_args()
    runtime = ROOT / 'runtime'
    session_file = runtime / 'native-session.json'
    saved_session = json.loads(session_file.read_text(encoding='utf-8')) if session_file.exists() else {}
    try:
        project = resolve_project_path(args.project, args.repo, saved_session.get('project'))
        context = load_initial_prompt(args.prompt_file.resolve())
        directory = prepare_project(project, args.repo)
    except (OSError, ValueError) as exc:
        parser.error(f'无法初始化目标项目或读取 prompt：{exc}')

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    runtime.mkdir(exist_ok=True)
    context += ('\n\n启动位置：' + str(directory) + '\n'
                + ('启动时预选项目：' + str(project) if project is not None
                   else '启动时未预选业务仓库，请在对话中接受用户指定的仓库。'))
    session_id, resumed, session_args = build_session_launch(saved_session, project, context)
    state = NativeState(runtime, session_id, project, ROOT / 'task', resumed=resumed)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(state))
    (runtime / 'http-token.txt').write_text(state.token, encoding='utf-8')
    hook_directory = runtime / 'hook-events' / uuid.uuid4().hex
    hook_directory.mkdir()
    hooks = runtime / 'native-hooks.json'
    command = '"' + sys.executable.replace('\\', '/') + '" "' + str(Path(__file__).resolve()).replace('\\', '/') + '" --hook'
    hooks.write_text(json.dumps({'hooks': {
        event: [{'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]}]
        for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'StopFailure', 'SessionEnd')
    }}, ensure_ascii=False), encoding='utf-8')
    environment = os.environ.copy()
    environment.update({'CCTEST_HOOK_DIR': str(hook_directory),
                        'ANTHROPIC_MODEL': 'deepseek-flash',
                        'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-flash'})
    executable = Path(shutil.which('claude') or '').parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    if not executable.is_file():
        raise RuntimeError('找不到已安装的原生 claude.exe')
    argv = [str(executable), '--model', 'deepseek-flash', '--setting-sources', 'user',
            '--settings', str(hooks), '--permission-mode', 'acceptEdits',
            '--add-dir', str(state.task_dir), *session_args]
    writer = None
    process = None
    server_started = False
    try:
        writer = ConsoleTaskWriter()
        print(f'HTTP: http://{args.host}:{args.port}/tasks | cwd: {directory} | CC session: {session_id}', flush=True)
        process = launch_console(argv, directory, environment)
        writer.ignore_parent_control_c()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        server_started = True
        while process.poll() is None:
            for event_file in sorted(hook_directory.glob('*.json'), key=lambda path: path.stat().st_mtime_ns):
                event_data = json.loads(event_file.read_text(encoding='utf-8'))
                state.on_event(event_data)
                with (runtime / 'events.jsonl').open('a', encoding='utf-8') as event_log:
                    event_log.write(json.dumps(event_data, ensure_ascii=False) + '\n')
                event_file.unlink()
            if time.monotonic() - state.last_stop > 1:
                task = state.next_task()
                if task:
                    prompt = '请读取任务文件 ' + task['file'].replace('\\', '/') + ' 并执行文件中的任务。'
                    writer.write('\x15\x1b[200~' + prompt + '\x1b[201~')
                    time.sleep(0.15)
                    writer.write('\r')
            time.sleep(0.05)
    finally:
        state.status = 'stopped'
        if server_started:
            server.shutdown()
        server.server_close()
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        finally:
            if writer is not None:
                writer.close()


def hook():
    data = json.load(sys.stdin)
    root = Path(os.environ['CCTEST_HOOK_DIR'])
    temporary = root / (uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.rename(temporary.with_suffix('.json'))


if __name__ == '__main__':
    hook() if '--hook' in sys.argv else main()
