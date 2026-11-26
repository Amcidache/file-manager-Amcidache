import os
import sys
import sqlite3
from datetime import datetime
import json
import html
import math
import getpass
import stat
import shutil
import ctypes
import time
from ctypes import wintypes

from PyQt6.QtCore import Qt, QEvent, QUrl, QMimeData, QFileInfo
from PyQt6 import uic
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QAbstractButton,
    QMessageBox,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QFileIconProvider,
    QInputDialog,
    QCheckBox,
    QDialog,
    QVBoxLayout,
    QDialogButtonBox,
    QRadioButton,
    QButtonGroup,
    QTextBrowser,
)
from PyQt6.QtGui import (
    QPixmap,
    QDrag,
    QIcon,
)

MASTER_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(MASTER_DIR, "settings.json"), encoding="utf-8") as f:
    DEFAULT_THEMES = json.load(f)["themes"]

SORT_CRITERIA = (
    ("created", "По дате создания"),
    ("modified", "По дате изменения"),
    ("name", "Алфавиту"),
    ("size", "По размеру"),
)
SORT_ORDER_BY = {
    "created": "created_at DESC, file_name ASC",
    "modified": "modified_at DESC, file_name ASC",
    "name": "file_name ASC",
    "size": "CAST(file_size AS INTEGER) DESC, file_name ASC",
}
COLUMN_WEIGHTS = (3, 3, 1, 1, 1)
CANCEL = object()  # Для кнопки "Отмена" у пользователя при вызове отдельных окон


def convert(num):
    "Конвертирует размер файла в более компактный"
    num = int(num)
    memory = "бит"
    memory_list = ["К", "М", "Г", "Т", "П", "Э", "З", "Й"]
    if num // 8:
        num = math.ceil(num / 8)
        memory = "байт"
        for base in range(8):
            if num // 1024:
                num /= 1024
                memory = f"{memory_list[base]}байт"
            else:
                break
    return f"{round(num, 1)} {memory}"


def get_disc_name(disc):
    """Получает имя диска"""
    if not sys.platform.startswith("win"):
        return disc
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.kernel32.GetVolumeInformationW(
        disc, buf, 256, None, None, None, None, 0
    )
    return buf.value


class DROPFILES(ctypes.Structure):
    """Задаёт формат буфера обмена"""
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def get_local_drives():
    """Возвращает список доступных корней дисков"""
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            if bitmask & (1 << i):
                drives.append(f"{letter}:\\")
    except Exception:
        pass
    return drives


def get_clipboard_api():
    """Функция для работы с буфером обмена"""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    return user32, kernel32


def open_clipboard(user32):
    """Для записи/чтения/очистки clipboard"""
    for _ in range(10):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.01)
    return False


