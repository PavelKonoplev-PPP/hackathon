"""Тесты аналитической точности агента (issue #6).

Для каждого вопроса из eval_cases.py:
1. выполняется эталонный SQL — это правильный ответ;
2. агент отвечает на вопрос сам (выбор таблицы, SQL, выполнение);
3. числа агента сравниваются с эталоном.

Запуск из корня проекта (нужны база и модель):
    python -m backend.llm.eval --gold-only          # проверить эталоны (без модели)
    python -m backend.llm.eval                      # все вопросы
    python -m backend.llm.eval --only sales_count --only ads_ctr_by_platform
Отчёт сохраняется в data/eval-report.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

REL_TOL = 0.001   # 0,1 % для дробных значений (средние, деньги)
ROUND_TOL = 0.0051  # агент мог округлить до сотых: 3,9412 -> 3,94


# ---------- сравнение результатов ----------

def _norm(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value).strip().lower()


def _split(row) -> tuple[frozenset, list[float]]:
    """Строка -> (подписи, числа). Порядок и названия колонок не важны."""
    values = [_norm(v) for v in row]
    labels = frozenset(v for v in values if isinstance(v, str))
    numbers = [v for v in values if isinstance(v, float)]
    return labels, numbers


def _close(a: float, b: float, scale100: bool) -> bool:
    """Количества сравниваем точно, дробные — с допуском на округление до сотых."""
    for candidate in ((b, b * 100) if scale100 else (b,)):
        if float(candidate).is_integer():
            tol = 0.5
        else:
            tol = max(REL_TOL * abs(candidate), ROUND_TOL if abs(candidate) >= 1 else 0.0)
        if abs(a - candidate) <= tol:
            return True
    return False


def _row_matches(gold_row, agent_row, scale100: bool) -> bool:
    g_labels, g_nums = _split(gold_row)
    a_labels, a_nums = _split(agent_row)
    if not g_labels <= a_labels:
        return False
    return all(any(_close(a, g, scale100) for a in a_nums) for g in g_nums)


def compare(mode: str, gold_rows: list, agent_rows: list, scale100: bool = False) -> tuple[bool, str]:
    """Возвращает (совпало, пояснение)."""
    gold_rows = [r for r in gold_rows if any(v is not None for v in r)]
    if not gold_rows:
        return False, "эталон пустой — проверь эталонный SQL"
    if not agent_rows:
        return False, "у агента нет строк результата"

    if mode == "value":
        target = _split(gold_rows[0])[1]
        if not target:
            return False, "в эталоне нет числа"
        found = any(_close(a, target[0], scale100) for row in agent_rows for a in _split(row)[1])
        return found, "" if found else f"ожидалось число {target[0]:g}"

    if mode == "top":
        if len(agent_rows) < len(gold_rows):
            return False, f"у агента {len(agent_rows)} строк, нужно минимум {len(gold_rows)}"
        for i, (g, a) in enumerate(zip(gold_rows, agent_rows), 1):
            if not _row_matches(g, a, scale100):
                return False, f"строка {i}: ожидалось {_brief(g)}, у агента {_brief(a)}"
        return True, ""

    # mode == "all"
    missing = [g for g in gold_rows if not any(_row_matches(g, a, scale100) for a in agent_rows)]
    if missing:
        return False, (f"не найдено {len(missing)} из {len(gold_rows)} строк эталона, "
                       f"например {_brief(missing[0])}")
    return True, ""


def _brief(row) -> str:
    parts = []
    for v in row:
        v = _norm(v)
        parts.append(f"{v:g}" if isinstance(v, float) else str(v))
    return "(" + ", ".join(parts) + ")"


# ---------- прогон ----------

def run_eval(cases: list[dict], catalog: dict, runner, client=None, gold_only=False,
             log=print) -> list[dict]:
    from .ask import answer_question, reset_session

    tables = {t["name"]: t for t in catalog.get("tables") or []}
    report = []
    for case in cases:
        item = {"id": case["id"], "question": case["question"], "expected_table": case["table"]}
        report.append(item)
        mode = case["mode"]
        gold_rows = []
        if mode in ("value", "all", "top"):
            if "expected" in case:
                gold_rows = [(case["expected"],)]
            else:
                table = tables.get(case["table"])
                if table is None:
                    item.update(ok=False, reason=f"нет таблицы {case['table']} в каталоге")
                    log(f"❌ {case['id']}: {item['reason']}")
                    continue
                try:
                    gold_rows = [tuple(r) for r in runner.run(table, case["gold"]).rows]
                except Exception as exc:
                    item.update(ok=False, reason=f"ошибка эталонного SQL: {str(exc).splitlines()[0]}")
                    log(f"❌ {case['id']}: {item['reason']}")
                    continue
            item["gold"] = [_brief(r) for r in gold_rows[:10]]

        if gold_only:
            if mode not in ("value", "all", "top"):
                continue
            empty = not gold_rows or all(v is None for r in gold_rows for v in r)
            item.update(ok=not empty, reason="эталон пустой" if empty else "")
            log(f"{'✅' if not empty else '⚠️'} {case['id']}: {len(gold_rows)} строк, "
                f"{', '.join(item['gold'][:3])}")
            continue

        session = f"eval-{case['id']}"
        reset_session(session)
        trace: dict = {}
        started = time.monotonic()
        try:
            for step in case.get("steps") or [case["question"]]:
                trace = {}
                response = answer_question(step, catalog, runner, client=client, trace=trace,
                                           session_id=session)
        except Exception as exc:
            response = {"answer": f"исключение: {exc}"}
        item["seconds"] = round(time.monotonic() - started, 1)
        item.update(table=trace.get("table"), sql=trace.get("sql"), attempts=trace.get("attempts"),
                    errors=trace.get("errors"), answer=response.get("answer"),
                    clarification=response.get("clarification"))

        if mode == "clarify":
            ok = trace.get("clarification") == case["clarify"]
            reason = "" if ok else f"ожидалось уточнение ({case['clarify']}), агент: " \
                                   f"{(response.get('answer') or '')[:80]}"
        elif mode == "refuse":
            ok = "rows" not in trace and not response.get("table") and not response.get("clarification")
            reason = "" if ok else "агент не отказался"
        elif trace.get("table") != case["table"]:
            ok, reason = False, (f"не та таблица: {trace.get('table')}" if trace.get("table")
                                 else "нет результата: " + (response.get("answer") or "")[:80])
        elif "rows" not in trace:
            ok, reason = False, "агент не получил результат: " + (response.get("answer") or "")[:80]
        else:
            agent_rows = [tuple(r) for r in trace["rows"]]
            item["agent"] = [_brief(r) for r in agent_rows[:10]]
            ok, reason = compare(mode, gold_rows, agent_rows, case.get("scale100", False))
        item.update(ok=ok, reason=reason)
        log(f"{'✅' if ok else '❌'} {case['id']} ({item['seconds']} c)"
            + ("" if ok else f": {reason}\n     SQL: {(item.get('sql') or '—').replace(chr(10), ' ')}"))
    return report


def main() -> int:
    from .eval_cases import CASES
    from .sql_runner import PostgresRunner

    parser = argparse.ArgumentParser(description="Тесты аналитической точности агента")
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    parser.add_argument("--report", type=Path, default=Path("data/eval-report.json"))
    parser.add_argument("--only", action="append", help="id кейса (можно несколько раз)")
    parser.add_argument("--gold-only", action="store_true", help="только проверить эталоны")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    cases = [c for c in CASES if not args.only or c["id"] in args.only]
    runner = PostgresRunner.from_env()
    report = run_eval(cases, catalog, runner, gold_only=args.gold_only)

    report = [item for item in report if "ok" in item]
    passed = sum(item["ok"] for item in report)
    print(f"\nИтого: {passed} из {len(report)} ({passed / max(len(report), 1):.0%})")
    if not args.gold_only:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                               encoding="utf-8")
        print(f"Подробности: {args.report}")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
