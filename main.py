"""
S2-LageLive Release 1
- FwDV 100 Rollen (EL, S1-S6, Presse, Beamer, Admin)
- Einsatz reset / neu (Admin)
- Upload Vordrucke + Meldungen mit KI-Analyse
- Tagebuch-Export TXT
- Karten-TZ (DV 102) mit Typ-spezifischen Formulardaten
- Freihand-Zeichnungen persistieren
- Infrastruktur-Objekte pflegbar (KH, Schulen etc.)
- Pressemeldung KI-Generierung
- Analyse-Berichte hochladen (KI lernt daraus)
- Lagebesprechung als Meilenstein
"""
import os, json, hashlib, asyncio, re, io, zipfile, secrets
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, List

for _d in ["data","exports","uploads","frontend","uploads/analyse"]:
    Path(_d).mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

import bcrypt as _bcrypt
def hash_pw(pw): return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()
def verify_pw(pw, h): return _bcrypt.checkpw(pw.encode(), h.encode())

SECRET_KEY    = os.environ.get("SECRET_KEY","s2-r1-dev-key-bitte-in-env-setzen-min32chars!")
ALGORITHM     = "HS256"
TOKEN_MIN     = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES","480"))
DATABASE_URL  = os.environ.get("DATABASE_URL","sqlite:///./data/s2.db")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY","")
PORT          = int(os.environ.get("PORT","8000"))
print(f"[BOOT] S2-LageLive R1 | PORT={PORT} | KI={'ja' if ANTHROPIC_KEY else 'nein'}")

# ═══════════════════════════════════════════════════════════════
# DATENBANK
# ═══════════════════════════════════════════════════════════════
class Base(DeclarativeBase): pass

# FwDV 100 Rollen
ROLES = {
    "admin":        {"name":"Administrator",         "color":"#C8000A"},
    "el":           {"name":"Einsatzleiter",          "color":"#C8000A"},
    "s1":           {"name":"S1 Personal",            "color":"#1A5FA8"},
    "s2":           {"name":"S2 Lage",                "color":"#1A5FA8"},
    "s3":           {"name":"S3 Einsatz",             "color":"#1A5FA8"},
    "s4":           {"name":"S4 Versorgung",          "color":"#1A8A3C"},
    "s5":           {"name":"S5 Presse",              "color":"#E07B00"},
    "s6":           {"name":"S6 Fernmelde",           "color":"#607080"},
    "presse":       {"name":"Pressesprecher",         "color":"#E07B00"},
    "buergermeister":{"name":"Bürgermeister",         "color":"#7A0006"},
    "beamer":       {"name":"Beamer (Anzeige)",       "color":"#384858"},
    "extern":       {"name":"Beobachter",             "color":"#384858"},
}

class User(Base):
    __tablename__="users"
    id           = Column(Integer,primary_key=True)
    username     = Column(String(64),unique=True,nullable=False)
    hashed_pw    = Column(String(256),nullable=False)
    display_name = Column(String(128),default="")
    role         = Column(String(32),default="extern")
    sachgebiet   = Column(String(32),default="")   # S1..S6 / EL
    aktiv_eid    = Column(Integer,default=1)       # welcher Einsatz aktiv
    active       = Column(Boolean,default=True)

class Einsatz(Base):
    __tablename__="einsaetze"
    id             = Column(Integer,primary_key=True)
    kennung        = Column(String(64),default="")
    stichwort      = Column(String(128),default="")
    einsatzort     = Column(Text,default="")
    alarmzeit      = Column(DateTime,default=datetime.utcnow)
    lagestufe      = Column(String(64),default="Erstmeldung")
    aktiv          = Column(Boolean,default=True)
    archiviert     = Column(Boolean,default=False)
    tote           = Column(Integer,default=0)
    verletzte      = Column(Integer,default=0)
    verschuettete  = Column(Integer,default=0)
    obdachlose     = Column(Integer,default=0)
    vermisste      = Column(Integer,default=0)
    gerettete      = Column(Integer,default=0)
    einsatzleiter  = Column(String(128),default="")
    fuehrstelle    = Column(Text,default="")
    alarmierung    = Column(Text,default="")
    region_info    = Column(Text,default="")  # Übungsszenario-Info
    lat            = Column(Float,nullable=True,default=48.4732)
    lng            = Column(Float,nullable=True,default=7.9414)
    zoom           = Column(Integer,default=12)
    beamer_token   = Column(String(64),nullable=True)
    # Nächste Lagebesprechung
    naechste_lagebesprechung = Column(DateTime,nullable=True)
    lagebesprechung_intervall_min = Column(Integer,default=60)

class Abschnitt(Base):
    __tablename__="abschnitte"
    id                    = Column(Integer,primary_key=True)
    einsatz_id            = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    nummer                = Column(String(16),default="A1")
    bezeichnung           = Column(String(128),default="")
    aufgabe               = Column(Text,default="")
    status                = Column(String(32),default="aktiv")
    abschnittsleiter_name = Column(String(128),default="")
    abschnittsleiter_funk = Column(String(64),default="")
    abschnittsleiter_tel  = Column(String(64),default="")
    staerke               = Column(Integer,default=0)
    lat                   = Column(Float,nullable=True)
    lng                   = Column(Float,nullable=True)
    parent_id             = Column(Integer,ForeignKey("abschnitte.id"),nullable=True)

class Kraft(Base):
    __tablename__="kraefte"
    id              = Column(Integer,primary_key=True)
    einsatz_id      = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    abschnitt_id    = Column(Integer,ForeignKey("abschnitte.id"),nullable=True)
    organisation    = Column(String(64),default="FW")
    einheit         = Column(String(128),default="")
    funktion        = Column(String(128),default="")
    staerke         = Column(Integer,default=0)
    fahrzeuge       = Column(String(128),default="")
    status          = Column(String(32),default="im_einsatz")
    ansprechpartner = Column(String(128),default="")
    funk            = Column(String(64),default="")
    lat             = Column(Float,nullable=True)
    lng             = Column(Float,nullable=True)

class KartenObjekt(Base):
    """Taktisches Zeichen nach DV 102 oder freies Objekt"""
    __tablename__="karten_objekte"
    id              = Column(Integer,primary_key=True)
    einsatz_id      = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    typ             = Column(String(64),default="punkt")       # punkt|linie|flaeche|freihand|tz
    tz_kategorie    = Column(String(64),default="allgemein")   # schadensstelle|bsr|einheit|sperrung|krankenhaus|...
    tz_symbol       = Column(String(32),default="kreis")       # DV 102 Symbol-ID
    farbe           = Column(String(16),default="#C8000A")
    titel           = Column(String(128),default="")
    beschreibung    = Column(Text,default="")
    # Typ-spezifische Felder als JSON
    daten_json      = Column(Text,default="{}")
    # Geometrie
    lat             = Column(Float,nullable=True)
    lng             = Column(Float,nullable=True)
    geo_json        = Column(Text,default="")  # für Linien, Flächen, Freihand
    radius_m        = Column(Integer,default=0)
    erstellt_von    = Column(String(128),default="")
    erstellt_dt     = Column(DateTime,default=datetime.utcnow)
    aktiv           = Column(Boolean,default=True)
    abschnitt_id    = Column(Integer,ForeignKey("abschnitte.id"),nullable=True)

class InfrastrukturObjekt(Base):
    """OSM-Objekte + eigene, mit pflegbaren Zustandsdaten"""
    __tablename__="infrastruktur"
    id              = Column(Integer,primary_key=True)
    einsatz_id      = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    osm_id          = Column(String(32),nullable=True)
    typ             = Column(String(64),default="krankenhaus")  # krankenhaus|schule|tankstelle|...
    name            = Column(String(128),default="")
    adresse         = Column(String(256),default="")
    lat             = Column(Float,nullable=False)
    lng             = Column(Float,nullable=False)
    # Pflegbare Zustandsdaten
    status          = Column(String(32),default="unbekannt")    # unbekannt|verfügbar|eingeschränkt|nicht_verfügbar|betroffen
    kapazitaet_gesamt   = Column(Integer,default=0)
    kapazitaet_frei     = Column(Integer,default=0)
    notizen         = Column(Text,default="")
    letzte_meldung  = Column(DateTime,nullable=True)
    gemeldet_von    = Column(String(128),default="")
    erstellt_dt     = Column(DateTime,default=datetime.utcnow)

