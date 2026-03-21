"""
S2-LageLive · Railway-Deployment · Kompaktversion
Alle Komponenten in einer Datei für einfaches Deployment
"""
import asyncio, json, os, hashlib, re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional, Dict, List
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, create_engine, event
from sqlalchemy.orm import DeclarativeBase, relationship, Session
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ── Config ──
SECRET_KEY   = os.getenv("SECRET_KEY", "s2-lagelive-dev-key-bitte-in-env-aendern-32chars")
ALGORITHM    = "HS256"
TOKEN_EXP    = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/s2.db")
API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")
PORT         = int(os.getenv("PORT", "8000"))
HOST         = os.getenv("HOST", "0.0.0.0")

for d in ["data","exports","uploads","frontend"]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════
# DATENBANK
# ══════════════════════════════════════════
class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True)
    username     = Column(String(64), unique=True, nullable=False)
    hashed_pw    = Column(String(256), nullable=False)
    display_name = Column(String(128))
    role         = Column(String(32), default="extern")
    active       = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

class Einsatz(Base):
    __tablename__ = "einsaetze"
    id           = Column(Integer, primary_key=True)
    kennung      = Column(String(64))
    stichwort    = Column(String(128))
    einsatzort   = Column(Text)
    alarmzeit    = Column(DateTime, default=datetime.utcnow)
    lagestufe    = Column(String(64), default="Erstmeldung")
    aktiv        = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    created_by   = Column(Integer, nullable=True)
    tote         = Column(Integer, default=0)
    verletzte    = Column(Integer, default=0)
    verschuettete= Column(Integer, default=0)
    obdachlose   = Column(Integer, default=0)
    kraefte      = Column(Text, default="[]")

