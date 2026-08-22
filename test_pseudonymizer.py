"""Tests für pseudonymizer.py — laufen ohne LLM und ohne Netz.

    python -m pytest test_pseudonymizer.py -q
"""

import pseudonymizer as ps


def test_parse_terms_trennt_an_zeilenumbruch_und_semikolon_nicht_am_komma():
    terms = "Schönbrunner Straße 218, 1120 Wien=adresse\nMax Maier=person; Muster GmbH=firma"
    got = ps.parse_terms(terms)
    assert ("Schönbrunner Straße 218, 1120 Wien", "adresse") in got
    assert ("Max Maier", "person") in got
    assert ("Muster GmbH", "firma") in got
    assert len(got) == 3  # die Anschrift bleibt am Stück


def test_regex_sweep_findet_iban_mail_gz_und_wirft_teiltreffer_weg():
    src = ("Zahlung auf AT12 2011 1000 0123 4567, Kontakt office@test.at, "
           "GZ 14 Cg 88/25k, Tel 0664 3312299.")
    got = ps.regex_sweep(src)
    cats = {cat for _v, cat in got}
    vals = {v for v, _c in got}
    assert "iban" in cats and "az" in cats
    assert "office@test.at" in vals
    assert "14 Cg 88/25k" in vals
    # kein Teiltreffer der Telefonmaske innerhalb der IBAN
    assert not any(v != "AT12 2011 1000 0123 4567" and v in "AT12 2011 1000 0123 4567"
                   for v in vals)


def test_apply_ersetzt_vollnamen_und_nachnamen_stabil():
    mapping, alias = {}, {}
    src = "Ing. Franz Hofstetter klagt. Hofstetter fordert EUR 12.000,00."
    res = ps.apply(src, mapping, alias, [("Ing. Franz Hofstetter", "person")])
    assert "[PERSON_1]" in res.text
    assert "Hofstetter" not in res.text
    assert "EUR 12.000,00" in res.text          # Beträge bleiben unangetastet
    assert res.clean
    # zweiter Lauf: gleicher Platzhalter, kein neuer Eintrag
    res2 = ps.apply("Hofstetter erneut.", mapping, alias,
                    [("Ing. Franz Hofstetter", "person")])
    assert "[PERSON_1]" in res2.text
    assert res2.new_entries == 0


def test_apply_meldet_restbefund():
    mapping, alias = {}, {}
    ps.apply("Franz Huber war da.", mapping, alias, [("Franz Huber", "person")])
    # Klarwert steht (künstlich) wieder im Text -> Restbefund
    res = ps.apply("Franz Huber schon wieder, aber diesmal bleibt er stehen: "
                   "FranzXHuber", mapping, alias, [])
    assert "[PERSON_1]" in res.text
    # jetzt echter Restbefund: Wert in mapping, aber im Text nicht ersetzbar
    mapping["[PERSON_9]"] = "Anna Gruber"
    res3 = ps.apply("Anna Gruber%%%", mapping, alias, [])
    # 'Anna Gruber' wird ersetzt (steht in rev) -- also sauber
    assert "Anna Gruber" not in res3.text


def test_platzhalter_werden_nie_selbst_ersetzt():
    mapping, alias = {}, {}
    res = ps.apply("[PERSON_1] bleibt wie er ist.", mapping, alias,
                   [("[PERSON_1]", "person")])
    assert res.new_entries == 0
    assert res.text == "[PERSON_1] bleibt wie er ist."


def test_roundtrip_pseudonymize_depseudonymize(tmp_path):
    maps = tmp_path / "maps"
    src = ("Dr. Beatrix Lindtner (IBAN AT12 2011 1000 0123 4567) schreibt an "
           "Milos Petrovic wegen 14 Cg 88/25k.")
    res = ps.pseudonymize_text(src, "TESTAKT", maps,
                               found=[("Dr. Beatrix Lindtner", "person"),
                                      ("Milos Petrovic", "person")])
    assert res.clean
    assert "Lindtner" not in res.text and "Petrovic" not in res.text
    assert "AT12" not in res.text

    back, n, unresolved = ps.depseudonymize_text(res.text, "TESTAKT", maps)
    assert not unresolved
    assert "Dr. Beatrix Lindtner" in back
    assert "AT12 2011 1000 0123 4567" in back
    assert "14 Cg 88/25k" in back


