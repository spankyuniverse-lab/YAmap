"""YAmap — парсер организаций с Яндекс.Карт."""

from .models import Organization, FIELDNAMES, HEADERS_RU, print_stats
from .config import CATEGORIES, list_categories, resolve_categories, ResumeManager
from .export import save_csv, save_json, save_xlsx, save_auto


def __getattr__(name):
    """Ленивый импорт: run_parser/run_category_parser подтягивают browser.py,
    который требует playwright. Откладываем до реального вызова."""
    if name == "run_parser":
        from .core import run_parser
        return run_parser
    if name == "run_category_parser":
        from .core import run_category_parser
        return run_category_parser
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Organization",
    "FIELDNAMES",
    "HEADERS_RU",
    "CATEGORIES",
    "list_categories",
    "resolve_categories",
    "ResumeManager",
    "run_parser",
    "run_category_parser",
    "save_csv",
    "save_json",
    "save_xlsx",
    "save_auto",
    "print_stats",
]
