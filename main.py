"""
S2-LageLive v4.0
Neue Features:
- Einsatz mit Einsatzdaten anlegen
- Kräfte anlegen und auf Karte platzieren
- Schadenskonten (Schadensstellen) mit Karte
- Betroffene Bereiche (Polygon/Kreis)
- Opferzahlen strukturiert
- Ansprechpartner je Abschnitt
- Führungsstrukturbaum
- Lagevortrag-Generierung mit Revisionen + Timer
- Automatische Meldungen
- Beamer-Modus mit eigenem Login-Token
- Alle Infrastruktur-Layer aus OSM/Overpass
"""
import os, json, hashlib, asyncio, re, io, zipfile
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, List

for _d in ["data","exports","uploads","frontend"]:
    Path(_d).mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import (FastAPI, WebSocket, WebSocketDisconnect,
                     Depends, HTTPException, UploadFile, File, BackgroundTasks)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy import (Column, Integer, String, Text, DateTime,
                        Boolean, Float, ForeignKey, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Session

import bcrypt as _bcrypt
def hash_pw(pw): return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()
def verify_pw(pw, h): return _bcrypt.checkpw(pw.encode(), h.encode())

SECRET_KEY    = os.environ.get("SECRET_KEY","s2-v4-dev-key-bitte-in-env-setzen-32chars!")
ALGORITHM     = "HS256"
TOKEN_MIN     = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES","480"))
DATABASE_URL  = os.environ.get("DATABASE_URL","sqlite:///./data/s2.db")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY","")
PORT          = int(os.environ.get("PORT","8000"))
print(f"[BOOT] S2-LageLive v4.0 | PORT={PORT} | KI={'ja' if ANTHROPIC_KEY else 'nein'}")

# ════════════════════════════════════════════════════════════════
# DATENBANK MODELLE
# ════════════════════════════════════════════════════════════════
class Base(DeclarativeBase): pass

class User(Base):
    __tablename__="users"
    id           = Column(Integer,primary_key=True)
    username     = Column(String(64),unique=True,nullable=False)
    hashed_pw    = Column(String(256),nullable=False)
    display_name = Column(String(128),default="")
    role         = Column(String(32),default="extern")
    active       = Column(Boolean,default=True)

class Einsatz(Base):
    __tablename__="einsaetze"
    id            = Column(Integer,primary_key=True)
    kennung       = Column(String(64),default="")
    stichwort     = Column(String(128),default="")
    einsatzort    = Column(Text,default="")
    alarmzeit     = Column(DateTime,default=datetime.utcnow)
    lagestufe     = Column(String(64),default="Erstmeldung")
    aktiv         = Column(Boolean,default=True)
    created_at    = Column(DateTime,default=datetime.utcnow)
    # Opferzahlen
    tote          = Column(Integer,default=0)
    verletzte     = Column(Integer,default=0)
    verschuettete = Column(Integer,default=0)
    obdachlose    = Column(Integer,default=0)
    vermisste     = Column(Integer,default=0)
    gerettete     = Column(Integer,default=0)
    # Einsatzdaten
    einsatzleiter = Column(String(128),default="")
    fuehrstelle   = Column(Text,default="")
    alarmierung   = Column(Text,default="")
    # Geo-Schwerpunkt
    lat           = Column(Float,nullable=True)
    lng           = Column(Float,nullable=True)
    zoom          = Column(Integer,default=12)
    # Beamer-Token (eigener Zugang ohne Login)
    beamer_token  = Column(String(64),nullable=True)

class Abschnitt(Base):
    __tablename__="abschnitte"
    id             = Column(Integer,primary_key=True)
    einsatz_id     = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    nummer         = Column(String(16),default="A1")
    bezeichnung    = Column(String(128),default="")
    aufgabe        = Column(Text,default="")
    status         = Column(String(32),default="aktiv")
    abschnittsleiter_name  = Column(String(128),default="")
    abschnittsleiter_funk  = Column(String(64),default="")
    abschnittsleiter_tel   = Column(String(64),default="")
    staerke        = Column(Integer,default=0)
    lat            = Column(Float,nullable=True)
    lng            = Column(Float,nullable=True)
    parent_id      = Column(Integer,ForeignKey("abschnitte.id"),nullable=True)

class Kraft(Base):
    """Einzelne Einheit / Kräfteansatz"""
    __tablename__="kraefte"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    abschnitt_id = Column(Integer,ForeignKey("abschnitte.id"),nullable=True)
    organisation = Column(String(64),default="FW")
    einheit      = Column(String(128),default="")
    funktion     = Column(String(128),default="")
    staerke      = Column(Integer,default=0)
    fahrzeuge    = Column(String(128),default="")
    status       = Column(String(32),default="im_einsatz")
    ansprechpartner = Column(String(128),default="")
    funk         = Column(String(64),default="")
    lat          = Column(Float,nullable=True)
    lng          = Column(Float,nullable=True)
    erstellt_dt  = Column(DateTime,default=datetime.utcnow)

class Schadensstelle(Base):
    """Schadenskonten auf der Karte"""
    __tablename__="schadensstellen"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    abschnitt_id = Column(Integer,ForeignKey("abschnitte.id"),nullable=True)
    typ          = Column(String(64),default="Schadensstelle")
    bezeichnung  = Column(String(128),default="")
    beschreibung = Column(Text,default="")
    schwere      = Column(String(16),default="mittel")  # leicht/mittel/schwer/kritisch
    status       = Column(String(32),default="aktiv")
    lat          = Column(Float,nullable=False)
    lng          = Column(Float,nullable=False)
    radius_m     = Column(Integer,default=0)  # 0 = Punkt, >0 = Kreis
    erstellt_von = Column(String(128),default="")
    erstellt_dt  = Column(DateTime,default=datetime.utcnow)

class BetroffenerBereich(Base):
    __tablename__="betroffene_bereiche"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    bezeichnung  = Column(String(128),default="")
    typ          = Column(String(64),default="Überflutung")
    beschreibung = Column(Text,default="")
    farbe        = Column(String(16),default="#1A5FA8")
    geo_json     = Column(Text,default="")  # GeoJSON Polygon/Circle
    erstellt_dt  = Column(DateTime,default=datetime.utcnow)

class KartenZeichen(Base):
    __tablename__="karten_zeichen"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"))
    tz_typ       = Column(String(128),default="Marker")
    kategorie    = Column(String(32),default="allgemein")
    farbe        = Column(String(16),default="#C8000A")
    emoji        = Column(String(8),default="📍")
    lat          = Column(Float,nullable=False)
    lng          = Column(Float,nullable=False)
    beschreibung = Column(Text,default="")
    erstellt_von = Column(String(128),default="")
    erstellt_dt  = Column(DateTime,default=datetime.utcnow)
    aktiv        = Column(Boolean,default=True)

class TagebuchEintrag(Base):
    __tablename__="tagebuch"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    author_name  = Column(String(128),default="")
    author_role  = Column(String(32),default="")
    eingang_dt   = Column(DateTime,default=datetime.utcnow)
    kategorie    = Column(String(64),default="Meldung")
    prioritaet   = Column(String(16),default="normal")
    betreff      = Column(String(256),default="")
    inhalt       = Column(Text,default="")
    quelle       = Column(String(128),default="Manuell")
    an           = Column(String(256),default="")
    prev_hash    = Column(String(64),default="GENESIS")
    entry_hash   = Column(String(64),nullable=True)
    freigegeben  = Column(Boolean,default=False)

    def berechne_hash(self):
        d=json.dumps({"id":self.id,"eid":self.einsatz_id,"author":self.author_name,
                      "dt":str(self.eingang_dt),"betreff":self.betreff,
                      "inhalt":(self.inhalt or "")[:300],"prev":self.prev_hash},
                     ensure_ascii=False,sort_keys=True)
        return hashlib.sha256(d.encode()).hexdigest()

class Lagevortrag(Base):
    __tablename__="lagevortraege"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    revision     = Column(Integer,default=1)
    erstellt_dt  = Column(DateTime,default=datetime.utcnow)
    erstellt_von = Column(String(128),default="")
    inhalt       = Column(Text,default="")
    freigegeben  = Column(Boolean,default=False)
    naechster_dt = Column(DateTime,nullable=True)
    intervall_min= Column(Integer,default=60)

class Meldung(Base):
    """Automatisierte Meldungen an verschiedene Empfänger"""
    __tablename__="meldungen"
    id           = Column(Integer,primary_key=True)
    einsatz_id   = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    typ          = Column(String(64),default="Lagemeldung")
    empfaenger   = Column(String(256),default="")
    inhalt       = Column(Text,default="")
    ki_generiert = Column(Boolean,default=False)
    versendet    = Column(Boolean,default=False)
    versendet_dt = Column(DateTime,nullable=True)
    erstellt_dt  = Column(DateTime,default=datetime.utcnow)

def _engine():
    if DATABASE_URL.startswith("sqlite"):
        eng=create_engine(DATABASE_URL,connect_args={"check_same_thread":False})
        @event.listens_for(eng,"connect")
        def _p(c,_): c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON")
        return eng
    return create_engine(DATABASE_URL)

engine=_engine()
oauth2=OAuth2PasswordBearer(tokenUrl="/api/auth/token")

def get_db():
    with Session(engine) as s: yield s

def init_db():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        if s.query(User).count()==0:
            for u,pw,name,role in [
                ("admin","admin123","Administrator","admin"),
                ("s2","s2pass","S2 – Sachgebiet Lage","s2"),
                ("el","elpass","Einsatzleiter","s1"),
                ("buerger","buerger","Bürgermeister","buergermeister"),
                ("presse","presse","Pressestelle","presse"),
                ("extern","extern","Beobachter","extern"),
            ]:
                s.add(User(username=u,hashed_pw=hash_pw(pw),display_name=name,role=role))
            s.commit(); print("[DB] Nutzer angelegt")
        if s.query(Einsatz).count()==0:
            import secrets
            s.add(Einsatz(kennung=f"E-{datetime.now():%Y%m%d}",stichwort="Bereitschaft",
                          einsatzort="–",lat=48.4732,lng=7.9414,
                          beamer_token=secrets.token_urlsafe(12)))
            s.commit(); print("[DB] Standard-Einsatz angelegt")

def _prev_hash(db,eid):
    last=(db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid)
            .order_by(TagebuchEintrag.id.desc()).first())
    return last.entry_hash if (last and last.entry_hash) else "GENESIS"

