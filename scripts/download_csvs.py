#!/usr/bin/env python3
"""Reliable HTTP Range auto-resuming downloader for Hackathon datasets from GitVerse.

Handles server disconnects gracefully by resuming from the exact byte offset
using HTTP Range headers until the full file size is verified.
"""

import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEST_DIR = PROJECT_ROOT / "Исходники"
DEST_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://gitverse.ru/api/repos/kban/HK_Datasets/raw/branch/main"

FILES = [
    "01_Продажи.csv",
    "02_Техническая_поддержка.csv",
    "03_Задачи_команд.csv",
    "04_Доставка.csv",
    "05_События_приложения.csv",
    "06_Рекламные_кампании.csv",
    "07_Заявки_на_закупку.csv",
    "08_Фоновые_задания.csv",
    "09_Лекарственные_препараты.csv",
]


def get_remote_file_size(url: str) -> int | None:
    """Determine total remote file size using Range: bytes=0-0 GET request."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Range": "bytes=0-0",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_range = resp.headers.get("Content-Range")
            if content_range and "/" in content_range:
                total_str = content_range.split("/")[-1].strip()
                if total_str.isdigit():
                    return int(total_str)
            return resp.length
    except Exception as exc:
        print(f"   [WARN] Could not determine remote size: {exc}")
        return None


def download_file_with_resume(filename: str) -> bool:
    encoded = urllib.parse.quote(filename)
    url = f"{BASE_URL}/{encoded}"
    target = DEST_DIR / filename
    part_file = target.with_suffix(".csv.part")

    total_size = get_remote_file_size(url)
    if not total_size:
        print(f" [ERROR] Could not reach {filename}")
        return False

    # Check if target already fully downloaded
    if target.is_file() and target.stat().st_size == total_size:
        print(f" [SKIP] {filename} уже полностью скачан ({total_size / (1024*1024):.1f} МБ).")
        return True

    print(f" [DOWNLOADING] {filename} (всего {total_size / (1024*1024):.1f} МБ)...")

    max_disconnects = 50
    disconnects = 0

    while True:
        current_offset = part_file.stat().st_size if part_file.is_file() else 0
        if current_offset >= total_size:
            break

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Range": f"bytes={current_offset}-",
        }
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(part_file, "ab") as out:
                block_size = 1024 * 512  # 512 KB
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    current_offset += len(chunk)
                    pct = (current_offset / total_size) * 100
                    mb = current_offset / (1024 * 1024)
                    tot_mb = total_size / (1024 * 1024)
                    print(f"\r   -> {filename}: {mb:.1f} / {tot_mb:.1f} МБ ({pct:.1f}%)", end="", flush=True)

            if current_offset >= total_size:
                break
        except Exception as exc:
            disconnects += 1
            if disconnects > max_disconnects:
                print(f"\n [ERROR] Превышен лимит разрывов ({max_disconnects}) для {filename}: {exc}")
                return False
            time.sleep(1)

    if part_file.is_file() and part_file.stat().st_size == total_size:
        if target.is_file():
            target.unlink()
        part_file.replace(target)
        mb = total_size / (1024 * 1024)
        print(f"\n [OK] {filename} успешно сохранён ({mb:.1f} МБ).")
        return True
    else:
        print(f"\n [ERROR] Несовпадение размера: {part_file.stat().st_size} != {total_size}")
        return False


def main():
    print("=" * 60)
    print("  Надёжное скачивание 9 датасетов кейса (с автодокачкой)")
    print("=" * 60)
    all_ok = True
    for f in FILES:
        if not download_file_with_resume(f):
            all_ok = False
            print(f" ❌ Не удалось скачать {f}.")

    if all_ok:
        print("\n Все 9 CSV датасетов успешно загружены в папку Исходники/!")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
