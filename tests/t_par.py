import sys, os, subprocess
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y

# 1) round-robin split
print("split:", y._split_round_robin(list("ABCDEFG"), 3))
print("split n>len:", y._split_round_robin(["A","B"], 5))
# 2) shard parse
print("shard:", y._parse_shard("0/3"), y._parse_shard("2/3"), y._parse_shard("bad"),
      y._parse_shard("3/3"), y._parse_shard(None))
# 3) part paths
print("part:", y._part_path(Path("kz_gt.xlsx"), 2), y._part_path(Path("out/kz.csv"), 1))

# 4) shards cover the grid exactly once, no overlap
tiles = y.country_grid(1.0)
N = 3
cover = []
for i in range(N):
    cover += [t for k, t in enumerate(tiles) if k % N == i]
print("tiles:", len(tiles), "covered:", len(cover), "unique:", len(set(cover)),
      "exact:", sorted(cover) == sorted(tiles))

# 5) run_parallel end-to-end with fake worker processes
fake = Path("fake_worker.py")
fake.write_text('''
import sys, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y
from pathlib import Path
out = sys.argv[sys.argv.index("-o") + 1]
cities = sys.argv[sys.argv.index("--cities") + 1].split(",")
orgs = [y.Organization(name=f"AZS-{c}", address=f"{c}, ул. 1", search_query="АЗС") for c in cities]
orgs.append(y.Organization(name="ОБЩИЙ ДУБЛЬ", address="ул. Общая 1", search_query="АЗС"))
Path(out).parent.mkdir(parents=True, exist_ok=True)
y.save_xlsx_by_categories({"АЗС": orgs}, Path(out))
print("worker done", out, os.environ.get("YAMAP_PROFILE_DIR"), os.environ.get("YAMAP_WORKER"))
''', encoding="utf-8")

y.__file__ = str(fake.resolve())          # run_parallel spawns os.path.abspath(__file__)
import yandex_parser
yandex_parser.__file__ = str(fake.resolve())
shards = [["--cities", ",".join(c)] for c in y._split_round_robin(["Алматы","Астана","Шымкент","Актобе","Тараз"], 3)]
n = y.run_parallel(["--tld","kz"], shards, "out/kz_par.xlsx")
print("MERGED:", n)
