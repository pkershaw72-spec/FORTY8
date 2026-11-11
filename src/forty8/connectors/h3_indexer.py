"""Real H3 indexing using the open-source h3 library."""

from __future__ import annotations

import h3


class H3LibIndexer:
    def __init__(self, options: dict | None = None):
        pass

    def cell(self, latitude: float, longitude: float, resolution: int) -> str:
        return h3.latlng_to_cell(latitude, longitude, resolution)
