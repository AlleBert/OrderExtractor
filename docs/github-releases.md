# Versioni e pubblicazione su GitHub

Il ramo `main` contiene il codice sorgente completo. Le modifiche passano per Git e vengono riepilogate nel [changelog](../CHANGELOG.md).

## Preparare una versione

1. Aggiorna la versione SemVer in `pyproject.toml`, `uv.lock` e `order_extractor/__init__.py`. Aggiorna il changelog.
2. Verifica i job Linux e Windows nella scheda **Actions** e crea il tag sul commit verificato. Il tag `v<versione>` produce l’installer stabile. Il tag `v<versione>-preview` produce l’anteprima con licenza demo locale.
3. Apri la bozza della stabile e collauda l’attivazione del seriale su Windows fuori dal tailnet prima di pubblicarla. La pipeline verifica checksum e attestazioni di EXE e ZIP desktop prima di creare la bozza.

## Controllare l’installer

Scarica l’EXE e `SHA256SUMS.txt` dagli asset della stessa release. In PowerShell, confronta l’hash del file con quello indicato nel manifest.

```powershell
Get-FileHash .\OrderExtractor-1.0.0-x64.exe -Algorithm SHA256
```

Puoi verificare anche la provenienza dell’EXE con una versione aggiornata della [CLI di GitHub](https://cli.github.com/). Scarica `provenance.sigstore.jsonl` dalla stessa release ed esegui dalla cartella dei download. Per versioni successive, usa il nome dell’EXE e il tag corrispondenti.

```powershell
gh attestation verify .\OrderExtractor-1.0.0-x64.exe --bundle .\provenance.sigstore.jsonl --repo AlleBert/OrderExtractor --signer-workflow AlleBert/OrderExtractor/.github/workflows/tests.yml --source-ref refs/tags/v1.0.0 --deny-self-hosted-runners
```

Gli installer di queste release sono distribuiti senza firma Authenticode e Windows può mostrare un avviso. Per un rilascio commerciale, segui la [checklist](release-checklist.md) e usa la [procedura di firma](operations.md#certificato-e-verifica-della-firma) con il certificato e il servizio licenze di produzione.


## Aggiornare dall’app

Nell’app apri **Impostazioni → Aggiornamenti**, premi **Verifica aggiornamenti**, quindi **Installa al prossimo avvio**. Chiudi e riapri **Order Extractor** dal menu Start. Il launcher scarica il pacchetto desktop, verifica l’attestazione di GitHub Actions e prova l’avvio su una copia dei dati. Dopo la prova trovi l’esito nelle impostazioni.

La pipeline pubblica `desktop-<versione>-x64.zip` e `desktop-provenance.sigstore.jsonl` per il launcher. Il download e la verifica dipendono dall’accesso a GitHub e ai servizi di verifica Sigstore. Per aggiornare il launcher stesso e la sua CLI di verifica, usa l’installer EXE della release. Per riprodurre una build locale con questa funzione, passa `-LocalPreview -GitHubCliDirectory <cartella-zip-estratta>` a `installer/build.ps1` con la CLI di GitHub per Windows x64.
