# ADR 003 · Limiti e isolamento del worker PDF

Il broker avvia un processo con una nuova sessione su Linux o assegnato a un Job Object su Windows. Il processo attende la conferma del broker prima di aprire il PDF. L'ambiente è ristretto ai parametri di sistema e OCR necessari. Il broker controlla progresso, durata e dimensioni e termina il gruppo all'annullamento. Il Job Object configura limiti di memoria, CPU e numero di processi e termina i figli all'ultimo rilascio del suo handle.

Su Linux il worker usa seccomp per impedire la creazione di socket e impedire di uscire dal gruppo di processi. Sono applicati limiti di memoria virtuale, CPU e dimensione massima per singolo file. Con Landlock ABI 3 o superiore si restringe anche l'accesso ai file. Runtime di sistema, programmi e modelli autorizzati sono leggibili e la directory di lavoro rimane scrivibile. La demo conserva il filtro di rete e i limiti sulle risorse sui kernel privi di Landlock, tra cui il runner attuale.

Il broker valida i dati JSON per tipo, dimensione e completezza dei metadati. Controlla percorso e tipo dei file ed esegue la preview solo da artefatti PNG di dimensione limitata. Le immagini vengono generate dal worker.

L'eseguibile distribuito su Windows avvia il worker in un AppContainer senza capability e senza handle ereditati. La directory del runtime riceve accesso in lettura ed esecuzione. Solo la directory temporanea del lavoro riceve l'accesso in modifica e l'etichetta di integrità bassa. Il worker controlla il proprio token prima di leggere il PDF. Il broker assegna il Job Object prima di riprendere il processo sospeso. Se la creazione fallisce, il lavoro viene rifiutato.

Il workflow Windows prevede una prova di annullamento e una prova AppContainer contro una porta loopback e un file sintetico fuori dalla directory di lavoro. Il candidato a un aggiornamento deve inoltre estrarre una scansione sintetica nel suo AppContainer prima che il launcher cambi versione. Le chiamate Windows, i permessi ereditati e il comportamento dell'installer richiedono ancora conferma su Windows 11.
