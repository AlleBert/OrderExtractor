# Threat model

| Superficie | Mitigazione | Rischio residuo |
|---|---|---|
| PDF malevoli | Worker con limiti di memoria e CPU, arresto dei processi figli, protocollo con limiti dimensionali e anteprima rasterizzata. Seccomp su Linux e AppContainer senza capability nel pacchetto Windows. | Il filesystem dipende da Landlock su Linux. Le restrizioni AppContainer di rete e file richiedono verifica su Windows 11. |
| Richieste loopback | Host esatto, cookie HttpOnly, Origin esatto sulle mutazioni, CSP e token di bootstrap monouso | Un processo già in esecuzione come utente può controllare browser e file. |
| CSV | Blocchi sui valori testuali interpretabili come formule | Il gestionale deve interpretare correttamente UTF-8 e separatore. |
| Licenze | Digest HMAC, Ed25519, prova di possesso e transazione sul limite posti | L'amministratore locale può manomettere lo stato. La revoca offline è limitata alla scadenza del lease. |

La disattivazione usa una challenge firmata separata dal rinnovo. In caso di esito di rete incerto, il client sospende nuovi lavori fino alla verifica firmata. Il server consente alla vecchia postazione di recuperare l’esito tramite prova di possesso. Le operazioni che richiedono una postazione attiva, tra cui aggiornamenti e diagnostica, restano bloccate.
