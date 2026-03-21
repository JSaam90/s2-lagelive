# S2-LageLive

KI-gestütztes Lagedarstellungssystem · FwDV 102 / DV 100

## Projektstruktur

```
s2-lagelive/
├── main.py           ← Gesamte Backend-Logik (FastAPI)
├── frontend/
│   └── index.html    ← Komplettes Dashboard
├── Dockerfile        ← Railway-Build
├── railway.toml      ← Railway-Konfiguration
├── requirements.txt
└── .env.example      ← Konfigurationsvorlage
```

## Railway Deployment (Schritt für Schritt)

### 1. GitHub-Repo anlegen
- https://github.com/new
- Name: `s2-lagelive`
- Public oder Private
- "Create repository"

### 2. Dateien hochladen
Auf der Repo-Seite: "uploading an existing file" → alle Dateien reinziehen → "Commit changes"

Wichtig: Die `frontend/`-Ordnerstruktur muss erhalten bleiben!

### 3. Railway-Projekt erstellen
- https://railway.app → Login with GitHub
- "New Project" → "Deploy from GitHub repo"
- `s2-lagelive` auswählen → "Deploy Now"

### 4. Umgebungsvariablen setzen (PFLICHT)
Im Railway-Dashboard: Service → Tab "Variables"

| Variable | Wert |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `SECRET_KEY` | Langes zufälliges Passwort (min. 32 Zeichen) |

### 5. Domain aktivieren
Service → Settings → Networking → "Generate Domain"

→ `https://dein-projekt.up.railway.app`

---

## Lokale Entwicklung

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# .env öffnen und API-Key eintragen
python main.py
# → http://localhost:8000
```

---

## Standard-Zugänge

| Nutzer | Passwort | Rolle |
|---|---|---|
| `admin` | `admin123` | Vollzugriff |
| `s2` | `s2pass` | S2 Lage |
| `el` | `elpass` | Einsatzleiter |
| `buerger` | `buerger` | Bürgermeister |
| `presse` | `presse` | Presse |
| `extern` | `extern` | Beobachter |

**Passwörter vor echtem Einsatz ändern!**

---

## API-Dokumentation

Nach dem Start: `https://dein-projekt.up.railway.app/docs`

---

## Rechtlicher Hinweis

Alle KI-generierten Inhalte sind Vorschläge und erfordern die Freigabe durch
verantwortliches Personal (DV 100 § 7). Das System ersetzt keine Führungsentscheidung.
