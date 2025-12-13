import streamlit as st
import re
import time
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import requests
import pandas as pd
import json

# --- 1. CONFIGURATION & PROFESSIONAL STYLING ---
st.set_page_config(page_title="Support Assistant", layout="centered")

# CSS: Professional, No Emojis, Clean Font (Inter/Helvetica)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .stApp {
        background-color: #0e1117; 
        color: #e0e0e0;
    }

    h1 {
        font-weight: 600;
        letter-spacing: -0.5px;
        color: #ffffff;
        font-size: 2rem;
        text-align: center;
        margin-bottom: 2rem;
    }

    .stChatMessage {
        background-color: #1f2229 !important;
        border: 1px solid #2e3036;
        border-radius: 4px;
        box-shadow: none;
    }
    .stChatMessage[data-testid="user-message"] {
        background-color: #2b303b !important;
        border: 1px solid #3b404d;
    }

    .stButton button {
        border-radius: 6px;
        height: 3rem;
        font-weight: 500;
        background-color: #2b303b;
        color: white;
        border: 1px solid #3b404d;
        transition: all 0.2s ease;
        width: 100%;
    }
    .stButton button:hover {
        background-color: #3b404d;
        border-color: #555;
        color: white;
    }
    
    .stTextInput input {
        border-radius: 4px;
        border: 1px solid #2e3036;
        background-color: #1f2229;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

SHEET_NAME = "Safety_Reports"

# --- 2. DATA STRUCTURES ---
COMPLAINT_FIELDS = [
    "Timestamp", "Make", "Model", "Model_Year", "VIN", "City", "State",
    "Speed", "Crash", "Fire", "Injured", "Deaths", "Description",
    "Component", "Mileage", "Technician_Notes",
    "Brake_Condition", "Engine_Temperature", "Date_Complaint",
    "Input_Length", "Suspicion_Score", "User_Risk_Level"
]

FEEDBACK_FIELDS = ["Feedback_Timestamp", "Feedback_Topic", "Feedback_Cause_Help"]

# Field Descriptions for the LLM to understand what it needs to ask for
FIELD_DESCRIPTIONS = {
    "Make": "Vehicle brand (e.g., Toyota, Ford)",
    "Model": "Vehicle model name",
    "Model_Year": "4-digit year of the vehicle",
    "VIN": "17-character Vehicle Identification Number",
    "City": "City where incident happened",
    "State": "State code (e.g., CA, NY)",
    "Speed": "Vehicle speed during incident",
    "Crash": "Did a crash occur? (Yes/No)",
    "Fire": "Was there fire or smoke? (Yes/No)",
    "Injured": "Number of injuries (0 if none)",
    "Deaths": "Number of fatalities (0 if none)",
    "Description": "Detailed description of the event",
    "Component": "Failed part (e.g., Brakes, Airbag)",
    "Mileage": "Current vehicle mileage",
    "Technician_Notes": "Any mechanic notes or diagnosis",
    "Brake_Condition": "Condition of brakes (Normal, Spongy, Failed)",
    "Engine_Temperature": "Engine temp (Normal, Hot)",
    "Date_Complaint": "Date of incident (YYYY-MM-DD)",
    "Feedback_Topic": "Main topic of feedback",
    "Feedback_Cause_Help": "Perceived cause and suggested help"
}

# --- 3. UTILITIES ---

def get_groq_headers():
    try:
        api_key = st.secrets["groq"]["api_key"]
        return {"Authorization": f"Bearer {api_key}"}
    except:
        return None

def stream_text(text):
    """Generator for typing effect"""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.02)

def save_to_sheet(record, mode):
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        
        try:
            sheet = client.open(SHEET_NAME).sheet1
        except gspread.SpreadsheetNotFound:
            st.error(f"Spreadsheet '{SHEET_NAME}' not found.")
            return False

        row_data = []
        if mode == "COMPLAINT":
            row_data = [str(record.get(f, "")) for f in COMPLAINT_FIELDS]
        elif mode == "FEEDBACK":
            # Padding to align with Complaint structure if sharing same sheet
            row_data.extend([str(record.get(f, "")) for f in FEEDBACK_FIELDS])
            
        sheet.append_row(row_data)
        return True
    except Exception as e:
        st.error(f"Database Error: {e}")
        return False
    
