"""Launch CC on the existing console; inject only explicitly queued HTTP tasks."""
import ctypes
from ctypes import wintypes
import subprocess


class KeyEvent(ctypes.Structure):
    _fields_ = [('down', wintypes.BOOL), ('repeat', wintypes.WORD),
                ('virtual_key', wintypes.WORD), ('scan', wintypes.WORD),
                ('char', wintypes.WCHAR), ('modifiers', wintypes.DWORD)]


class EventUnion(ctypes.Union):
    _fields_ = [('key', KeyEvent), ('padding', ctypes.c_byte * 16)]


class InputRecord(ctypes.Structure):
    _fields_ = [('kind', wintypes.WORD), ('event', EventUnion)]


def launch_console(arguments, directory, environment):
    # No pipes or pseudoterminal: CC owns the same console as an ordinary CMD launch.
    return subprocess.Popen(arguments, cwd=str(directory), env=environment)


class ConsoleTaskWriter:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord),
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.WriteConsoleInputW.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
        self.kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
        self.handle = self.kernel.CreateFileW('CONIN$', 0xC0000000, 3, None, 3, 0, None)
        if self.handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.ignoring_control_c = False

    def ignore_parent_control_c(self):
        # Call after spawning so CC keeps its own Ctrl+C handling.
        if not self.kernel.SetConsoleCtrlHandler(None, True):
            raise ctypes.WinError(ctypes.get_last_error())
        self.ignoring_control_c = True

    def write(self, text):
        encoded = text.encode('utf-16-le')
        characters = [chr(int.from_bytes(encoded[index:index + 2], 'little'))
                      for index in range(0, len(encoded), 2)]
        for offset in range(0, len(characters), 128):
            chunk = characters[offset:offset + 128]
            records = (InputRecord * (2 * len(chunk)))()
            for index, char in enumerate(chunk):
                virtual_key = {'\r': 0x0D, '\x1b': 0x1B, '\t': 0x09}.get(char, 0)
                for down, record in ((True, records[2 * index]), (False, records[2 * index + 1])):
                    record.kind = 1
                    record.event.key = KeyEvent(down, 1, virtual_key, 0, char, 0)
            written = wintypes.DWORD()
            if not self.kernel.WriteConsoleInputW(self.handle, records, len(records), ctypes.byref(written)):
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value != len(records):
                raise OSError('未能完整输入 HTTP 任务，请检查 CC 窗口')

    def close(self):
        if self.handle is not None:
            try:
                if self.ignoring_control_c and not self.kernel.SetConsoleCtrlHandler(None, False):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.kernel.CloseHandle(self.handle)
                self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
