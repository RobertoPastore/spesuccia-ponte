"""Il ponte verso Google Keep: **i cinque verbi**, e nient'altro.

ADR-0002 dice che il ponte è trasporto puro: «non capisce niente e non decide
niente». Questo modulo è la verifica materiale di quell'affermazione — la sua
intera superficie pubblica sono le sei operazioni che ADR-0002 elenca:

    leggi_righe()                        -> list[RigaDiNota]     (il pull)
    crea_riga(testo)                     -> external_id
    spunta_riga(external_id)             -> None
    despunta_riga(external_id)           -> None
    modifica_testo(external_id, testo)   -> None
    rimuovi_riga(external_id)            -> None

Qui dentro **non** compaiono, e non devono comparire mai: categoria, emoji,
nota per articolo, quantità, pietra tombale, catalogo, famiglia, ombre,
`normalized_name`. Il `testo` arriva già reso dalla Edge Function e viene
ricopiato. Se una sessione futura sente il bisogno di un sesto verbo che
richieda una di quelle nozioni, sta violando ADR-0002: la logica va aggiunta
nella Edge Function, non qui.

**Stato del modulo.** È la bozza di trasporto usata dallo spike di M0. Diventerà
il worker di casa solo se lo spike sceglierà il fallback Python della Sez. 4 del
brief; se M0 sceglierà il serverless, queste stesse sei firme andranno
riscritte in TypeScript. In entrambi i casi la superficie non cambia — è
esattamente il guadagno che ADR-0002 cercava.

Vocabolario (glossario §1 e §5): una **riga di nota** è un `ListItem` di Keep,
cioè *solo testo + spuntato/non spuntato*. Il suo `external_id` è ciò che su
Supabase diventa `external_id_keep`, cioè una **prenotazione** (ADR-0003), non
un'identità.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

import gkeepapi
from gkeepapi import node as _node

from sicurezza import ConsensoNotaDiProva, ConsensoNotaReale, registra_segreto

#: I cinque verbi, come li nomina il glossario §5. Serve allo spike per
#: dimostrare che li ha esercitati tutti, e a chi legge per contarli.
I_CINQUE_VERBI: Final[tuple[str, ...]] = (
    "crea_riga",
    "spunta_riga",
    "despunta_riga",
    "modifica_testo",
    "rimuovi_riga",
)


class RigaInesistente(Exception):
    """L'`external_id` richiesto non corrisponde a nessuna riga viva della nota."""


@dataclass(frozen=True)
class RigaDiNota:
    """Lo snapshot di una riga, nella forma esatta del payload di `keep-pull`.

    ADR-0002 §«Il ciclo, in tre passi»: `{ external_id, testo, spuntato }`.
    Non c'è un quarto campo perché su Keep non c'è un quarto campo.
    """

    external_id: str
    testo: str
    spuntato: bool

    #: Identificativo assegnato dal **server** di Keep, distinto da `external_id`.
    #: Non entra nel payload: è qui solo perché lo spike deve confrontare la
    #: stabilità dei due candidati prima che il modello dati ne scelga uno.
    server_id: str | None = None


@dataclass(frozen=True)
class EsitoAutenticazione:
    """Quel che serve sapere dell'autenticazione, senza toccare il segreto."""

    email: str
    secondi: float


def autentica(
    email: str,
    master_token: str,
    device_id: str,
) -> tuple[gkeepapi.APIAuth, EsitoAutenticazione]:
    """Ottiene un oggetto di autenticazione riutilizzabile.

    Si autentica **una volta sola** e si riusa l'oggetto per più istanze di
    `Keep`: ogni chiamata a `APIAuth.load` fa un giro su `gpsoauth`, e non c'è
    ragione di ripeterlo dentro lo stesso processo.

    Il master token non viene né restituito né registrato altrove: viene
    aggiunto all'elenco delle stringhe da redigere e poi dimenticato.
    """
    registra_segreto(master_token)
    autenticazione = gkeepapi.APIAuth(gkeepapi.Keep.OAUTH_SCOPES)
    inizio = time.monotonic()
    autenticazione.load(email, master_token, device_id)
    durata = time.monotonic() - inizio
    # Anche il token OAuth derivato è una credenziale: va redatto come il master.
    registra_segreto(autenticazione.getAuthToken())
    return autenticazione, EsitoAutenticazione(email=email, secondi=durata)


def apri_sessione(
    autenticazione: gkeepapi.APIAuth,
    stato: dict[str, Any] | None = None,
) -> tuple[gkeepapi.Keep, float]:
    """Crea un client Keep e lo sincronizza. Restituisce anche quanto ci ha messo.

    `stato` è il blob di `Keep.dump()`: se c'è, la sincronizzazione è
    incrementale (a caldo); se è `None`, si riscarica tutto (a freddo). La
    differenza fra i due tempi è la misura che serve a decidere *dove* posare
    quel blob — o se non posarlo affatto. Vedi ADR-0002, punto 4 di M0.
    """
    keep = gkeepapi.Keep()
    inizio = time.monotonic()
    keep.load(autenticazione, stato, sync=True)
    return keep, time.monotonic() - inizio


def risolvi_nota(keep: gkeepapi.Keep, note_id: str) -> Any:  # noqa: ANN401
    """Recupera la nota **per ID**, mai per titolo.

    La ricerca per titolo è il modo in cui si finisce per sbaglio sulla nota
    reale, ed è anche il modo in cui si finisce per crearne una nuova quando
    non la si trova — cosa che il brief Sez. 13 vieta. Qui non si crea nulla:
    se l'ID non risolve, si torna `None` e il chiamante si ferma.
    """
    return keep.get(note_id)