def generate_ai_response(messages, target_field, current_value_status, mode):
    """
    Generates the AI response.
    If 'current_value_status' is 'MISSING', it asks for the 'target_field'.
    If 'current_value_status' is 'SMALL_TALK', it chats and then nudges back to 'target_field'.
    """
    headers = get_groq_headers()
    if not headers:
        return f"Please provide details for {target_field}."

    field_desc = FIELD_DESCRIPTIONS.get(target_field, target_field)
    
    system_prompt = f"""
    You are a professional, empathetic vehicle support assistant.
    Current Mode: {mode}.
    Goal: We need to collect information for the field: "{target_field}" ({field_desc}).

    User Input Status: {current_value_status}

    Instructions:
    1. If the user's last message was just casual (hi, hello, thanks) or off-topic:
       - Respond politely to the small talk.
       - Then, gently guide them back to asking for "{target_field}".
    
    2. If we are strictly asking for data:
       - Ask a clear, specific question to get "{target_field}".
       - Do not be repetitive. vary your phrasing slightly based on context.

    3. Keep it concise. Do not write long paragraphs.
    """

    payload = {
        "model": "llama-3.1-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages
        ],
        "temperature": 0.5,
        "max_tokens": 120
    }

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=5
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        else:
            return f"Could you please tell me about the {target_field}?"
    except Exception:
        return f"Could you please tell me about the {target_field}?"

def check_input_relevance(field, value):
    """
    Simple check to see if input is likely 'small talk' or actual data.
    Returns: (is_valid_data, clean_value)
    """
    val = str(value).strip()
    val_lower = val.lower()
    
    # Common small talk phrases
    small_talk = ["hi", "hello", "hey", "sup", "how are you", "good morning", "thanks", "ok", "okay"]
    
    if val_lower in small_talk:
        return False, val # It's conversation, not data
        
    # Validation logic for specific fields
    if field == "Model_Year":
        match = re.search(r"(19|20)\d{2}", val)
        if match: return True, match.group(0)
        return False, val # Likely conversation or invalid year
        
    if field in ["Crash", "Fire"]:
        if any(x in val_lower for x in ["yes", "yeah", "yep"]): return True, "YES"
        if any(x in val_lower for x in ["no", "nah", "nope"]): return True, "NO"
        # If they wrote a sentence without yes/no, might be conversation
        return False, val

    # Default: assume if it's not strictly small talk, it might be data
    # But if length is super short (1 char) and not yes/no, might be noise
    if len(val) < 2 and field not in ["Injured", "Deaths"]:
        return False, val

    return True, val

# --- 4. APP STATE ---
if "page" not in st.session_state: st.session_state.page = "INTRO" 
if "mode" not in st.session_state: st.session_state.mode = None
if "messages" not in st.session_state: st.session_state.messages = []
if "record" not in st.session_state: st.session_state.record = {}
if "current_q" not in st.session_state: st.session_state.current_q = None
if "intro_done" not in st.session_state: st.session_state.intro_done = False

st.markdown('<h1>Automated Support System</h1>', unsafe_allow_html=True)

