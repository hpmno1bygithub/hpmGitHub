# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod


class Renderer(ABC):
    """Rendering contract.

    Gameplay code depends on this interface, not on PytoUI/Metal.
    A later Metal renderer can replace the debug renderer without
    rewriting Player / Physics / World.
    """

    @abstractmethod
    def render(self, game):
        raise NotImplementedError

    @abstractmethod
    def close(self):
        raise NotImplementedError
