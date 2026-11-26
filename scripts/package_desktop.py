import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from order_extractor.version_store import EXECUTABLE, checked_paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if not (args.payload / EXECUTABLE).is_file():
        parser.error("Pacchetto desktop incompleto.")
    with ZipFile(args.archive, "w", compression=ZIP_DEFLATED) as archive:
        for file in sorted(args.payload.rglob("*")):
            if file.is_symlink() or file.is_junction():
                parser.error("Il pacchetto contiene un collegamento.")
            if file.is_file():
                archive.write(file, file.relative_to(args.payload).as_posix())
    with ZipFile(args.archive) as archive:
        list(checked_paths(archive))


if __name__ == "__main__":
    main()
