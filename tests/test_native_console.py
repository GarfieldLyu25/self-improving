"""Check inherited native console ownership and task injection in an isolated console."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_console import ConsoleTaskWriter, InputRecord, launch_console


def child(result_file):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    kernel.ReadConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord), wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD)]
    input_handle = kernel.GetStdHandle(-10)
    mode = wintypes.DWORD()
    input_is_console = bool(kernel.GetConsoleMode(input_handle, ctypes.byref(mode)))
    output_is_console = bool(kernel.GetConsoleMode(kernel.GetStdHandle(-11), ctypes.byref(mode)))
    pids = (wintypes.DWORD * 16)()
    count = kernel.GetConsoleProcessList(pids, len(pids))
    characters = []
    finished = False
    while not finished:
        records = (InputRecord * 256)()
        read = wintypes.DWORD()
        if not kernel.ReadConsoleInputW(input_handle, records, len(records), ctypes.byref(read)):
            raise ctypes.WinError(ctypes.get_last_error())
        for index in range(read.value):
            record = records[index]
            if record.kind == 1 and record.event.key.down:
                if record.event.key.char == '\r':
                    finished = True
                    break
                characters.append(record.event.key.char)
    text = ''.join(characters).encode('utf-16-le', errors='surrogatepass').decode('utf-16-le')
    Path(result_file).write_text(json.dumps({'input_is_console': input_is_console,
                                            'output_is_console': output_is_console,
                                            'console_processes': list(pids[:count]), 'text': text}), encoding='utf-8')


def probe():
    import os
    with ConsoleTaskWriter() as writer, tempfile.TemporaryDirectory(dir=ROOT) as folder:
        kernel = writer.kernel
        kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        kernel.SetStdHandle.restype = wintypes.BOOL
        output = kernel.CreateFileW('CONOUT$', 0xC0000000, 3, None, 3, 0, None)
        try:
            for kind, handle in ((-10, writer.handle), (-11, output), (-12, output)):
                if not kernel.SetStdHandle(kind, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            result_file = Path(folder) / 'result.json'
            process = launch_console([sys.executable, __file__, '--child', str(result_file)], ROOT, os.environ.copy())
            try:
                writer.write('\x1b[200~任务😀\x1b[201~\r')
                process.wait(timeout=10)
                result = json.loads(result_file.read_text(encoding='utf-8'))
                passed = (result['input_is_console'] and result['output_is_console']
                          and os.getpid() in result['console_processes'] and process.pid in result['console_processes']
                          and result['text'] == '\x1b[200~任务😀\x1b[201~')
                print(json.dumps({'passed': passed, 'same_console': os.getpid() in result['console_processes'],
                                  'native_input': result['input_is_console'], 'native_output': result['output_is_console'],
                                  'task_text_preserved': result['text'] == '\x1b[200~任务😀\x1b[201~'}))
                return 0 if passed else 1
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        finally:
            kernel.CloseHandle(output)


class NativeConsoleTests(unittest.TestCase):
    def test_child_owns_native_console_and_receives_only_queued_task_text(self):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        result = subprocess.run([sys.executable, __file__, '--probe'],
                                creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['passed'])


if __name__ == '__main__':
    if '--child' in sys.argv:
        child(sys.argv[-1])
    elif '--probe' in sys.argv:
        sys.exit(probe())
    else:
        unittest.main()
