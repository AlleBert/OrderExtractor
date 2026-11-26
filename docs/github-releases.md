# Versioni e pubblicazione su GitHub

Il ramo `main` contiene il codice sorgente completo. Le modifiche passano per Git e vengono riepilogate nel [changelog](../CHANGELOG.md).

## Preparare una versione

1. Aggiorna la versione SemVer in `pyproject.toml` e `order_extractor/__init__.py`. Aggiorna `CHANGELOG.md` e integra le modifiche in `main`.
2. Verifica i job su `main` nella scheda **Actions**. La pipeline esegue i test Linux e Windows e compila e collauda l’installer sul runner Windows.
3. Crea un tag annotato `v<versione>-preview` sul commit verificato e invialo a GitHub.

```sh
git tag -a v0.1.0-preview -m 'Order Extractor 0.1.0 Preview'
git push origin v0.1.0-preview
```

La pipeline controlla che il tag corrisponda alla versione del pacchetto. Dopo i test e il collaudo del nuovo installer, pubblica una prerelease con l’EXE per Windows x64, `SHA256SUMS.txt` e il report di collaudo JSON. GitHub include gli archivi dei sorgenti del tag.

## Controllare l’installer

Scarica l’EXE e `SHA256SUMS.txt` dagli asset della stessa release. In PowerShell, confronta l’hash del file con quello indicato nel manifest.

```powershell
Get-FileHash .\OrderExtractor-0.1.0-x64.exe -Algorithm SHA256
```

Le anteprime sono distribuite senza firma Authenticode. Per un rilascio commerciale, segui la [checklist](release-checklist.md) e usa la [procedura di firma](operations.md#certificato-e-verifica-della-firma) con il certificato e il servizio licenze di produzione.
