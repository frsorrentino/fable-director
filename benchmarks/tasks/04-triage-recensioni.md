Nella cartella `fixtures/reviews/` ci sono 40 recensioni clienti (`rev001.txt`…`rev040.txt`).
Produci `fixtures/reviews/triage.csv` con header `id,sentiment,tema,segnalazione_sicurezza` e una riga per recensione, ordinate per id:
- `sentiment`: uno tra `positivo`, `negativo`, `misto`
- `tema`: il tema dominante, uno tra `batteria`, `spedizione`, `qualita`, `prezzo`, `assistenza`, `usabilita`
- `segnalazione_sicurezza`: `YES` solo se la recensione riporta un rischio per l'incolumità (surriscaldamento, incendio, scossa elettrica, parti che si staccano, sostanze nocive, rischio soffocamento), altrimenti `NO`

Non stampare il contenuto delle recensioni né il CSV a schermo: scrivi solo il file. Finito, rispondi solo `DONE`.
