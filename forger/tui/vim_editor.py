"""An embedded vim-style modal editor built on :class:`~textual.widgets.TextArea`.

Running the real vim process *inside* a Textual pane would require a terminal
emulator widget, which isn't available here. Instead this provides an in-pane
modal layer: NORMAL mode (the default) for motions and commands, INSERT mode
for ordinary editing, and Esc to return to NORMAL. It is a useful subset, not a
complete vim.

NORMAL-mode keys supported:
    h j k l  /  arrows        move
    0 $  w  b                 line start/end, word forward/back
    g g   /   G               top / bottom
    x                         delete char under cursor
    d d                       delete line
    u                         undo
    i I a A o O               enter INSERT mode (at / first non-blank / after /
                              end / line-below / line-above)
    : w  /  : x               approve (commit to library)
    : q                       reject (discard)

Ctrl/F-key bindings (Forge, language, backend, ...) are left untouched so they
still reach the app.
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class VimTextArea(TextArea):
    """TextArea plus an embedded vim modal layer."""

    class ModeChanged(Message):
        def __init__(self, mode: str) -> None:
            self.mode = mode
            super().__init__()

    class VimCommand(Message):
        def __init__(self, command: str) -> None:
            self.command = command
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.vim_mode = "normal"
        self._pending: str | None = None

    def _set_mode(self, mode: str) -> None:
        if mode == self.vim_mode:
            return
        self.vim_mode = mode
        if mode == "insert":
            self.add_class("vim-insert")
        else:
            self.remove_class("vim-insert")
        self.post_message(self.ModeChanged(mode))

    def _last_line(self) -> int:
        return self.text.count("\n")

    async def _on_key(self, event: events.Key) -> None:
        # TextArea inserts printable characters inside its own _on_key; we
        # override it to implement modal vim. Non-printable keys (Ctrl+F-key
        # bindings such as Forge) are left unhandled here so the app's bindings
        # still fire through Textual's normal binding resolution.
        if event.key == "escape":
            self._pending = None
            self._set_mode("normal")
            event.prevent_default()
            event.stop()
            return

        if self.vim_mode == "insert":
            await super()._on_key(event)
            return

        if not (event.is_printable or event.key == "enter"):
            return  # non-printable: defer to binding resolution (arrows, ctrl/f-keys)

        event.prevent_default()
        event.stop()
        self._handle_normal(event.character or "", event.key)

    def _handle_normal(self, char: str, key: str) -> None:
        # Resolve a pending two-key / colon sequence first.
        if self._pending is not None:
            pending, self._pending = self._pending, None
            if pending == ":":
                if char in ("w", "x"):
                    self.post_message(self.VimCommand("approve"))
                elif char == "q":
                    self.post_message(self.VimCommand("reject"))
            elif pending == "g" and char == "g":
                self.move_cursor((0, 0))
            elif pending == "d" and char == "d":
                self.action_delete_line()
            return

        if char == ":":
            self._pending = ":"
        elif char == "g":
            self._pending = "g"
        elif char == "d":
            self._pending = "d"
        elif char == "h" or key == "left":
            self.action_cursor_left()
        elif char == "l" or key == "right":
            self.action_cursor_right()
        elif char == "j" or key in ("down", "enter"):
            self.action_cursor_down()
        elif char == "k" or key == "up":
            self.action_cursor_up()
        elif char == "0" or key == "home":
            self.action_cursor_line_start()
        elif char == "$" or key == "end":
            self.action_cursor_line_end()
        elif char == "w":
            self.action_cursor_word_right()
        elif char == "b":
            self.action_cursor_word_left()
        elif char == "G":
            self.move_cursor((self._last_line(), 0))
            self.scroll_cursor_visible()
        elif char == "x":
            self.action_delete_right()
        elif char == "u":
            self.action_undo()
        elif char in ("i", "I", "a", "A", "o", "O"):
            self._enter_insert(char)
        # else: ignore unknown normal-mode key

    def _enter_insert(self, kind: str) -> None:
        self.read_only = False
        if kind == "I":
            self.action_cursor_line_start()
        elif kind == "a":
            self.action_cursor_right()
        elif kind == "A":
            self.action_cursor_line_end()
        elif kind == "o":
            self.action_cursor_line_end()
            self.insert("\n")
        elif kind == "O":
            self.action_cursor_line_start()
            self.insert("\n")
            self.action_cursor_up()
        self._set_mode("insert")
