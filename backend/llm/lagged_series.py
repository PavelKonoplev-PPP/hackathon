"""Reproducible lead/lag correlations for aligned calendar time series.

Positive lag L correlates X(t) with Y(t + L), so X leads Y.
Missing periods and missing/invalid values are not replaced with zero.
The caller must supply one pre-aggregated observation per calendar bucket.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

VALID_GRAINS = {"day", "month"}
MAX_ALLOWED_LAG = 36


def _period_key(value: Any, grain: str) -> int:
    """An integer calendar bucket; not the position in an array."""
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        value = value.strip()
        try:
            if len(value) == 7:
                parsed = date.fromisoformat(value + "-01")
            else:
                parsed = date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(f"Неверный календарный период: {value!r}") from exc
    else:
        raise ValueError(f"Неподдерживаемый период: {value!r}")
    if grain == "month":
        return parsed.year * 12 + parsed.month - 1
    return parsed.toordinal()


def _numeric(value: Any) -> float | None:
    """Convert numeric values safely; missing/invalid values stay missing."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value.replace(" ", "").replace("\u00a0", "").replace(",", ".")
                       if isinstance(value, str) else value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mean_x = math.fsum(xs) / len(xs)
    mean_y = math.fsum(ys) / len(ys)
    centered_x = [x - mean_x for x in xs]
    centered_y = [y - mean_y for y in ys]
    ssx = math.fsum(x * x for x in centered_x)
    ssy = math.fsum(y * y for y in centered_y)
    if ssx <= 0 or ssy <= 0:
        return None
    covariance = math.fsum(x * y for x, y in zip(centered_x, centered_y))
    r = covariance / math.sqrt(ssx * ssy)
    return max(-1.0, min(1.0, r)) if math.isfinite(r) else None


def analyze_lags(
    rows: list[list[Any] | tuple[Any, ...]],
    *,
    grain: str = "month",
    max_lag: int = 3,
    min_pairs: int = 4,
) -> dict[str, Any]:
    """Calculate Pearson r for L in [-max_lag, max_lag].

    Each input row is (calendar_period, x_metric, y_metric).
    Rows are pre-aggregated to unique calendar periods. The shifted pair for
    lag L is X(t), Y(t+L). A genuine calendar gap cannot cause a false match.
    """
    if grain not in VALID_GRAINS:
        raise ValueError("Поддерживаются только day и month")
    if type(max_lag) is not int or not 0 <= max_lag <= MAX_ALLOWED_LAG:
        raise ValueError("max_lag должен быть целым от 0 до 36")
    if type(min_pairs) is not int or min_pairs < 3:
        raise ValueError("min_pairs должен быть целым не меньше 3")
    if not isinstance(rows, list):
        raise ValueError("Ожидается список строк")

    x_values: dict[int, float | None] = {}
    y_values: dict[int, float | None] = {}
    for pos, row in enumerate(rows, 1):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError(f"Строка {pos}: нужны период, X и Y")
        key = _period_key(row[0], grain)
        if key in x_values:
            raise ValueError(f"Повторяющийся период в строке {pos}; сначала агрегируйте исходные данные")
        x_values[key] = _numeric(row[1])
        y_values[key] = _numeric(row[2])

    results = []
    for lag in range(-max_lag, max_lag + 1):
        pairs = [
            (x, y_values[key + lag])
            for key, x in x_values.items()
            if x is not None and (y := y_values.get(key + lag)) is not None
        ]
        n = len(pairs)
        r = _pearson(pairs) if n >= min_pairs else None
        status = ("insufficient_pairs" if n < min_pairs
                  else "zero_variance" if r is None
                  else "checked")
        results.append({"lag": lag, "r": r, "pairs": n, "status": status})

    checked = [entry for entry in results if entry["status"] == "checked"]
    # Prefer fewer shifted buckets at exactly the same absolute correlation;
    # do not turn small floating-point differences into fabricated certainty.
    checked.sort(key=lambda entry: (-abs(entry["r"]), abs(entry["lag"]), entry["lag"]))
    best = checked[0] if checked else None
    ambiguous = (
        bool(best) and
        sum(abs(abs(entry["r"]) - abs(best["r"])) <= 1e-9 for entry in checked) > 1
    )
    return {
        "grain": grain,
        "convention": "positive lag: X(t) leads Y(t+lag)",
        "min_pairs": min_pairs,
        "max_lag": max_lag,
        "periods": len(x_values),
        "results": results,
        "best": best,
        "ambiguous": ambiguous,
        "conclusion": (
            "insufficient_data" if best is None else
            "ambiguous" if ambiguous else "calculated"
        ),
    }


def summarize_lags(result: dict[str, Any], x_label: str = "X", y_label: str = "Y") -> str:
    """Interpret the result without making causal claims."""
    best = result.get("best")
    if not best:
        return "Недостаточно сопоставимых наблюдений или изменчивости данных для корреляции."
    lag, r, n = best["lag"], best["r"], best["pairs"]
    unit = "месяц(ев)" if result["grain"] == "month" else "день(дней)"
    if lag > 0:
        relation = f"«{x_label}» опережает «{y_label}» на {lag} {unit}"
    elif lag < 0:
        relation = f"«{y_label}» опережает «{x_label}» на {-lag} {unit}"
    else:
        relation = "максимальная абсолютная корреляция наблюдается без сдвига"
    warning = (
        " Несколько лагов имеют одинаковую по модулю корреляцию; однозначный оптимальный лаг не установлен."
        if result.get("ambiguous") else ""
    )
    return (
        f"Корреляция Пирсона: r = {r:+.3f}, пар наблюдений: {n}. "
        f"Среди проверенных лагов {relation}.{warning} "
        "Корреляция не доказывает причинно-следственную связь."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only calculation from pre-aggregated series JSON")
    parser.add_argument("--input", type=Path, required=True,
                        help="JSON list of [period, x, y] rows, one row per bucket")
    parser.add_argument("--grain", choices=sorted(VALID_GRAINS), default="month")
    parser.add_argument("--max-lag", type=int, default=3)
    parser.add_argument("--min-pairs", type=int, default=4)
    parser.add_argument("--x-label", default="X")
    parser.add_argument("--y-label", default="Y")
    args = parser.parse_args()
    try:
        rows = json.loads(args.input.read_text(encoding="utf-8"))
        report = analyze_lags(rows, grain=args.grain, max_lag=args.max_lag,
                              min_pairs=args.min_pairs)
        report["summary"] = summarize_lags(report, args.x_label, args.y_label)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"Ошибка корреляционного анализа: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
