# Identità amministrativa privata con Keycloak

Questo stack opzionale usa Keycloak con un database PostgreSQL dedicato. Per il nodo che ha già servizi sulle porte Tailscale 443 e 8443, l’esempio usa le porte private 10444 per Keycloak e 10443 per la console. L’API licenze per i client esterni può usare Funnel sulla 10000. Sostituisci `server.tailnet.ts.net` con il DNS del tuo nodo e verifica che le tre porte siano libere. Sul server verifica anche le porte locali 18100, 18101 e 18102.

## 1. Avvia Keycloak solo nel tailnet

Crea `.env.identity` partendo da `.env.identity.example`. Proteggi il file con permessi 600 e usa due password distinte e casuali, una per PostgreSQL e una per l’amministratore temporaneo. Imposta `OE_IDENTITY_URL=https://server.tailnet.ts.net:10444` e compila le altre variabili dell’esempio. Riserva l’accesso alla 10444 alla sola identità Tailscale del proprietario prima di aprire Serve. Controlla anche i grant preesistenti che autorizzano l’accesso al nodo.

```sh
docker compose --env-file .env.identity -f infra/keycloak.compose.yaml up -d --wait --wait-timeout 240
sudo tailscale serve --bg --https=10444 http://127.0.0.1:18102
```

Dal tuo PC nel tailnet apri l’URL privato di Keycloak e accedi con l’amministratore temporaneo. Usa quell’account per configurare il realm. Dopo il collaudo, configura e prova un accesso amministrativo permanente con MFA anche per Keycloak prima di rimuovere l’account temporaneo e le relative variabili di bootstrap.

## 2. Configura l’identità di Order Extractor

- Crea il realm `order-extractor`, il ruolo di realm `owner` e il tuo utente. Assegna `owner` solo a quell’utente e annota il suo ID. Il realm può mantenere disattivata la registrazione pubblica. Fai impostare la password e registrare il codice TOTP con l’azione obbligatoria **Configure OTP**.
- Nel realm configura un flusso **Browser** con verifica della password e dell’OTP obbligatorie. Imposta **Authenticator Reference** a `mfa` e **Authenticator Reference Max Age** a `120` secondi sull’esecuzione OTP effettivamente usata. In Keycloak 26.8.2, un valore assente o pari a zero può far scadere il riferimento prima che il flusso Device Authorization emetta il token. Evita rami di login che possano completare l’autenticazione saltando l’OTP. Usa questo flusso per il client della console e collauda da una sessione nuova e con una sessione SSO già aperta.
- Crea un client OIDC pubblico con ID `order-extractor-console`. Abilita **OAuth 2.0 Device Authorization Grant** per il flusso illustrato sotto. Disabilita **Direct Access Grants**. Aggiungi i mapper al token di accesso per **Authentication Method Reference**, **User Realm Role** nel claim multivalore `roles` e **Audience** con **Included Custom Audience** impostato a `order-extractor-console` e **Included Client Audience** vuoto. Controlla un token di prova emesso dal flusso con OTP prima del deploy.
- Nel file `.env` di Order Extractor imposta `OE_OIDC_ISSUER=https://server.tailnet.ts.net:10444/realms/order-extractor`, `OE_OIDC_AUDIENCE=order-extractor-console`, `OE_OIDC_JWKS` allo stesso issuer seguito da `/protocol/openid-connect/certs` e `OE_ADMIN_SUBJECT` al claim `sub` del tuo utente. Verifica il valore effettivo nel token.

Per verificare il flusso, inserisci un nuovo codice dispositivo in una finestra privata e controlla che Keycloak richieda realmente password e OTP. Dopo la registrazione iniziale di Authenticator, ripeti il test con un nuovo codice per verificare l’OTP. Verifica che il token abbia `aud` con `order-extractor-console`, `amr` con `mfa` e `roles` con `owner`.

### Accesso quotidiano dal browser

Per il client pubblico `order-extractor-console` abilita **Standard flow** e imposta **Proof Key for Code Exchange Code Challenge Method** a **S256**. In **Valid redirect URIs** aggiungi l’indirizzo esatto `https://server.tailnet.ts.net:10443/auth/callback`. Il callback si apre nella stessa scheda. Lascia vuoti i **Web origins** se non servono ad altre integrazioni, perché lo scambio del codice avviene tra API e Keycloak nella rete Docker. Mantieni il flusso Browser con password e OTP e i mapper dei claim.

