"""Il ciclo del ponte: legge la nota, chiede il da farsi, esegue, riferisce.

ADR-0002, i tre passi:

    1.  POST /keep-pull   { run_id, righe }        →  { operazioni }
    2.  il ponte applica le operazioni, una per una
    3.  POST /keep-ack    { run_id, esiti }

**Qui dentro non c'è nessun ragionamento**, ed è il punto. Il ponte non sa cosa
siano una categoria, un'emoji, una nota per articolo, una pietra tombale, il
catalogo, la famiglia. Riceve cinque verbi e li applica; il testo arriva già
reso e viene ricopiato. Se una sessione futura sentisse il bisogno di guardare
*dentro* il testo di un'operazione, starebbe violando ADR-0002 — e con esso la
ragione per cui questo processo può girare su una macchina che non è nostra
senza avere un solo privilegio sulle tabelle.

─────────────────────────────────────────────────────────────────────────────
⚠️ UN'OPERAZIONE CHE FALLISCE NON FERMA LE ALTRE
─────────────────────────────────────────────────────────────────────────────
Ogni operazione ha un esito suo, e tutti gli esiti tornano indietro insieme.
Fermarsi al primo errore sembrerebbe prudente e sarebbe il contrario: una riga
che qualcuno ha cancellato dall'app Keep un istante prima farebbe fallire la sua
`spunta_riga`, e con essa **tutto il resto del giro** — compreso ciò che non
c'entra niente. Il riconciliatore è costruito per ricalcolare tutto da capo al
ciclo dopo (ADR-0004), quindi un fallimento isolato costa cinque minuti, non un
guasto.

L'ordine invece si rispetta: le operazioni si applicano nell'ordine in cui
arrivano.

─────────────────────────────────────────────────────────────────────────────
IL CICLO NON HA STATO
─────────────────────────────────────────────────────────────────────────────
ADR-0008 §4: si riparte a freddo ogni volta. Nessun `dump()`/`restore()`, niente
da posare su disco, niente da cifrare — e quindi niente che possa disallinearsi.
Su GitHub Actions (ADR-0012) è anche l'unica cosa che funzioni: la macchina che
esegue il ciclo non esiste più cinque minuti dopo.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger("spesuccia.ciclo")

#: Quanto si aspetta una Edge Function prima di dichiararla irraggiungibile.
#: Generoso: un ciclo con cinquanta righe fa cinquanta scritture su Keep, e la
#: `keep-pull` deve leggere articoli e catalogo prima di rispondere.
TIMEOUT_SECONDI: Final[int] = 60

#: L'intestazione con cui il ponte si presenta alle due Edge Function
#: (ADR-0012 §4). Deve coincidere con `_condiviso/autenticazione.ts`.
INTESTAZIONE_PONTE: Final[str] = "x-spesuccia-ponte"


class ErroreDiCiclo(Exception):
    """Il ciclo non è potuto arrivare in fondo."""


@dataclass(frozen=True, repr=False)
class ConfigurazioneCiclo:
    """Ciò che serve per parlare con le due Edge Function.

    `__repr__` è soppresso come in `sicurezza.Configurazione`: un `repr` di
    questo oggetto in un traceback stamperebbe il token del ponte.
    """

    url_funzioni: str
    token_ponte: str

    def __repr__(self) -> str:
        return f"ConfigurazioneCiclo(url_funzioni={self.url_funzioni!r}, token_ponte=<redatto>)"


def carica_configurazione_ciclo() -> ConfigurazioneCiclo:
    """Legge dall'ambiente. Su GitHub Actions arriva dai Secrets.

    Non legge da `.env`: quel file è la comodità dello sviluppo locale, e il
    ciclo di produzione non deve dipendere da un file che potrebbe non esserci.
    Chi lavora in locale esporta le variabili, o le mette in `bridge/.env` e le
    carica prima.
    """
    url = os.environ.get("SPESUCCIA_URL_FUNZIONI", "").strip().rstrip("/")
    token = os.environ.get("SPESUCCIA_TOKEN_PONTE", "").strip()

    if not url:
        raise ErroreDiCiclo(
            "Manca SPESUCCIA_URL_FUNZIONI: l'indirizzo delle Edge Function, "
            "tipo https://<progetto>.supabase.co/functions/v1"
        )
    if not token:
        raise ErroreDiCiclo(
            "Manca SPESUCCIA_TOKEN_PONTE: il segreto condiviso con le due Edge "
            "Function (ADR-0012 §4)."
        )
    if not url.startswith("https://"):
        # ⚠️ Non è pignoleria: su http il token del ponte viaggerebbe in chiaro.
        raise ErroreDiCiclo("SPESUCCIA_URL_FUNZIONI deve essere un indirizzo https.")

    return ConfigurazioneCiclo(url_funzioni=url, token_ponte=token)


# ─────────────────────────────────────────────────────────────────────────────
# Il dialogo con le Edge Function
# ─────────────────────────────────────────────────────────────────────────────


def _chiama(
    configurazione: ConfigurazioneCiclo,
    funzione: str,
    corpo: dict[str, Any],
) -> dict[str, Any]:
    """POST JSON a una delle due Edge Function, e ne restituisce la risposta.

    Si usa `urllib` invece di `requests` di proposito: `requests` c'è già fra le
    dipendenze dello spike, ma questo modulo gira su una macchina effimera dove
    ogni pacchetto in meno è un minuto di installazione in meno — e ADR-0012
    conta i minuti, perché sono la quota gratuita.
    """
    dati = json.dumps(corpo).encode("utf-8")
    richiesta = urllib.request.Request(
        f"{configurazione.url_funzioni}/{funzione}",
        data=dati,
        method="POST",
        headers={
            "content-type": "application/json",
            INTESTAZIONE_PONTE: configurazione.token_ponte,
        },
    )

    try:
        with urllib.request.urlopen(richiesta, timeout=TIMEOUT_SECONDI) as risposta:
            return json.loads(risposta.read().decode("utf-8"))
    except urllib.error.HTTPError as errore:
        # ⚠️ Il corpo della risposta **non** finisce nel messaggio: le due Edge
        # Function rispondono generico apposta, ma se un giorno rispondessero di
        # più, questo è il punto in cui finirebbe in un log di GitHub Actions —
        # che su un repository pubblico legge chiunque.
        raise ErroreDiCiclo(
            f"{funzione} ha risposto {errore.code}. "
            "Se è 401, il token del ponte non coincide con quello della funzione."
        ) from None
    except urllib.error.URLError as errore:
        raise ErroreDiCiclo(f"{funzione} non raggiungibile: {errore.reason}") from None
    except json.JSONDecodeError:
        raise ErroreDiCiclo(f"{funzione} ha risposto qualcosa che non è JSON.") from None


def chiedi_operazioni(
    configurazione: ConfigurazioneCiclo,
    run_id: str,
    righe: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Passo 1: manda lo snapshot, riceve le operazioni."""
    risposta = _chiama(configurazione, "keep-pull", {"run_id": run_id, "righe": righe})
    operazioni = risposta.get("operazioni")
    if not isinstance(operazioni, list):
        raise ErroreDiCiclo("keep-pull non ha restituito un elenco di operazioni.")
    return operazioni