def _einsatz_dict(e):
    return {"id":e.id,"kennung":e.kennung,"stichwort":e.stichwort,"einsatzort":e.einsatzort,
            "lagestufe":e.lagestufe,"aktiv":e.aktiv,"tote":e.tote,"verletzte":e.verletzte,
            "verschuettete":e.verschuettete,"obdachlose":e.obdachlose,
            "vermisste":e.vermisste,"gerettete":e.gerettete,
            "einsatzleiter":e.einsatzleiter,"fuehrstelle":e.fuehrstelle,
            "alarmierung":e.alarmierung,"lat":e.lat,"lng":e.lng,"zoom":e.zoom,
            "beamer_token":e.beamer_token,
            "alarmzeit":e.alarmzeit.isoformat() if e.alarmzeit else None}

# ════════════════════════════════════════════════════════════════
# WEBSOCKET HUB
# ════════════════════════════════════════════════════════════════
class Hub:
    def __init__(self): self._c:Dict[int,List[tuple]]={}
    async def connect(self,ws,eid,uid):
        await ws.accept()
        self._c.setdefault(eid,[]).append((ws,uid))
        try: await ws.send_json({"type":"connected","eid":eid,"user":uid,"zeit":datetime.now().strftime("%H:%M")})
        except: pass
    def disconnect(self,ws,eid): self._c[eid]=[(w,u) for w,u in self._c.get(eid,[]) if w is not ws]
    async def broadcast(self,eid,msg,exclude=None):
        msg.setdefault("_ts",datetime.now().isoformat())
        payload=json.dumps(msg,ensure_ascii=False,default=str)
        dead=[]
        for ws,uid in list(self._c.get(eid,[])):
            if ws is exclude: continue
            try: await ws.send_text(payload)
            except: dead.append((ws,uid))
        if dead: self._c[eid]=[(w,u) for w,u in self._c.get(eid,[]) if (w,u) not in dead]
