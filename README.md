# S2-LageLive · Railway Deployment

## Schnellstart lokal
```bash
pip install -r requirements.txt
cp .env.example .env
# .env öffnen: ANTHROPIC_API_KEY eintragen
python main.py
# → http://localhost:8000
```

## Standard-Zugänge
| User | Passwort | Rolle |
|------|----------|-------|
| admin | admin123 | Vollzugriff |
| s2 | s2pass | S2 Lage |
| el | elpass | Einsatzleiter |
| buerger | buerger | Bürgermeister |
| presse | presse | Presse |

**Passwörter vor Einsatz ändern!**

## Railway Deployment
1. Diesen Ordner als GitHub-Repo hochladen
2. railway.app → New Project → GitHub verbinden
3. Umgebungsvariablen setzen (ANTHROPIC_API_KEY, SECRET_KEY)
4. Deploy → fertig
