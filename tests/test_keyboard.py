import unittest
from unittest.mock import Mock

from service import read_console_key


class KeyboardTests(unittest.TestCase):
    def test_shift_tab_reaches_cc_as_backtab(self):
        self.assertEqual(read_console_key(Mock(side_effect=['\x00', '\x0f'])), '\x1b[Z')

    def test_editing_keys_reach_cc(self):
        for scan, expected in {'H': '\x1b[A', 'P': '\x1b[B',
                               'K': '\x1b[D', 'M': '\x1b[C',
                               'G': '\x1b[H', 'O': '\x1b[F',
                               'S': '\x1b[3~', 'R': '\x1b[2~',
                               'I': '\x1b[5~', 'Q': '\x1b[6~'}.items():
            for prefix in ('\x00', '\xe0'):
                with self.subTest(scan=scan, prefix=prefix):
                    self.assertEqual(read_console_key(Mock(side_effect=[prefix, scan])), expected)

    def test_text_and_control_characters_are_preserved(self):
        for key in ('中', 'a', '\t', '\r', '\x1b', '\x08', '\x15'):
            with self.subTest(key=key):
                self.assertEqual(read_console_key(Mock(return_value=key)), key)
