import os
import re
import time
import sqlite3
import threading
from datetime import datetime
from typing import Optional, Tuple, List
from queue import Queue, Empty
from threading import Lock

import streamlit as st

# Speech
import speech_recognition as sr

# ML
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# Email
import smtplib
import imaplib
import email as email_pkg
from email.message import EmailMessage

# Map / location
import folium
from streamlit_folium import st_folium

try:
    from streamlit_js_eval import get_geolocation
except Exception:
    get_geolocation = None

from streamlit_autorefresh import st_autorefresh


# -------------------- CONFIG --------------------
st.set_page_config(page_title="VANI", page_icon="🚨", layout="wide")

DB_PATH = "vani.db"

EMAIL_SENDER = os.getenv("VANI_EMAIL", "vedikad945@gmail.com")
EMAIL_PASSWORD = os.getenv("VANI_APP_PASSWORD", "hnil rwis dedy fmth")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"

PHRASE_TIME_LIMIT = 4
ACK_POLL_INTERVAL = 30


# -------------------- KEYWORDS --------------------
EMERGENCY_KEYWORDS = [
    "help", "emergency", "bachao", "save me", "call police", "ambulance", "fire",
    "attack", "danger", "madad", "madad karo", "mala vachava", "vachava",
    "help karo", "please help", "someone is following me", "i am unsafe",
    "i feel unsafe", "i need help", "police", "please save me", 
    "someone is attacking", "kidnap", "they are taking me",
    "i am being followed", "i am hurt", "i am bleeding"
]


# -------------------- ML DATASET --------------------
DISTRESS_SAMPLES = [
    "help me", "help me please", "emergency", "save me", "someone is attacking me",
    "i am in danger", "call police", "please call police", "please help",
    "i feel unsafe", "someone is following me", "i am being followed",
    "i am hurt", "i am bleeding", "ambulance needed", "call an ambulance",
    "there is a fire", "fire emergency", "i am trapped", "i cannot breathe",
    "i am choking", "heart attack", "i have chest pain", "i fainted",
    "bachao", "bachao mujhe", "mujhe bachao", "madad karo", "koi help karo",
    "police bulao", "please bachao", "arre bachao", "mala vachava", "vachava",
    "मदत करा"
]

NON_DISTRESS_SAMPLES = [
    "I don't need your help", "help me with homework", "help me with this assignment", "emergency movie is nice",
    "this is an emergency meeting", "save me a seat please", "call police in the movie",
    "i need help choosing a laptop", "help karo yaar with project", "help me in coding",
    "i feel unsafe about exam tomorrow", "dangerously tasty food", "fire emoji looks cool",
    "ambulance game on phone", "police station near college", "attack on titan is my favorite",
    "i am bleeding in game", "heart attack movie scene", "i am choking on laughter",
    "this is a fire song", "save me a file", "help me schedule a meeting",
    "madad chahiye for notes", "bachao means save", "please help karo in excel",
    "emergency contact form", "call police number is 112", "police verification form",
    "accidentally deleted file", "i am trapped in traffic", "i am stuck in code bug",
    "fire drill practice", "please save me from boredom", "someone is following me on instagram",
    "attack plan for game", "madad karo with groceries", "vachava word meaning",
    "please help with presentation", "police show is nice"
]

X_TEXT = DISTRESS_SAMPLES[:35] + NON_DISTRESS_SAMPLES[:35]
Y = [1] * 35 + [0] * 35


# -------------------- DATABASE --------------------
def db_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE)")
    cur.execute("CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, trigger_type TEXT NOT NULL, transcript TEXT, lat REAL, lon REAL)")
    cur.execute("CREATE TABLE IF NOT EXISTS acks (alert_id INTEGER NOT NULL, contact_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', ack_ts TEXT, PRIMARY KEY (alert_id, contact_id))")
    conn.commit()
    conn.close()

init_db()


# -------------------- ML MODEL --------------------
@st.cache_resource
def load_model():
    vec = TfidfVectorizer(ngram_range=(1, 2), lowercase=True)
    Xv = vec.fit_transform(X_TEXT)
    clf = LogisticRegression(max_iter=1000)
    clf.fit(Xv, Y)
    return vec, clf

VEC, CLF = load_model()

def distress_probability(text: str) -> float:
    if not text: return 0.0
    Xv = VEC.transform([text])
    return float(CLF.predict_proba(Xv)[0][1])

