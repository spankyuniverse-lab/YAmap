"""YAmap — точка входа CLI.

Логика разделена на пакет `yamap/`. Этот файл оставлен для обратной
совместимости с командами вида `python yandex_parser.py …`.
"""

import sys

from yamap.cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОтменено пользователем.", file=sys.stderr)
        sys.exit(130)
