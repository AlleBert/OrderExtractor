"""Package the source preview and the compiled frontend without local state."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT_FILES = (
    ".dockerignore", ".env.example", ".gitattributes", ".gitignore", "AGENTS.md", "CHANGELOG.md",
    "README.md", "alembic.ini", "pyproject.toml", "uv.lock",
)
SOURCE_FOLDERS = (".github", "docs", "fixtures", "frontend", "infra", "installer",
                  "migrations", "order_extractor", "packages", "scripts", "tests")
IGNORE_NAMES = {"__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}
TYPES = {".css", ".html", ".ico", ".ini", ".js", ".json", ".jsx", ".md", ".pdf", ".png", ".ps1", ".py",
         ".sh", ".toml", ".txt", ".yaml", ".yml", ".iss", ".mako"}


def collect(root):
    for name in ROOT_FILES:
        yield root / name
    for folder in SOURCE_FOLDERS:
        for file in sorted((root / folder).rglob("*")):
            relative = file.relative_to(root)
            if not IGNORE_NAMES.intersection(relative.parts) and file.is_file():
                if file.suffix in TYPES or file.name in {"Dockerfile", "Caddyfile"}:
                    yield file


def package(root, destination):
    destination = destination.resolve()
    if not (root / "order_extractor/web/app.js").is_file():
        raise ValueError("Compila il frontend prima di creare il pacchetto.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for file in collect(root):
            if file.is_symlink() or not file.resolve().is_relative_to(root):
                raise ValueError(f"Collegamento sorgente non consentito: {file.name}")
            entry = ZipInfo("order-extractor/" + file.relative_to(root).as_posix())
            entry.create_system = 3
            mode = 0o100755 if file.suffix == ".sh" else 0o100644
            entry.external_attr = mode << 16
            archive.writestr(entry, file.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    package(Path(__file__).resolve().parents[1], args.destination)
