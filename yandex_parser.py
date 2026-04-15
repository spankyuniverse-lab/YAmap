"""Обратная совместимость: python yandex_parser.py ... → python -m yandex_parser ..."""

# Этот файл оставлен для обратной совместимости.
# Основной код переехал в пакет yandex_parser/
# Рекомендуемый запуск: python -m yandex_parser "запрос"

if __name__ == "__main__":
    from yandex_parser.cli import main
    main()