class PonteKeep:
    """Trasporto verso una singola nota-lista di Keep.

    Non si costruisce senza un consenso, e i consensi sono **due tipi distinti**:
    `ConsensoNotaDiProva` per lo spike di M0, `ConsensoNotaReale` per il ciclo di
    M3. Nessuno dei due si costruisce a mano (vedi `sicurezza.py`).

    Due tipi e non uno con un flag: un flag si passa sbagliato, un tipo no. Lo
    spike chiede il primo e continuerà a riceverne solo quello, quindi la nota
    vera gli resta preclusa anche adesso che quella strada esiste.
    """

    def __init__(
        self,
        keep: gkeepapi.Keep,
        lista: Any,  # noqa: ANN401 - gkeepapi.node.List
        consenso: ConsensoNotaDiProva | ConsensoNotaReale,
    ) -> None:
        if not isinstance(consenso, (ConsensoNotaDiProva, ConsensoNotaReale)):
            raise TypeError(
                "PonteKeep richiede un consenso prodotto da "
                "sicurezza.verifica_nota_di_prova() o "
                "sicurezza.verifica_nota_reale()."
            )
        self._keep = keep
        self._lista = lista
        self.consenso = consenso
        #: Diagnostica per lo spike, non parte della superficie del ponte:
        #: l'`external_id` che `crea_riga` conosce **prima** di sincronizzare.
        self.ultimo_id_prima_del_sync: str | None = None
        #: Quante volte è stato chiamato `sync()`, per capire il costo dei cicli.
        self.sincronizzazioni: int = 0

    # ── lettura ─────────────────────────────────────────────────────────────

    def leggi_righe(self) -> list[RigaDiNota]:
        """Lo snapshot completo della nota: il payload di `keep-pull`.

        ADR-0002 accetta che il costo sia O(dimensione della lista) e non
        O(modifiche): per una spesa domestica sono 1-2 KB per ciclo.
        """
        return [
            RigaDiNota(
                external_id=riga.id,
                testo=riga.text,
                spuntato=riga.checked,
                server_id=riga.server_id,
            )
            for riga in self._lista.items
        ]

    def ricarica(self) -> float:
        """Risincronizza con il server. Restituisce i secondi impiegati."""
        inizio = time.monotonic()
        self._keep.sync()
        self.sincronizzazioni += 1
        return time.monotonic() - inizio

    # ── i cinque verbi ──────────────────────────────────────────────────────

    def crea_riga(self, testo: str) -> str:
        """Aggiunge una riga non spuntata in fondo alla nota.

        Restituisce l'`external_id` **utilizzabile subito**: `gkeepapi` genera
        l'identificativo lato client (`Node._generateId`) e lo manda al server,
        quindi esiste già prima della sincronizzazione. Che il server lo
        conservi invece di riassegnarne un altro è però un'ipotesi, e ipotesi
        del genere è il mestiere dello spike verificarle: vedi la prova 3.
        """
        riga = self._lista.add(
            testo,
            False,
            _node.NewListItemPlacementValue.Bottom,
        )
        self.ultimo_id_prima_del_sync = riga.id
        self._keep.sync()
        self.sincronizzazioni += 1
        return riga.id

    def spunta_riga(self, external_id: str) -> None:
        """Marca la riga come spuntata. ↔ articolo in lista *comprato*."""
        riga = self._riga(external_id)
        riga.checked = True
        self._keep.sync()
        self.sincronizzazioni += 1

    def despunta_riga(self, external_id: str) -> None:
        """Toglie la spunta. ↔ articolo in lista *da comprare*."""
        riga = self._riga(external_id)
        riga.checked = False
        self._keep.sync()
        self.sincronizzazioni += 1

    def modifica_testo(self, external_id: str, testo: str) -> None:
        """Sostituisce il testo della riga. Il testo arriva già reso: si ricopia."""
        riga = self._riga(external_id)
        riga.text = testo
        self._keep.sync()
        self.sincronizzazioni += 1

    def rimuovi_riga(self, external_id: str) -> None:
        """Elimina la riga dalla nota.

        `ListItem.delete()` marca il nodo come eliminato; `List.items` non lo
        restituisce più. È l'operazione con cui il riconciliatore impedisce la
        resurrezione (ADR-0004 §3, Caso 2) e fa la pulizia a 30 giorni.
        """
        riga = self._riga(external_id)
        riga.delete()
        self._keep.sync()
        self.sincronizzazioni += 1

    # ── stato di sincronizzazione ───────────────────────────────────────────

    def esporta_stato(self) -> dict[str, Any]:
        """Il blob di `dump()`: l'unica cosa con stato rimasta nel ponte.

        Non contiene il master token (`Keep.dump()` serializza `keep_version`,
        etichette e nodi), ma contiene il **contenuto** delle note dell'account
        dedicato. Va trattato come materiale di sessione.
        """
        return self._keep.dump()

    # ── interno ─────────────────────────────────────────────────────────────

    def _riga(self, external_id: str) -> Any:  # noqa: ANN401 - gkeepapi.node.ListItem
        riga = self._lista.get(external_id)
        if riga is None or not isinstance(riga, _node.ListItem) or riga.deleted:
            raise RigaInesistente(
                f"Nessuna riga viva con external_id {external_id!r} in questa nota."
            )
        return riga
