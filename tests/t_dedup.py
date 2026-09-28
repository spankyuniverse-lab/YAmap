"""Дедупликация: одна точка из разных источников — одна строка, и в ней
собрано всё лучшее. Разные филиалы сети при этом не схлопываются."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL") + " | " + msg)
    if not cond:
        fails.append(msg)

O = y.Organization

# 1. Та же точка: из сниппета (адрес есть, телефона нет) и из API (наоборот)
snippet = O(name="АЗС Гелиос", address="Алматы, ул. Абая, 1", org_id="111",
            rating="4.5")
api     = O(name="Гелиос", address="", org_id="111", phone="+7 701 111-11-11",
            latitude="43.2", longitude="76.9")
res = y._finalize_orgs([snippet, api])
check(len(res) == 1, f"одна точка → одна строка (вышло {len(res)})")
if res:
    r = res[0]
    check(r.phone == "+7 701 111-11-11", f"телефон подтянулся из API: {r.phone!r}")
    check(r.address == "Алматы, ул. Абая, 1", f"адрес сохранился: {r.address!r}")
    check(r.rating == "4.5", f"рейтинг из сниппета сохранился: {r.rating!r}")
    check(r.latitude == "43.2", "координаты подтянулись")

# 2. Разные филиалы сети — разные записи
a = O(name="АЗС Гелиос", address="Алматы, ул. Абая, 1", org_id="111")
b = O(name="АЗС Гелиос", address="Алматы, ул. Розыбакиева, 5", org_id="222")
check(len(y._finalize_orgs([a, b])) == 2, "разные филиалы не схлопнулись")

# 3. Без ID работает старое правило имя+адрес
c1 = O(name="Магазин", address="Астана, ул. Мира, 3")
c2 = O(name="Магазин", address="ул. Мира, 3")     # тот же адрес без города
check(len(y._finalize_orgs([c1, c2])) == 1, "без ID: адрес с городом и без — одна точка")

d1 = O(name="Магазин", address="Астана, ул. Мира, 3")
d2 = O(name="Магазин", address="Астана, ул. Абая, 7")
check(len(y._finalize_orgs([d1, d2])) == 2, "без ID: разные адреса — разные точки")

# 4. Записи без названия отбрасываются
check(len(y._finalize_orgs([O(name=""), O(name="  "), O(name="Х", org_id="9")])) == 1,
      "пустые названия выброшены")

# 5. Раньше это давало ДВА: одна точка, у одной копии адреса нет
old_style = [O(name="АЗС", address="Алматы, ул. Абая, 1", org_id="777"),
             O(name="АЗС", address="", org_id="777")]
check(len(y._finalize_orgs(old_style)) == 1,
      "копия без адреса не создаёт дубль (был баг)")

# 6. Одна точка: снята без ID (имя+адрес) и с ID — должна слиться в одну
no_id = O(name="АЗС Гелиос", address="Алматы, ул. Абая, 1", rating="4.5")
with_id = O(name="АЗС Гелиос", address="Алматы, ул. Абая, 1", org_id="555",
            phone="+7 777 555-55-55")
res6 = y._finalize_orgs([no_id, with_id])
check(len(res6) == 1, f"строка без ID слилась со строкой с ID (вышло {len(res6)})")
if res6:
    check(res6[0].phone and res6[0].rating == "4.5",
          "после слияния есть и телефон (ID-строка), и рейтинг (без-ID)")

# 7. ResumeManager: чтение файла не схлопывает филиалы с одинаковым именем
import tempfile, os
from pathlib import Path as _P
tmpdir = _P(tempfile.mkdtemp())
f = tmpdir / "resume_test.xlsx"
branches = [O(name="КазМунайГаз", address="", org_id=str(1000 + i),
              search_query="Заправки", source_query="Заправки")
            for i in range(5)]
y.save_xlsx(branches, f)
rm = y.ResumeManager(f)
check(rm.existing_count == 5,
      f"резюме сохранило все 5 филиалов без адреса (вышло {rm.existing_count})")
check(rm.is_query_done("Заправки"), "выполненный запрос виден в резюме")

print("\n" + ("✅ ДЕДУП ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
