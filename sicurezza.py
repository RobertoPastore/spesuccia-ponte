"""Sicurezza dello spike del ponte — Spesuccia.

Tre responsabilità, tutte imposte dalla Sez. 10.1 del brief e da ADR-0005 §6.

1. **Caricare il master token** solo da variabile d'ambiente o da `bridge/.env`
   (già escluso dal `.gitignore` al primo commit). Mai da un argomento a riga di
   comando: gli argomenti finiscono nella lista dei processi e nella cronologia
   della shell, cioè in due posti che nessuno ripulisce.
2. **Redigere.** Il master token non deve comparire in nessun output: non nei
   log, non nei messaggi d'errore, non nelle tracce di eccezione, **nemmeno
   troncato**. Qui si installano un filtro sul logging e un `excepthook` che
   riscrivono qualunque occorrenza prima che raggiunga lo schermo.
3. **Impedire che lo spike giri sulla nota reale.** Il brief Sez. 13 vieta di
   creare una nuova nota e ADR-0005 §6 impone che M0 usi una *nota di prova*.
   Le guardie qui sotto sono **sette** e devono passare tutte. Non esiste un
   flag per saltarle: un'opzione `--forza` renderebbe inutile l'intero
   meccanismo il giorno in cui qualcuno ha fretta.

Nota sul modello di minaccia: il master token dà accesso **completo**
all'account che lo ha generato (brief Sez. 2.1). Per questo l'account è
dedicato e non contiene nient'altro. Questo modulo riduce la probabilità che il
token esca dal processo per distrazione; non protegge da un attaccante che
abbia già il controllo della macchina.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import types
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

# ─────────────────────────────────────────────────────────────────────────────
# Errori
# ─────────────────────────────────────────────────────────────────────────────


class ErroreDiSicurezza(Exception):
    """Una condizione di sicurezza non è soddisfatta: lo spike non parte."""


class ConfigurazioneMancante(ErroreDiSicurezza):
    """Manca un valore obbligatorio nella configurazione."""


class NotaNonDiProva(ErroreDiSicurezza):
    """La nota indicata non supera le guardie: potrebbe essere quella reale."""


# ─────────────────────────────────────────────────────────────────────────────
# Redazione dei segreti
# ─────────────────────────────────────────────────────────────────────────────

_SOSTITUZIONE: Final[str] = "«segreto redatto»"
_segreti: list[str] = []


def registra_segreto(valore: str | None) -> None:
    """Aggiunge un valore all'elenco delle stringhe da redigere ovunque.

    Si registrano il master token e, dopo l'autenticazione, anche il token
    OAuth derivato: entrambi sono credenziali riutilizzabili.
    """
    if valore and len(valore) >= 8 and valore not in _segreti:
        _segreti.append(valore)


def redigi(testo: str) -> str:
    """Sostituisce ogni occorrenza di un segreto registrato."""
    for segreto in _segreti:
        if segreto in testo:
            testo = testo.replace(segreto, _SOSTITUZIONE)
    return testo


class _FiltroRedazione(logging.Filter):
    """Riscrive messaggio e argomenti di ogni record prima dell'emissione."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = redigi(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        chiave: redigi(str(valore))
                        for chiave, valore in record.args.items()
                    }
                else:
                    record.args = tuple(redigi(str(a)) for a in record.args)
        except Exception:  # pragma: no cover - la redazione non deve mai far cadere il log
            record.msg = _SOSTITUZIONE
            record.args = None
        return True


def installa_redazione() -> None:
    """Applica il filtro a tutto il logging e all'`excepthook` del processo.

    Va chiamata **prima** di caricare la configurazione: da quel momento in poi
    nessun percorso di uscita standard può stampare un segreto per distrazione.
    """
    filtro = _FiltroRedazione()
    logging.getLogger().addFilter(filtro)
    for nome in ("gkeepapi", "gpsoauth", "requests", "urllib3"):
        logger = logging.getLogger(nome)
        logger.addFilter(filtro)
        # Nessun DEBUG: gkeepapi e urllib3 in DEBUG stampano header e corpi.
        logger.setLevel(logging.WARNING)

    originale = sys.excepthook

    def _excepthook(
        tipo: type[BaseException],
        valore: BaseException,
        traccia: types.TracebackType | None,
    ) -> None:
        try:
            nuovi_args = tuple(
                redigi(a) if isinstance(a, str) else a for a in valore.args
            )
            valore.args = nuovi_args
        except Exception:  # pragma: no cover
            pass
        originale(tipo, valore, traccia)

    sys.excepthook = _excepthook


