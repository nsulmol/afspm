"""Holds UI-based Afspm Component."""

import logging
import tkinter as tk


from .component import AfspmComponent


logger = logging.getLogger(__name__)


class AfspmComponentUI(AfspmComponent):
    """Component with tkinter ui.

    AfspmComponentUI adds a 'root' tkinter interface for creating user
    interfaces. It hooks into the standard tkinter even loop via mainloop(),
    calling self._per_loop_step() every self.loop_sleep_s period (the same
    rough logic as AfspmComponentBase). With it, one can develop components
    with simple user interfaces that rest on top of standard AfspmComponent
    logic.

    For instantiation of the UI, one should use self._create_ui(), which is
    automatically called at construction.

    Attributes:
        root: the base tkinter Tk() instance.
    """

    def __init__(self, **kwargs):
        """Initialize our UI class."""
        self.root = tk.Tk()
        self._create_ui()
        super().__init__(**kwargs)
        self._register_loop_step()

    def run(self):
        """Override main loop.

        Since we are using tkinter, we call mainloop() instead.
        """
        self.root.mainloop()

    def _per_loop_step(self):
        """Override to destroy UI if we are no longer suppoed to be alive."""
        if self.stay_alive:
            super()._per_loop_step()
            self._register_loop_step()
        else:
            self.root.destroy()

    def _create_ui(self):
        """Set up the tkinter UI."""
        pass

    def _register_loop_step(self):
        """Call per-loop-step after a sleep period."""
        self.root.after(int(self.loop_sleep_s * 1000), self._per_loop_step)