# ---------------------------------------------------------
# PHASE 1: INTRO & SELECTION
# ---------------------------------------------------------
if st.session_state.page == "INTRO":
    
    if not st.session_state.intro_done:
        greeting = "Hello. I am your automated assistant. I can help you submit feedback or report a vehicle safety issue."
        st.write_stream(stream_text(greeting))
        st.session_state.intro_done = True
    else:
        st.write("Hello. I am your automated assistant. I can help you submit feedback or report a vehicle safety issue.")
    
    st.write("---")
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("General Feedback"):
            st.session_state.mode = "FEEDBACK"
            st.session_state.record = {k: None for k in FEEDBACK_FIELDS}
            st.session_state.record["Feedback_Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.session_state.current_q = "Feedback_Topic"
            
            # Initial AI Message
            start_msg = generate_ai_response([], "Feedback_Topic", "MISSING", "FEEDBACK")
            st.session_state.messages = [{"role": "assistant", "content": start_msg}]
            st.session_state.page = "CHAT"
            st.rerun()
            
    with c2:
        if st.button("Vehicle Safety Complaint"):
            st.session_state.mode = "COMPLAINT"
            st.session_state.record = {k: None for k in COMPLAINT_FIELDS}
            st.session_state.current_q = "Make" 
            
            # Initial AI Message
            start_msg = generate_ai_response([], "Make", "MISSING", "COMPLAINT")
            st.session_state.messages = [{"role": "assistant", "content": start_msg}]
            st.session_state.page = "CHAT"
            st.rerun()

# ---------------------------------------------------------
# PHASE 2: CHAT LOOP
# ---------------------------------------------------------
elif st.session_state.page == "CHAT":
    
    # Display History
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input Area
    if prompt := st.chat_input("Type your response..."):
        # 1. User Message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 2. Analyze Input
        curr_field = st.session_state.current_q
        is_data, clean_val = check_input_relevance(curr_field, prompt)

        if is_data:
            # It's valid data -> Save and Move Next
            st.session_state.record[curr_field] = clean_val
            
            # Find Next Field
            field_list = FEEDBACK_FIELDS if st.session_state.mode == "FEEDBACK" else COMPLAINT_FIELDS
            auto_fields = ["Timestamp", "Input_Length", "Suspicion_Score", "User_Risk_Level", "Feedback_Timestamp"]
            
            next_field = None
            for f in field_list:
                if f not in auto_fields and st.session_state.record.get(f) is None:
                    next_field = f
                    break
            
            if next_field:
                st.session_state.current_q = next_field
                # Generate AI Question for Next Field
                with st.spinner("..."):
                    ai_reply = generate_ai_response(
                        st.session_state.messages, 
                        next_field, 
                        "MISSING", 
                        st.session_state.mode
                    )
                
                st.session_state.messages.append({"role": "assistant", "content": ai_reply})
                with st.chat_message("assistant"):
                    st.write_stream(stream_text(ai_reply))
            
            else:
                # DONE
                st.session_state.page = "REVIEW"
                st.rerun()

        else:
            # It is SMALL TALK or Invalid -> Don't save, just Chat back
            # We stay on the SAME current_q
            with st.spinner("..."):
                ai_reply = generate_ai_response(
                    st.session_state.messages, 
                    curr_field, 
                    "SMALL_TALK", 
                    st.session_state.mode
                )
            
            st.session_state.messages.append({"role": "assistant", "content": ai_reply})
            with st.chat_message("assistant"):
                st.write_stream(stream_text(ai_reply))

# ---------------------------------------------------------
# PHASE 3: REVIEW & EDIT
# ---------------------------------------------------------
elif st.session_state.page == "REVIEW":
    st.markdown("### Review Your Report")
    st.info("Please review your answers below. You can edit any field before submitting.")
    
    display_data = {
        k: v for k, v in st.session_state.record.items() 
        if v is not None and "Timestamp" not in k and "Score" not in k and "Risk" not in k
    }
    
    df_review = pd.DataFrame(list(display_data.items()), columns=["Field", "Value"])
    
    edited_df = st.data_editor(
        df_review,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "Field": st.column_config.TextColumn("Field", disabled=True),
            "Value": st.column_config.TextColumn("Value")
        }
    )
    
    col_submit, col_back = st.columns([2, 1])
    
    with col_submit:
        if st.button("Confirm & Submit Report"):
            for index, row in edited_df.iterrows():
                st.session_state.record[row["Field"]] = row["Value"]
            
            if st.session_state.mode == "COMPLAINT":
                st.session_state.record["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.session_state.record["User_Risk_Level"] = "LOW"
                st.session_state.record["Input_Length"] = sum(len(str(v)) for v in st.session_state.record.values() if v)
            
            with st.spinner("Submitting to database..."):
                success = save_to_sheet(st.session_state.record, st.session_state.mode)
            
            if success:
                st.session_state.page = "SUCCESS"
                st.rerun()
            else:
                st.error("Submission failed. Please check your connection.")

# ---------------------------------------------------------
# PHASE 4: SUCCESS
# ---------------------------------------------------------
elif st.session_state.page == "SUCCESS":
    st.balloons()
    st.success("Report submitted successfully.")
    st.write("We have received your details. Thank you for your time.")
    
    if st.button("Start New Session"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
