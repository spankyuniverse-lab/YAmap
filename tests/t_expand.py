"""Расширили список городов — прошлый прогон не должен пересобираться.
Воспроизводим: 18 городов собраны, список расширили до всех 96 (--cities all).
Плюс проверяем дефолт --all-cities = рабочие 19."""
import sys, json, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

HERE = Path(__file__).resolve().parent
out = HERE / "out" / "kz_azs.xlsx"
out.parent.mkdir(parents=True, exist_ok=True)

# Имитируем прошлый прогон в 2 воркера: каждый отметил свои 9 городов.
prev = list(y.KZ_CITIES.keys())
half = y._split_round_robin(prev, 2)
for i, chunk in enumerate(half, 1):
    part = y._part_path(out, i)
    (part.with_name(part.stem + ".cities.json")).write_text(
        json.dumps(chunk, ensure_ascii=False), encoding="utf-8")

assert y._done_cities(out) == set(prev), "не подхватил пройденные города"
print(f"пройдено ранее: {len(y._done_cities(out))}")

captured = {}
y.run_parallel = lambda base, shards, output, finalize=None: (
    captured.update(shards=shards, base=base) or 0)

ns = argparse.Namespace(
    tld=None, ll=None, z=None, api_intercept=True, detail=False, no_headless=False,
    max_results=None, scroll_pause=1.0, cooldown_every=0, cooldown_sec=90, grid=1,
    proxy=None, proxy_file=None, country=False, all_cities=True, all_categories=False,
    category=["gt-fuel"], query=None, city=None, output=str(out), step=0.25,
    tile_z=0, cities="all")
y._dispatch_parallel(ns, 2, headless=True)

sent = []
for s in captured["shards"]:
    sent += s[s.index("--cities") + 1].split(",")
overlap = set(sent) & set(prev)
print(f"к сбору отправлено: {len(sent)} городов, пересечений со старыми: {len(overlap)}")
assert not overlap, f"пересобирает уже собранное: {sorted(overlap)[:5]}"
assert len(sent) == len(y.KZ_CITIES_ALL) - len(prev), \
    f"ожидал {len(y.KZ_CITIES_ALL) - len(prev)}, отправлено {len(sent)}"

# Дефолт --all-cities — рабочие 19, а не все 96
assert len(y.resolve_city_list(None)) == 19, y.resolve_city_list(None)
assert len(y.resolve_city_list("all")) == len(y.KZ_CITIES_ALL)
assert y.resolve_city_list("Алматы,Астана") == ["Алматы", "Астана"]
print("дефолт --all-cities:", len(y.resolve_city_list(None)), "городов")

# Повторный вызов, когда собрано ВСЁ — должен честно сказать «работы нет»
for i, chunk in enumerate(y._split_round_robin(list(y.KZ_CITIES_ALL), 2), 1):
    part = y._part_path(out, i)
    (part.with_name(part.stem + ".cities.json")).write_text(
        json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
captured.clear()
rc = y._dispatch_parallel(ns, 2, headless=True)
assert rc == 0 and not captured, "запустил воркеров, хотя собирать нечего"
print("✅ расширение списка не пересобирает старое; полный охват = работы нет")
