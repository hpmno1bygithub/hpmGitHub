# -*- coding: utf-8 -*-
"""Minimal UIKit -> pyto_ui wrapper adapted from Pyto's objc_view example."""
from time import sleep
import pyto_ui as ui
from UIKit import UIView
from mainthread import mainthread


class WrapperView(ui.View):
    objc_class = UIView

    def __init__(self):
        super().__init__()
        self.__setup_finished__ = False
        self._make_view()

        deadline = __import__('time').monotonic() + 5.0
        while not self.__setup_finished__:
            if __import__('time').monotonic() > deadline:
                raise RuntimeError('UIKit wrapper setup timeout')
            sleep(0.02)

        self._configure_view(self.__py_view__.managed)

    @property
    def objc_view(self):
        return self.__py_view__.managed

    @mainthread
    def _make_view(self):
        self.__py_view__.managed = self.objc_class.new()
        self.__setup_finished__ = True

    @mainthread
    def _configure_view(self, view):
        self.configure_view(view)

    def configure_view(self, view):
        pass
