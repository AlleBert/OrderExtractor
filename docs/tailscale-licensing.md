# Server licenze domestico con Tailscale

La console amministrativa usa Tailscale Serve su HTTPS. La porta della console e quella dell’API ascoltano sul loopback del server Linux. PostgreSQL resta sulla rete dei container.

## Se il server ospita già altri servizi Tailscale

Mantieni le configurazioni Serve o Funnel presenti. I comandi qui sotto usano la porta HTTPS 443 per la console e 8443 per i client soltanto come esempi. Su un nodo che occupa già entrambe, una combinazione possibile è Serve su 10443 per la console e su 10444 per [Keycloak](keycloak-tailnet.md), con Funnel su 10000 per l’API dei client esterni. Controlla le porte con `tailscale serve status`, `tailscale funnel status` e `ss` prima di aggiungere listener. Punta Funnel al solo proxy filtrato `http://127.0.0.1:18101`.

## Accesso riservato e MFA

1. Predisponi Docker Engine 28 o successivo, Docker Compose 2.24.4 o successivo e Tailscale sul server. Abilita MagicDNS e i certificati HTTPS nel tailnet. Tieni aggiornati i dispositivi con accesso amministrativo.
2. Configura il [provider OIDC e il proprietario](operations.md#console-privata-via-ssh). Se usi un’app Authenticator, rendi obbligatori password e TOTP per l’accesso alla console. Il token deve riportare `amr` con `mfa` solo quando il provider ha davvero verificato l’MFA. Deve riportare anche `roles` con `owner` e il `sub` esatto impostato come `OE_ADMIN_SUBJECT`. Il browser della console richiede di incollare il token di accesso.
3. Nel file `.env` imposta `OE_HOSTNAME=http://:8080` e completa PostgreSQL, chiave licenze e OIDC come descritto nella guida. Avvia lo stack dalla radice del repository.

```sh
docker compose --env-file .env -f infra/compose.yaml -f infra/private-admin.compose.yaml \
  -f infra/tailscale.compose.yaml up --build -d --wait --wait-timeout 120
```

Nel tailnet concedi al solo account proprietario l’accesso TCP alla porta 443 del server. Controlla anche i grant più ampi già presenti, perché i permessi Tailscale si sommano. Verifica che la porta sia libera nella configurazione Serve del nodo prima di registrare la console.

```sh
tailscale serve status
sudo tailscale serve --bg --https=443 http://127.0.0.1:18100
```

Sul tuo PC con Tailscale connesso, apri l’indirizzo HTTPS indicato dal comando. Verifica che la dashboard sia raggiungibile e che un token di un altro account, anche con MFA, venga rifiutato. Per l’accesso di manutenzione via SSH, riserva l’accesso alla porta 22 nel tailnet ai tuoi dispositivi o alla tua identità, secondo la policy di accesso adottata.

## Accesso dei client all’API licenze

Se tutti i client sono nel tailnet, esponi il proxy dell’API su un’altra porta e concedi ai dispositivi dei clienti solo quella porta. L’URL HTTPS restituito da Serve, incluso `:8443`, è il valore da inserire nel client Windows al momento della build.

```sh
sudo tailscale serve --bg --https=8443 http://127.0.0.1:18101
```

Se i client non sono nel tailnet, Serve non può raggiungerli. Usa un URL pubblico separato che inoltri **solo** al proxy filtrato sulla porta locale `18101`. Tailscale Funnel sulla porta HTTPS 8443 è una possibilità da verificare per il proprio tailnet. Serve e Funnel richiedono porte HTTPS distinte sullo stesso nodo. Controlla che la radice e `/api/v1/admin/customers` restituiscano 404 sull’URL dei client, quindi prova attivazione e rinnovo da un dispositivo cliente.

## Aggiornamenti del server da remoto

Accedi via SSH attraverso Tailscale e aggiorna da una revisione Git che hai verificato. Conserva insieme il backup del database, la revisione installata e una copia protetta della configurazione e delle chiavi. Nella finestra di manutenzione, ferma il servizio API per sospendere le scritture. Esegui dalla radice del repository dopo aver impostato `BACKUP_FILE` a un nuovo percorso protetto.

```sh
umask 077
docker compose --env-file .env -f infra/compose.yaml -f infra/private-admin.compose.yaml \
  -f infra/tailscale.compose.yaml stop api
set -C
docker compose --env-file .env -f infra/compose.yaml -f infra/private-admin.compose.yaml \
  -f infra/tailscale.compose.yaml exec -T postgres pg_dump -U order -Fc order > "${BACKUP_FILE:?Imposta un nuovo percorso di backup}"
```

Controlla l’esito del backup prima di proseguire. Recupera le revisioni con `git fetch --tags origin`, seleziona il commit verificato con `git switch --detach <SHA-verificato>` e ricostruisci i servizi con il comando Compose sopra. Verifica `/health/ready` attraverso Serve, accedi alla console e prova il rinnovo con una licenza di collaudo. Tieni disponibile l’immagine precedente. Se una migrazione rende la nuova struttura dati incompatibile con quella precedente, ripristina prima il backup in un database separato e verifica i dati prima di commutare il servizio.

Il flusso di aggiornamento attestato di Order Extractor Preview riguarda il client Windows. Per il server, una futura pipeline può pubblicare un’immagine Linux versionata e attestata, verificata per digest dal server prima dell’avvio. Il comando di manutenzione può restare sul server e fuori dal processo della console web.
