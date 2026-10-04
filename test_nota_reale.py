"""Prove sulla guardia della nota reale.

`verifica_nota_reale` è ciò che sta fra il ponte e la lista della spesa vera: se
sbaglia, o il ponte non parte, o scrive sulla nota sbagliata. Una guardia di
sicurezza senza test è una guardia di cui nessuno sa se scatta.

Le prove sono divise in due gruppi:

  - le **due conferme** — l'ID passato e l'ID in configurazione — girano senza
    `gkeepapi`, perché nel codice stanno prima dell'import. È deliberato: sono
    le uniche che non hanno bisogno di guardare la nota, e tenerle davanti le
    rende provabili ovunque;
  - le guardie **sulla nota** hanno bisogno di `gkeepapi` per il controllo di
    tipo, e si saltano dove non c'è.
"""

from __future__ import annotations

import unittest

from sicurezza import (
    ConsensoNotaReale,
    Configurazione,
    NotaRealeNonConfermata,
    verifica_nota_reale,
)

try:  # pragma: no cover - dipende dall'ambiente, non dal codice
    from gkeepapi import node as _node

    GKEEPAPI_CE = True
except ImportError:  # pragma: no cover
    GKEEPAPI_CE = False


ID_NOTA = "1a2b3c4d5e6f"


def configurazione(note_id: str = ID_NOTA, titolo_reale: str = "Spesuccia") -> Configurazione:
    return Configurazione(
        email="dedicato@example.com",
        master_token="aas_et/finto-per-i-test-non-e-un-token-vero",
        note_id=note_id,
        device_id="0123456789abcdef",
        titolo_reale=titolo_reale,
        marcatore_prova="PROVA",
        max_righe=40,
    )


class TestLeDueConferme(unittest.TestCase):
    """Girano senza gkeepapi: nel codice stanno prima dell'import."""

    def test_conferma_vuota_ferma_tutto(self) -> None:
        # Il ponte non indovina su quale lista lavorare.
        with self.assertRaises(NotaRealeNonConfermata):
            verifica_nota_reale(object(), configurazione(), "")
        with self.assertRaises(NotaRealeNonConfermata):
            verifica_nota_reale(object(), configurazione(), "   ")

    def test_conferma_diversa_dalla_configurazione_ferma_tutto(self) -> None:
        # ⚠️ Quando i due valori divergono, uno dei due è sbagliato e non si può
        # sapere quale. Fermarsi è l'unica condotta onesta: scegliere il primo
        # o il secondo vorrebbe dire scrivere su una nota a caso.
        with self.assertRaises(NotaRealeNonConfermata):
            verifica_nota_reale(object(), configurazione(), "un-altro-id")

    def test_gli_spazi_intorno_non_contano(self) -> None:
        # Un ID incollato da un URL si porta dietro spazi: farlo fallire per
        # quello manderebbe a cercare un errore che non c'è. Qui però la nota è
        # `None`, quindi si arriva alla guardia successiva — che è la prova che
        # le due conferme sono passate.
        with self.assertRaises(NotaRealeNonConfermata) as caso:
            verifica_nota_reale(None, configurazione(), f"  {ID_NOTA}  ")
        self.assertIn("visibile dall'account dedicato", str(caso.exception))

    def test_censimento_remoto_accetta_qualsiasi_id(self) -> None:
        # Se la nota arriva dal censimento autorizzato remoto (GET /keep-pull),
        # l'ID non è vincolato a SPESUCCIA_KEEP_NOTE_ID locale.
        with self.assertRaises(NotaRealeNonConfermata) as caso:
            verifica_nota_reale(None, configurazione(), "altro-id-valido", da_censimento_remoto=True)
        self.assertIn("visibile dall'account dedicato", str(caso.exception))


class TestConsensoNonFalsificabile(unittest.TestCase):
    def test_non_si_costruisce_a_mano(self) -> None:
        # È il motivo per cui il consenso esiste: chi volesse saltare le guardie
        # dovrebbe riscrivere `sicurezza.py`, cioè fare una cosa visibile in
        # code review.
        with self.assertRaises(NotaRealeNonConfermata):
            ConsensoNotaReale(
                sigillo=object(),
                note_id=ID_NOTA,
                titolo="Spesuccia",
                righe_osservate=0,
            )


