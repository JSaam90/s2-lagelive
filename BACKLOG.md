# S2-LageLive · Backlog & Roadmap

> Strukturiertes Backlog für zielgerichtete Entwicklung.
> Jedes Item hat: Priorität (P1/P2/P3), Status, Abhängigkeiten, Akzeptanzkriterien.

---

## Legende

| Symbol | Bedeutung |
|--------|-----------|
| 🔴 P1 | Kritisch – System nicht nutzbar |
| 🟡 P2 | Wichtig – nächste Release |
| 🟢 P3 | Nice-to-have – nach R2 |
| ✅ Done | Implementiert |
| 🔧 In Progress | Aktuelle Arbeit |
| ⬜ Open | Noch offen |
| 🧊 Frozen | Zurückgestellt |

---

## SPRINT 1 — Kritische Bugfixes (jetzt)

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| B-01 | 🔴 P1 | 🔧 | Kräfteübersicht lädt nicht | sp() ruft loadKraefte() auf, aber Render-Fehler im Modal |
| B-02 | 🔴 P1 | 🔧 | Tagebucheingabe funktioniert nicht | Modal öffnet nicht korrekt, API-Aufruf fehlerhaft |
| B-03 | 🔴 P1 | 🔧 | Führungsstruktur erscheint in anderen Reitern | abschn-list doppelt referenziert |
| B-04 | 🔴 P1 | 🔧 | Lagevortrag-Tab zeigt Führungsstruktur | Falscher Panel-Inhalt |
| B-05 | 🟡 P2 | ⬜ | Theme-Umschalter (hell/dunkel) | Settings-Button, localStorage |
| B-06 | 🟡 P2 | ⬜ | Neuer Einsatz aus Tab erreichbar | Nicht nur Admin-Panel |

**Akzeptanzkriterien Sprint 1:**
- [ ] Kräfte anlegen, anzeigen, auf Karte platzieren
- [ ] Tagebucheintrag anlegen und anzeigen
- [ ] Jeder Tab zeigt nur seinen eigenen Inhalt
- [ ] Lagevortrag generieren und anzeigen

---

## RELEASE 2 — Karte & Visualisierung

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| K-01 | 🟡 P2 | ⬜ | Kräfte Drag-to-Map | Karte in Kräfteliste als Drop-Target, Marker automatisch setzen |
| K-02 | 🟡 P2 | ⬜ | Erweiterte OSM-Layer | Altenheime, Kitas, Polizei, Supermärkte, Brücken, Wasserwerk |
| K-03 | 🟡 P2 | ⬜ | Taktische Zeichen vollständig | DV 102 vollständiger Satz (aktuell 19/~40) |
| K-04 | 🟡 P2 | ⬜ | Schadenskonten-Formular optimiert | Vollständige DV 102 Schadensstellen-Erfassung |
| K-05 | 🟡 P2 | ⬜ | Dashboard Präsentationsansicht | Ähnlich hormuzstraitmonitor.com: Vollbild-KPI-Dashboard |
| K-06 | 🟡 P2 | ⬜ | Karte drucken / Screenshot | Leaflet-Print-Plugin |
| K-07 | 🟢 P3 | ⬜ | Karte offline-fähig | Tile-Caching für Gebiete ohne Internet |
| K-08 | 🟢 P3 | ⬜ | WMS/WFS-Layer | Eigene Geodaten-Server einbinden |

**Dashboard (K-05) Anforderungen:**
- Vollbild-Ansicht für Lagebesprechungsraum
- Echtzeit-Opferzahlen groß dargestellt
- Lagekarte embedded
- Laufender Lagevortrag-Text
- Countdown nächste Lagebesprechung
- Letzte 5 Tagebucheinträge
- Kräfte-Übersicht numerisch
- Kein Login erforderlich (Beamer-Token)

---

## RELEASE 3 — Führung & Prozesse

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| F-01 | 🟡 P2 | ⬜ | Führungsstrukturbaum visuell | Organigramm-Darstellung EL → Abschnitte → Einheiten |
| F-02 | 🟡 P2 | ⬜ | Einsatzplanung strukturiert | SMEAK-Einsatzbefehl Vorlage |
| F-03 | 🟡 P2 | ⬜ | Intervall-Lagebesprechungsplaner | Automatischer Timer, Benachrichtigung |
| F-04 | 🟡 P2 | ⬜ | Geplante Führungsstruktur | Einpflegen der Soll-Führungsstruktur vor Einsatz |
| F-05 | 🟢 P3 | ⬜ | S-Sachgebiets-Tabs individuell | Jedes S-Sachgebiet hat eigene Ansicht und Checklisten |
| F-06 | 🟢 P3 | ⬜ | Checklisten nach DV 100 | Standardchecklisten je Lagestufe und Sachgebiet |

---

## RELEASE 4 — KI & Lernfunktion

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| A-01 | 🟡 P2 | ✅ | Analyseberichte hochladen | PDF/Text → KI-Zusammenfassung |
| A-02 | 🟡 P2 | ⬜ | Kontextspezifische Empfehlungen | Aus Berichten + aktuellem Einsatz → konkrete Handlungen |
| A-03 | 🟡 P2 | ⬜ | Trainingsdaten-Upload (Strukturiert) | JSON-Format für strukturierte Lerndaten |
| A-04 | 🟢 P3 | ⬜ | KI-Ähnlichkeitssuche | "Welcher Bericht ähnelt unserem aktuellen Einsatz?" |
| A-05 | 🟢 P3 | ⬜ | Automatische Lagefortschreibung | KI erkennt Muster und schlägt Maßnahmen vor |

