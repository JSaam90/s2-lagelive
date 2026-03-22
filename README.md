# S2-LageLive

**KI-gestütztes Führungsunterstützungssystem für Katastrophenschutz und Großschadenlagen**

Gemäß DV 100 / FwDV 102 · Deployment auf Railway.app · Stand: Release 1

---

## Übersicht

S2-LageLive ist ein webbasiertes Einsatzführungssystem für Führungsstäbe bei Großschadenlagen und Katastrophenschutzeinsätzen. Es kombiniert eine interaktive Lagekarte mit einem strukturierten Einsatztagebuch, KI-gestützter Lageanalyse (via Claude API) und rollenbasierter Zugangskontrolle gemäß DV 100.

### Kernfunktionen

| Bereich | Funktion |
|---------|----------|
| Lagekarte | Taktische Zeichen nach DV 102, Freihand-Zeichnen, OSM-Infrastruktur |
| Tagebuch | Hash-gesichertes Einsatztagebuch mit Export, Datei-Upload + KI-Analyse |
| Führung | Abschnitte, Abschnittsleiter, Ansprechpartner, Führungsstruktur |
| Kräfte | Kräfteerfassung, Drag-to-Map (R2), Statusverfolgung |
| Lagevortrag | KI-Generierung, Revisionen, Lagebesprechungs-Countdown mit Glocke |
| Meldungen | KI-Generierung, Versandverfolgung, Upload Vordrucke |
| Presse S5 | Pressemeldungs-Bausteine, KI-Generierung, Bilder-Upload, Freigabe |
| Analysen | Katastrophenberichte hochladen → KI lernt, macht Empfehlungen |
| Beamer | Öffentliche Großanzeigeansicht ohne Login |
| Admin | Einsätze anlegen/wechseln/zurücksetzen, Nutzerverwaltung |

---

## Schnellstart

### Logins (vor Einsatz ändern!)

| Benutzer | Passwort | Rolle |
|----------|----------|-------|
| el | el123 | Einsatzleitung |
| s1 | s1123 | S1 Personal |
| s2 | s2123 | S2 Lage |
| s3 | s3123 | S3 Einsatz |
| s4 | s4123 | S4 Versorgung |
| s5 | s5123 | S5 Presse |
| s6 | s6123 | S6 Fernmelde |
| presse | presse123 | Pressesprecher |
| admin | admin123 | Administrator |
| beamer | beamer123 | Beamer-Anzeige |
| extern | extern123 | Beobachter |

### Deployment Railway.app

1. Repository auf GitHub pushen
2. Railway → New Project → Deploy from GitHub
3. Environment Variables setzen:
   - `ANTHROPIC_API_KEY` → sk-ant-...
   - `SECRET_KEY` → zufälliger String (32+ Zeichen)
   - `DATABASE_URL` → leer lassen (SQLite Standard)
4. Railway deployt automatisch bei jedem Push

### Lokaler Start

```bash
pip install -r requirements.txt
python main.py
# → http://localhost:8000
```

---

## Architektur

```
s2-lagelive/
├── main.py              # FastAPI Backend (1200 Zeilen)
│   ├── Datenbankmodelle (SQLAlchemy/SQLite)
│   ├── REST API Endpoints
│   ├── WebSocket Hub (Live-Updates)
│   └── KI-Agenten (Anthropic Claude)
├── frontend/
│   └── index.html       # Single-Page-App (1400 Zeilen)
│       ├── Leaflet.js Lagekarte
│       ├── Leaflet.draw Freihand
│       └── Vanilla JS (kein Framework)
├── Dockerfile           # Railway-kompatibel
├── railway.toml         # Health-Check /health
└── requirements.txt
```

### Tech-Stack

- **Backend**: Python 3.11, FastAPI, SQLAlchemy, SQLite (Postgres-kompatibel)
- **Auth**: JWT (python-jose), bcrypt
- **KI**: Anthropic Claude claude-sonnet-4-20250514 via API
- **Karte**: Leaflet.js 1.9.4 + Leaflet.draw 1.0.4, OpenStreetMap, Overpass API
- **Frontend**: Vanilla HTML/CSS/JS (kein Build-Schritt, kein Framework)
- **Deployment**: Docker, Railway.app

---

