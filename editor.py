# Queyntisen - AI Native Terminal Editor
# Copyright (C) 2026 fMert

import curses
import json
import os
import re
import sys
import textwrap
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai import OpenAI


API_KEY = os.getenv("QUEYNTISEN_API_KEY") or os.getenv("OPENAI_API_KEY") or "lm-studio"
BASE_URL = os.getenv("QUEYNTISEN_BASE_URL", "http://localhost:1234/v1")
MODEL_NAME = os.getenv("QUEYNTISEN_MODEL", "local-model")
CONFIG_PATH = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "queyntisen" / "config.json"

TAB = "    "
COMMANDS = ["q", "quit", "w", "write", "wq", "x", "setup", "model"]
PROVIDERS = {
    "OpenAI": ("openai", "https://api.openai.com/v1", "gpt-4.1"),
    "Anthropic": ("anthropic", "https://api.anthropic.com/v1", "claude-sonnet-4-20250514"),
    "DeepSeek": ("openai", "https://api.deepseek.com/v1", "deepseek-chat"),
    "LM Studio (local)": ("openai", "http://localhost:1234/v1", "local-model"),
    "Ollama (local)": ("ollama", "http://localhost:11434", "llama3.1"),
    "OpenRouter": ("openai", "https://openrouter.ai/api/v1", "openai/gpt-4.1"),
    "Other": ("openai", BASE_URL, MODEL_NAME),
}
@dataclass
class State:
    lines: list[str] = field(default_factory=lambda: [""])
    filename: str | None = None
    mode: str = "NORMAL"
    cy: int = 0
    cx: int = 0
    top: int = 0
    left: int = 0
    view: str = "preview"
    status: str = ""
    last_search: str = ""
    clipboard: str | None = None
    history: list[tuple[list[str], int, int]] = field(default_factory=list)
    dirty: bool = False
    insert_snapshot: bool = False
    pane: str = "note"
    chat: list[str] = field(default_factory=lambda: ["Queyntisen AI", "Ctrl-W switches panes. Ctrl-E edits Markdown."])
    chat_input: str = ""
    ai_busy: bool = False
    ai_provider: str = "LM Studio (local)"
    ai_kind: str = "openai"
    ai_api_key: str = API_KEY
    ai_base_url: str = BASE_URL
    ai_model: str = MODEL_NAME

    def snapshot(self) -> None:
        self.history.append((self.lines[:], self.cy, self.cx))
        if len(self.history) > 200:
            self.history.pop(0)
        self.dirty = True

    def undo(self) -> None:
        if not self.history:
            self.status = "Nothing to undo"
            return
        self.lines, self.cy, self.cx = self.history.pop()
        self.clamp_cursor()
        self.dirty = True
        self.status = "Undo"

    def clamp_cursor(self) -> None:
        if not self.lines:
            self.lines = [""]
        self.cy = max(0, min(self.cy, len(self.lines) - 1))
        self.cx = max(0, min(self.cx, len(self.lines[self.cy])))


def safe_add(win, y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        if y >= 0 and x >= 0:
            win.addstr(y, x, text, attr)
    except curses.error:
        pass


def safe_ch(win, y: int, x: int, ch, attr: int = 0) -> None:
    try:
        if y >= 0 and x >= 0:
            win.addch(y, x, ch, attr)
    except curses.error:
        pass


def load_file(path: str) -> tuple[list[str], str]:
    file_path = Path(path)
    if not file_path.exists():
        return [""], f"New file: {path}"
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
        return lines or [""], f"Opened {path}"
    except OSError as exc:
        return [f"Error: {exc}"], f"Could not open {path}: {exc}"


def save_file(state: State, path: str | None = None) -> None:
    if path:
        state.filename = path
    if not state.filename:
        state.status = "No file name. Use :w path"
        return
    try:
        Path(state.filename).write_text("\n".join(state.lines), encoding="utf-8")
        state.dirty = False
        state.status = f"Saved {state.filename}"
    except OSError as exc:
        state.status = f"Save failed: {exc}"


def ai_config(state: State) -> dict[str, str]:
    return {
        "provider": state.ai_provider,
        "kind": state.ai_kind,
        "api_key": state.ai_api_key,
        "base_url": state.ai_base_url,
        "model": state.ai_model,
    }


def apply_ai_config(state: State, config: dict) -> None:
    provider = str(config.get("provider", state.ai_provider))
    kind = str(config.get("kind", state.ai_kind))
    base_url = str(config.get("base_url", state.ai_base_url)).rstrip("/")
    model = str(config.get("model", state.ai_model))
    api_key = str(config.get("api_key", state.ai_api_key))

    if provider in PROVIDERS:
        default_kind, default_base, default_model = PROVIDERS[provider]
        kind = kind or default_kind
        base_url = base_url or default_base
        model = model or default_model
    state.ai_provider = provider
    state.ai_kind = kind
    state.ai_api_key = api_key
    state.ai_base_url = base_url
    state.ai_model = model


def load_config(state: State) -> None:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as exc:
        state.status = f"Config load failed: {exc}"
        return
    if isinstance(data, dict) and isinstance(data.get("ai"), dict):
        apply_ai_config(state, data["ai"])
        state.chat.append(f"AI: loaded setup for {state.ai_provider}")


def save_config(state: State) -> tuple[bool, str]:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps({"ai": ai_config(state)}, indent=2), encoding="utf-8")
        CONFIG_PATH.chmod(0o600)
        return True, f"Saved setup to {CONFIG_PATH}"
    except OSError as exc:
        return False, f"Config save failed: {exc}"