---

## RELEASE 5 — Integration & Schnittstellen

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| I-01 | 🟢 P3 | ⬜ | E-Mail-Empfang (IMAP) | Meldungen per E-Mail empfangen → automatisch Tagebuch |
| I-02 | 🟢 P3 | ⬜ | E-Mail-Versand | Meldungen direkt verschicken |
| I-03 | 🟢 P3 | ⬜ | PDF-Export Tagebuch | Formatierter PDF-Export statt TXT |
| I-04 | 🟢 P3 | ⬜ | Excel-Export Kräfte | Kräfteübersicht als Excel |
| I-05 | 🟢 P3 | ⬜ | Postgres-Migration | Skalierung auf mehrere Instanzen |
| I-06 | 🟢 P3 | ⬜ | DAVID/ELDIS-Adapter | Anbindung an Leitstellensysteme |

---

## EINSTELLUNGEN & UX

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| U-01 | 🟡 P2 | ⬜ | Hell/Dunkel-Umschalter | CSS-Variablen-Swap, localStorage |
| U-02 | 🟡 P2 | ⬜ | Farbschema wählen | Mindestens: Dunkel (Standard), Hell, Kontrast |
| U-03 | 🟡 P2 | ⬜ | Schriftgröße einstellen | Barrierefreiheit für ältere Nutzer |
| U-04 | 🟢 P3 | ⬜ | Tablet-Optimierung | Bessere Touch-Targets, Stift-Unterstützung |
| U-05 | 🟢 P3 | ⬜ | Mobile-View | Kompakte Ansicht für Smartphone |

---

## SICHERHEIT & ADMINISTRATION

| ID | Prio | Status | Titel | Details |
|----|------|--------|-------|---------|
| S-01 | 🟡 P2 | ⬜ | Passwort-Änderung im UI | Nutzer kann eigenes Passwort ändern |
| S-02 | 🟡 P2 | ⬜ | Nutzer-Verwaltung im Admin | Neue Nutzer anlegen, deaktivieren |
| S-03 | 🟢 P3 | ⬜ | 2FA | TOTP für Admin und EL |
| S-04 | 🟢 P3 | ⬜ | Audit-Log | Alle Admin-Aktionen protokollieren |

---

## PROJECTSTAND (aktuell R1)

### Implementiert ✅
- FastAPI Backend mit SQLite/Postgres-Kompatibilität
- JWT-Auth mit FwDV 100 Rollen (EL, S1-S6, Presse, Admin, Beamer)
- WebSocket Hub für Live-Updates
- Lagekarte mit Leaflet.js
- Taktische Zeichen DV 102 (19 Symbole)
- Freihand-Zeichnen (Leaflet.draw via CDN)
- OSM-Infrastruktur via Overpass (6 Layer)
- Infrastruktur-Objekte pflegbar (Status/Kapazität)
- Hash-gesichertes Einsatztagebuch
- Tagebuch-Export TXT
- Datei-Upload + KI-Analyse (PDF, Word, Text)
- Lagevortrag mit Revisionen + LB-Meilenstein
- Lagebesprechungs-Countdown im Banner
- Meldungen mit KI-Generierung
- Pressestelle S5 mit Bausteinen + Bilder
- Analyseberichte: KI lernt aus Katastrophenberichten
- Übungsszenario hochladen + KI-Extraktion
- Einsatz zurücksetzen (Admin)
- Neuer Einsatz (Admin)
- Beamer-Modus ohne Login

### Bekannte Bugs (Sprint 1 Fix) 🔧
- Kräfte-Tab: Rendern nach Laden fehlerhaft
- Tagebuch-Modal: Eingabe nicht immer funktional
- Führungsstruktur erscheint in falschen Tabs
- Lagevortrag-Tab teils fehlerhafter Inhalt

### Nicht implementiert ⬜
- Drag-to-Map für Kräfte
- Hell-Theme
- Vollbild-Dashboard (Präsentationsmodus)
- Führungsstrukturbaum visuell
- Erweiterte OSM-Layer (Altenheime, Kitas, etc.)
- Passwort-Änderung im UI

---

## USE-CASE KLÄRUNGSBEDARF

Folgende Fragen wären hilfreich für die Priorisierung:

1. **Trainingsdaten-Format**: Sollen Analyseberichte als freie PDF-Texte hochgeladen werden, oder gibt es ein strukturiertes Format (JSON, Excel)?

2. **Führungsstruktur**: Soll die Führungsstruktur vor dem Einsatz einmalig geplant werden (Soll-Struktur) und dann mit Ist-Daten befüllt werden?

3. **Dashboard**: Hormuzstrait-ähnlich — soll das ein separater URL-Zugang sein (wie Beamer), oder ein Tab innerhalb der App?

4. **Neue Einsätze**: Sollen alte Einsätze komplett archiviert und als Lernmaterial genutzt werden? Automatisch oder manuell?

5. **Schadenkonten DV 102**: Welche Schadenkonten-Typen werden am häufigsten gebraucht? Vollständige DV 102 Liste oder die 10 wichtigsten?

6. **Tablet/Stift**: Welches Gerät? iPad + Apple Pencil, Samsung Galaxy Tab, Microsoft Surface?

7. **Hell-Theme**: Soll es helle und dunkle Variante geben, oder nur hell als Standard?

8. **Datenschutz/DSGVO**: Laufen echte Patientendaten durch? Welche Anforderungen an Datenlöschung?

---

*Zuletzt aktualisiert: R1*
