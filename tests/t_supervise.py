"""Воркер падает на первом запуске, родитель обязан поднять его и получить
данные со второго. Плюс проверяем добор и что --resume доезжает до воркера."""
import sys, os
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y

y.MAX_RESTARTS = 3
fake = Path("crashy_worker.py").resolve()
fake.write_text('''
import sys, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y
from pathlib import Path
out = Path(sys.argv[sys.argv.index("-o") + 1])
cities = sys.argv[sys.argv.index("--cities") + 1].split(",")
assert "--resume" in sys.argv, "родитель не передал --resume"
flag = Path(f"crashed_{os.environ['YAMAP_WORKER']}.flag")
# Воркер 1 падает на первом запуске
if os.environ["YAMAP_WORKER"] == "1" and not flag.exists():
    flag.write_text("x")
    print("ERROR симулирую падение", flush=True)
    sys.exit(3)
orgs = [y.Organization(name=f"AZS-{c}", address=f"{c}, ул. 1", search_query="АЗС") for c in cities]
out.parent.mkdir(parents=True, exist_ok=True)
y.save_xlsx_by_categories({"АЗС": orgs}, out)
# отмечаем пройденные города (кроме одного — чтобы проверить добор)
import json
json.dumps
(out.with_name(out.stem + ".cities.json")).write_text(json.dumps(cities[:-1], ensure_ascii=False), encoding="utf-8")
print("worker ok", out, flush=True)
''', encoding="utf-8")

import yandex_parser
yandex_parser.__file__ = str(fake)

ALL = ["Алматы","Астана","Шымкент","Актобе"]
called = {}
def finalize(parts):
    import json
    done = set()
    for p in parts:
        cj = p.with_name(p.stem + ".cities.json")
        if cj.exists():
            done |= set(json.loads(cj.read_text(encoding="utf-8")))
    missing = [c for c in ALL if c not in done]
    called["missing"] = missing
    print("FINALIZE вызван, недостающие:", missing)
    return len(missing)

shards = [["--cities", ",".join(c)] for c in y._split_round_robin(ALL, 2)]
n = y.run_parallel(["--tld","kz"], shards, "out/kz_sup.xlsx", finalize=finalize)
print("\nИТОГ:", n, "| добор увидел:", called.get("missing"))
assert Path("crashed_1.flag").exists(), "воркер 1 не падал?"
assert n >= 2, f"после рестарта данных нет: {n}"
assert called.get("missing"), "добор не заметил пропущенные города"
print("✅ рестарт + resume + добор работают")