def contains_keyword(text: str, keywords: List[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)


# -------------------- LOCATION --------------------
def get_location() -> Tuple[Optional[float], Optional[float], str]:
    if get_geolocation is None: return None, None, "unavailable"
    try:
        loc = get_geolocation()
        if loc and "coords" in loc:
            lat = loc["coords"].get("latitude")
            lon = loc["coords"].get("longitude")
            if lat is not None and lon is not None: return float(lat), float(lon), "browser_gps"
    except Exception: pass
    return None, None, "unavailable"

def make_map(lat: float, lon: float):
    m = folium.Map(location=[lat, lon], zoom_start=16)
    folium.Marker([lat, lon], tooltip="User Location", icon=folium.Icon(color="red")).add_to(m)
    return m


# -------------------- EMAIL --------------------
def send_emergency_emails(recipients: List[str], subject: str, body: str) -> Tuple[int, List[str]]:
    if not EMAIL_SENDER or not EMAIL_PASSWORD: return 0, recipients
    success, failed = 0, []
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            for r in recipients:
                msg = EmailMessage()
                msg["From"] = EMAIL_SENDER
                msg["To"] = r
                msg["Subject"] = subject
                msg.set_content(body)
                try:
                    server.send_message(msg)
                    success += 1
                except Exception: failed.append(r)
    except Exception: return 0, recipients
    return success, failed


# -------------------- DB HELPERS --------------------
def fetch_contacts():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email FROM contacts ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def insert_alert(trigger_type: str, transcript: str, lat: Optional[float], lon: Optional[float]) -> int:
    conn = db_conn()
    cur = conn.cursor()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("INSERT INTO alerts (ts, trigger_type, transcript, lat, lon) VALUES (?, ?, ?, ?, ?)", (ts, trigger_type, transcript, lat, lon))
    alert_id = cur.lastrowid
    cur.execute("SELECT id FROM contacts")
    contact_ids = [r[0] for r in cur.fetchall()]
    for cid in contact_ids:
        cur.execute("INSERT OR IGNORE INTO acks (alert_id, contact_id, status) VALUES (?, ?, 'PENDING')", (alert_id, cid))
    conn.commit()
    conn.close()
    return alert_id

def get_latest_alert_id() -> Optional[int]:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM alerts ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def read_alerts(limit=30):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, ts, trigger_type, transcript, lat, lon FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def read_ack_status(alert_id: Optional[int]):
    if alert_id is None: return []
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT c.name, c.email, a.status, a.ack_ts FROM acks a JOIN contacts c ON c.id = a.contact_id WHERE a.alert_id = ? ORDER BY c.name", (alert_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def update_ack(alert_id: int, contact_email: str):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM contacts WHERE lower(email)=lower(?)", (contact_email,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    cid = row[0]
    ack_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE acks SET status='ACKNOWLEDGED', ack_ts=? WHERE alert_id=? AND contact_id=? AND status!='ACKNOWLEDGED'", (ack_ts, alert_id, cid))
    conn.commit()
    conn.close()


# -------------------- ACK MONITOR --------------------
ACK_REGEX = re.compile(r"(ack|acknowledge|acknowledged|received|ok|okay|got it|on my way|coming)", re.I)

def ack_monitor_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            latest_alert = get_latest_alert_id()
            if latest_alert is not None:
                contacts = fetch_contacts()
                contact_emails = {c[2].lower() for c in contacts}
                if EMAIL_SENDER and EMAIL_PASSWORD:
                    mail = imaplib.IMAP4_SSL(IMAP_HOST)
                    mail.login(EMAIL_SENDER, EMAIL_PASSWORD)
                    mail.select("inbox")
                    status, data = mail.search(None, "UNSEEN")
                    if status == "OK":
                        for num in data[0].split():
                            _, msg_data = mail.fetch(num, "(RFC822)")
                            msg = email_pkg.message_from_bytes(msg_data[0][1])
                            from_addr = email_pkg.utils.parseaddr(msg.get("From", ""))[1].lower()
                            if from_addr in contact_emails:
                                update_ack(latest_alert, from_addr)
                    mail.logout()
        except Exception: pass
        time.sleep(ACK_POLL_INTERVAL)


# -------------------- SESSION STATE INIT --------------------
def ss_init(key, value):
    if key not in st.session_state: st.session_state[key] = value

ss_init("monitoring", False)
ss_init("bg_stop_fn", None)
ss_init("listener_error", None)
ss_init("audio_queue", Queue())
ss_init("listener_lock", Lock())
ss_init("listener_state", {"last_text": "", "last_prob": 0.0, "last_kw": False, "last_time": ""})
ss_init("transcript_feed", [])
ss_init("last_alert_info", None)
ss_init("show_soft_warning", False)

ss_init("ack_stop", None)
ss_init("ack_thread", None)


# -------------------- EMERGENCY TRIGGER --------------------
def trigger_emergency(trigger_type: str, transcript: str, lat: Optional[float], lon: Optional[float]):
    alert_id = insert_alert(trigger_type, transcript, lat, lon)
    contacts = fetch_contacts()
    recipients = [c[2] for c in contacts]

    # FIXED: Better Google Maps Link
    maps_link = f"https://www.google.com/maps?q={lat},{lon}" if lat and lon else "Location data not available."

    subject = f"🚨 VANI EMERGENCY ALERT (Alert #{alert_id})"
    body = (
        f"EMERGENCY ALERT TRIGGERED via VANI\n\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Trigger Type: {trigger_type}\n"
        f"Detected Text: {transcript}\n"
        f"Coordinates: {lat if lat else 'N/A'}, {lon if lon else 'N/A'}\n"
        f"Maps Link: {maps_link}\n\n"
        f"Please REPLY to this email with any message to ACKNOWLEDGE.\n"
    )

    success, failed = send_emergency_emails(recipients, subject, body)
    st.session_state["last_alert_info"] = {
        "alert_id": alert_id,
        "success": success,
        "failed": failed,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


# -------------------- BACKGROUND LISTENER --------------------
def start_background_listener(energy_threshold: int):
    if st.session_state.get("bg_stop_fn"): return
    recog = sr.Recognizer()
    recog.energy_threshold = energy_threshold
    recog.dynamic_energy_threshold = False
    try:
        mic = sr.Microphone()
        with mic as source: recog.adjust_for_ambient_noise(source, duration=0.4)
        q = st.session_state["audio_queue"]
        def callback(r, audio):
            try:
                text = r.recognize_google(audio)
                q.put(("TEXT", text))
            except Exception: q.put(("NOISE", ""))
        st.session_state["bg_stop_fn"] = recog.listen_in_background(mic, callback, phrase_time_limit=PHRASE_TIME_LIMIT)
    except Exception as e: st.session_state["listener_error"] = str(e)

def stop_background_listener():
    if st.session_state.get("bg_stop_fn"): st.session_state["bg_stop_fn"](wait_for_stop=False)
    st.session_state["bg_stop_fn"] = None


# -------------------- ACK MONITOR START --------------------
def start_ack_monitor_if_needed():
    th = st.session_state.get("ack_thread")
    if th is None or (hasattr(th, "is_alive") and not th.is_alive()):
        stop_event = threading.Event()
        th = threading.Thread(target=ack_monitor_loop, args=(stop_event,), daemon=True)
        st.session_state["ack_stop"] = stop_event
        st.session_state["ack_thread"] = th
        th.start()

start_ack_monitor_if_needed()


# -------------------- SIDEBAR --------------------
st.sidebar.title("👥 Guardians")
contacts = fetch_contacts()

with st.sidebar.expander("➕ Add Guardian", expanded=True):
    with st.form("add_contact_form", clear_on_submit=True):
        name = st.text_input("Name")
        email_addr = st.text_input("Email")
        if st.form_submit_button("Add Guardian"):
            if name and email_addr:
                try:
                    conn = db_conn(); cur = conn.cursor()
                    cur.execute("INSERT INTO contacts (name, email) VALUES (?,?)", (name, email_addr))
                    conn.commit(); conn.close(); st.rerun()
                except: st.sidebar.error("Error adding contact")

with st.sidebar.expander("✏️ Edit / Delete Guardians"):
    if contacts:
        contact_options = {f"{c[1]} <{c[2]}>": c for c in contacts}
        sel = st.selectbox("Select", list(contact_options.keys()))
        c = contact_options[sel]
        if st.button("Delete"):
            conn = db_conn(); cur = conn.cursor()
            cur.execute("DELETE FROM contacts WHERE id=?", (c[0],))
            conn.commit(); conn.close(); st.rerun()

st.sidebar.divider()
energy_threshold = st.sidebar.slider("Mic sensitivity", 60, 400, 150)
ml_threshold = st.sidebar.slider("ML-only trigger", 0.10, 0.90, 0.50)
kw_ml_threshold = st.sidebar.slider("Keyword+ML trigger", 0.10, 0.80, 0.40)

if st.sidebar.button("Start Monitor", use_container_width=True):
    st.session_state["monitoring"] = True
    start_background_listener(energy_threshold); st.rerun()
if st.sidebar.button("Stop Monitor", use_container_width=True):
    st.session_state["monitoring"] = False
    stop_background_listener(); st.rerun()


# -------------------- MAIN UI --------------------
st.title("🚨 VANI — Voice-Activated Emergency Alert System")

if st.session_state["monitoring"]:
    st_autorefresh(interval=500, key="refresh")

# Processing logic (No cooldowns)
try:
    while True:
        kind, payload = st.session_state["audio_queue"].get_nowait()
        if kind == "TEXT":
            text = payload.strip(); prob = distress_probability(text); kw = contains_keyword(text, EMERGENCY_KEYWORDS)
            with st.session_state["listener_lock"]:
                st.session_state["listener_state"] = {"last_text": text, "last_prob": prob, "last_kw": kw, "last_time": datetime.now().strftime("%H:%M:%S")}
            st.session_state["transcript_feed"].append({"t": datetime.now().strftime("%H:%M:%S"), "text": text, "kw": kw, "prob": prob})
            
            triggered = False
            if kw and prob >= kw_ml_threshold:
                lat, lon, _ = get_location(); trigger_emergency("KEYWORD+ML", text, lat, lon)
                st.toast("🚨 EMERGENCY TRIGGERED", icon="🚨"); triggered = True
            elif prob >= ml_threshold:
                lat, lon, _ = get_location(); trigger_emergency("ML_ONLY", text, lat, lon)
                st.toast("🚨 EMERGENCY TRIGGERED", icon="🚨"); triggered = True
            
            st.session_state["show_soft_warning"] = (not triggered and kw and prob < kw_ml_threshold)
except Empty: pass

# Layout 
latest_alert_id = get_latest_alert_id()
ack_rows = read_ack_status(latest_alert_id)

colA, colB, colC, colD = st.columns(4)
colA.metric("Monitoring", "ON" if st.session_state["monitoring"] else "OFF")
colB.metric("Guardians", len(contacts))
colC.metric("Latest Alert ID", latest_alert_id or "-")
colD.metric("Acknowledged", f"{sum(1 for r in ack_rows if r[2] == 'ACKNOWLEDGED')}/{len(ack_rows)}" if ack_rows else "-")

left, right = st.columns([1.1, 0.9], gap="large")

with left:
    st.subheader("🗺️ Live Location")
    lat, lon, _ = get_location()
    if lat: st_folium(make_map(lat, lon), height=360, width=None)
    else: st.info("GPS loading...")

    st.subheader("🧾 Live Transcript (Last 10)")
    for item in reversed(st.session_state["transcript_feed"][-10:]):
        st.write(f"`{item['t']}` {'🟧 KW' if item['kw'] else '🟩'} — **{item['text']}** (p={item['prob']:.2f})")

    st.subheader("✅ Acknowledgment Status")
    if latest_alert_id and ack_rows:
        for name, email, status, ts in ack_rows:
            st.write(f"**{name}** — {'🟢 ACK' if status=='ACKNOWLEDGED' else '🟡 WAIT'} {ts or ''}")

    st.divider()
    
    # Visible instant warning popup right above SOS button
    if st.session_state["show_soft_warning"]:
        st.error("⚠️ **HELP NEEDED?** Keyword detected but low confidence. Use Manual SOS if in danger!")

    st.subheader("🆘 Manual SOS Trigger")
    if st.button("TRIGGER EMERGENCY NOW", type="primary", use_container_width=True):
        lat, lon, _ = get_location()
        trigger_emergency("MANUAL_SOS", "Manual SOS", lat, lon)
        st.session_state["show_soft_warning"] = False; st.rerun()

with right:
    # Listening animation (no HTML/CSS)
    rings = ["◌ ◌ ◌", "◍ ◌ ◌", "◎ ◍ ◌", "◉ ◎ ◍", "◎ ◍ ◌", "◍ ◌ ◌"]
    frame = rings[int(time.time() * 3) % len(rings)]
    
    if st.session_state["monitoring"]:
        st.subheader("🎙️ VANI is LISTENING")
        st.markdown(f"### {frame}")
        st.caption("Listening... Speak clearly or use 'help' to test.")
    else:
        st.subheader("⏸️ Monitoring is OFF")

    st.subheader("📜 Emergency Logs")
    logs = read_alerts(15)
    for (aid, ts, trig, transcript, la, lo) in logs:
        with st.expander(f"Alert #{aid} • {ts}"):
            st.write(f"**Trigger:** {trig}")
            st.write(f"**Text:** {transcript}")
            st.write(f"**Location:** {la}, {lo}")
