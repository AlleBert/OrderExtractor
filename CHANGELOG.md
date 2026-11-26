# Changelog

## 1.0.1 Beta

- Rilettura su richiesta di una pagina tramite Ollama locale con `qwen2.5:3b`.
- Proposta JSON versionata con celle di origine verificate e decimali normalizzati.
- RapidOCR su CPU per le scansioni, con Tesseract come riserva.
- Installer beta separato con attivazione sul server licenze esistente.

## 1.0.0

- Prima build stabile di Order Extractor per Windows 11 x64, con attivazione tramite seriale.
- Aggiornamenti in-app delle release stabili, con verifica dell’attestazione GitHub del pacchetto.
- Console licenze privata con accesso dal browser tramite Keycloak e Authenticator.

## 0.1.4

Nuova anteprima distribuita anche come aggiornamento in-app dalla 0.1.3. La pipeline include un collaudo Windows del percorso di aggiornamento.

## 0.1.3

La Preview Windows può cercare e installare le nuove anteprime pubblicate su GitHub. Il launcher verifica l’attestazione del pacchetto e prova la nuova versione su una copia dei dati prima di attivarla.

## 0.1.2

Nuovo logo nell’app, nella dashboard e nell’installer Windows. Il manuale spiega come accedere all’amministrazione e provare la creazione di seriali.

## 0.1.1

L’installer Windows include un’attestazione di provenienza GitHub Actions. La pipeline verifica l’attestazione e lo SHA-256 prima di pubblicare la prerelease.

## 0.1.0

Prima anteprima Windows distribuita da GitHub Releases. L’installer per utente include Tesseract e attiva la demo in locale.

Baseline locale di estrazione PDF, OCR CPU, revisione ed export CSV con demo licenze, dashboard e diagnostica facoltativa.

L’anteprima include profili di layout, riuso della mappatura manuale, trasferimento licenze, aggiornamenti TUF, worker AppContainer in build Windows e prova di avvio su snapshot prima dell’attivazione di un aggiornamento.