Imposta `OE_ADMIN_ORIGIN=https://server.tailnet.ts.net:10443` nel file `.env` dello stack licenze e ricrea il servizio API con i file Compose del punto 3. Apri la console da un dispositivo nel tailnet, fai clic su **Accedi con Authenticator** e completa password e OTP. La sessione della console resta nella scheda e dura al massimo 15 minuti, entro la scadenza del token Keycloak. **Esci** la revoca. Il riavvio del servizio chiude le sessioni attive.

### Diagnosi del flusso Device Authorization

Dal server, con `curl` e `jq` disponibili, chiedi un token per la console:

```sh
./scripts/keycloak-admin-token.sh https://server.tailnet.ts.net:10444/realms/order-extractor
```

Apri nel browser indicato l’URL di verifica e inserisci il codice mostrato nel terminale. Completa l’autenticazione con Authenticator. Il comando emette un token per il collaudo amministrativo nella rete privata. Il login quotidiano della console usa il flusso Browser quando è configurato `OE_ADMIN_ORIGIN`.

### Se il DNS del nodo punta a Funnel

Quando lo stesso nome Tailscale serve anche un Funnel, il resolver del server può restituire un IP pubblico. Se la porta privata funziona quando indirizzi la richiesta all’IPv4 Tailscale del server, passa quell’IP allo script. L’issuer, il nome TLS e la verifica del certificato restano quelli HTTPS di Keycloak.

```sh
OE_IDENTITY_TAILNET_IP="$(tailscale ip -4)" \
  ./scripts/keycloak-admin-token.sh https://server.tailnet.ts.net:10444/realms/order-extractor
```

Lo stack Keycloak crea la rete interna Docker `order-extractor-oidc`. Il file `infra/tailscale-keycloak.compose.yaml` collega l’API a questa rete per leggere il JWKS direttamente da Keycloak via HTTP. Il browser usa HTTPS su Tailscale Serve e l’API verifica l’issuer HTTPS, la firma, l’audience, l’MFA e il proprietario. Aggiungi solo Keycloak e l’API licenze a questa rete.

Se hai già avviato entrambi gli stack con una revisione precedente, aggiorna il repository e riapplica prima lo stack Keycloak con il comando del punto 1. Poi ricompila lo stack licenze come al punto 3. Riusa i file `.env.identity` e `.env` esistenti.

## 3. Collauda la console prima di esporre l’API

Per una prima installazione senza `.env`, puoi generare chiavi e configurazione in locale con lo script di inizializzazione. Ottieni prima il token amministrativo con lo script precedente e conservalo nella variabile di shell `TOKEN`. L’inizializzazione estrae il `sub` dai claim controllati per configurare il proprietario. L’API convalida firma e scadenza del token quando accedi. Il comando richiede `sudo` per assegnare la chiave privata all’UID del container.

```sh
printf '%s' "$TOKEN" | ./scripts/init-tailnet-licensing.sh \
  https://server.tailnet.ts.net:10444/realms/order-extractor
docker compose --env-file .env -f infra/compose.yaml -f infra/private-admin.compose.yaml \
  -f infra/tailscale.compose.yaml -f infra/tailscale-keycloak.compose.yaml \
  up --build -d --wait --wait-timeout 180
```

Verifica dall’API in container che le chiavi del realm siano raggiungibili. Esegui il comando dalla radice del repository con lo stack licenze già avviato.

```sh
docker compose --env-file .env -f infra/compose.yaml -f infra/private-admin.compose.yaml \
  -f infra/tailscale.compose.yaml -f infra/tailscale-keycloak.compose.yaml exec -T api python -c \
  'import jwt; from order_extractor.launcher import admin_jwks_url; print("JWKS:", len(jwt.PyJWKClient(admin_jwks_url(), timeout=8).get_signing_keys()), "chiavi di firma")'
```

Riserva l’accesso a TCP 10443 alla sola identità Tailscale del proprietario e pubblica la console nel tailnet.

```sh
sudo tailscale serve --bg --https=10443 http://127.0.0.1:18100
```

Verifica che la console accetti il token dopo OTP. Prova anche il rifiuto di token scaduti e di altri utenti. Quando questi controlli sono passati, esponi la sola API filtrata ai client esterni.

```sh
sudo tailscale funnel --bg --https=10000 http://127.0.0.1:18101
```

Prova da una rete fuori dal tailnet che `/` e `/api/v1/admin/customers` su HTTPS 10000 rispondano 404. Collauda l’attivazione con un client Windows configurato con l’URL pubblico inclusa la porta 10000. Se la rete del cliente blocca le porte HTTPS non standard, usa un dominio pubblico separato sulla 443 per la sola API.
