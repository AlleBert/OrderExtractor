# Installazione e operatività

## Dashboard

Per la demo, usa il token generato dal launcher. Per una prova di staging, configura un issuer OIDC con access token firmati RS256 o ES256, audience dedicata e claim `amr` con `mfa`. Il provider deve emettere i ruoli applicativi in `roles`. Il flusso corrente richiede di inserire manualmente nella dashboard il token, che rimane nella memoria della scheda. `owner` e `admin` possono creare e modificare clienti e licenze, mentre `support` può consultare. Copia il seriale quando viene generato. La revoca di una postazione libera un posto. Viene rilevata alla prossima verifica online. Un dispositivo scollegato può proseguire fino alla scadenza del lease.

## Staging Linux

Copia `.env.example` in un file protetto, compila i campi con i valori di staging e fornisci una chiave Ed25519 distinta per ogni ambiente. Genera il pepper con un generatore crittografico, ad esempio `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Mantieni struttura e permessi del file di chiave compatibili con l’UID 10001 del container API.

```sh
umask 077
openssl genpkey -algorithm ED25519 -out lease-signing.pem
openssl pkey -in lease-signing.pem -pubout -out lease-public.pem
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose.yaml up --build -d
```

La configurazione richiede DNS e un dominio di staging accessibile a Caddy per HTTPS. Il database non è pubblicato dall’esempio Compose. La chiave privata va solo al server. Il client deve ricevere il file pubblico e l’indirizzo HTTPS autorizzato. Prima di ripristinare un database, sospendi le scritture, ripristina un dump in un database separato, verifica migrazione e `/health/ready`, poi verifica che un seriale sintetico completi attivazione e rinnovo. Per generare il dump, usa `pg_dump -Fc` con credenziali e storage protetti. Nessun ripristino su produzione è stato eseguito.

Se il proxy di build usa una CA aziendale, fornisci il bundle fidato come segreto BuildKit `build_ca`. La CA viene usata nella fase di download delle dipendenze. Il Dockerfile mantiene la verifica TLS.

## Build Windows di verifica

Su Windows 11 x64 installa uv, Node.js, Inno Setup e WebView2. Fornisci una directory Tesseract x64 con eseguibile, DLL, licenze e modelli `ita` e `eng` da tessdata_fast 4.1.0. Il build controlla gli SHA-256 in `installer/ocr-models.json`. Il default è `eng` in base al benchmark sintetico. Per layout o contenuti in italiano puoi impostare `-OcrLanguages ita` o una configurazione bilingue dopo averla confrontata sui tuoi documenti autorizzati. Il runtime legge modelli già presenti e non richiede il file di configurazione `tsv` della distribuzione Tesseract. Usa la chiave pubblica di staging:

```powershell
.\installer\build.ps1 -ControlUrl "https://staging.example" -LeasePublicKey ".\lease-public.pem" -TesseractDirectory "C:\staging\tesseract"
```

Lo script configura le risorse locali e costruisce PyInstaller onedir e un installer per utente, con checksum. La build e l’installazione richiedono verifica su Windows. La verifica Authenticode, WebView2, l’isolamento del processo PDF e l’aggiornamento con rollback su Windows sono gate di distribuzione ancora aperti.

## Incidenti di licenza

Conserva l’audit, ruota le credenziali compromesse e revoca le postazioni esposte. Le chiavi di firma non si possono sostituire in un client distribuito senza redistribuire la chiave pubblica attendibile. Fino alla loro scadenza i lease già rilasciati possono essere accettati offline. Apri la distribuzione commerciale solo dopo verifica privacy, firma e rotazione testata.

## Distribuzione dei metadati e download firmato

Prepara il repository TUF offline con chiavi di release separate dalla chiave delle licenze. Per abilitare il controllo nell’app Windows, passa `-UpdateTrustedRoot <root.json>`, `-PublisherThumbprint <thumbprint>` e `-TimestampUrl <url>` a `installer/build.ps1`. Il certificato deve essere accessibile tramite SignTool. Il build firma l’installer, il launcher e l’eseguibile applicativo prima di confezionare l’archivio. Il file include le chiavi pubbliche e le soglie di fiducia iniziali. Per una prova dal sorgente in modalità `client` imposta `OE_UPDATE_TRUSTED_ROOT` al percorso dello stesso file.

Ogni canale (`stable`, `beta`, `dev`) ha due directory nella radice server. In `metadata/` pubblica timestamp, snapshot, targets e le root versionate per la rotazione. In `targets/` pubblica `desktop.zip` a partire da `dist/desktop-<versione>.zip`, o usa `<sha256>.desktop.zip` con snapshot consistenti. Per il download manuale dai sorgenti è disponibile il target separato `installer.exe` (o `<sha256>.installer.exe`). Usa SHA-256 per i target; imposta nel campo firmato `custom` le proprietà `version` (tre componenti numeriche), `channel`, `product` (`OrderExtractor`), `platform` (`windows-x64`) e `notes` (fino a 2000 caratteri). Distribuisci il timestamp per ultimo dopo aver verificato firmatari, scadenze e presenza di tutti i file immutabili. Per il deploy Compose aggiungi `-f infra/updates.compose.yaml` e imposta `OE_HOST_UPDATES_ROOT` alla directory del repository, leggibile dal container.

Il controllo è manuale da **Impostazioni → Aggiornamenti**. Il client mostra versione, canale, dimensioni e note dopo la verifica TUF. Su Windows installato, chiudi eventuali ordini aperti e conferma **Installa al prossimo avvio**. Chiudi l’app e riaprila dal menu Start. Il launcher ricontrolla accesso alla release, hash, firma Authenticode, OCR nel worker e avvio del backend e del frontend in una finestra WebView2 nascosta su una copia del database. Al termine mostra l’esito nelle impostazioni. In caso di errore prima dell’attivazione resta selezionata la versione precedentemente verificata. Il journal consente il ripristino anche in caso di interruzione del cambio. Per aggiornare il launcher stesso, usa un nuovo installer firmato. Il download manuale dal sorgente resta disponibile con **Salva il pacchetto verificato**. Il collaudo corrente usa firme TUF, archivi e guasti sintetici su Linux. Per la distribuzione serve una prova su Windows 11 con certificato, installer, WebView2 e una migrazione applicativa reale.

## Collaudo AppContainer su Windows 11

Da PowerShell esegui il workflow equivalente con `uv run --locked python -m pytest tests/test_windows_appcontainer.py -q` e `uv run --locked python -m pytest tests/test_worker_limits.py -k cancel -q`. La prova del pacchetto descritta sotto apre WebView2 in una finestra nascosta, verifica SQLite e processa una scansione sintetica nel worker AppContainer. Il suo isolamento dipende anche dalle ACL dei file e dalle eccezioni loopback configurate sul dispositivo. Completa poi il collaudo interattivo dell’installer, del CSV e dell’aggiornamento firmato.

## Certificato e verifica della firma

Per una build destinata ai clienti configura il certificato Code Signing nel profilo Windows che eseguirà SignTool, oppure collega un provider di firma che espone il certificato allo stesso store. Usa il fornitore e il servizio di timestamp autorizzati dall’organizzazione. Mantieni la chiave privata nel provider o nel dispositivo di firma.

```powershell
Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } |
    Format-Table Subject, Thumbprint, NotAfter
Get-Command signtool.exe
```

Per il collaudo locale usa il comando di build di verifica sopra, senza i parametri di firma e aggiornamento. Per firmare passa il thumbprint restituito da Windows con `-PublisherThumbprint` e l’URL del timestamp del fornitore con `-TimestampUrl`. Puoi specificare il percorso dello strumento SDK con `-SignToolPath`. Per gli aggiornamenti integrati aggiungi anche `-UpdateTrustedRoot`.

Dopo l’installazione esegui questa verifica, che controlla il checksum e avvia il client su dati sintetici in una directory temporanea:

```powershell
.\installer\verify.ps1 -ReleaseVersion 0.1.0 `
    -InstallerPath .\dist\installer\OrderExtractor-0.1.0-x64.exe `
    -ReportPath .\verifica-windows.json
```

Su una build firmata aggiungi `-PublisherThumbprint` per imporre firma valida e corrispondenza del certificato sia per l’installer sia per il client. Il report contiene esito dei controlli, versione e checksum. Per il rilascio resta da provare il flusso interattivo completo con attivazione di staging, revoca, AppContainer, CSV e aggiornamento da una release firmata precedente.