class TagebuchEintrag(Base):
    __tablename__="tagebuch"
    id              = Column(Integer,primary_key=True)
    einsatz_id      = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    author_name     = Column(String(128),default="")
    author_role     = Column(String(32),default="")
    eingang_dt      = Column(DateTime,default=datetime.utcnow)
    kategorie       = Column(String(64),default="Meldung")
    prioritaet      = Column(String(16),default="normal")
    betreff         = Column(String(256),default="")
    inhalt          = Column(Text,default="")
    quelle          = Column(String(128),default="Manuell")
    an              = Column(String(256),default="")
    datei_pfad      = Column(String(512),nullable=True)  # hochgeladene Datei
    datei_name      = Column(String(256),nullable=True)
    ki_analyse      = Column(Text,nullable=True)  # KI-Auswertung der Datei
    prev_hash       = Column(String(64),default="GENESIS")
    entry_hash      = Column(String(64),nullable=True)
    freigegeben     = Column(Boolean,default=False)
    freigabe_von    = Column(String(128),nullable=True)

    def berechne_hash(self):
        d=json.dumps({"id":self.id,"eid":self.einsatz_id,"author":self.author_name,
                      "dt":str(self.eingang_dt),"betreff":self.betreff,
                      "inhalt":(self.inhalt or "")[:300],"prev":self.prev_hash},
                     ensure_ascii=False,sort_keys=True)
        return hashlib.sha256(d.encode()).hexdigest()

class Lagevortrag(Base):
    __tablename__="lagevortraege"
    id            = Column(Integer,primary_key=True)
    einsatz_id    = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    revision      = Column(Integer,default=1)
    erstellt_dt   = Column(DateTime,default=datetime.utcnow)
    erstellt_von  = Column(String(128),default="")
    inhalt        = Column(Text,default="")
    freigegeben   = Column(Boolean,default=False)
    freigabe_von  = Column(String(128),nullable=True)
    ist_lagebesprechung = Column(Boolean,default=False)  # Meilenstein
    lagebesprechung_dt  = Column(DateTime,nullable=True)
    naechster_dt  = Column(DateTime,nullable=True)
    intervall_min = Column(Integer,default=60)

class Meldung(Base):
    __tablename__="meldungen"
    id              = Column(Integer,primary_key=True)
    einsatz_id      = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    typ             = Column(String(64),default="Lagemeldung")
    empfaenger      = Column(String(256),default="")
    inhalt          = Column(Text,default="")
    ki_generiert    = Column(Boolean,default=False)
    datei_pfad      = Column(String(512),nullable=True)
    datei_name      = Column(String(256),nullable=True)
    versendet       = Column(Boolean,default=False)
    versendet_dt    = Column(DateTime,nullable=True)
    erstellt_dt     = Column(DateTime,default=datetime.utcnow)
    erstellt_von    = Column(String(128),default="")

class PresseMeldung(Base):
    __tablename__="pressemeldungen"
    id              = Column(Integer,primary_key=True)
    einsatz_id      = Column(Integer,ForeignKey("einsaetze.id"),nullable=False)
    revision        = Column(Integer,default=1)
    titel           = Column(String(256),default="")
    inhalt          = Column(Text,default="")
    baustein_lage   = Column(Text,default="")
    baustein_massnahmen = Column(Text,default="")
    baustein_appell = Column(Text,default="")
    bilder_json     = Column(Text,default="[]")  # Liste von Dateipfaden
    ki_generiert    = Column(Boolean,default=False)
    freigegeben     = Column(Boolean,default=False)
    erstellt_dt     = Column(DateTime,default=datetime.utcnow)
    erstellt_von    = Column(String(128),default="")

class AnalyseBericht(Base):
    """Hochgeladene Katastrophen-Analyseberichte zum Lernen"""
    __tablename__="analyseberichte"
    id              = Column(Integer,primary_key=True)
    titel           = Column(String(256),default="")
    kategorie       = Column(String(64),default="Erdbeben")
    region          = Column(String(128),default="")
    datum_ereignis  = Column(String(64),default="")
    datei_pfad      = Column(String(512),nullable=True)
    inhalt_text     = Column(Text,default="")
    ki_zusammenfassung = Column(Text,default="")
    ki_empfehlungen = Column(Text,default="")
    hochgeladen_dt  = Column(DateTime,default=datetime.utcnow)
    hochgeladen_von = Column(String(128),default="")

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
            users=[
                ("admin",  "admin123",  "Administrator",           "admin"),
                ("el",     "el123",     "Einsatzleitung",          "el"),
                ("s1",     "s1123",     "S1 Personal",             "s1"),
                ("s2",     "s2123",     "S2 Lage",                 "s2"),
                ("s3",     "s3123",     "S3 Einsatz",              "s3"),
                ("s4",     "s4123",     "S4 Versorgung",           "s4"),
                ("s5",     "s5123",     "S5 Presse",               "s5"),
                ("s6",     "s6123",     "S6 Fernmelde",            "s6"),
                ("presse", "presse123", "Pressesprecher",          "presse"),
                ("beamer", "beamer123", "Beamer-Anzeige",          "beamer"),
                ("extern", "extern123", "Beobachter",              "extern"),
            ]
            for u,pw,name,role in users:
                s.add(User(username=u,hashed_pw=hash_pw(pw),display_name=name,role=role))
            s.commit(); print("[DB] FwDV 100 Nutzer angelegt (Passwörter vor Einsatz ändern!)")
        if s.query(Einsatz).count()==0:
            s.add(Einsatz(kennung=f"E-{datetime.now():%Y%m%d}",stichwort="Bereitschaft",
                          einsatzort="–",beamer_token=secrets.token_urlsafe(12)))
            s.commit(); print("[DB] Standard-Einsatz angelegt")

def _prev_hash(db,eid):
    last=(db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid)
            .order_by(TagebuchEintrag.id.desc()).first())
    return last.entry_hash if (last and last.entry_hash) else "GENESIS"

def _einsatz_dict(e):
    return {
        "id":e.id,"kennung":e.kennung,"stichwort":e.stichwort,"einsatzort":e.einsatzort,
        "lagestufe":e.lagestufe,"aktiv":e.aktiv,"archiviert":e.archiviert,
        "tote":e.tote,"verletzte":e.verletzte,"verschuettete":e.verschuettete,
        "obdachlose":e.obdachlose,"vermisste":e.vermisste,"gerettete":e.gerettete,
        "einsatzleiter":e.einsatzleiter,"fuehrstelle":e.fuehrstelle,"alarmierung":e.alarmierung,
        "region_info":e.region_info,"lat":e.lat,"lng":e.lng,"zoom":e.zoom,
        "beamer_token":e.beamer_token,
        "naechste_lagebesprechung":e.naechste_lagebesprechung.isoformat() if e.naechste_lagebesprechung else None,
        "lagebesprechung_intervall_min":e.lagebesprechung_intervall_min,
        "alarmzeit":e.alarmzeit.isoformat() if e.alarmzeit else None,
    }

# ═══════════════════════════════════════════════════════════════
# WEBSOCKET HUB
# ═══════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════
# KI
# ═══════════════════════════════════════════════════════════════
SYS_S2="Du bist KI-Unterstützung im Führungsstab gemäß DV 100. Keine Markdown-Sternchen. Sachlich, belastbar, als ENTWURF kennzeichnen."

async def ki_call(system:str,prompt:str,max_tokens:int=2500)->str:
    if not ANTHROPIC_KEY: return f"[KI nicht verfügbar – ANTHROPIC_API_KEY in Railway Variables setzen]\n\nEingabe:\n{prompt[:300]}"
    try:
        import anthropic as _a
        client=_a.Anthropic(api_key=ANTHROPIC_KEY)
        msg=client.messages.create(model="claude-sonnet-4-20250514",max_tokens=max_tokens,
            system=system,messages=[{"role":"user","content":prompt}])
        return msg.content[0].text
    except Exception as e: return f"KI-Fehler: {e}"

