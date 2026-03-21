"""
S2-LageLive · Vollständige Anwendung
Railway-kompatibel: PORT aus Umgebungsvariable, Health-Check auf /health
"""
import os
import json
import hashlib
import asyncio
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, List

# ── Verzeichnisse beim Import anlegen ──────────────────────────
for _d in ["data", "exports", "uploads", "frontend"]:
    Path(_d).mkdir(parents=True, exist_ok=True)

# ── Dotenv (optional, ignorieren wenn nicht vorhanden) ─────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── FastAPI ────────────────────────────────────────────────────
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, UploadFile, File, Form
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

# ── Auth ───────────────────────────────────────────────────────
from jose import JWTError, jwt
from passlib.context import CryptContext

# ── Datenbank ──────────────────────────────────────────────────
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,
    Boolean, Float, ForeignKey, create_engine, event, text
)
from sqlalchemy.orm import DeclarativeBase, Session

# ── Konfiguration ──────────────────────────────────────────────
SECRET_KEY   = os.environ.get("SECRET_KEY", "s2-lagelive-dev-key-32-zeichen-lang-!")
ALGORITHM    = "HS256"
TOKEN_EXPIRE = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/s2.db")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Railway setzt PORT automatisch - das ist der kritische Teil
PORT = int(os.environ.get("PORT", "8000"))

# ==============================================================
# DATENBANK MODELLE
# ==============================================================

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True, index=True)
    username     = Column(String(64), unique=True, nullable=False, index=True)
    hashed_pw    = Column(String(256), nullable=False)
    display_name = Column(String(128), default="")
    role         = Column(String(32), default="extern")
    active       = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)


class Einsatz(Base):
    __tablename__ = "einsaetze"
    id            = Column(Integer, primary_key=True, index=True)
    kennung       = Column(String(64), default="")
    stichwort     = Column(String(128), default="")
    einsatzort    = Column(Text, default="")
    alarmzeit     = Column(DateTime, default=datetime.utcnow)
    lagestufe     = Column(String(64), default="Erstmeldung")
    aktiv         = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    tote          = Column(Integer, default=0)
    verletzte     = Column(Integer, default=0)
    verschuettete = Column(Integer, default=0)
    obdachlose    = Column(Integer, default=0)
    kraefte_json  = Column(Text, default="[]")