def setup_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    pairs = [
        (1, curses.COLOR_CYAN, background),
        (2, curses.COLOR_YELLOW, background),
        (3, curses.COLOR_GREEN, background),
        (4, curses.COLOR_MAGENTA, background),
        (5, curses.COLOR_BLUE, background),
        (6, curses.COLOR_BLACK, curses.COLOR_CYAN),
        (7, curses.COLOR_WHITE, curses.COLOR_BLACK),
        (8, curses.COLOR_BLACK, curses.COLOR_WHITE),
        (9, curses.COLOR_RED, background),
        (10, curses.COLOR_BLACK, curses.COLOR_YELLOW),
    ]
    for pair in pairs:
        try:
            curses.init_pair(*pair)
        except curses.error:
            pass


def color(name: int, bold: bool = False) -> int:
    try:
        if not curses.has_colors():
            return curses.A_BOLD
        attr = curses.color_pair(name)
    except curses.error:
        return curses.A_BOLD
    return attr | curses.A_BOLD if bold else attr


Segment = tuple[str, int]


def line(text: str = "", attr: int = 0) -> list[Segment]:
    return [(text, attr)]


def text_len(segments: list[Segment]) -> int:
    return sum(len(text) for text, _ in segments)


def draw_segments(win, y: int, x: int, segments: list[Segment], max_width: int) -> None:
    offset = 0
    for text, attr in segments:
        if offset >= max_width:
            break
        safe_add(win, y, x + offset, text[: max_width - offset], attr)
        offset += len(text)


def inline_segments(text: str, base_attr: int = 0) -> list[Segment]:
    segments: list[Segment] = []
    pos = 0
    pattern = re.compile(
        r"!\[([^\]]*)\]\(([^)]+)\)"
        r"|`([^`]+)`"
        r"|\*\*([^*]+)\*\*"
        r"|~~([^~]+)~~"
        r"|\*([^*]+)\*"
        r"|\[([^\]]+)\]\(([^)]+)\)"
        r"|<(https?://[^>]+)>"
    )
    for match in pattern.finditer(text):
        if match.start() > pos:
            segments.append((text[pos:match.start()], base_attr))
        if match.group(2):
            label = match.group(1) or "image"
            segments.append((f"[image: {label}]", color(4, True)))
            segments.append((f" <{match.group(2)}>", color(5)))
        elif match.group(3):
            segments.append((match.group(3), color(10)))
        elif match.group(4):
            segments.append((match.group(4), base_attr | curses.A_BOLD))
        elif match.group(5):
            segments.append((match.group(5), base_attr | curses.A_DIM))
        elif match.group(6):
            segments.append((match.group(6), base_attr | curses.A_UNDERLINE))
        elif match.group(7):
            segments.append((match.group(7), base_attr | curses.A_UNDERLINE | color(1)))
            segments.append((f" <{match.group(8)}>", color(5)))
        elif match.group(9):
            segments.append((match.group(9), base_attr | curses.A_UNDERLINE | color(1)))
        pos = match.end()
    if pos < len(text):
        segments.append((text[pos:], base_attr))
    return segments or [("", base_attr)]


def wrap_segments(segments: list[Segment], width: int, indent: str = "") -> list[list[Segment]]:
    width = max(1, width)
    rows: list[list[Segment]] = []
    current: list[Segment] = []
    current_len = 0
    for text, attr in segments:
        parts = re.findall(r"\S+\s*|\s+", text)
        for part in parts:
            if current_len and current_len + len(part.rstrip()) > width:
                rows.append(current)
                current = [(indent, 0)] if indent else []
                current_len = len(indent)
                part = part.lstrip()
            while len(part) > width:
                room = max(1, width - current_len)
                current.append((part[:room], attr))
                rows.append(current)
                current = [(indent, 0)] if indent else []
                current_len = len(indent)
                part = part[room:]
            if part:
                current.append((part, attr))
                current_len += len(part)
    if current:
        rows.append(current)
    return rows or [line()]


