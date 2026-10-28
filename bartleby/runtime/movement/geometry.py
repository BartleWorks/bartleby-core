"""Angles and distances the way the game measures them.

wizwalker's yaw: 0 faces -y, and it turns counterclockwise (``calculate_perfect_yaw``).
Walking is horizontal only; a stair's height is free, so distances are in x and y.
"""

from __future__ import annotations

import math

Point = tuple[float, float, float]


def wrap(angle: float) -> float:
    """An angle in (-pi, pi]."""
    return (angle + math.pi) % math.tau - math.pi


def heading_to(origin: Point, target: Point) -> float:
    """The yaw that faces *target* from *origin*."""
    return math.atan2(-(target[0] - origin[0]), -(target[1] - origin[1])) % math.tau


def facing(yaw: float) -> tuple[float, float]:
    """The unit step a wizard with this yaw takes walking forward."""
    return -math.sin(yaw), -math.cos(yaw)


def flat_distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def offset_from(origin: Point, yaw: float, angle: float, radius: float) -> Point:
    """The point *radius* from *origin* at *angle* off the way *yaw* faces (pi is straight behind)."""
    dx, dy = facing(yaw + angle)
    return origin[0] + dx * radius, origin[1] + dy * radius, origin[2]
