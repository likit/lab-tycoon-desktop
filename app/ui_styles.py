"""Shared visual styles for Lab Tycoon windows."""

DANGER_BUTTON_COLOR = ("white", "#dc2626")


def bring_window_to_front(window) -> None:
    """Raise and focus a finalized window once Tk has mapped it."""
    def activate() -> None:
        if window.TKroot.winfo_exists():
            window.TKroot.deiconify()
            window.TKroot.lift()
            window.TKroot.focus_force()

    window.refresh()
    activate()
    window.TKroot.after_idle(activate)
