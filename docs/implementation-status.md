# Stato verificabile

| Milestone | Disponibile | Da completare o verificare |
|---|---|---|
| M0–M1 | Contratti Pydantic, OpenAPI centrale, lockfile, corpus sintetico, parsing a coordinate, validazione e golden CSV | Schema gestionale, valuta, sconti e unità da confermare con ordini reali autorizzati |
| M2 | Tesseract CPU per pagina con baseline sintetica | Accuratezza sulle scansioni, benchmark su Windows e confronto con altri motori |
| M3 | Client React, anteprima raster, modifica, evidenziazione sorgente, annullamento, storico, profili numerici, mappatura manuale per pagina e riuso facoltativo delle colonne per intestazioni compatibili | Verifica su layout reali del pilota |
| M4–M5 | API FastAPI, migrazione SQL, dashboard, seriali, limite postazioni, lease offline, revoca firmata, DPAPI nel codice Windows | Staging con identity provider reale, test DPAPI e verifica delle policy sull’identità; rotazione del pin |
| M6 | Diagnostica disattivata di default, allowlist, accodamento, deduplicazione e registro amministrativo | Test di intero traffico, filtri completi e retention centralizzata indipendente dal traffico |
| M7 | Configurazione PyInstaller/Inno Setup, Job Object e AppContainer per il pacchetto Windows, contenimento e IPC verificati su Linux | Installer compilato e firmato, verifica di Job Object, AppContainer, rete e filesystem su Windows |
| M8 | Controllo volontario, metadati TUF, repository con accesso per canale, launcher separato, archivi verificati, conferma del frontend prima dell’attivazione e rollback simulato | Build e firma reali, prova WebView2 e rollback con migrazioni su Windows 11 |
| M9 | Configurazione Compose, Caddy e istruzioni di staging | Staging reale, restore verificato, SBOM, revisione licenze, prove con ordini reali e gate pilota |

Il motore OCR può confondere caratteri simili come O e 0 o saltare righe su griglie complesse. La revisione mostra sempre la pagina da cui deriva ogni riga disponibile. Le misure sintetiche descrivono un corpus ristretto con impaginazioni imparentate e non costituiscono una stima per clienti reali.

## Verifiche eseguite

- Suite Python su Linux e Chromium con PostgreSQL 17 reale e migrazione Alembic applicata. 31 test passati, incluso il flusso di revisione e download byte per byte.
- Baseline precedente alla doppia lettura Tesseract 5 su 30 PDF sintetici. Corrispondenza esatta del documento per 20/20 PDF digitali, 0/7 scansioni e 0/3 PDF misti. Sulle scansioni 20/21 righe corrispondono per articolo, quantità e prezzo, mentre sui PDF misti corrispondono 18/18. Le differenze includono caratteri nei lotti.
- Ruff, validazione del lockfile Python, build React e validazione della configurazione Compose.
- Build Docker e avvio del control plane con SQLite di test. La migrazione è stata eseguita all’avvio, `/health/ready` ha risposto e la dashboard è stata servita. Il test con PostgreSQL è stato eseguito separatamente.
- Verifica del download TUF con firme sintetiche. Coperti rifiuto di metadati scaduti o con firma errata, rollback dei metadati, pacchetto alterato, redirect, canale incompatibile e download monouso.
- Aggiornamento affiancato verificato con archivi sintetici. Coperti cambio riuscito, mancato avvio, ripristino dopo interruzione, mantenimento del DB e rifiuto di percorsi e collegamenti non consentiti. La firma Authenticode e il launcher compilato non sono stati eseguiti su Windows.
- Suite dopo aggiornamento e rollback: 53 test passati su Linux/Chromium, incluso il flusso PDF → CSV.
- Nuova prova OCR sui 30 PDF sintetici. Con il modello `eng` corrispondenza completa su 20/20 digitali, 5/7 scansioni e 3/3 misti. Articolo, quantità e prezzo coincidono su tutte le 21 righe attese dalle scansioni. Due lotti restano errati. Con `ita+eng` 4/7 scansioni complete e 18/21 righe con i tre campi critici corretti. I JSON conservano risultati e tempi per pagina. Il campione non basta a stimare l’accuratezza sugli ordini reali.
- Le letture OCR con segmentazioni diverse conservano testo e coordinate. In revisione l’app contrassegna i campi discordanti e mostra l’altra lettura. I numeri OCR non vengono normalizzati sulla base di somiglianze come O/0.
- Verificato il worker Linux con PDF scansionato. Confermati blocco socket per processo e figli, rimozione delle variabili d’ambiente estranee, limite memoria, arresto dei figli all’annullamento e rifiuto di artefatti malformati. Il kernel dell’ambiente non espone Landlock.
- Suite dopo il contenimento worker: 59 test passati su Linux/Chromium. Il Job Object resta da eseguire su Windows.
- Aggiunto avvio AppContainer Windows senza capability e verifica del token prima del parsing. Il runner Linux compila i moduli Python e prova il rifiuto dell’entry point fuori dal pacchetto. La prova AppContainer è predisposta nel job Windows e non è stata eseguita qui.
- La prova aggiornamento ora richiede che WebView2 carichi il frontend su una copia del database. Conferma del frontend verificata in Chromium, compreso il caso di un endpoint iniziale indisponibile. Il renderer effettivo di Windows resta da collaudare.
- Verificati il mantenimento dei profili storici SQLite, l’associazione solo dopo export, il suggerimento per un secondo PDF compatibile con valori diversi e l’assenza del suggerimento quando cambiano le intestazioni. I PDF con più layout non generano un’associazione.
- Aggiunto il trasferimento di posto con disattivazione firmata e registrata nel control plane. La prova di possesso usa una challenge monouso con un messaggio dedicato. Se la risposta si perde, la verifica online recupera lo stato definitivo.
- Trasferimento verificato con PostgreSQL 17.12 reale e due istanze distinte del control plane. Suite completa su Linux/Chromium: 71 passati, prova AppContainer Windows saltata.
- Mappatura manuale verificata in Chromium con intestazioni sconosciute. L’operatore ha selezionato le coordinate, recuperato le righe e scaricato il CSV. Verificate la conservazione delle altre pagine, la gestione delle revisioni stale e l’invalidazione dell’export dopo rilettura.
- I profili salvano solo bordi di colonna e impronta dell’intestazione. Il secondo PDF in Chromium ha una riga aggiuntiva. L’operatore indica la fine delle righe e la riga extra entra nel CSV. Una diversa intestazione non riceve il suggerimento.
- Suite finale su Linux e PostgreSQL 17.12: 77 passati, prova AppContainer Windows saltata.
- Ripristinato con `pg_restore` un backup `pg_dump --format=custom` su un database distinto. Verificati cliente, licenza, postazione e audit. L’installazione ha rinnovato il lease dopo il ripristino.
- Aggiornato PyJWT a 2.15.1 dopo l’audit e rigenerati SBOM Python e frontend. Gli audit delle dipendenze Python bloccate e npm runtime non hanno segnalato vulnerabilità note dopo l’aggiornamento. I binari esterni Windows richiedono l’inventario del pacchetto compilato.
- Collaudato l’archivio dei sorgenti estraendolo in una nuova directory. Verificati installazione pulita delle dipendenze, avvio della demo, attivazione sintetica, rifiuto dell’export anticipato e CSV identico alla fixture.
