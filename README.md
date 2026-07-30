# spesuccia-ponte

Il **ponte** fra una nota-lista di Google Keep e un backend Supabase.

Gira su GitHub Actions, un ciclo alla volta, e non conserva niente fra
un'esecuzione e l'altra.

## Che cosa fa, in tre passi

```
POST /keep-pull  { run_id, righe }  →  { operazioni }
                 il ponte applica i cinque verbi
POST /keep-ack   { run_id, esiti }
```

Legge le righe della nota, le manda a una Edge Function, riceve un elenco di
operazioni, le applica, e riferisce com'è andata.

## Che cosa **non** fa, ed è il punto

Il ponte non capisce niente e non decide niente. La sua intera superficie sono
cinque verbi:

| verbo | payload |
| ---------------- | ------------------------------- |
| `crea_riga` | `{ op_id, testo }` |
| `spunta_riga` | `{ op_id, external_id }` |
| `despunta_riga` | `{ op_id, external_id }` |
| `modifica_testo` | `{ op_id, external_id, testo }` |
| `rimuovi_riga` | `{ op_id, external_id }` |

Il `testo` arriva **già composto**: il ponte non compone stringhe, le ricopia.
Qui dentro non esistono — e non devono esistere mai — le nozioni di categoria,
emoji, quantità, nota per articolo, cancellazione logica, catalogo. Tutto il
ragionamento sta dall'altra parte, nella Edge Function.

**Non è purismo architetturale: è ciò che permette a questo processo di girare
su una macchina che non è la nostra senza avere un solo privilegio di scrittura
sul database.** Il ponte parla con due endpoint HTTP e con Google Keep, e con
nient'altro.

Se leggendo questo codice viene voglia di aggiungere un sesto verbo che abbia
bisogno di una di quelle nozioni, la risposta è che quella logica va aggiunta
nella Edge Function.

## Che cosa c'è qui dentro

| file | |
| ------------------- | --------------------------------------------------- |
| `avvia.py` | il punto d'ingresso: un ciclo, poi esce |
| `ciclo.py` | i tre passi e l'applicazione dei verbi |
| `ponte_keep.py` | il trasporto verso Keep, via `gkeepapi` |
| `sicurezza.py` | redazione dei segreti nei log, guardie sulla nota |
| `test_ciclo.py` | prove del ciclo, **senza rete e senza Google** |
| `test_nota_reale.py`| prove delle guardie |

```bash
python -m unittest discover
```

Le prove girano a vuoto: il trasporto è sostituito da un finto che registra le
chiamate. È possibile perché il ponte non ragiona — tutto ciò che c'è da
verificare è che applichi i verbi giusti, nell'ordine giusto, e che riferisca
com'è andata.

## Configurazione

Sei variabili d'ambiente, i cui nomi stanno in `.env.example`. In esecuzione
vivono nei **Secrets** del repository; in locale si esportano o si mettono in un
`.env`, che è escluso dal versionamento.

⚠️ **Il master token di Google vale quanto la password dell'account che lo ha
generato, e non scade.** Appartiene a un account dedicato, creato apposta e
aggiunto come collaboratore alla nota: la superficie di rischio è quel solo
account. Non deve finire in un file versionato, in un log, in un messaggio.

`sicurezza.py` installa un filtro che redige i segreti registrati da ogni log e
da ogni traceback non gestito, **prima** che la configurazione venga letta. Su
un repository pubblico i log delle esecuzioni li legge chiunque: non è una
precauzione, è la condizione perché questo codice possa girare qui.

## Perché è pubblico

Per i minuti di GitHub Actions, che sui repository pubblici sono illimitati.

È possibile perché qui non c'è niente da nascondere: nessun segreto, nessun
identificativo, nessuna logica di dominio. Le sei variabili sono cifrate nei
Secrets, e il codice — cinque verbi e un client HTTP — è la parte meno
riservata del sistema.

## Il ciclo si spegne da solo dopo 60 giorni

GitHub disabilita i workflow schedulati dopo 60 giorni senza commit nuovi, e
solo i commit azzerano il contatore. Il workflow si scrive quindi da sé un
commit di mantenimento una volta a settimana.

È brutto — sono commit che non dicono niente — e l'alternativa è un ponte che
muore in silenzio due mesi dopo l'ultima modifica.

## Licenza

Nessuna: tutti i diritti riservati. È pubblicato per essere eseguito, non per
essere riusato.
