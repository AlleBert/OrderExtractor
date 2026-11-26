# Checklist rilascio Order Extractor

## Anteprima dai sorgenti

- [x] Estrazione PDF digitale e OCR sintetico, revisione in Chromium, export byte per byte e conferma.
- [x] Mappatura manuale e riuso facoltativo delle colonne su un secondo PDF compatibile.
- [x] Licenze e concorrenza posti su PostgreSQL 17 con migrazioni.
- [x] Backup PostgreSQL in formato custom e ripristino in un database separato. L’installazione già attivata ha rinnovato il lease dopo il ripristino.
- [x] SBOM CycloneDX delle dipendenze bloccate Python e del frontend. Il manifest OCR distingue i modelli dal runtime Tesseract.
- [x] Prova dei metadati TUF e del rollback simulato.

## Gate Windows e pilota

- [ ] Installazione per utente su Windows 11 x64, rimozione programma e conservazione dei dati locali.
- [ ] AppContainer e rete verificati con il test Windows e con l’installer compilato. Verifica anche l’arresto del worker e dei figli.
- [ ] Attivazione reale di staging, scadenza offline, revoca e trasferimento con configurazione autorizzata.
- [ ] Firma Authenticode e checksum dell’installer e degli eseguibili. Controlla il firmatario con `installer/verify.ps1`.
- [ ] Due release firmate per provare aggiornamento, prima apertura in WebView2 e ripristino dopo un errore reale.
- [ ] Benchmark e revisione su ordini reali autorizzati e hardware Windows rappresentativo. Conferma schema CSV con il gestionale.
- [ ] Inventario finale di eseguibili, DLL, licenze, modelli e artefatti effettivamente distribuiti. Verifica legale e privacy.
- [ ] Approvazione del pilota, configurazione di supporto e canali di rilascio.

Il report locale di Windows può essere condiviso con chi approva la release. Conserva le chiavi di firma nel provider o dispositivo autorizzato. Il report e la checklist richiedono la convalida di chi esegue e approva il collaudo.