def table_cells(raw: str) -> list[str]:
    stripped = raw.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def is_table_separator(raw: str) -> bool:
    cells = table_cells(raw)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def render_table(rows: list[list[str]], width: int) -> list[list[Segment]]:
    cols = max(len(row) for row in rows)
    rows = [row + [""] * (cols - len(row)) for row in rows]
    max_total = max(8, width - (cols * 3 + 1))
    col_widths = [min(max(len(row[i]) for row in rows), max(8, max_total // cols)) for i in range(cols)]

    def border() -> str:
        return "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"

    def row_segments(row: list[str], attr: int = 0) -> list[Segment]:
        out: list[Segment] = [("|", color(5))]
        for i, cell in enumerate(row):
            value = cell[: max(0, col_widths[i] - 3)] + "..." if len(cell) > col_widths[i] else cell
            out.append((" ", color(5)))
            out.extend(inline_segments(value.ljust(col_widths[i]), attr))
            out.append((" |", color(5)))
        return out

    rendered = [line(border(), color(5)), row_segments(rows[0], curses.A_BOLD), line(border(), color(5))]
    rendered.extend(row_segments(row) for row in rows[1:])
    rendered.append(line(border(), color(5)))
    return rendered


def markdown_blocks(lines: list[str], width: int) -> list[list[Segment]]:
    rendered: list[list[Segment]] = []
    in_code = False
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if i + 1 < len(lines) and "|" in raw and is_table_separator(lines[i + 1]):
            table: list[list[str]] = [table_cells(raw)]
            i += 2
            while i < len(lines) and "|" in lines[i].strip() and lines[i].strip():
                table.append(table_cells(lines[i]))
                i += 1
            rendered.extend(render_table(table, width))
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            rendered.append(line("  " + stripped, color(4)))
        elif in_code:
            rendered.append(line("  " + raw, color(2)))
        elif not stripped:
            rendered.append(line())
        elif stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            prefix = " " if level == 1 else "  "
            attr = color(1, True) if level <= 2 else color(1)
            rendered.extend(wrap_segments([(prefix, attr)] + inline_segments(text, attr), width))
        elif stripped.startswith(">"):
            quote = stripped.lstrip("> ").strip()
            rendered.extend(wrap_segments([("| ", color(5))] + inline_segments(quote), width, "  "))
        elif stripped.startswith(("- [ ]", "- [x]", "- [X]")):
            checked = stripped[3].lower() == "x"
            mark = "[x]" if checked else "[ ]"
            attr = color(3) if checked else 0
            rendered.extend(wrap_segments([(f" {mark} ", attr)] + inline_segments(stripped[5:].strip()), width, "     "))
        elif stripped[:2] in {"- ", "* "}:
            rendered.extend(wrap_segments([(" * ", color(2))] + inline_segments(stripped[2:].strip()), width, "   "))
        elif re.match(r"\d+\.\s+", stripped):
            number, text = stripped.split(".", 1)
            prefix = f" {number}. "
            rendered.extend(wrap_segments([(prefix, color(2))] + inline_segments(text.strip()), width, " " * len(prefix)))
        elif re.fullmatch(r"[-*_]{3,}", stripped):
            rendered.append(line("-" * min(width, 72), color(5)))
        else:
            rendered.extend(wrap_segments(inline_segments(stripped), width))
        i += 1
    return rendered or [line()]


def draw_markdown_source(win, y: int, x: int, text: str, max_width: int) -> None:
    stripped = text.lstrip()
    attr = 0
    if stripped.startswith("#"):
        attr = color(1, True)
    elif stripped.startswith((">", "- [ ]", "- [x]", "- [X]")):
        attr = color(5)
    elif stripped.startswith(("- ", "* ", "```")):
        attr = color(2)
    safe_add(win, y, x, text[:max_width], attr)


def visible_window(state: State, height: int, width: int) -> None:
    body_h = max(1, height - 2)
    gutter = gutter_width(state)
    text_w = max(1, width - gutter - 1)

    if state.cy < state.top:
        state.top = state.cy
    elif state.cy >= state.top + body_h:
        state.top = state.cy - body_h + 1

    if state.cx < state.left:
        state.left = state.cx
    elif state.cx >= state.left + text_w:
        state.left = state.cx - text_w + 1
    state.top = max(0, state.top)
    state.left = max(0, state.left)


def gutter_width(state: State) -> int:
    return 4


def draw_note(stdscr, state: State, y0: int, x0: int, height: int, width: int) -> None:
    body_h = max(1, height - 2)
    active = state.pane == "note"
    name = Path(state.filename).name if state.filename else "Untitled.md"
    title = f" {name}  {state.view.upper()} "
    title_attr = color(6, True) if active else color(7, True)
    safe_add(stdscr, y0, x0, title.ljust(width)[:width], title_attr)

    if state.view == "preview":
        content_w = max(1, width - 4)
        rendered = markdown_blocks(state.lines, content_w)
        state.top = max(0, min(state.top, max(0, len(rendered) - body_h)))
        for row in range(body_h):
            screen_y = y0 + 1 + row
            safe_add(stdscr, screen_y, x0, " " * width)
            idx = state.top + row
            if idx < len(rendered):
                draw_segments(stdscr, screen_y, x0 + 2, rendered[idx], content_w)
        status = f" PREVIEW {'*' if state.dirty else ' '}  Ctrl-E edit  :setup  :model"
        if state.status:
            status += f"  {state.status}"
        safe_add(stdscr, y0 + height - 1, x0, status.ljust(width)[:width], color(8))
        return

    visible_window(state, height, width)
    gutter = gutter_width(state)
    text_w = max(1, width - gutter - 1)
    for row in range(body_h):
        screen_y = y0 + 1 + row
        idx = state.top + row
        safe_add(stdscr, screen_y, x0, " " * width)

        if idx >= len(state.lines):
            safe_add(stdscr, screen_y, x0 + gutter - 2, "~", color(5))
            continue

        num = str(idx + 1).rjust(gutter - 2)
        attr = color(1, True) if idx == state.cy else color(5)
        safe_add(stdscr, screen_y, x0, f"{num} ", attr)
        safe_ch(stdscr, screen_y, x0 + gutter - 1, curses.ACS_VLINE, color(5))
        draw_markdown_source(stdscr, screen_y, x0 + gutter, state.lines[idx][state.left:], text_w)

    dirty = "*" if state.dirty else " "
    status = f" SOURCE {state.mode} {dirty}  {state.cy + 1}:{state.cx + 1}  Ctrl-E preview"
    if state.left:
        status += f"  col+{state.left}"
    if state.status:
        status += f"  {state.status}"
    safe_add(stdscr, y0 + height - 1, x0, status.ljust(width)[:width], color(8))


def draw_chat(stdscr, state: State, y0: int, x0: int, height: int, width: int) -> None:
    active = state.pane == "chat"
    model = state.ai_model.split("/")[-1]
    label = f" AI  {model[:24]}{'  working' if state.ai_busy else ''} "
    safe_add(stdscr, y0, x0, label.ljust(width)[:width], color(6, True) if active else color(7, True))

    body_h = max(1, height - 2)
    chat_lines = state.chat[-body_h:]
    for row in range(body_h):
        y = y0 + 1 + row
        safe_add(stdscr, y, x0, " " * width)
        if row < len(chat_lines):
            line = chat_lines[row]
            attr = color(2) if line.startswith("You:") else color(3) if line.startswith("AI:") else color(5)
            safe_add(stdscr, y, x0 + 1, line[: max(0, width - 2)], attr)

    prompt = "> " + state.chat_input
    if len(prompt) > width - 1:
        prompt = ">" + prompt[-(width - 2):]
    safe_add(stdscr, y0 + height - 1, x0, prompt.ljust(width)[:width], color(8) if active else color(7))


def draw(stdscr, state: State) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < 6 or width < 40:
        safe_add(stdscr, 0, 0, "Terminal is too small")
        stdscr.refresh()
        return

    chat_w = max(28, min(48, width // 3))
    note_w = width - chat_w - 1
    draw_note(stdscr, state, 0, 0, height, note_w)
    for y in range(height):
        safe_ch(stdscr, y, note_w, curses.ACS_VLINE, color(5))
    draw_chat(stdscr, state, 0, note_w + 1, height, chat_w)

    gutter = gutter_width(state)
    try:
        curses.curs_set(1 if state.pane == "chat" or state.view == "source" else 0)
        if state.pane == "note" and state.view == "source":
            cy = 1 + state.cy - state.top
            cx = gutter + state.cx - state.left
            stdscr.move(max(1, min(height - 2, cy)), max(gutter, min(note_w - 1, cx)))
        elif state.pane == "chat":
            stdscr.move(height - 1, min(width - 1, note_w + 3 + len(state.chat_input)))
        else:
            stdscr.move(height - 1, min(width - 1, note_w - 1))
    except curses.error:
        pass
    stdscr.refresh()


def move_cursor(state: State, dy: int = 0, dx: int = 0) -> None:
    state.cy = max(0, min(len(state.lines) - 1, state.cy + dy))
    state.cx = max(0, min(len(state.lines[state.cy]), state.cx + dx))


def find_next(state: State, query: str, direction: int = 1) -> bool:
    if not query:
        return False
    total = len(state.lines)
    for step in range(total):
        y = (state.cy + step * direction) % total
        line = state.lines[y]
        if direction > 0:
            start = state.cx + 1 if y == state.cy else 0
            x = line.find(query, start)
        else:
            end = state.cx if y == state.cy else len(line)
            x = line.rfind(query, 0, end)
        if x != -1:
            state.cy, state.cx = y, x
            return True
    return False


def prompt(stdscr, prefix: str) -> str:
    height, width = stdscr.getmaxyx()
    safe_add(stdscr, height - 1, 0, " " * width, color(8))
    safe_add(stdscr, height - 1, 0, prefix, color(8))
    curses.echo()
    try:
        value = stdscr.getstr(height - 1, len(prefix), max(1, width - len(prefix) - 1))
        return value.decode("utf-8", "replace").strip()
    finally:
        curses.noecho()


def command_matches(text: str) -> list[str]:
    head = text.split(maxsplit=1)[0] if text.strip() else ""
    return [cmd for cmd in COMMANDS if cmd.startswith(head)]


def complete_command(text: str) -> str:
    matches = command_matches(text)
    if not matches:
        return text
    head, sep, tail = text.partition(" ")
    if len(matches) == 1:
        return matches[0] + (" " + tail if sep else "")
    prefix = os.path.commonprefix(matches)
    return prefix + (" " + tail if sep else "")


def draw_command_popup(stdscr, text: str) -> None:
    matches = command_matches(text)[:8]
    if not matches:
        return
    height, width = stdscr.getmaxyx()
    box_w = min(width - 4, max(18, max(len(cmd) for cmd in matches) + 4))
    box_h = len(matches) + 2
    y = max(0, height - box_h - 1)
    x = 1
    draw_box(stdscr, y, x, box_h, box_w, "Commands")
    for i, cmd in enumerate(matches):
        safe_add(stdscr, y + 1 + i, x + 2, cmd.ljust(box_w - 4), color(8) if i == 0 else color(7))


def command_prompt(stdscr, state: State) -> str:
    text = ""
    while True:
        draw(stdscr, state)
        height, width = stdscr.getmaxyx()
        safe_add(stdscr, height - 1, 0, " " * width, color(8))
        draw_command_popup(stdscr, text)
        prompt_text = ":" + text
        safe_add(stdscr, height - 1, 0, prompt_text[:width], color(8))
        try:
            stdscr.move(height - 1, min(width - 1, len(prompt_text)))
        except curses.error:
            pass
        key = stdscr.getch()
        if key in (10, 13):
            return text.strip()
        if key == 27:
            return ""
        if key == 9:
            text = complete_command(text)
        elif key in (127, curses.KEY_BACKSPACE, 8):
            text = text[:-1]
        elif 32 <= key <= 126:
            text += chr(key)


def centered_rect(height: int, width: int, box_h: int, box_w: int) -> tuple[int, int, int, int]:
    box_h = min(box_h, max(6, height - 2))
    box_w = min(box_w, max(36, width - 4))
    return max(1, (height - box_h) // 2), max(2, (width - box_w) // 2), box_h, box_w


def draw_box(stdscr, y: int, x: int, h: int, w: int, title: str) -> None:
    for row in range(h):
        safe_add(stdscr, y + row, x, " " * w, color(7))
    safe_ch(stdscr, y, x, curses.ACS_ULCORNER, color(6, True))
    safe_ch(stdscr, y, x + w - 1, curses.ACS_URCORNER, color(6, True))
    safe_ch(stdscr, y + h - 1, x, curses.ACS_LLCORNER, color(6, True))
    safe_ch(stdscr, y + h - 1, x + w - 1, curses.ACS_LRCORNER, color(6, True))
    for col in range(1, w - 1):
        safe_ch(stdscr, y, x + col, curses.ACS_HLINE, color(6, True))
        safe_ch(stdscr, y + h - 1, x + col, curses.ACS_HLINE, color(6, True))
    for row in range(1, h - 1):
        safe_ch(stdscr, y + row, x, curses.ACS_VLINE, color(6, True))
        safe_ch(stdscr, y + row, x + w - 1, curses.ACS_VLINE, color(6, True))
    safe_add(stdscr, y, x + 2, f" {title} ", color(6, True))


def edit_field(stdscr, y: int, x: int, width: int, value: str, hidden: bool = False) -> str | None:
    curses.curs_set(1)
    text = value
    pos = len(text)
    while True:
        visible = "*" * len(text) if hidden and text else text
        if len(visible) > width:
            visible = visible[-width:]
        safe_add(stdscr, y, x, visible.ljust(width), color(8))
        stdscr.move(y, min(x + width - 1, x + min(pos, width - 1)))
        key = stdscr.getch()
        if key in (10, 13):
            return text
        if key == 27:
            return None
        if key in (127, curses.KEY_BACKSPACE, 8):
            if pos:
                text = text[:pos - 1] + text[pos:]
                pos -= 1
        elif key == curses.KEY_LEFT:
            pos = max(0, pos - 1)
        elif key == curses.KEY_RIGHT:
            pos = min(len(text), pos + 1)
        elif 32 <= key <= 126:
            text = text[:pos] + chr(key) + text[pos:]
            pos += 1


def read_url_json(url: str, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return False, str(exc)
    if isinstance(data, dict):
        count = len(data.get("data") or data.get("models") or [])
        return True, f"Connected ({count} models)" if count else "Connected"
    return True, "Connected"


def validate_ai_config(kind: str, base_url: str, api_key: str) -> tuple[bool, str]:
    base_url = base_url.rstrip("/")
    if kind == "anthropic":
        if not api_key:
            return False, "API key required"
        return read_url_json(
            f"{base_url}/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    if kind == "ollama":
        return read_url_json(f"{base_url}/api/tags")

    if not api_key:
        api_key = "not-needed"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=8)
        models = client.models.list()
        count = len(models.data or [])
        return True, f"Connected ({count} models)" if count else "Connected"
    except Exception as exc:
        return False, str(exc)


def provider_config(state: State, provider: str) -> tuple[str, str, str, str]:
    kind, base_url, model = PROVIDERS[provider]
    api_key = ""
    if provider == state.ai_provider:
        return state.ai_kind, state.ai_base_url, state.ai_api_key, state.ai_model
    if provider in {"LM Studio (local)", "Ollama (local)"}:
        api_key = "local"
    return kind, base_url, api_key, model


def fetch_models(kind: str, base_url: str, api_key: str) -> tuple[bool, list[str], str]:
    base_url = base_url.rstrip("/")
    try:
        if kind == "anthropic":
            if not api_key:
                return False, [], "API key required. Use :setup first."
            request = Request(
                f"{base_url}/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            with urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            models = [item.get("id", "") for item in data.get("data", []) if item.get("id")]
        elif kind == "ollama":
            request = Request(f"{base_url}/api/tags")
            with urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            models = [item.get("name", "") for item in data.get("models", []) if item.get("name")]
        else:
            client = OpenAI(api_key=api_key or "not-needed", base_url=base_url, timeout=8)
            models = [model.id for model in client.models.list().data]
    except Exception as exc:
        return False, [], str(exc)
    return bool(models), sorted(models), f"{len(models)} models" if models else "No models found. Use :setup first."


def draw_setup_menu(stdscr, state: State, selected: int, field: int, message: str) -> tuple[int, int, int, int]:
    draw(stdscr, state)
    height, width = stdscr.getmaxyx()
    y, x, h, w = centered_rect(height, width, 21, 76)
    draw_box(stdscr, y, x, h, w, "AI Setup")
    safe_add(stdscr, y + 2, x + 3, "Provider", color(1, True))
    safe_add(stdscr, y + 2, x + 28, "Connection", color(1, True))

    names = list(PROVIDERS)
    for i, name in enumerate(names):
        marker = ">" if i == selected else " "
        attr = color(8) if field == 0 and i == selected else color(7)
        safe_add(stdscr, y + 4 + i, x + 3, f"{marker} {name}".ljust(22), attr)

    labels = ["API key", "Base URL", "Model"]
    values = [state.ai_api_key, state.ai_base_url, state.ai_model]
    for i, label in enumerate(labels):
        row = y + 4 + i * 3
        attr = color(6, True) if field == i + 1 else color(7, True)
        safe_add(stdscr, row, x + 28, label, attr)
        value = values[i]
        if label == "API key" and value and state.ai_provider not in {"LM Studio (local)", "Ollama (local)"}:
            value = value[:4] + "*" * max(0, min(22, len(value) - 4))
        safe_add(stdscr, row + 1, x + 28, value[: w - 32].ljust(w - 32), color(8) if field == i + 1 else color(7))

    msg_attr = color(3) if message.startswith("Connected") else color(9) if message.startswith("Invalid") else color(2)
    safe_add(stdscr, y + h - 5, x + 3, message[: w - 6].ljust(w - 6), msg_attr)
    safe_add(stdscr, y + h - 3, x + 3, "Arrows/Tab move  Enter edit  T test  S save  Esc cancel", color(5))
    stdscr.refresh()
    return y, x, h, w


def setup_menu(stdscr, state: State) -> None:
    names = list(PROVIDERS)
    selected = names.index(state.ai_provider) if state.ai_provider in names else 0
    field = 0
    message = "Select a provider, fill the fields, then press T to test."
    old_config = (state.ai_provider, state.ai_kind, state.ai_api_key, state.ai_base_url, state.ai_model)

    def apply_provider(name: str) -> None:
        kind, base_url, model = PROVIDERS[name]
        state.ai_provider = name
        state.ai_kind = kind
        state.ai_base_url = base_url
        state.ai_model = model
        if name in {"LM Studio (local)", "Ollama (local)"}:
            state.ai_api_key = "local"

    if state.ai_provider not in names:
        apply_provider(names[selected])
    while True:
        y, x, _, w = draw_setup_menu(stdscr, state, selected, field, message)
        key = stdscr.getch()
        if key in (27,):
            state.ai_provider, state.ai_kind, state.ai_api_key, state.ai_base_url, state.ai_model = old_config
            state.status = "AI setup cancelled"
            return
        if key in (9, curses.KEY_RIGHT):
            field = (field + 1) % 4
        elif key == curses.KEY_LEFT:
            field = (field - 1) % 4
        elif key in (curses.KEY_UP, ord("k")):
            if field == 0:
                selected = (selected - 1) % len(names)
                apply_provider(names[selected])
        elif key in (curses.KEY_DOWN, ord("j")):
            if field == 0:
                selected = (selected + 1) % len(names)
                apply_provider(names[selected])
        elif key in (10, 13):
            if field == 0:
                selected = (selected + 1) % len(names)
                apply_provider(names[selected])
            else:
                row = y + 5 + (field - 1) * 3
                value = [state.ai_api_key, state.ai_base_url, state.ai_model][field - 1]
                edited = edit_field(stdscr, row, x + 28, w - 32, value, hidden=field == 1)
                if edited is not None:
                    if field == 1:
                        state.ai_api_key = edited
                    elif field == 2:
                        state.ai_base_url = edited.rstrip("/")
                    elif field == 3:
                        state.ai_model = edited
                    message = "Updated. Press T to test."
        elif key in (ord("t"), ord("T")):
            message = "Testing connection..."
            draw_setup_menu(stdscr, state, selected, field, message)
            ok, detail = validate_ai_config(state.ai_kind, state.ai_base_url, state.ai_api_key)
            message = detail if ok else f"Invalid: {detail}"
        elif key in (ord("s"), ord("S")):
            ok, detail = validate_ai_config(state.ai_kind, state.ai_base_url, state.ai_api_key)
            if ok:
                saved, save_message = save_config(state)
                state.status = f"AI setup saved: {state.ai_provider}" if saved else save_message
                state.chat.append(f"AI: {state.ai_provider} configured")
                return
            message = f"Invalid: {detail}"


def draw_model_menu(
    stdscr,
    state: State,
    provider_idx: int,
    model_idx: int,
    models: list[str],
    message: str,
) -> tuple[int, int, int, int]:
    draw(stdscr, state)
    height, width = stdscr.getmaxyx()
    y, x, h, w = centered_rect(height, width, 22, 82)
    draw_box(stdscr, y, x, h, w, "Model Selection")
    safe_add(stdscr, y + 2, x + 3, "Providers", color(1, True))
    safe_add(stdscr, y + 2, x + 29, "Models", color(1, True))

    names = list(PROVIDERS)
    for i, name in enumerate(names):
        marker = ">" if i == provider_idx else " "
        attr = color(8) if i == provider_idx else color(7)
        suffix = " *" if name == state.ai_provider else ""
        safe_add(stdscr, y + 4 + i, x + 3, f"{marker} {name}{suffix}".ljust(23), attr)

    list_h = h - 8
    if models:
        start = max(0, model_idx - list_h + 1)
        for row, model in enumerate(models[start:start + list_h]):
            idx = start + row
            marker = ">" if idx == model_idx else " "
            attr = color(8) if idx == model_idx else color(7)
            current = " *" if names[provider_idx] == state.ai_provider and model == state.ai_model else ""
            safe_add(stdscr, y + 4 + row, x + 29, f"{marker} {model}{current}"[: w - 33].ljust(w - 33), attr)
    else:
        safe_add(stdscr, y + 4, x + 29, "No models available.".ljust(w - 33), color(9))
        safe_add(stdscr, y + 5, x + 29, "Use :setup to configure this provider.".ljust(w - 33), color(2))

    msg_attr = color(3) if models else color(2)
    safe_add(stdscr, y + h - 4, x + 3, message[: w - 6].ljust(w - 6), msg_attr)
    safe_add(stdscr, y + h - 2, x + 3, "Up/Down model  Left/Right provider  Enter select  R reload  Esc close", color(5))
    stdscr.refresh()
    return y, x, h, w


def model_menu(stdscr, state: State) -> None:
    names = list(PROVIDERS)
    provider_idx = names.index(state.ai_provider) if state.ai_provider in names else 0
    model_idx = 0
    cache: dict[str, tuple[bool, list[str], str]] = {}

    def load(provider: str) -> tuple[list[str], str]:
        if provider not in cache:
            cache[provider] = fetch_models(*provider_config(state, provider)[:3])
        ok, models, detail = cache[provider]
        return models, detail if ok else f"No models: {detail}"

    models, message = load(names[provider_idx])
    if state.ai_model in models:
        model_idx = models.index(state.ai_model)

    while True:
        draw_model_menu(stdscr, state, provider_idx, model_idx, models, message)
        key = stdscr.getch()
        if key == 27:
            state.status = "Model selection closed"
            return
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            step = -1 if key == curses.KEY_LEFT else 1
            provider_idx = (provider_idx + step) % len(names)
            model_idx = 0
            models, message = load(names[provider_idx])
            if state.ai_provider == names[provider_idx] and state.ai_model in models:
                model_idx = models.index(state.ai_model)
        elif key in (curses.KEY_UP, ord("k")) and models:
            model_idx = max(0, model_idx - 1)
        elif key in (curses.KEY_DOWN, ord("j")) and models:
            model_idx = min(len(models) - 1, model_idx + 1)
        elif key in (ord("r"), ord("R")):
            cache.pop(names[provider_idx], None)
            models, message = load(names[provider_idx])
            model_idx = 0
        elif key in (10, 13):
            if not models:
                message = "No models available. Run :setup first."
                continue
            provider = names[provider_idx]
            kind, base_url, api_key, _ = provider_config(state, provider)
            state.ai_provider = provider
            state.ai_kind = kind
            state.ai_base_url = base_url
            state.ai_api_key = api_key
            state.ai_model = models[model_idx]
            saved, detail = save_config(state)
            state.status = f"Model selected: {state.ai_model}" if saved else detail
            state.chat.append(f"AI: model set to {state.ai_model}")
            return


def run_command(stdscr, state: State) -> bool:
    command = command_prompt(stdscr, state)
    if not command:
        return False
    parts = command.split()
    cmd = parts[0]

    if cmd in {"q", "quit"}:
        return True
    if cmd in {"w", "write"}:
        save_file(state, parts[1] if len(parts) > 1 else None)
    elif cmd in {"wq", "x"}:
        save_file(state, parts[1] if len(parts) > 1 else None)
        return not state.status.startswith("Save failed") and not state.status.startswith("No file")
    elif cmd == "setup":
        setup_menu(stdscr, state)
    elif cmd == "model":
        model_menu(stdscr, state)
    else:
        state.status = f"Unknown command: {cmd}"
    return False


def ai_client(state: State) -> tuple[OpenAI, str]:
    if state.ai_kind == "ollama":
        client = OpenAI(api_key="ollama", base_url=state.ai_base_url.rstrip("/") + "/v1")
        return client, state.ai_model
    client = OpenAI(api_key=state.ai_api_key or "not-needed", base_url=state.ai_base_url)
    if state.ai_model != MODEL_NAME:
        return client, state.ai_model
    models = client.models.list()
    model = models.data[0].id if models.data else MODEL_NAME
    return client, model


def markdown_system_prompt() -> str:
    return (
        "You are a meticulous Markdown note editor and writing assistant. "
        "Improve clarity, structure, and usefulness while preserving the user's intent. "
        "Use clean Markdown with meaningful headings, concise paragraphs, lists, task lists, "
        "tables, quotes, and code fences only when they help the note. "
        "Return only the complete revised Markdown document. Do not add explanations."
    )


def clean_ai_markdown(text: str) -> str:
    stripped = text.strip("\n")
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip("\n")
    return stripped


def anthropic_edit(state: State, message: str, note: str) -> str:
    if not state.ai_api_key:
        raise RuntimeError("Anthropic API key required")
    payload = json.dumps({
        "model": state.ai_model,
        "max_tokens": 8192,
        "temperature": 0.2,
        "system": markdown_system_prompt(),
        "messages": [
            {"role": "user", "content": f"CURRENT MARKDOWN NOTE:\n{note}\n\nREQUEST:\n{message}"}
        ],
    }).encode("utf-8")
    request = Request(
        state.ai_base_url.rstrip("/") + "/messages",
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": state.ai_api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    parts = data.get("content", [])
    return "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")


def ai_worker(state: State, message: str, note: str) -> None:
    state.ai_busy = True
    state.chat.append(f"You: {message}")
    try:
        if state.ai_kind == "anthropic":
            state.chat.append(f"AI: Editing with {state.ai_model}")
            new_code = anthropic_edit(state, message, note)
        else:
            client, model = ai_client(state)
            state.ai_model = model
            state.chat.append(f"AI: Editing with {model}")
            response = client.chat.completions.create(
                model=model,
                temperature=0.2,
                messages=[
                    {
                        "role": "system",
                        "content": markdown_system_prompt(),
                    },
                    {"role": "user", "content": f"CURRENT MARKDOWN NOTE:\n{note}\n\nREQUEST:\n{message}"},
                ],
            )
            new_code = response.choices[0].message.content or ""
        new_code = clean_ai_markdown(new_code)
        if new_code:
            state.snapshot()
            state.lines = new_code.splitlines() or [""]
            state.clamp_cursor()
            state.chat.append("AI: Applied changes")
            state.status = "AI changes applied"
        else:
            state.chat.append("AI: Empty response")
    except Exception as exc:
        state.chat.append(f"AI error: {exc}")
        state.status = "AI unavailable"
    finally:
        state.ai_busy = False


def submit_chat(state: State) -> None:
    message = state.chat_input.strip()
    if not message or state.ai_busy:
        return
    state.chat_input = ""
    note = "\n".join(state.lines)
    thread = threading.Thread(target=ai_worker, args=(state, message, note), daemon=True)
    thread.start()


def handle_chat_key(state: State, key: int) -> None:
    if key in (10, 13):
        submit_chat(state)
    elif key in (27,):
        state.pane = "note"
        state.status = "Markdown pane"
    elif key in (127, curses.KEY_BACKSPACE, 8):
        state.chat_input = state.chat_input[:-1]
    elif 32 <= key <= 126:
        state.chat_input += chr(key)


def normal_key(stdscr, state: State, key: int) -> bool:
    if key == 5:
        state.view = "source" if state.view == "preview" else "preview"
        state.mode = "NORMAL"
        state.top = 0
        state.status = "Plaintext edit mode" if state.view == "source" else "Markdown preview"
    elif state.view == "preview":
        height, width = stdscr.getmaxyx()
        chat_w = max(28, min(48, width // 3))
        note_w = width - chat_w - 1
        body_h = max(1, height - 2)
        rendered_len = len(markdown_blocks(state.lines, max(1, note_w - 4)))
        if key in (ord("j"), curses.KEY_DOWN):
            state.top = min(max(0, rendered_len - body_h), state.top + 1)
        elif key in (ord("k"), curses.KEY_UP):
            state.top = max(0, state.top - 1)
        elif key in (ord(" "), curses.KEY_NPAGE):
            state.top = min(max(0, rendered_len - body_h), state.top + body_h)
        elif key == curses.KEY_PPAGE:
            state.top = max(0, state.top - body_h)
        elif key == ord("i"):
            state.view = "source"
            state.mode = "INSERT"
            state.insert_snapshot = False
            state.status = "Plaintext edit mode"
        elif key == ord("/"):
            query = prompt(stdscr, "/")
            if query:
                state.last_search = query
                state.status = f"Found {query}" if find_next(state, query) else f"Not found: {query}"
        elif key == ord(":"):
            return run_command(stdscr, state)
        elif key == ord("q"):
            state.status = "Use :q to quit"
    elif key in (ord("i"),):
        state.mode = "INSERT"
        state.insert_snapshot = False
        state.status = ""
    elif key in (ord("h"), curses.KEY_LEFT):
        move_cursor(state, dx=-1)
    elif key in (ord("l"), curses.KEY_RIGHT):
        move_cursor(state, dx=1)
    elif key in (ord("k"), curses.KEY_UP):
        move_cursor(state, dy=-1)
    elif key in (ord("j"), curses.KEY_DOWN):
        move_cursor(state, dy=1)
    elif key == ord("0"):
        state.cx = 0
    elif key == ord("$"):
        state.cx = len(state.lines[state.cy])
    elif key == ord("u") or key == 26:
        state.undo()
    elif key == ord("p") and state.clipboard is not None:
        state.snapshot()
        state.lines.insert(state.cy + 1, state.clipboard)
        state.cy += 1
        state.cx = min(state.cx, len(state.lines[state.cy]))
        state.status = "Pasted"
    elif key in (ord("y"), ord("d")):
        second = stdscr.getch()
        if key == ord("y") and second == ord("y"):
            state.clipboard = state.lines[state.cy]
            state.status = "Yanked line"
        elif key == ord("d") and second == ord("d"):
            state.snapshot()
            state.clipboard = state.lines.pop(state.cy)
            if not state.lines:
                state.lines = [""]
            state.clamp_cursor()
            state.status = "Deleted line"
    elif key == ord("/"):
        query = prompt(stdscr, "/")
        if query:
            state.last_search = query
            state.status = f"Found {query}" if find_next(state, query) else f"Not found: {query}"
    elif key in (ord("n"), ord("N"), ord("b")):
        direction = -1 if key in (ord("N"), ord("b")) else 1
        state.status = (
            f"Found {state.last_search}"
            if find_next(state, state.last_search, direction)
            else f"Not found: {state.last_search or '?'}"
        )
    elif key == ord(":"):
        return run_command(stdscr, state)
    elif key == ord("q"):
        state.status = "Use :q to quit"
    return False


def insert_key(state: State, key: int) -> None:
    line = state.lines[state.cy]
    def snapshot_once() -> None:
        if not state.insert_snapshot:
            state.snapshot()
            state.insert_snapshot = True

    if key == 27:
        state.mode = "NORMAL"
        state.insert_snapshot = False
    elif key == 9:
        snapshot_once()
        state.lines[state.cy] = line[:state.cx] + TAB + line[state.cx:]
        state.cx += len(TAB)
    elif key in (10, 13):
        snapshot_once()
        state.lines[state.cy] = line[:state.cx]
        state.lines.insert(state.cy + 1, line[state.cx:])
        state.cy += 1
        state.cx = 0
    elif key in (127, curses.KEY_BACKSPACE, 8):
        if state.cx:
            snapshot_once()
            width = len(TAB) if line[:state.cx].endswith(TAB) else 1
            state.lines[state.cy] = line[: state.cx - width] + line[state.cx:]
            state.cx -= width
        elif state.cy:
            snapshot_once()
            state.cx = len(state.lines[state.cy - 1])
            state.lines[state.cy - 1] += state.lines.pop(state.cy)
            state.cy -= 1
    elif key == curses.KEY_LEFT:
        move_cursor(state, dx=-1)
    elif key == curses.KEY_RIGHT:
        move_cursor(state, dx=1)
    elif key == curses.KEY_UP:
        move_cursor(state, dy=-1)
    elif key == curses.KEY_DOWN:
        move_cursor(state, dy=1)
    elif 0 <= key <= 0x10FFFF:
        try:
            char = chr(key)
        except ValueError:
            return
        if char.isprintable():
            snapshot_once()
            state.lines[state.cy] = line[:state.cx] + char + line[state.cx:]
            state.cx += len(char)


def handle_mouse(state: State, key: int, height: int, note_w: int) -> bool:
    if key != curses.KEY_MOUSE:
        return False
    try:
        _, mx, my, _, _ = curses.getmouse()
    except curses.error:
        return True
    state.pane = "chat" if mx > note_w else "note"
    if state.pane == "note" and state.view == "source" and 0 < my < height - 1:
        gutter = gutter_width(state)
        state.cy = min(len(state.lines) - 1, state.top + my - 1)
        state.cx = max(0, min(len(state.lines[state.cy]), state.left + mx - gutter))
    return True


def main(stdscr) -> None:
    curses.curs_set(1)
    curses.raw()
    curses.noecho()
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    stdscr.keypad(True)
    setup_colors()

    state = State()
    load_config(state)
    if len(sys.argv) > 1:
        state.filename = sys.argv[1]
        state.lines, state.status = load_file(state.filename)

    while True:
        draw(stdscr, state)
        height, width = stdscr.getmaxyx()
        chat_w = max(28, min(48, width // 3))
        note_w = width - chat_w - 1
        stdscr.timeout(100 if state.pane == "chat" or state.ai_busy else -1)
        key = stdscr.getch()
        if key == -1:
            continue

        if key == 23:
            state.pane = "chat" if state.pane == "note" else "note"
            state.status = "Chat pane" if state.pane == "chat" else "Markdown pane"
            continue
        if key == 5 and state.pane == "note":
            state.view = "source" if state.view == "preview" else "preview"
            state.mode = "NORMAL"
            state.top = 0
            state.status = "Plaintext edit mode" if state.view == "source" else "Markdown preview"
            continue
        if handle_mouse(state, key, height, note_w):
            continue
        if state.pane == "chat":
            handle_chat_key(state, key)
            continue
        if state.mode == "NORMAL":
            if normal_key(stdscr, state, key):
                return
        elif state.mode == "INSERT":
            insert_key(state, key)
        state.clamp_cursor()


if __name__ == "__main__":
    curses.wrapper(main)