# ─────────────────────────────────────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────────────────────────────────────

#: Testo esatto della riga che l'operatore mette **a mano** nella nota di prova.
#: È una prova di consenso positiva: non può essere vera per sbaglio sulla nota
#: reale, perché nessuno la digiterebbe lì.
RIGA_DI_CONSENSO: Final[str] = "SPESUCCIA-NOTA-DI-PROVA"

#: Prefisso di ogni riga creata dallo spike. La pulizia finale rimuove solo
#: queste; la riga di consenso non comincia così e non viene mai toccata.
PREFISSO_SPIKE: Final[str] = "SPIKE"


@dataclass(repr=False)
class Configurazione:
    """Valori letti dall'ambiente o da `bridge/.env`.

    `__repr__` è ridefinito di proposito: la rappresentazione automatica di una
    dataclass stamperebbe il master token in ogni traccia di eccezione.
    """

    email: str
    master_token: str = field(repr=False)
    note_id: str
    device_id: str
    titolo_reale: str = "Spesuccia"
    marcatore_prova: str = "PROVA"
    max_righe: int = 40

    def __repr__(self) -> str:
        return (
            f"Configurazione(email={self.email!r}, note_id={self.note_id!r}, "
            f"device_id=«non stampato», master_token=«non stampato», "
            f"titolo_reale={self.titolo_reale!r}, "
            f"marcatore_prova={self.marcatore_prova!r}, "
            f"max_righe={self.max_righe!r})"
        )

    @property
    def forma_token_plausibile(self) -> bool:
        """Il master token di Google comincia per `aas_et/`.

        Restituisce un booleano — **non** un pezzo del token — così l'operatore
        può capire di aver incollato la stringa sbagliata senza che nulla del
        segreto finisca a schermo.
        """
        return self.master_token.startswith("aas_et/")


_RIGA_ENV = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _leggi_file_env(percorso: Path) -> dict[str, str]:
    """Parser minimo di `.env`.

    Volutamente senza dipendenze: una libreria in più per quindici righe di
    codice è una superficie in più da fidarsi, proprio nel punto in cui passa
    il segreto.
    """
    valori: dict[str, str] = {}
    if not percorso.is_file():
        return valori
    for riga in percorso.read_text(encoding="utf-8-sig").splitlines():
        if not riga.strip() or riga.lstrip().startswith("#"):
            continue
        corrispondenza = _RIGA_ENV.match(riga)
        if corrispondenza is None:
            continue
        chiave, grezzo = corrispondenza.group(1), corrispondenza.group(2).strip()
        if len(grezzo) >= 2 and grezzo[0] == grezzo[-1] and grezzo[0] in "\"'":
            grezzo = grezzo[1:-1]
        else:
            grezzo = grezzo.split(" #", 1)[0].strip()
        valori[chiave] = grezzo
    return valori


