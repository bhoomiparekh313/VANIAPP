import streamlit as st
import sqlite3
import time
import geocoder
import smtplib
import imaplib
import email
import speech_recognition as sr
from email.message import EmailMessage
from streamlit_folium import folium_static
import folium

# ================= CONFIG =================
EMAIL = "vedikad945@gmail.com"
PASSWORD = "hnil rwis dedy fmth"
DB = "vani.db"

# ================= DATABASE =================
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        acknowledged INTEGER DEFAULT 0
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        time TEXT,
        trigger TEXT,
        message TEXT,
        lat TEXT,
        lon TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# ================= HELPERS =================
def get_contacts():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    data = c.execute("SELECT * FROM contacts").fetchall()
    conn.close()
    return data

def add_contact(name, email):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO contacts (name,email) VALUES (?,?)", (name, email))
        conn.commit()
    except:
        pass
    conn.close()

def delete_contact(cid):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM contacts WHERE id=?", (cid,))
    conn.commit()
    conn.close()

def reset_ack():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE contacts SET acknowledged = 0")
    conn.commit()
    conn.close()

def mark_ack(email):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE contacts SET acknowledged = 1 WHERE email=?", (email,))
    conn.commit()
    conn.close()

def log_event(trigger, msg, lat, lon):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO logs VALUES (?,?,?,?,?)",
              (time.ctime(), trigger, msg, lat, lon))
    conn.commit()
    conn.close()

# ================= EMAIL =================
def send_alert():
    contacts = get_contacts()
    emails = [c[2] for c in contacts]

    reset_ack()

    g = geocoder.ip("me", service="arcgis")
    lat, lon = g.latlng

    msg = EmailMessage()
    msg["Subject"] = "🚨 VANI EMERGENCY ALERT"
    msg["From"] = EMAIL
    msg["To"] = ", ".join(emails)

    msg.set_content(
        f"🚨 EMERGENCY ALERT 🚨\n\n"
        f"User needs help immediately.\n\n"
        f"📍 Location:\nhttps://maps.google.com/?q={lat},{lon}\n\n"
        f"Reply to this email to acknowledge."
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL, PASSWORD)
        smtp.send_message(msg)

    log_event("ALERT", "Emergency Triggered", lat, lon)

# ================= ACK CHECK =================
def check_acknowledgements():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL, PASSWORD)
        mail.select("inbox")

        status, data = mail.search(None, "UNSEEN")
        for num in data[0].split():
            msg = mail.fetch(num, "(RFC822)")
            raw = msg[1][0][1]
            parsed = email.message_from_bytes(raw)

            sender = parsed["From"].split("<")[-1].replace(">", "")
            mark_ack(sender)

        mail.logout()
    except:
        pass

# ================= UI =================
st.set_page_config(page_title="VANI", layout="wide")


# Check acknowledgements on every refresh
check_acknowledgements()

st.markdown("""
<style>
body { background: linear-gradient(135deg, #fceabb, #f8b500); }
.card { background: white; padding: 20px; border-radius: 16px; }
</style>
""", unsafe_allow_html=True)

st.title("🚨 VANI – Emergency Safety App")

# Layout
left, center, right = st.columns([1.3, 2, 1.3])

# ================= CONTACTS =================
with left:
    st.subheader("👥 Emergency Contacts")

    name = st.text_input("Name")
    email = st.text_input("Email")

    if st.button("➕ Add Contact"):
        add_contact(name, email)
        st.success("Contact added")
        st.rerun()

    st.markdown("---")

    for cid, name, email, ack in get_contacts():
        col1, col2, col3 = st.columns([3, 4, 2])
        col1.write(f"**{name}**")
        col2.write(email)

        if ack:
            col3.success("✅ Acknowledged")
        else:
            col3.warning("⏳ Waiting")

        if st.button("❌", key=cid):
            delete_contact(cid)
            st.rerun()

# ================= MAP =================
with center:
    st.subheader("📍 Live Location")
    g = geocoder.ip("me", service="arcgis")
    loc = g.latlng

    m = folium.Map(location=loc, zoom_start=15)
    folium.Marker(loc, tooltip="You are here").add_to(m)
    folium_static(m)

# ================= ACTION =================
with right:
    st.subheader("🚨 Emergency Panel")

    if "status" not in st.session_state:
        st.session_state.status = "Listening..."

    st.info(st.session_state.status)

    if st.button("🚨 SOS"):
        send_alert()
        st.session_state.status = "🚨 Alert Sent"
        st.success("Emergency alert sent!")

    st.markdown("---")

    if st.button("🎤 Voice Trigger"):
        r = sr.Recognizer()
        with sr.Microphone() as src:
            st.info("Listening...")
            audio = r.listen(src, phrase_time_limit=4)

        try:
            text = r.recognize_google(audio).lower()
            st.write("Heard:", text)

            if any(k in text for k in ["vani","help","fire","save","bachao"]):
                send_alert()
                st.success("Emergency Triggered!")
        except:
            st.error("Could not recognize voice")

# ================= LOGS =================
st.markdown("## 📊 Emergency Logs")
conn = sqlite3.connect(DB)
logs = conn.execute("SELECT * FROM logs").fetchall()
conn.close()
st.table(logs)

# Auto refresh every 10 seconds (built-in way)
time.sleep(10)
st.rerun()