def riferisci_esiti(
    configurazione: ConfigurazioneCiclo,
    run_id: str,
    esiti: list[dict[str, Any]],
) -> None:
    """Passo 3: dice com'è andata."""
    _chiama(configurazione, "keep-ack", {"run_id": run_id, "esiti": esiti})


# ─────────────────────────────────────────────────────────────────────────────
# L'esecuzione delle operazioni
# ─────────────────────────────────────────────────────────────────────────────


class Trasporto:
    """Ciò che `applica_operazioni` si aspetta di poter chiamare.

    Non è una classe base da ereditare: è la forma che `PonteKeep` ha già. Sta
    qui scritta perché i test possano sostituirla con qualcosa che non parla con
    Google — che è la sola ragione per cui la logica di questo modulo è
    verificabile senza un master token.
    """

    def crea_riga(self, testo: str) -> str: ...  # pragma: no cover
    def spunta_riga(self, external_id: str) -> None: ...  # pragma: no cover
    def despunta_riga(self, external_id: str) -> None: ...  # pragma: no cover
    def modifica_testo(self, external_id: str, testo: str) -> None: ...  # pragma: no cover
    def rimuovi_riga(self, external_id: str) -> None: ...  # pragma: no cover


def applica_operazioni(
    ponte: Trasporto,
    operazioni: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Applica le operazioni **nell'ordine** e restituisce un esito per ciascuna.

    Non solleva mai per colpa di una singola operazione: vedi la nota in cima.
    Un'operazione con un verbo sconosciuto **fallisce**, non viene ignorata —
    ignorarla in silenzio significherebbe che una versione nuova delle Edge
    Function e una vecchia del ponte convivono senza che nessuno se ne accorga.
    """
    esiti: list[dict[str, Any]] = []

    for operazione in operazioni:
        op_id = operazione.get("op_id")
        if not isinstance(op_id, str) or not op_id:
            # Senza `op_id` non c'è niente da riferire: l'ack non saprebbe a
            # quale riga del piano attaccare l'esito. Si salta e si annota.
            logger.warning("Operazione senza op_id, saltata.")
            continue

        try:
            assegnato = _esegui(ponte, operazione)
        except Exception as errore:  # noqa: BLE001 - qualunque guasto è un esito
            logger.warning("Operazione %s fallita: %s", op_id, errore)
            esiti.append({"op_id": op_id, "ok": False, "errore": str(errore)[:300]})
            continue

        esito: dict[str, Any] = {"op_id": op_id, "ok": True}
        if assegnato is not None:
            esito["external_id_assegnato"] = assegnato
        esiti.append(esito)

    return esiti


#: I cinque verbi (ADR-0002). Un sesto non si aggiunge qui: si aggiunge la
#: logica nella Edge Function, e questo elenco resta di cinque.
VERBI_NOTI: Final[frozenset[str]] = frozenset(
    {"crea_riga", "spunta_riga", "despunta_riga", "modifica_testo", "rimuovi_riga"}
)


def _esegui(ponte: Trasporto, operazione: dict[str, Any]) -> str | None:
    """Un verbo solo. Restituisce l'`external_id` assegnato, per `crea_riga`."""
    verbo = operazione.get("op")

    # ⚠️ Il verbo si riconosce **prima** di guardare i suoi campi, e non è un
    # dettaglio di stile: è il messaggio d'errore a fare la diagnosi. Cercando
    # `external_id` per primo, un `archivia_riga` arrivato da una Edge Function
    # più nuova fallirebbe con «senza external_id», e chi legge il log andrebbe
    # a cercare un campo mancante invece della versione disallineata.
    if not isinstance(verbo, str) or verbo not in VERBI_NOTI:
        raise ErroreDiCiclo(
            f"Verbo sconosciuto: {verbo!r}. I verbi sono cinque (ADR-0002) e questo "
            "non è fra quelli: probabilmente le Edge Function sono più nuove del ponte."
        )

    if verbo == "crea_riga":
        testo = operazione.get("testo")
        if not isinstance(testo, str):
            raise ErroreDiCiclo("crea_riga senza testo.")
        return ponte.crea_riga(testo)

    external_id = operazione.get("external_id")
    if not isinstance(external_id, str) or not external_id:
        raise ErroreDiCiclo(f"{verbo} senza external_id.")

    if verbo == "spunta_riga":
        ponte.spunta_riga(external_id)
        return None
    if verbo == "despunta_riga":
        ponte.despunta_riga(external_id)
        return None
    if verbo == "rimuovi_riga":
        ponte.rimuovi_riga(external_id)
        return None
    if verbo == "modifica_testo":
        testo = operazione.get("testo")
        if not isinstance(testo, str):
            raise ErroreDiCiclo("modifica_testo senza testo.")
        ponte.modifica_testo(external_id, testo)
        return None

    # Irraggiungibile: `verbo` è già stato riconosciuto fra i cinque, e tutti
    # e cinque hanno un ramo qui sopra. Se `VERBI_NOTI` crescesse senza che
    # cresca questa catena, si arriverebbe qui — ed è meglio un errore che un
    # `None` silenzioso interpretato come «riuscita».
    raise ErroreDiCiclo(f"Verbo {verbo!r} riconosciuto ma non applicato.")


def nuovo_run_id() -> str:
    """L'identificativo del ciclo. `bridge_operations.run_id` è una colonna `uuid`."""
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# Il giro completo
# ─────────────────────────────────────────────────────────────────────────────


def esegui_ciclo(
    ponte: Any,  # noqa: ANN401 - PonteKeep, importato dal chiamante
    configurazione: ConfigurazioneCiclo,
    run_id: str | None = None,
) -> dict[str, Any]:
    """I tre passi, in fila. Restituisce un riepilogo per il log.

    Il riepilogo contiene **numeri**, non testi: finisce nei log di GitHub
    Actions, che su un repository pubblico legge chiunque. Il testo di una riga
    della lista della spesa non è un segreto, ma non c'è ragione di pubblicarlo.
    """
    identificativo = run_id or nuovo_run_id()

    righe = [
        {"external_id": riga.external_id, "testo": riga.testo, "spuntato": riga.spuntato}
        for riga in ponte.leggi_righe()
    ]
    logger.info("Ciclo %s: %d righe sulla nota.", identificativo, len(righe))

    operazioni = chiedi_operazioni(configurazione, identificativo, righe)
    if not operazioni:
        # Il caso normale a regime: nessuna scrittura su Keep. Se qui
        # comparisse un'operazione a ogni giro, sarebbe il ciclo che non
        # converge di ADR-0009 vincolo 4.
        #
        # ⚠️ **Si chiama `keep-ack` lo stesso, con un elenco vuoto**, e la prima
        # stesura non lo faceva — sembrava sprecato chiamare una funzione per
        # dirle che non è successo niente.
        #
        # È invece la cosa più importante che questo ramo fa. `keep-ack` è
        # l'unico che scrive `bridge_state.last_sync_at`, e la spia dell'app
        # deriva «ponte fermo» dall'**età** di quell'istante: nessuno scrive
        # «sono fermo», perché un ponte fermo non scrive niente.
        #
        # Saltando l'ack, a regime — cioè quasi sempre — l'istante non si
        # sarebbe mosso più: dopo 45 minuti l'app avrebbe detto «ponte in
        # ritardo» e dopo tre ore «ponte fermo», **mentre il ponte funzionava
        # benissimo**. Una spia che grida al lupo quando tutto va bene si impara
        # a ignorarla, ed è esattamente il modo di non accorgersi dell'allarme
        # vero.
        #
        # «Ho girato e non c'era niente da fare» è un'informazione, e va
        # riferita.
        logger.info("Ciclo %s: niente da fare.", identificativo)
        riferisci_esiti(configurazione, identificativo, [])
        return {"run_id": identificativo, "righe": len(righe), "operazioni": 0, "riuscite": 0}

    logger.info("Ciclo %s: %d operazioni da applicare.", identificativo, len(operazioni))
    esiti = applica_operazioni(ponte, operazioni)
    riferisci_esiti(configurazione, identificativo, esiti)

    riuscite = sum(1 for esito in esiti if esito.get("ok"))
    logger.info("Ciclo %s: %d riuscite su %d.", identificativo, riuscite, len(esiti))

    return {
        "run_id": identificativo,
        "righe": len(righe),
        "operazioni": len(operazioni),
        "riuscite": riuscite,
    }


def percorso_env_predefinito() -> Path:
    """`bridge/.env`, per chi lancia il ciclo a mano dal proprio computer."""
    return Path(__file__).resolve().parent / ".env"
