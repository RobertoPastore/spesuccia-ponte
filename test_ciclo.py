"""Prove sul ciclo del ponte.

Girano **senza rete, senza Google e senza master token**: il trasporto è
sostituito da un finto che registra le chiamate. È possibile perché il ciclo non
ragiona — applica cinque verbi — e quindi tutto ciò che c'è da verificare è che
li applichi giusti, nell'ordine giusto, e che riferisca com'è andata.

Il pezzo che *ragiona* ha i suoi test dall'altra parte, in
`packages/shared/src/riconciliazione`, e non ha bisogno di Python.
"""

from __future__ import annotations

import unittest
from typing import Any

from ciclo import ConfigurazioneCiclo, ErroreDiCiclo, applica_operazioni, nuovo_run_id


class TrasportoFinto:
    """Registra i verbi ricevuti. Può essere istruito a fallire su un verbo."""

    def __init__(self, fallisci_su: str | None = None) -> None:
        self.chiamate: list[tuple[str, Any, ...]] = []
        self.fallisci_su = fallisci_su
        self._prossimo_id = 0

    def _forse_fallisci(self, verbo: str) -> None:
        if self.fallisci_su == verbo:
            raise RuntimeError(f"{verbo} non riuscita")

    def crea_riga(self, testo: str) -> str:
        self._forse_fallisci("crea_riga")
        self.chiamate.append(("crea_riga", testo))
        self._prossimo_id += 1
        return f"K{self._prossimo_id}"

    def spunta_riga(self, external_id: str) -> None:
        self._forse_fallisci("spunta_riga")
        self.chiamate.append(("spunta_riga", external_id))

    def despunta_riga(self, external_id: str) -> None:
        self._forse_fallisci("despunta_riga")
        self.chiamate.append(("despunta_riga", external_id))

    def modifica_testo(self, external_id: str, testo: str) -> None:
        self._forse_fallisci("modifica_testo")
        self.chiamate.append(("modifica_testo", external_id, testo))

    def rimuovi_riga(self, external_id: str) -> None:
        self._forse_fallisci("rimuovi_riga")
        self.chiamate.append(("rimuovi_riga", external_id))


