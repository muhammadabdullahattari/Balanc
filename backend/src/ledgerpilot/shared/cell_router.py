"""G3: Regional data residency cell router for multi-cell database & object storage isolation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from ledgerpilot.config import Settings
from ledgerpilot.shared.errors import DomainError

SUPPORTED_CELLS: tuple[str, ...] = (
    "us-east-va-cell-1",
    "eu-central-fra-cell-1",
    "me-central-dxb-cell-1",
    "ap-south-khi-cell-1",
)


@dataclass(frozen=True)
class CellEndpoint:
    cell_id: str
    database_url: str
    storage_prefix: str


class CellRouter:
    """Resolve tenant-pinned regional cells for DB connections and object storage."""

    def __init__(self, settings: Settings, cell_urls: dict[str, str] | None = None) -> None:
        default_db = settings.database_url.get_secret_value()
        configured_map: dict[str, str] = {}
        raw_json = getattr(settings, "cell_database_urls_json", "")
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    configured_map = {str(k): str(v) for k, v in parsed.items()}
            except ValueError:
                pass
        if cell_urls:
            configured_map.update(cell_urls)

        self._cells: dict[str, CellEndpoint] = {
            cell: CellEndpoint(
                cell_id=cell,
                database_url=configured_map.get(cell, default_db),
                storage_prefix=f"cells/{cell}",
            )
            for cell in SUPPORTED_CELLS
        }

    def resolve(
        self,
        pinned_cell: str,
        *,
        requested_cell: str | None = None,
        residency_locked: bool = True,
    ) -> CellEndpoint:
        if pinned_cell not in self._cells:
            raise DomainError("ERR_VALIDATION", f"Unsupported regional cell '{pinned_cell}'.", 422)
        if requested_cell and requested_cell != pinned_cell and residency_locked:
            raise DomainError(
                "ERR_RESIDENCY_LOCKED",
                f"Workspace data residency is locked to '{pinned_cell}'; "
                f"cross-cell access to '{requested_cell}' is forbidden.",
                403,
            )
        target = requested_cell or pinned_cell
        if target not in self._cells:
            raise DomainError("ERR_VALIDATION", f"Unsupported regional cell '{target}'.", 422)
        return self._cells[target]

    def object_key(self, pinned_cell: str, business_id: UUID, evidence_id: UUID) -> str:
        endpoint = self.resolve(pinned_cell)
        return f"{endpoint.storage_prefix}/{business_id}/{evidence_id}.bin"