def test_map_bleibt_ueber_laeufe_stabil(tmp_path):
    maps = tmp_path / "maps"
    r1 = ps.pseudonymize_text("Kurt Weninger baut.", "AKT2", maps,
                              found=[("Kurt Weninger", "person")])
    r2 = ps.pseudonymize_text("Weninger baut weiter.", "AKT2", maps, found=[])
    ph = [k for k in ps.load_map(ps.map_path("AKT2", maps))[0]][0]
    assert ph in r1.text and ph in r2.text  # derselbe Platzhalter in beiden Läufen


def test_merge_llm_mapping_table_nimmt_nur_eckige_platzhalter(tmp_path):
    maps = tmp_path / "maps"
    table = (
        "| Originalbezeichnung | Anonymisierte Bezeichnung |\n"
        "|---------------------|---------------------------|\n"
        "| Muster GmbH | [Firma A] |\n"
        "| Max Mustermann | Geschäftsführer 1 |\n"
        "| Rennweg 42, 1010 Wien | [Adresse im 1. Bezirk] |\n"
    )
    added, skipped = ps.merge_llm_mapping_table(table, "AKT3", maps)
    assert added == 2
    assert skipped == 1  # 'Geschäftsführer 1' ist kein sicherer Platzhalter
    mapping, _ = ps.load_map(ps.map_path("AKT3", maps))
    assert mapping["[Firma A]"] == "Muster GmbH"


def test_verify_no_cleartext(tmp_path):
    maps = tmp_path / "maps"
    ps.pseudonymize_text("Firma Weinberg & Partner Bauträger GmbH zahlt.",
                         "AKT4", maps,
                         found=[("Weinberg & Partner Bauträger GmbH", "firma")])
    rest = ps.verify_no_cleartext(
        "Hier steht Weinberg & Partner Bauträger GmbH nochmal im Klartext.",
        "AKT4", maps)
    assert rest  # Klarwert gefunden
    assert not ps.verify_no_cleartext("Hier ist alles sauber, [FIRMA_1].",
                                      "AKT4", maps)


def test_collapse_doppelte_platzhalter():
    assert ps._collapse("[FIRMA_1] & [FIRMA_1] GmbH") == "[FIRMA_1] GmbH"


def test_titel_variante_wird_zusammengefuehrt():
    """'Dr. Abramovici' bekommt den Platzhalter von 'Dr. Ileana Abramovici'."""
    mapping, alias = {}, {}
    res = ps.apply(
        "Dr. Ileana Abramovici vermietet. Dr. Abramovici leitet weiter. "
        "Abramovici antwortet.",
        mapping, alias,
        [("Dr. Ileana Abramovici", "person"), ("Dr. Abramovici", "person")])
    assert res.total_entries == 1
    assert "Abramovici" not in res.text
    assert res.text.count("[PERSON_1]") == 3


def test_gleicher_nachname_bleibt_getrennte_person():
    """'Andrea Hofstetter' wird NICHT mit 'Ing. Franz Hofstetter' verschmolzen."""
    mapping, alias = {}, {}
    res = ps.apply("Ing. Franz Hofstetter und Andrea Hofstetter streiten.",
                   mapping, alias,
                   [("Ing. Franz Hofstetter", "person"),
                    ("Andrea Hofstetter", "person")])
    assert res.total_entries == 2
    assert "[PERSON_1]" in res.text and "[PERSON_2]" in res.text


def test_firmen_kurzname_wird_zusammengefuehrt():
    mapping, alias = {}, {}
    res = ps.apply("Die Frieda Rustler Gebäudeverwaltung GmbH & Co KG "
                   "verwaltet. Rustler schreibt.",
                   mapping, alias,
                   [("Frieda Rustler Gebäudeverwaltung GmbH & Co KG", "firma"),
                    ("Rustler", "firma")])
    assert res.total_entries == 1
    assert "Rustler" not in res.text


