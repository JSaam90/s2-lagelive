"""
S2-LageLive v3.2
Fix: passlib durch direktes bcrypt ersetzt (kein Versionskonflikt mehr)
"""
import os, json, hashlib, asyncio, re, io, zipfile
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Dict, List

for _d in ["data", "exports", "uploads", "frontend"]:
    Path(_d).mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, UploadFile, File
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy import (
    Column, Integer, String, Text, DateTime,
    Boolean, Float, ForeignKey, create_engine, event
)
from sqlalchemy.orm import DeclarativeBase, Session

# ── bcrypt direkt — kein passlib ────────────────────────────────
import bcrypt as _bcrypt

def hash_password(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return _bcrypt.checkpw(pw.encode(), hashed.encode())

# ── Config ───────────────────────────────────────────────────────
SECRET_KEY    = os.environ.get("SECRET_KEY", "s2-dev-key-bitte-in-railway-env-setzen-min32!")
ALGORITHM     = "HS256"
TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
DATABASE_URL  = os.environ.get("DATABASE_URL", "sqlite:///./data/s2.db")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PORT          = int(os.environ.get("PORT", "8000"))

print(f"[BOOT] PORT={PORT} | KI={'ja' if ANTHROPIC_KEY else 'kein Key'} | DB={DATABASE_URL}")

# ════════════════════════════════════════════════════════════════
# DATENBANK
# ════════════════════════════════════════════════════════════════

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True)
    username     = Column(String(64), unique=True, nullable=False)
    hashed_pw    = Column(String(256), nullable=False)
    display_name = Column(String(128), default="")
    role         = Column(String(32), default="extern")
    active       = Column(Boolean, default=True)

class Einsatz(Base):
    __tablename__ = "einsaetze"
    id            = Column(Integer, primary_key=True)
    kennung       = Column(String(64), default="")
    stichwort     = Column(String(128), default="")
    einsatzort    = Column(Text, default="")
    alarmzeit     = Column(DateTime, default=datetime.utcnow)
    lagestufe     = Column(String(64), default="Erstmeldung")
    aktiv         = Column(Boolean, default=True)
    tote          = Column(Integer, default=0)
    verletzte     = Column(Integer, default=0)
    verschuettete = Column(Integer, default=0)
    obdachlose    = Column(Integer, default=0)

