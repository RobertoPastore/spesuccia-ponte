"""Il punto d'ingresso del ponte: un ciclo, poi esce.

    python avvia.py

Un ciclo e basta, non un processo che resta acceso. È ADR-0012: il ponte gira su
GitHub Actions, dove la macchina che lo esegue non esiste più cinque minuti
dopo. Un ciclo per esecuzione è quindi l'unica forma possibile — ed è anche la
più semplice da capire e da provare a mano.

─────────────────────────────────────────────────────────────────────────────
⚠️ COSA SUCCEDE SE QUALCOSA VA STORTO
─────────────────────────────────────────────────────────────────────────────
Si esce con un codice diverso da zero, così GitHub Actions colora di rosso
l'esecuzione. **Non si ritenta qui dentro**: il ciclo successivo ricalcola tutto
da capo dallo stato (ADR-0004), quindi un giro perso costa il tempo di un giro.
Un ritentativo dentro il processo, invece, raddoppierebbe le scritture su
un'API privata di Google — che è la cosa che il progetto ha più interesse a non
fare.

─────────────────────────────────────────────────────────────────────────────
⚠️ LA REDAZIONE SI INSTALLA PRIMA DI TUTTO
─────────────────────────────────────────────────────────────────────────────
`installa_redazione()` è la prima riga eseguita, prima ancora di leggere la
configurazione. Da quel momento nessun percorso di uscita standard — log,
traceback non gestito — può stampare il master token per distrazione.

Su un repository pubblico i log di Actions li legge chiunque, quindi qui non è
una precauzione: è la condizione perché il ponte possa girare lì.
"""

from __future__ import annotations

import logging
import sys

from ciclo import (
    ErroreDiCiclo,
    carica_configurazione_ciclo,
    esegui_ciclo,
    ottieni_note_attive,
    percorso_env_predefinito,
)
from ponte_keep import PonteKeep, apri_sessione, autentica, risolvi_nota
from sicurezza import (
    ErroreDiSicurezza,
    carica_configurazione,
    installa_redazione,
    verifica_nota_reale,
)

logger = logging.getLogger("spesuccia.avvia")


def main() -> int:
    installa_redazione()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    try:
        configurazione_ciclo = carica_configurazione_ciclo()
        configurazione_keep = carica_configurazione(percorso_env_predefinito())
    except (ErroreDiCiclo, ErroreDiSicurezza) as errore:
        logger.error("Configurazione incompleta: %s", errore)
        return 2

    try:
        autenticazione, esito = autentica(
            configurazione_keep.email,
            configurazione_keep.master_token,
            configurazione_keep.device_id,
        )
        logger.info("Autenticato in %.2f s.", esito.secondi)

        # A freddo, sempre: ADR-0008 §4 ha misurato che conservare lo stato di
        # sync fa risparmiare 0,05 s su un budget di minuti, in cambio di 8,4 KB
        # di materiale di sessione da custodire. Non conviene, e su una macchina
        # effimera non sarebbe nemmeno possibile.
        keep, secondi = apri_sessione(autenticazione, stato=None)
        logger.info("Sincronizzato a freddo in %.2f s.", secondi)

        # Passo 1: fetch configurazione note attive censite
        try:
            note_attive = ottieni_note_attive(configurazione_ciclo)
        except ErroreDiCiclo as errore:
            logger.warning(
                "Fetch configurazione GET /keep-pull non riuscito (%s), fallback su nota da configurazione locale.",
                errore,
            )
            note_attive = []

        if not note_attive and configurazione_keep.note_id:
            note_attive = [{"list_id": "", "keep_note_id": configurazione_keep.note_id}]

        if not note_attive:
            logger.warning("Nessuna nota Keep attiva da sincronizzare.")
            return 0

        logger.info("Trovate %d note attive da processare in sequenza.", len(note_attive))

        note_completate = 0
        errori_note = 0

        for voce in note_attive:
            list_id = voce.get("list_id") or None
            keep_note_id = voce.get("keep_note_id")
            if not keep_note_id:
                logger.warning("Voce nota priva di keep_note_id, saltata: %s", voce)
                continue

            logger.info("--- Inizio ciclo nota Keep: %s (list_id: %s) ---", keep_note_id, list_id or "default")
            try:
                nodo = risolvi_nota(keep, keep_note_id)
                # ⚠️ La conferma è l'ID della nota verificato contro configurazione/censimento
                consenso = verifica_nota_reale(nodo, configurazione_keep, keep_note_id)
                logger.info("Nota «%s»: %d righe.", consenso.titolo, consenso.righe_osservate)

                ponte = PonteKeep(keep, nodo, consenso)
                riepilogo = esegui_ciclo(ponte, configurazione_ciclo, list_id=list_id)
                logger.info(
                    "Nota %s completata. righe=%d operazioni=%d riuscite=%d",
                    keep_note_id,
                    riepilogo["righe"],
                    riepilogo["operazioni"],
                    riepilogo["riuscite"],
                )
                note_completate += 1
            except Exception as errore:  # noqa: BLE001
                # Invariante 3 (ADR-0016): Un errore su una nota non abortisce le altre!
                logger.exception("Errore isolato durante il ciclo della nota %s: %s", keep_note_id, errore)
                errori_note += 1
                continue

        logger.info(
            "Ciclo complessivo terminato. Note completate: %d, Note con errori: %d.",
            note_completate,
            errori_note,
        )

    except ErroreDiSicurezza as errore:
        # Una guardia che scatta non è un guasto da ritentare: è una condizione
        # da guardare in faccia. La nota è sparita, è stata rinominata, o l'ID è
        # sbagliato.
        logger.error("Il ponte si ferma: %s", errore)
        return 3
    except ErroreDiCiclo as errore:
        logger.error("Ciclo non completato: %s", errore)
        return 4
    except Exception as errore:  # noqa: BLE001 - l'ultima rete, con la redazione attiva
        logger.exception("Guasto inatteso: %s", errore)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