def put_clipboard_bytes(user32, kernel32, format_id, data):
    """Для помещения сырых байтов в буфер обмена Windows"""
    handle = kernel32.GlobalAlloc(0x0002, len(data))
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(format_id, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def build_dropfiles(paths):
    """Создаёт байты определённого формата из списка путей для буфера"""
    header = DROPFILES(ctypes.sizeof(DROPFILES), wintypes.POINT(0, 0), False, True)
    files = b"".join(path.encode("utf-16-le") + b"\x00\x00" for path in paths)
    return bytes(header) + files + b"\x00\x00"


def set_system_clipboard(paths=None, text=None, cut=False):
    """Вносит всё необходимое в буфер обмена"""
    user32, kernel32 = get_clipboard_api()
    if not open_clipboard(user32):
        return False
    try:
        user32.EmptyClipboard()
        result = True
        if text is not None:
            if not put_clipboard_bytes(
                user32, kernel32, 13, text.encode("utf-16-le") + b"\x00\x00"
            ):
                result = False
        if paths:
            if not put_clipboard_bytes(user32, kernel32, 15, build_dropfiles(paths)):
                result = False
            if cut:
                effect_format = user32.RegisterClipboardFormatW("Preferred DropEffect")
                if effect_format and not put_clipboard_bytes(
                    user32, kernel32, effect_format, (2).to_bytes(4, "little")
                ):
                    result = False
        return result
    finally:
        user32.CloseClipboard()


def clear_system_clipboard():
    """Очищает буфер обмена"""
    user32 = get_clipboard_api()[0]
    if not open_clipboard(user32):
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


def get_system_clipboard_paths():
    """Читает из буфера список существующих путей"""
    user32, kernel32 = get_clipboard_api()
    if not open_clipboard(user32):
        return []
    paths = []
    try:
        if user32.IsClipboardFormatAvailable(15):
            handle = user32.GetClipboardData(15)
            pointer = kernel32.GlobalLock(handle) if handle else None
            if pointer:
                data = ctypes.string_at(pointer, kernel32.GlobalSize(handle))
                header = ctypes.cast(pointer, ctypes.POINTER(DROPFILES)).contents
                paths = [
                    path
                    for path in data[header.pFiles :]
                    .decode("utf-16-le", "ignore")
                    .split("\x00")
                    if path and os.path.exists(path)
                ]
                kernel32.GlobalUnlock(handle)
        if not paths and user32.IsClipboardFormatAvailable(13):
            handle = user32.GetClipboardData(13)
            pointer = kernel32.GlobalLock(handle) if handle else None
            if pointer:
                path = ctypes.wstring_at(pointer)
                kernel32.GlobalUnlock(handle)
                if os.path.exists(path):
                    paths = [path]
    finally:
        user32.CloseClipboard()
    return paths


def is_hidden(path):
    """Определяет, спрятан ли файл/директория или нет"""
    try:
        return bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
    except (OSError, AttributeError):
        return False


class DBManager:
    """Класс для регуляции БД"""

    def __init__(self):
        """Инициализирует соединение с SQLite, создаёт таблицу files и определяет корневые директории"""
        self.db_name = os.path.join(MASTER_DIR, "file_manager.db")
        self.conn = sqlite3.connect(self.db_name)
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA journal_mode=WAL;")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS files (
                file_name TEXT NOT NULL,
                file_path TEXT PRIMARY KEY,
                file_size TEXT,
                is_directory INTEGER,
                modified_at TEXT,
                created_at TEXT
            )""")
        columns = {row[1] for row in self.cursor.execute("PRAGMA table_info(files)")}
        if "created_at" not in columns:
            self.cursor.execute("ALTER TABLE files ADD COLUMN created_at TEXT")
        self.conn.commit()
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if sys.platform.startswith("win") else ""
        self.root_dirs = (
            [f"{letter}:\\" for letter in letters if os.path.exists(f"{letter}:\\")]
            if letters
            else ["/"]
        )

    def scan_dir(self, abs_dir_path):
        """Сканирует переданную директорию, вносит её данные в БД и возвращает список путей внутри неё"""
        to_bd, result = [], []
        sql = """INSERT OR REPLACE INTO files
                 (file_name, file_path, file_size, is_directory, modified_at, created_at)
                 VALUES (?, ?, ?, ?, ?, ?)"""
        try:
            with os.scandir(abs_dir_path) as entries:
                for entry in entries:
                    try:
                        info = entry.stat()
                        to_bd.append(
                            (
                                entry.name,
                                entry.path,
                                str(info.st_size),
                                int(entry.is_dir()),
                                datetime.fromtimestamp(info.st_mtime).strftime(
                                    "%Y-%m-%d"
                                ),
                                datetime.fromtimestamp(info.st_ctime).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            )
                        )
                        result.append(entry.path)
                    except (OSError, PermissionError, FileNotFoundError):
                        continue
                    if len(to_bd) >= 100:
                        self.cursor.executemany(sql, to_bd)
                        to_bd = []
            if to_bd:
                self.cursor.executemany(sql, to_bd)
        except (OSError, PermissionError, FileNotFoundError):
            return
        self.conn.commit()
        return result


class NoFocusDelegate(QStyledItemDelegate):
    """Класс для того, чтобы убрать границы клеточек в таблицах"""

    def paint(self, painter, option, index):
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


class DragTableWidget(QTableWidget):
    """Для реализации drag-and-drop между двумя панелями"""

    def startDrag(self, supportedActions):
        """Передаёт выбранные локальные пути в QMimeData при перетаскивании строк таблицы"""
        window = self.window()
        if window is not None and hasattr(window, "get_selected_paths"):
            paths = window.get_selected_paths(self)
            if paths:
                drag = QDrag(self)
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
                drag.setMimeData(mime)
                drag.exec(supportedActions)
                return
        super().startDrag(supportedActions)


class SearchCleanDialog(QDialog):
    """Диалог поиска и очистки файлов"""

    def __init__(self, parent, mode):
        """Загружает UI диалога, задаёт режим поиска/очистки и подключает кнопки"""
        super().__init__(parent)
        uic.loadUi(os.path.join(MASTER_DIR, "For_search_and_clean.ui"), self)
        self.mode = mode
        self.parent_window = parent
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("Поиск" if mode == "search" else "Очистка")
        self.radioButton.setChecked(True)
        self.help_browser = QTextBrowser(self)
        self.help_browser.setGeometry(205, 10, 405, 130)
        self.help_browser.setStyleSheet("font: 8pt 'Arial';")
        self.help_browser.setReadOnly(True)
        self.help_browser.setPlainText(
            "Критерии:\n"
            "• По расширению: py, .txt.\n"
            "• По имени: имя без расширения; % — любые символы, _ — один символ. Например: %report%.\n"
            "• По дате: YYYY-MM-DD или ДД.ММ.ГГГГ; операторы <, <=, >, >=, =. Например: >=2026-01-01.\n\n"
            "Включать вложенные файлы — рекурсивный поиск; без флага — только текущая папка.\n"
            "Поиск идёт в активной панели. Очистка удаляет найденное после подтверждения."
        )
        self.setMinimumSize(620, 206)
        self.setMaximumSize(620, 206)
        self.resize(620, 206)
        self.pushButton.clicked.connect(self.run_action)
        self.pushButton_2.clicked.connect(self.reject)

    def parse_date_pattern(self, pattern):
        """Разбирает шаблон даты"""
        op = "="
        for candidate in ("<=", ">=", "<", ">", "="):
            if pattern.startswith(candidate):
                op, pattern = candidate, pattern[len(candidate) :].strip()
                break
        return op, pattern.replace(".", "-")

    def get_criterion(self):
        """Возвращает выбранный критерий"""
        if self.radioButton_2.isChecked():
            return "ext"
        if self.radioButton.isChecked():
            return "name"
        if self.radioButton_3.isChecked():
            return "modified"
        return "created"

    def build_sql(self, pattern, criterion):
        """Составляет sql-запрос к таблице"""
        pattern = pattern.strip()
        if criterion == "name":
            return (
                "SELECT file_name, file_path, file_size, is_directory, modified_at, created_at FROM files WHERE file_name LIKE ? OR file_name LIKE ?",
                (pattern, f"{pattern}.%"),
            )
        if criterion == "ext":
            ext = pattern if pattern.startswith(".") else f".{pattern}"
            return (
                "SELECT file_name, file_path, file_size, is_directory, modified_at, created_at FROM files WHERE file_name LIKE ?",
                (f"%{ext}",),
            )
        op, date = self.parse_date_pattern(pattern)
        column = "modified_at" if criterion == "modified" else "created_at"
        if column == "created_at":
            return (
                f"SELECT file_name, file_path, file_size, is_directory, modified_at, created_at FROM files WHERE substr(created_at, 1, 10) {op} ?",
                (date,),
            )
        return (
            f"SELECT file_name, file_path, file_size, is_directory, modified_at, created_at FROM files WHERE modified_at {op} ?",
            (date,),
        )

    def match_path(self, path, pattern, criterion):
        """Проверяет файл, если сразу подходит по критерию"""
        name = os.path.basename(path)
        if criterion == "name":
            stem = os.path.splitext(name)[0]
            sql = "SELECT 1 WHERE ? LIKE ?"
            row = self.parent_window.bd.cursor.execute(sql, (stem, pattern)).fetchone()
            return bool(row)
        if criterion == "ext":
            ext = pattern if pattern.startswith(".") else f".{pattern}"
            return name.lower().endswith(ext.lower())
        try:
            stat = os.stat(path)
        except OSError:
            return False
        op, target = self.parse_date_pattern(pattern)
        if criterion == "modified":
            value = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
        else:
            value = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d")
        if op == "<":
            return value < target
        if op == "<=":
            return value <= target
        if op == ">":
            return value > target
        if op == ">=":
            return value >= target
        return value == target

    def iter_scan_paths(self, recursive, scope_dir=None):
        """Для генерации списка путей для"""
        if scope_dir and scope_dir not in ("/", "__results__"):
            roots = [scope_dir]
        else:
            roots = self.parent_window.bd.root_dirs
        for root in roots:
            if not os.path.exists(root):
                continue
            if recursive:
                for cur, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if not is_hidden(os.path.join(cur, d))]
                    for name in files:
                        yield os.path.join(cur, name)
            else:
                try:
                    with os.scandir(root) as entries:
                        for entry in entries:
                            if entry.is_file():
                                yield entry.path
                except OSError:
                    continue

    def run_action(self):
        """Полностью всё совмещает и пробегается по шаблону"""
        pattern = self.lineEdit.text().strip()
        if not pattern:
            QMessageBox.warning(self, self.windowTitle(), "Введите шаблон поиска.")
            return
        criterion = self.get_criterion()
        recursive = self.checkBox.isChecked()
        show_hidden = self.parent_window.settings["show_hidden_dirs"]
        if self.mode == "clean":
            answer = QMessageBox.question(
                self,
                "Подтверждение очистки",
                "Удалить найденные файлы? Это действие необратимо.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        active_table = (
            self.parent_window.active_table or self.parent_window.tableWidget_1
        )
        cur_dir, prev_dir = self.parent_window.get_dirs_for_table(active_table)
        scope_dir = (
            prev_dir
            if cur_dir == "__results__" and prev_dir not in (None, "", "__results__")
            else cur_dir
        )
        self.parent_window.prepare_search_results(
            "Результаты поиска" if self.mode == "search" else "Результаты очистки"
        )
        self.pushButton.setEnabled(False)
        self.parent_window.loading_label.setText("Загрузка... Найдено: 0")
        QApplication.processEvents()
        found = []
        seen = set()

        def in_scope(path):
            """Решает, должен ли найденный путь участвовать в поиске/очистке"""
            norm_path = os.path.normcase(os.path.normpath(path))
            if not scope_dir or scope_dir in ("/", "__results__"):
                if recursive:
                    return True
                parent = os.path.normcase(os.path.normpath(os.path.dirname(path)))
                roots = [
                    os.path.normcase(os.path.normpath(r))
                    for r in self.parent_window.bd.root_dirs
                ]
                return parent in roots
            norm_scope = os.path.normcase(os.path.normpath(scope_dir))
            try:
                common = os.path.commonpath([norm_path, norm_scope])
            except ValueError:
                return False
            if common != norm_scope:
                return False
            if recursive:
                return True
            return (
                os.path.normcase(os.path.normpath(os.path.dirname(path))) == norm_scope
            )

        sql, params = self.build_sql(pattern, criterion)
        try:
            rows = self.parent_window.bd.cursor.execute(sql, params).fetchall()
        except Exception as error:
            QMessageBox.critical(self, self.windowTitle(), f"Ошибка запроса:\n{error}")
            self.parent_window.loading_label.setText("")
            self.pushButton.setEnabled(True)
            return
        stale = []
        for row in rows:
            path = row[1]
            if not in_scope(path):
                continue
            if not show_hidden and is_hidden(path):
                continue
            if not self.match_path(path, pattern, criterion):
                continue
            if os.path.exists(path):
                if path not in seen:
                    seen.add(path)
                    found.append(row)
                    self.parent_window.add_search_result_row(row)
                    self.parent_window.loading_label.setText(
                        f"Поиск... Найдено: {len(found)}"
                    )
                    QApplication.processEvents()
            else:
                stale.append(path)
        if stale:
            self.parent_window.bd.cursor.executemany(
                "DELETE FROM files WHERE file_path = ?", [(p,) for p in stale]
            )
            self.parent_window.bd.conn.commit()

        for path in self.iter_scan_paths(recursive, scope_dir):
            if path in seen:
                continue
            try:
                if not in_scope(path):
                    continue
                if not show_hidden and is_hidden(path):
                    continue
                if not self.match_path(path, pattern, criterion):
                    continue
                info = os.stat(path)
                row = (
                    os.path.basename(path),
                    path,
                    str(info.st_size),
                    0,
                    datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d"),
                    datetime.fromtimestamp(info.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                )
                self.parent_window.bd.cursor.execute(
                    "INSERT OR REPLACE INTO files (file_name, file_path, file_size, is_directory, modified_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    row,
                )
                seen.add(path)
                found.append(row)
                self.parent_window.add_search_result_row(row)
                self.parent_window.loading_label.setText(
                    f"Поиск... Найдено: {len(found)}"
                )
                if len(found) % 20 == 0:
                    self.parent_window.bd.conn.commit()
                QApplication.processEvents()
            except (OSError, PermissionError):
                continue
        self.parent_window.bd.conn.commit()
        if self.mode == "clean":
            errors = []
            deleted = 0
            for row in found:
                path = row[1]
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                        deleted += 1
                    elif os.path.exists(path):
                        os.remove(path)
                        deleted += 1
                    self.parent_window.bd.cursor.execute(
                        "DELETE FROM files WHERE file_path = ?", (path,)
                    )
                except (OSError, PermissionError) as error:
                    errors.append(f"{path}: {error}")
            self.parent_window.bd.conn.commit()
            table = self.parent_window.active_table or self.parent_window.tableWidget_1
            for row in range(table.rowCount() - 1, -1, -1):
                item = table.item(row, 0)
                path = item.data(Qt.ItemDataRole.UserRole) if item else None
                if path and not os.path.exists(path):
                    table.removeRow(row)
            self.parent_window.finish_search_results(deleted, mode="clean")
            self.parent_window.loading_label.setText(f"Удалено файлов: {deleted}")
            self.pushButton.setEnabled(True)
            if errors:
                QMessageBox.warning(self, "Очистка", "\n".join(errors[:20]))
            self.accept()
            return
        self.parent_window.finish_search_results(len(found), mode="search")
        self.parent_window.loading_label.setText(f"Найдено элементов: {len(found)}")
        self.pushButton.setEnabled(True)
        self.accept()


class YandexProject(QMainWindow):
    """Класс основного окна"""
    def __init__(self):
        """Создаёт главное окно: загружает настройки, интерфейс, панели и начальное состояние"""
        super().__init__()
        uic.loadUi(os.path.join(MASTER_DIR, "Yandex_Project.ui"), self)

        self.tables = (self.tableWidget_1, self.tableWidget_2)
        self.labels = (self.path_label, self.path_label_2)
        self.empty_labels = (self.disappointing_label1, self.disappointing_label2)
        for table in self.tables:
            table.__class__ = DragTableWidget

        self.settings_path = os.path.join(MASTER_DIR, "settings.json")
        with open(self.settings_path, encoding="utf-8") as f:
            self.settings = json.load(f)
        self.panel_mode = None
        self.apply_panel_layout()

        self.active_table = self.tableWidget_1
        self.group_by = [None, None]
        self.search_results_mode = False
        self.cur_dir = self.previous_dir = "/"
        self.cur_dir_2 = self.previous_dir_2 = "/"
        self.clicked_item = None
        self.bd = DBManager()
        self.search_quick_access_dirs(getpass.getuser())

        self.path_offsets = dict.fromkeys(self.labels, 0)
        self.path_parts = dict.fromkeys(self.labels, [])
        self.path_expanded = dict.fromkeys(self.labels, False)
        self.apply_theme()
        for label in self.labels:
            label.linkActivated.connect(self.navigate_from_path_label)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(False)

        self.buffer = []
        self.cut_mode = False
        self.load_disc()

        for table, selector in zip(
            self.tables, (self.select_in_first_table, self.select_in_second_table)
        ):
            table.itemDoubleClicked.connect(self.open_file)
            table.itemClicked.connect(self.file_click)
            table.itemSelectionChanged.connect(selector)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.drop_in_progress = False
        for table in self.tables:
            table.setDragEnabled(True)
            table.setAcceptDrops(True)
            table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
            table.setDefaultDropAction(Qt.DropAction.CopyAction)
            table.installEventFilter(self)
            table.setItemDelegate(NoFocusDelegate(table))
            table.setAlternatingRowColors(True)

        self.return_to_older_directory_button.clicked.connect(self.return_old_directory)
        self.quick_access.itemClicked.connect(self.mobile_access)
        self.quick_access.currentItemChanged.connect(self.update_delete_quick_access)
        self.pushButton.clicked.connect(self.save_text_file)
        self.update_button.clicked.connect(self.update)

        self.new_dir_action.triggered.connect(self.new_dir)
        self.new_text_action.triggered.connect(self.new_text)
        self.preview_action.triggered.connect(lambda: self.preview_and_edit())
        self.copy_on_other_window_action.triggered.connect(self.copy_in_other_window)
        self.move_to_other_window_action.triggered.connect(self.move_in_other_window)
        self.synchron_action.triggered.connect(self.synchron_dir)
        self.show_hidden_dirs_action.triggered.connect(self.hidden)
        self.dual_panel_mode_action.triggered.connect(self.switch_panel_mode)
        self.delete_action.triggered.connect(self.delete)
        self.rename_action.triggered.connect(self.rename)
        self.copy_action.triggered.connect(self.copy)
        self.copy_as_path_action.triggered.connect(self.copy_as_path)
        self.cut_action.triggered.connect(self.cut)
        self.merge_action.triggered.connect(self.merge)
        self.add_quick_access_action.triggered.connect(self.add_to_quick_access)
        self.delete_quick_access_action.triggered.connect(self.delete_quick_access)
        self.delete_quick_access_action.setEnabled(False)
        self.return_action.triggered.connect(self.return_old_directory)
        self.paste_action.triggered.connect(self.paste)
        self.select_all_action.triggered.connect(self.select_all)
        self.sort_action.triggered.connect(self.sort)
        self.information_set_action.triggered.connect(self.information_set)
        self.grouping_action.triggered.connect(self.grouping)
        self.search_action.triggered.connect(self.open_search_dialog)
        self.action.triggered.connect(self.synchron_dir)
        self.clear_action.triggered.connect(self.open_clean_dialog)
        self.mass_rename_action.triggered.connect(self.mass_rename)
        self.action_2.triggered.connect(lambda: self.set_theme("white"))
        self.action_3.triggered.connect(lambda: self.set_theme("black"))
        for table in self.tables:
            table.itemDelegate().closeEditor.connect(self.end_of_rename)

        self.block_all()
        self.cur_dir_2 = "/"
        self.previous_dir_2 = ""
        self.load_second_table()

    def get_table_index(self, table):
        """Индекс переданной таблицы"""
        return 0 if table is self.tableWidget_1 else 1

    def get_panel_dirs(self, i):
        """Текущая и предыдущая директории переданного индекса панели"""
        return (
            (self.cur_dir, self.previous_dir)
            if i == 0
            else (self.cur_dir_2, self.previous_dir_2)
        )

    def set_panel_dirs(self, i, cur_dir, previous_dir):
        """Новые значения для определённой таблицы"""
        cur = self.cur_dir if i == 0 else self.cur_dir_2
        if cur != cur_dir:
            self.group_by[i] = None
        if i == 0:
            self.cur_dir, self.previous_dir = cur_dir, previous_dir
        else:
            self.cur_dir_2, self.previous_dir_2 = cur_dir, previous_dir

    def get_active_panel_index(self):
        return self.get_table_index(self.active_table)

    def set_dirs(self, cur_dir, previous_dir):
        """Новые значения для активной таблицы"""
        self.set_panel_dirs(self.get_active_panel_index(), cur_dir, previous_dir)

    def get_other_dir(self):
        """Значения для неактивной таблицы"""
        return self.get_panel_dirs(1 - self.get_active_panel_index())[0]

    def get_dirs_for_table(self, table):
        return self.get_panel_dirs(self.get_table_index(table))

    def set_dirs_for_table(self, table, cur_dir, previous_dir):
        self.set_panel_dirs(self.get_table_index(table), cur_dir, previous_dir)

    def get_disappointing_label(self):
        """Дизапоинт строчка для активной таблицы"""
        return self.empty_labels[self.get_active_panel_index()]

    def split_dir_path(self, cur_dir):
        """Превращает строку пути в список пар"""
        parts = [("Этот компьютер", "/")]
        if not cur_dir or cur_dir == "/":
            return parts
        if cur_dir == "__results__":
            return [("Результаты", "__results__")]
        drive, tail = os.path.splitdrive(cur_dir)
        current = drive + os.sep if drive else "/"
        if drive:
            parts.append((drive, current))
        for name in [part for part in tail.split(os.sep) if part]:
            current = os.path.join(current, name)
            parts.append((name, current))
        return parts

    def update_path_label(self, table, cur_dir):
        """Берёт пары из предыдущео метода и создаёт нужный html"""
        i = self.get_table_index(table)
        label, prefix = self.labels[i], str(i + 1)
        results_mode = cur_dir == "__results__"
        if results_mode:
            scope_dir = self.get_dirs_for_table(table)[1]
            scope_dir = scope_dir if scope_dir and scope_dir != "__results__" else "/"
            parts = self.split_dir_path(scope_dir)
        else:
            parts = self.split_dir_path(cur_dir)
        self.path_parts[label] = parts
        offset = self.path_offsets.get(label, 0)
        expanded = self.path_expanded.get(label, False)
        if len(parts) > 4:
            if (not expanded and offset == 0) or offset > len(parts) - 1:
                offset = len(parts) - 3
        elif offset > 0 or expanded:
            offset, self.path_expanded[label] = 0, False
        self.path_offsets[label] = max(0, offset)
        theme = self.get_themes()[self.settings["theme"]]
        more_color = theme["path_more"]
        link_color = theme["path_link"]
        chunks = []
        if self.path_offsets[label] > 0:
            chunks.append(
                f'<a href="more{prefix}:1" style="text-decoration:none; color:{more_color}">...</a>'
            )
        for name, path in parts[self.path_offsets[label] :]:
            chunks.append(
                '<a href="dir{p}:{s}" style="text-decoration:none; color:{c}">{n}</a>'.format(
                    p=prefix,
                    s=html.escape(path, quote=True),
                    n=html.escape(name),
                    c=link_color,
                )
            )
        if results_mode:
            chunks.append(f'<span style="color:{more_color}">Результаты поиска</span>')
        label.setText(" &nbsp;--&gt;&nbsp; ".join(chunks))

    def navigate_from_path_label(self, link):
        """Разбирает href и переводит панель в соответствующий каталог"""
        kind = link[:4]
        if kind not in ("dir1", "dir2", "more"):
            return
        i = int(link[4]) - 1 if kind == "more" else 0 if kind == "dir1" else 1
        table, label = self.tables[i], self.labels[i]
        if kind == "more":
            self.path_expanded[label] = True
            self.path_offsets[label] = max(0, self.path_offsets.get(label, 0) - 3)
            self.update_path_label(table, self.get_panel_dirs(i)[0])
            return
        path = html.unescape(link[5:])
        self.active_table = table
        self.path_offsets[label], self.path_expanded[label] = 0, False
        if path == "__results__":
            cur_dir, prev_dir = self.get_panel_dirs(i)
            target = prev_dir if prev_dir and prev_dir != "__results__" else "/"
            self.set_panel_dirs(
                i, target, os.path.dirname(target) if target != "/" else "/"
            )
            if target == "/":
                self.load_disc(table)
            else:
                self.load_table(self.bd.scan_dir(target), table, target)
            return
        self.set_panel_dirs(i, path, os.path.dirname(path))
        if path == "/":
            self.load_disc(table)
        else:
            self.load_table(self.bd.scan_dir(path), table, path)

    def clear_preview(self):
        self.picture_label.clear()
        self.picture_label.hide()
        self.textEdit.show()
        self.lock_text_editor()

    def lock_text_editor(self):
        self.textEdit.clear()
        self.textEdit.setReadOnly(True)
        self.pushButton.setEnabled(False)

    def reload_active_table(self):
        cur_dir = self.get_panel_dirs(self.get_active_panel_index())[0]
        self.load_table(self.bd.scan_dir(cur_dir), self.active_table, cur_dir)

    def get_free_path(self, target_dir, name):
        """Возвращает свободный путь, добавляя suffixes (1), (2), если имя уже занято"""
        path = os.path.join(target_dir, name)
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(name)
        i = 1
        while os.path.exists(path := os.path.join(target_dir, f"{base} ({i}){ext}")):
            i += 1
        return path

    def update_actions(self):
        """Обновляет возможность нажатия кнопок в текущей директории"""
        in_disc = self.get_panel_dirs(self.get_active_panel_index())[
            0
        ] == "/" or self.search_results_mode
        self.sort_action.setEnabled(not in_disc)
        self.grouping_action.setEnabled(not in_disc)
        self.add_quick_access_action.setEnabled(not in_disc)
        self.preview_action.setEnabled(True)
        selected = self.get_selected_paths(self.active_table)
        has_selection = bool(selected)
        single = len(selected) == 1
        if not has_selection:
            self.clear_preview()
        for action in (
            self.delete_action,
            self.merge_action,
            self.copy_action,
            self.copy_as_path_action,
            self.cut_action,
        ):
            action.setEnabled(has_selection)
        self.rename_action.setEnabled(single)
        if self.settings["dual_panel"] == "on":
            self.copy_on_other_window_action.setEnabled(has_selection)
            self.move_to_other_window_action.setEnabled(has_selection)
            self.synchron_action.setEnabled(True)

    def update_paste_action(self):
        """Наблюдает за возможностью вставить скопированный файл/папку"""
        ready = any(os.path.exists(path) for path in self.buffer) or bool(
            get_system_clipboard_paths()
        )
        self.paste_action.setEnabled(ready)

    def block_all(self):
        """Отключает действия над выделением и сбрасывает временное состояние выбора"""
        for action in (
            self.copy_on_other_window_action,
            self.move_to_other_window_action,
            self.synchron_action,
            self.add_quick_access_action,
            self.delete_action,
            self.rename_action,
            self.copy_action,
            self.copy_as_path_action,
            self.cut_action,
            self.merge_action,
        ):
            action.setEnabled(False)
        self.clicked_item = None
        self.clear_preview()
        self.update_paste_action()
        self.update_actions()

    def get_selected_paths(self, table):
        """Возвращает существующие пути выделенных строк таблицы с учётом последнего клика"""
        paths = []
        for index in table.selectionModel().selectedRows():
            item = table.item(index.row(), 0)
            if item:
                path = item.data(Qt.ItemDataRole.UserRole)
                if path and os.path.exists(path):
                    paths.append(path)
        if not paths and self.clicked_item and self.clicked_item.tableWidget() is table:
            path = self.clicked_item.data(Qt.ItemDataRole.UserRole)
            if path and os.path.exists(path):
                paths.append(path)
        return paths

    def can_accept_drop(self, data):
        """Проверяет, можем ли мы принять хоть что-то из переданных данных"""
        if data.hasUrls():
            return any(url.isLocalFile() for url in data.urls())
        return data.hasFormat("application/x-qabstractitemmodeldatalist")

    def get_drag_paths(self, data):
        """Bзвлечение путей из мим данных"""
        if data.hasUrls():
            return [
                path
                for path in (
                    url.toLocalFile() for url in data.urls() if url.isLocalFile()
                )
                if os.path.exists(path)
            ]
        table = self.clicked_item.tableWidget() if self.clicked_item else None
        return self.get_selected_paths(table) if table else []

    def transfer(self, paths, target_dir, move=False, title="Операция"):
        """Метод, который копирует/переносит файлы и папки"""
        errors = []
        for path in paths:
            if not os.path.exists(path):
                continue
            name = os.path.basename(path.rstrip("\\/"))
            target = self.get_free_path(target_dir, name)
            try:
                if move:
                    shutil.move(path, target)
                elif os.path.isdir(path):
                    shutil.copytree(path, target)
                else:
                    shutil.copy2(path, target)
            except (OSError, shutil.Error) as error:
                errors.append(f"{name}: {error}")
        if errors:
            QMessageBox.critical(self, title, "\n".join(errors))
        self.update()

    def handle_drop(self, table, event):
        """Определяет, что делать при броске"""
        paths = self.get_drag_paths(event.mimeData())
        if paths:
            move = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.transfer(
                paths,
                self.get_panel_dirs(self.get_table_index(table))[0],
                move,
                "Перетаскивание",
            )

    def eventFilter(self, obj, event):
        """Перехват файлов из внешней  среды"""
        if obj not in self.tables:
            return super().eventFilter(obj, event)
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self.can_accept_drop(event.mimeData()):
                event.acceptProposedAction()
            else:
                event.ignore()
            return True
        if kind == QEvent.Type.Drop:
            self.drop_in_progress = True
            self.handle_drop(obj, event)
            event.acceptProposedAction()
            self.drop_in_progress = False
            self.update()
            return True
        return super().eventFilter(obj, event)

    def apply_panel_layout(self):
        """Переключает вторую таблицу в зависимости от режима"""
        mode = self.settings["dual_panel"]
        if mode == "off":
            self.tableWidget_2.move(1740, 40)
            self.tableWidget_2.setFixedSize(0, 0)
            self.tableWidget_2.hide()
            self.path_label_2.hide()
            self.disappointing_label2.hide()
        else:
            self.tableWidget_2.setMinimumSize(0, 0)
            self.tableWidget_2.setMaximumSize(16777215, 16777215)
            self.tableWidget_2.show()
            self.path_label_2.show()
            self.disappointing_label2.show()
        if self.panel_mode != mode:
            self.panel_mode = mode
            self.set_adaptive_window_size()
        self.apply_adaptive_geometry()

    def set_adaptive_window_size(self):
        """Изменяет размер окна под определённый режим"""
        width, height = {"off": (1370, 550), "on": (1050, 900)}[self.panel_mode]
        self.setMinimumSize(1000, 514)
        self.setMaximumSize(16777215, 16777215)
        self.resize(width, height)

    def apply_adaptive_geometry(self):
        """Определяет размеры виджетов при изменении размеров окна приложения"""
        base_width, base_height = {"off": (1370, 550), "on": (1050, 900)}[self.panel_mode]
        dx = self.width() - base_width
        dy = self.height() - base_height
        half = dy // 2
        table1_height = 391 + (half if self.panel_mode == "on" else dy)
        self.tableWidget_1.setGeometry(
            270, 41, max(240, 681 + dx), max(220, table1_height)
        )
        if self.panel_mode == "on":
            self.tableWidget_2.setGeometry(
                10, 450 + half, max(240, 701 + dx), max(220, 391 + half)
            )
            preview_x, preview_y = 730 + dx, 450 + half
            preview_height = max(160, 321 + half)
            save_x, save_y = 850 + dx, 780 + dy
        else:
            preview_x, preview_y = 980 + dx, 40
            preview_height = max(160, 321 + half)
            save_x, save_y = 980 + dx, 370 + half
        for widget in (self.picture_label, self.textEdit):
            widget.setGeometry(preview_x, preview_y, 311, preview_height)
        self.pushButton.setGeometry(save_x, save_y, 71, 41)
        self.update_table_column_widths()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_adaptive_geometry()

    def distribute_column_widths(self, table, weights, minimum=40):
        """Функция для пропорционального распределения ширины таблицы между колонками"""
        total = sum(weights)
        available = max(table.viewport().width() - 2, total * minimum)
        unit = available // total
        widths = [unit * weight for weight in weights]
        for column in range(available - sum(widths)):
            widths[column % len(widths)] += 1
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)

    def set_info_column_widths(self, table, info=None):
        if info is None:
            info = self.get_info_settings()
        weights = [weight for weight, on in zip(COLUMN_WEIGHTS, info) if on]
        self.distribute_column_widths(table, weights or [COLUMN_WEIGHTS[0]])

    def set_disc_column_widths(self, table):
        self.distribute_column_widths(table, (3, 2, 2, 2), 60)

    def update_table_column_widths(self):
        """Пересчитывает ширины колонок в обеих панелях в зависимости от содержимого таблицы"""
        for table in self.tables:
            if table.width() <= 0:
                continue
            if table.property("content") == "discs":
                self.set_disc_column_widths(table)
            elif table.property("content") == "files":
                self.set_info_column_widths(table)

    def load_second_table(self):
        """Проверяет, включен ли режим двойной панели, затем загружает окружение второй панели"""
        if self.settings["dual_panel"] != "on":
            return
        if self.cur_dir_2 == "/":
            self.load_disc(self.tableWidget_2)
        else:
            self.load_table(
                self.bd.scan_dir(self.cur_dir_2), self.tableWidget_2, self.cur_dir_2
            )
        self.synchron_action.setEnabled(True)

    def switch_panel_mode(self):
        """Включает/выключает режим двойной панели"""
        self.settings["dual_panel"] = (
            "off" if self.settings["dual_panel"] == "on" else "on"
        )
        self.save_settings()
        self.apply_panel_layout()
        self.block_all()
        self.load_second_table()

    def get_group_by(self, table):
        return self.group_by[self.get_table_index(table)]

    def is_group_item(self, item):
        """Является ли переданная ячейка заголовком группы"""
        return bool(item and item.data(Qt.ItemDataRole.UserRole + 1) == "group")

    def get_group_label(self, key, row_data):
        """Функция, которая для каждой строки файла определяет подпись группы"""
        name, path, size, is_directory, modified, created = row_data
        if key == "name":
            first = name[:1].upper()
            return first if first.isalnum() else "#"
        if key == "created":
            return (created or "")[:10] or "Без даты"
        if key == "modified":
            return (modified or "")[:10] or "Без даты"
        try:
            value = int(size)
        except (TypeError, ValueError):
            value = 0
        if value == 0:
            return "Пустые"
        if value < 1024 * 1024:
            return "Маленькие (< 1 МБ)"
        if value < 100 * 1024 * 1024:
            return "Средние (1-100 МБ)"
        if value < 1024 * 1024 * 1024:
            return "Большие (100 МБ - 1 ГБ)"
        return "Огромные (> 1 ГБ)"

    def get_file_icon(self, path, is_dir=False, quick_name=None):
        """Надевает кастомные/извлекает заложенные иконки директорий/файлов"""
        if not hasattr(self, "file_icons"):
            self.file_icons = {}
            self.icon_provider = QFileIconProvider()
        quick = {
            "desktop": "Dekstop.png",
            "downloads": "Downloads.png",
            "documents": "Documents.png",
            "pictures": "Images.png",
            "images": "Images.png",
            "music": "Music.png",
            "videos": "Video.png",
            "video": "Video.png",
        }
        if quick_name and quick_name.lower() in quick:
            name = quick[quick_name.lower()]
        elif is_dir:
            name = "disc.png" if path.rstrip("\\/").endswith(":") else "Folder.png"
        elif is_hidden(path):
            name = "Hidden.png"
        else:
            ext = os.path.splitext(path)[1].lower()
            ext_icons = {
                **{e: "Text.png" for e in (".txt", ".md", ".log", ".ini", ".json", ".csv", ".html", ".xml",)},
                **{e: "Python.png" for e in (".py",)},
                **{e: "Images.png" for e in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp")},
                **{e: "Video.png" for e in (".mp4", ".avi", ".mkv", ".mov", ".wmv")},
                **{e: "Music.png" for e in (".mp3", ".wav", ".flac", ".aac", ".ogg")},
            }
            if ext not in ext_icons:
                key = f"system:{path}"
                if key not in self.file_icons:
                    icon = self.icon_provider.icon(QFileInfo(path))
                    self.file_icons[key] = (
                        icon
                        if not icon.isNull()
                        else QIcon(os.path.join(MASTER_DIR, "images", "Info.png"))
                    )
                return self.file_icons[key]
            name = ext_icons[ext]
        if name not in self.file_icons:
            self.file_icons[name] = QIcon(os.path.join(MASTER_DIR, "images", name))
        return self.file_icons[name]

    def load_table(self, path_list, table=None, cur_dir=None):
        """Загружает окружение из переданных путей в переданную панель"""
        self.search_results_mode = False
        self.loading_label.setText("")
        table = table or self.tableWidget_1
        cur_dir = cur_dir or self.cur_dir
        path_list = [] if path_list is None else list(path_list)
        i = self.get_table_index(table)
        table.clearSpans()
        table.clearContents()
        table.setRowCount(0)
        settings = self.settings
        info = settings["info_settings"]
        cols = sum(info)
        names = [n for n, on in zip(("Имя", "Путь", "Размер", "Тип", "Изменён"), info) if on]
        table.setColumnCount(cols)
        table.setHorizontalHeaderLabels(names)
        table.setProperty("content", "files")
        self.set_info_column_widths(table, info)
        if not settings["show_hidden_dirs"]:
            path_list = [p for p in path_list if not is_hidden(p)]
        params = [f"{cur_dir}%", f"{cur_dir}%\\%"] + path_list
        query = "DELETE FROM files WHERE file_path LIKE ? AND file_path NOT LIKE ?"
        if path_list:
            query += f" AND file_path NOT IN ({', '.join('?' for entry in path_list)})"
        self.bd.cursor.execute(query, params)
        self.bd.conn.commit()

        self.empty_labels[i].setText("Похоже, здесь ничего нет :(" if not path_list else "")
        if not path_list:
            self.update_path_label(table, cur_dir)
            self.block_all()
            return

        placeholders = ", ".join("?" for _ in path_list)
        order_by = SORT_ORDER_BY[settings["sort"]]
        sql = (
            "SELECT file_name, file_path, file_size, is_directory, modified_at, created_at "
            f"FROM files WHERE file_path IN ({placeholders}) ORDER BY {order_by}"
        )
        rows = self.bd.cursor.execute(sql, path_list).fetchall()
        display_rows = []
        panel_index = self.get_table_index(table)
        group_by = self.group_by[panel_index] if cur_dir != "/" else None
        if group_by in dict(SORT_CRITERIA):
            groups = []
            index_by_label = {}
            for data in rows:
                label = self.get_group_label(group_by, data)
                if label not in index_by_label:
                    index_by_label[label] = len(groups)
                    groups.append((label, []))
                groups[index_by_label[label]][1].append(data)

            for label, group_rows in groups:
                display_rows.append(("group", label))
                for data in group_rows:
                    display_rows.append(("file", data))
        else:
            display_rows = [("file", data) for data in rows]
        table.setRowCount(len(display_rows))
        table.setVerticalHeaderLabels([""] * len(display_rows))
        for row, (kind, data) in enumerate(display_rows):
            if kind == "group":
                item = QTableWidgetItem(str(data))
                item.setData(Qt.ItemDataRole.UserRole + 1, "group")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                table.setItem(row, 0, item)
                for col in range(1, cols):
                    empty = QTableWidgetItem("")
                    empty.setFlags(Qt.ItemFlag.NoItemFlags)
                    table.setItem(row, col, empty)
                if cols > 1:
                    table.setSpan(row, 0, 1, cols)
                continue
            file_name, path, size, is_directory, modified, created_at = data
            is_dir = bool(is_directory)
            name = QTableWidgetItem(file_name)
            name.setData(Qt.ItemDataRole.UserRole, path)
            name.setIcon(self.get_file_icon(path, is_dir))
            table.setItem(row, 0, name)
            values = []
            if info[1]:
                values.append(path)
            if info[2]:
                values.append(convert(size))
            if info[3]:
                values.append("Папка" if is_dir else "Файл или другое")
            if info[4]:
                values.append(modified)
            for col, value in enumerate(values, start=1):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        self.update_path_label(table, cur_dir)
        self.block_all()

    def open_file(self, item):
        """Добавляет защиту для кода, пробует запустить файл/загружает содержимое выбранной дериктории и обновляет БД"""
        if self.drop_in_progress or self.is_group_item(item):
            return
        table = item.tableWidget()
        self.active_table = table
        path = table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        cur_dir = self.get_panel_dirs(self.get_active_panel_index())[0]
        if cur_dir != "/" and os.path.isfile(path):
            try:
                os.startfile(path)
            except (OSError, PermissionError) as error:
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось открыть файл!\n{error}"
                )
            return
        prev_dir = cur_dir
        if cur_dir == "__results__":
            prev_dir = self.get_dirs_for_table(table)[1]
            if prev_dir in (None, "", "__results__"):
                prev_dir = "/"
        self.set_dirs(path, prev_dir)
        new_dir = self.get_panel_dirs(self.get_active_panel_index())[0]
        self.load_table(self.bd.scan_dir(new_dir), table, new_dir)

    def return_old_directory(self):
        """Возвращает в предыдущую директорию"""
        cur_dir, previous_dir = self.get_panel_dirs(self.get_active_panel_index())
        if not previous_dir:
            return
        table = self.active_table
        if previous_dir == "/" or cur_dir in self.bd.root_dirs:
            self.load_disc(table)
        else:
            self.set_dirs(previous_dir, os.path.dirname(previous_dir))
            self.reload_active_table()
        self.get_disappointing_label().setText("")

    def get_info_settings(self):
        """Возвращает список аттрибутов, которые можно/нельзя показывать пользователю"""
        return list(self.settings["info_settings"])

    def get_current_selection_paths(self):
        """Возвращает список путей выбранных файлов/папок в текущей активной таблице"""
        table = self.active_table
        if table is None:
            return []
        paths = []
        for item in table.selectedItems():
            if item.column() != 0 or self.is_group_item(item):
                continue
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and path not in paths:
                paths.append(path)
        return paths

    def select_all(self):
        """Выделяет все строки в активной таблице"""
        if self.active_table:
            self.active_table.selectAll()

    def select_in_first_table(self):                    #|
        self.active_table = self.tableWidget_1          #|\
        self.update_actions()                           #| \

    def select_in_second_table(self):                   #| Делают определённый стол активным
        self.active_table = self.tableWidget_2          #|/
        self.update_actions()                           #|

    def file_click(self, item):
        """Обрабатывает одиночный клик по строке в таблице"""
        if item is None or self.is_group_item(item):
            self.clicked_item = None
            self.update_actions()
            return
        table = item.tableWidget()
        if table is not None:
            self.active_table = table
        self.clicked_item = item
        self.update_actions()

    def load_disc_mode(self, table=None):
        """Показан ли в определённой таблице список дисков?"""
        table = table or self.active_table or self.tableWidget_1
        return table.property("content") == "discs"

    def load_disc(self, table=None):
        """Загружает список дисков"""
        table = table or self.active_table or self.tableWidget_1
        self.search_results_mode = False
        self.group_by[self.get_table_index(table)] = None
        self.loading_label.setText("")
        table.clearSpans()
        table.clearContents()
        table.setRowCount(0)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Имя", "Тип", "Всего", "Свободно"])
        table.setProperty("content", "discs")
        self.set_disc_column_widths(table)
        disks = get_local_drives()
        table.setRowCount(len(disks))
        for row, disk in enumerate(disks):
            volume = get_disc_name(disk)
            name = f"{volume} ({disk.rstrip(chr(92))})" if volume else disk
            item = QTableWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, disk)
            item.setIcon(self.get_file_icon(disk, True))
            table.setItem(row, 0, item)
            table.setItem(row, 1, QTableWidgetItem("Локальный диск"))
            try:
                usage = shutil.disk_usage(disk)
                total = convert(usage.total * 8) if usage.total else "0 байт"
                free = convert(usage.free * 8) if usage.free else "0 байт"
            except OSError:
                total = free = ""
            table.setItem(row, 2, QTableWidgetItem(total))
            table.setItem(row, 3, QTableWidgetItem(free))
        self.set_dirs_for_table(table, "/", "/")
        self.update_path_label(table, "/")
        self.update_actions()

    def prepare_search_results(self, title=None):
        """Подготавливает активную таблицу к отображению результатов поиска/очистки"""
        self.search_results_mode = True
        table = self.active_table or self.tableWidget_1
        table.clearSpans()
        table.clearContents()
        table.setRowCount(0)
        info = self.settings["info_settings"]
        cols = sum(info)
        names = [
            n for n, on in zip(("Имя", "Путь", "Размер", "Тип", "Изменён"), info) if on
        ]
        table.setColumnCount(cols)
        table.setHorizontalHeaderLabels(names)
        table.setProperty("content", "files")
        self.set_info_column_widths(table, info)
        self.empty_labels[self.get_table_index(table)].setText("")
        cur_dir = self.get_dirs_for_table(table)[0]
        prev = (
            cur_dir if cur_dir != "__results__" else self.get_dirs_for_table(table)[1]
        )
        self.set_dirs_for_table(table, "__results__", prev)
        self.update_path_label(table, "__results__")
        self.block_all()

    def add_search_result_row(self, row):
        """Добавляет одну найденную строку в таблицу результатов поиска"""
        table = self.active_table or self.tableWidget_1
        file_name, path, size, is_directory, modified, created_at = row
        is_dir = bool(is_directory)
        info = self.settings["info_settings"]
        r = table.rowCount()
        table.insertRow(r)
        name = QTableWidgetItem(file_name)
        name.setData(Qt.ItemDataRole.UserRole, path)
        name.setIcon(self.get_file_icon(path, is_dir))
        table.setItem(r, 0, name)
        values = []
        if info[1]:
            values.append(path)
        if info[2]:
            values.append(convert(size))
        if info[3]:
            values.append("Папка" if is_dir else "Файл или ярлык")
        if info[4]:
            values.append(modified)
        for col, value in enumerate(values, start=1):
            table.setItem(r, col, QTableWidgetItem(str(value)))

    def finish_search_results(self, count=0, mode="search"):
        if mode == "search":
            self.search_results_mode = True
        self.block_all()

    def search_quick_access_dirs(self, username=None):
        """Перечитывает настройки и заполняет быстрый доступ базовыми и пользовательскими папками"""
        with open(self.settings_path, encoding="utf-8") as f:
            self.settings = json.load(f)
        self.quick_access.clear()
        base = [
            ("Desktop", "Desktop"),
            ("Downloads", "Downloads"),
            ("Documents", "Documents"),
            ("Pictures", "Pictures"),
            ("Music", "Music"),
            ("Videos", "Videos"),
        ]
        seen = set()
        for name, rel in base:
            path = os.path.normpath(os.path.join(os.path.expanduser("~"), rel))
            if path in seen:
                continue
            seen.add(path)
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setIcon(self.get_file_icon(path, True, quick_name=name))
            self.quick_access.addItem(item)
        for entry in self.settings["quick_access"]:
            path = (
                entry
                if os.path.isabs(entry)
                else os.path.join(os.path.expanduser("~"), entry)
            )
            path = os.path.normpath(path)
            if path in seen or not os.path.exists(path):
                continue
            seen.add(path)
            item = QListWidgetItem(os.path.basename(path) or path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setIcon(self.get_file_icon(path, os.path.isdir(path)))
            self.quick_access.addItem(item)
        self.update_actions()

    def delete_quick_access(self):
        """Удаляет выбранную пользовательскую папку из быстрого доступа и сохраняет настройки"""
        item = self.quick_access.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        base_paths = {
            os.path.normpath(os.path.join(os.path.expanduser("~"), n))
            for n in [
                "Desktop",
                "Downloads",
                "Documents",
                "Pictures",
                "Music",
                "Videos",
            ]
        }
        if os.path.normpath(path) in base_paths:
            return
        quick = self.settings["quick_access"]
        norm = os.path.normpath(path)
        self.settings["quick_access"] = [
            p
            for p in quick
            if os.path.normpath(
                p if os.path.isabs(p) else os.path.join(os.path.expanduser("~"), p)
            )
            != norm
        ]
        self.save_settings()
        self.search_quick_access_dirs()

    def merge(self):
        """Собирает выделенные элементы в подпапку merge текущей директории"""
        paths = self.get_current_selection_paths()
        if not paths:
            return
        table = self.active_table or self.tableWidget_1
        cur_dir = self.get_dirs_for_table(table)[0]
        merge_dir = os.path.join(cur_dir, "merge")
        os.makedirs(merge_dir, exist_ok=True)
        for path in paths:
            if not os.path.exists(path):
                continue
            dest = self.get_free_path(merge_dir, os.path.basename(path))
            try:
                shutil.move(path, dest)
            except Exception:
                pass
        self.reload_active_table()
        self.update_actions()

    def choice_dialog(self, title, options, current):
        """Показывает диалог выбора одного варианта и возвращает ключ или CANCEL"""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        group = QButtonGroup(dialog)
        for label, key in options:
            radio = QRadioButton(label, dialog)
            radio.setChecked(key == current)
            group.addButton(radio)
            layout.addWidget(radio)
            radio.choice_key = key
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for radio in group.buttons():
                if radio.isChecked():
                    return radio.choice_key
        return CANCEL

    def sort(self):
        """Выбирает критерий сортировки, сохраняет его и перезагружает таблицу"""
        options = [(label, key) for key, label in SORT_CRITERIA]
        key = self.choice_dialog(
            "Сортировка", options, self.settings["sort"]
        )
        if key is not CANCEL:
            self.settings["sort"] = key
            self.save_settings()
            self.reload_active_table()

    def grouping(self):
        """Выбирает критерий группировки для текущей панели и обновляет таблицу"""
        if self.load_disc_mode():
            return
        options = [("Без группировки", None)] + [
            (label, key) for key, label in SORT_CRITERIA
        ]
        key = self.choice_dialog(
            "Группировка", options, self.get_group_by(self.active_table)
        )
        if key is not CANCEL:
            self.group_by[self.get_table_index(self.active_table)] = key
            self.reload_active_table()

    def open_search_dialog(self):
        SearchCleanDialog(self, "search").exec()

    def open_clean_dialog(self):
        SearchCleanDialog(self, "clean").exec()

    def set_clipboard_selection(self, cut=False, paths_only=False):
        """Копирует выделенные пути во внутренний буфер и системный clipboard, noting cut"""
        paths = self.get_current_selection_paths()
        if not paths:
            return
        self.buffer = [] if paths_only else list(paths)
        self.cut_mode = bool(cut and not paths_only)
        set_system_clipboard(
            paths=None if paths_only else paths, text="\n".join(paths), cut=cut
        )
        self.update_paste_action()

    def copy_as_path(self):
        self.set_clipboard_selection(paths_only=True)

    def copy(self):
        self.set_clipboard_selection()

    def cut(self):
        self.set_clipboard_selection(cut=True)

    def paste(self):
        """Вставляет пути из внутреннего буфера или системного clipboard в текущую директорию"""
        table = self.active_table or self.tableWidget_1
        cur_dir = self.get_dirs_for_table(table)[0]
        if not cur_dir or cur_dir in ("/", "__results__"):
            return
        paths = [p for p in self.buffer if os.path.exists(p)]
        cut_mode = self.cut_mode
        if not paths:
            paths, cut_mode = get_system_clipboard_paths(), False
        if not paths:
            return
        errors = []
        for path in paths:
            if not os.path.exists(path):
                continue
            dest = self.get_free_path(cur_dir, os.path.basename(path))
            try:
                if cut_mode:
                    shutil.move(path, dest)
                elif os.path.isdir(path):
                    shutil.copytree(path, dest)
                else:
                    shutil.copy2(path, dest)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        if self.cut_mode and not errors:
            self.buffer, self.cut_mode = [], False
        self.reload_active_table()
        self.update_paste_action()
        if errors:
            QMessageBox.warning(self, "Вставка", "\n".join(errors[:10]))

    def delete(self):
        """Удаляет выделенные файлы/папки после подтверждения и чистит записи БД"""
        paths = self.get_current_selection_paths()
        if not paths:
            return
        answer = QMessageBox.question(
            self, "Удаление", f"Удалить выбранные элементы ({len(paths)})?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        errors = []
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
                self.bd.cursor.execute("DELETE FROM files WHERE file_path = ?", (path,))
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        self.bd.conn.commit()
        self.reload_active_table()
        if errors:
            QMessageBox.warning(self, "Удаление", "\n".join(errors[:10]))

    def rename(self):
        """Переименовывает единственный выделенный элемент и обновляет БД"""
        paths = self.get_current_selection_paths()
        if len(paths) != 1:
            return
        old_path = paths[0]
        base = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "Переименование", "Новое имя:", text=base
        )
        if not ok or not new_name or new_name == base:
            return
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        try:
            os.rename(old_path, new_path)
            self.bd.cursor.execute("DELETE FROM files WHERE file_path = ?", (old_path,))
            self.bd.conn.commit()
        except Exception as e:
            QMessageBox.warning(self, "Переименование", str(e))
        self.reload_active_table()

    def create_folder(self):
        """Создаёт новую папку в текущей директории с уникальным именем"""
        table = self.active_table or self.tableWidget_1
        cur_dir = self.get_dirs_for_table(table)[0]
        if not cur_dir or cur_dir in ("/", "__results__"):
            return
        name, ok = QInputDialog.getText(
            self, "Новая папка", "Имя папки:", text="Новая папка"
        )
        if not ok or not name:
            return
        path = self.get_free_path(cur_dir, name)
        try:
            os.makedirs(path, exist_ok=False)
        except Exception as e:
            QMessageBox.warning(self, "Новая папка", str(e))
        self.reload_active_table()

    def transfer_to_other_window(self, move=False):
        """Копирует или перемещает выделенные элементы в другую панель и обновляет обе таблицы"""
        if self.settings["dual_panel"] != "on":
            return
        paths, dst = self.get_current_selection_paths(), self.get_other_dir()
        if not paths or not dst or dst in ("/", "__results__"):
            return
        for path in paths:
            if not os.path.exists(path):
                continue
            target = self.get_free_path(dst, os.path.basename(path))
            try:
                if move:
                    shutil.move(path, target)
                elif os.path.isdir(path):
                    shutil.copytree(path, target)
                else:
                    shutil.copy2(path, target)
            except Exception:
                pass
        self.reload_active_table()
        other = (
            self.tableWidget_2
            if (self.active_table or self.tableWidget_1) is self.tableWidget_1
            else self.tableWidget_1
        )
        other_dir = self.get_dirs_for_table(other)[0]
        if other_dir and other_dir not in ("/", "__results__"):
            self.load_table(self.bd.scan_dir(other_dir), other, other_dir)

    def copy_in_other_window(self):
        self.transfer_to_other_window()

    def move_in_other_window(self):
        self.transfer_to_other_window(True)

    def preview_and_edit(self, path=None):
        """Показывает изображение или редактируемый текст выбранного файла в панели предпросмотра"""
        if path is None:
            paths = self.get_current_selection_paths()
            if len(paths) != 1:
                self.clear_preview()
                return
            path = paths[0]
        if not path or not os.path.isfile(path):
            self.clear_preview()
            return
        ext = os.path.splitext(path)[1].lower()
        self.clear_preview()
        if ext in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}:
            pix = QPixmap(path)
            if not pix.isNull():
                self.picture_label.setPixmap(
                    pix.scaled(
                        self.picture_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.picture_label.show()
                self.textEdit.hide()
            return
        if ext in {
            ".txt", ".py", ".json", ".md", ".log", ".csv", ".xml", ".html",
            ".css", ".js", ".ts", ".ini", ".cfg", ".yaml", ".yml",
            }:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception:
                return
            self.textEdit.show()
            self.textEdit.setPlainText(text)
            self.textEdit.setReadOnly(False)
            self.textEdit.setProperty("source_path", path)
            self.pushButton.setEnabled(True)
            return

    def save_text_file(self):
        """Сохраняет текст из редактора обратно в исходный файл и блокирует редактор"""
        path = self.textEdit.property("source_path")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.textEdit.toPlainText())
            QMessageBox.information(self, "Сохранение", "Файл сохранён")
        except Exception as e:
            QMessageBox.warning(self, "Сохранение", str(e))
            return
        self.lock_text_editor()

    def clear(self):
        self.buffer, self.cut_mode = [], False
        clear_system_clipboard()
        self.update_paste_action()

    def mass_rename(self):
        """Переименовывает выделенные файлы, добавляя префикс и порядковый номер"""
        paths = self.get_current_selection_paths()
        if not paths:
            return
        prefix, ok = QInputDialog.getText(self, "Массовое переименование", "Префикс:")
        if not ok:
            return
        for i, path in enumerate(paths, 1):
            ext = os.path.splitext(path)[1]
            new_path = self.get_free_path(os.path.dirname(path), f"{prefix}{i}{ext}")
            try:
                os.rename(path, new_path)
            except Exception:
                pass
        self.reload_active_table()

    def mobile_access(self, item):
        """Открывает выбранную папку быстрого доступа в панели или отправляет файл в предпросмотр"""
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if os.path.isdir(path):
            table = self.active_table or self.tableWidget_1
            self.set_dirs_for_table(table, path, self.get_dirs_for_table(table)[0])
            self.load_table(self.bd.scan_dir(path), table, path)
        elif os.path.isfile(path):
            self.preview_and_edit(path)

    def update_delete_quick_access(self, current=None, previous=None):
        """Включает кнопку удаления только для пользовательских записей быстрого доступа"""
        item = current or self.quick_access.currentItem()
        if item is None:
            self.delete_quick_access_action.setEnabled(False)
            return
        path = os.path.normpath(item.data(Qt.ItemDataRole.UserRole) or "")
        base = {
            os.path.normpath(os.path.join(os.path.expanduser("~"), n))
            for n in ["Desktop", "Downloads", "Documents", "Pictures", "Music", "Videos",]
        }
        self.delete_quick_access_action.setEnabled(path not in base)

    def add_to_quick_access(self):
        """Добавляет выбранные папки или текущий каталог в быстрый доступ и сохраняет настройки"""
        table = self.active_table or self.tableWidget_1
        paths = self.get_selected_paths(table)
        candidates = [p for p in paths if os.path.isdir(p)]
        if not candidates:
            cur_dir = self.get_dirs_for_table(table)[0]
            if (
                cur_dir
                and cur_dir not in ("/", "__results__")
                and os.path.isdir(cur_dir)
            ):
                candidates = [cur_dir]
        if not candidates:
            return
        base = {
            os.path.normpath(os.path.join(os.path.expanduser("~"), n))
            for n in [
                "Desktop",
                "Downloads",
                "Documents",
                "Pictures",
                "Music",
                "Videos",
            ]
        }
        quick = list(self.settings["quick_access"])
        existing = {
            os.path.normpath(
                p if os.path.isabs(p) else os.path.join(os.path.expanduser("~"), p)
            )
            for p in quick
        }
        changed = False
        for path in candidates:
            norm = os.path.normpath(path)
            if norm in base or norm in existing:
                continue
            quick.append(norm)
            existing.add(norm)
            changed = True
        if not changed:
            return
        self.settings["quick_access"] = quick
        self.save_settings()
        self.search_quick_access_dirs()

    def new_dir(self):
        self.create_folder()

    def new_text(self):
        """Создаёт пустой текстовый файл в текущей директории"""
        table = self.active_table or self.tableWidget_1
        cur_dir = self.get_dirs_for_table(table)[0]
        if not cur_dir or cur_dir in ("/", "__results__"):
            return
        path = self.get_free_path(cur_dir, "Новый текстовый документ.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            QMessageBox.warning(self, "Новый файл", str(e))
            return
        self.reload_active_table()

    def synchron_dir(self):
        """Синхронизирует директорию активной панели с другой панелью в режиме двух панелей"""
        if self.settings["dual_panel"] != "on":
            return
        table = self.active_table or self.tableWidget_1
        cur_dir = self.get_dirs_for_table(table)[0]
        other = (
            self.tableWidget_2 if table is self.tableWidget_1 else self.tableWidget_1
        )
        if not cur_dir or cur_dir in ("/", "__results__"):
            return
        self.set_dirs_for_table(other, cur_dir, self.get_dirs_for_table(other)[0])
        self.load_table(self.bd.scan_dir(cur_dir), other, cur_dir)

    def hidden(self):
        self.settings["show_hidden_dirs"] = not self.settings["show_hidden_dirs"]
        self.save_settings()
        self.reload_active_table()

    def information_set(self):
        """Настраивает видимость дополнительных информационных колонок таблицы"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Информация")
        layout = QVBoxLayout(dialog)
        info = self.get_info_settings()
        names = [("Путь", 1), ("Размер", 2), ("Тип", 3)]
        boxes = []
        for name, idx in names:
            cb = QCheckBox(name, dialog)
            cb.setChecked(bool(info[idx]))
            layout.addWidget(cb)
            boxes.append((idx, cb))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            info = [1, 0, 0, 0, 1]
            for idx, cb in boxes:
                info[idx] = 1 if cb.isChecked() else 0
            self.settings["info_settings"] = info
            self.save_settings()
            self.reload_active_table()

    def end_of_rename(self, editor, hint):
        """Завершает редактирование имени ячейки: переименовывает файл и обновляет БД"""
        table = self.active_table
        if table is None:
            return
        item = table.currentItem()
        if item is None or item.column() != 0 or self.is_group_item(item):
            return
        old_path = item.data(Qt.ItemDataRole.UserRole)
        new_name = item.text().strip()
        if not old_path or not new_name:
            self.reload_active_table()
            return
        old_name = os.path.basename(old_path)
        if new_name == old_name:
            return
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(
                self, "Переименование", "Файл с таким именем уже существует"
            )
            self.reload_active_table()
            return
        try:
            os.rename(old_path, new_path)
            self.bd.cursor.execute("DELETE FROM files WHERE file_path = ?", (old_path,))
            self.bd.conn.commit()
        except Exception as e:
            QMessageBox.warning(self, "Переименование", str(e))
        self.reload_active_table()

    def get_themes(self):
        return self.settings["themes"]

    def save_settings(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=4, ensure_ascii=False)

    def set_theme(self, theme):
        self.settings["theme"] = theme
        self.save_settings()
        self.apply_theme(theme)

    def apply_theme(self, theme=None):
        """Применяет stylesheet определённой темы к текущему интерфейсу"""
        theme = theme or self.settings["theme"]
        colors = self.get_themes()[theme]
        self.setStyleSheet(colors["main"])
        app = QApplication.instance()
        app.setStyleSheet(colors["app"])
        header_bg = "#e6e6e6" if theme == "white" else "#353535"
        header_fg = "#1e1e1e" if theme == "white" else "#ffffff"
        for table in self.tables:
            table.setStyleSheet(colors["table"])
            h_header = table.horizontalHeader()
            v_header = table.verticalHeader()
            h_header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            v_header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            h_header.setStyleSheet(
                f"QHeaderView {{ background-color: {header_bg}; color: {header_fg}; border: none; border-top-right-radius: 14px; }}"
                f"QHeaderView::section {{ background-color: {header_bg}; color: {header_fg}; border: none; padding: 4px; font: 10pt 'Arial'; }}"
            )
            v_header.setStyleSheet(
                f"QHeaderView {{ background-color: {header_bg}; color: {header_fg}; border: none; border-bottom-left-radius: 14px; }}"
                f"QHeaderView::section {{ background-color: {header_bg}; color: {header_fg}; border: none; padding: 4px; font: 10pt 'Arial'; }}"
            )
            corner = table.findChild(QAbstractButton)
            if corner:
                corner.setStyleSheet(
                    f"QTableCornerButton::section {{ background-color: {header_bg}; border: none; border-top-left-radius: 8px; color: {header_fg}; }}"
                )
        self.quick_access.setStyleSheet(colors["quick_access"])
        self.action_2.setChecked(theme == "white")
        self.action_3.setChecked(theme == "black")
        for table in self.tables:
            self.update_path_label(table, self.get_dirs_for_table(table)[0])


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DEFAULT_THEMES["black"]["app"])
    window = YandexProject()
    window.show()
    sys.exit(app.exec())
