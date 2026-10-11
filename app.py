import re
import io
import zipfile
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="WhatsApp Escalation Counter", layout="wide")
st.title("📱 WhatsApp Escalation Counter")

# ---------- CONFIG ----------
ESCALATION_KEYWORDS = [
    "lock inactive", "locked", "forgot his password", "forgot her password",
    "forgot the device password", "forgot his phone password", "forgot his phone passward",
    "faulty", "faulty device", "faulty screen", "faulty pbone", "falty",
    "broken screen", "broken phone screen", "spoilt device",
    "can't access", "cannot access", "cant access", "can't acess",
    "unable to reach", "can't locate", "can't find",
    "has not received", "has never received", "no feedback", "no update",
    "follow up", "not responding", "not received any",
    "phone hungs", "phone hangs", "hungs", "hanging",
    "issues with", "problem with", "powering on", "charging system",
    "installing apps", "needs assistance", "needs our help", "needs to be assisted",
    "kindly assist", "kindly help", "kindly follow up",
]

PICK_PATTERNS = [
    r"\bpicked\b", r"\bpicked up\b", r"\bpick up\b",
    r"picked\s*[☑✅✔️👍]",
]

NOISE_PATTERNS = [
    r"this message was deleted",
    r"created the username",
    r"changed their username",
    r"added ~",
    r"<this message was edited>",
    r"messages and calls are end-to-end encrypted",
    r"^\s*@⁨~?[^⁩]+⁩?\s*$",  # standalone mentions only
]

# ---------- PARSING ----------
LINE_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*([^:]+):\s*(.*)$"
)

def parse_chat(text):
    rows = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            # continuation of previous message
            if rows:
                rows[-1]["message"] += "\n" + line
            continue
        date_s, time_s, sender, msg = m.groups()
        rows.append({
            "date": date_s.strip(),
            "time": time_s.strip(),
            "sender": sender.strip(),
            "message": msg.strip(),
        })
    return rows

def is_noise(msg):
    low = msg.lower()
    for p in NOISE_PATTERNS:
        if re.search(p, low):
            return True
    return False

def is_escalation(msg):
    low = msg.lower()
    return any(k in low for k in ESCALATION_KEYWORDS)

def is_pick(msg):
    low = msg.lower()
    return any(re.search(p, low) for p in PICK_PATTERNS)

def parse_dt(date_s, time_s):
    for fmt in ("%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(f"{date_s} {time_s}", fmt)
        except ValueError:
            continue
    return None

# ---------- UPLOAD ----------
uploaded = st.file_uploader("Upload WhatsApp export (.zip or .txt)", type=["zip", "txt"])

if uploaded:
    # read
    if uploaded.name.endswith(".zip"):
        with zipfile.ZipFile(uploaded) as z:
            txt_name = [n for n in z.namelist() if n.endswith(".txt")][0]
            text = z.read(txt_name).decode("utf-8", errors="ignore")
    else:
        text = uploaded.read().decode("utf-8", errors="ignore")

    rows = parse_chat(text)

    # classify
    escalations = []
    picks = []

    for r in rows:
        if is_noise(r["message"]):
            continue
        dt = parse_dt(r["date"], r["time"])
        r["dt"] = dt
        if is_pick(r["message"]):
            picks.append(r)
        elif is_escalation(r["message"]):
            escalations.append(r)

    # match picks to escalations (first pick after each escalation, global queue)
    picks_sorted = sorted([p for p in picks if p["dt"]], key=lambda x: x["dt"])
    esc_sorted = sorted([e for e in escalations if e["dt"]], key=lambda x: x["dt"])

    used_picks = set()
    detail_rows = []
    for esc in esc_sorted:
        matched = None
        for i, p in enumerate(picks_sorted):
            if i in used_picks:
                continue
            if p["dt"] >= esc["dt"]:
                matched = p
                used_picks.add(i)
                break
        detail_rows.append({
            "Date": esc["date"],
            "Time": esc["time"],
            "Escalating Agent": esc["sender"],
            "Escalation Message": esc["message"].replace("\n", " ")[:200],
            "Picked": "Yes" if matched else "No",
            "Picked By": matched["sender"] if matched else "",
            "Pick Time": f'{matched["date"]} {matched["time"]}' if matched else "",
            "Wait (min)": int((matched["dt"] - esc["dt"]).total_seconds() / 60) if matched and esc["dt"] else "",
        })

    detail_df = pd.DataFrame(detail_rows)

    # summary per agent
    if not detail_df.empty:
        summary = detail_df.groupby("Escalating Agent").agg(
            Escalations=("Picked", "count"),
            Picked=("Picked", lambda s: (s == "Yes").sum()),
        ).reset_index()
        summary["Not Picked"] = summary["Escalations"] - summary["Picked"]
        summary["Pick Rate %"] = (summary["Picked"] / summary["Escalations"] * 100).round(1)
        summary = summary.sort_values("Escalations", ascending=False)

        st.subheader("📊 Summary per Agent")
        st.dataframe(summary, use_container_width=True)

        st.subheader("📋 Escalation Detail")
        st.dataframe(detail_df, use_container_width=True)

        # excel
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            summary.to_excel(w, sheet_name="Summary", index=False)
            detail_df.to_excel(w, sheet_name="Detail", index=False)
        buf.seek(0)

        st.download_button(
            "⬇️ Download Excel",
            data=buf,
            file_name="whatsapp_escalations.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.warning("No escalations found. Check your keyword list.")