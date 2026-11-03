import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

FONT_CANDIDATES = [r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]


@pytest.fixture(scope="session")
def font():
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    pytest.skip("нет шрифта с кириллицей для тестового PDF")


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory, font):
    from make_test_pdf import make
    out = tmp_path_factory.mktemp("pdf") / "НСС-02 тест.pdf"
    make(font, str(out))
    return out


@pytest.fixture(scope="session")
def sample_xlsx(tmp_path_factory):
    from make_test_pdf import make_xlsx
    out = tmp_path_factory.mktemp("xlsx") / "Спецификация НСС-02.xlsx"
    make_xlsx(str(out))
    return out


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SSDC_HOME", str(tmp_path / "home"))
