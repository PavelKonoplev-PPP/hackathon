"""Тесты аналитической точности агента (issue #6, BI-06 / #34).

Для каждого вопроса из eval_cases.py:
1. выполняется эталонный SQL — это правильный ответ;
2. агент отвечает на вопрос сам (выбор таблицы, SQL, выполнение);
3. числа агента сравниваются с эталоном.

Запуск из корня проекта (нужны база и модель):
    python -m backend.llm.eval --gold-only          # проверить эталоны (без модели)
    python -m backend.llm.eval                      # все вопросы, агент вызывается напрямую
    python -m backend.llm.eval --only sales_count --only ads_ctr_by_platform
    python -m backend.llm.eval --holdout            # «незнакомые» вопросы (eval_holdout.py)

Сквозной режим — те же вопросы через запущенный FastAPI, как их задаёт фронтенд
(POST /api/chat/jobs + опрос статуса; --api-sync — POST /api/chat):
    python -m backend.llm.eval --api http://localhost:8000
    python -m backend.llm.eval --api http://localhost:8000 --holdout
В этом режиме числа берутся из таблицы ответа API (как их видит пользователь),
таблица — из комментария к SQL. Эталоны по-прежнему считает PostgreSQL напрямую.
Отчёт сохраняется в data/eval-report.json (с --api — data/eval-report-api.json).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path

AGENT_ROW_CAP = 200   # агент показывает не больше 200 строк (PostgresRunner.max_rows)
GOLD_MAX_ROWS = 100_000  # эталон считаем без этого ограничения
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


def _close(a: float, b: float, scale100: bool, rounded: bool = False) -> bool:
    """Количества сравниваем точно, дробные — с допуском на округление до сотых.
    rounded=True — число взято из ответа API, где дробные показаны с двумя знаками."""
    for candidate in ((b, b * 100) if scale100 else (b,)):
        if float(candidate).is_integer():
            tol = 0.5
        else:
            tol = max(REL_TOL * abs(candidate),
                      ROUND_TOL if abs(candidate) >= 1 or rounded else 0.0)
        if abs(a - candidate) <= tol:
            return True
    return False


def _row_matches(gold_row, agent_row, scale100: bool, rounded: bool = False) -> bool:
    g_labels, g_nums = _split(gold_row)
    a_labels, a_nums = _split(agent_row)
    if not g_labels <= a_labels:
        return False
    return all(any(_close(a, g, scale100, rounded) for a in a_nums) for g in g_nums)


def compare(mode: str, gold_rows: list, agent_rows: list, scale100: bool = False,
            rounded: bool = False) -> tuple[bool, str]:
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
        found = any(_close(a, target[0], scale100, rounded) for row in agent_rows for a in _split(row)[1])
        return found, "" if found else f"ожидалось число {target[0]:g}"

    if mode == "top":
        if len(agent_rows) < len(gold_rows):
            return False, f"у агента {len(agent_rows)} строк, нужно минимум {len(gold_rows)}"
        for i, (g, a) in enumerate(zip(gold_rows, agent_rows), 1):
            if not _row_matches(g, a, scale100, rounded):
                return False, f"строка {i}: ожидалось {_brief(g)}, у агента {_brief(a)}"
        return True, ""

    # mode == "all"
    if len(agent_rows) >= AGENT_ROW_CAP and len(gold_rows) > len(agent_rows):
        # ответ обрезан до лимита строк (пользователь видит «показана часть»):
        # проверяем, что все показанные строки верны
        wrong = [a for a in agent_rows
                 if not any(_row_matches(g, a, scale100, rounded) for g in gold_rows)]
        if wrong:
            return False, (f"ответ обрезан до {len(agent_rows)} строк из {len(gold_rows)}, и "
                           f"{len(wrong)} из них не совпали с эталоном, например {_brief(wrong[0])}")
        return True, ""
    missing = [g for g in gold_rows
               if not any(_row_matches(g, a, scale100, rounded) for a in agent_rows)]
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


# ---------- ответы API: таблица как её видит пользователь ----------

_NUMBER = re.compile(r"^[+\-\u2212]?\d[\d \u00a0\u202f]*(,\d+)?%?$")
_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})(?: (\d{2}):(\d{2}))?$")
_TABLE_NOTE = re.compile(r"данные таблицы «([^»]+)»")
API_ROUND_TOL = 0.0051  # API показывает дробные числа с двумя знаками


def parse_cell(value):
    """'1 234,56' -> 1234.56, '+20,0%' -> 20.0, '01.09.2025' -> date, '—' -> None."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text in ("", "—", "-"):
        return None
    if text in ("да", "нет"):
        return text == "да"
    if _NUMBER.match(text):
        digits = re.sub(r"[ \u00a0\u202f%+]", "", text).replace("\u2212", "-").replace(",", ".")
        number = float(digits)
        return int(number) if number.is_integer() and "," not in text else number
    m = _DATE.match(text)
    if m:
        day, month, year, hour, minute = m.groups()
        if hour is None:
            return dt.date(int(year), int(month), int(day))
        return dt.datetime(int(year), int(month), int(day), int(hour), int(minute))
    return text