class NotaFinta:
    """Il minimo che le guardie guardano. Non è una `gkeepapi.node.List`."""

    def __init__(self, titolo: str, righe: int = 3, trashed: bool = False) -> None:
        self.title = titolo
        self.items = [object()] * righe
        self.trashed = trashed
        self.deleted = False
        self.id = ID_NOTA


@unittest.skipUnless(GKEEPAPI_CE, "gkeepapi non installato in questo ambiente")
class TestLeGuardieSullaNota(unittest.TestCase):
    """Hanno bisogno di gkeepapi per il controllo di tipo."""

    def _lista(self, titolo: str, righe: int = 3, trashed: bool = False):  # noqa: ANN202
        """Una `List` vera di gkeepapi, con il titolo che serve al caso."""
        lista = _node.List()
        lista.title = titolo
        for numero in range(righe):
            lista.add(f"riga {numero}", False)
        if trashed:
            lista.trash()
        return lista

    def test_una_nota_che_non_e_una_lista_non_passa(self) -> None:
        # La lista della spesa deve essere una lista con caselle: su una nota di
        # testo semplice i cinque verbi non hanno senso.
        with self.assertRaises(NotaRealeNonConfermata) as caso:
            verifica_nota_reale(NotaFinta("Spesuccia"), configurazione(), ID_NOTA)
        self.assertIn("non è una nota-lista", str(caso.exception))

    def test_una_nota_nel_cestino_non_passa(self) -> None:
        # ⚠️ Il ponte si ferma invece di ricrearla: il brief Sez. 13 vieta di
        # creare note, e una nota nel cestino è un fatto da guardare in faccia.
        with self.assertRaises(NotaRealeNonConfermata) as caso:
            verifica_nota_reale(self._lista("Spesuccia", trashed=True), configurazione(), ID_NOTA)
        self.assertIn("cestino", str(caso.exception))

    def test_il_titolo_sbagliato_ferma_tutto(self) -> None:
        # ⚠️ È la guardia **opposta** a quella dello spike: lì il titolo reale
        # era vietato, qui è obbligatorio. O è la nota sbagliata, o qualcuno
        # l'ha rinominata: in entrambi i casi non ci si scrive dentro.
        with self.assertRaises(NotaRealeNonConfermata) as caso:
            verifica_nota_reale(self._lista("Lista PROVA ponte"), configurazione(), ID_NOTA)
        self.assertIn("non «Spesuccia»", str(caso.exception))

    def test_il_titolo_giusto_passa(self) -> None:
        consenso = verifica_nota_reale(self._lista("Spesuccia", righe=5), configurazione(), ID_NOTA)
        self.assertIsInstance(consenso, ConsensoNotaReale)
        self.assertEqual(consenso.titolo, "Spesuccia")
        self.assertEqual(consenso.righe_osservate, 5)

    def test_il_confronto_sul_titolo_ignora_accenti_e_maiuscole(self) -> None:
        # Chi rinomina la nota dal telefono può metterci una maiuscola diversa.
        # Fermare il ponte per quello sarebbe zelo, non sicurezza.
        consenso = verifica_nota_reale(self._lista("SPESUCCIA"), configurazione(), ID_NOTA)
        self.assertIsInstance(consenso, ConsensoNotaReale)

    def test_nessun_limite_di_righe_sulla_nota_vera(self) -> None:
        # Lo spike si ferma sopra le 40 righe perché una nota di prova è
        # piccola. Qui è il contrario: la nota vera ne ha 47 e ne avrà di più.
        consenso = verifica_nota_reale(self._lista("Spesuccia", righe=200), configurazione(), ID_NOTA)
        self.assertEqual(consenso.righe_osservate, 200)

    def test_censimento_remoto_accetta_qualsiasi_titolo(self) -> None:
        # Una famiglia può intitolare la nota come preferisce.
        consenso = verifica_nota_reale(
            self._lista("Spesa di famiglia"),
            configurazione(),
            "qualsiasi-id",
            da_censimento_remoto=True,
        )
        self.assertIsInstance(consenso, ConsensoNotaReale)
        self.assertEqual(consenso.titolo, "Spesa di famiglia")


if __name__ == "__main__":
    unittest.main()