## API-Übersicht

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | /health | Health-Check |
| POST | /api/auth/token | Login |
| GET | /api/auth/beamer/{token} | Beamer-Login |
| GET/POST | /api/einsaetze | Einsätze |
| POST | /api/einsaetze/{eid}/reset | Einsatz zurücksetzen (Admin) |
| GET/POST | /api/einsaetze/{eid}/karte | Karten-Objekte (TZ) |
| GET/POST | /api/einsaetze/{eid}/kraefte | Kräfte |
| GET/POST | /api/einsaetze/{eid}/abschnitte | Abschnitte |
| GET/POST | /api/einsaetze/{eid}/tagebuch | Tagebuch |
| GET | /api/einsaetze/{eid}/export/tagebuch | Export TXT |
| POST | /api/einsaetze/{eid}/upload | Datei-Upload + KI |
| POST | /api/einsaetze/{eid}/lagevortraege/generieren | LV generieren |
| GET/POST | /api/einsaetze/{eid}/presse | Pressemeldungen |
| POST | /api/einsaetze/{eid}/presse/generieren | Presse KI |
| GET/POST | /api/analyseberichte | Analyseberichte |
| WS | /ws/{eid}?token= | WebSocket Live |

---

## Rollen & Berechtigungen

| Rolle | Lesen | Tagebuch | Karte | Lagevortrag | Presse | Admin |
|-------|-------|----------|-------|-------------|--------|-------|
| admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| el | ✅ | ✅ | ✅ | ✅ Freigabe | ✅ Freigabe | - |
| s2 | ✅ | ✅ | ✅ | ✅ Erstellen | - | - |
| s5/presse | ✅ | lesen | lesen | lesen | ✅ | - |
| s1/s3/s4/s6 | ✅ | ✅ | lesen | lesen | - | - |
| beamer | lesen | - | lesen | lesen | - | - |
| extern | lesen | - | - | - | - | - |

---

## Taktische Zeichen (DV 102)

### Schadensstellen
- Schadensstelle (Kreuz im Kreis)
- Brand (Dreieck, rot)
- Einsturz (Kreuz im Quadrat)
- Überflutung (Wellenlinien)
- MANV (Kreuz im Rechteck)
- Gefahrgut (Dreieck mit !)
- Vermisst (Fragezeichen im Kreis)
- Totenfund (gefüllter Kreis)

### Kräfte & Führung
- FW-Einheit, RD-Einheit, THW
- Führungsstelle (doppelt umrandetes Rechteck)
- BHP (Behandlungsplatz)
- Abschnitt (Raute)

### Sonstiges
- BSR (Bereitstellungsraum)
- Sperrung, Umleitung
- Sammelstelle, LS-Platz (Hubschrauber)
- Notunterkunft

---

## OSM-Infrastruktur-Layer

Alle Layer werden lazy per Overpass API geladen (nur aktueller Kartenausschnitt):

- 🏥 Krankenhäuser
- 🚒 Feuerwachen
- 🚑 Rettungswachen
- 🏫 Schulen / Sammelstellen
- ⛽ Tankstellen
- ⚡ Strom / KRITIS-Infrastruktur

**Geplant (R2):** Altenheime, Kitas, Polizei, Supermärkte, Brücken, Wasserwerke

Jedes OSM-Objekt kann mit Status (verfügbar/eingeschränkt/betroffen) und Kapazitätsdaten versehen werden.

---

## Beamer-Modus

Öffentliche Ansicht ohne Login — ideal für Lagebesprechungsraum-Projektor:

```
https://dein-projekt.up.railway.app/?beamer=TOKEN
```

Der Token steht im Beamer-Tab. Zeigt: Opferzahlen, Lagebeschreibung, freigegebener Lagevortrag.

---

## Datensicherheit

- JWT-Token mit 8h Ablaufzeit
- Einsatztagebuch mit SHA-256 Hash-Kette (Manipulationsschutz)
- Keine Übertragung sensibler Daten an externe Dienste außer Anthropic API
- Alle KI-Ausgaben als ENTWURF gekennzeichnet, Freigabe durch EL erforderlich

---

## Bekannte Einschränkungen (R1)

- SQLite (kein Mehrbenutzer-Write-Lock bei >20 gleichzeitigen Schreibern)
- Kein E-Mail-Versand (Meldungen müssen manuell weitergeleitet werden)
- Bilder-Upload für Presse lokal (kein S3)
- Drag-to-Map für Kräfte noch nicht implementiert (R2)

---

## Changelog

### R1 (aktuell)
- FwDV 100 Rollen und Logins
- Taktische Zeichen DV 102 auf Karte
- Freihand-Zeichnen (Leaflet.draw)
- Typ-spezifische TZ-Formulare
- OSM-Infrastruktur-Layer (6 Typen)
- Infrastruktur-Objekte pflegbar (Status, Kapazität)
- Einsatz zurücksetzen / neuer Einsatz (Admin)
- Tagebuch-Export als TXT
- Upload Vordrucke + KI-Analyse
- Lagebesprechungs-Countdown im Banner
- Pressestelle S5 mit KI-Bausteinen + Bilder
- Analyseberichte: KI lernt aus Katastrophenberichten
- Übungsszenario hochladen

### R0 (Vorgänger)
- Grundlegende Lagekarte, Tagebuch, Lagevortrag, Meldungen

---

*S2-LageLive · DV 100 / FwDV 102 · Anthropic Claude API*