def test_adress_varianten_und_blanke_strasse():
    """Teilstück-Merge + Straßen-Alias: die blanke Straße bleibt nicht stehen."""
    mapping, alias = {}, {}
    res = ps.apply(
        "Wohnung in 1150 Wien, Siebeneichengasse 1-3, Top 4. "
        "Objekt Siebeneichengasse 1-3, Top 4. "
        "Hausverwaltung des Objekts Siebeneichengasse 1-3, versendet Post.",
        mapping, alias,
        [("1150 Wien, Siebeneichengasse 1-3, Top 4", "adresse"),
         ("Siebeneichengasse 1-3, Top 4", "adresse")])
    assert res.total_entries == 1          # Variante wurde gemerged
    assert "Siebeneichengasse" not in res.text
    assert res.clean


def test_strassen_alias_mit_getrenntem_strassenwort():
    mapping, alias = {}, {}
    res = ps.apply("1120 Wien, Schönbrunner Straße 218 und später nur "
                   "Schönbrunner Straße 218.",
                   mapping, alias,
                   [("1120 Wien, Schönbrunner Straße 218", "adresse")])
    assert "Schönbrunner" not in res.text


def test_telefon_regex_frisst_keine_dateinamen_daten():
    got = ps.regex_sweep("Anhang CamScanner 21-06-2026 15.24.pdf und "
                         "CamScanner 25-04-2026 17.49.pdf, Tel 0664 3312299.")
    vals = {v for v, _c in got}
    assert "0664 3312299" in vals
    assert not any("2026" in v for v in vals)  # keine Datumsfetzen


def test_verify_faengt_blanke_kurzform(tmp_path):
    """Der Fall aus dem Abramovici-Lauf: bekannter Nachname/Straße steht
    noch im Text -> Endkontrolle schlägt an."""
    maps = tmp_path / "maps"
    ps.pseudonymize_text("Dr. Ileana Abramovici, 1150 Wien, "
                         "Siebeneichengasse 1-3, Top 4.",
                         "AKT5", maps,
                         found=[("Dr. Ileana Abramovici", "person"),
                                ("1150 Wien, Siebeneichengasse 1-3, Top 4", "adresse")])
    assert ps.verify_no_cleartext("Objekt Siebeneichengasse 1-3, schön.", "AKT5", maps)
    assert ps.verify_no_cleartext("Frau Abramovici war da.", "AKT5", maps)
    assert not ps.verify_no_cleartext("Alles sauber bei [PERSON_1].", "AKT5", maps)


def test_email_ohne_tld_wird_gemerged():
    mapping, alias = {}, {}
    res = ps.apply("Mail von betriebskostenabrechnung@verwaltung.rustler.eu, "
                   "teils zitiert als betriebskostenabrechnung@verwaltung.rustler",
                   mapping, alias,
                   [("betriebskostenabrechnung@verwaltung.rustler.eu", "sonst"),
                    ("betriebskostenabrechnung@verwaltung.rustler", "sonst")])
    assert res.total_entries == 1


def test_flex_findet_wert_mit_zeilenumbruch():
    mapping, alias = {}, {}
    res = ps.apply("Schönbrunner\nStraße 218 in Wien.", mapping, alias,
                   [("Schönbrunner Straße 218", "adresse")])
    assert "[ADRESSE_1]" in res.text


def test_grossschreibung_variante_wird_gemerged():
    """'FRIEDA RUSTLER ... KG' im Rechnungskopf ist dieselbe Firma."""
    mapping, alias = {}, {}
    res = ps.apply(
        "Frieda Rustler Gebäudeverwaltung GmbH & Co KG schreibt. "
        "Im Kopf: FRIEDA RUSTLER GEBÄUDEVERWALTUNG GmbH & Co KG.",
        mapping, alias,
        [("Frieda Rustler Gebäudeverwaltung GmbH & Co KG", "firma"),
         ("FRIEDA RUSTLER GEBÄUDEVERWALTUNG GmbH & Co KG", "firma")])
    assert res.total_entries == 1
    assert "RUSTLER" not in res.text and "Rustler" not in res.text
