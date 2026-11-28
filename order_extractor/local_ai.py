"""Call local Ollama only to help interpret unresolved column headings."""

import json
import os
import time

import httpx

from .core import MAX_ROWS
from .manual import MAX_PAGE_CACHE, CachedText
from .structured_rows import StructuredDocument, StructuredRow
from .table_mapping import extract_table, normalize_number
from .worker_protocol import read_artifact

MODEL = "qwen2.5:3b"
OLLAMA = "http://127.0.0.1:11434"
MAX_RESPONSE = 384 * 1024


def ollama_request(method, path, *, payload=None, transport=None):
    try:
        with httpx.Client(base_url=OLLAMA, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(90, connect=2), transport=transport) as client:
            with client.stream(method, path, json=payload) as response:
                response.raise_for_status()
                buffer = bytearray()
                for chunk in response.iter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > MAX_RESPONSE:
                        raise ValueError("La risposta di Ollama supera il limite consentito.")
                return json.loads(buffer)
    except httpx.ConnectError as error:
        raise ValueError("Ollama non è raggiungibile. Avvialo sullo stesso PC.") from error
    except httpx.TimeoutException as error:
        raise ValueError("Ollama ha impiegato troppo tempo per rispondere.") from error
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        raise ValueError("Risposta di Ollama non valida. Verifica che qwen2.5:3b sia installato in locale.") from error


def check_model(*, transport=None):
    result = ollama_request("GET", "/api/tags", transport=transport)
    models = result.get("models", []) if isinstance(result, dict) else []
    if not isinstance(models, list) or not any(isinstance(item, dict) and item.get("name") == MODEL
                                               and not item.get("remote_host") for item in models):
        raise ValueError("Installa il modello locale con: ollama pull qwen2.5:3b")
    return {"model": MODEL, "ready": True}


def ollama_stream(path, payload, *, transport=None, timeout=90):
    try:
        with httpx.Client(base_url=OLLAMA, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(timeout, connect=2), transport=transport) as client:
            with client.stream("POST", path, json=payload) as response:
                response.raise_for_status()
                pending = b""
                for chunk in response.iter_bytes():
                    pending += chunk
                    if len(pending) > MAX_RESPONSE:
                        raise ValueError("La risposta di Ollama supera il limite consentito.")
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if line.strip():
                            yield json.loads(line)
                if pending.strip():
                    yield json.loads(pending)
    except httpx.TimeoutException as error:
        raise ValueError("Ollama ha impiegato troppo tempo per rispondere.") from error
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        raise ValueError("Ollama non è disponibile. Controlla il motore e il modello nelle impostazioni.") from error


def pull_model(progress, *, transport=None):
    sizes = {}
    for event in ollama_stream("/api/pull", {"model": MODEL, "stream": True}, transport=transport, timeout=300):
        if "error" in event:
            raise ValueError("Download del modello non riuscito.")
        if all(key in event for key in ("digest", "completed", "total")):
            sizes[event["digest"]] = (event["completed"], event["total"])
        progress(sum(done for done, _ in sizes.values()), sum(total for _, total in sizes.values()))
        if event.get("status") == "success":
            return
    raise ValueError("Il download del modello è terminato prima del completamento.")


def cpu_threads():
    try:
        import psutil
        physical = psutil.cpu_count(logical=False)
        available = len(psutil.Process().cpu_affinity()) if hasattr(psutil.Process, "cpu_affinity") else None
    except (ImportError, OSError):
        physical, available = None, None
    return max(1, min(physical or 2, available or os.cpu_count() or 1))


def infer_page(page, index, number_style, *, transport=None, progress=None, cancel=None, debug=None):
    debug = {} if debug is None else debug

    def ask(request):
        check_model(transport=transport)
        request.update(model=MODEL, stream=True, keep_alive="30s",
                       options={"temperature": 0, "num_ctx": 2048, "num_predict": 96, "num_gpu": 0,
                                "num_thread": cpu_threads()})
        chunks = []
        length = 0
        if progress:
            progress("ollama", 0)
        debug["ollama"]["called"] = True
        for event in ollama_stream("/api/chat", request, transport=transport):
            if cancel and cancel():
                raise ValueError("Analisi annullata.")
            if not isinstance(event, dict) or event.get("error"):
                break
            content = event.get("message", {}).get("content", "")
            length += len(content)
            if length > 16_384:
                break
            chunks.append(content)
            debug["ollama"]["response"] = "".join(chunks)
            if progress:
                progress("ollama", length)
            if event.get("done") is True:
                raw = "".join(chunks)
                stats = {key: event[key] for key in ("prompt_eval_count", "eval_count", "done_reason")
                         if key in event}
                try:
                    answer = json.loads(raw) if event.get("done_reason") != "length" else None
                except json.JSONDecodeError:
                    answer = None
                return {"answer": answer, "response": raw, **stats}
        raise ValueError("Risposta di Ollama incompleta. Usa Mappa colonne.")

    before = time.monotonic()
    try:
        rows = extract_table(page, index, number_style, ask, debug)
    except ValueError:
        if not debug.get("issues"):
            debug.setdefault("issues", []).append({"code": "extraction_blocked"})
        raise
    finally:
        debug["elapsed_ms"] = round((time.monotonic() - before) * 1000)
    document = StructuredDocument(rows=[
        StructuredRow(article=row.article or None, description=row.description or None,
                      quantity=normalize_number(row.quantity, "it" if number_style == "it" else "en") if row.quantity else None,
                      unit_price=normalize_number(row.unit_price, "it" if number_style == "it" else "en") if row.unit_price else None,
                      page=index + 1) for row in rows
    ], notices=[])
    return rows, document


def read_ai_page(directory, index):
    return CachedText.model_validate_json(read_artifact(directory / f"text-{index}.json", MAX_PAGE_CACHE))


def infer_document(directory, metadata, number_style, progress, *, cancel=None, report=None, unresolved=None):
    rows = []
    for index in range(metadata["page_count"]):
        if cancel and cancel():
            raise ValueError("Analisi annullata.")
        debug = {"page": index + 1, "engine": metadata["engines"][index], "issues": [], "mode": "nessun_testo"}
        progress(index, metadata["page_count"], "layout", 0)
        try:
            if index in metadata["mappable_pages"]:
                cached = read_ai_page(directory, index)
                proposed, _ = infer_page(
                    cached, index, number_style, cancel=cancel, debug=debug,
                    progress=lambda phase, chars: progress(index, metadata["page_count"], phase, chars),
                )
                rows.extend(proposed)
                if len(rows) > MAX_ROWS:
                    raise RuntimeError("Il documento supera il limite di righe.")
        except ValueError as error:
            if unresolved is None or cancel and cancel():
                raise
            unresolved(index, str(error))
        finally:
            if report:
                report(debug)
    return rows
