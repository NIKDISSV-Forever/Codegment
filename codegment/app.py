import configparser
import os.path
import re
from functools import lru_cache
from pathlib import Path
from tkinter import filedialog, messagebox

import kivy.extras.highlight as _kv_lex
import pygments.lexer
import pygments.lexers
import pygments.plugin
import pygments.styles
from kivy.clock import Clock
from kivy.core.window import Keyboard, Window
from kivy.input import MotionEvent
from kivy.uix.button import Button
from kivy.uix.codeinput import CodeInput
from kivymd.app import MDApp as App
from kivymd.toast import toast
from kivymd.uix.boxlayout import MDBoxLayout as BoxLayout
from kivymd.uix.button import MDRectangleFlatButton as RectangleFlatButton
from kivymd.uix.dialog import MDDialog as Dialog
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.textfield import MDTextField as TextInput

from . import CONFIG_FILENAME

Window.minimum_height = 230
Window.minimum_width = 460


def _install_kv_lexer():
    getattr(_kv_lex, '__all__', setattr(_kv_lex, '__all__', ['KivyLexer']))
    pygments.lexers.LEXERS[_kv_lex.KivyLexer.name] = (_kv_lex.KivyLexer.__module__,
                                                      _kv_lex.KivyLexer.name, (*_kv_lex.KivyLexer.aliases,),
                                                      (*_kv_lex.KivyLexer.filenames,), (*_kv_lex.KivyLexer.mimetypes,))


