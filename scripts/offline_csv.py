"""Extract all PDFs in a local input folder into validated CSV files."""

import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from order_extractor.core import Revision, export, validate
from order_extractor.extraction import extract
from order_extractor.local import MAX_UPLOAD
from order_extractor.local_ai import infer_document


def run(root, number_style, *, init=False):
    incoming, outgoing = root / "input", root / "output"
    incoming.mkdir(parents=True, exist_ok=True)
    outgoing.mkdir(parents=True, exist_ok=True)
    if init:
        print(f"Copia i PDF in {incoming}. I CSV saranno scritti in {outgoing}.")
        return 0
    sources = sorted(path for path in incoming.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    if not sources:
        print(f"Nessun PDF in {incoming}.", file=sys.stderr)
        return 1
    failed = 0
    for path in sources:
        target = outgoing / f"{path.stem}.csv"
        try:
            if target.exists():
                raise ValueError("CSV già presente.")
            if path.stat().st_size > MAX_UPLOAD:
                raise ValueError("Il PDF supera 50 MiB.")
            with TemporaryDirectory(prefix="oe-csv-") as temporary:
                work = Path(temporary)
                pdf = work / "source.pdf"
                shutil.copyfile(path, pdf)
                metadata = extract(pdf, work, lambda index, count: print(
                    f"{path.name} · lettura {min(index + 1, count)}/{count}", file=sys.stderr), text_only=True, use_pymupdf=True)

                def progress(index, count, phase, chars):
                    if chars == 0:
                        print(f"{path.name} · {phase} pagina {index + 1}/{count}", file=sys.stderr)

                rows = infer_document(work, metadata, number_style, progress)
            if not rows:
                raise ValueError("Nessuna riga articolo da esportare.")
            currencies = {row.currency for row in rows if row.currency}
            if len(currencies) > 1:
                raise ValueError("Il documento contiene più valute. Verificalo nell’app.")
            revision = Revision(revision=0, rows=rows, number_style="en" if number_style == "auto" else number_style,
                                currency=next(iter(currencies), ""), reviewed=True)
            blocking = [issue["message"] for issue in validate(revision) if issue["severity"] == "blocking"]
            if blocking:
                raise ValueError(blocking[0])
            payload = export(revision)
            with target.open("xb") as output:
                output.write(payload)
            print(f"{target.name} · {len(rows)} righe")
        except (OSError, ValueError, ImportError) as error:
            failed += 1
            print(f"{path.name} · {error}", file=sys.stderr)
    return int(failed > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--number-style", choices=("it", "en", "auto"), default="it")
    args = parser.parse_args()
    return run(args.directory, args.number_style, init=args.init)


if __name__ == "__main__":
    raise SystemExit(main())