def table_from_sql(sql: str | None) -> str | None:
    """'-- t = данные таблицы «Продажи» …' -> 'Продажи'."""
    m = _TABLE_NOTE.search(sql or "")
    return m.group(1) if m else None


class ApiAsker:
    """Задаёт вопросы запущенному FastAPI так же, как фронтенд."""

    def __init__(self, base_url: str, datasource_id: str | None = None, use_jobs: bool = True,
                 timeout: float = 600.0, poll: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.datasource_id = datasource_id
        self.use_jobs = use_jobs
        self.timeout = timeout
        self.poll = poll

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"API недоступен ({self.base_url}): {exc.reason}") from exc

    def check(self) -> None:
        self._request("GET", "/api/health")

    def chat(self, message: str, session_id: str) -> dict:
        body = {"session_id": session_id, "message": message}
        if self.datasource_id:
            body["datasource_id"] = self.datasource_id
        if not self.use_jobs:
            return self._request("POST", "/api/chat", body)
        job = self._request("POST", "/api/chat/jobs", body)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            status = self._request("GET", f"/api/chat/jobs/{job['job_id']}")
            if status.get("status") == "completed":
                return status.get("result") or {}
            if status.get("status") == "failed":
                raise RuntimeError(f"задание завершилось ошибкой: {status.get('error')}")
            time.sleep(self.poll)
        raise RuntimeError(f"нет ответа за {self.timeout:.0f} с")

    def __call__(self, steps: list[str], session: str) -> dict:
        session = f"{session}-{uuid.uuid4().hex[:8]}"  # новая сессия на каждый прогон
        response: dict = {}
        for step in steps:
            response = self.chat(step, session)
        table = response.get("table") or None
        return {
            "table": table_from_sql(response.get("sql")),
            "rows": [tuple(parse_cell(v) for v in row) for row in table["rows"]] if table else None,
            "sql": response.get("sql"),
            "answer": response.get("answer"),
            "clarification": bool(response.get("clarification")),
            "has_table": bool(table),
            "status": response.get("status"),
            "rounded": True,
        }


def direct_asker(catalog: dict, runner, client=None):
    """Вызывает агента напрямую (answer_question), как раньше."""
    from .ask import answer_question, reset_session

    def ask(steps: list[str], session: str) -> dict:
        reset_session(session)
        trace: dict = {}
        response: dict = {}
        for step in steps:
            trace = {}
            response = answer_question(step, catalog, runner, client=client, trace=trace,
                                       session_id=session)
        return {
            "table": trace.get("table"),
            "rows": [tuple(r) for r in trace["rows"]] if "rows" in trace else None,
            "sql": trace.get("sql"), "attempts": trace.get("attempts"), "errors": trace.get("errors"),
            "answer": response.get("answer"),
            "clarification": trace.get("clarification") or bool(response.get("clarification")),
            "has_table": bool(response.get("table")),
            "rounded": False,
        }
    return ask


# ---------- прогон ----------

def _same_table(got: str | None, expected: str) -> bool:
    from .catalog_summary import display_table_name
    return got in (expected, display_table_name(expected))


