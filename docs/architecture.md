# Architettura

Il launcher avvia FastAPI su loopback con porta assegnata dal sistema e un token di bootstrap monouso. Ogni job passa un PDF temporaneo a un processo separato che produce immagini raster, righe candidate e provenienza. La revisione e il CSV restano sul dispositivo. SQLite conserva profili e metadati minimi dello storico. Il control plane gestisce identità dispositivo, seriali e lease firmati.

La rilettura manuale usa il testo e le coordinate già estratti dal worker. La cache è temporanea, con 2 MiB a pagina e 24 MiB complessivi, e viene eliminata quando si chiude l’ordine. Il PDF originale viene eliminato dal worker dopo l’estrazione. Il broker rilegge soltanto gli artefatti JSON validati, mantenendo le righe delle altre pagine e invalidando l’approvazione CSV precedente.