class TagebuchEintrag(Base):
    __tablename__ = "tagebuch"
    id           = Column(Integer, primary_key=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"), nullable=False)
    author_name  = Column(String(128), default="")
    author_role  = Column(String(32), default="")
    eingang_dt   = Column(DateTime, default=datetime.utcnow)
    kategorie    = Column(String(64), default="Meldung")
    prioritaet   = Column(String(16), default="normal")
    betreff      = Column(String(256), default="")
    inhalt       = Column(Text, default="")
    quelle       = Column(String(128), default="Manuell")
    vordruck_nr  = Column(String(64), nullable=True)
    vordruck_von = Column(String(128), nullable=True)
    vordruck_an  = Column(String(128), nullable=True)
    prev_hash    = Column(String(64), default="GENESIS")
    entry_hash   = Column(String(64), nullable=True)
    freigegeben  = Column(Boolean, default=False)
    freigabe_von = Column(String(128), nullable=True)

    def berechne_hash(self) -> str:
        data = json.dumps({
            "id": self.id, "eid": self.einsatz_id,
            "author": self.author_name, "dt": str(self.eingang_dt),
            "kat": self.kategorie, "betreff": self.betreff,
            "inhalt": (self.inhalt or "")[:300], "prev": self.prev_hash,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def verifiziere(self, vorheriger=None) -> bool:
        exp = vorheriger.entry_hash if vorheriger else "GENESIS"
        return self.prev_hash == exp and self.entry_hash == self.berechne_hash()

class KartenZeichen(Base):
    __tablename__ = "karten_zeichen"
    id           = Column(Integer, primary_key=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"))
    tz_typ       = Column(String(128), default="Marker")
    farbe        = Column(String(16), default="#C8000A")
    emoji        = Column(String(8), default="📍")
    lat          = Column(Float, nullable=False)
    lng          = Column(Float, nullable=False)
    beschreibung = Column(Text, default="")
    erstellt_von = Column(String(128), default="")
    erstellt_dt  = Column(DateTime, default=datetime.utcnow)
    aktiv        = Column(Boolean, default=True)

class Einsatzplanung(Base):
    __tablename__ = "einsatzplanung"
    id           = Column(Integer, primary_key=True)
    einsatz_id   = Column(Integer, ForeignKey("einsaetze.id"), unique=True)
    phasen_json  = Column(Text, default="[]")
    abschn_json  = Column(Text, default="[]")
    ki_plan      = Column(Text, default="")
    einsatzbefehl= Column(Text, default="")
    geaendert_dt = Column(DateTime, default=datetime.utcnow)

def _create_engine():
    if DATABASE_URL.startswith("sqlite"):
        eng = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
        @event.listens_for(eng, "connect")
        def _p(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        return eng
    return create_engine(DATABASE_URL)

engine = _create_engine()
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

def get_db():
    with Session(engine) as s:
        yield s

def init_db():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        if s.query(User).count() == 0:
            for u, pw, name, role in [
                ("admin",   "admin123", "Administrator",        "admin"),
                ("s2",      "s2pass",   "S2 – Sachgebiet Lage", "s2"),
                ("el",      "elpass",   "Einsatzleiter",        "s1"),
                ("buerger", "buerger",  "Bürgermeister",        "buergermeister"),
                ("presse",  "presse",   "Pressestelle",         "presse"),
                ("extern",  "extern",   "Beobachter",           "extern"),
            ]:
                s.add(User(username=u, hashed_pw=hash_password(pw),
                           display_name=name, role=role))
            s.commit()
            print("[DB] Nutzer angelegt")
        if s.query(Einsatz).count() == 0:
            s.add(Einsatz(kennung=f"E-{datetime.now():%Y%m%d}",
                          stichwort="Bereitschaft", einsatzort="–"))
            s.commit()
            print("[DB] Standard-Einsatz angelegt")

def _prev_hash(db: Session, eid: int) -> str:
    last = (db.query(TagebuchEintrag)
              .filter(TagebuchEintrag.einsatz_id == eid)
              .order_by(TagebuchEintrag.id.desc()).first())
    return last.entry_hash if (last and last.entry_hash) else "GENESIS"

def _tb_dict(r: TagebuchEintrag) -> dict:
    return {
        "id": r.id, "einsatz_id": r.einsatz_id,
        "author_name": r.author_name, "author_role": r.author_role,
        "eingang_dt": r.eingang_dt.isoformat() if r.eingang_dt else None,
        "kategorie": r.kategorie, "prioritaet": r.prioritaet,
        "betreff": r.betreff, "inhalt": r.inhalt, "quelle": r.quelle,
        "vordruck_nr": r.vordruck_nr, "vordruck_von": r.vordruck_von,
        "vordruck_an": r.vordruck_an, "entry_hash": r.entry_hash,
        "prev_hash": r.prev_hash, "freigegeben": r.freigegeben,
        "freigabe_von": r.freigabe_von,
    }

# ════════════════════════════════════════════════════════════════
# WEBSOCKET HUB
# ════════════════════════════════════════════════════════════════

class Hub:
    def __init__(self):
        self._c: Dict[int, List[tuple]] = {}

    async def connect(self, ws: WebSocket, eid: int, uid: str):
        await ws.accept()
        self._c.setdefault(eid, []).append((ws, uid))
        try:
            await ws.send_json({"type": "connected", "eid": eid,
                                "user": uid, "clients": len(self._c[eid]),
                                "zeit": datetime.now().strftime("%H:%M")})
        except Exception:
            pass

    def disconnect(self, ws: WebSocket, eid: int):
        self._c[eid] = [(w, u) for w, u in self._c.get(eid, []) if w is not ws]

    async def broadcast(self, eid: int, msg: dict, exclude: WebSocket = None):
        msg.setdefault("_ts", datetime.now().isoformat())
        payload = json.dumps(msg, ensure_ascii=False, default=str)
        dead = []
        for ws, uid in list(self._c.get(eid, [])):
            if ws is exclude:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append((ws, uid))
        if dead:
            self._c[eid] = [(w, u) for w, u in self._c.get(eid, [])
                            if (w, u) not in dead]

hub = Hub()

# ════════════════════════════════════════════════════════════════
# KI-AGENT
# ════════════════════════════════════════════════════════════════

_ORTE = {
    "offenburg":  (48.4732, 7.9414, "🏛", "#FFD100"),
    "kehl":       (48.5745, 7.8119, "🔴", "#C8000A"),
    "lahr":       (48.3413, 7.8684, "🔴", "#C8000A"),
    "achern":     (48.5540, 8.0781, "🟡", "#E07B00"),
    "rheinau":    (48.4180, 7.7990, "🌊", "#1A5FA8"),
    "gambsheim":  (48.6180, 7.8820, "🌊", "#1A5FA8"),
    "freistätt":  (48.5400, 7.8600, "🌊", "#1A5FA8"),
    "oberkirch":  (48.5333, 8.2167, "🏥", "#1A8A3C"),
    "wolfach":    (48.2953, 8.2869, "🔴", "#C8000A"),
    "ettenheim":  (48.2667, 7.7833, "🏥", "#1A8A3C"),
}

def _geo(text: str) -> list:
    result, tl = [], text.lower()
    for name, (lat, lng, emoji, farbe) in _ORTE.items():
        if name in tl:
            result.append({"tz_typ": name.capitalize(), "farbe": farbe,
                           "emoji": emoji, "lat": lat, "lng": lng,
                           "beschreibung": f"Erkannt: {name.capitalize()}"})
    m = re.search(r"([\d.]+)[°\s]*N[,\s]+([\d.]+)[°\s]*E", text, re.I)
    if m:
        try:
            lat, lng = float(m[1]), float(m[2])
            if 47 < lat < 50 and 6 < lng < 10:
                result.insert(0, {"tz_typ": "Einsatzschwerpunkt",
                                  "farbe": "#C8000A", "emoji": "🎯",
                                  "lat": lat, "lng": lng,
                                  "beschreibung": "Koordinaten aus Eingabe"})
        except ValueError:
            pass
    return result[:8]

async def ki_lage(eingaben: dict) -> dict:
    if not ANTHROPIC_KEY:
        return {
            "raw": "⚠ ANTHROPIC_API_KEY fehlt – in Railway Variables setzen.",
            "lagebeschreibung": (
                f"Lageeingabe gespeichert. Stand: {datetime.now():%d.%m.%Y %H:%M}\n\n"
                f"Ort: {eingaben.get('einsatzort','–')}\n"
                f"Stichwort: {eingaben.get('stichwort','–')}\n"
                f"Schadenslage: {eingaben.get('schadenslage','–')}"
            ),
            "geodaten": _geo(eingaben.get("schadenslage","") + " " +
                             eingaben.get("geoLage","") + " " +
                             eingaben.get("koordinaten","")),
        }
    try:
        import anthropic as _a
    except ImportError:
        return {"raw": "anthropic nicht installiert.", "lagebeschreibung": "–", "geodaten": []}

    now = f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    outputs = eingaben.get("outputs", ["Aktuelle Lagebeschreibung"])
    prompt = f"""LAGEINFORMATION STAND {now}

Stichwort:    {eingaben.get('stichwort','–')}
Einsatzort:   {eingaben.get('einsatzort','–')}
Lagestufe:    {eingaben.get('lagestufe','–')}
Takt. Zeichen:{eingaben.get('taktischeZeichen','–')}

SCHADENSLAGE:
{eingaben.get('schadenslage','–')}

GEFAHRENLAGE:
{eingaben.get('gefahrenlage','–')}

OPFER: Tote={eingaben.get('tote','–')} | Verletzte={eingaben.get('verletzte','–')} | Verschüttete={eingaben.get('verschuettete','–')} | Obdachlose={eingaben.get('obdachlose','–')}

KRÄFTE:
{eingaben.get('kraefte','–')}

GEO/SPERRUNGEN:
{eingaben.get('geoLage','–')}

KOORDINATEN: {eingaben.get('koordinaten','–')}
WETTER: {eingaben.get('temperatur','–')}
HINWEISE: {eingaben.get('hinweise','')}

GEWÜNSCHTE AUSGABEN:
{chr(10).join('- ' + o for o in outputs)}

Erstelle alle Produkte. Trenne mit === PRODUKTNAME === als Überschrift.
Stand {now} bei jedem Produkt. Keine Markdown-Sternchen. DV 100 konform.
Alle Ausgaben sind Entwürfe – Freigabe durch verantwortliches Personal."""

    try:
        client = _a.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=4000,
            system="Du bist KI-Unterstützung S2 gemäß DV 100 / FwDV 102. "
                   "Alle Ausgaben sind Vorschläge. Trenne mit === ÜBERSCHRIFT ===.",
            messages=[{"role": "user", "content": prompt}])
        text = msg.content[0].text
    except Exception as e:
        text = f"KI-Fehler: {e}"

    result: dict = {"raw": text, "stand": now}
    for sec in ["LAGEBESCHREIBUNG", "AKTUELLE LAGEBESCHREIBUNG", "KURZLAGE",
                "MANAGEMENT SUMMARY", "PRESSEMITTEILUNG", "BÜRGERINFORMATION",
                "VERHALTENSEMPFEHLUNG", "FÜHRUNGSRHYTHMUS", "OFFENE PUNKTE",
                "LAGEFORTSCHREIBUNG", "EINSATZPLANUNG", "EINSATZBEFEHL"]:
        m = re.search(
            rf"={2,}[^=]*{re.escape(sec)}[^=]*={2,}([\s\S]*?)(?:={2,}|$)",
            text, re.IGNORECASE)
        if m:
            k = (sec.lower().replace(" ","_")
                 .replace("ü","ue").replace("ä","ae").replace("ö","oe"))
            result[k] = m.group(1).strip()
    result["geodaten"] = _geo(
        eingaben.get("schadenslage","") + " " +
        eingaben.get("geoLage","") + " " +
        eingaben.get("koordinaten",""))
    return result

async def ki_dokument(text: str, dateiname: str, kontext: dict) -> dict:
    if not ANTHROPIC_KEY:
        return {"kategorie":"Information","prioritaet":"normal",
                "betreff":f"Dokument: {dateiname}","inhalt":text[:600],
                "ki_zusammenfassung":"KI nicht verfügbar.","geodaten":[]}
    try:
        import anthropic as _a
        client = _a.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = (f"Analysiere Dokument '{dateiname}' für S2-Tagebuch.\n"
                  f"Kontext: {json.dumps(kontext, ensure_ascii=False)}\n\n"
                  f"Inhalt:\n{text[:3000]}\n\n"
                  "Antworte NUR mit JSON:\n"
                  '{"kategorie":"Lage|Maßnahme|Meldung|Vordruck|Information",'
                  '"prioritaet":"kritisch|hoch|normal","betreff":"max 80 Zeichen",'
                  '"inhalt":"Zusammenfassung","ki_zusammenfassung":"2-3 Sätze","geodaten":[]}')
        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=600,
            system="Antworte ausschließlich mit validem JSON.",
            messages=[{"role":"user","content":prompt}])
        raw = re.sub(r"```json?\s*|\s*```","",msg.content[0].text).strip()
        return json.loads(raw)
    except Exception as e:
        return {"kategorie":"Information","prioritaet":"normal",
                "betreff":f"Dokument: {dateiname}","inhalt":text[:500],
                "ki_zusammenfassung":f"Analyse: {e}","geodaten":[]}

async def ki_planung(edict: dict, abschnitte: list, phasen: list) -> dict:
    eb = _smeak(edict, abschnitte)
    if not ANTHROPIC_KEY:
        return {"ki_plan":"KI nicht verfügbar.","einsatzbefehl":eb}
    try:
        import anthropic as _a
        client = _a.Anthropic(api_key=ANTHROPIC_KEY)
        now = f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
        prompt = (f"EINSATZPLANUNG · Stand {now}\n\n"
                  f"Einsatz:\n{json.dumps(edict,ensure_ascii=False,indent=2)}\n\n"
                  f"Abschnitte:\n{json.dumps(abschnitte,ensure_ascii=False,indent=2)}\n\n"
                  f"Phasen:\n{json.dumps(phasen,ensure_ascii=False,indent=2)}\n\n"
                  "Erstelle:\n=== EINSATZPLAN ===\n=== FÜHRUNGSRHYTHMUS ===\n"
                  "=== AUFBAU-FAHRPLAN ===\n=== TAKTISCHE HINWEISE ===\n"
                  f"DV 100 konform. Stand {now}. Alle Ausgaben sind Vorschläge.")
        msg = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=3000,
            system="Du bist S2-KI gemäß DV 100.",
            messages=[{"role":"user","content":prompt}])
        return {"ki_plan":msg.content[0].text,"einsatzbefehl":eb}
    except Exception as e:
        return {"ki_plan":f"Fehler: {e}","einsatzbefehl":eb}

def _smeak(e: dict, abschnitte: list) -> str:
    now = f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
    ab = "\n".join(f"  {a.get('nummer','')}: {a.get('bezeichnung','–')} – {a.get('aufgabe','–')}"
                   for a in abschnitte)
    return (f"EINSATZBEFEHL (ENTWURF) · {e.get('kennung','E-XXX')} · {now}\n\n"
            f"S · SITUATION\n  {e.get('stichwort','–')} · {e.get('einsatzort','–')}\n\n"
            f"M · MISSION\n  Bewältigung der Lage unter Führung des Führungsstabs.\n\n"
            f"E · EXECUTION\n{ab or '  [Abschnitte definieren]'}\n\n"
            f"A · ADMINISTRATION\n  Führungsstelle: {e.get('fuehrstelle','[einzutragen]')}\n\n"
            f"K · KOMMANDO\n  EL: {e.get('el_name','[einzutragen]')}\n\n"
            f"ENTWURF · Freigabe durch EL · Stand {now}")

def _std_abschnitte(stw: str) -> list:
    stw = (stw or "").upper()
    if "MANV" in stw or "KAT" in stw:
        return [
            {"nummer":"A1","bezeichnung":"Patientenversorgung/BHP","aufgabe":"Sichtung, Behandlung","status":"aktiv"},
            {"nummer":"A2","bezeichnung":"Rettung/Bergung","aufgabe":"Verschüttete befreien","status":"aktiv"},
            {"nummer":"A3","bezeichnung":"Absperrung","aufgabe":"Zufahrten sichern","status":"aktiv"},
            {"nummer":"A4","bezeichnung":"Betreuung","aufgabe":"Obdachlose versorgen","status":"aktiv"},
            {"nummer":"LOG","bezeichnung":"Logistik","aufgabe":"Nachschub, Ablösung","status":"aktiv"},
        ]
    if any(x in stw for x in ["HW","WASSER","DAMM","ÜSG"]):
        return [
            {"nummer":"A1","bezeichnung":"Deich/Dammsicherung","aufgabe":"Dammverteidigung","status":"aktiv"},
            {"nummer":"A2","bezeichnung":"Evakuierung","aufgabe":"Betroffene evakuieren","status":"aktiv"},
            {"nummer":"A3","bezeichnung":"Wasserrettung","aufgabe":"Personen aus Überflutung","status":"aktiv"},
            {"nummer":"A4","bezeichnung":"Betreuung","aufgabe":"Evakuierte betreuen","status":"aktiv"},
            {"nummer":"LOG","bezeichnung":"Logistik","aufgabe":"Pumpen, Material","status":"aktiv"},
        ]
    return [
        {"nummer":"A1","bezeichnung":"Einsatzschwerpunkt","aufgabe":"–","status":"aktiv"},
        {"nummer":"A2","bezeichnung":"Unterstützung","aufgabe":"–","status":"aktiv"},
        {"nummer":"A3","bezeichnung":"Absperrung","aufgabe":"Zufahrten, Absperrradius","status":"aktiv"},
        {"nummer":"LOG","bezeichnung":"Logistik","aufgabe":"Versorgung, Ablösung","status":"aktiv"},
    ]

def _std_phasen(stw: str) -> list:
    stw = (stw or "").upper()
    phasen = [
        {"nr":1,"name":"Erstmaßnahmen","dauer":"0–30 min",
         "aufgaben":["Lageüberblick","Führungsstelle einrichten","Erkundung","Erstmeldung"]},
        {"nr":2,"name":"Führungsaufbau","dauer":"30–90 min",
         "aufgaben":["Sachgebiete besetzen","Abschnitte einteilen","Kommunikationskonzept","1. Lagebesprechung"]},
        {"nr":3,"name":"Einsatzbetrieb","dauer":"laufend",
         "aufgaben":["Führungsrhythmus","Lagefortschreibung","Ressourcenmanagement","Medienarbeit"]},
        {"nr":4,"name":"Rückzug/Nachbereitung","dauer":"variabel",
         "aufgaben":["Kräfte abziehen","Übergabe","Abschlussbericht","Tagebuch schließen"]},
    ]
    if "MANV" in stw or "KAT" in stw:
        phasen[0]["aufgaben"][:0] = ["MANV-Leitung einsetzen","BHP einrichten"]
    if any(x in stw for x in ["HW","DAMM","ÜSG"]):
        phasen[0]["aufgaben"][:0] = ["Pegelstand überwachen","Evakuierungszonen festlegen"]
    return phasen

def _text_aus_bytes(content: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt",".md",".rst",".csv"):
        return content.decode("utf-8", errors="replace")[:6000]
    if suffix == ".json":
        try:
            return json.dumps(json.loads(content.decode("utf-8",errors="replace")),
                              ensure_ascii=False, indent=2)[:4000]
        except Exception:
            return content.decode("utf-8",errors="replace")[:3000]
    if suffix == ".pdf":
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            return "\n\n".join(p.extract_text() or "" for p in reader.pages)[:6000]
        except ImportError:
            return f"[PDF: {filename} – pip install pypdf2]"
        except Exception as e:
            return f"[PDF-Fehler: {e}]"
    if suffix == ".docx":
        try:
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                xml_data = z.read("word/document.xml")
            tree = ET.fromstring(xml_data)
            ns = {"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return " ".join(t.text or "" for t in tree.findall(".//w:t",ns))[:6000]
        except Exception as e:
            return f"[Word-Fehler: {e}]"
    return content.decode("utf-8", errors="replace")[:3000]

# ════════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════════

def _token(data: dict) -> str:
    exp = datetime.utcnow() + timedelta(minutes=TOKEN_MINUTES)
    return jwt.encode({**data, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def get_user(tk: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    exc = HTTPException(401, "Nicht autorisiert",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        p = jwt.decode(tk, SECRET_KEY, algorithms=[ALGORITHM])
        u = db.query(User).filter(User.username == p.get("sub")).first()
        if not u or not u.active:
            raise exc
        return u
    except JWTError:
        raise exc

def req(*roles: str):
    def dep(u: User = Depends(get_user)) -> User:
        if u.role not in roles and u.role != "admin":
            raise HTTPException(403, "Keine Berechtigung")
        return u
    return dep

# ════════════════════════════════════════════════════════════════
# APP
# ════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print(f"[OK] S2-LageLive v3.2 gestartet auf Port {PORT}")
    yield

app = FastAPI(title="S2-LageLive", version="3.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_fe = Path("frontend")
if _fe.exists() and any(_fe.iterdir()):
    app.mount("/static", StaticFiles(directory="frontend"), name="static")

# ── HEALTH ──────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status":"ok","version":"3.2.0",
            "port":PORT,"time":datetime.now().isoformat()}

# ── WEBSOCKET ────────────────────────────────────────────────────
@app.websocket("/ws/{eid}")
async def ws_ep(ws: WebSocket, eid: int, token: Optional[str] = None):
    uid = "anonym"
    if token:
        try:
            p = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = p.get("sub","anonym")
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

# ── AUTH ROUTES ──────────────────────────────────────────────────
@app.post("/api/auth/token")
async def login(form: OAuth2PasswordRequestForm = Depends(),
                db: Session = Depends(get_db)):
    u = db.query(User).filter(User.username == form.username).first()
    if not u or not verify_password(form.password, u.hashed_pw):
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    return {
        "access_token": _token({"sub": u.username, "role": u.role}),
        "token_type": "bearer",
        "user": {"username": u.username, "role": u.role,
                 "display_name": u.display_name},
    }

@app.get("/api/auth/me")
async def me(u: User = Depends(get_user)):
    return {"username":u.username,"role":u.role,"display_name":u.display_name}

# ── EINSATZ ──────────────────────────────────────────────────────
@app.get("/api/einsaetze")
async def list_e(db: Session = Depends(get_db), u: User = Depends(get_user)):
    rows = db.query(Einsatz).order_by(Einsatz.id.desc()).all()
    return [{"id":e.id,"kennung":e.kennung,"stichwort":e.stichwort,
             "einsatzort":e.einsatzort,"lagestufe":e.lagestufe,"aktiv":e.aktiv,
             "tote":e.tote,"verletzte":e.verletzte,
             "verschuettete":e.verschuettete,"obdachlose":e.obdachlose,
             "alarmzeit":e.alarmzeit.isoformat() if e.alarmzeit else None}
            for e in rows]

@app.post("/api/einsaetze")
async def create_e(data: dict, db: Session = Depends(get_db),
                   u: User = Depends(req("s1","s2"))):
    e = Einsatz(kennung=data.get("kennung",f"E-{datetime.now():%Y%m%d-%H%M}"),
                stichwort=data.get("stichwort","–"),
                einsatzort=data.get("einsatzort","–"),
                lagestufe=data.get("lagestufe","Erstmeldung"))
    db.add(e); db.commit(); db.refresh(e)
    await hub.broadcast(e.id,{"type":"einsatz_erstellt","einsatz_id":e.id,"kennung":e.kennung})
    return {"id":e.id,"kennung":e.kennung}

@app.get("/api/einsaetze/{eid}")
async def get_e(eid: int, db: Session = Depends(get_db), u: User = Depends(get_user)):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404,"Einsatz nicht gefunden")
    return {"id":e.id,"kennung":e.kennung,"stichwort":e.stichwort,
            "einsatzort":e.einsatzort,"lagestufe":e.lagestufe,
            "tote":e.tote,"verletzte":e.verletzte,
            "verschuettete":e.verschuettete,"obdachlose":e.obdachlose,
            "alarmzeit":e.alarmzeit.isoformat() if e.alarmzeit else None}

@app.patch("/api/einsaetze/{eid}")
async def patch_e(eid: int, data: dict, db: Session = Depends(get_db),
                  u: User = Depends(req("s1","s2"))):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    for f in ["lagestufe","tote","verletzte","verschuettete","obdachlose","stichwort","einsatzort"]:
        if f in data: setattr(e, f, data[f])
    db.commit()
    await hub.broadcast(eid,{"type":"einsatz_update","einsatz_id":eid,**data})
    return {"ok":True}

# ── TAGEBUCH ─────────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/tagebuch")
async def list_tb(eid: int, db: Session = Depends(get_db), u: User = Depends(get_user)):
    rows = (db.query(TagebuchEintrag)
              .filter(TagebuchEintrag.einsatz_id==eid)
              .order_by(TagebuchEintrag.id.asc()).all())
    return [_tb_dict(r) for r in rows]

@app.post("/api/einsaetze/{eid}/tagebuch")
async def add_tb(eid: int, data: dict, db: Session = Depends(get_db),
                 u: User = Depends(get_user)):
    r = TagebuchEintrag(
        einsatz_id=eid, author_name=u.display_name or u.username,
        author_role=u.role, eingang_dt=datetime.utcnow(),
        kategorie=data.get("kategorie","Meldung"),
        prioritaet=data.get("prioritaet","normal"),
        betreff=data.get("betreff",""), inhalt=data.get("inhalt",""),
        quelle=data.get("quelle","Manuell"),
        vordruck_nr=data.get("vordruck_nr"),
        vordruck_von=data.get("vordruck_von"),
        vordruck_an=data.get("vordruck_an"),
        prev_hash=_prev_hash(db,eid))
    db.add(r); db.flush()
    r.entry_hash = r.berechne_hash()
    db.commit(); db.refresh(r)
    msg = {**_tb_dict(r),"type":"tagebuch_eintrag",
           "zeit":r.eingang_dt.strftime("%d.%m. %H:%M"),
           "hash":(r.entry_hash or "")[:12]+"…","author":r.author_name}
    await hub.broadcast(eid, msg)
    return {"id":r.id,"entry_hash":r.entry_hash}

@app.get("/api/einsaetze/{eid}/tagebuch/verify")
async def verify_tb(eid: int, db: Session = Depends(get_db), u: User = Depends(req("s1","s2"))):
    rows = (db.query(TagebuchEintrag)
              .filter(TagebuchEintrag.einsatz_id==eid)
              .order_by(TagebuchEintrag.id.asc()).all())
    fehler = []
    for i, r in enumerate(rows):
        if not r.verifiziere(rows[i-1] if i > 0 else None):
            fehler.append({"id":r.id,"betreff":r.betreff})
    return {"valid":len(fehler)==0,"eintraege":len(rows),"fehler":fehler}

@app.post("/api/einsaetze/{eid}/tagebuch/{tid}/freigeben")
async def freigeben(eid: int, tid: int, db: Session = Depends(get_db),
                    u: User = Depends(req("s1","s2"))):
    r = db.query(TagebuchEintrag).filter(
        TagebuchEintrag.id==tid, TagebuchEintrag.einsatz_id==eid).first()
    if not r: raise HTTPException(404)
    r.freigegeben=True; r.freigabe_von=u.display_name; db.commit()
    return {"ok":True}

@app.get("/api/einsaetze/{eid}/export/tagebuch")
async def export_tb(eid: int, db: Session = Depends(get_db), u: User = Depends(req("s1","s2"))):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    rows = (db.query(TagebuchEintrag)
              .filter(TagebuchEintrag.einsatz_id==eid)
              .order_by(TagebuchEintrag.id.asc()).all())
    lines = ["="*70,f"EINSATZTAGEBUCH · {e.kennung}",
             f"Stand: {datetime.now():%d.%m.%Y %H:%M}",f"Einträge: {len(rows)}","="*70,""]
    for r in rows:
        dt = r.eingang_dt.strftime("%d.%m.%Y %H:%M") if r.eingang_dt else "–"
        lines += [f"#{r.id:04d} | {dt} | {r.kategorie} | {r.prioritaet}",
                  f"Betreff: {r.betreff}",f"Autor: {r.author_name} ({r.author_role})",
                  f"Quelle: {r.quelle}","",r.inhalt or "–","",
                  f"HASH: {r.entry_hash or '–'}",f"PREV: {r.prev_hash or '–'}","─"*50,""]
    path = f"exports/tagebuch_{eid}_{datetime.now():%Y%m%d_%H%M}.txt"
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return FileResponse(path, media_type="text/plain",
                        filename=f"Tagebuch_{e.kennung}.txt")

# ── KARTE ────────────────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/karte")
async def list_karte(eid: int, db: Session = Depends(get_db), u: User = Depends(get_user)):
    rows = db.query(KartenZeichen).filter(
        KartenZeichen.einsatz_id==eid, KartenZeichen.aktiv==True).all()
    return [{"id":k.id,"tz_typ":k.tz_typ,"farbe":k.farbe,"emoji":k.emoji,
             "lat":k.lat,"lng":k.lng,"beschreibung":k.beschreibung,
             "erstellt_von":k.erstellt_von,
             "erstellt_dt":k.erstellt_dt.isoformat() if k.erstellt_dt else None}
            for k in rows]

@app.post("/api/einsaetze/{eid}/karte")
async def add_karte(eid: int, data: dict, db: Session = Depends(get_db),
                    u: User = Depends(get_user)):
    k = KartenZeichen(einsatz_id=eid, tz_typ=data.get("tz_typ","Marker"),
                      farbe=data.get("farbe","#C8000A"), emoji=data.get("emoji","📍"),
                      lat=data["lat"], lng=data["lng"],
                      beschreibung=data.get("beschreibung",""),
                      erstellt_von=u.display_name or u.username)
    db.add(k); db.commit(); db.refresh(k)
    await hub.broadcast(eid,{"type":"karten_zeichen","id":k.id,"tz_typ":k.tz_typ,
                              "farbe":k.farbe,"emoji":k.emoji,"lat":k.lat,"lng":k.lng,
                              "beschreibung":k.beschreibung,"erstellt_von":k.erstellt_von,
                              "zeit":datetime.now().strftime("%H:%M")})
    return {"id":k.id}

@app.delete("/api/einsaetze/{eid}/karte/{kid}")
async def del_karte(eid: int, kid: int, db: Session = Depends(get_db),
                    u: User = Depends(req("s2"))):
    k = db.get(KartenZeichen, kid)
    if not k: raise HTTPException(404)
    k.aktiv=False; db.commit()
    await hub.broadcast(eid,{"type":"karten_zeichen_delete","id":kid})
    return {"ok":True}

# ── KI-ANALYSE ───────────────────────────────────────────────────
@app.post("/api/einsaetze/{eid}/ki/analyse")
async def ki_route(eid: int, data: dict, db: Session = Depends(get_db),
                   u: User = Depends(req("s2"))):
    result = await ki_lage(data)
    e = db.get(Einsatz, eid)
    if e:
        for f in ["tote","verletzte","verschuettete","obdachlose"]:
            try:
                if data.get(f): setattr(e, f, int(data[f]))
            except (ValueError,TypeError):
                pass
        if data.get("lagestufe"): e.lagestufe = data["lagestufe"]
    inhalt = (result.get("lagebeschreibung") or
              result.get("aktuelle_lagebeschreibung") or
              result.get("raw","–"))
    r = TagebuchEintrag(einsatz_id=eid, author_name="KI-System S2",
                        author_role="system", kategorie="Lage", prioritaet="hoch",
                        betreff=f"KI-Analyse · {datetime.now():%H:%M Uhr}",
                        inhalt=inhalt[:2000], quelle="KI-Analyse",
                        prev_hash=_prev_hash(db,eid))
    db.add(r); db.flush(); r.entry_hash=r.berechne_hash()
    for geo in result.get("geodaten",[]):
        if not (geo.get("lat") and geo.get("lng")): continue
        k = KartenZeichen(einsatz_id=eid, tz_typ=geo.get("tz_typ","Marker"),
                          farbe=geo.get("farbe","#C8000A"), emoji=geo.get("emoji","📍"),
                          lat=geo["lat"], lng=geo["lng"],
                          beschreibung=geo.get("beschreibung",""), erstellt_von="KI-Agent")
        db.add(k); db.flush()
        await hub.broadcast(eid,{"type":"karten_zeichen","id":k.id,**geo,
                                  "zeit":datetime.now().strftime("%H:%M")})
    db.commit()
    if e:
        await hub.broadcast(eid,{"type":"einsatz_update","einsatz_id":eid,
                                  "tote":e.tote,"verletzte":e.verletzte,
                                  "verschuettete":e.verschuettete,"obdachlose":e.obdachlose})
    await hub.broadcast(eid,{"type":"ki_analyse_fertig",**result})
    return result

# ── FILE UPLOAD ──────────────────────────────────────────────────
@app.post("/api/einsaetze/{eid}/upload")
async def upload(eid: int, file: UploadFile = File(...),
                 db: Session = Depends(get_db), u: User = Depends(get_user)):
    content = await file.read()
    safe = re.sub(r"[^\w.\-]","_", file.filename or "datei")
    path = f"uploads/{eid}_{datetime.now():%Y%m%d_%H%M%S}_{safe}"
    Path(path).write_bytes(content)
    text = _text_aus_bytes(content, file.filename or "")
    e = db.get(Einsatz, eid)
    ctx = {"stichwort":e.stichwort if e else "–","einsatzort":e.einsatzort if e else "–"}
    await hub.broadcast(eid,{"type":"datei_eingang","datei":file.filename,
                              "ki_kurz":"Analyse läuft …",
                              "zeit":datetime.now().strftime("%H:%M")})
    asyncio.create_task(_ingest(eid, file.filename or safe, text, ctx))
    return {"datei":file.filename,"status":"wird verarbeitet"}

async def _ingest(eid: int, filename: str, text: str, ctx: dict):
    try:
        analyse = await ki_dokument(text, filename, ctx)
        with Session(engine) as db:
            r = TagebuchEintrag(einsatz_id=eid, author_name=f"Ingest: {filename}",
                                author_role="system",
                                kategorie=analyse.get("kategorie","Information"),
                                prioritaet=analyse.get("prioritaet","normal"),
                                betreff=analyse.get("betreff",f"Dokument: {filename}"),
                                inhalt=analyse.get("inhalt",text[:800]),
                                quelle=f"Upload: {filename}",
                                prev_hash=_prev_hash(db,eid))
            db.add(r); db.flush(); r.entry_hash=r.berechne_hash(); db.commit(); db.refresh(r)
        msg = {**_tb_dict(r),"type":"tagebuch_eintrag",
               "zeit":r.eingang_dt.strftime("%d.%m. %H:%M"),
               "hash":(r.entry_hash or "")[:12]+"…","author":r.author_name}
        await hub.broadcast(eid, msg)
        await hub.broadcast(eid,{"type":"datei_fertig","datei":filename,
                                  "ki_kurz":analyse.get("ki_zusammenfassung","Verarbeitet"),
                                  "zeit":datetime.now().strftime("%H:%M")})
    except Exception as ex:
        print(f"[INGEST] Fehler {filename}: {ex}")

# ── EINSATZPLANUNG ───────────────────────────────────────────────
@app.get("/api/einsaetze/{eid}/planung")
async def get_planung(eid: int, db: Session = Depends(get_db), u: User = Depends(get_user)):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    p = db.query(Einsatzplanung).filter(Einsatzplanung.einsatz_id==eid).first()
    phasen     = json.loads(p.phasen_json) if p else _std_phasen(e.stichwort)
    abschnitte = json.loads(p.abschn_json) if p else _std_abschnitte(e.stichwort)
    return {"einsatz_id":eid,"phasen":phasen,"abschnitte":abschnitte,
            "ki_plan":p.ki_plan if p else "","einsatzbefehl":p.einsatzbefehl if p else ""}

@app.post("/api/einsaetze/{eid}/planung/ki")
async def planung_ki(eid: int, data: dict, db: Session = Depends(get_db),
                     u: User = Depends(req("s1","s2"))):
    e = db.get(Einsatz, eid)
    if not e: raise HTTPException(404)
    edict = {"kennung":e.kennung,"stichwort":e.stichwort,"einsatzort":e.einsatzort,
             "lagestufe":e.lagestufe,"tote":e.tote,"verletzte":e.verletzte,
             "obdachlose":e.obdachlose,"el_name":data.get("el_name","–"),
             "fuehrstelle":data.get("fuehrstelle","–")}
    abschnitte = data.get("abschnitte") or _std_abschnitte(e.stichwort)
    phasen     = data.get("phasen") or _std_phasen(e.stichwort)
    result = await ki_planung(edict, abschnitte, phasen)
    p = db.query(Einsatzplanung).filter(Einsatzplanung.einsatz_id==eid).first()
    if not p: p = Einsatzplanung(einsatz_id=eid); db.add(p)
    p.phasen_json=json.dumps(phasen,ensure_ascii=False)
    p.abschn_json=json.dumps(abschnitte,ensure_ascii=False)
    p.ki_plan=result.get("ki_plan","")
    p.einsatzbefehl=result.get("einsatzbefehl","")
    p.geaendert_dt=datetime.utcnow(); db.commit()
    with Session(engine) as db2:
        r = TagebuchEintrag(einsatz_id=eid, author_name="KI-Einsatzplanung",
                            author_role="system", kategorie="Maßnahme", prioritaet="hoch",
                            betreff=f"Einsatzplanung · {datetime.now():%H:%M Uhr}",
                            inhalt=result.get("ki_plan","")[:1500], quelle="KI-Planung",
                            prev_hash=_prev_hash(db2,eid))
        db2.add(r); db2.flush(); r.entry_hash=r.berechne_hash(); db2.commit()
    await hub.broadcast(eid,{"type":"einsatzplanung_fertig",
                              "ki_plan":result.get("ki_plan",""),
                              "einsatzbefehl":result.get("einsatzbefehl",""),
                              "phasen":phasen,"abschnitte":abschnitte})
    return result

# ── AUSGABEN ─────────────────────────────────────────────────────
AUSGABE_PROMPTS = {
    "lagebericht":          "Vollständiger Lagebericht DV 100: 1.Lage 2.Schaden 3.Gefahr 4.Kräfte 5.Geo 6.Maßnahmen 7.Entwicklung 8.Info-Bedarfe",
    "lagevortrag":          "Lagevortrag 5-10 Min für Lagebesprechung: 1.Lage 2.Kräfte 3.Maßnahmen 4.Offene Punkte 5.Nächste Schritte",
    "kurzlage":             "Kurzlage 1 Seite Bulletpoints: Was/Wo/Wieviele/Kräfte/Unklar/Nächster Schritt",
    "management_summary":   "Management Summary für Bürgermeister: kein Fachjargon, klare Zahlen, Was getan, Was gebraucht",
    "pressemitteilung":     "Pressemitteilung ENTWURF: Titel/Lead/Hintergrund/Maßnahmen/Zitat/Verhaltenshinweise/Kontakt. NUR belegte Fakten.",
    "buergerinfo":          "Bürgerinfo einfache Sprache: Was passiert/Was TUN/Was NICHT TUN/Wo Info/Notruf. Max 15 Sätze.",
    "verhaltensempfehlung": "Verhaltensempfehlungen nummerierte Liste: Sofortmaßnahmen/Selbstschutz/Was mitnehmen/Kommunikation",
    "fuehrungs_unterlage":  "Führungsunterlage Lagebesprechung: Tagesordnung/Lageinformation je SG/Offene Fragen/Entscheidungsbedarfe",
    "einsatzbericht":       "Einsatzabschlussbericht: Grundlagen/Ablauf/Kräfte/Maßnahmen/Ergebnisse/Bewertung/Empfehlungen",
    "einsatzbefehl":        "Einsatzbefehl SMEAK: S=Situation M=Mission E=Execution A=Administration K=Kommando. Als ENTWURF.",
}

@app.get("/api/ausgaben/typen")
async def get_typen(u: User = Depends(get_user)):
    return [{"id":k,"name":k.replace("_"," ").title()} for k in AUSGABE_PROMPTS]

@app.post("/api/einsaetze/{eid}/ausgaben")
async def ausgaben(eid: int, data: dict, db: Session = Depends(get_db),
                   u: User = Depends(req("s2"))):
    gewaehlte = data.get("ausgaben",["lagebericht"])
    e = db.get(Einsatz, eid)
    lage = {
        "stichwort":    e.stichwort if e else data.get("stichwort","–"),
        "einsatzort":   e.einsatzort if e else data.get("einsatzort","–"),
        "lagestufe":    e.lagestufe if e else data.get("lagestufe","–"),
        "tote":         e.tote if e else 0,
        "verletzte":    e.verletzte if e else 0,
        "verschuettete":e.verschuettete if e else 0,
        "obdachlose":   e.obdachlose if e else 0,
        **{k:data.get(k,"–") for k in ["schadenslage","gefahrenlage",
                                        "kraefte","geo","wetter","massnahmen"]},
    }
    ki_text = "[KI nicht verfügbar – ANTHROPIC_API_KEY setzen]"
    if ANTHROPIC_KEY:
        try:
            import anthropic as _a
            now = f"{datetime.now():%d.%m.%Y %H:%M Uhr}"
            produkte = "\n\n".join(
                f"=== {k.replace('_',' ').upper()} ===\n{AUSGABE_PROMPTS.get(k,'')}"
                for k in gewaehlte)
            prompt = (f"LAGESTAND {now}:\n{json.dumps(lage,ensure_ascii=False,indent=2)}\n\n"
                      f"ERSTELLE MIT === NAME === ÜBERSCHRIFT UND STAND {now}:\n{produkte}\n\n"
                      "DV 100 konform. Als ENTWURF wo zutreffend.")
            client = _a.Anthropic(api_key=ANTHROPIC_KEY)
            msg = client.messages.create(
                model="claude-sonnet-4-20250514", max_tokens=5000,
                system="Du bist S2-KI DV 100. Alle Ausgaben sind Entwürfe.",
                messages=[{"role":"user","content":prompt}])
            ki_text = msg.content[0].text
        except Exception as ex:
            ki_text = f"KI-Fehler: {ex}"
    path = f"exports/ausgaben_{eid}_{datetime.now():%Y%m%d_%H%M}.txt"
    Path(path).write_text(ki_text, encoding="utf-8")
    with Session(engine) as db2:
        r = TagebuchEintrag(einsatz_id=eid, author_name="Ausgabe-Pipeline",
                            author_role="system", kategorie="Lage", prioritaet="normal",
                            betreff=f"KI-Ausgaben: {', '.join(gewaehlte[:3])} · {datetime.now():%H:%M}",
                            inhalt=ki_text[:1500], quelle="Ausgabe-Pipeline",
                            prev_hash=_prev_hash(db2,eid))
        db2.add(r); db2.flush(); r.entry_hash=r.berechne_hash(); db2.commit()
    await hub.broadcast(eid,{"type":"ausgaben_fertig","ausgaben":gewaehlte})
    return {"ki_text":ki_text,"ausgaben":gewaehlte,"export":path}

@app.get("/api/einsaetze/{eid}/ausgaben/download")
async def dl_ausgaben(eid: int, db: Session = Depends(get_db), u: User = Depends(get_user)):
    import glob
    files = sorted(glob.glob(f"exports/ausgaben_{eid}_*.txt"), reverse=True)
    if not files: raise HTTPException(404,"Noch keine Ausgaben erstellt.")
    return FileResponse(files[0], media_type="text/plain",
                        filename=f"Ausgaben-E{eid}.txt")

# ── FRONTEND ─────────────────────────────────────────────────────
# Wichtig: index.html direkt einlesen und als Response zurückgeben
# FileResponse schlägt fehl wenn das Arbeitsverzeichnis nicht stimmt
@app.get("/")
async def root():
    for candidate in [
        Path("frontend/index.html"),
        Path("/app/frontend/index.html"),
    ]:
        if candidate.exists():
            return FileResponse(str(candidate), media_type="text/html")
    return JSONResponse({"status":"S2-LageLive","health":"/health","docs":"/docs"})

@app.get("/{path:path}")
async def spa(path: str):
    # Statische Dateien
    for base in [Path("frontend"), Path("/app/frontend")]:
        fp = base / path
        if fp.exists() and fp.is_file():
            return FileResponse(str(fp))
    # SPA-Fallback: immer index.html
    for candidate in [
        Path("frontend/index.html"),
        Path("/app/frontend/index.html"),
    ]:
        if candidate.exists():
            return FileResponse(str(candidate), media_type="text/html")
    raise HTTPException(404, f"Nicht gefunden: {path}")

# ════════════════════════════════════════════════════════════════
# START
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    print(f"[START] uvicorn auf 0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