def carica_configurazione(percorso_env: Path) -> Configurazione:
    """Legge la configurazione: prima l'ambiente, poi `bridge/.env`.

    L'ambiente ha la precedenza, così su una macchina condivisa si può passare
    il token senza scriverlo su disco.
    """
    da_file = _leggi_file_env(percorso_env)

    def leggi(chiave: str, obbligatorio: bool = True, default: str = "") -> str:
        valore = os.environ.get(chiave) or da_file.get(chiave) or default
        valore = valore.strip()
        if obbligatorio and not valore:
            raise ConfigurazioneMancante(
                f"Manca {chiave}. Impostala come variabile d'ambiente oppure in "
                f"{percorso_env} (vedi bridge/.env.example e SETUP_KEEP.md)."
            )
        return valore

    master_token = leggi("SPESUCCIA_KEEP_MASTER_TOKEN")
    registra_segreto(master_token)

    device_id = leggi("SPESUCCIA_KEEP_DEVICE_ID", obbligatorio=False)
    if not device_id:
        raise ConfigurazioneMancante(
            "Manca SPESUCCIA_KEEP_DEVICE_ID. Non lasciarlo derivare dal MAC "
            "address della macchina: il ponte definitivo potrebbe girare "
            "altrove e l'identificativo di dispositivo deve restare lo stesso "
            "che ha generato il token. Generane uno una volta sola con "
            "`python -c \"import secrets; print(secrets.token_hex(8))\"` e "
            "conservalo accanto al token."
        )
    if not re.fullmatch(r"[0-9a-f]{16}", device_id):
        raise ConfigurazioneMancante(
            "SPESUCCIA_KEEP_DEVICE_ID deve essere esattamente 16 cifre "
            "esadecimali minuscole (l'androidId che gpsoauth si aspetta)."
        )

    massimo = leggi("SPESUCCIA_KEEP_MAX_RIGHE", obbligatorio=False, default="40")
    try:
        max_righe = int(massimo)
    except ValueError as errore:
        raise ConfigurazioneMancante(
            "SPESUCCIA_KEEP_MAX_RIGHE deve essere un numero intero."
        ) from errore

    return Configurazione(
        email=leggi("SPESUCCIA_KEEP_EMAIL"),
        master_token=master_token,
        note_id=leggi("SPESUCCIA_KEEP_NOTE_ID"),
        device_id=device_id,
        titolo_reale=leggi(
            "SPESUCCIA_KEEP_TITOLO_REALE", obbligatorio=False, default="Spesuccia"
        ),
        marcatore_prova=leggi(
            "SPESUCCIA_KEEP_MARCATORE_PROVA", obbligatorio=False, default="PROVA"
        ),
        max_righe=max_righe,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Le guardie sulla nota di prova
# ─────────────────────────────────────────────────────────────────────────────

_SIGILLO: Final[object] = object()


@dataclass(frozen=True)
class ConsensoNotaDiProva:
    """Prova che le sette guardie sono state superate.

    `PonteKeep` non si costruisce senza questo oggetto, e questo oggetto lo
    produce solo `verifica_nota_di_prova`. È il modo per rendere **impossibile
    per costruzione** aprire il ponte su una nota non verificata: chi in futuro
    volesse saltare il controllo dovrebbe riscrivere questo file, cioè fare una
    cosa visibile in code review.
    """

    sigillo: object = field(repr=False)
    note_id: str
    titolo: str
    righe_osservate: int

    def __post_init__(self) -> None:
        if self.sigillo is not _SIGILLO:
            raise NotaNonDiProva(
                "ConsensoNotaDiProva non può essere costruito a mano: "
                "passa da verifica_nota_di_prova()."
            )


def _normalizza(testo: str) -> str:
    """Minuscole, senza accenti, spazi compattati — solo per confrontare titoli."""
    senza_accenti = "".join(
        carattere
        for carattere in unicodedata.normalize("NFD", testo)
        if unicodedata.category(carattere) != "Mn"
    )
    return " ".join(senza_accenti.lower().split())


def titolo_confondibile(titolo_prova: str, titolo_reale: str) -> bool:
    """Vero se il titolo della nota di prova **contiene** quello della nota reale.

    La guardia 4 rifiuta solo il titolo *identico*: «Spesuccia PROVA» la supera
    — ed era anzi il nome che `SETUP_KEEP.md` suggeriva.

    Il punto è che le sette guardie proteggono tutto ciò che **questo script**
    tocca, e il canale vocale non ci passa. Quando si detta «Ehi Google,
    aggiungi pomodorini alla lista Spesuccia PROVA», il riconoscimento del nome
    della lista lo fa l'Assistente, per approssimazione, con criteri che non
    sono sotto il nostro controllo: è l'unico punto in cui M0 può finire per
    scrivere sulla **nota reale condivisa**, cosa che ADR-0005 §6 vieta.

    Questa funzione non è una guardia — non può esserlo, perché il rischio non
    è nel codice — è l'avvertimento che serve prima di dettare quel nome.
    """
    prova, reale = _normalizza(titolo_prova), _normalizza(titolo_reale)
    return bool(reale) and reale in prova


def verifica_conferma_nota(configurazione: Configurazione, conferma: str) -> None:
    """Guardia 2 — l'ID della nota va scritto **due volte**, in due posti diversi.

    Una volta nella configurazione e una volta sulla riga di comando. Un
    copia-incolla sbagliato non basta a far partire lo spike sulla nota reale:
    servirebbe farlo due volte allo stesso modo.
    """
    if not conferma:
        raise NotaNonDiProva(
            "Serve --conferma-nota con l'ID della nota di prova. "
            "È la conferma esplicita richiesta dal brief Sez. 5.1 e Sez. 13."
        )
    if conferma.strip() != configurazione.note_id:
        raise NotaNonDiProva(
            "L'ID passato a --conferma-nota non coincide con "
            "SPESUCCIA_KEEP_NOTE_ID. Lo spike si ferma: quando i due valori "
            "non concordano non è possibile sapere quale nota volevi."
        )


def verifica_nota_di_prova(
    nodo: Any,  # noqa: ANN401 - gkeepapi.node.TopLevelNode, importato dal chiamante
    configurazione: Configurazione,
) -> ConsensoNotaDiProva:
    """Guardie 3-7 sul nodo appena risolto. Tutte devono passare.

    Il chiamante ha già superato le guardie 1 (ID presente in configurazione,
    quindi **nessuna ricerca per titolo**: è la ricerca per titolo il modo in
    cui si finisce per sbaglio sulla nota reale) e 2 (doppia conferma).
    """
    from gkeepapi import node as _node  # import locale: il modulo resta testabile a vuoto

    # Guardia 3 — la nota esiste, è una lista, è viva. E non la creiamo mai.
    if nodo is None:
        raise NotaNonDiProva(
            "Nessuna nota con quell'ID è visibile dall'account dedicato. "
            "Lo spike NON crea note (brief Sez. 13): crea a mano la nota di "
            "prova, condividila con l'account dedicato e riprova."
        )
    if not isinstance(nodo, _node.List):
        raise NotaNonDiProva(
            "Quell'ID esiste ma non è una nota-lista con caselle. La nota di "
            "prova deve essere una lista, come quella reale."
        )
    if nodo.trashed or nodo.deleted:
        raise NotaNonDiProva("La nota di prova è nel cestino: ripristinala o creane un'altra.")

    titolo = nodo.title or ""

    # Guardia 4 — lista di esclusione: mai il titolo della nota reale.
    if _normalizza(titolo) == _normalizza(configurazione.titolo_reale):
        raise NotaNonDiProva(
            f"La nota si intitola «{titolo}», cioè come la nota reale della "
            "spesa. Lo spike si rifiuta di toccarla. M0 gira su una nota di "
            "prova (ADR-0005 §6)."
        )

    # Guardia 5 — il titolo dichiara che è una prova.
    if configurazione.marcatore_prova.lower() not in titolo.lower():
        raise NotaNonDiProva(
            f"Il titolo della nota («{titolo}») non contiene il marcatore "
            f"«{configurazione.marcatore_prova}». Rinomina la nota di prova, "
            "per esempio «Lista PROVA ponte», così è riconoscibile a colpo "
            "d'occhio anche dall'app Keep.\n"
            "    Non suggerisco «Spesuccia PROVA»: supererebbe questa guardia, "
            "ma il canale vocale riconosce i nomi lista per approssimazione, e "
            "un «Ehi Google» di prova finirebbe sulla lista vera. Meglio un "
            "nome che non le somigli."
        )

    righe = list(nodo.items)

    # Guardia 6 — prova di consenso positiva, messa a mano dall'operatore.
    if not any(riga.text.strip() == RIGA_DI_CONSENSO for riga in righe):
        raise NotaNonDiProva(
            f"Nella nota manca la riga di consenso «{RIGA_DI_CONSENSO}». "
            "Aggiungila a mano dall'app Keep (non spuntata) e riprova: è la "
            "conferma che questa è la nota di prova e non un'altra. Lo spike "
            "non la modifica e non la rimuove mai."
        )

    # Guardia 7 — una nota di prova è piccola. Una lista della spesa vera no.
    if len(righe) > configurazione.max_righe:
        raise NotaNonDiProva(
            f"La nota contiene {len(righe)} righe, più del limite di "
            f"{configurazione.max_righe}. Una nota di prova è quasi vuota: "
            "questa somiglia troppo a una lista vera. Se il limite è davvero "
            "troppo basso, alzalo con SPESUCCIA_KEEP_MAX_RIGHE — dopo aver "
            "guardato la nota."
        )

    return ConsensoNotaDiProva(
        sigillo=_SIGILLO,
        note_id=nodo.id,
        titolo=titolo,
        righe_osservate=len(righe),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Materiale di sessione su disco
# ─────────────────────────────────────────────────────────────────────────────


def scrivi_file_riservato(percorso: Path, contenuto: str) -> None:
    """Scrive in `bridge/state/` un file trattato come segreto.

    Lo stato di `gkeepapi` (`dump()`) non contiene il master token — verificato
    leggendo `Keep.dump()`, che serializza solo `keep_version`, etichette e
    nodi — ma contiene **il contenuto di tutte le note dell'account dedicato**.
    È materiale di sessione: glossario §5, *stato gkeepapi*. Per questo l'intera
    cartella `bridge/state/` è nel `.gitignore` dal primo commit.

    I permessi 0o600 hanno effetto su POSIX. Su Windows non sono significativi:
    lì la difesa è la cifratura del volume (BitLocker), come scritto in
    `SETUP_KEEP.md`.
    """
    percorso.parent.mkdir(parents=True, exist_ok=True)
    testo = redigi(contenuto)
    for segreto in _segreti:
        if segreto in testo:  # pragma: no cover - la redazione l'ha già tolto
            raise ErroreDiSicurezza(
                "Rifiuto di scrivere su disco: il contenuto conteneva un segreto."
            )
    descrittore = os.open(percorso, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descrittore, "w", encoding="utf-8") as file:
        file.write(testo)


def contiene_segreti(contenuto: str) -> bool:
    """Vero se il testo contiene un segreto registrato. Usato come asserzione."""
    return any(segreto in contenuto for segreto in _segreti)


# ─────────────────────────────────────────────────────────────────────────────
# La nota REALE (M3)
#
# Fino a M0 il ponte poteva toccare **solo** la nota di prova, e la guardia 4 qui
# sopra si rifiutava esplicitamente di aprire quella vera. Da M3 il ponte lavora
# sulla nota vera: è il suo mestiere.
#
# ⚠️ Le due strade restano **separate e con due sigilli distinti**, invece di
# ammorbidire le guardie esistenti. La ragione è che lo spike non deve poter
# toccare la nota reale nemmeno per sbaglio, adesso che quella strada esiste:
# `spike_keep.py` chiede un `ConsensoNotaDiProva` e continuerà a riceverne uno
# solo da `verifica_nota_di_prova`, che la nota vera la rifiuta come prima.
#
# Chi vuole la nota reale deve chiedere un tipo diverso, in un punto del codice
# diverso — cioè fare una cosa visibile in code review, che è lo stesso criterio
# con cui è stato costruito il consenso originale.
# ─────────────────────────────────────────────────────────────────────────────

_SIGILLO_REALE: Final[object] = object()


class NotaRealeNonConfermata(ErroreDiSicurezza):
    """La nota indicata non è la lista reale confermata in configurazione."""


@dataclass(frozen=True)
class ConsensoNotaReale:
    """Prova che la nota aperta è **quella** confermata a mano, e non un'altra.

    Come `ConsensoNotaDiProva`, non si costruisce a mano: lo produce solo
    `verifica_nota_reale`.
    """

    sigillo: object = field(repr=False)
    note_id: str
    titolo: str
    righe_osservate: int

    def __post_init__(self) -> None:
        if self.sigillo is not _SIGILLO_REALE:
            raise NotaRealeNonConfermata(
                "ConsensoNotaReale non può essere costruito a mano: "
                "passa da verifica_nota_reale()."
            )


def verifica_nota_reale(
    nodo: Any,  # noqa: ANN401 - gkeepapi.node.TopLevelNode
    configurazione: Configurazione,
    conferma_note_id: str,
) -> ConsensoNotaReale:
    """Guardie sulla nota reale. Poche, ma nessuna saltabile.

    Il brief Sez. 5.1 chiede l'«aggancio alla nota esistente, per titolo, con
    **conferma esplicita dell'ID nota** in configurazione», e Sez. 13 vieta di
    crearne una nuova. Qui l'ID va detto **due volte** — una in configurazione e
    una dal chiamante — perché un ID sbagliato in un file `.env` è un errore che
    si copia, mentre due valori che devono coincidere non si copiano insieme per
    distrazione.

    Non c'è nessun limite di righe e nessun marcatore da cercare: la nota vera è
    grande e si chiama come si chiama. La guardia qui è l'opposto di quella dello
    spike — **il titolo deve essere quello reale**, non deve non esserlo.
    """
    # ⚠️ Le due conferme si controllano **prima** di importare gkeepapi, e non è
    # un dettaglio di stile: sono le uniche guardie che non hanno bisogno di
    # guardare la nota, e tenerle davanti le rende provabili senza la libreria.
    # Una guardia di sicurezza senza test è una guardia di cui nessuno sa se
    # scatta.
    if not conferma_note_id.strip():
        raise NotaRealeNonConfermata(
            "Serve la conferma esplicita dell'ID della nota. Il ponte non "
            "indovina su quale lista lavorare."
        )
    ids_ammessi = [i.strip() for i in configurazione.note_id.split(",") if i.strip()]
    if ids_ammessi and conferma_note_id.strip() not in ids_ammessi:
        raise NotaRealeNonConfermata(
            "L'ID confermato non coincide con SPESUCCIA_KEEP_NOTE_ID. Il ponte "
            "si ferma: quando i due valori divergono, uno dei due è sbagliato e "
            "non si può sapere quale."
        )

    if nodo is None:
        raise NotaRealeNonConfermata(
            "Nessuna nota con quell'ID è visibile dall'account dedicato. "
            "Il ponte NON crea note (brief Sez. 13): controlla che l'account "
            "dedicato sia ancora collaboratore della nota."
        )

    # L'import sta **qui**, il più tardi possibile: è il primo controllo che ha
    # davvero bisogno della libreria. Tutto ciò che sta sopra si prova senza.
    from gkeepapi import node as _node  # noqa: PLC0415 - import locale, deliberato

    if not isinstance(nodo, _node.List):
        raise NotaRealeNonConfermata(
            "Quell'ID esiste ma non è una nota-lista con caselle. La lista "
            "della spesa deve essere una lista."
        )
    if nodo.trashed or nodo.deleted:
        raise NotaRealeNonConfermata(
            "La nota è nel cestino. Il ponte si ferma invece di ricrearla: "
            "ripristinala dall'app Keep."
        )

    titolo = nodo.title or ""
    if _normalizza(titolo) != _normalizza(configurazione.titolo_reale):
        raise NotaRealeNonConfermata(
            f"La nota si intitola «{titolo}», non «{configurazione.titolo_reale}». "
            "O è la nota sbagliata, o qualcuno l'ha rinominata: in entrambi i "
            "casi il ponte si ferma invece di scriverci dentro. Se il nome è "
            "cambiato davvero, aggiorna SPESUCCIA_KEEP_TITOLO_REALE."
        )

    righe = list(nodo.items)
    return ConsensoNotaReale(
        sigillo=_SIGILLO_REALE,
        note_id=nodo.id,
        titolo=titolo,
        righe_osservate=len(righe),
    )