class TagebuchEintrag(Base):
    """Append-Only Einsatztagebuch mit SHA-256 Hash-Chain."""
    __tablename__ = "tagebuch"
    id           = Column(Integer, primary_key=True, index=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"), nullable=False, index=True)
    author_name  = Column(String(128), default="")
    author_role  = Column(String(32), default="")
    eingang_dt   = Column(DateTime, default=datetime.utcnow)
    kategorie    = Column(String(64), default="Meldung")
    prioritaet   = Column(String(16), default="normal")
    betreff      = Column(String(256), default="")
    inhalt       = Column(Text, nullable=False, default="")
    quelle       = Column(String(128), default="Manuell")
    vordruck_nr  = Column(String(64), nullable=True)
    vordruck_von = Column(String(128), nullable=True)
    vordruck_an  = Column(String(128), nullable=True)
    prev_hash    = Column(String(64), default="GENESIS")
    entry_hash   = Column(String(64), nullable=True, unique=True)
    freigegeben  = Column(Boolean, default=False)
    freigabe_von = Column(String(128), nullable=True)

    def berechne_hash(self) -> str:
        payload = json.dumps({
            "id":        self.id,
            "eid":       self.einsatz_id,
            "author":    self.author_name,
            "dt":        str(self.eingang_dt),
            "kategorie": self.kategorie,
            "betreff":   self.betreff,
            "inhalt":    (self.inhalt or "")[:500],
            "prev":      self.prev_hash,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class KartenZeichen(Base):
    __tablename__ = "karten_zeichen"
    id           = Column(Integer, primary_key=True, index=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"), index=True)
    tz_typ       = Column(String(128), default="Marker")
    farbe        = Column(String(16), default="#C8000A")
    emoji        = Column(String(8), default="📍")
    lat          = Column(Float, nullable=False)
    lng          = Column(Float, nullable=False)
    beschreibung = Column(Text, default="")
    erstellt_von = Column(String(128), default="")
    erstellt_dt  = Column(DateTime, default=datetime.utcnow)
    aktiv        = Column(Boolean, default=True)


class KiAnalyse(Base):
    __tablename__ = "ki_analysen"
    id          = Column(Integer, primary_key=True, index=True)
    einsatz_id  = Column(Integer, ForeignKey("einsaetze.id"), index=True)
    typ         = Column(String(64), default="lage")
    eingabe_hash= Column(String(64), default="")
    ausgabe     = Column(Text, default="")
    erstellt_dt = Column(DateTime, default=datetime.utcnow)


# ==============================================================
# DATENBANK SETUP
# ==============================================================

def create_db_engine():
    url = DATABASE_URL
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
        )
        @event.listens_for(engine, "connect")
        def on_connect(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    else:
        engine = create_engine(url)
    return engine


engine = create_db_engine()
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def get_db():
    with Session(engine) as session:
        yield session


def init_db():
    """Erstellt alle Tabellen und Standard-Nutzer."""
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        if s.query(User).count() == 0:
            users = [
                User(username="admin",   hashed_pw=pwd_ctx.hash("admin123"),
                     display_name="Administrator", role="admin"),
                User(username="s2",      hashed_pw=pwd_ctx.hash("s2pass"),
                     display_name="S2 – Lage", role="s2"),
                User(username="el",      hashed_pw=pwd_ctx.hash("elpass"),
                     display_name="Einsatzleiter", role="s1"),
                User(username="buerger", hashed_pw=pwd_ctx.hash("buerger"),
                     display_name="Bürgermeister", role="buergermeister"),
                User(username="presse",  hashed_pw=pwd_ctx.hash("presse"),
                     display_name="Pressestelle", role="presse"),
                User(username="extern",  hashed_pw=pwd_ctx.hash("extern"),
                     display_name="Beobachter", role="extern"),
            ]
            s.add_all(users)
            s.commit()
            print("✅ Standard-Nutzer angelegt")

        if s.query(Einsatz).count() == 0:
            e = Einsatz(
                kennung=f"E-{datetime.now().strftime('%Y%m%d')}",
                stichwort="Bereitschaft",
                einsatzort="–",
            )
            s.add(e)
            s.commit()
            print("✅ Standard-Einsatz angelegt")


# ==============================================================
# WEBSOCKET HUB
# ==============================================================

class WebSocketHub:
    def __init__(self):
        self._connections: Dict[int, List[tuple]] = {}

    async def connect(self, ws: WebSocket, eid: int, uid: str):
        await ws.accept()
        self._connections.setdefault(eid, []).append((ws, uid))
        try:
            await ws.send_json({
                "type": "connected",
                "einsatz": eid,
                "user": uid,
                "clients": len(self._connections[eid]),
                "zeit": datetime.now().strftime("%H:%M"),
            })
        except Exception:
            pass

    def disconnect(self, ws: WebSocket, eid: int):
        conns = self._connections.get(eid, [])
        self._connections[eid] = [(w, u) for w, u in conns if w is not ws]

    async def broadcast(self, eid: int, message: dict, exclude: WebSocket = None):
        message.setdefault("_ts", datetime.now().isoformat())
        payload = json.dumps(message, ensure_ascii=False, default=str)
        dead = []
        for ws, uid in list(self._connections.get(eid, [])):
            if ws is exclude:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append((ws, uid))
        if dead:
            conns = self._connections.get(eid, [])
            self._connections[eid] = [(w, u) for w, u in conns if (w, u) not in dead]


hub = WebSocketHub()


# ==============================================================
# KI-AGENT
# ==============================================================

async def ki_analyse(eingaben: dict) -> dict:
    """Ruft Claude API auf. Gibt strukturiertes Ergebnis zurück."""
    if not ANTHROPIC_KEY:
        return {
            "raw": "⚠ ANTHROPIC_API_KEY nicht gesetzt. Bitte in Railway-Variablen eintragen.",
            "lagebeschreibung": f"Lageeingabe gespeichert. Stand: {datetime.now().strftime('%d.%m.%Y %H:%M Uhr')}\n\n"
                                f"Einsatzort: {eingaben.get('einsatzort','–')}\n"
                                f"Stichwort: {eingaben.get('stichwort','–')}\n"
                                f"Schadenslage: {eingaben.get('schadenslage','–')}",
        }

    try:
        import anthropic
    except ImportError:
        return {"raw": "anthropic-Paket nicht installiert.", "lagebeschreibung": "–"}

    now = datetime.now().strftime("%d.%m.%Y %H:%M Uhr")
    outputs = eingaben.get("outputs", ["Aktuelle Lagebeschreibung"])

    prompt = f"""LAGEINFORMATION STAND {now}:

Stichwort:    {eingaben.get('stichwort', '–')}
Einsatzort:   {eingaben.get('einsatzort', '–')}
Lagestufe:    {eingaben.get('lagestufe', '–')}
Taktische Zeichen (FwDV 102): {eingaben.get('taktischeZeichen', '–')}

SCHADENSLAGE:
{eingaben.get('schadenslage', '–')}

GEFAHRENLAGE:
{eingaben.get('gefahrenlage', '–')}

OPFER: Tote={eingaben.get('tote', '–')} | Verletzte={eingaben.get('verletzte', '–')} | Verschüttete={eingaben.get('verschuettete', '–')} | Obdachlose={eingaben.get('obdachlose', '–')}

KRÄFTE:
{eingaben.get('kraefte', '–')}

GEO / SPERRUNGEN:
{eingaben.get('geoLage', '–')}

KOORDINATEN: {eingaben.get('koordinaten', '–')}
WETTER: {eingaben.get('temperatur', '–')}

GEWÜNSCHTE AUSGABEN:
{chr(10).join('- ' + o for o in outputs)}

Erstelle alle gewünschten Produkte. Trenne sie mit === PRODUKTNAME === als Überschrift.
Stand {now} bei jedem Produkt vermerken.
Keine Markdown-Sternchen. Sachlich, DV 100 konform.
{eingaben.get('hinweise', '')}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system="Du bist KI-Unterstützung S2 gemäß DV 100 / FwDV 102. "
                   "Alle Ausgaben sind Vorschläge und erfordern Freigabe durch verantwortliches Personal.",
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text
    except Exception as e:
        text = f"KI-Fehler: {e}"

    # Sektionen parsen
    result: dict = {"raw": text, "stand": now}
    for section in [
        "LAGEBESCHREIBUNG", "AKTUELLE LAGEBESCHREIBUNG",
        "KURZLAGE", "MANAGEMENT SUMMARY",
        "PRESSEMITTEILUNG", "BÜRGERINFORMATION",
        "FÜHRUNGSRHYTHMUS", "OFFENE PUNKTE", "LAGEFORTSCHREIBUNG",
    ]:
        m = re.search(
            rf"={2,}[^=]*{re.escape(section)}[^=]*={2,}([\s\S]*?)(?:={2,}|$)",
            text, re.IGNORECASE
        )
        if m:
            key = section.lower().replace(" ", "_").replace("ü", "ue").replace("ä", "ae")
            result[key] = m.group(1).strip()

    # Geodaten extrahieren
    result["geodaten"] = _extrahiere_geodaten(
        eingaben.get("schadenslage", "") + " " +
        eingaben.get("geoLage", "") + " " +
        eingaben.get("koordinaten", "")
    )
    return result


def _extrahiere_geodaten(text: str) -> list:
    """Erkennt bekannte Orte im Ortenaukreis und gibt Koordinaten zurück."""
    orte = []
    bekannte = {
        "offenburg":  (48.4732, 7.9414, "🏛", "#FFD100", "Führungsstab / Offenburg"),
        "kehl":       (48.5745, 7.8119, "🔴", "#C8000A", "Schadensstelle Kehl"),
        "lahr":       (48.3413, 7.8684, "🔴", "#C8000A", "Schadensstelle Lahr"),
        "achern":     (48.5540, 8.0781, "🟡", "#E07B00", "TEL Nord / Achern"),
        "rheinau":    (48.4180, 7.7990, "🌊", "#1A5FA8", "Überflutung Rheinau"),
        "gambsheim":  (48.6180, 7.8820, "🌊", "#1A5FA8", "Dammbruch Gambsheim"),
        "freistätt":  (48.5400, 7.8600, "🌊", "#1A5FA8", "Überflutung Freistätt"),
        "oberkirch":  (48.5333, 8.2167, "🏥", "#1A8A3C", "Kreiskrankenhaus Oberkirch"),
        "wolfach":    (48.2953, 8.2869, "🔴", "#C8000A", "Schadensstelle Wolfach"),
        "ettenheim":  (48.2667, 7.7833, "🏥", "#1A8A3C", "Kreiskrankenhaus Ettenheim"),
    }
    text_lower = text.lower()
    for name, (lat, lng, emoji, farbe, label) in bekannte.items():
        if name in text_lower:
            orte.append({
                "tz_typ": label, "farbe": farbe, "emoji": emoji,
                "lat": lat, "lng": lng, "beschreibung": label,
            })

    # Direkte Koordinaten aus Text
    m = re.search(r"([\d.]+)[°\s]*N[,\s]+([\d.]+)[°\s]*E", text, re.IGNORECASE)
    if m:
        try:
            lat, lng = float(m.group(1)), float(m.group(2))
            if 47 < lat < 50 and 6 < lng < 10:
                orte.insert(0, {
                    "tz_typ": "Einsatzschwerpunkt", "farbe": "#C8000A",
                    "emoji": "🎯", "lat": lat, "lng": lng,
                    "beschreibung": "Koordinaten aus Lageeingabe",
                })
        except ValueError:
            pass

    return orte[:8]


async def ki_ingest_dokument(text: str, dateiname: str, einsatz_kontext: dict) -> dict:
    """Analysiert ein eingehende Dokument/Datei."""
    if not ANTHROPIC_KEY:
        return {
            "kategorie": "Information", "prioritaet": "normal",
            "betreff": f"Dokument: {dateiname}",
            "inhalt": text[:500],
            "ki_zusammenfassung": "KI nicht verfügbar – API-Key fehlt.",
            "geodaten": [],
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        now = datetime.now().strftime("%d.%m.%Y %H:%M Uhr")

        prompt = f"""Analysiere dieses Dokument für das Einsatztagebuch S2.
Dokument: {dateiname}
Stand: {now}

EINSATZKONTEXT:
{json.dumps(einsatz_kontext, ensure_ascii=False)}

DOKUMENTINHALT:
{text[:3000]}

Antworte NUR mit validem JSON (kein Markdown, keine Erklärungen):
{{
  "kategorie": "Lage|Maßnahme|Meldung|Vordruck|Information",
  "prioritaet": "kritisch|hoch|normal",
  "betreff": "Kurzer Betreff max 80 Zeichen",
  "inhalt": "Strukturierte Zusammenfassung des Dokuments",
  "ki_zusammenfassung": "2-3 Sätze was dieses Dokument bedeutet",
  "geodaten": []
}}"""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system="Du bist S2-Assistent. Antworte ausschließlich mit validem JSON.",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception as e:
        return {
            "kategorie": "Information", "prioritaet": "normal",
            "betreff": f"Dokument: {dateiname}",
            "inhalt": text[:500],
            "ki_zusammenfassung": f"Analyse fehlgeschlagen: {e}",
            "geodaten": [],
        }


# ==============================================================
# AUTH HELPERS
# ==============================================================

def create_access_token(data: dict) -> str:
    exp = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE)
    return jwt.encode({**data, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    exc = HTTPException(
        status_code=401,
        detail="Nicht autorisiert",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise exc
    except JWTError:
        raise exc
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.active:
        raise exc
    return user


def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user
    return dependency


# ==============================================================
# APP
# ==============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print(f"🚒 S2-LageLive gestartet · Port {PORT} · {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    yield
    print("🔴 S2-LageLive beendet")


app = FastAPI(
    title="S2-LageLive",
    description="KI-gestütztes Lagedarstellungssystem · FwDV 102 / DV 100",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Frontend-Dateien ausliefern
if Path("frontend").exists() and any(Path("frontend").iterdir()):
    app.mount("/static", StaticFiles(directory="frontend"), name="static")


# ==============================================================
# HEALTH CHECK — muss als erstes registriert werden
# ==============================================================

@app.get("/health")
async def health_check():
    """Railway Health Check — immer 200 zurückgeben."""
    return {
        "status": "ok",
        "system": "S2-LageLive",
        "version": "2.0.0",
        "time": datetime.now().isoformat(),
        "port": PORT,
    }


# ==============================================================
# WEBSOCKET
# ==============================================================

@app.websocket("/ws/{eid}")
async def websocket_endpoint(
    ws: WebSocket,
    eid: int,
    token: Optional[str] = None,
):
    uid = "anonym"
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub", "anonym")
        except Exception:
            pass

    await hub.connect(ws, eid, uid)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "karten_zeichen":
                    await hub.broadcast(eid, msg, exclude=ws)
            except Exception:
                pass
    except WebSocketDisconnect:
        hub.disconnect(ws, eid)


# ==============================================================
# AUTH ROUTEN
# ==============================================================

@app.post("/api/auth/token")
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not pwd_ctx.verify(form.password, user.hashed_pw):
        raise HTTPException(status_code=401, detail="Benutzername oder Passwort falsch")

    token = create_access_token({"sub": user.username, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
        },
    }


@app.get("/api/auth/me")
async def get_me(user: User = Depends(get_current_user)):
    return {
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name,
    }


# ==============================================================
# EINSATZ ROUTEN
# ==============================================================

@app.get("/api/einsaetze")
async def list_einsaetze(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.query(Einsatz).order_by(Einsatz.id.desc()).all()
    return [
        {
            "id": e.id, "kennung": e.kennung, "stichwort": e.stichwort,
            "einsatzort": e.einsatzort, "lagestufe": e.lagestufe, "aktiv": e.aktiv,
            "tote": e.tote, "verletzte": e.verletzte,
            "verschuettete": e.verschuettete, "obdachlose": e.obdachlose,
            "alarmzeit": e.alarmzeit.isoformat() if e.alarmzeit else None,
        }
        for e in rows
    ]


@app.post("/api/einsaetze")
async def create_einsatz(
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s1", "s2")),
):
    e = Einsatz(
        kennung=data.get("kennung", f"E-{datetime.now().strftime('%Y%m%d-%H%M')}"),
        stichwort=data.get("stichwort", "Neuer Einsatz"),
        einsatzort=data.get("einsatzort", "–"),
        lagestufe=data.get("lagestufe", "Erstmeldung"),
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    await hub.broadcast(e.id, {
        "type": "einsatz_erstellt",
        "einsatz_id": e.id,
        "kennung": e.kennung,
    })
    return {"id": e.id, "kennung": e.kennung}


@app.get("/api/einsaetze/{eid}")
async def get_einsatz(
    eid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    e = db.get(Einsatz, eid)
    if not e:
        raise HTTPException(404, "Einsatz nicht gefunden")
    return {
        "id": e.id, "kennung": e.kennung, "stichwort": e.stichwort,
        "einsatzort": e.einsatzort, "lagestufe": e.lagestufe, "aktiv": e.aktiv,
        "tote": e.tote, "verletzte": e.verletzte,
        "verschuettete": e.verschuettete, "obdachlose": e.obdachlose,
        "alarmzeit": e.alarmzeit.isoformat() if e.alarmzeit else None,
    }


@app.patch("/api/einsaetze/{eid}")
async def update_einsatz(
    eid: int,
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s1", "s2")),
):
    e = db.get(Einsatz, eid)
    if not e:
        raise HTTPException(404)
    for field in ["lagestufe", "tote", "verletzte", "verschuettete", "obdachlose", "stichwort", "einsatzort"]:
        if field in data:
            setattr(e, field, data[field])
    db.commit()
    await hub.broadcast(eid, {"type": "einsatz_update", "einsatz_id": eid, **data})
    return {"ok": True}


# ==============================================================
# TAGEBUCH ROUTEN
# ==============================================================

def _get_prev_hash(db: Session, eid: int) -> str:
    letzter = (
        db.query(TagebuchEintrag)
        .filter(TagebuchEintrag.einsatz_id == eid)
        .order_by(TagebuchEintrag.id.desc())
        .first()
    )
    if letzter and letzter.entry_hash:
        return letzter.entry_hash
    return "GENESIS"


@app.get("/api/einsaetze/{eid}/tagebuch")
async def get_tagebuch(
    eid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(TagebuchEintrag)
        .filter(TagebuchEintrag.einsatz_id == eid)
        .order_by(TagebuchEintrag.id.asc())
        .all()
    )
    return [
        {
            "id": r.id, "einsatz_id": r.einsatz_id,
            "author_name": r.author_name, "author_role": r.author_role,
            "eingang_dt": r.eingang_dt.isoformat() if r.eingang_dt else None,
            "kategorie": r.kategorie, "prioritaet": r.prioritaet,
            "betreff": r.betreff, "inhalt": r.inhalt, "quelle": r.quelle,
            "vordruck_nr": r.vordruck_nr, "vordruck_von": r.vordruck_von,
            "vordruck_an": r.vordruck_an,
            "entry_hash": r.entry_hash, "prev_hash": r.prev_hash,
            "freigegeben": r.freigegeben, "freigabe_von": r.freigabe_von,
        }
        for r in rows
    ]


@app.post("/api/einsaetze/{eid}/tagebuch")
async def add_tagebuch(
    eid: int,
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    prev_hash = _get_prev_hash(db, eid)
    entry = TagebuchEintrag(
        einsatz_id=eid,
        author_name=user.display_name or user.username,
        author_role=user.role,
        eingang_dt=datetime.utcnow(),
        kategorie=data.get("kategorie", "Meldung"),
        prioritaet=data.get("prioritaet", "normal"),
        betreff=data.get("betreff", ""),
        inhalt=data.get("inhalt", ""),
        quelle=data.get("quelle", "Manuell"),
        vordruck_nr=data.get("vordruck_nr"),
        vordruck_von=data.get("vordruck_von"),
        vordruck_an=data.get("vordruck_an"),
        prev_hash=prev_hash,
    )
    db.add(entry)
    db.flush()
    entry.entry_hash = entry.berechne_hash()
    db.commit()
    db.refresh(entry)

    msg = {
        "type": "tagebuch_eintrag",
        "einsatz_id": eid,
        "id": entry.id,
        "kategorie": entry.kategorie,
        "prioritaet": entry.prioritaet,
        "betreff": entry.betreff,
        "inhalt": entry.inhalt[:300],
        "author": entry.author_name,
        "author_name": entry.author_name,
        "author_role": entry.author_role,
        "quelle": entry.quelle,
        "zeit": entry.eingang_dt.strftime("%d.%m. %H:%M"),
        "hash": (entry.entry_hash or "")[:12] + "…",
        "entry_hash": entry.entry_hash,
        "prev_hash": entry.prev_hash,
        "freigegeben": False,
        "eingang_dt": entry.eingang_dt.isoformat(),
        "vordruck_nr": entry.vordruck_nr,
        "vordruck_von": entry.vordruck_von,
        "vordruck_an": entry.vordruck_an,
    }
    await hub.broadcast(eid, msg)
    return {"id": entry.id, "entry_hash": entry.entry_hash}


@app.get("/api/einsaetze/{eid}/tagebuch/verify")
async def verify_chain(
    eid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s1", "s2")),
):
    rows = (
        db.query(TagebuchEintrag)
        .filter(TagebuchEintrag.einsatz_id == eid)
        .order_by(TagebuchEintrag.id.asc())
        .all()
    )
    fehler = []
    for i, r in enumerate(rows):
        expected_prev = rows[i - 1].entry_hash if i > 0 else "GENESIS"
        if r.prev_hash != expected_prev:
            fehler.append({"id": r.id, "betreff": r.betreff, "fehler": "prev_hash falsch"})
        elif r.entry_hash != r.berechne_hash():
            fehler.append({"id": r.id, "betreff": r.betreff, "fehler": "hash stimmt nicht"})
    return {"valid": len(fehler) == 0, "eintraege": len(rows), "fehler": fehler}


@app.post("/api/einsaetze/{eid}/tagebuch/{tid}/freigeben")
async def freigeben(
    eid: int,
    tid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s1", "s2")),
):
    entry = (
        db.query(TagebuchEintrag)
        .filter(TagebuchEintrag.id == tid, TagebuchEintrag.einsatz_id == eid)
        .first()
    )
    if not entry:
        raise HTTPException(404)
    entry.freigegeben = True
    entry.freigabe_von = user.display_name
    db.commit()
    return {"ok": True}


@app.get("/api/einsaetze/{eid}/export/tagebuch")
async def export_tagebuch(
    eid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s1", "s2")),
):
    e = db.get(Einsatz, eid)
    if not e:
        raise HTTPException(404)
    rows = (
        db.query(TagebuchEintrag)
        .filter(TagebuchEintrag.einsatz_id == eid)
        .order_by(TagebuchEintrag.id.asc())
        .all()
    )
    lines = [
        "=" * 70,
        f"EINSATZTAGEBUCH · {e.kennung}",
        f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"Einträge: {len(rows)}",
        "=" * 70, "",
    ]
    for r in rows:
        dt = r.eingang_dt.strftime("%d.%m.%Y %H:%M") if r.eingang_dt else "–"
        lines += [
            f"#{r.id:04d} | {dt} | {r.kategorie.upper()} | {r.prioritaet.upper()}",
            f"Betreff: {r.betreff}",
            f"Autor:   {r.author_name} ({r.author_role})",
            f"Quelle:  {r.quelle}",
            "",
            r.inhalt or "–",
            "",
            f"HASH: {r.entry_hash or '–'}",
            f"PREV: {r.prev_hash or '–'}",
            "─" * 50, "",
        ]
    path = f"exports/tagebuch_{eid}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return FileResponse(path, media_type="text/plain",
                        filename=f"Tagebuch_{e.kennung}.txt")


# ==============================================================
# KARTE ROUTEN
# ==============================================================

@app.get("/api/einsaetze/{eid}/karte")
async def get_karte(
    eid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(KartenZeichen)
        .filter(KartenZeichen.einsatz_id == eid, KartenZeichen.aktiv == True)
        .all()
    )
    return [
        {
            "id": k.id, "tz_typ": k.tz_typ, "farbe": k.farbe, "emoji": k.emoji,
            "lat": k.lat, "lng": k.lng, "beschreibung": k.beschreibung,
            "erstellt_von": k.erstellt_von,
            "erstellt_dt": k.erstellt_dt.isoformat() if k.erstellt_dt else None,
        }
        for k in rows
    ]


@app.post("/api/einsaetze/{eid}/karte")
async def add_karte(
    eid: int,
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kz = KartenZeichen(
        einsatz_id=eid,
        tz_typ=data.get("tz_typ", "Marker"),
        farbe=data.get("farbe", "#C8000A"),
        emoji=data.get("emoji", "📍"),
        lat=data["lat"],
        lng=data["lng"],
        beschreibung=data.get("beschreibung", ""),
        erstellt_von=user.display_name or user.username,
    )
    db.add(kz)
    db.commit()
    db.refresh(kz)
    await hub.broadcast(eid, {
        "type": "karten_zeichen",
        "id": kz.id, "tz_typ": kz.tz_typ,
        "farbe": kz.farbe, "emoji": kz.emoji,
        "lat": kz.lat, "lng": kz.lng,
        "beschreibung": kz.beschreibung,
        "erstellt_von": kz.erstellt_von,
        "zeit": datetime.now().strftime("%H:%M"),
    })
    return {"id": kz.id}


@app.delete("/api/einsaetze/{eid}/karte/{kid}")
async def delete_karte(
    eid: int,
    kid: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s2")),
):
    kz = db.get(KartenZeichen, kid)
    if not kz:
        raise HTTPException(404)
    kz.aktiv = False
    db.commit()
    await hub.broadcast(eid, {"type": "karten_zeichen_delete", "id": kid})
    return {"ok": True}


# ==============================================================
# KI-ANALYSE ROUTEN
# ==============================================================

@app.post("/api/einsaetze/{eid}/ki/analyse")
async def ki_analyse_route(
    eid: int,
    data: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("s2")),
):
    result = await ki_analyse(data)

    # Tagebucheintrag anlegen
    prev_hash = _get_prev_hash(db, eid)
    inhalt = (
        result.get("lagebeschreibung")
        or result.get("aktuelle_lagebeschreibung")
        or result.get("raw", "–")
    )
    entry = TagebuchEintrag(
        einsatz_id=eid,
        author_name="KI-System S2",
        author_role="system",
        kategorie="Lage",
        prioritaet="hoch",
        betreff=f"KI-Analyse · {datetime.now().strftime('%H:%M Uhr')}",
        inhalt=inhalt[:2000],
        quelle="KI-Analyse",
        prev_hash=prev_hash,
    )
    db.add(entry)
    db.flush()
    entry.entry_hash = entry.berechne_hash()

    # Zahlen aktualisieren
    e = db.get(Einsatz, eid)
    if e:
        for field in ["tote", "verletzte", "verschuettete", "obdachlose"]:
            val = data.get(field)
            if val:
                try:
                    setattr(e, field, int(val))
                except (ValueError, TypeError):
                    pass
        if data.get("lagestufe"):
            e.lagestufe = data["lagestufe"]

    db.commit()

    # Geodaten auf Karte
    for geo in result.get("geodaten", []):
        if not (geo.get("lat") and geo.get("lng")):
            continue
        kz = KartenZeichen(
            einsatz_id=eid,
            tz_typ=geo.get("tz_typ", "Marker"),
            farbe=geo.get("farbe", "#C8000A"),
            emoji=geo.get("emoji", "📍"),
            lat=geo["lat"],
            lng=geo["lng"],
            beschreibung=geo.get("beschreibung", ""),
            erstellt_von="KI-Agent",
        )
        db.add(kz)
        db.flush()
        await hub.broadcast(eid, {
            "type": "karten_zeichen",
            "id": kz.id, **geo,
            "zeit": datetime.now().strftime("%H:%M"),
        })

    db.commit()

    # Einsatz-Update broadcasten
    if e:
        await hub.broadcast(eid, {
            "type": "einsatz_update",
            "einsatz_id": eid,
            "tote": e.tote, "verletzte": e.verletzte,
            "verschuettete": e.verschuettete, "obdachlose": e.obdachlose,
            "lagestufe": e.lagestufe,
        })

    await hub.broadcast(eid, {"type": "ki_analyse_fertig", **result})
    return result


# ==============================================================
# DATEI-UPLOAD UND INGEST
# ==============================================================

@app.post("/api/einsaetze/{eid}/upload")
async def upload_file(
    eid: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = await file.read()
    safe_name = re.sub(r"[^\w\.\-]", "_", file.filename or "datei")
    path = f"uploads/{eid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    Path(path).write_bytes(content)

    # Text extrahieren
    text = _extrahiere_datei_text(path, content, file.filename or "")

    # Einsatz-Kontext für KI
    e = db.get(Einsatz, eid)
    einsatz_kontext = {
        "stichwort": e.stichwort if e else "–",
        "einsatzort": e.einsatzort if e else "–",
        "lagestufe": e.lagestufe if e else "–",
    }

    # KI-Analyse asynchron starten
    asyncio.create_task(
        _verarbeite_upload(eid, file.filename or safe_name, text, einsatz_kontext, db, user)
    )

    await hub.broadcast(eid, {
        "type": "datei_eingang",
        "datei": file.filename,
        "ki_kurz": "Datei empfangen, Analyse läuft…",
        "zeit": datetime.now().strftime("%H:%M"),
    })

    return {"datei": file.filename, "status": "wird verarbeitet"}


def _extrahiere_datei_text(path: str, content: bytes, filename: str) -> str:
    """Text aus verschiedenen Dateiformaten extrahieren."""
    suffix = Path(filename).suffix.lower()

    if suffix in (".txt", ".md", ".rst", ".csv"):
        try:
            return content.decode("utf-8", errors="replace")[:6000]
        except Exception:
            return ""

    if suffix == ".pdf":
        try:
            import PyPDF2
            import io
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            return "\n\n".join(
                p.extract_text() or "" for p in reader.pages
            )[:6000]
        except ImportError:
            return f"[PDF: {filename} – PyPDF2 nicht installiert]"
        except Exception as e:
            return f"[PDF-Fehler: {e}]"

    if suffix in (".docx",):
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            import io
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                xml_data = z.read("word/document.xml")
            tree = ET.fromstring(xml_data)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            texts = [t.text or "" for t in tree.findall(".//w:t", ns)]
            return " ".join(texts)[:6000]
        except Exception as e:
            return f"[Word-Fehler: {e}]"

    if suffix == ".json":
        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
            return json.dumps(data, ensure_ascii=False, indent=2)[:4000]
        except Exception:
            return content.decode("utf-8", errors="replace")[:3000]

    return content.decode("utf-8", errors="replace")[:3000]


async def _verarbeite_upload(
    eid: int, filename: str, text: str,
    einsatz_kontext: dict, db: Session, user
):
    """Asynchrone Verarbeitung nach Upload."""
    try:
        # Neue DB-Session für async Task
        with Session(engine) as s:
            analyse = await ki_ingest_dokument(text, filename, einsatz_kontext)

            prev_hash = _get_prev_hash(s, eid)
            entry = TagebuchEintrag(
                einsatz_id=eid,
                author_name=f"Ingest: {filename}",
                author_role="system",
                kategorie=analyse.get("kategorie", "Information"),
                prioritaet=analyse.get("prioritaet", "normal"),
                betreff=analyse.get("betreff", f"Dokument: {filename}"),
                inhalt=analyse.get("inhalt", text[:1000]),
                quelle=f"Upload: {filename}",
                prev_hash=prev_hash,
            )
            s.add(entry)
            s.flush()
            entry.entry_hash = entry.berechne_hash()
            s.commit()

        await hub.broadcast(eid, {
            "type": "tagebuch_eintrag",
            "einsatz_id": eid,
            "id": entry.id,
            "kategorie": entry.kategorie,
            "prioritaet": entry.prioritaet,
            "betreff": entry.betreff,
            "inhalt": entry.inhalt[:300],
            "author": entry.author_name,
            "author_name": entry.author_name,
            "author_role": "system",
            "quelle": entry.quelle,
            "zeit": datetime.now().strftime("%d.%m. %H:%M"),
            "hash": (entry.entry_hash or "")[:12] + "…",
            "entry_hash": entry.entry_hash,
            "prev_hash": entry.prev_hash,
            "freigegeben": False,
            "eingang_dt": datetime.now().isoformat(),
        })
        await hub.broadcast(eid, {
            "type": "datei_eingang",
            "datei": filename,
            "ki_kurz": analyse.get("ki_zusammenfassung", "Verarbeitet"),
            "kategorie": analyse.get("kategorie", "Information"),
            "prioritaet": analyse.get("prioritaet", "normal"),
            "zeit": datetime.now().strftime("%H:%M"),
        })
    except Exception as e:
        print(f"Upload-Verarbeitung fehlgeschlagen: {e}")


# ==============================================================
# FRONTEND + FALLBACK
# ==============================================================

@app.get("/")
async def root():
    index = Path("frontend/index.html")
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"status": "S2-LageLive läuft", "docs": "/docs"})


@app.get("/{path:path}")
async def catch_all(path: str):
    # Statische Dateien
    fp = Path("frontend") / path
    if fp.exists() and fp.is_file():
        return FileResponse(str(fp))
    # SPA-Fallback
    index = Path("frontend/index.html")
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(404)


# ==============================================================
# START
# ==============================================================

if __name__ == "__main__":
    import uvicorn
    print(f"🚒 S2-LageLive · Port {PORT}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
