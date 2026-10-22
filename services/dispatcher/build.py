"""Đóng gói Lambda: python services/dispatcher/build.py."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
for module, handler in (("dispatcher", "app.py"), ("dlq", "dlq.py")):
    output = ROOT / "infra" / "build" / f"{module}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, name in (
            (ROOT / "services" / "dispatcher" / handler, handler),
            (ROOT / "spotpool_core" / "__init__.py", "spotpool_core/__init__.py"),
            (ROOT / "spotpool_core" / "contracts.py", "spotpool_core/contracts.py"),
        ):
            archive.write(path, name)
    print(output)
