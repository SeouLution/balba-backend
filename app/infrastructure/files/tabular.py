"""판매 파일(xlsx/xls/csv)을 행 목록으로 읽는다. 수식은 실행하지 않고 저장된 값만 읽는다."""

import csv
import io
from pathlib import Path

XLSX_SIGNATURE = b"PK\x03\x04"
XLS_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class TooManyRows(Exception):
    pass


def decode_csv(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949")


def has_valid_signature(extension: str, head: bytes) -> bool:
    if extension == "xlsx":
        return head.startswith(XLSX_SIGNATURE)
    if extension == "xls":
        return head.startswith(XLS_SIGNATURE)
    if b"\x00" in head:
        return False
    # 한도에 잘린 마지막 글자가 깨질 수 있어 끝 몇 바이트를 떼고 해독해 본다.
    for encoding in ("utf-8-sig", "cp949"):
        for trim in range(4):
            try:
                head[: len(head) - trim].decode(encoding)
                return True
            except UnicodeDecodeError:
                continue
    return False


def read_rows(path: Path, max_rows: int) -> list[list]:
    extension = path.suffix.lstrip(".")
    if extension == "xlsx":
        rows = _read_xlsx(path, max_rows)
    elif extension == "xls":
        rows = _read_xls(path, max_rows)
    else:
        rows = _read_csv(path, max_rows)
    return rows


def _guard(rows: list, max_rows: int) -> None:
    if len(rows) > max_rows + 1:
        raise TooManyRows


def _read_xlsx(path: Path, max_rows: int) -> list[list]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = []
        for row in workbook.worksheets[0].iter_rows(values_only=True):
            rows.append(list(row))
            _guard(rows, max_rows)
        return rows
    finally:
        workbook.close()


def _read_xls(path: Path, max_rows: int) -> list[list]:
    import xlrd

    workbook = xlrd.open_workbook(path)
    sheet = workbook.sheet_by_index(0)
    if sheet.nrows > max_rows + 1:
        raise TooManyRows
    rows = []
    for row_idx in range(sheet.nrows):
        values = []
        for cell in sheet.row(row_idx):
            if cell.ctype == xlrd.XL_CELL_DATE:
                values.append(xlrd.xldate_as_datetime(cell.value, workbook.datemode))
            elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                values.append(None)
            else:
                values.append(cell.value)
        rows.append(values)
    return rows


def _read_csv(path: Path, max_rows: int) -> list[list]:
    rows = []
    for row in csv.reader(io.StringIO(decode_csv(path.read_bytes()))):
        rows.append(list(row))
        _guard(rows, max_rows)
    return rows