hub=Hub()

# ════════════════════════════════════════════════════════════════
# KI-AGENT
# ════════════════════════════════════════════════════════════════
async def ki_call(system:str, prompt:str, max_tokens:int=3000) -> str:
    if not ANTHROPIC_KEY: return f"[KI nicht verfügbar – ANTHROPIC_API_KEY setzen]\n{prompt[:200]}"
    try:
        import anthropic as _a
        client=_a.Anthropic(api_key=ANTHROPIC_KEY)
        msg=client.messages.create(model="claude-sonnet-4-20250514",max_tokens=max_tokens,
            system=system,messages=[{"role":"user","content":prompt}])
        return msg.content[0].text
    except Exception as e: return f"KI-Fehler: {e}"

SYS_S2="Du bist KI-Unterstützung S2 gemäß DV 100/FwDV 102. Alle Ausgaben sind Vorschläge. Keine Markdown-Sternchen."

async def ki_lagevortrag(einsatz:dict, abschnitte:list, kraefte:list, schadensstellen:list) -> str:
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    ab_text="\n".join(f"  {a['nummer']} {a['bezeichnung']}: {a.get('aufgabe','')} (AL: {a.get('abschnittsleiter_name','')} Funk: {a.get('abschnittsleiter_funk','')})" for a in abschnitte)
    krf_text="\n".join(f"  {k['organisation']} · {k['einheit']} · {k['funktion']} · {k['staerke']} Mann" for k in kraefte[:20])
    ss_text="\n".join(f"  {s['typ']}: {s['bezeichnung']} ({s['schwere']})" for s in schadensstellen[:10])
    prompt=f"""LAGEVORTRAG erstellen für Stand {now}

EINSATZ: {einsatz.get('stichwort','–')} | {einsatz.get('einsatzort','–')} | Stufe: {einsatz.get('lagestufe','–')}
OPFER: Tote={einsatz.get('tote',0)} | Verletzte={einsatz.get('verletzte',0)} | Verschüttete={einsatz.get('verschuettete',0)} | Obdachlose={einsatz.get('obdachlose',0)} | Vermisste={einsatz.get('vermisste',0)}

ABSCHNITTE:
{ab_text or '–'}

KRÄFTE:
{krf_text or '–'}

SCHADENSSTELLEN:
{ss_text or '–'}

Erstelle einen strukturierten LAGEVORTRAG (5-8 Minuten) mit:
1. LAGE (Schadenslage, Gefahrenlage, Entwicklung)
2. EIGENE KRÄFTE (Gliederung, Stärke, Abschnitte)
3. MASSNAHMEN (Was läuft, was ist entschieden)
4. OFFENE PUNKTE (Was fehlt, Entscheidungsbedarfe)
5. NÄCHSTE SCHRITTE (Zeitschiene)

Stand {now}. DV 100 konform. Als ENTWURF kennzeichnen."""
    return await ki_call(SYS_S2, prompt, 2500)

async def ki_meldung(einsatz:dict, typ:str, empfaenger:str) -> str:
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    prompt=f"""Erstelle eine {typ} für {empfaenger}. Stand {now}.

Einsatz: {einsatz.get('stichwort','–')} | {einsatz.get('einsatzort','–')}
Tote: {einsatz.get('tote',0)} | Verletzte: {einsatz.get('verletzte',0)} | Verschüttete: {einsatz.get('verschuettete',0)}
Lagestufe: {einsatz.get('lagestufe','–')}

Adressatengerecht, sachlich, DV 100 konform. Als ENTWURF."""
    return await ki_call(SYS_S2, prompt, 1200)

# ════════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════════
def _token(data:dict) -> str:
    exp=datetime.utcnow()+timedelta(minutes=TOKEN_MIN)
    return jwt.encode({**data,"exp":exp},SECRET_KEY,algorithm=ALGORITHM)

def get_user(tk:str=Depends(oauth2),db:Session=Depends(get_db)) -> User:
    exc=HTTPException(401,"Nicht autorisiert",headers={"WWW-Authenticate":"Bearer"})
    try:
        p=jwt.decode(tk,SECRET_KEY,algorithms=[ALGORITHM])
        u=db.query(User).filter(User.username==p.get("sub")).first()
        if not u or not u.active: raise exc
        return u
    except JWTError: raise exc

def req(*roles):
    def dep(u:User=Depends(get_user)) -> User:
        if u.role not in roles and u.role!="admin": raise HTTPException(403,"Keine Berechtigung")
        return u
    return dep

# ════════════════════════════════════════════════════════════════
# APP
# ════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app):
    init_db()
    print(f"[OK] S2-LageLive v4.0 auf Port {PORT}")
    yield