async def ki_lagevortrag(e:dict,abschnitte:list,kraefte:list,ss:list)->str:
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    return await ki_call(SYS_S2,f"""LAGEVORTRAG erstellen · Stand {now}

EINSATZ: {e.get('stichwort','–')} | {e.get('einsatzort','–')} | {e.get('lagestufe','–')}
OPFER: Tote={e.get('tote',0)} | Verl={e.get('verletzte',0)} | Versch={e.get('verschuettete',0)} | Obdachlos={e.get('obdachlose',0)} | Vermisst={e.get('vermisste',0)}
ABSCHNITTE: {json.dumps(abschnitte[:10],ensure_ascii=False)}
KRÄFTE: {json.dumps(kraefte[:15],ensure_ascii=False)}
SCHADENSSTELLEN: {json.dumps(ss[:10],ensure_ascii=False)}

Struktur:
1. LAGE (Schadenslage, Gefahrenlage, Tendenz)
2. EIGENE KRÄFTE (Gliederung, Abschnitte, Stärke)
3. MASSNAHMEN (laufend + geplant)
4. OFFENE PUNKTE (Entscheidungsbedarfe, Lücken)
5. NÄCHSTE SCHRITTE (Zeitschiene)

Stand {now} · Als ENTWURF · Freigabe durch EL erforderlich.""",2500)

async def ki_pressemeldung(e:dict,sachstand:str)->dict:
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    text=await ki_call(SYS_S2,f"""PRESSEMITTEILUNG erstellen · Stand {now}

EINSATZ: {e.get('stichwort','–')} | {e.get('einsatzort','–')}
Tote: {e.get('tote',0)} | Verletzte: {e.get('verletzte',0)} | Vermisste: {e.get('vermisste',0)}
SACHSTAND: {sachstand}

Erstelle separate Bausteine, trenne mit === BAUSTEIN ===:

=== LAGE ===
Sachliche Lagebeschreibung (3-5 Sätze, für Öffentlichkeit)

=== MASSNAHMEN ===
Was unternimmt die Einsatzleitung (3-4 Sätze)

=== APPELL ===
Verhaltenshinweise für Bevölkerung (Bulletpoints)

=== VOLLTEXT ===
Vollständige Pressemitteilung inkl. Titel, Datum, alle Bausteine, Kontaktangaben-Platzhalter

Stand {now} · ENTWURF · Freigabe durch Pressestelle und EL.""",2000)
    result={"raw":text,"lage":"","massnahmen":"","appell":"","volltext":""}
    for sec in ["LAGE","MASSNAHMEN","APPELL","VOLLTEXT"]:
        m=re.search(rf"={3}[^=]*{sec}[^=]*={3}([\s\S]*?)(?:={3}|$)",text,re.IGNORECASE)
        if m: result[sec.lower()]=m.group(1).strip()
    return result

async def ki_meldung(e:dict,typ:str,empfaenger:str,zusatz:str="")->str:
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    return await ki_call(SYS_S2,f"""Erstelle {typ} für {empfaenger} · Stand {now}
Einsatz: {e.get('stichwort','–')} | {e.get('einsatzort','–')}
Tote: {e.get('tote',0)} | Verletzte: {e.get('verletzte',0)} | Lagestufe: {e.get('lagestufe','–')}
{zusatz}
Adressatengerecht · Als ENTWURF.""",1000)

async def ki_dokument_analyse(text:str,dateiname:str,kontext:dict)->dict:
    if not ANTHROPIC_KEY:
        return {"kategorie":"Information","prioritaet":"normal",
                "betreff":f"Dokument: {dateiname}","inhalt":text[:500],
                "ki_zusammenfassung":"KI nicht verfügbar.","geodaten":[]}
    try:
        import anthropic as _a
        raw=await ki_call(SYS_S2,
            f"Analysiere '{dateiname}' für S2-Tagebuch.\nKontext: {json.dumps(kontext,ensure_ascii=False)}\n\n{text[:3000]}\n\nAntworte NUR als JSON:\n"
            '{"kategorie":"Lage|Maßnahme|Meldung|Vordruck|Anforderung|Information","prioritaet":"kritisch|hoch|normal","betreff":"max 80 Zeichen","inhalt":"Zusammenfassung","ki_zusammenfassung":"2-3 Sätze Bedeutung","geodaten":[]}',
            600)
        return json.loads(re.sub(r"```json?\s*|\s*```","",raw).strip())
    except Exception as e:
        return {"kategorie":"Information","prioritaet":"normal","betreff":f"Dokument: {dateiname}","inhalt":text[:500],"ki_zusammenfassung":f"Analyse: {e}","geodaten":[]}

async def ki_analysebericht(text:str,titel:str)->dict:
    raw=await ki_call(SYS_S2,
        f"""Analysiere diesen Katastrophenschutzbericht: '{titel}'

{text[:5000]}

Antworte NUR als JSON:
{{"zusammenfassung":"3-5 Sätze was passiert ist","lektionen":"Liste der wichtigsten Lektionen (max 10)","empfehlungen":"Konkrete Empfehlungen für ähnliche Einsätze (max 8)","relevante_stichworte":"kommagetrennte Liste"}}""",
        1500)
    try: return json.loads(re.sub(r"```json?\s*|\s*```","",raw).strip())
    except: return {"zusammenfassung":raw[:500],"lektionen":"–","empfehlungen":"–","relevante_stichworte":"–"}

