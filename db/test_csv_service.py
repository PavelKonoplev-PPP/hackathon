"""Предпросмотр CSV и контракт для backend без подключения к базе."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from csv_service import import_uploaded_csv, preview_csv


class CSVServiceTests(unittest.TestCase):
    def make_csv(self, folder, name, text, encoding="utf-8"):
        path = Path(folder) / name
        path.write_text(text, encoding=encoding)
        return path

    def test_semicolon_utf8_with_russian_and_leading_zeroes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "01_Продажи.csv",
                                 "Код;Цена;Код\n00123;15,50;A\n00008;2,00;B\n")
            result = preview_csv(path)
            self.assertEqual(result["delimiter"], ";")
            self.assertEqual(result["table_name"], "data_01_Продажи")
            self.assertEqual(result["columns"], ["Код", "Цена", "Код__2"])
            self.assertEqual(result["rows"][0], ["00123", "15,50", "A"])

    def test_semicolon_currency_column_and_decimal_commas(self):
        # Regression: Sniffer chose ',' and split the header into 2 columns.
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(
                folder, "bi_smoke_orders.csv",
                "Номер заказа;Город;Выручка, руб.;Дата\n"
                "001;Москва;100,50;2026-10-20\n"
                "002;Казань;200,00;2026-10-21\n"
                "003;Москва;50,50;2026-10-22\n",
            )
            result = preview_csv(path)
            self.assertEqual(result["delimiter"], ";")
            self.assertEqual(
                result["columns"],
                ["Номер заказа", "Город", "Выручка, руб.", "Дата"],
            )
            self.assertEqual(len(result["rows"]), 3)
            self.assertEqual(result["rows"][0][0], "001")
            self.assertEqual(result["rows"][0][2], "100,50")
            self.assertEqual(result["warnings"], [])

    def test_comma_delimiter_with_quotes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "sales.csv",
                                 'product,price\n"Paper, A",12.5\nPen,2\n')
            result = preview_csv(path)
            self.assertEqual(result["delimiter"], ",")
            self.assertEqual(result["rows"][0], ["Paper, A", "12.5"])

    def test_cp1251_and_tab_delimiter(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "orders.csv",
                                 "Имя\tЦена\nТовар\t100\n", "cp1251")
            result = preview_csv(path, encoding="cp1251", delimiter="\t")
            self.assertEqual(result["rows"], [["Товар", "100"]])

    def test_bad_row_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "bad.csv", "a;b\n1;2\n3\n")
            with self.assertRaisesRegex(ValueError, "Строка 3"):
                preview_csv(path)

    def test_path_traversal_name_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "safe.csv", "a;b\n1;2\n")
            with self.assertRaises(ValueError):
                preview_csv(path, original_filename="../unsafe.csv")

    def test_invalid_encoding_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "safe.csv", "a;b\n1;2\n")
            with self.assertRaises(ValueError):
                preview_csv(path, encoding="utf-16")

    def test_empty_and_invalid_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "safe.csv", ";\n1;2\n")
            with self.assertRaises(ValueError):
                preview_csv(path)

    def test_backend_import_delegates_to_existing_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "upload.csv", "Код;Цена\n001;1,50\n")
            conn = Mock()
            with patch("csv_service.prepare") as prepare, patch(
                "csv_service.import_file", return_value=1
            ) as do_import:
                result = import_uploaded_csv(conn, path, original_filename="orders.csv")
            prepare.assert_called_once_with(conn, "raw")
            kwargs = do_import.call_args.kwargs
            self.assertEqual(kwargs["table_name"], "data_orders")
            self.assertEqual(kwargs["source_name"], "orders.csv")
            self.assertFalse(do_import.call_args.args[4])
            self.assertEqual(result["row_count"], 1)

    def test_import_rejects_non_raw_schema_before_db_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_csv(folder, "upload.csv", "a;b\n1;2\n")
            with self.assertRaises(ValueError):
                import_uploaded_csv(Mock(), path, original_filename="upload.csv", schema="public")


if __name__ == "__main__":
    unittest.main()
