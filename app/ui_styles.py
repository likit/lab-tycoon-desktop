"""Shared styles and window activation for Lab Tycoon."""
from weakref import ref
import tkinter as tk

import FreeSimpleGUI as sg

DANGER_BUTTON_COLOR = ("white", "#dc2626")
_windows = []
_window_generation = 0


def table_window_layout(layout, screen_size):
    """Bound table dialogs without putting their controls inside a scroll area."""
    def tables(rows):
        for row in rows:
            for element in row:
                if getattr(element, 'Type', None) == sg.ELEM_TYPE_TABLE:
                    yield element
                children = getattr(element, 'Rows', None)
                if children:
                    yield from tables(children)

    found = list(tables(layout))
    if not found:
        return layout, {}
    screen_width, screen_height = screen_size
    width = min(1100, max(1, int(screen_width) - 80))
    height = min(750, max(1, int(screen_height) - 120))
    for table in found:
        table.VerticalScrollOnly = False
        # Do not let a large row count consume the entire scrollable body.
        table.NumRows = min(table.NumRows or 12, 12)
    return layout, dict(size=(width, height), resizable=True,
        location=((int(screen_width) - width) // 2, (int(screen_height) - height) // 2))


def pin_table_controls(layout):
    """Reserve footer rows before expandable tables consume the available height."""
    def contains_table(rows):
        return any(getattr(element, 'Type', None) == sg.ELEM_TYPE_TABLE or
                   contains_table(getattr(element, 'Rows', []) or [])
                   for row in rows for element in row)

    table_rows = [i for i, row in enumerate(layout) if contains_table([row])]
    if table_rows:
        anchor = getattr(layout[table_rows[0]][0], 'ParentRowFrame', None)
        if anchor is not None:
            for row in reversed(layout[table_rows[-1] + 1:]):
                if row:
                    frame = getattr(row[0], 'ParentRowFrame', None)
                    if frame is not None:
                        frame.pack_configure(side='bottom', before=anchor)
    for row in layout:
        for element in row:
            children = getattr(element, 'Rows', None)
            if children:
                pin_table_controls(children)
            tree = getattr(element, 'TKTreeview', None)
            if getattr(element, 'Type', None) == sg.ELEM_TYPE_TABLE and tree is not None:
                for column in tree['columns']:
                    tree.column(column, stretch=False)


def _visible(root):
    try:
        return root is not None and root.winfo_exists() and root.winfo_viewable()
    except tk.TclError:
        return False


def bring_window_to_front(window) -> None:
    """Activate once after mapping, without keeping the window above other apps."""
    generation = _window_generation
    def activate():
        if generation != _window_generation:
            return
        root = window.TKroot
        try:
            if root is not None and root.winfo_exists():
                root.deiconify()
                root.wm_attributes('-topmost',
                                   int(getattr(window, '_lab_always_on_top', False) is True))
                root.lift()
                root.focus_force()
        except tk.TclError:
            pass  # The user may close a window before the idle callback runs.

    window.refresh()
    activate()
    if window.TKroot is not None:
        window.TKroot.after_idle(activate)


def active_window(*, role=None):
    """Find the visible workspace before opening a menu's intermediate dialogs."""
    global _windows
    _windows = [item for item in _windows if item() is not None]
    candidates = [item() for item in reversed(_windows)
                  if _visible(item().TKroot)
                  and (role is None or getattr(item(), '_lab_window_role', None) == role)]
    for candidate in candidates:
        try:
            focused = candidate.TKroot.focus_get()
            if focused is not None and focused.winfo_toplevel() == candidate.TKroot:
                return candidate
        except tk.TclError:
            continue
    return candidates[0] if candidates else None


def create_window(*args, parent=None, always_on_top=False, window_role=None,
                  independent=False, **kwargs):
    """Create a mapped window, associate its owner, and restore focus on close."""
    global _window_generation
    # Wide tables can exceed the desktop's logical size on high-DPI Windows.
    # Reserve footer rows instead of scrolling the entire window.
    layout = kwargs.get('layout') if 'layout' in kwargs else (args[1] if len(args) > 1 else None)
    if layout is not None:
        bounded_layout, options = table_window_layout(layout, sg.Window.get_screen_size())
        if options:
            if 'layout' in kwargs:
                kwargs['layout'] = bounded_layout
            else:
                args = (args[0], bounded_layout, *args[2:])
            kwargs.update(options)
    # Finalizing a new window can process idle callbacks from a closed dialog.
    # Invalidate those callbacks before construction so they cannot cover it.
    _window_generation += 1
    if independent:
        parent = None
    elif parent is None or not _visible(parent.TKroot):
        parent = active_window()
    kwargs['finalize'] = True
    always_on_top = always_on_top or getattr(parent, '_lab_always_on_top', False) is True
    kwargs['keep_on_top'] = always_on_top
    window = sg.Window(*args, **kwargs)
    window._lab_always_on_top = always_on_top
    window._lab_window_role = window_role
    root = window.TKroot
    if layout is not None:
        pin_table_controls(layout)
    if parent is not None and _visible(parent.TKroot):
        root.transient(parent.TKroot)
        parent_ref = ref(parent)

        def restore_parent(event):
            if event.widget is not root:
                return
            owner = parent_ref()
            if owner is not None and _visible(owner.TKroot):
                # Let Tk release the child's modal grab before focusing its owner.
                generation = _window_generation
                owner.TKroot.after_idle(lambda: bring_window_to_front(owner)
                                        if generation == _window_generation and _visible(owner.TKroot) else None)

        root.bind('<Destroy>', restore_parent, add='+')
    _windows.append(ref(window))
    bring_window_to_front(window)
    return window
