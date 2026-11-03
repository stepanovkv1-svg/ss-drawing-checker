"""ТЗ 3.2, 6.8: сетевой код допускается только в app/gateway (Э3). Сейчас его нет нигде."""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
FORBIDDEN = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "ftplib", "smtplib",
             "poplib", "imaplib", "telnetlib", "xmlrpc", "websocket", "websockets", "PySide6.QtNetwork",
             "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "webbrowser"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
            for a in node.names:
                yield f"{node.module}.{a.name}"


def test_no_network_imports_outside_gateway():
    bad = []
    for py in APP.rglob("*.py"):
        if "gateway" in py.relative_to(APP).parts:
            continue
        for name in _imports(py):
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
                bad.append(f"{py.relative_to(APP.parent)}: {name}")
    assert not bad, "Сетевой импорт вне app/gateway:\n" + "\n".join(bad)


def test_gateway_absent_until_stage3():
    assert not (APP / "gateway").exists(), "Пакет gateway появляется только на этапе Э3"