def run_eval(cases: list[dict], catalog: dict, runner, client=None, gold_only=False,
             log=print, asker=None) -> list[dict]:
    """asker(steps, session) -> dict; по умолчанию — агент напрямую."""
    asker = asker or direct_asker(catalog, runner, client)
    gold_runner = runner
    if dataclasses.is_dataclass(runner) and hasattr(runner, "max_rows"):
        gold_runner = dataclasses.replace(runner, max_rows=GOLD_MAX_ROWS)
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
                    gold_rows = [tuple(r) for r in gold_runner.run(table, case["gold"]).rows]
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

        started = time.monotonic()
        try:
            got = asker(case.get("steps") or [case["question"]], f"eval-{case['id']}")
        except Exception as exc:
            got = {"answer": f"исключение: {exc}", "rows": None, "clarification": False,
                   "has_table": False, "error": True}
        item["seconds"] = round(time.monotonic() - started, 1)
        item.update(table=got.get("table"), sql=got.get("sql"), attempts=got.get("attempts"),
                    errors=got.get("errors"), answer=got.get("answer"),
                    clarification=got.get("clarification"))
        rows = got.get("rows")
        answer = (got.get("answer") or "")[:80]

        if got.get("error"):
            ok, reason = False, answer
        elif mode == "clarify":
            kind = got.get("clarification")
            ok = kind == case["clarify"] if isinstance(kind, str) else bool(kind)
            reason = "" if ok else f"ожидалось уточнение ({case['clarify']}), агент: {answer}"
        elif mode == "refuse":
            ok = rows is None and not got.get("has_table") and not got.get("clarification")
            reason = "" if ok else "агент не отказался"
        elif got.get("table") and not _same_table(got["table"], case["table"]):
            ok, reason = False, f"не та таблица: {got['table']}"
        elif not rows:
            ok, reason = False, "агент не получил результат: " + answer
        else:
            item["agent"] = [_brief(r) for r in rows[:10]]
            ok, reason = compare(mode, gold_rows, rows, case.get("scale100", False),
                                 rounded=got.get("rounded", False))
        item.update(ok=ok, reason=reason)
        log(f"{'✅' if ok else '❌'} {case['id']} ({item['seconds']} c)"
            + ("" if ok else f": {reason}\n     SQL: {(item.get('sql') or '—').replace(chr(10), ' ')}"))
    return report


def main() -> int:
    from .eval_cases import CASES
    from .eval_holdout import HOLDOUT
    from .sql_runner import PostgresRunner

    parser = argparse.ArgumentParser(description="Тесты аналитической точности агента")
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    parser.add_argument("--report", type=Path, default=None,
                        help="куда сохранить отчёт (по умолчанию data/eval-report[-api].json)")
    parser.add_argument("--only", action="append", help="id кейса (можно несколько раз)")
    parser.add_argument("--gold-only", action="store_true", help="только проверить эталоны")
    parser.add_argument("--holdout", action="store_true",
                        help="незнакомые вопросы из eval_holdout.py вместо основного набора")
    parser.add_argument("--with-holdout", action="store_true", help="основной набор + незнакомые")
    parser.add_argument("--api", metavar="URL",
                        help="сквозной прогон через FastAPI, например http://localhost:8000")
    parser.add_argument("--api-sync", action="store_true",
                        help="с --api: POST /api/chat вместо фоновых заданий /api/chat/jobs")
    parser.add_argument("--datasource", help="с --api: datasource_id источника")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    pool = HOLDOUT if args.holdout else CASES + (HOLDOUT if args.with_holdout else [])
    cases = [c for c in pool if not args.only or c["id"] in args.only]
    runner = PostgresRunner.from_env()
    asker = None
    if args.api and not args.gold_only:
        asker = ApiAsker(args.api, datasource_id=args.datasource, use_jobs=not args.api_sync)
        asker.check()
        print(f"Сквозной прогон через {args.api} "
              f"({'/api/chat' if args.api_sync else '/api/chat/jobs'})")
    report = run_eval(cases, catalog, runner, gold_only=args.gold_only, asker=asker)
    if args.report is None:
        args.report = Path("data/eval-report-api.json" if asker else "data/eval-report.json")

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