def _text_aus_bytes(content:bytes,filename:str)->str:
    suffix=Path(filename).suffix.lower()
    if suffix in (".txt",".md",".rst",".csv"): return content.decode("utf-8",errors="replace")[:6000]
    if suffix==".json":
        try: return json.dumps(json.loads(content.decode("utf-8",errors="replace")),ensure_ascii=False,indent=2)[:4000]
        except: return content.decode("utf-8",errors="replace")[:3000]
    if suffix==".pdf":
        try:
            import PyPDF2; reader=PyPDF2.PdfReader(io.BytesIO(content))
            return "\n\n".join(p.extract_text() or "" for p in reader.pages)[:6000]
        except ImportError: return f"[PDF: {filename} – pip install pypdf2]"
        except Exception as e: return f"[PDF-Fehler: {e}]"
    if suffix==".docx":
        try:
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(content)) as z: xml_data=z.read("word/document.xml")
            tree=ET.fromstring(xml_data)
            ns={"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return " ".join(t.text or "" for t in tree.findall(".//w:t",ns))[:6000]
        except Exception as e: return f"[Word-Fehler: {e}]"
    return content.decode("utf-8",errors="replace")[:3000]

# ═══════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════
def _token(data:dict)->str:
    exp=datetime.utcnow()+timedelta(minutes=TOKEN_MIN)
    return jwt.encode({**data,"exp":exp},SECRET_KEY,algorithm=ALGORITHM)

def get_user(tk:str=Depends(oauth2),db:Session=Depends(get_db))->User:
    exc=HTTPException(401,"Nicht autorisiert",headers={"WWW-Authenticate":"Bearer"})
    try:
        p=jwt.decode(tk,SECRET_KEY,algorithms=[ALGORITHM])
        u=db.query(User).filter(User.username==p.get("sub")).first()
        if not u or not u.active: raise exc
        return u
    except JWTError: raise exc

def req(*roles):
    def dep(u:User=Depends(get_user))->User:
        if u.role not in roles and u.role!="admin": raise HTTPException(403,"Keine Berechtigung")
        return u
    return dep

# ═══════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app):
    init_db()
    print(f"[OK] S2-LageLive R1 auf Port {PORT}")
    yield

app=FastAPI(title="S2-LageLive",version="R1",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
_fe=Path("frontend")
if _fe.exists() and any(_fe.iterdir()):
    app.mount("/static",StaticFiles(directory="frontend"),name="static")

@app.get("/health")
async def health(): return {"status":"ok","version":"R1","port":PORT,"time":datetime.now().isoformat()}

@app.websocket("/ws/{eid}")
async def ws_ep(ws:WebSocket,eid:int,token:Optional[str]=None):
    uid="anonym"
    if token:
        try:
            p=jwt.decode(token,SECRET_KEY,algorithms=[ALGORITHM]); uid=p.get("sub","anonym")
        except: pass
    await hub.connect(ws,eid,uid)
    try:
        while True:
            data=await ws.receive_text()
            try:
                msg=json.loads(data)
                if msg.get("type") in ("karten_objekt_neu","freihand"):
                    await hub.broadcast(eid,msg,exclude=ws)
            except: pass
    except WebSocketDisconnect: hub.disconnect(ws,eid)

# AUTH
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

@app.get("/api/auth/beamer/{beamer_token}")
async def beamer_login(beamer_token:str,db:Session=Depends(get_db)):
    e=db.query(Einsatz).filter(Einsatz.beamer_token==beamer_token).first()
    if not e: raise HTTPException(404,"Ungültiger Beamer-Token")
    token=_token({"sub":"beamer","role":"beamer","eid":e.id})
    return {"access_token":token,"token_type":"bearer","einsatz_id":e.id,
            "user":{"username":"beamer","role":"beamer","display_name":"Beamer"}}

@app.get("/api/rollen")
async def get_rollen(): return ROLES

# EINSATZ
@app.get("/api/einsaetze")
async def list_e(db:Session=Depends(get_db),u:User=Depends(get_user)):
    return [_einsatz_dict(e) for e in db.query(Einsatz).filter(Einsatz.archiviert==False).order_by(Einsatz.id.desc()).all()]

@app.post("/api/einsaetze")
async def create_e(data:dict,db:Session=Depends(get_db),u:User=Depends(req("el","s2","admin"))):
    e=Einsatz(kennung=data.get("kennung",f"E-{datetime.now():%Y%m%d-%H%M}"),
              stichwort=data.get("stichwort","–"),einsatzort=data.get("einsatzort","–"),
              lagestufe=data.get("lagestufe","Erstmeldung"),
              einsatzleiter=data.get("einsatzleiter",""),fuehrstelle=data.get("fuehrstelle",""),
              alarmierung=data.get("alarmierung",""),region_info=data.get("region_info",""),
              lat=data.get("lat",48.4732),lng=data.get("lng",7.9414),zoom=data.get("zoom",12),
              beamer_token=secrets.token_urlsafe(12))
    # Nächste Lagebesprechung
    intervall=data.get("lagebesprechung_intervall_min",60)
    if intervall>0:
        e.lagebesprechung_intervall_min=intervall
        e.naechste_lagebesprechung=datetime.utcnow()+timedelta(minutes=intervall)
    db.add(e); db.commit(); db.refresh(e)
    return _einsatz_dict(e)

@app.get("/api/einsaetze/{eid}")
async def get_e(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    return _einsatz_dict(e)

@app.patch("/api/einsaetze/{eid}")
async def patch_e(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    e=db.get(Einsatz,eid)
    if not e: raise HTTPException(404)
    for f in ["lagestufe","tote","verletzte","verschuettete","obdachlose","vermisste","gerettete",
              "stichwort","einsatzort","einsatzleiter","fuehrstelle","alarmierung","lat","lng","zoom",
              "region_info","lagebesprechung_intervall_min"]:
        if f in data: setattr(e,f,data[f])
    if "naechste_lagebesprechung" in data:
        e.naechste_lagebesprechung=datetime.fromisoformat(data["naechste_lagebesprechung"]) if data["naechste_lagebesprechung"] else None
    db.commit()
    await hub.broadcast(eid,{"type":"einsatz_update","einsatz_id":eid,**_einsatz_dict(e)})
    return _einsatz_dict(e)

# ADMIN: Einsatz zurücksetzen
@app.post("/api/einsaetze/{eid}/reset")
async def reset_e(eid:int,db:Session=Depends(get_db),u:User=Depends(req("admin"))):
    """Löscht alle Daten des Einsatzes außer dem Einsatz selbst"""
    for model in [TagebuchEintrag,KartenObjekt,InfrastrukturObjekt,Abschnitt,Kraft,Lagevortrag,Meldung,PresseMeldung]:
        db.query(model).filter(getattr(model,'einsatz_id')==eid).delete()
    e=db.get(Einsatz,eid)
    if e:
        e.tote=e.verletzte=e.verschuettete=e.obdachlose=e.vermisste=e.gerettete=0
        e.lagestufe="Erstmeldung"; e.naechste_lagebesprechung=None
    db.commit()
    await hub.broadcast(eid,{"type":"einsatz_reset","einsatz_id":eid})
    return {"ok":True,"message":"Einsatz zurückgesetzt"}

@app.post("/api/einsaetze/{eid}/archivieren")
async def archiv_e(eid:int,db:Session=Depends(get_db),u:User=Depends(req("admin","el"))):
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    e.archiviert=True
    e.aktiv=False
    db.commit()
    return {"ok":True}

# ÜBUNGSSZENARIO hochladen
@app.post("/api/einsaetze/{eid}/szenario")
async def upload_szenario(eid:int,file:UploadFile=File(...),
                           db:Session=Depends(get_db),u:User=Depends(req("admin","el","s2"))):
    content=await file.read()
    text=_text_aus_bytes(content,file.filename or "szenario")
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    # KI extrahiert Region + Einsatzdaten
    ki_info=await ki_call(SYS_S2,
        f"Extrahiere aus diesem Übungsszenario die wichtigsten Informationen.\n\n{text[:4000]}\n\n"
        "Antworte NUR als JSON:\n"
        '{"stichwort":"","einsatzort":"","region_beschreibung":"mehrzeiliger Text über die Region","schadenslage":"","besonderheiten":""}',
        800)
    try:
        info=json.loads(re.sub(r"```json?\s*|\s*```","",ki_info).strip())
        if info.get("stichwort"): e.stichwort=info["stichwort"]
        if info.get("einsatzort"): e.einsatzort=info["einsatzort"]
        e.region_info=info.get("region_beschreibung","")+"\n\nSchadenslage:\n"+info.get("schadenslage","")
        db.commit()
    except: e.region_info=text[:2000]; db.commit()
    await hub.broadcast(eid,{"type":"szenario_geladen","stichwort":e.stichwort,"einsatzort":e.einsatzort})
    return {"ok":True,"stichwort":e.stichwort,"einsatzort":e.einsatzort,"region_info":e.region_info}

# ABSCHNITTE
@app.get("/api/einsaetze/{eid}/abschnitte")
async def list_ab(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Abschnitt).filter(Abschnitt.einsatz_id==eid).all()
    return [{"id":a.id,"einsatz_id":a.einsatz_id,"nummer":a.nummer,"bezeichnung":a.bezeichnung,
             "aufgabe":a.aufgabe,"status":a.status,"abschnittsleiter_name":a.abschnittsleiter_name,
             "abschnittsleiter_funk":a.abschnittsleiter_funk,"abschnittsleiter_tel":a.abschnittsleiter_tel,
             "staerke":a.staerke,"lat":a.lat,"lng":a.lng,"parent_id":a.parent_id} for a in rows]

@app.post("/api/einsaetze/{eid}/abschnitte")
async def create_ab(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    a=Abschnitt(einsatz_id=eid,**{k:data[k] for k in ["nummer","bezeichnung","aufgabe","status",
        "abschnittsleiter_name","abschnittsleiter_funk","abschnittsleiter_tel",
        "staerke","lat","lng","parent_id"] if k in data})
    db.add(a); db.commit(); db.refresh(a)
    row={"id":a.id,"einsatz_id":a.einsatz_id,"nummer":a.nummer,"bezeichnung":a.bezeichnung,
         "aufgabe":a.aufgabe,"status":a.status,"abschnittsleiter_name":a.abschnittsleiter_name,
         "abschnittsleiter_funk":a.abschnittsleiter_funk,"abschnittsleiter_tel":a.abschnittsleiter_tel,
         "staerke":a.staerke,"lat":a.lat,"lng":a.lng,"parent_id":a.parent_id}
    await hub.broadcast(eid,{"type":"abschnitt_neu","abschnitt":row})
    return row

@app.patch("/api/einsaetze/{eid}/abschnitte/{aid}")
async def patch_ab(eid:int,aid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    a=db.get(Abschnitt,aid)

    if not a: raise HTTPException(404)
    for f in ["bezeichnung","aufgabe","status","abschnittsleiter_name","abschnittsleiter_funk",
              "abschnittsleiter_tel","staerke","lat","lng"]:
        if f in data: setattr(a,f,data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"abschnitt_update","aid":aid,**data})
    return {"ok":True}

@app.delete("/api/einsaetze/{eid}/abschnitte/{aid}")
async def del_ab(eid:int,aid:int,db:Session=Depends(get_db),u:User=Depends(req("el","s2","admin"))):
    a=db.get(Abschnitt,aid)

    if not a: raise HTTPException(404)
    db.delete(a); db.commit()
    await hub.broadcast(eid,{"type":"abschnitt_delete","aid":aid})
    return {"ok":True}

# KRÄFTE
@app.get("/api/einsaetze/{eid}/kraefte")
async def list_krf(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Kraft).filter(Kraft.einsatz_id==eid).all()
    return [{"id":k.id,"einsatz_id":k.einsatz_id,"abschnitt_id":k.abschnitt_id,
             "organisation":k.organisation,"einheit":k.einheit,"funktion":k.funktion,
             "staerke":k.staerke,"fahrzeuge":k.fahrzeuge,"status":k.status,
             "ansprechpartner":k.ansprechpartner,"funk":k.funk,"lat":k.lat,"lng":k.lng} for k in rows]

@app.post("/api/einsaetze/{eid}/kraefte")
async def create_krf(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    k=Kraft(einsatz_id=eid,**{f:data[f] for f in ["organisation","einheit","funktion","staerke",
        "fahrzeuge","status","ansprechpartner","funk","lat","lng","abschnitt_id"] if f in data})
    db.add(k); db.commit(); db.refresh(k)
    row={"id":k.id,"organisation":k.organisation,"einheit":k.einheit,"funktion":k.funktion,
         "staerke":k.staerke,"fahrzeuge":k.fahrzeuge,"status":k.status,
         "ansprechpartner":k.ansprechpartner,"funk":k.funk,"lat":k.lat,"lng":k.lng}
    await hub.broadcast(eid,{"type":"kraft_neu","kraft":row})
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
async def del_krf(eid:int,kid:int,db:Session=Depends(get_db),u:User=Depends(req("el","s2","admin"))):
    k=db.get(Kraft,kid)

    if not k: raise HTTPException(404)
    db.delete(k); db.commit()
    await hub.broadcast(eid,{"type":"kraft_delete","kid":kid})
    return {"ok":True}

# KARTEN-OBJEKTE (TZ + Freihand)
@app.get("/api/einsaetze/{eid}/karte")
async def list_karte(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(KartenObjekt).filter(KartenObjekt.einsatz_id==eid,KartenObjekt.aktiv==True).all()
    return [{"id":o.id,"typ":o.typ,"tz_kategorie":o.tz_kategorie,"tz_symbol":o.tz_symbol,
             "farbe":o.farbe,"titel":o.titel,"beschreibung":o.beschreibung,
             "daten_json":o.daten_json,"lat":o.lat,"lng":o.lng,"geo_json":o.geo_json,
             "radius_m":o.radius_m,"erstellt_von":o.erstellt_von,
             "erstellt_dt":o.erstellt_dt.isoformat() if o.erstellt_dt else None,
             "abschnitt_id":o.abschnitt_id} for o in rows]

@app.post("/api/einsaetze/{eid}/karte")
async def add_karte(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    o=KartenObjekt(einsatz_id=eid,typ=data.get("typ","punkt"),
                   tz_kategorie=data.get("tz_kategorie","allgemein"),
                   tz_symbol=data.get("tz_symbol","kreis"),
                   farbe=data.get("farbe","#C8000A"),titel=data.get("titel",""),
                   beschreibung=data.get("beschreibung",""),
                   daten_json=json.dumps(data.get("daten",{}),ensure_ascii=False),
                   lat=data.get("lat"),lng=data.get("lng"),
                   geo_json=data.get("geo_json",""),radius_m=data.get("radius_m",0),
                   erstellt_von=u.display_name or u.username,
                   abschnitt_id=data.get("abschnitt_id"))
    db.add(o); db.commit(); db.refresh(o)
    msg={"type":"karten_objekt_neu","id":o.id,"typ":o.typ,"tz_kategorie":o.tz_kategorie,
         "tz_symbol":o.tz_symbol,"farbe":o.farbe,"titel":o.titel,"beschreibung":o.beschreibung,
         "daten_json":o.daten_json,"lat":o.lat,"lng":o.lng,"geo_json":o.geo_json,
         "radius_m":o.radius_m,"erstellt_von":o.erstellt_von,"abschnitt_id":o.abschnitt_id,
         "zeit":datetime.now().strftime("%H:%M")}
    await hub.broadcast(eid,msg)
    return {"id":o.id}

@app.patch("/api/einsaetze/{eid}/karte/{oid}")
async def patch_karte(eid:int,oid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    o=db.get(KartenObjekt,oid)

    if not o: raise HTTPException(404)
    for f in ["titel","beschreibung","farbe","daten_json","lat","lng","geo_json","radius_m","abschnitt_id"]:
        if f in data: setattr(o,f,data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"karten_objekt_update","id":oid,**data})
    return {"ok":True}

@app.delete("/api/einsaetze/{eid}/karte/{oid}")
async def del_karte(eid:int,oid:int,db:Session=Depends(get_db),u:User=Depends(req("s2","el","admin"))):
    o=db.get(KartenObjekt,oid)

    if not o: raise HTTPException(404)
    o.aktiv=False; db.commit()
    await hub.broadcast(eid,{"type":"karten_objekt_delete","id":oid})
    return {"ok":True}

# INFRASTRUKTUR (OSM + Pflege)
@app.get("/api/einsaetze/{eid}/infrastruktur")
async def list_infra(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(InfrastrukturObjekt).filter(InfrastrukturObjekt.einsatz_id==eid).all()
    return [{"id":i.id,"osm_id":i.osm_id,"typ":i.typ,"name":i.name,"adresse":i.adresse,
             "lat":i.lat,"lng":i.lng,"status":i.status,
             "kapazitaet_gesamt":i.kapazitaet_gesamt,"kapazitaet_frei":i.kapazitaet_frei,
             "notizen":i.notizen,"letzte_meldung":i.letzte_meldung.isoformat() if i.letzte_meldung else None,
             "gemeldet_von":i.gemeldet_von} for i in rows]

@app.post("/api/einsaetze/{eid}/infrastruktur")
async def create_infra(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    i=InfrastrukturObjekt(einsatz_id=eid,osm_id=data.get("osm_id"),typ=data.get("typ","krankenhaus"),
                          name=data.get("name",""),adresse=data.get("adresse",""),
                          lat=data["lat"],lng=data["lng"],status=data.get("status","unbekannt"),
                          kapazitaet_gesamt=data.get("kapazitaet_gesamt",0),
                          kapazitaet_frei=data.get("kapazitaet_frei",0),
                          notizen=data.get("notizen",""),
                          letzte_meldung=datetime.utcnow() if data.get("notizen") else None,
                          gemeldet_von=u.display_name or u.username)
    db.add(i); db.commit(); db.refresh(i)
    return {"id":i.id}

@app.patch("/api/einsaetze/{eid}/infrastruktur/{iid}")
async def patch_infra(eid:int,iid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    i=db.get(InfrastrukturObjekt,iid)

    if not i: raise HTTPException(404)
    for f in ["status","kapazitaet_gesamt","kapazitaet_frei","notizen"]:
        if f in data: setattr(i,f,data[f])
    i.letzte_meldung=datetime.utcnow(); i.gemeldet_von=u.display_name or u.username
    db.commit()
    await hub.broadcast(eid,{"type":"infrastruktur_update","iid":iid,
                              "name":i.name,"status":i.status,"kapazitaet_frei":i.kapazitaet_frei})
    return {"ok":True}

# TAGEBUCH
@app.get("/api/einsaetze/{eid}/tagebuch")
async def list_tb(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id.asc()).all()
    return [{"id":r.id,"einsatz_id":r.einsatz_id,"author_name":r.author_name,"author_role":r.author_role,
             "eingang_dt":r.eingang_dt.isoformat() if r.eingang_dt else None,"kategorie":r.kategorie,
             "prioritaet":r.prioritaet,"betreff":r.betreff,"inhalt":r.inhalt,"quelle":r.quelle,"an":r.an,
             "datei_name":r.datei_name,"ki_analyse":r.ki_analyse,
             "entry_hash":r.entry_hash,"freigegeben":r.freigegeben,"freigabe_von":r.freigabe_von} for r in rows]

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
async def freigeben_tb(eid:int,tid:int,db:Session=Depends(get_db),u:User=Depends(req("el","s2","admin"))):
    r=db.query(TagebuchEintrag).filter(TagebuchEintrag.id==tid,TagebuchEintrag.einsatz_id==eid).first()
    if not r: raise HTTPException(404)
    r.freigegeben=True; r.freigabe_von=u.display_name; db.commit()
    return {"ok":True}

@app.get("/api/einsaetze/{eid}/tagebuch/verify")
async def verify_tb(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id.asc()).all()
    fehler=[]
    for i,r in enumerate(rows):
        exp=rows[i-1].entry_hash if i>0 else "GENESIS"
        if r.prev_hash!=exp or r.entry_hash!=r.berechne_hash():
            fehler.append({"id":r.id,"betreff":r.betreff})
    return {"valid":len(fehler)==0,"eintraege":len(rows),"fehler":fehler}

@app.get("/api/einsaetze/{eid}/export/tagebuch")
async def export_tb(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    rows=db.query(TagebuchEintrag).filter(TagebuchEintrag.einsatz_id==eid).order_by(TagebuchEintrag.id.asc()).all()
    lines=["="*72,f"EINSATZTAGEBUCH · {e.kennung} · {e.stichwort}",
           f"Stand: {datetime.now():%d.%m.%Y %H:%M:%S}",f"Einträge gesamt: {len(rows)}","="*72,""]
    for r in rows:
        dt=r.eingang_dt.strftime("%d.%m.%Y %H:%M") if r.eingang_dt else "–"
        fg=" [FREIGEGEBEN von "+r.freigabe_von+"]" if r.freigegeben else ""
        lines+=[f"#{r.id:04d} | {dt} | {r.kategorie.upper()} | Prio: {r.prioritaet.upper()}{fg}",
                f"An:      {r.an or '–'}",f"Betreff: {r.betreff}",
                f"Autor:   {r.author_name} ({ROLES.get(r.author_role,{}).get('name',r.author_role)})",
                f"Quelle:  {r.quelle}",
                *([f"Datei:   {r.datei_name}"] if r.datei_name else []),"",
                r.inhalt or "–","",
                f"HASH:    {r.entry_hash or '–'}",f"PREV:    {r.prev_hash or '–'}","─"*60,""]
    path=f"exports/tagebuch_{eid}_{datetime.now():%Y%m%d_%H%M%S}.txt"
    Path(path).write_text("\n".join(lines),encoding="utf-8")
    return FileResponse(path,media_type="text/plain; charset=utf-8",
                        filename=f"Tagebuch_{e.kennung}_{datetime.now():%Y%m%d}.txt")

# UPLOAD VORDRUCKE / MELDUNGEN
@app.post("/api/einsaetze/{eid}/upload")
async def upload_dokument(eid:int,file:UploadFile=File(...),
                           kategorie:str=Form("Vordruck"),prioritaet:str=Form("normal"),
                           db:Session=Depends(get_db),u:User=Depends(get_user)):
    content=await file.read()
    safe=re.sub(r"[^\w.\-]","_",file.filename or "datei")
    path=f"uploads/{eid}_{datetime.now():%Y%m%d_%H%M%S}_{safe}"
    Path(path).write_bytes(content)
    text=_text_aus_bytes(content,file.filename or "")
    e=db.get(Einsatz,eid)
    ctx={"stichwort":e.stichwort if e else "–","einsatzort":e.einsatzort if e else "–"}
    await hub.broadcast(eid,{"type":"upload_eingang","datei":file.filename,"status":"analysiere…"})
    asyncio.create_task(_verarbeite_upload(eid,file.filename or safe,text,path,kategorie,prioritaet,u.display_name or u.username,u.role,ctx))
    return {"datei":file.filename,"status":"wird verarbeitet"}

async def _verarbeite_upload(eid,filename,text,pfad,kategorie,prioritaet,author_name,author_role,ctx):
    try:
        analyse=await ki_dokument_analyse(text,filename,ctx)
        with Session(engine) as db:
            r=TagebuchEintrag(einsatz_id=eid,author_name=author_name,author_role=author_role,
                              kategorie=analyse.get("kategorie",kategorie),
                              prioritaet=analyse.get("prioritaet",prioritaet),
                              betreff=analyse.get("betreff",f"Dokument: {filename}"),
                              inhalt=analyse.get("inhalt",text[:800]),
                              quelle=f"Upload: {filename}",datei_pfad=pfad,datei_name=filename,
                              ki_analyse=analyse.get("ki_zusammenfassung",""),
                              prev_hash=_prev_hash(db,eid))
            db.add(r); db.flush(); r.entry_hash=r.berechne_hash(); db.commit(); db.refresh(r)
        msg={"type":"tagebuch_eintrag","id":r.id,"einsatz_id":eid,
             "kategorie":r.kategorie,"prioritaet":r.prioritaet,"betreff":r.betreff,
             "inhalt":r.inhalt[:300],"author":r.author_name,"author_name":r.author_name,
             "author_role":r.author_role,"quelle":r.quelle,"datei_name":filename,
             "ki_analyse":r.ki_analyse,"zeit":r.eingang_dt.strftime("%d.%m. %H:%M"),
             "hash":(r.entry_hash or "")[:12]+"…","entry_hash":r.entry_hash,
             "freigegeben":False,"eingang_dt":r.eingang_dt.isoformat(),"an":""}
        await hub.broadcast(eid,msg)
        await hub.broadcast(eid,{"type":"upload_fertig","datei":filename,
                                  "ki_kurz":analyse.get("ki_zusammenfassung","Verarbeitet")})
    except Exception as ex:
        print(f"[UPLOAD] Fehler {filename}: {ex}")

# KI-ANALYSE (Lageeingabe)
@app.post("/api/einsaetze/{eid}/ki/analyse")
async def ki_analyse(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s2","el","admin"))):
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    for f in ["tote","verletzte","verschuettete","obdachlose","vermisste"]:
        try:
            if data.get(f) is not None: setattr(e,f,int(data[f]))
        except: pass
    if data.get("lagestufe"): e.lagestufe=data["lagestufe"]
    db.commit()
    now=f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    outputs=data.get("outputs",["Aktuelle Lagebeschreibung"])
    text=await ki_call(SYS_S2,
        f"LAGEINFORMATION STAND {now}\nStichwort: {data.get('stichwort','–')} | Ort: {data.get('einsatzort','–')} | Stufe: {data.get('lagestufe','–')}\n"
        f"OPFER: Tote={data.get('tote','–')} Verl={data.get('verletzte','–')} Versch={data.get('verschuettete','–')} Obdachlos={data.get('obdachlose','–')}\n"
        f"SCHADENSLAGE:\n{data.get('schadenslage','–')}\nKRÄFTE:\n{data.get('kraefte','–')}\nGEO: {data.get('geoLage','–')}\nWETTER: {data.get('temperatur','–')}\n\n"
        f"AUSGABEN: {', '.join(outputs)}\nErstelle alle Produkte mit === PRODUKTNAME === als Überschrift. Stand {now}.",3500)
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

# LAGEVORTRAG
@app.get("/api/einsaetze/{eid}/lagevortraege")
async def list_lv(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Lagevortrag).filter(Lagevortrag.einsatz_id==eid).order_by(Lagevortrag.revision.desc()).all()
    return [{"id":r.id,"revision":r.revision,"erstellt_dt":r.erstellt_dt.isoformat() if r.erstellt_dt else None,
             "erstellt_von":r.erstellt_von,"inhalt":r.inhalt,"freigegeben":r.freigegeben,
             "ist_lagebesprechung":r.ist_lagebesprechung,
             "lagebesprechung_dt":r.lagebesprechung_dt.isoformat() if r.lagebesprechung_dt else None,
             "naechster_dt":r.naechster_dt.isoformat() if r.naechster_dt else None,
             "intervall_min":r.intervall_min} for r in rows]

@app.post("/api/einsaetze/{eid}/lagevortraege/generieren")
async def gen_lv(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s2","el","admin"))):
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    abschnitte=[{"nummer":a.nummer,"bezeichnung":a.bezeichnung,"abschnittsleiter_name":a.abschnittsleiter_name,"abschnittsleiter_funk":a.abschnittsleiter_funk}
                for a in db.query(Abschnitt).filter(Abschnitt.einsatz_id==eid).all()]
    kraefte=[{"organisation":k.organisation,"einheit":k.einheit,"funktion":k.funktion,"staerke":k.staerke}
             for k in db.query(Kraft).filter(Kraft.einsatz_id==eid).all()]
    ss=[{"tz_kategorie":o.tz_kategorie,"titel":o.titel,"beschreibung":o.beschreibung[:80]}
        for o in db.query(KartenObjekt).filter(KartenObjekt.einsatz_id==eid,
                                                KartenObjekt.tz_kategorie.in_(["schadensstelle","brand","einsturz","ueberflutung"])).all()]
    inhalt=await ki_lagevortrag(_einsatz_dict(e),abschnitte,kraefte,ss)
    rev_max=db.query(Lagevortrag).filter(Lagevortrag.einsatz_id==eid).count()
    intervall=data.get("intervall_min",e.lagebesprechung_intervall_min or 60)
    ist_lb=data.get("ist_lagebesprechung",False)
    naechster=datetime.utcnow()+timedelta(minutes=intervall) if intervall>0 else None
    lv=Lagevortrag(einsatz_id=eid,revision=rev_max+1,erstellt_von=u.display_name or u.username,
                   inhalt=inhalt,intervall_min=intervall,naechster_dt=naechster,
                   ist_lagebesprechung=ist_lb,
                   lagebesprechung_dt=datetime.utcnow() if ist_lb else None)
    db.add(lv)
    # Nächste Lagebesprechung im Einsatz aktualisieren
    if naechster: e.naechste_lagebesprechung=naechster
    db.commit(); db.refresh(lv)
    # Tagebucheintrag
    r=TagebuchEintrag(einsatz_id=eid,author_name=u.display_name or u.username,author_role=u.role,
                      kategorie="Lage",prioritaet="hoch" if ist_lb else "normal",
                      betreff=f"{'🔔 LAGEBESPRECHUNG' if ist_lb else 'Lagevortrag'} Rev. {lv.revision} · {datetime.now():%H:%M Uhr}",
                      inhalt=inhalt[:1500],quelle="KI-Lagevortrag",prev_hash=_prev_hash(db,eid))
    db.add(r); db.flush(); r.entry_hash=r.berechne_hash(); db.commit()
    await hub.broadcast(eid,{"type":"lagevortrag_neu","revision":lv.revision,
                              "ist_lagebesprechung":ist_lb,
                              "naechster_dt":lv.naechster_dt.isoformat() if lv.naechster_dt else None,
                              "naechste_lagebesprechung":e.naechste_lagebesprechung.isoformat() if e.naechste_lagebesprechung else None})
    return {"id":lv.id,"revision":lv.revision,"inhalt":inhalt,
            "naechster_dt":lv.naechster_dt.isoformat() if lv.naechster_dt else None}

@app.post("/api/einsaetze/{eid}/lagevortraege/{lid}/freigeben")
async def freigeben_lv(eid:int,lid:int,db:Session=Depends(get_db),u:User=Depends(req("el","admin"))):
    lv=db.get(Lagevortrag,lid)

    if not lv: raise HTTPException(404)
    lv.freigegeben=True; lv.freigabe_von=u.display_name; db.commit()
    await hub.broadcast(eid,{"type":"lagevortrag_freigegeben","lid":lid,"revision":lv.revision})
    return {"ok":True}

# MELDUNGEN
@app.get("/api/einsaetze/{eid}/meldungen")
async def list_ml(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(Meldung).filter(Meldung.einsatz_id==eid).order_by(Meldung.id.desc()).all()
    return [{"id":m.id,"typ":m.typ,"empfaenger":m.empfaenger,"inhalt":m.inhalt,
             "ki_generiert":m.ki_generiert,"datei_name":m.datei_name,
             "versendet":m.versendet,"erstellt_dt":m.erstellt_dt.isoformat() if m.erstellt_dt else None,
             "erstellt_von":m.erstellt_von} for m in rows]

@app.post("/api/einsaetze/{eid}/meldungen")
async def create_ml(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(get_user)):
    m=Meldung(einsatz_id=eid,typ=data.get("typ","Lagemeldung"),
              empfaenger=data.get("empfaenger",""),inhalt=data.get("inhalt",""),
              ki_generiert=False,erstellt_von=u.display_name or u.username)
    db.add(m); db.commit(); db.refresh(m)
    return {"id":m.id}

@app.post("/api/einsaetze/{eid}/meldungen/generieren")
async def gen_ml(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s2","s5","el","presse","admin"))):
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    inhalt=await ki_meldung(_einsatz_dict(e),data.get("typ","Lagemeldung"),
                             data.get("empfaenger","–"),data.get("zusatz",""))
    m=Meldung(einsatz_id=eid,typ=data.get("typ","Lagemeldung"),
              empfaenger=data.get("empfaenger",""),inhalt=inhalt,
              ki_generiert=True,erstellt_von=u.display_name or u.username)
    db.add(m); db.commit(); db.refresh(m)
    await hub.broadcast(eid,{"type":"meldung_neu","meldung_id":m.id,"typ":m.typ,"empfaenger":m.empfaenger})
    return {"id":m.id,"inhalt":inhalt}

@app.post("/api/einsaetze/{eid}/meldungen/{mid}/versendet")
async def mark_versendet(eid:int,mid:int,db:Session=Depends(get_db),u:User=Depends(req("el","s2","admin"))):
    m=db.get(Meldung,mid)

    if not m: raise HTTPException(404)
    m.versendet=True; m.versendet_dt=datetime.utcnow(); db.commit()
    return {"ok":True}

# PRESSEMELDUNGEN (S5 / Presse)
@app.get("/api/einsaetze/{eid}/presse")
async def list_presse(eid:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(PresseMeldung).filter(PresseMeldung.einsatz_id==eid).order_by(PresseMeldung.revision.desc()).all()
    return [{"id":p.id,"revision":p.revision,"titel":p.titel,"inhalt":p.inhalt,
             "baustein_lage":p.baustein_lage,"baustein_massnahmen":p.baustein_massnahmen,
             "baustein_appell":p.baustein_appell,"bilder_json":p.bilder_json,
             "ki_generiert":p.ki_generiert,"freigegeben":p.freigegeben,
             "erstellt_dt":p.erstellt_dt.isoformat() if p.erstellt_dt else None,
             "erstellt_von":p.erstellt_von} for p in rows]

@app.post("/api/einsaetze/{eid}/presse/generieren")
async def gen_presse(eid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s5","presse","el","admin"))):
    e=db.get(Einsatz,eid)

    if not e: raise HTTPException(404)
    bausteine=await ki_pressemeldung(_einsatz_dict(e),data.get("sachstand",""))
    rev_max=db.query(PresseMeldung).filter(PresseMeldung.einsatz_id==eid).count()
    p=PresseMeldung(einsatz_id=eid,revision=rev_max+1,
                    titel=f"Pressemitteilung {e.kennung} · Stand {datetime.now():%d.%m.%Y %H:%M}",
                    inhalt=bausteine.get("volltext",""),
                    baustein_lage=bausteine.get("lage",""),
                    baustein_massnahmen=bausteine.get("massnahmen",""),
                    baustein_appell=bausteine.get("appell",""),
                    ki_generiert=True,erstellt_von=u.display_name or u.username)
    db.add(p); db.commit(); db.refresh(p)
    await hub.broadcast(eid,{"type":"presse_neu","revision":p.revision})
    return {"id":p.id,"revision":p.revision,"bausteine":bausteine}

@app.patch("/api/einsaetze/{eid}/presse/{pid}")
async def patch_presse(eid:int,pid:int,data:dict,db:Session=Depends(get_db),u:User=Depends(req("s5","presse","el","admin"))):
    p=db.get(PresseMeldung,pid)

    if not p: raise HTTPException(404)
    for f in ["titel","inhalt","baustein_lage","baustein_massnahmen","baustein_appell"]:
        if f in data: setattr(p,f,data[f])
    db.commit(); return {"ok":True}

@app.post("/api/einsaetze/{eid}/presse/{pid}/freigeben")
async def freigeben_presse(eid:int,pid:int,db:Session=Depends(get_db),u:User=Depends(req("el","admin"))):
    p=db.get(PresseMeldung,pid)

    if not p: raise HTTPException(404)
    p.freigegeben=True; db.commit()
    await hub.broadcast(eid,{"type":"presse_freigegeben","pid":pid})
    return {"ok":True}

@app.post("/api/einsaetze/{eid}/presse/{pid}/bild")
async def upload_pressebild(eid:int,pid:int,file:UploadFile=File(...),
                              db:Session=Depends(get_db),u:User=Depends(req("s5","presse","el","admin"))):
    p=db.get(PresseMeldung,pid)

    if not p: raise HTTPException(404)
    safe=re.sub(r"[^\w.\-]","_",file.filename or "bild")
    path=f"uploads/presse_{pid}_{datetime.now():%Y%m%d_%H%M%S}_{safe}"
    Path(path).write_bytes(await file.read())
    bilder=json.loads(p.bilder_json or "[]")
    bilder.append({"pfad":path,"name":file.filename,"dt":datetime.now().isoformat()})
    p.bilder_json=json.dumps(bilder,ensure_ascii=False); db.commit()
    return {"pfad":path,"name":file.filename}

@app.get("/api/einsaetze/{eid}/presse/{pid}/bild/{idx}")
async def get_pressebild(eid:int,pid:int,idx:int,db:Session=Depends(get_db),u:User=Depends(get_user)):
    p=db.get(PresseMeldung,pid)

    if not p: raise HTTPException(404)
    bilder=json.loads(p.bilder_json or "[]")
    if idx>=len(bilder): raise HTTPException(404)
    path=bilder[idx]["pfad"]
    if not Path(path).exists(): raise HTTPException(404)
    return FileResponse(path)

# ANALYSEBERICHTE
@app.get("/api/analyseberichte")
async def list_ab(db:Session=Depends(get_db),u:User=Depends(get_user)):
    rows=db.query(AnalyseBericht).order_by(AnalyseBericht.id.desc()).all()
    return [{"id":a.id,"titel":a.titel,"kategorie":a.kategorie,"region":a.region,
             "datum_ereignis":a.datum_ereignis,"ki_zusammenfassung":a.ki_zusammenfassung,
             "ki_empfehlungen":a.ki_empfehlungen,"hochgeladen_dt":a.hochgeladen_dt.isoformat() if a.hochgeladen_dt else None} for a in rows]

@app.post("/api/analyseberichte")
async def upload_analyse(file:UploadFile=File(...),titel:str=Form(""),
                          kategorie:str=Form("Katastrophe"),region:str=Form(""),datum:str=Form(""),
                          db:Session=Depends(get_db),u:User=Depends(req("admin","el","s2"))):
    content=await file.read()
    safe=re.sub(r"[^\w.\-]","_",file.filename or "bericht")
    path=f"uploads/analyse/{datetime.now():%Y%m%d_%H%M%S}_{safe}"
    Path(path).write_bytes(content)
    text=_text_aus_bytes(content,file.filename or "")
    ki_info=await ki_analysebericht(text,titel or file.filename or "Analysebericht")
    a=AnalyseBericht(titel=titel or file.filename or "Analysebericht",
                     kategorie=kategorie,region=region,datum_ereignis=datum,
                     datei_pfad=path,inhalt_text=text[:5000],
                     ki_zusammenfassung=ki_info.get("zusammenfassung",""),
                     ki_empfehlungen=str(ki_info.get("empfehlungen","")),
                     hochgeladen_von=u.display_name or u.username)
    db.add(a); db.commit(); db.refresh(a)
    return {"id":a.id,"ki_zusammenfassung":a.ki_zusammenfassung,"ki_empfehlungen":a.ki_empfehlungen}

@app.get("/api/analyseberichte/{aid}/empfehlungen")
async def get_empfehlungen(aid:int,kontext:str="",db:Session=Depends(get_db),u:User=Depends(get_user)):
    a=db.get(AnalyseBericht,aid)

    if not a: raise HTTPException(404)
    if not kontext: return {"empfehlungen":a.ki_empfehlungen}
    # Kontext-spezifische Empfehlungen
    text=await ki_call(SYS_S2,
        f"Aus diesem Analysebericht ({a.titel}):\n{a.ki_zusammenfassung}\n\nEmpfehlungen:\n{a.ki_empfehlungen}\n\n"
        f"Formuliere 5 spezifische Handlungsempfehlungen für folgende Situation:\n{kontext}",800)
    return {"empfehlungen":text}

# BEAMER
@app.get("/api/beamer/{beamer_token}/lage")
async def beamer_lage(beamer_token:str,db:Session=Depends(get_db)):
    e=db.query(Einsatz).filter(Einsatz.beamer_token==beamer_token).first()
    if not e: raise HTTPException(404)
    lv=db.query(Lagevortrag).filter(Lagevortrag.einsatz_id==e.id,Lagevortrag.freigegeben==True).order_by(Lagevortrag.revision.desc()).first()
    karte_count=db.query(KartenObjekt).filter(KartenObjekt.einsatz_id==e.id,KartenObjekt.aktiv==True).count()
    return {**_einsatz_dict(e),"kraefte_count":db.query(Kraft).filter(Kraft.einsatz_id==e.id).count(),
            "karte_count":karte_count,"letzter_lagevortrag":(lv.inhalt or "")[:600] if lv else None,
            "lv_revision":lv.revision if lv else 0,"lv_dt":lv.erstellt_dt.isoformat() if lv else None}

# FRONTEND
@app.get("/")
async def root():
    for p in [Path("frontend/index.html"),Path("/app/frontend/index.html")]:
        if p.exists(): return FileResponse(str(p),media_type="text/html")
    return JSONResponse({"status":"S2-LageLive R1","health":"/health"})

@app.get("/{path:path}")
async def spa(path:str):
    for base in [Path("frontend"),Path("/app/frontend")]:
        fp=base/path
        if fp.exists() and fp.is_file(): return FileResponse(str(fp))
    for p in [Path("frontend/index.html"),Path("/app/frontend/index.html")]:
        if p.exists(): return FileResponse(str(p),media_type="text/html")
    raise HTTPException(404)

if __name__=="__main__":
    import uvicorn
    print(f"[START] uvicorn 0.0.0.0:{PORT}")
    uvicorn.run(app,host="0.0.0.0",port=PORT,log_level="info")
