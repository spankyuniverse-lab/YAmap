"""Менеджер резюмирования — загружает уже собранные данные."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .models import FIELDNAMES, HEADERS_RU, Organization, normalize_for_dedup


log = logging.getLogger("yandex_parser")


class ResumeManager:
    """Менеджер докачки: загружает уже собранные данные из файла."""

    def __init__(self, path: Path | None = None):
        self._existing: dict[str, Organization] = {}
        self._completed_queries: set[str] = set()
        if path and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        suffix = path.suffix.lower()
        rows: list[dict] = []

        if suffix == ".csv":
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    rows = list(reader)
            except (OSError, csv.Error) as exc:
                log.warning("Не удалось прочитать CSV для резюме: %s", exc)
                return

        elif suffix == ".xlsx":
            try:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True)
                ws = wb.active
                if ws is None:
                    return
                headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    row_dict = {h: (v or "") for h, v in zip(headers, row) if h}
                    rows.append(row_dict)
                wb.close()
            except Exception as exc:
                log.warning("Не удалось прочитать XLSX для резюме: %s", exc)
                return

        elif suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict) and "organizations" in data:
                    rows = data["organizations"]
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Не удалось прочитать JSON для резюме: %s", exc)
                return

        ru_to_en = {v: k for k, v in HEADERS_RU.items()}

        for row in rows:
            norm = {}
            for k, v in row.items():
                en_key = ru_to_en.get(k, k)
                norm[en_key] = str(v) if v else ""

            key = f"{normalize_for_dedup(norm.get('name', ''))}|{normalize_for_dedup(norm.get('address', ''))}"
            if key != "|":
                org = Organization(**{f: norm.get(f, "") for f in FIELDNAMES if f in norm})
                self._existing[key] = org
                q = norm.get("search_query", "")
                if q:
                    self._completed_queries.add(q)

        log.info(
            "Резюме: загружено %d существующих организаций, %d выполненных запросов",
            len(self._existing), len(self._completed_queries),
        )

    @property
    def existing_count(self) -> int:
        return len(self._existing)

    def is_known(self, name: str, address: str) -> bool:
        return f"{normalize_for_dedup(name)}|{normalize_for_dedup(address)}" in self._existing

    def is_query_done(self, query: str) -> bool:
        return query in self._completed_queries

    def existing_orgs(self) -> list[Organization]:
        return list(self._existing.values())

    def existing_keys(self) -> set[str]:
        return set(self._existing.keys())
