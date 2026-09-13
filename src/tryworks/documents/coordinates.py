"""Coordinate systems for element positions, API-compatible with ``unstructured.documents.coordinates``."""

from __future__ import annotations

from enum import Enum
from typing import Union


class Orientation(Enum):
    """Axis directions as (x, y) signs relative to a bottom-left origin."""

    SCREEN = (1, -1)  # -- origin top-left, y grows downward (pixels) --
    CARTESIAN = (1, 1)  # -- origin bottom-left, y grows upward (PDF points) --


def convert_coordinate(old_t: float, old_t_max: float, new_t_max: float, t_orientation: int) -> float:
    """Map one axis value between systems of different extent and direction."""
    fraction = old_t / old_t_max
    if t_orientation < 0:
        fraction = 1 - fraction
    return fraction * new_t_max


class CoordinateSystem:
    """A rectangular coordinate space of ``width`` x ``height``."""

    orientation: Orientation

    def __init__(self, width: Union[int, float], height: Union[int, float]):
        self.width = width
        self.height = height

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CoordinateSystem)
            and type(self) is type(other)
            and self.width == other.width
            and self.height == other.height
        )

    def convert_from_relative(self, x: float, y: float) -> tuple[float, float]:
        """Convert (x, y) from the unit square (cartesian) into this system."""
        x_sign, y_sign = self.orientation.value
        return convert_coordinate(x, 1, self.width, x_sign), convert_coordinate(y, 1, self.height, y_sign)

    def convert_to_relative(self, x: float, y: float) -> tuple[float, float]:
        """Convert (x, y) from this system into the unit square (cartesian)."""
        x_sign, y_sign = self.orientation.value
        return convert_coordinate(x, self.width, 1, x_sign), convert_coordinate(y, self.height, 1, y_sign)

    def convert_coordinates_to_new_system(
        self, new_system: CoordinateSystem, x: float, y: float
    ) -> tuple[float, float]:
        rel_x, rel_y = self.convert_to_relative(x, y)
        return new_system.convert_from_relative(rel_x, rel_y)


class RelativeCoordinateSystem(CoordinateSystem):
    """The unit square with a cartesian orientation."""

    orientation = Orientation.CARTESIAN

    def __init__(self):
        super().__init__(width=1, height=1)


class PixelSpace(CoordinateSystem):
    """Image-style coordinates: origin top-left, y downward."""

    orientation = Orientation.SCREEN


class PointSpace(CoordinateSystem):
    """PDF-style coordinates: origin bottom-left, y upward."""

    orientation = Orientation.CARTESIAN


SYSTEM_BY_NAME = {cls.__name__: cls for cls in (PixelSpace, PointSpace, RelativeCoordinateSystem)}
