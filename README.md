# Order Extractor

Order Extractor estrae le righe dagli ordini PDF, permette di correggerle ed esporta un CSV. L’elaborazione avviene sul dispositivo.

## Scarica per Windows

Apri [GitHub Releases](https://github.com/AlleBert/OrderExtractor/releases/latest) e scarica `OrderExtractor-<versione>-x64.exe`. L’app richiede Windows 11 x64, Microsoft Edge WebView2 e un seriale emesso dal gestore. Avvia **Order Extractor** dal menu Start e attiva la licenza mentre sei online. L’installer include Tesseract. [Come verificare il download](docs/github-releases.md#controllare-linstaller)

In **Impostazioni → Aggiornamenti** puoi cercare le nuove versioni stabili. L’app verifica l’attestazione GitHub del pacchetto e prova la nuova versione prima di attivarla.

## Order Extractor 1.0.1 Beta

La beta si installa come **Order Extractor Beta**, con i propri dati locali. Usa il server licenze di Order Extractor e richiede l’attivazione come postazione distinta dalla stabile.

1. [Installa Ollama per Windows](https://ollama.com/download/windows), avvialo e scarica il modello una volta da PowerShell.

   ```powershell
   ollama pull qwen2.5:3b
   ```

2. In Order Extractor Beta apri **Impostazioni → AI locale → Verifica Ollama**.
3. Apri un ordine, seleziona una pagina e premi **Riprova con AI locale**. Se la pagina ha già delle righe, il pulsante chiede conferma prima di sostituirle. Verifica le proposte confrontandole con il PDF.
4. Usa **Scarica proposta JSON** per ottenere la proposta della pagina. Il file riporta il numero di pagina, i decimali normalizzati e gli avvisi per valori mancanti o ambigui. Per esportare eventuali correzioni dalla griglia usa il normale CSV.

La beta legge il testo nativo e usa RapidOCR per le scansioni, con Tesseract come riserva. Invia testo e coordinate solo a Ollama sullo stesso PC. Dopo aver scaricato Ollama e il modello e attivato la licenza, puoi collaudare l’estrazione scollegando Internet. La beta si aggiorna scaricando il suo installer.

## Prova l’app dai sorgenti

Servono Python 3.12, uv, un browser, libseccomp su Linux e Tesseract con il modello inglese `eng`. Per usare anche il modello italiano installa `ita` e imposta `OE_OCR_LANGS=ita+eng`. L’app non scarica modelli. Da terminale nella radice del progetto:

```sh
./scripts/demo.sh
```

Su Windows, per provare dai sorgenti, esegui `uv sync --locked` e poi `uv run --locked python -m order_extractor demo` in PowerShell. Il binario Tesseract deve essere nel PATH.

Il launcher apre l’app su un indirizzo locale e mostra nel terminale il seriale dimostrativo, l’indirizzo della dashboard e il suo token temporaneo. Inserisci il seriale nell’app, scegli **Usa l’ordine demo** o carica un PDF, controlla tutte le pagine, modifica le righe e seleziona **Esporta CSV** seguito da **Conferma e scarica**. Il gestionale riceve un CSV UTF-8, separato da punto e virgola, con CRLF e punto decimale. Per valori come `1.234` seleziona il formato numerico corrispondente al documento.

Ogni avvio demo genera un control plane temporaneo su loopback e una nuova licenza firmata. Il flusso di estrazione dopo l’attivazione funziona sul dispositivo e non richiede il server finché il lease è valido. Lo storico e i profili demo restano nella sottocartella `demo` dei dati locali. Per scegliere la cartella usa `--data-dir <cartella>`. Per avviare senza aprire automaticamente il browser usa `--no-browser`. L’app arresterà il servizio locale quando il processo viene chiuso.

## Rileggere una tabella con colonne sconosciute

Nell’anteprima scegli **Mappa colonne**. Indica l’inizio e la fine delle righe e il bordo sinistro delle colonne **Articolo**, **Descrizione**, **Quantità** e **Prezzo unitario**. Puoi fare clic sul PDF o inserire le percentuali. Aggiungi Lotto, Numero riga e Totale se presenti. **Rileggi questa pagina** sostituisce le righe di quella pagina. Le altre pagine e le righe inserite a mano restano nella griglia. Dopo la rilettura rivedi i valori e conferma di nuovo l’export.

La mappatura è disponibile finché l’ordine è aperto e la pagina contiene testo estratto. Se una riga continua su più pagine, correggila nella griglia.

## Profili per ordini ricorrenti

Dopo aver confermato e scaricato un CSV, apri **Impostazioni → Profili locali**, dai un nome alle impostazioni e seleziona **Suggerisci il profilo per ordini con questo layout**. Al prossimo PDF compatibile, **Applica profilo suggerito** carica formato numerico e valuta. Controlla comunque le righe prima dell’esportazione.

La corrispondenza usa un’impronta locale e versionata delle intestazioni riconosciute e della posizione delle colonne. Le righe e i valori commerciali sono esclusi dall’impronta. Per layout diversi o incerti puoi selezionare manualmente un profilo. Modificare i valori del profilo rimuove le associazioni precedenti, che puoi confermare di nuovo con un’esportazione.

## Trasferire una licenza a un’altra postazione

Chiudi l’ordine aperto, poi scegli **Impostazioni → Disattiva postazione**. La conferma online libera un posto. Usa il seriale della licenza per attivare la nuova postazione. I profili e lo storico restano sul dispositivo di origine. Se la risposta si interrompe, riapri le impostazioni e scegli **Verifica licenza online**. L’app controllerà se il server ha completato la disattivazione.

## Verifiche riproducibili

```sh
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked ruff check .
uv run --locked python -m pytest -q
uv run --locked python -m scripts.benchmark docs/benchmark-sintetico.json
```

Per l’E2E installa Chromium oppure esegui `uv run --locked playwright install chromium`. Puoi indicare il binario con `OE_CHROMIUM`. Per verificare il control plane usa un database PostgreSQL **di test** vuoto tramite `OE_TEST_POSTGRES=postgresql+psycopg://...` ed esegui `python -m pytest tests/test_control.py` dall’ambiente virtuale. I test creano record sintetici.

- [Stato e limiti](docs/implementation-status.md)
- [Architettura](docs/architecture.md)
- [Rischi e mitigazioni](docs/threat-model.md)
- [Benchmark sintetico con modello inglese](docs/benchmark-sintetico.json)
- [Benchmark sintetico con modelli italiano e inglese](docs/benchmark-sintetico-ita-eng.json)
- [Configurazione amministrativa, server e Windows](docs/operations.md)
- [Checklist di rilascio](docs/release-checklist.md)
- [Versioni e pubblicazione su GitHub](docs/github-releases.md)