app=FastAPI(title="S2-LageLive",version="4.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
_fe=Path("frontend")
if _fe.exists() and any(_fe.iterdir()):
    app.mount("/static",StaticFiles(directory="frontend"),name="static")

# ── HEALTH ──────────────────────────────────────────────────────
@app.get("/health")
async def health(): return {"status":"ok","version":"4.0.0","port":PORT,"time":datetime.now().isoformat()}

# ── WEBSOCKET ────────────────────────────────────────────────────
@app.websocket("/ws/{eid}")
async def ws_ep(ws:WebSocket,eid:int,token:Optional[str]=None):
    uid="anonym"
    if token:
        try:
            p=jwt.decode(token,SECRET_KEY,algorithms=[ALGORITHM])
            uid=p.get("sub","anonym")
        except: pass
    await hub.connect(ws,eid,uid)
    try:
        while True:
            data=await ws.receive_text()
            try:
                msg=json.loads(data)
                if msg.get("type") in ("karten_zeichen","cursor"):
                    await hub.broadcast(eid,msg,exclude=ws)
            except: pass
    except WebSocketDisconnect: hub.disconnect(ws,eid)

# ── AUTH ─────────────────────────────────────────────────────────
@app.post("/api/auth/token")
async def login(form:OAuth2PasswordRequestForm=Depends(),db:Session=Depends(get_db)):
    u=db.query(User).filter(User.username==form.username).first()
    if not u or not verify_pw(form.password,u.hashed_pw):
        raise HTTPException(401,"Benutzername oder Passwort falsch")
    return {"access_token":_token({"sub":u.username,"role":u.role}),"token_type":"bearer",
            "user":{"username":u.username,"role":u.role,"display_name":u.display_name}}

@app.get("/api/auth/me")
async def me(u:User=Depends(get_user)):
    return {"username":u.username,"role":u.role,"display_name":u.display_name}

# Beamer-Login über Einsatz-Token (kein Passwort nötig)
@app.get("/api/auth/beamer/{beamer_token}")
async def beamer_login(beamer_token:str,db:Session=Depends(get_db)):
    e=db.query(Einsatz).filter(Einsatz.beamer_token==beamer_token).first()
    if not e: raise HTTPException(404,"Ungültiger Beamer-Token")
    token=_token({"sub":"beamer","role":"beamer","eid":e.id})
    return {"access_token":token,"token_type":"bearer","einsatz_id":e.id,
            "user":{"username":"beamer","role":"beamer","display_name":"Beamer"}}

# ── EINSATZ ──────────────────────────────────────────────────────
@app.get("/api/einsaetze")
async def list_e(db:Session=Depends(get_db),u:User=Depends(get_user)):
    return [_einsatz_dict(e) for e in db.query(Einsatz).order_by(Einsatz.id.desc()).all()]

@app.post("/api/einsaetze")
async def create_e(data:dict,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    import secrets
    e=Einsatz(kennung=data.get("kennung",f"E-{datetime.now():%Y%m%d-%H%M}"),
              stichwort=data.get("stichwort","–"),einsatzort=data.get("einsatzort","–"),
              lagestufe=data.get("lagestufe","Erstmeldung"),
              einsatzleiter=data.get("einsatzleiter",""),
              fuehrstelle=data.get("fuehrstelle",""),
              alarmierung=data.get("alarmierung",""),
              lat=data.get("lat",48.4732),lng=data.get("lng",7.9414),
              zoom=data.get("zoom",12),beamer_token=secrets.token_urlsafe(12))
    db.add(e); db.commit(); db.refresh(e)
    await hub.broadcast(e.id,{"type":"einsatz_erstellt",**_einsatz_dict(e)})
    return _einsatz_dict(e)

@app.get("/api/einsaetze/{eid}")
async def get_e(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    return _einsatz_dict(e)

@app.patch("/api/einsaetze/{eid}")
async def patch_e(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    for f in ["lagestufe","tote","verletzte","verschuettete","obdachlose","vermisste","gerettete",
              "stichwort","einsatzort","einsatzleiter","fuehrstelle","alarmierung","lat","lng","zoom"]:
        if f in data: setattr(e,f,data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"einsatz_update","einsatz_id":eid,**_einsatz_dict(e)})
    return _einsatz_dict(e)

# ── ABSCHNITTE ───────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/abschnitte")
async def list_ab(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Abschnitt).filter(Abschnitt.einsatz_id==eid).all()
    return [{"id":a.id,"einsatz_id":a.einsatz_id,"nummer":a.nummer,"bezeichnung":a.bezeichnung,
             "aufgabe":a.aufgabe,"status":a.status,"abschnittsleiter_name":a.abschnittsleiter_name,
             "abschnittsleiter_funk":a.abschnittsleiter_funk,"abschnittsleiter_tel":a.abschnittsleiter_tel,
             "staerke":a.staerke,"lat":a.lat,"lng":a.lng,"parent_id":a.parent_id} for a in rows]

@app.post("/api/einsaetze/{eid}/abschnitte")
async def create_ab(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    a=Abschnitt(einsatz_id=eid,nummer=data.get("nummer","A1"),
                bezeichnung=data.get("bezeichnung",""),aufgabe=data.get("aufgabe",""),
                status=data.get("status","aktiv"),
                abschnittsleiter_name=data.get("abschnittsleiter_name",""),
                abschnittsleiter_funk=data.get("abschnittsleiter_funk",""),
                abschnittsleiter_tel=data.get("abschnittsleiter_tel",""),
                staerke=data.get("staerke",0),lat=data.get("lat"),lng=data.get("lng"),
                parent_id=data.get("parent_id"))
    db.add(a); db.commit(); db.refresh(a)
    row={"id":a.id,"einsatz_id":a.einsatz_id,"nummer":a.nummer,"bezeichnung":a.bezeichnung,
         "aufgabe":a.aufgabe,"status":a.status,"abschnittsleiter_name":a.abschnittsleiter_name,
         "abschnittsleiter_funk":a.abschnittsleiter_funk,"abschnittsleiter_tel":a.abschnittsleiter_tel,
         "staerke":a.staerke,"lat":a.lat,"lng":a.lng,"parent_id":a.parent_id}
    await hub.broadcast(eid,{"type":"abschnitt_neu","abschnitt":row})
    return row

@app.patch("/api/einsaetze/{eid}/abschnitte/{aid}")
async def patch_ab(eid:int,aid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    a=db.get(Abschnitt,aid)
    if not a: raise HTTPException(404)
    for f in ["bezeichnung","aufgabe","status","abschnittsleiter_name","abschnittsleiter_funk",
              "abschnittsleiter_tel","staerke","lat","lng"]:
        if f in data: setattr(a,f,data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"abschnitt_update","aid":aid,**data})
    return {"ok":True}

@app.delete("/api/einsaetze/{eid}/abschnitte/{aid}")
async def del_ab(eid:int,aid:int,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    a=db.get(Abschnitt,aid)
    if not a: raise HTTPException(404)
    db.delete(a); db.commit()
    await hub.broadcast(eid,{"type":"abschnitt_delete","aid":aid})
    return {"ok":True}

# ── KRÄFTE ───────────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/kraefte")
async def list_krf(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Kraft).filter(Kraft.einsatz_id==eid).all()
    return [{"id":k.id,"einsatz_id":k.einsatz_id,"abschnitt_id":k.abschnitt_id,
             "organisation":k.organisation,"einheit":k.einheit,"funktion":k.funktion,
             "staerke":k.staerke,"fahrzeuge":k.fahrzeuge,"status":k.status,
             "ansprechpartner":k.ansprechpartner,"funk":k.funk,"lat":k.lat,"lng":k.lng,
             "erstellt_dt":k.erstellt_dt.isoformat() if k.erstellt_dt else None} for k in rows]

@app.post("/api/einsaetze/{eid}/kraefte")
async def create_krf(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    k=Kraft(einsatz_id=eid,abschnitt_id=data.get("abschnitt_id"),
            organisation=data.get("organisation","FW"),einheit=data.get("einheit",""),
            funktion=data.get("funktion",""),staerke=data.get("staerke",0),
            fahrzeuge=data.get("fahrzeuge",""),status=data.get("status","im_einsatz"),
            ansprechpartner=data.get("ansprechpartner",""),funk=data.get("funk",""),
            lat=data.get("lat"),lng=data.get("lng"))
    db.add(k); db.commit(); db.refresh(k)
    row={"id":k.id,"organisation":k.organisation,"einheit":k.einheit,"funktion":k.funktion,
         "staerke":k.staerke,"fahrzeuge":k.fahrzeuge,"status":k.status,
         "ansprechpartner":k.ansprechpartner,"funk":k.funk,"lat":k.lat,"lng":k.lng}
    await hub.broadcast(eid,{"type":"kraft_neu","kraft":row})
    if k.lat and k.lng:
        farbe={"FW":"#C8000A","RD":"#1A8A3C","THW":"#1A5FA8"}.get(k.organisation,"#E07B00")
        emoji={"FW":"🚒","RD":"🚑","THW":"🔵"}.get(k.organisation,"🟠")
        await hub.broadcast(eid,{"type":"karten_zeichen","id":f"krf_{k.id}",
                                  "tz_typ":f"{k.organisation} · {k.einheit}",
                                  "farbe":farbe,"emoji":emoji,"lat":k.lat,"lng":k.lng,
                                  "beschreibung":f"{k.funktion} · {k.staerke} Mann",
                                  "kategorie":"kraft","kraft_id":k.id})
    return row

@app.patch("/api/einsaetze/{eid}/kraefte/{kid}")
async def patch_krf(eid:int,kid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    k=db.get(Kraft,kid)
    if not k: raise HTTPException(404)
    for f in ["status","staerke","funktion","lat","lng","ansprechpartner","funk","abschnitt_id"]:
        if f in data: setattr(k,f,data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"kraft_update","kid":kid,**data})
    return {"ok":True}

@app.delete("/api/einsaetze/{eid}/kraefte/{kid}")
async def del_krf(eid:int,kid:int,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    k=db.get(Kraft,kid)
    if not k: raise HTTPException(404)
    db.delete(k); db.commit()
    await hub.broadcast(eid,{"type":"kraft_delete","kid":kid})
    return {"ok":True}

# ── SCHADENSSTELLEN ──────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/schadensstellen")
async def list_ss(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Schadensstelle).filter(Schadensstelle.einsatz_id==eid,Schadensstelle.status!="archiviert").all()
    return [{"id":s.id,"typ":s.typ,"bezeichnung":s.bezeichnung,"beschreibung":s.beschreibung,
             "schwere":s.schwere,"status":s.status,"lat":s.lat,"lng":s.lng,"radius_m":s.radius_m,
             "erstellt_von":s.erstellt_von,"abschnitt_id":s.abschnitt_id} for s in rows]

@app.post("/api/einsaetze/{eid}/schadensstellen")
async def create_ss(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    s=Schadensstelle(einsatz_id=eid,typ=data.get("typ","Schadensstelle"),
                     bezeichnung=data.get("bezeichnung",""),beschreibung=data.get("beschreibung",""),
                     schwere=data.get("schwere","mittel"),status=data.get("status","aktiv"),
                     lat=data["lat"],lng=data["lng"],radius_m=data.get("radius_m",0),
                     abschnitt_id=data.get("abschnitt_id"),erstellt_von=u.display_name or u.username)
    db.add(s); db.commit(); db.refresh(s)
    farben={"leicht":"#22B84E","mittel":"#E07B00","schwer":"#C8000A","kritisch":"#7A0006"}
    emojis={"Brand":"🔥","Überflutung":"🌊","Einsturz":"🏗","Verletzt":"🚑","Vermisst":"🔍","Schadensstelle":"❌"}
    farbe=farben.get(s.schwere,"#C8000A")
    emoji=emojis.get(s.typ,"❌")
    row={"id":s.id,"typ":s.typ,"bezeichnung":s.bezeichnung,"schwere":s.schwere,
         "status":s.status,"lat":s.lat,"lng":s.lng,"radius_m":s.radius_m}
    await hub.broadcast(eid,{"type":"schadensstelle_neu","schadensstelle":row})
    await hub.broadcast(eid,{"type":"karten_zeichen","id":f"ss_{s.id}","tz_typ":f"{s.typ}: {s.bezeichnung}",
                              "farbe":farbe,"emoji":emoji,"lat":s.lat,"lng":s.lng,
                              "beschreibung":f"{s.schwere} · {s.beschreibung[:80]}",
                              "kategorie":"schadensstelle","ss_id":s.id,
                              "radius_m":s.radius_m,"zeit":datetime.now().strftime("%H:%M")})
    return row

@app.patch("/api/einsaetze/{eid}/schadensstellen/{sid}")
async def patch_ss(eid:int,sid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    s=db.get(Schadensstelle,sid)
    if not s: raise HTTPException(404)
    for f in ["status","schwere","beschreibung","bezeichnung"]:
        if f in data: setattr(s,f,data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"schadensstelle_update","sid":sid,**data})
    return {"ok":True}

# ── BETROFFENE BEREICHE ──────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/bereiche")
async def list_ber(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(BetroffenerBereich).filter(BetroffenerBereich.einsatz_id==eid).all()
    return [{"id":b.id,"bezeichnung":b.bezeichnung,"typ":b.typ,"beschreibung":b.beschreibung,
             "farbe":b.farbe,"geo_json":b.geo_json} for b in rows]

@app.post("/api/einsaetze/{eid}/bereiche")
async def create_ber(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    b=BetroffenerBereich(einsatz_id=eid,bezeichnung=data.get("bezeichnung",""),
                         typ=data.get("typ","Überflutung"),beschreibung=data.get("beschreibung",""),
                         farbe=data.get("farbe","#1A5FA8"),geo_json=data.get("geo_json",""))
    db.add(b); db.commit(); db.refresh(b)
    row={"id":b.id,"bezeichnung":b.bezeichnung,"typ":b.typ,"farbe":b.farbe,"geo_json":b.geo_json}
    await hub.broadcast(eid,{"type":"bereich_neu","bereich":row})
    return row

@app.delete("/api/einsaetze/{eid}/bereiche/{bid}")
async def del_ber(eid:int,bid:int,db:Session=Depends(get_db),u:User=Depends(req("s2"))):
    b=db.get(BetroffenerBereich,bid)
    if not b: raise HTTPException(404)
    db.delete(b); db.commit()
    await hub.broadcast(eid,{"type":"bereich_delete","bid":bid})
    return {"ok":True}

# ── KARTE ────────────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/karte")
async def list_karte(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(KartenZeichen).filter(KartenZeichen.einsatz_id==eid,KartenZeichen.aktiv==True).all()
    return [{"id":k.id,"tz_typ":k.tz_typ,"kategorie":k.kategorie,"farbe":k.farbe,
             "emoji":k.emoji,"lat":k.lat,"lng":k.lng,"beschreibung":k.beschreibung,
             "erstellt_von":k.erstellt_von} for k in rows]

@app.post("/api/einsaetze/{eid}/karte")
async def add_karte(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    k=KartenZeichen(einsatz_id=eid,tz_typ=data.get("tz_typ","Marker"),
                    kategorie=data.get("kategorie","allgemein"),
                    farbe=data.get("farbe","#C8000A"),emoji=data.get("emoji","📍"),
                    lat=data["lat"],lng=data["lng"],beschreibung=data.get("beschreibung",""),
                    erstellt_von=u.display_name or u.username)
    db.add(k); db.commit(); db.refresh(k)
    msg={"type":"karten_zeichen","id":k.id,"tz_typ":k.tz_typ,"kategorie":k.kategorie,
         "farbe":k.farbe,"emoji":k.emoji,"lat":k.lat,"lng":k.lng,
         "beschreibung":k.beschreibung,"erstellt_von":k.erstellt_von,
         "zeit":datetime.now().strftime("%H:%M")}
    await hub.broadcast(eid,msg)
    return {"id":k.id}

@app.delete("/api/einsaetze/{eid}/karte/{kid}")
async def del_karte(eid:int,kid:int,db:Session=Depends(get_db),u:User=Depends(req("s2"))):
    k=db.get(KartenZeichen,kid)
    if not k: raise HTTPException(404)
    k.aktiv=False; db.commit()
    await hub.broadcast(eid,{"type":"karten_zeichen_delete","id":kid})
    return {"ok":True}

# ── TAGEBUCH ─────────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/tagebuch")
async def list_tb(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id.asc()).all()
    return [{"id":r.id,"einsatz_id":r.einsatz_id,"author_name":r.author_name,"author_role":r.author_role,
             "eingang_dt":r.eingang_dt.isoformat() if r.eingang_dt else None,"kategorie":r.kategorie,
             "prioritaet":r.prioritaet,"betreff":r.betreff,"inhalt":r.inhalt,"quelle":r.quelle,
             "an":r.an,"entry_hash":r.entry_hash,"freigegeben":r.freigegeben} for r in rows]

@app.post("/api/einsaetze/{eid}/tagebuch")
async def add_tb(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    r=TagebuchEintrag(einsatz_id=eid,author_name=u.display_name or u.username,author_role=u.role,
                      eingang_dt=datetime.utcnow(),kategorie=data.get("kategorie","Meldung"),
                      prioritaet=data.get("prioritaet","normal"),betreff=data.get("betreff",""),
                      inhalt=data.get("inhalt",""),quelle=data.get("quelle","Manuell"),
                      an=data.get("an",""),prev_hash=_prev_hash(db,eid))
    db.add(r); db.flush(); r.entry_hash=r.berechne_hash(); db.commit(); db.refresh(r)
    msg={"type":"tagebuch_eintrag","id":r.id,"einsatz_id":eid,"kategorie":r.kategorie,
         "prioritaet":r.prioritaet,"betreff":r.betreff,"inhalt":r.inhalt[:300],
         "author":r.author_name,"author_name":r.author_name,"author_role":r.author_role,
         "quelle":r.quelle,"an":r.an,"zeit":r.eingang_dt.strftime("%d.%m. %H:%M"),
         "hash":(r.entry_hash or "")[:12]+"…","entry_hash":r.entry_hash,
         "freigegeben":False,"eingang_dt":r.eingang_dt.isoformat()}
    await hub.broadcast(eid,msg)
    return {"id":r.id,"entry_hash":r.entry_hash}

@app.post("/api/einsaetze/{eid}/tagebuch/{tid}/freigeben")
async def freigeben(eid:int,tid:int,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    r=db.query(TagebuchEintrag).filter(TagebuchEintrag.id==tid,TagebuchEintrag.einsatz_id==eid).first()
    if not r: raise HTTPException(404)
    r.freigegeben=True; db.commit(); return {"ok":True}

@app.get("/api/einsaetze/{eid}/export/tagebuch")
async def export_tb(eid:int,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    rows=db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id.asc()).all()
    lines=["="*70,f"EINSATZTAGEBUCH · {e.kennung}",f"Stand: {datetime.now():%d.%m.%Y %H:%M}","="*70,""]
    for r in rows:
        dt=r.eingang_dt.strftime("%d.%m.%Y %H:%M") if r.eingang_dt else "–"
        lines+=[f"#{r.id:04d} | {dt} | {r.kategorie} | {r.prioritaet}",
                f"An: {r.an or '–'} | Betreff: {r.betreff}",
                f"Autor: {r.author_name}","",r.inhalt or "–","",
                f"HASH: {r.entry_hash or '–'}","─"*50,""]
    path=f"exports/tagebuch_{eid}_{datetime.now():%Y%m%d_%H%M}.txt"
    Path(path).write_text("\n".join(lines),encoding="utf-8")
    return FileResponse(path,media_type="text/plain",filename=f"Tagebuch_{e.kennung}.txt")

# ── LAGEVORTRAG ──────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/lagevortraege")
async def list_lv(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Lagevortrag).filter(Lagevortrag.einsatz_id==eid).order_by(Lagevortrag.revision.desc()).all()
    return [{"id":r.id,"revision":r.revision,"erstellt_dt":r.erstellt_dt.isoformat() if r.erstellt_dt else None,
             "erstellt_von":r.erstellt_von,"inhalt":r.inhalt,"freigegeben":r.freigegeben,
             "naechster_dt":r.naechster_dt.isoformat() if r.naechster_dt else None,
             "intervall_min":r.intervall_min} for r in rows]

@app.post("/api/einsaetze/{eid}/lagevortraege/generieren")
async def gen_lv(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s2"))):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    abschnitte=[{"nummer":a.nummer,"bezeichnung":a.bezeichnung,"aufgabe":a.aufgabe,
                  "abschnittsleiter_name":a.abschnittsleiter_name,"abschnittsleiter_funk":a.abschnittsleiter_funk}
                for a in db.query(Abschnitt).filter(Abschnitt.einsatz_id==eid).all()]
    kraefte=[{"organisation":k.organisation,"einheit":k.einheit,"funktion":k.funktion,"staerke":k.staerke}
             for k in db.query(Kraft).filter(Kraft.einsatz_id==eid).all()]
    schadensstellen=[{"typ":s.typ,"bezeichnung":s.bezeichnung,"schwere":s.schwere}
                     for s in db.query(Schadensstelle).filter(Schadensstelle.einsatz_id==eid,Schadensstelle.status=="aktiv").all()]
    inhalt=await ki_lagevortrag(_einsatz_dict(e),abschnitte,kraefte,schadensstellen)
    rev_max=db.query(Lagevortrag).filter(Lagevortrag.einsatz_id==eid).count()
    intervall=data.get("intervall_min",60)
    naechster=datetime.utcnow()+timedelta(minutes=intervall) if intervall>0 else None
    lv=Lagevortrag(einsatz_id=eid,revision=rev_max+1,erstellt_von=u.display_name or u.username,
                   inhalt=inhalt,intervall_min=intervall,naechster_dt=naechster)
    db.add(lv); db.commit(); db.refresh(lv)
    await hub.broadcast(eid,{"type":"lagevortrag_neu","revision":lv.revision,
                              "naechster_dt":lv.naechster_dt.isoformat() if lv.naechster_dt else None})
    return {"id":lv.id,"revision":lv.revision,"inhalt":inhalt,
            "naechster_dt":lv.naechster_dt.isoformat() if lv.naechster_dt else None}

@app.post("/api/einsaetze/{eid}/lagevortraege/{lid}/freigeben")
async def freigeben_lv(eid:int,lid:int,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    lv=db.get(Lagevortrag,lid)
    if not lv: raise HTTPException(404)
    lv.freigegeben=True; db.commit()
    await hub.broadcast(eid,{"type":"lagevortrag_freigegeben","lid":lid,"revision":lv.revision})
    return {"ok":True}

# ── MELDUNGEN ────────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/meldungen")
async def list_ml(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Meldung).filter(Meldung.einsatz_id==eid).order_by(Meldung.id.desc()).all()
    return [{"id":m.id,"typ":m.typ,"empfaenger":m.empfaenger,"inhalt":m.inhalt,
             "ki_generiert":m.ki_generiert,"versendet":m.versendet,
             "erstellt_dt":m.erstellt_dt.isoformat() if m.erstellt_dt else None} for m in rows]

@app.post("/api/einsaetze/{eid}/meldungen/generieren")
async def gen_ml(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s2"))):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    typ=data.get("typ","Lagemeldung")
    empfaenger=data.get("empfaenger","Übergeordnete Führungsstelle")
    inhalt=await ki_meldung(_einsatz_dict(e),typ,empfaenger)
    m=Meldung(einsatz_id=eid,typ=typ,empfaenger=empfaenger,inhalt=inhalt,ki_generiert=True)
    db.add(m); db.commit(); db.refresh(m)
    await hub.broadcast(eid,{"type":"meldung_neu","meldung_id":m.id,"typ":typ,"empfaenger":empfaenger})
    return {"id":m.id,"inhalt":inhalt}

@app.post("/api/einsaetze/{eid}/meldungen")
async def create_ml(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    m=Meldung(einsatz_id=eid,typ=data.get("typ","Lagemeldung"),
              empfaenger=data.get("empfaenger",""),inhalt=data.get("inhalt",""),ki_generiert=False)
    db.add(m); db.commit(); db.refresh(m)
    return {"id":m.id}

@app.post("/api/einsaetze/{eid}/meldungen/{mid}/versendet")
async def mark_versendet(eid:int,mid:int,db:Session=Depends(get_db),u:User=Depends(req("s1","s2"))):
    m=db.get(Meldung,mid)
    if not m: raise HTTPException(404)
    m.versendet=True; m.versendet_dt=datetime.utcnow(); db.commit()
    return {"ok":True}

# ── KI-ANALYSE (Lageeingabe) ─────────────────────────────────────
@app.post("/api/einsaetze/{eid}/ki/analyse")
async def ki_analyse(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s2"))):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    for f in ["tote","verletzte","verschuettete","obdachlose","vermisste","lagestufe"]:
        try:
            if data.get(f): setattr(e,f,int(data[f]) if f!="lagestufe" else data[f])
        except: pass
    db.commit()
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    outputs=data.get("outputs",["Aktuelle Lagebeschreibung"])
    prompt=f"""LAGEINFORMATION STAND {now}
Stichwort: {data.get('stichwort','–')} | Ort: {data.get('einsatzort','–')} | Stufe: {data.get('lagestufe','–')}
Tote={data.get('tote','–')} Verletzte={data.get('verletzte','–')} Verschüttete={data.get('verschuettete','–')} Obdachlose={data.get('obdachlose','–')}
SCHADENSLAGE: {data.get('schadenslage','–')}
KRÄFTE: {data.get('kraefte','–')}
GEO: {data.get('geoLage','–')}
WETTER: {data.get('temperatur','–')}
AUSGABEN: {', '.join(outputs)}
Erstelle alle Produkte mit === PRODUKTNAME === als Überschrift. Stand {now}."""
    text=await ki_call(SYS_S2,prompt,3500)
    result={"raw":text,"stand":now}
    for sec in ["LAGEBESCHREIBUNG","AKTUELLE LAGEBESCHREIBUNG","KURZLAGE","MANAGEMENT SUMMARY",
                "PRESSEMITTEILUNG","BÜRGERINFORMATION","OFFENE PUNKTE","LAGEFORTSCHREIBUNG"]:
        m=re.search(rf"={2,}[^=]*{re.escape(sec)}[^=]*={2,}([\s\S]*?)(?:={2,}|$)",text,re.IGNORECASE)
        if m: result[sec.lower().replace(" ","_").replace("ü","ue").replace("ä","ae")]=m.group(1).strip()
    r=TagebuchEintrag(einsatz_id=eid,author_name="KI-System S2",author_role="system",
                      kategorie="Lage",prioritaet="hoch",
                      betreff=f"KI-Analyse · {datetime.now():%H:%M Uhr}",
                      inhalt=(result.get("lagebeschreibung") or result.get("aktuelle_lagebeschreibung") or text[:1500]),
                      quelle="KI-Analyse",prev_hash=_prev_hash(db,eid))
    db.add(r); db.flush(); r.entry_hash=r.berechne_hash(); db.commit()
    await hub.broadcast(eid,{"type":"einsatz_update","einsatz_id":eid,**_einsatz_dict(e)})
    await hub.broadcast(eid,{"type":"ki_analyse_fertig",**result})
    return result

# ── FILE UPLOAD ──────────────────────────────────────────────────
@app.post("/api/einsaetze/{eid}/upload")
async def upload(eid:int,file:UploadFile=File(...),db:Session=Depends(get_db),u:User=Depends(get_user)):
    content=await file.read()
    safe=re.sub(r"[^\w.\-]","_",file.filename or "datei")
    Path(f"uploads/{eid}_{datetime.now():%Y%m%d_%H%M%S}_{safe}").write_bytes(content)
    await hub.broadcast(eid,{"type":"datei_eingang","datei":file.filename,"ki_kurz":"Empfangen",
                              "zeit":datetime.now().strftime("%H:%M")})
    return {"datei":file.filename,"status":"empfangen"}

# ── BEAMER (öffentliche Lageansicht) ─────────────────────────────
@app.get("/api/beamer/{beamer_token}/lage")
async def beamer_lage(beamer_token:str,db:Session=Depends(get_db)):
    e=db.query(Einsatz).filter(Einsatz.beamer_token==beamer_token).first()
    if not e: raise HTTPException(404)
    schadensstellen=[{"typ":s.typ,"bezeichnung":s.bezeichnung,"schwere":s.schwere,"lat":s.lat,"lng":s.lng}
                     for s in db.query(Schadensstelle).filter(Schadensstelle.einsatz_id==e.id,Schadensstelle.status=="aktiv").all()]
    kraefte_summary=db.query(Kraft).filter(Kraft.einsatz_id==e.id).count()
    lv=db.query(Lagevortrag).filter(Lagevortrag.einsatz_id==e.id,Lagevortrag.freigegeben==True).order_by(Lagevortrag.revision.desc()).first()
    return {**_einsatz_dict(e),"schadensstellen":schadensstellen,
            "kraefte_count":kraefte_summary,
            "letzter_lagevortrag":lv.inhalt[:500] if lv else None,
            "lv_revision":lv.revision if lv else 0}

# ── FRONTEND ─────────────────────────────────────────────────────
@app.get("/")
async def root():
    for p in [Path("frontend/index.html"),Path("/app/frontend/index.html")]:
        if p.exists(): return FileResponse(str(p),media_type="text/html")
    return JSONResponse({"status":"S2-LageLive v4.0","health":"/health"})

@app.get("/{path:path}")
async def spa(path:str):
    for base in [Path("frontend"),Path("/app/frontend")]:
        fp=base/path
        if fp.exists() and fp.is_file(): return FileResponse(str(fp))
    for p in [Path("frontend/index.html"),Path("/app/frontend/index.html")]:
        if p.exists(): return FileResponse(str(p),media_type="text/html")
    raise HTTPException(404)

# ── START ─────────────────────────────────────────────────────────
if __name__=="__main__":
    import uvicorn
    print(f"[START] uvicorn 0.0.0.0:{PORT}")
    uvicorn.run(app,host="0.0.0.0",port=PORT,log_level="info")