class TestApplicaOperazioni(unittest.TestCase):
    def test_applica_i_cinque_verbi(self) -> None:
        ponte = TrasportoFinto()
        operazioni = [
            {"op": "crea_riga", "op_id": "r-1", "testo": "🥕 carote"},
            {"op": "spunta_riga", "op_id": "r-2", "external_id": "K9"},
            {"op": "despunta_riga", "op_id": "r-3", "external_id": "K9"},
            {"op": "modifica_testo", "op_id": "r-4", "external_id": "K9", "testo": "🥕 2 kg carote"},
            {"op": "rimuovi_riga", "op_id": "r-5", "external_id": "K9"},
        ]

        esiti = applica_operazioni(ponte, operazioni)

        self.assertEqual(
            ponte.chiamate,
            [
                ("crea_riga", "🥕 carote"),
                ("spunta_riga", "K9"),
                ("despunta_riga", "K9"),
                ("modifica_testo", "K9", "🥕 2 kg carote"),
                ("rimuovi_riga", "K9"),
            ],
        )
        self.assertTrue(all(esito["ok"] for esito in esiti))
        self.assertEqual([esito["op_id"] for esito in esiti], ["r-1", "r-2", "r-3", "r-4", "r-5"])

    def test_crea_riga_riporta_lidentificativo_assegnato(self) -> None:
        # È l'unico valore che il ponte **produce** invece di ricopiare: senza,
        # keep-ack non può assegnare la prenotazione e al ciclo dopo nascerebbe
        # una seconda riga.
        esiti = applica_operazioni(
            TrasportoFinto(), [{"op": "crea_riga", "op_id": "r-1", "testo": "🍞 pane"}]
        )
        self.assertEqual(esiti[0]["external_id_assegnato"], "K1")

    def test_gli_altri_verbi_non_assegnano_niente(self) -> None:
        esiti = applica_operazioni(
            TrasportoFinto(), [{"op": "spunta_riga", "op_id": "r-1", "external_id": "K9"}]
        )
        self.assertNotIn("external_id_assegnato", esiti[0])

    def test_una_operazione_fallita_non_ferma_le_altre(self) -> None:
        # ⚠️ È la proprietà che decide fra «un giro perso» e «cinque minuti».
        # Una riga cancellata dall'app Keep un istante prima farebbe fallire la
        # sua spunta, e fermarsi lì butterebbe via anche ciò che non c'entra.
        ponte = TrasportoFinto(fallisci_su="spunta_riga")
        operazioni = [
            {"op": "crea_riga", "op_id": "r-1", "testo": "🥕 carote"},
            {"op": "spunta_riga", "op_id": "r-2", "external_id": "K9"},
            {"op": "rimuovi_riga", "op_id": "r-3", "external_id": "K8"},
        ]

        esiti = applica_operazioni(ponte, operazioni)

        self.assertEqual([esito["ok"] for esito in esiti], [True, False, True])
        # La terza è stata eseguita davvero, non solo dichiarata riuscita.
        self.assertIn(("rimuovi_riga", "K8"), ponte.chiamate)

    def test_un_fallimento_riferisce_il_perche(self) -> None:
        esiti = applica_operazioni(
            TrasportoFinto(fallisci_su="rimuovi_riga"),
            [{"op": "rimuovi_riga", "op_id": "r-1", "external_id": "K9"}],
        )
        self.assertFalse(esiti[0]["ok"])
        self.assertIn("rimuovi_riga non riuscita", esiti[0]["errore"])

    def test_un_verbo_sconosciuto_fallisce_invece_di_essere_ignorato(self) -> None:
        # Ignorarlo in silenzio vorrebbe dire che una versione nuova delle Edge
        # Function e una vecchia del ponte convivono senza che nessuno lo sappia.
        ponte = TrasportoFinto()
        esiti = applica_operazioni(
            ponte, [{"op": "archivia_riga", "op_id": "r-1", "external_id": "K9"}]
        )

        self.assertFalse(esiti[0]["ok"])
        self.assertEqual(ponte.chiamate, [])
        # ⚠️ È il **messaggio** a fare la diagnosi. Se il verbo si riconoscesse
        # dopo aver cercato i suoi campi, un `archivia_riga` senza external_id
        # fallirebbe con «senza external_id» e manderebbe a cercare un campo
        # mancante invece della versione disallineata.
        self.assertIn("Verbo sconosciuto", esiti[0]["errore"])
        self.assertIn("archivia_riga", esiti[0]["errore"])

    def test_operazione_senza_op_id_si_salta(self) -> None:
        # Senza op_id non c'è niente da riferire: l'ack non saprebbe a quale
        # riga del piano attaccare l'esito.
        ponte = TrasportoFinto()
        esiti = applica_operazioni(ponte, [{"op": "spunta_riga", "external_id": "K9"}])

        self.assertEqual(esiti, [])
        self.assertEqual(ponte.chiamate, [])

    def test_operazione_malformata_fallisce_senza_toccare_la_nota(self) -> None:
        ponte = TrasportoFinto()
        esiti = applica_operazioni(
            ponte,
            [
                {"op": "crea_riga", "op_id": "r-1"},  # senza testo
                {"op": "spunta_riga", "op_id": "r-2"},  # senza external_id
                {"op": "modifica_testo", "op_id": "r-3", "external_id": "K9"},  # senza testo
            ],
        )

        self.assertEqual([esito["ok"] for esito in esiti], [False, False, False])
        self.assertEqual(ponte.chiamate, [])

    def test_un_elenco_vuoto_non_fa_niente(self) -> None:
        ponte = TrasportoFinto()
        self.assertEqual(applica_operazioni(ponte, []), [])
        self.assertEqual(ponte.chiamate, [])


class TestConfigurazione(unittest.TestCase):
    def test_il_token_non_compare_nel_repr(self) -> None:
        # ⚠️ Un `repr` di questo oggetto in un traceback finirebbe nei log di
        # GitHub Actions, che su un repository pubblico legge chiunque.
        configurazione = ConfigurazioneCiclo(
            url_funzioni="https://esempio.supabase.co/functions/v1",
            token_ponte="TOKEN-SEGRETISSIMO-DEL-PONTE",
        )
        self.assertNotIn("TOKEN-SEGRETISSIMO", repr(configurazione))
        self.assertIn("redatto", repr(configurazione))


class TestRunId(unittest.TestCase):
    def test_e_un_uuid_e_cambia_ogni_volta(self) -> None:
        # `bridge_operations.run_id` è una colonna `uuid`: un identificativo
        # qualunque verrebbe rifiutato da Postgres a metà ciclo.
        import uuid as _uuid

        primo, secondo = nuovo_run_id(), nuovo_run_id()
        self.assertNotEqual(primo, secondo)
        _uuid.UUID(primo)  # solleva se non è un UUID


class TestErroreDiCiclo(unittest.TestCase):
    def test_e_unaeccezione_propria(self) -> None:
        self.assertTrue(issubclass(ErroreDiCiclo, Exception))


if __name__ == "__main__":
    unittest.main()