class TagebuchEintrag(Base):
    """Append-Only Einsatztagebuch mit SHA-256 Hash-Chain (rechtssicher)."""
    __tablename__ = "tagebuch"
    id           = Column(Integer, primary_key=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"), nullable=False)
    author_name  = Column(String(128))
    author_role  = Column(String(32))
    eingang_dt   = Column(DateTime, default=datetime.utcnow)
    ereignis_dt  = Column(DateTime, nullable=True)
    kategorie    = Column(String(64))
    prioritaet   = Column(String(16), default="normal")
    betreff      = Column(String(256))
    inhalt       = Column(Text, nullable=False)
    quelle       = Column(String(128))
    vordruck_nr  = Column(String(64), nullable=True)
    vordruck_von = Column(String(128), nullable=True)
    vordruck_an  = Column(String(128), nullable=True)
    ki_analyse   = Column(Text, nullable=True)
    prev_hash    = Column(String(64), default="GENESIS")
    entry_hash   = Column(String(64), unique=True, nullable=True)
    freigegeben  = Column(Boolean, default=False)
    freigabe_von = Column(String(128), nullable=True)
    freigabe_dt  = Column(DateTime, nullable=True)

    def berechne_hash(self) -> str:
        payload = json.dumps({
            "id": self.id, "einsatz_id": self.einsatz_id,
            "author": self.author_name, "eingang": str(self.eingang_dt),
            "kategorie": self.kategorie, "betreff": self.betreff,
            "inhalt": self.inhalt, "prev_hash": self.prev_hash,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def verifiziere_chain(self, vorheriger=None) -> bool:
        expected = vorheriger.entry_hash if vorheriger else "GENESIS"
        if self.prev_hash != expected: return False
        return self.entry_hash == self.berechne_hash()

class KartenZeichen(Base):
    __tablename__ = "karten_zeichen"
    id           = Column(Integer, primary_key=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"))
    tz_typ       = Column(String(128))
    farbe        = Column(String(16), default="#C8000A")
    emoji        = Column(String(8), default="📍")
    lat          = Column(Float, nullable=False)
    lng          = Column(Float, nullable=False)
    beschreibung = Column(Text, nullable=True)
    erstellt_von = Column(String(128))
    erstellt_dt  = Column(DateTime, default=datetime.utcnow)
    aktiv        = Column(Boolean, default=True)

def get_engine():
    db_url = DATABASE_URL
    if "sqlite" in db_url:
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        @event.listens_for(engine, "connect")
        def set_pragma(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    else:
        engine = create_engine(db_url)
    return engine

engine = get_engine()
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2  = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

def init_db():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        if s.query(User).count() == 0:
            users = [
                User(username="admin",   hashed_pw=pwd_ctx.hash("admin123"),   display_name="Administrator",           role="admin"),
                User(username="s2",      hashed_pw=pwd_ctx.hash("s2pass"),      display_name="S2 – Sachgebiet Lage",    role="s2"),
                User(username="el",      hashed_pw=pwd_ctx.hash("elpass"),      display_name="Einsatzleiter",           role="s1"),
                User(username="buerger", hashed_pw=pwd_ctx.hash("buerger"),     display_name="Bürgermeister",           role="buergermeister"),
                User(username="presse",  hashed_pw=pwd_ctx.hash("presse"),      display_name="Pressestelle",            role="presse"),
                User(username="extern",  hashed_pw=pwd_ctx.hash("extern"),      display_name="Externer Beobachter",     role="extern"),
            ]
            s.add_all(users); s.commit()
            print("✅ Standard-Nutzer angelegt")
        if s.query(Einsatz).count() == 0:
            e = Einsatz(kennung=f"E-{datetime.now().strftime('%Y%m%d')}",
                        stichwort="Bereitschaft", einsatzort="–")
            s.add(e); s.commit()

def get_db():
    with Session(engine) as s: yield s

# ══════════════════════════════════════════
# WEBSOCKET HUB
# ══════════════════════════════════════════
class Hub:
    def __init__(self):
        self._conns: Dict[int, List[tuple]] = {}

    async def connect(self, ws: WebSocket, eid: int, uid: str):
        await ws.accept()
        self._conns.setdefault(eid, []).append((ws, uid))
        await ws.send_json({"type":"connected","einsatz":eid,"user":uid,
                            "clients":len(self._conns[eid]),"zeit":datetime.now().strftime("%H:%M")})

    def disconnect(self, ws, eid):
        self._conns[eid] = [(w,u) for w,u in self._conns.get(eid,[]) if w!=ws]

    async def broadcast(self, eid: int, msg: dict, exclude=None):
        msg["_ts"] = datetime.now().isoformat()
        payload = json.dumps(msg, ensure_ascii=False, default=str)
        dead = []
        for ws, uid in self._conns.get(eid, []):
            if ws == exclude: continue
            try: await ws.send_text(payload)
            except: dead.append((ws, uid))
        if dead:
            self._conns[eid] = [(w,u) for w,u in self._conns.get(eid,[]) if (w,u) not in dead]

hub = Hub()

# ══════════════════════════════════════════
# KI-AGENT (Claude API)
# ══════════════════════════════════════════
async def ki_lage_analyse(eingaben: dict) -> dict:
    if not API_KEY:
        return {"raw": "[KI nicht verfügbar – ANTHROPIC_API_KEY fehlt]",
                "lagebeschreibung": "[API-Key fehlt – Lageeingabe gespeichert]"}

    outputs = eingaben.get("outputs", ["Aktuelle Lagebeschreibung"])
    now = datetime.now().strftime("%d.%m.%Y %H:%M Uhr")

    prompt = f"""LAGEINFORMATION STAND {now}:

Stichwort: {eingaben.get('stichwort','–')}
Einsatzort: {eingaben.get('einsatzort','–')}
Lagestufe: {eingaben.get('lagestufe','–')}
Taktische Zeichen (FwDV 102): {eingaben.get('taktischeZeichen','–')}

SCHADENSLAGE:
{eingaben.get('schadenslage','–')}

GEFAHRENLAGE:
{eingaben.get('gefahrenlage','–')}

OPFER: Tote={eingaben.get('tote','–')} | Verletzte={eingaben.get('verletzte','–')} | Verschüttete={eingaben.get('verschuettete','–')} | Obdachlose={eingaben.get('obdachlose','–')}

KRÄFTE:
{eingaben.get('kraefte','–')}

GEO / SPERRUNGEN:
{eingaben.get('geoLage','–')}

WETTER: {eingaben.get('temperatur','–')}

GEWÜNSCHTE AUSGABEN:
{chr(10).join('- '+o for o in outputs)}

Erstelle alle gewünschten Produkte. Trenne sie mit === PRODUKTNAME === als Überschrift.
Jedes Produkt mit "Stand: {now}" versehen.
Keine Markdown-Sternchen. Sachlich, DV 100 konform.
{eingaben.get('hinweise','')}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=3000,
            system="Du bist KI-Unterstützung S2 gemäß DV 100 / FwDV 102. Alle Ausgaben sind Vorschläge.",
            messages=[{"role":"user","content":prompt}]
        )
        text = msg.content[0].text
    except Exception as e:
        text = f"KI-Fehler: {e}\n\nLageeingabe gespeichert:\n{prompt[:500]}"

    result = {"raw": text, "stand": now}
    for section in ["LAGEBESCHREIBUNG","KURZLAGE","MANAGEMENT SUMMARY",
                    "PRESSEMITTEILUNG","BÜRGERINFORMATION","OFFENE PUNKTE","LAGEFORTSCHREIBUNG"]:
        m = re.search(rf"={2,}[^=]*{section}[^=]*={2,}([\s\S]*?)(?:={2,}|$)", text, re.IGNORECASE)
        if m:
            key = section.lower().replace(" ","_").replace("ü","ue")
            result[key] = m.group(1).strip()

    # Geodaten extrahieren
    result["geodaten"] = await extrahiere_geo(
        f"{eingaben.get('schadenslage','')} {eingaben.get('geoLage','')}", eingaben.get('koordinaten',''))
    return result

async def extrahiere_geo(text: str, koor: str) -> list:
    orte = []
    # Bekannte Orte mit Koordinaten
    bekannte = {
        "offenburg": (48.4732, 7.9414, "🔴", "#C8000A"),
        "kehl":      (48.5745, 7.8119, "🔴", "#C8000A"),
        "lahr":      (48.3413, 7.8684, "🔴", "#C8000A"),
        "achern":    (48.5540, 8.0781, "🟡", "#E07B00"),
        "rheinau":   (48.4180, 7.7990, "🌊", "#1A5FA8"),
        "gambsheim": (48.6180, 7.8820, "🌊", "#1A5FA8"),
        "freistätt": (48.5400, 7.8600, "🌊", "#1A5FA8"),
    }
    for name, (lat, lng, emoji, farbe) in bekannte.items():
        if name in text.lower():
            orte.append({"tz_typ":name.capitalize(),"farbe":farbe,"emoji":emoji,"lat":lat,"lng":lng,
                        "beschreibung":f"Erkannt in Lageeingabe"})
    # Koordinaten direkt aus Eingabe
    if koor:
        m = re.search(r"([\d.]+)[°\s]*N[,\s]+([\d.]+)[°\s]*E", koor, re.IGNORECASE)
        if m:
            orte.insert(0, {"tz_typ":"Einsatzschwerpunkt","farbe":"#C8000A","emoji":"🎯",
                            "lat":float(m.group(1)),"lng":float(m.group(2)),"beschreibung":"Koordinaten aus Eingabe"})
    return orte[:8]  # max 8 Marker

# ══════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print(f"🚒 S2-LageLive gestartet · {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print(f"   Dashboard: http://localhost:{PORT}")
    print(f"   API-Docs:  http://localhost:{PORT}/docs")
    yield

app = FastAPI(title="S2-LageLive", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Frontend ausliefern
if Path("frontend").exists():
    app.mount("/static", StaticFiles(directory="frontend"), name="static")

# ── AUTH ──
def create_token(data: dict):
    exp = datetime.utcnow() + timedelta(minutes=TOKEN_EXP)
    return jwt.encode({**data,"exp":exp}, SECRET_KEY, algorithm=ALGORITHM)

def get_user(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    exc = HTTPException(401, "Nicht autorisiert", headers={"WWW-Authenticate":"Bearer"})
    try:
        p = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        u = db.query(User).filter(User.username==p.get("sub")).first()
        if not u or not u.active: raise exc
        return u
    except JWTError: raise exc

def require(*roles):
    def dep(u: User = Depends(get_user)):
        if u.role not in roles and u.role != "admin":
            raise HTTPException(403, "Keine Berechtigung")
        return u
    return dep

@app.post("/api/auth/token")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.username==form.username).first()
    if not u or not pwd_ctx.verify(form.password, u.hashed_pw):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    token = create_token({"sub":u.username,"role":u.role})
    return {"access_token":token,"token_type":"bearer",
            "user":{"username":u.username,"role":u.role,"display_name":u.display_name}}

# ── WEBSOCKET ──
@app.websocket("/ws/{eid}")
async def ws_endpoint(ws: WebSocket, eid: int, token: Optional[str] = None):
    uid = "anonym"
    if token:
        try:
            p = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = p.get("sub","anonym")
        except: pass
    await hub.connect(ws, eid, uid)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "karten_zeichen":
                    await hub.broadcast(eid, msg, exclude=ws)
            except: pass
    except WebSocketDisconnect:
        hub.disconnect(ws, eid)

# ── EINSATZ ──
@app.get("/api/einsaetze")
async def list_einsaetze(db=Depends(get_db), u=Depends(get_user)):
    rows = db.query(Einsatz).order_by(Einsatz.id.desc()).all()
    return [{"id":e.id,"kennung":e.kennung,"stichwort":e.stichwort,
             "einsatzort":e.einsatzort,"lagestufe":e.lagestufe,"aktiv":e.aktiv,
             "tote":e.tote,"verletzte":e.verletzte,"verschuettete":e.verschuettete,"obdachlose":e.obdachlose} for e in rows]

@app.post("/api/einsaetze")
async def create_einsatz(data: dict, db=Depends(get_db), u=Depends(require("s1","s2","admin"))):
    e = Einsatz(kennung=data.get("kennung",f"E-{datetime.now().strftime('%Y%m%d-%H%M')}"),
                stichwort=data.get("stichwort","–"), einsatzort=data.get("einsatzort","–"),
                lagestufe=data.get("lagestufe","Erstmeldung"), created_by=u.id)
    db.add(e); db.commit(); db.refresh(e)
    await hub.broadcast(e.id,{"type":"einsatz_erstellt","einsatz_id":e.id,"kennung":e.kennung})
    return {"id":e.id,"kennung":e.kennung}

@app.get("/api/einsaetze/{eid}")
async def get_einsatz(eid: int, db=Depends(get_db), u=Depends(get_user)):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    return {"id":e.id,"kennung":e.kennung,"stichwort":e.stichwort,"einsatzort":e.einsatzort,
            "lagestufe":e.lagestufe,"tote":e.tote,"verletzte":e.verletzte,
            "verschuettete":e.verschuettete,"obdachlose":e.obdachlose}

@app.patch("/api/einsaetze/{eid}")
async def update_einsatz(eid: int, data: dict, db=Depends(get_db), u=Depends(require("s1","s2","admin"))):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    for f in ["lagestufe","tote","verletzte","verschuettete","obdachlose"]:
        if f in data: setattr(e, f, data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"einsatz_update","einsatz_id":eid,**data})
    return {"ok":True}

# ── TAGEBUCH ──
@app.get("/api/einsaetze/{eid}/tagebuch")
async def get_tagebuch(eid: int, db=Depends(get_db), u=Depends(get_user)):
    rows = db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id).all()
    return [{"id":e.id,"einsatz_id":e.einsatz_id,"author_name":e.author_name,"author_role":e.author_role,
             "eingang_dt":e.eingang_dt.isoformat() if e.eingang_dt else None,
             "kategorie":e.kategorie,"prioritaet":e.prioritaet,"betreff":e.betreff,
             "inhalt":e.inhalt,"quelle":e.quelle,"vordruck_nr":e.vordruck_nr,
             "vordruck_von":e.vordruck_von,"vordruck_an":e.vordruck_an,
             "entry_hash":e.entry_hash,"prev_hash":e.prev_hash,"freigegeben":e.freigegeben,
             "freigabe_von":e.freigabe_von} for e in rows]

@app.post("/api/einsaetze/{eid}/tagebuch")
async def add_tagebuch(eid: int, data: dict, db=Depends(get_db), u=Depends(get_user)):
    letzter = db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid)\
                .order_by(TagebuchEintrag.id.desc()).first()
    prev_hash = letzter.entry_hash if (letzter and letzter.entry_hash) else "GENESIS"

    e = TagebuchEintrag(
        einsatz_id=eid, author_name=u.display_name or u.username, author_role=u.role,
        eingang_dt=datetime.utcnow(), kategorie=data.get("kategorie","Meldung"),
        prioritaet=data.get("prioritaet","normal"), betreff=data.get("betreff",""),
        inhalt=data.get("inhalt",""), quelle=data.get("quelle","Manuell"),
        vordruck_nr=data.get("vordruck_nr"), vordruck_von=data.get("vordruck_von"),
        vordruck_an=data.get("vordruck_an"), ki_analyse=data.get("ki_analyse"),
        prev_hash=prev_hash,
    )
    db.add(e); db.flush()
    e.entry_hash = e.berechne_hash()
    db.commit(); db.refresh(e)

    await hub.broadcast(eid,{
        "type":"tagebuch_eintrag","einsatz_id":eid,"id":e.id,
        "kategorie":e.kategorie,"prioritaet":e.prioritaet,"betreff":e.betreff,
        "inhalt":e.inhalt[:300],"author":e.author_name,"quelle":e.quelle,
        "zeit":e.eingang_dt.strftime("%d.%m. %H:%M"),
        "hash":e.entry_hash[:12]+"…" if e.entry_hash else "–",
        "entry_hash":e.entry_hash,"prev_hash":e.prev_hash,"freigegeben":False,
        "author_name":e.author_name,"author_role":e.author_role,
        "eingang_dt":e.eingang_dt.isoformat(),
    })
    return {"id":e.id,"entry_hash":e.entry_hash}

@app.get("/api/einsaetze/{eid}/tagebuch/verify")
async def verify_chain(eid: int, db=Depends(get_db), u=Depends(require("s1","s2","admin"))):
    rows = db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id).all()
    fehler = []
    for i, e in enumerate(rows):
        prev = rows[i-1] if i > 0 else None
        if not e.verifiziere_chain(prev):
            fehler.append({"id":e.id,"betreff":e.betreff})
    return {"valid":len(fehler)==0,"eintraege":len(rows),"fehler":fehler}

@app.post("/api/einsaetze/{eid}/tagebuch/{tid}/freigeben")
async def freigeben(eid: int, tid: int, db=Depends(get_db), u=Depends(require("s1","s2","admin"))):
    e = db.query(TagebuchEintrag).filter(TagebuchEintrag.id==tid, TagebuchEintrag.einsatz_id==eid).first()
    if not e: raise HTTPException(404)
    e.freigegeben=True; e.freigabe_von=u.display_name; e.freigabe_dt=datetime.utcnow()
    db.commit()
    return {"ok":True}

@app.get("/api/einsaetze/{eid}/export/tagebuch.pdf")
async def export_pdf(eid: int, db=Depends(get_db), u=Depends(require("s1","s2","admin"))):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    rows = db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id).all()
    # Einfaches Text-Export als Fallback
    path = f"exports/tagebuch_{eid}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    lines=["="*70,f"EINSATZTAGEBUCH · {e.kennung}",f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}","="*70,""]
    for r in rows:
        lines+=[f"#{r.id:04d} | {r.eingang_dt.strftime('%d.%m.%Y %H:%M') if r.eingang_dt else '–'} | {r.kategorie}",
                f"Betreff: {r.betreff}",f"Inhalt: {r.inhalt}",
                f"Hash: {r.entry_hash}","─"*50,""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return FileResponse(path, media_type="text/plain", filename=f"Tagebuch_{e.kennung}.txt")

# ── KARTE ──
@app.get("/api/einsaetze/{eid}/karte")
async def get_karte(eid: int, db=Depends(get_db), u=Depends(get_user)):
    rows = db.query(KartenZeichen).filter(KartenZeichen.einsatz_id==eid, KartenZeichen.aktiv==True).all()
    return [{"id":k.id,"tz_typ":k.tz_typ,"farbe":k.farbe,"emoji":k.emoji,
             "lat":k.lat,"lng":k.lng,"beschreibung":k.beschreibung,
             "erstellt_von":k.erstellt_von,"erstellt_dt":k.erstellt_dt.isoformat() if k.erstellt_dt else None} for k in rows]

@app.post("/api/einsaetze/{eid}/karte")
async def add_karte(eid: int, data: dict, db=Depends(get_db), u=Depends(get_user)):
    kz = KartenZeichen(einsatz_id=eid, tz_typ=data.get("tz_typ","Marker"),
                       farbe=data.get("farbe","#C8000A"), emoji=data.get("emoji","📍"),
                       lat=data["lat"], lng=data["lng"],
                       beschreibung=data.get("beschreibung"), erstellt_von=u.display_name or u.username)
    db.add(kz); db.commit(); db.refresh(kz)
    await hub.broadcast(eid,{"type":"karten_zeichen","id":kz.id,"tz_typ":kz.tz_typ,
                              "farbe":kz.farbe,"emoji":kz.emoji,"lat":kz.lat,"lng":kz.lng,
                              "beschreibung":kz.beschreibung,"erstellt_von":kz.erstellt_von,
                              "zeit":datetime.now().strftime("%H:%M")})
    return {"id":kz.id}

@app.delete("/api/einsaetze/{eid}/karte/{kid}")
async def del_karte(eid: int, kid: int, db=Depends(get_db), u=Depends(require("s2","admin"))):
    kz = db.get(KartenZeichen, kid)
    if not kz: raise HTTPException(404)
    kz.aktiv=False; db.commit()
    await hub.broadcast(eid,{"type":"karten_zeichen_delete","id":kid})
    return {"ok":True}

# ── KI ──
@app.post("/api/einsaetze/{eid}/ki/analyse")
async def ki_analyse(eid: int, data: dict, db=Depends(get_db), u=Depends(require("s2","admin"))):
    result = await ki_lage_analyse(data)

    # Auto-Tagebucheintrag
    if result.get("lagebeschreibung") or result.get("raw"):
        letzter = db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid)\
                    .order_by(TagebuchEintrag.id.desc()).first()
        prev_hash = letzter.entry_hash if (letzter and letzter.entry_hash) else "GENESIS"
        inhalt = result.get("lagebeschreibung") or result.get("raw","–")
        e = TagebuchEintrag(einsatz_id=eid, author_name="KI-System S2", author_role="system",
                            kategorie="Lage", prioritaet="hoch",
                            betreff=f"KI-Lagebeschreibung {datetime.now().strftime('%H:%M Uhr')}",
                            inhalt=inhalt[:2000], quelle="KI-Analyse", prev_hash=prev_hash)
        db.add(e); db.flush(); e.entry_hash=e.berechne_hash(); db.commit()

    # Kartenpunkte aus Geodaten
    for g in result.get("geodaten",[]):
        if not (g.get("lat") and g.get("lng")): continue
        kz = KartenZeichen(einsatz_id=eid, tz_typ=g.get("tz_typ","Marker"),
                           farbe=g.get("farbe","#C8000A"), emoji=g.get("emoji","📍"),
                           lat=g["lat"], lng=g["lng"],
                           beschreibung=g.get("beschreibung"), erstellt_von="KI-Agent")
        db.add(kz); db.flush()
        await hub.broadcast(eid,{"type":"karten_zeichen","id":kz.id,**g,
                                  "zeit":datetime.now().strftime("%H:%M")})
    db.commit()

    await hub.broadcast(eid,{"type":"ki_analyse_fertig",**result})
    return result

# ── UPLOAD ──
@app.post("/api/einsaetze/{eid}/upload")
async def upload(eid: int, file: UploadFile = File(...), db=Depends(get_db), u=Depends(get_user)):
    content = await file.read()
    path = f"uploads/{eid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    with open(path,"wb") as f: f.write(content)
    await hub.broadcast(eid,{"type":"datei_eingang","datei":file.filename,
                              "ki_kurz":"Datei hochgeladen, Verarbeitung läuft","zeit":datetime.now().strftime("%H:%M")})
    return {"datei":file.filename,"path":path}

# ── HEALTH + FRONTEND ──
@app.get("/health")
async def health():
    return {"status":"ok","system":"S2-LageLive","version":"1.0.0","time":datetime.now().isoformat()}

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

@app.get("/{path:path}")
async def catch_all(path: str):
    fp = f"frontend/{path}"
    if os.path.exists(fp): return FileResponse(fp)
    return FileResponse("frontend/index.html")

# ── START ──
if __name__ == "__main__":
    import uvicorn
    print(f"🚒 S2-LageLive startet auf http://{HOST}:{PORT}")
    uvicorn.run("main:app", host=HOST, port=PORT,
                reload=os.getenv("DEBUG","false").lower()=="true", log_level="info")
