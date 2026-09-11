"""Настоящий сквозной тест CLI: гоняем main() с реальным argparse (реальные
дефолты!) и ловим argv, который ушёл бы воркерам. Именно этого не хватало —
прошлый тест подставлял свой Namespace и не видел default=None у --tld."""
import sys, subprocess
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y

captured = []
def fake_run_parallel(base, shards, output, finalize=None):
    captured.append((list(base), [list(s) for s in shards], output))
    # Прогоняем ровно ту же сборку argv, что делает run_parallel, и проверяем,
    # что Windows-ветка Popen (list2cmdline) её переварит.
    for i, extra in enumerate(shards, 1):
        argv = [sys.executable, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "yandex_parser.py"), *base, *extra,
                "-o", str(y._part_path(y.Path(output), i))]
        subprocess.list2cmdline(argv)     # именно тут падало на None
    return 0
y.run_parallel = fake_run_parallel

CASES = [
    "--all-cities --category gt-fuel -o kz_azs.xlsx --workers 2",
    "--all-cities --category gt -o kz_gt.xlsx -w 3 --api-intercept",
    "--country --category gt-fuel -o kz_azs.xlsx --workers 2 --step 0.2",
    "--city Алматы --category gt -w 2",
    "--all-cities --category gt -w 2 --detail --no-headless -n 1000 --grid 2 --cooldown-every 5",
    "--country -w 2 --tld ru --ll 37.6,55.7 --z 12 АЗС",
    "--routes --category gt-село -o kz_routes.xlsx --workers 2",
    "--routes -w 3 --corridor 1 --step 0.3 --api-intercept",
    "--all-cities --cities аулы --category gt-село -o kz_aul.xlsx -w 2",
    "--all-cities --cities макс --category gt -o kz_max.xlsx -w 2",
]
fails = []
for cmd in CASES:
    captured.clear()
    sys.argv = ["yandex_parser.py", *cmd.split()]
    try:
        y.main()
        assert captured, "run_parallel не вызван"
        base, shards, out = captured[0]
        bad = [x for x in base + [i for s in shards for i in s] if not isinstance(x, str)]
        assert not bad, f"не-строки в argv: {bad}"
        print(f"OK   | {cmd}\n       base: {' '.join(base)}\n       out: {out}, воркеров: {len(shards)}")
    except Exception as exc:
        print(f"FAIL | {cmd}\n       {type(exc).__name__}: {exc}")
        fails.append(cmd)
print("\n" + ("✅ ВСЕ КЕЙСЫ ОК" if not fails else f"❌ ПРОВАЛЫ: {len(fails)}"))
sys.exit(1 if fails else 0)