class CodeEditorApp(App):
    __slots__ = ()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._opened_file = ''
        self.opened_file = os.devnull
        self._found_n = 0
        self._found = ()
        self.find_dialog = self._build_find_dialog()
        self.configuration = configparser.ConfigParser(allow_no_value=True)
        self._suggest_menu = MDDropdownMenu(caller=self.root)
        self._suggest_menu_selected = None
        self._reset_suggest()

    def build(self):
        self._cur_lbl = Button(size_hint_x=.1, disabled=True, disabled_color='black', halign='center', valign='center')
        self._lex_name = Button(size_hint_x=.2, bold=True, on_touch_down=self.on_touch_down_file_settings,
                                halign='center', valign='center')
        self._code_inp = CodeInput(lexer=pygments.lexers.TextLexer(), do_wrap=False)
        self._tabs = BoxLayout()
        self.footer = BoxLayout(
            self._tabs, self._cur_lbl, self._lex_name,
            size_hint_y=.05
        )

        self._keyboard_on_key_down_default = self._code_inp.keyboard_on_key_down
        self._code_inp.keyboard_on_key_down = self._on_keyboard_down
        self.tabs = {*()}
        self._lex_name.text = self._code_inp.lexer.name
        self._tabs.bind(size=self.resize_tabs)
        self._cur_lbl.bind(size=self._text_resize)
        self._lex_name.bind(size=self._text_resize)
        self._code_inp.bind(text=self.on_change, cursor=self.on_cursor)

        return BoxLayout(
            BoxLayout(self._code_inp),
            self.footer,
            orientation='vertical')

    @property
    def opened_file(self):
        return self._opened_file

    @opened_file.setter
    def opened_file(self, value):
        self._opened_file = value
        if value:
            self.title = value

    def on_change(self, inst: CodeInput, _=None):
        if not self.opened_file.endswith('*'):
            self.opened_file += '*'
        if self._suggest_menu_selected is not None:
            self.resuggest(inst)

    def _on_keyboard_down(self, windows: Keyboard, keycode: tuple[int, str], text: str, modifiers: list[str]):
        if 'ctrl' in modifiers:
            match text:
                case 'o':
                    self.open(filedialog.askopenfilename())
                case 's':
                    self.save()
                case 'n':
                    self.open(os.devnull)
                case 'f':
                    self.find()
                case 'w':
                    self.close_tab()
                case '-':
                    self.set_font_size(self._code_inp.font_size - 1)
                case '=':  # +
                    self.set_font_size(self._code_inp.font_size + 1)
                case ' ':
                    def back(_, t=self._code_inp.text, cursor=self._code_inp.cursor):
                        self._code_inp.text = t
                        self._code_inp.cursor = cursor
                        if self._suggest_menu_selected is None:
                            self.auto_compete_dialog()
                        else:
                            self._reset_suggest()

                    Clock.schedule_once(back)
        if self._suggest_menu_selected is not None:
            match keycode[1]:
                case 'escape':
                    self._reset_suggest()
                case 'tab':
                    self._suggest_menu_selected = (self._suggest_menu_selected + (-1) ** ('shift' in modifiers)
                                                   ) % len(self._suggest_menu.items)
                    self._mark_selected_suggest()
                    return
        match keycode[1]:
            case 'f3':
                if 'ctrl' in modifiers:
                    self.replace()
                else:
                    self.find_highlight((-1) ** ('shift' in modifiers))

        return self._keyboard_on_key_down_default(windows, keycode, text, modifiers)

    def save(self):
        code = self._code_inp.text
        fn = self.opened_file.removesuffix('*')
        if fn == os.devnull:
            self.open(fn := filedialog.asksaveasfilename())
            self._code_inp.text = code
        if not fn:
            return
        try:
            with open(fn, 'w', encoding='UTF-8') as f:
                f.write(code)
        except OSError:
            pass
        else:
            self.opened_file = fn
            if fn == CONFIG_FILENAME:
                self.load_config_file()

    def open(self, fn: str):
        if not fn:
            return
        fallback = self.opened_file.removesuffix('*')
        self._code_inp.text = ''
        self.opened_file = fn
        fn = Path(fn)
        if fn.is_file():
            self._code_inp.text = fn.read_text(encoding='UTF-8', errors='ignore')
        else:
            try:
                open(fn, 'wb').close()
            except OSError:
                self.open(fallback)
        self.opened_file = self.opened_file.removesuffix('*')
        if lexer := pygments.lexers.find_lexer_class_for_filename(self.opened_file, self._code_inp.text):
            self._code_inp.lexer = lexer()
            self._lex_name.text = lexer.name
        self.tabs.add(self.opened_file)

        self.retabs()
        if fn == CONFIG_FILENAME:
            self.load_config_file()
        self._code_inp.cursor = (0, 0)

    def find(self):
        self.find_dialog.open()

    def find_pattern(self, compiled: re.Pattern):
        self._compiled = compiled
        self._found = *((n.start(), n.end()) for n in self._compiled.finditer(self._code_inp.text)),

    def find_highlight(self, delta: int):
        if not self._found:
            toast("Nothing found")
            return
        self._found_n = (self._found_n + delta) % len(self._found)
        start, end = self._found[self._found_n]
        self._code_inp.select_text(start, end)
        self._code_inp.cursor = self._code_inp.get_cursor_from_index(start)

    def replace(self):
        start, end = self._found[self._found_n]
        self._code_inp.text = f'{self._code_inp.text[:start]}{self.replace_input.text}{self._code_inp.text[end:]}'
        self._code_inp.cursor = self._code_inp.get_cursor_from_index(start)

    def check_save(self, fn: str):
        if not fn.endswith('*'):
            return
        fn = fn[:-1]
        if messagebox.askyesno('File not saved', f'Save {fn}?'):
            self.save()

    def on_cursor(self, inst, cursor: tuple[int, int]):
        col, row = cursor
        self._cur_lbl.text = f'{row + 1}:{col + 1}'
        if self._suggest_menu_selected is not None:
            Clock.schedule_once(lambda _: self.resuggest(inst))

    def _build_find_dialog(self):
        self.replace_input = TextInput(hint_text="Replace", multiline=False)
        self.search_input = TextInput(hint_text="Search regexp", multiline=False)
        return Dialog(
            type="custom",
            title="Search",
            content_cls=BoxLayout(self.search_input, self.replace_input,
                                  size_hint_y=None, orientation='vertical'),
            buttons=[RectangleFlatButton(text='Replace all', on_press=self._replace_all),
                     RectangleFlatButton(text='Search!', on_press=self._find)],
        )

    def _replace_all(self, _=None):
        self._find(_)
        if not self._compiled:
            return
        self._code_inp.text = self._compiled.sub(self.replace_input.text, self._code_inp.text)

    def _find(self, _=None):
        try:
            self.find_pattern(re.compile(self.search_input.text))
        except re.error:
            self.find_pattern(re.compile(re.escape(self.search_input.text)))
        self.find_dialog.dismiss()
        self.find_highlight(1)

    def _open_tab_func(self, fn: str):
        def on_press(_=None):
            self.open(fn)

        return on_press

    def close_tab(self):
        if self.tabs == {os.devnull}:
            self.stop()
        fn = self.opened_file
        self.check_save(fn)
        fn = fn.removesuffix('*')
        if fn in self.tabs:
            self.tabs.remove(fn)
        self.retabs()
        self.open((*self.tabs,)[0])

    def retabs(self):
        self._tabs.clear_widgets()
        if not self.tabs:
            self.tabs.add(os.devnull)
        elif len(self.tabs) > 1 and os.devnull in self.tabs:
            self.tabs.remove(os.devnull)
        if len(self.tabs) == 1:
            def is_bold(_=None):
                return False
        else:
            is_bold = self.opened_file.__eq__
        for i in self.tabs:
            self._tabs.add_widget(
                Button(text=Path(i).name, on_press=self._open_tab_func(i), halign='center', valign='center',
                       bold=is_bold(i)))
        self.resize_tabs(self._tabs, self._tabs.size)

    @staticmethod
    def resize_tabs(wid, size):
        b_count = len(wid.children)
        size_hint_x = 1 / b_count
        text_size = (size[0] / b_count, size[1] / b_count)
        for i in wid.children:
            i.text_size = text_size
            i.size_hint_x = size_hint_x

    @staticmethod
    def _text_resize(wid, size):
        wid.text_size = size

    def set_font_size(self, size):
        if 1 <= size <= 99:
            self._code_inp.font_size = size
            self.save_config()

    def on_touch_down_file_settings(self, inst, touch: MotionEvent):
        if not inst.collide_point(*touch.pos):
            return
        if touch.is_double_tap:
            self.open(CONFIG_FILENAME)
        else:
            self.load_config_file()

    def on_start(self):
        _install_kv_lexer()
        self.configuration.read_dict(
            {'Font': {'family': str(self._code_inp.font_family),
                      'name': str(self._code_inp.font_name),
                      'size': f"{self._code_inp.font_size}",
                      '; 0 < size < 128': None},
             'CodeInput': {
                 'default_lexer': str(self._code_inp.lexer.name),
                 '; see https://pygments.org/languages': None,
                 'style_name': str(self._code_inp.style_name),
                 '; see https://pygments.org/styles': None,
             },
             self.configuration.default_section: {'last_file': 'nul'}})
        self.load_config_file()
        self.open(self.last_file)

    def on_stop(self):
        self.check_save(self.opened_file)
        self.last_file = self.opened_file
        self.save_config()

    def load_config_file(self):
        self.configuration.read((CONFIG_FILENAME,))
        font_family = self.configuration.get('Font', 'family')
        font_name = self.configuration.get('Font', 'name')
        font_size = self.configuration.get('Font', 'size')
        style_name = self.configuration.get('CodeInput', 'style_name')
        default_lexer = self.configuration.get('CodeInput', 'default_lexer')
        last_file = self.configuration.get(self.configuration.default_section, 'last_file')

        self._code_inp.font_family = font_family
        self._code_inp.font_name = font_name
        self._code_inp.font_size = font_size
        self._code_inp.style_name = style_name
        if lexer := pygments.lexers.find_lexer_class(default_lexer):
            self._code_inp.lexer = lexer()
            self._lex_name.text = self._code_inp.lexer.name
        self.last_file = last_file
        self.save_config()

    def save_config(self):
        self.configuration.set('Font', 'family', str(self._code_inp.font_family))
        self.configuration.set('Font', 'name', str(self._code_inp.font_name))
        self.configuration.set('Font', 'size', str(self._code_inp.font_size))
        self.configuration.set('CodeInput', 'style_name', str(self._code_inp.style_name))
        self.configuration.set(self.configuration.default_section, 'last_file', str(self.last_file))
        with open(CONFIG_FILENAME, 'w') as f:
            self.configuration.write(f)

    @staticmethod
    @lru_cache(maxsize=256)
    def get_possible_words(lexer, filter_func) -> dict[str, tuple[str]] | dict:
        if not hasattr(lexer, 'tokens'):
            return {}
        result = {}
        for k, v in lexer.tokens.items():
            for tokens in v:
                match tokens:
                    case [tokens, tp]:
                        match tokens:
                            case pygments.lexer.words(words=words):
                                tp = '.'.join(tp)
                                result[tp] = ()
                                for word in words:
                                    if filter_func(word):
                                        result[tp] += word,
        return result

    def _suggest(self):
        self._reset_suggest()
        start = self._code_inp.cursor_index((0, self._code_inp.cursor[1]))
        text = self._code_inp.text[start:self._code_inp.cursor_index(self._code_inp.cursor) + 1]
        match_with: list[re.Match] = [*re.finditer(r'\w+', text)]
        if match_with:
            match_with: re.Match = match_with[-1]
            self._suggest_paste_at = start + match_with.start()
            word = match_with.group(0)
            m = re.compile(fr'.*{re.escape(word)}.*', re.I).match
        else:
            m = re.compile(r'.*', re.I).match
            self._suggest_paste_at = self._code_inp.cursor_index(self._code_inp.cursor)
        suggestions = self.get_possible_words(self._code_inp.lexer, m)
        items = ()
        for tp, words in suggestions.items():
            for word in words:
                items += {'viewclass': 'TwoLineListItem',
                          'text': word, 'secondary_text': tp, 'on_release': self.paste(word)},
        if not items:
            return
        self._suggest_menu = self._suggest_menu_with_items(items)
        self._suggest_menu.open()
        self._suggest_menu_selected = 0
        self._suggest_at_row = self._code_inp.cursor_row
        self._mark_selected_suggest()

    def auto_compete_dialog(self):
        self._suggest()

    def _reset_suggest(self):
        self._suggest_menu.dismiss()
        self._suggest_menu_selected = self._suggest_menu_selected_mem = None
        self._suggest_paste_at = 0
        self._suggest_at_row = 0

    def resuggest(self, inst: CodeInput):
        if (pos := inst.cursor_index(inst.cursor)) >= len(inst.text) or inst.cursor_row != self._suggest_at_row:
            self._reset_suggest()
            return
        if inst.text[pos] == '\n':
            self._suggest_menu.items[self._suggest_menu_selected]['on_release']()
            return
        self._reset_suggest()
        self.auto_compete_dialog()

    def _mark_selected_suggest(self):
        if self._suggest_menu_selected != self._suggest_menu_selected_mem:
            self._suggest_menu.dismiss()
            self._suggest_menu_selected_mem = self._suggest_menu_selected
            for item in self._suggest_menu.items:
                item['secondary_text'] = item['secondary_text'].removesuffix(' (selected)')
            self._suggest_menu.items[self._suggest_menu_selected]['secondary_text'] += ' (selected)'
            self._suggest_menu.open()

    def paste(self, word):
        def callback():
            inst = self._code_inp
            first = (f"{self._code_inp.text[:self._suggest_paste_at]}"
                     f"{word}")
            self._reset_suggest()

            def replace(_, cursor_at=len(first), text=f'{first}{inst.text[inst.cursor_index(inst.cursor) + 1:]}'):
                self._code_inp.text = text
                self._code_inp.cursor = self._code_inp.get_cursor_from_index(cursor_at)
                self._reset_suggest()

            Clock.schedule_once(replace)

        return callback

    def _suggest_menu_with_items(self, items):
        return MDDropdownMenu(caller=self._code_inp, items=items, width_mult=4, opening_time=0)


def run():
    CodeEditorApp().run()
