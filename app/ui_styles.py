"""Shared styles and window activation for Lab Tycoon."""
from weakref import ref
import tkinter as tk

import FreeSimpleGUI as sg

DANGER_BUTTON_COLOR = ("white", "#dc2626")
_windows = []


def _visible(root):
    try:
        return root is not None and root.winfo_exists() and root.winfo_viewable()
    except tk.TclError:
        return False


def bring_window_to_front(window) -> None:
    """Activate once after mapping, without keeping the window above other apps."""
    def activate():
        root = window.TKroot
        try:
            if root is not None and root.winfo_exists():
                root.deiconify()
                root.lift()
                root.focus_force()
        except tk.TclError:
            pass  # The user may close a window before the idle callback runs.

    window.refresh()
    activate()
    if window.TKroot is not None:
        window.TKroot.after_idle(activate)


def create_window(*args, parent=None, **kwargs):
    """Create a mapped window, associate its owner, and restore focus on close."""
    global _windows
    _windows = [item for item in _windows if item() is not None]
    candidates = [item() for item in reversed(_windows)
                  if _visible(item().TKroot)]
    if parent is None and candidates:
        # Prefer the window handling the action, then the most recent visible one.
        parent = candidates[0]
        for candidate in candidates:
            try:
                focused = candidate.TKroot.focus_get()
                if focused is not None and focused.winfo_toplevel() == candidate.TKroot:
                    parent = candidate
                    break
            except tk.TclError:
                continue
    kwargs['finalize'] = True
    kwargs['keep_on_top'] = False
    window = sg.Window(*args, **kwargs)
    root = window.TKroot
    if parent is not None and _visible(parent.TKroot):
        root.transient(parent.TKroot)
        parent_ref = ref(parent)

        def restore_parent(event):
            if event.widget is not root:
                return
            owner = parent_ref()
            if owner is not None and _visible(owner.TKroot):
                # Let Tk release the child's modal grab before focusing its owner.
                owner.TKroot.after_idle(lambda: bring_window_to_front(owner)
                                        if _visible(owner.TKroot) else None)

        root.bind('<Destroy>', restore_parent, add='+')
    _windows.append(ref(window))
    bring_window_to_front(window)
    return window
