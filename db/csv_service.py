"""Предпросмотр и потоковый импорт произвольного CSV для backend.

Файл должен быть заранее сохранён backend во временной директории.
Функции не принимают пользовательские SQL-имена и не изменяют исходный CSV.
"""

import csv
from pathlib import Path

try:
    from .load_csv import columns_for, import_file, prepare, table_name_for
except ImportError:  # Для запуска тестов через unittest discover -s db.
    from load_csv import columns_for, import_file, prepare, table_name_for

DELIMITERS = (";", ",", "\t", "|")
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")
MAX_PREVIEW_ROWS = 20
MAX_COLUMNS = 500
SNIFF_BYTES = 32768


def _source_name(filename: str) -> str:
    """Исходное имя только как метаданные; пути из запроса запрещены."""
    if (
        not isinstance(filename, str)
        or not filename
        or filename in (".", "..")
        or "/" in filename
        or "\\" in filename
        or "\0" in filename
        or not filename.lower().endswith(".csv")
        or not Path(filename).stem.strip()
    ):
        raise ValueError("Требуется имя CSV-файла без пути (например, orders.csv)")
    return filename


def _delimiter_for(sample: str, requested: str | None) -> tuple[str, bool]:
    if requested is not None:
        if requested not in DELIMITERS:
            raise ValueError("Разделитель должен быть ; , TAB или |")
        return requested, False
    try:
        detected = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        if detected in DELIMITERS:
            return detected, False
    except csv.Error:
        pass
    # Для одноколоночных CSV разделитель не обнаруживается однозначно.
    # Не угадываем из текста: используем ; и сообщаем об этом пользователю.
    return ";", True


def preview_csv(
    path: Path,
    *,
    original_filename: str | None = None,
    delimiter: str | None = None,
    encoding: str = "utf-8-sig",
    preview_rows: int = 5,
) -> dict:
    """Возвращает заголовки, варианты имён колонок и первые строки.

    Не читает CSV целиком. Пробелы, нули в начале кодов и десятичные запятые
    остаются строками. Кодировку и разделитель можно выбрать через интерфейс.
    """
    path = Path(path)
    filename = _source_name(original_filename or path.name)
    if encoding not in ENCODINGS:
        raise ValueError("Поддерживаются кодировки UTF-8, UTF-8 BOM и Windows-1251")
    if not 0 <= preview_rows <= MAX_PREVIEW_ROWS:
        raise ValueError(f"preview_rows должен быть от 0 до {MAX_PREVIEW_ROWS}")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("CSV-файл отсутствует или пустой")

    try:
        with path.open("r", encoding=encoding, newline="") as handle:
            sample = handle.read(SNIFF_BYTES)
            chosen_delimiter, fallback = _delimiter_for(sample, delimiter)
            handle.seek(0)
            reader = csv.reader(handle, delimiter=chosen_delimiter, strict=True)
            headers = next(reader, None)
            if headers is None or not headers or not any(h.strip() for h in headers):
                raise ValueError("Не найдена строка заголовков CSV")
            if len(headers) > MAX_COLUMNS:
                raise ValueError(f"CSV содержит более {MAX_COLUMNS} колонок")
            if any("\0" in h for h in headers):
                raise ValueError("Недопустимый символ в заголовке CSV")

            columns = columns_for(headers)
            rows = []
            for number, row in enumerate(reader, start=2):
                if len(rows) == preview_rows:
                    break
                if len(row) != len(headers):
                    raise ValueError(
                        f"Строка {number}: ожидалось {len(headers)} колонок, найдено {len(row)}"
                    )
                rows.append(row)
    except UnicodeError as exc:
        raise ValueError("Неверная кодировка CSV: выберите другую") from exc
    except csv.Error as exc:
        raise ValueError(f"Ошибка формата CSV: {exc}") from exc

    warnings = []
    if fallback:
        warnings.append("Разделитель не удалось определить; выбран ;. Проверьте предпросмотр.")
    if columns != headers:
        warnings.append("Некоторые имена колонок нормализованы для PostgreSQL.")

    return {
        "filename": filename,
        "schema": "raw",
        "table_name": table_name_for(Path(filename)),
        "encoding": encoding,
        "delimiter": chosen_delimiter,
        "original_headers": headers,
        "columns": columns,
        "rows": rows,
        "warnings": warnings,
    }


def import_uploaded_csv(
    conn,
    path: Path,
    *,
    original_filename: str,
    schema: str = "raw",
    delimiter: str | None = None,
    encoding: str = "utf-8-sig",
    replace: bool = False,
) -> dict:
    """Импортирует загруженный файл; вызывается backend под владельцем БД.

    По умолчанию никогда не перезаписывает существующую таблицу.
    Атомарность создания таблицы и COPY обеспечивает load_csv.import_file.
    """
    if schema != "raw":
        raise ValueError("Веб-импорт разрешён только в схему raw")
    preview = preview_csv(
        path,
        original_filename=original_filename,
        delimiter=delimiter,
        encoding=encoding,
    )
    path = Path(path)
    prepare(conn, schema)
    count = import_file(
        conn,
        path,
        path.parent,
        schema,
        replace,
        delimiter=preview["delimiter"],
        encoding=encoding,
        table_name=preview["table_name"],
        source_name=preview["filename"],
    )
    return {
        "schema": schema,
        "table_name": preview["table_name"],
        "row_count": count,
        "columns": preview["columns"],
        "warnings": preview["warnings"],
    }
