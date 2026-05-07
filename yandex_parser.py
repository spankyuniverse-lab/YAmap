"""YAmap — точка входа CLI.

Логика разделена на пакет `yamap/`. Этот файл оставлен для обратной
совместимости с командами вида `python yandex_parser.py …`.
"""

from yamap.cli import main

if __name__ == "__main__":
    main()
