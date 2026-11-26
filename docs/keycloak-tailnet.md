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

Dal server, con `curl` e `jq` disponibili, chiedi un token per la console:

```sh
./scripts/keycloak-admin-token.sh https://server.tailnet.ts.net:10444/realms/order-extractor
```

Apri nel browser indicato l’URL di verifica e inserisci il codice mostrato nel terminale. Completa l’autenticazione con Authenticator. Il comando stampa il token da copiare nel campo **Token di accesso** della console. Il token va usato solo nell’URL privato della console.

### Se il DNS del nodo punta a Funnel

Quando lo stesso nome Tailscale serve anche un Funnel, il resolver del server può restituire un IP pubblico. Se la porta privata funziona quando indirizzi la richiesta all’IPv4 Tailscale del server, passa quell’IP allo script. L’issuer, il nome TLS e la verifica del certificato restano quelli HTTPS di Keycloak.

```sh
OE_IDENTITY_TAILNET_IP="$(tailscale ip -4)" \
  ./scripts/keycloak-admin-token.sh https://server.tailnet.ts.net:10444/realms/order-extractor
```

Per applicare la stessa risoluzione nel container licenze, imposta in `.env` `OE_IDENTITY_TAILNET_HOST` al nome Tailscale di Keycloak senza porta e `OE_IDENTITY_TAILNET_IP` all’IPv4 Tailscale del nodo. Aggiungi `-f infra/tailscale-keycloak.compose.yaml` dopo `-f infra/tailscale.compose.yaml` nei comandi Compose dello stack licenze, incluso il controllo JWKS qui sotto. Il file aggiuntivo modifica la risoluzione del nome solo nel container `api`. Il controllo del JWKS conferma anche la raggiungibilità di Serve dal container.

## 3. Collauda la console prima di esporre l’API

Avvia lo [stack licenze](tailscale-licensing.md#accesso-riservato-e-mfa) e usa la porta privata della console:

```sh
sudo tailscale serve --bg --https=10443 http://127.0.0.1:18100
```

Verifica dall’applicazione in container la risoluzione DNS e l’HTTPS del JWKS. Esegui il comando dalla radice del repository con la configurazione licenze già caricata.

```sh
docker compose --env-file .env -f infra/compose.yaml -f infra/private-admin.compose.yaml \
  -f infra/tailscale.compose.yaml exec -T api python -c \
  'import os, urllib.request; print(urllib.request.urlopen(os.environ["OE_OIDC_JWKS"], timeout=5).status)'
```

Verifica che la risposta sia 200 e che la console accetti il token dopo OTP. Prova anche il rifiuto di token scaduti e di altri utenti. Quando questi controlli sono passati, esponi la sola API filtrata ai client esterni.

```sh
sudo tailscale funnel --bg --https=10000 http://127.0.0.1:18101
```

Prova da una rete fuori dal tailnet che `/` e `/api/v1/admin/customers` su HTTPS 10000 rispondano 404. Collauda l’attivazione con un client Windows configurato con l’URL pubblico inclusa la porta 10000. Se la rete del cliente blocca le porte HTTPS non standard, usa un dominio pubblico separato sulla 443 per la sola API.
