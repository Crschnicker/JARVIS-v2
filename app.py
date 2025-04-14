# --- START OF IMPROVED app.py ---
import os
import logging
import json
import time
import re
import base64
import uuid
from flask import (
    Flask, request, jsonify, render_template,
    session, redirect, url_for, flash, Response, stream_with_context
)
from flask_session import Session
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any, List, Iterator, Tuple
from enhanced_halo_api import HaloAPI, HaloAuthenticationError
import prompts # Use updated prompts
import functools

# --- Enhanced Logging Setup ---
log_level = os.getenv("LOG_LEVEL", "DEBUG")
numeric_level = getattr(logging, log_level.upper(), logging.INFO)
logging.basicConfig(
    level=numeric_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logging.info(f"Initialized logging at level: {log_level}")

# --- Configuration ---
load_dotenv()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # Changed to DEBUG for better diagnostics

# --- Initialize Flask App & Session ---
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key: raise ValueError("FLASK_SECRET_KEY not set.")
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
Session(app)

# --- Initialize Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY: raise ValueError("GEMINI_API_KEY not set.")
genai.configure(api_key=GEMINI_API_KEY)
try:
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    logging.info("Using Gemini model: gemini-1.5-flash")
except Exception as e_model:
    logging.error(f"Failed to initialize Gemini model 'gemini-1.5-flash': {e_model}", exc_info=True)
    # Make log level configurable
    if os.getenv("FLASK_DEBUG"):
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    raise

safety_settings = [
    {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
    for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
              "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]
]
generation_config = genai.GenerationConfig()

# Create user data directory if it doesn't exist
USER_DATA_DIR = 'user_data'
if not os.path.exists(USER_DATA_DIR):
    os.makedirs(USER_DATA_DIR)

# --- Helper Functions ---
def safe_json_parse(json_string: str) -> dict:
    """Safely parse a JSON string, cleaning common markdown/code fences."""
    try:
        # Remove potential markdown code fences and strip whitespace
        cleaned = json_string.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        if not cleaned:
            logging.warning("Attempted to parse empty JSON string.")
            return {}
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logging.error(f"JSON Parse Error: {e}. String (first 100 chars): '{json_string[:100]}'")
        # Attempt to fix common issues like trailing commas (simple case)
        try:
            import json5
            return json5.loads(cleaned) # Use json5 for more lenient parsing
        except: # If json5 also fails, return empty dict
            logging.error("JSON5 parsing also failed.")
            return {}
    except Exception as e: # Catch other potential errors
        logging.error(f"Unexpected JSON Parse Error: {e}. Str: '{json_string[:100]}'")
        return {}


def call_gemini(prompt: str, is_json_output: bool = False,
                structured_content: list = None, stream: bool = False) -> Any:
    """Call Gemini with enhanced support for structured content and streaming"""
    logging.debug(f"Calling Gemini. JSON out: {is_json_output}, Streaming: {stream}")
    try:
        # Prepare content based on structured or simple prompt
        content_to_send = structured_content if structured_content else prompt

        if stream:
            # Return the stream iterator directly
            return gemini_model.generate_content(
                content_to_send,
                generation_config=generation_config,
                safety_settings=safety_settings,
                stream=True
            )
        else:
            # Make blocking call for non-streaming response
            response = gemini_model.generate_content(
                content_to_send,
                generation_config=generation_config,
                safety_settings=safety_settings
            )

            # --- Robust Text Extraction ---
            response_text = ""
            try:
                 if response.parts:
                     response_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
                 elif hasattr(response, 'text'):
                     response_text = response.text
            except ValueError as ve:
                 # Handle cases where accessing response parts/text fails (e.g., blocked content)
                 logging.warning(f"ValueError accessing Gemini response parts/text: {ve}")
                 if response.prompt_feedback and response.prompt_feedback.block_reason:
                      logging.warning(f"Gemini response blocked due to: {response.prompt_feedback.block_reason}")
                      # Return specific messages based on context
                      return "My response was blocked due to safety settings." if not is_json_output else "{}"
                 else:
                      logging.warning(f"Unexpected Gemini response structure or access error. Full object: {response}")
                      return "Received an unusual response from the AI." if not is_json_output else "{}"


            # Check for explicitly empty or blocked response AFTER extraction attempt
            if not response_text:
                 if response.prompt_feedback and response.prompt_feedback.block_reason:
                      logging.warning(f"Gemini blocked: {response.prompt_feedback.block_reason}")
                      return "Response blocked due to safety settings." if not is_json_output else "{}"
                 else:
                      # Log the full response if text is empty without explicit block reason
                      logging.warning(f"Gemini returned empty text content. Full object: {response}")
                      return "Received an empty response from the AI." if not is_json_output else "{}"

            logging.debug(f"Gemini Raw Resp: {response_text[:200]}...")

            # If JSON output is expected, parse it here for validation
            if is_json_output:
                parsed_json = safe_json_parse(response_text)
                logging.debug(f"Gemini JSON Parsed: {parsed_json}")
                return parsed_json # Return the parsed dict/list
            else:
                return response_text # Return raw text

    except Exception as e:
        logging.exception(f"Gemini API call failed:") # Log full traceback
        # Provide different errors based on context
        return {"error": "AI connection failed"} if is_json_output else "Sorry, I'm having trouble connecting to the AI service right now."


def call_gemini_with_image(text_prompt: str, image_data: bytes, mime_type: str) -> str:
    """Process a text prompt with an image using Gemini's multimodal capabilities"""
    logging.debug(f"Calling Gemini with image ({mime_type}) and prompt: {text_prompt[:100]}...")
    try:
        # Prepare multimodal content
        image_part = {
            "mime_type": mime_type,
            "data": base64.b64encode(image_data).decode('utf-8')
        }
        multimodal_parts = [text_prompt, image_part] # Simpler format usually works

        response = gemini_model.generate_content(
            multimodal_parts,
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        # --- Robust Text Extraction (adapted for multimodal) ---
        response_text = ""
        try:
             if response.parts:
                 response_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
             elif hasattr(response, 'text'):
                 response_text = response.text
        except ValueError as ve:
             logging.warning(f"ValueError accessing Gemini multimodal response parts/text: {ve}")
             if response.prompt_feedback and response.prompt_feedback.block_reason:
                  logging.warning(f"Gemini image response blocked due to: {response.prompt_feedback.block_reason}")
                  return "My analysis of the image was blocked due to safety settings."
             else:
                  logging.warning(f"Unexpected Gemini response structure or access error. Full object: {response}")
                  return "Received an unusual response while analyzing the image."

        if not response_text:
             if response.prompt_feedback and response.prompt_feedback.block_reason:
                  logging.warning(f"Gemini blocked multimodal: {response.prompt_feedback.block_reason}")
                  return "Response blocked due to safety settings for the image/prompt."
             else:
                  logging.warning(f"Gemini returned empty text content for image analysis. Full object: {response}")
                  return "I couldn't analyze this image properly or generate a text response."

        logging.debug(f"Gemini Image Resp Text: {response_text[:200]}...")
        return response_text

    except Exception as e:
        logging.exception("Error processing image with Gemini:")
        return f"Sorry, an error occurred while analyzing the image: {str(e)}"


def parse_refined_content(raw_text: str) -> Dict[str, str]:
    """Parses the Summary/Details from the refinement prompt response."""
    summary = ""
    details = ""

    # Try precise markers first
    summary_match = re.search(r"Refined Summary:\s*(.*)", raw_text, re.IGNORECASE | re.MULTILINE)
    details_match = re.search(r"Refined Details:\s*(.*)", raw_text, re.IGNORECASE | re.DOTALL)

    if summary_match:
        summary = summary_match.group(1).strip()
    if details_match:
        details = details_match.group(1).strip()

    # Fallback/cleanup logic
    if not summary and not details:
        # If markers fail, assume the whole text might be the details if it's long enough
        if len(raw_text.strip()) > 30: # Arbitrary threshold
            details = raw_text.strip()
            # Try to extract a first line as summary
            first_line_match = re.match(r"^(.*?)\n", details)
            if first_line_match:
                 potential_summary = first_line_match.group(1).strip()
                 if len(potential_summary) < 80: # Avoid using long lines as summary
                      summary = potential_summary
                      # Remove the extracted summary line from details (optional)
                      # details = details[len(summary):].lstrip('\n').strip()

    elif not details and summary and '\n' in summary:
         # If summary has newlines, maybe model put details there
         parts = summary.split('\n', 1)
         summary = parts[0].strip()
         details = parts[1].strip()

    # Final cleanup of stray markers
    if summary.startswith("Refined Summary:"): summary = summary[len("Refined Summary:"):].strip()
    if details.startswith("Refined Details:"): details = details[len("Refined Details:"):].strip()

    logging.debug(f"Parsed Refined Content - Summary: '{summary}', Details: '{details[:100]}...'")
    return {"summary": summary, "details": details}


def get_persistent_entities(username: str) -> Dict[str, Any]:
    """Retrieve persistent entities for this user from previous sessions."""
    user_file = os.path.join(USER_DATA_DIR, f"{username.replace('@', '_at_').replace('.', '_dot_')}.json") # Safer filename
    if os.path.exists(user_file):
        try:
            with open(user_file, 'r', encoding='utf-8') as f: # Specify encoding
                data = json.load(f)
                # Basic validation: ensure it's a dictionary
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError, TypeError) as e: # Catch more specific errors
            logging.error(f"Error reading persistent entity file {user_file}: {e}")
            return {}
    return {}

def save_persistent_entities(username: str, entities: Dict[str, Any]):
    """Save important entities that should persist between sessions."""
    # Only save certain entity types known to be useful across sessions
    persistent_keys = ['client_id', 'client_name', 'user_id', 'user_name', 'site_id', 'site_name']
    # Filter out None values before saving
    to_save = {k: v for k, v in entities.items() if k in persistent_keys and v is not None}

    if not to_save:
        logging.debug("No new persistent entities to save.")
        return

    user_file = os.path.join(USER_DATA_DIR, f"{username.replace('@', '_at_').replace('.', '_dot_')}.json") # Safer filename

    # Read existing data first to merge, handle potential file corruption
    existing_data = get_persistent_entities(username) # Reuse read logic

    # Update existing data with new values
    existing_data.update(to_save)

    # Save merged data
    try:
        with open(user_file, 'w', encoding='utf-8') as f: # Specify encoding
            json.dump(existing_data, f, indent=2) # Add indent for readability
            logging.debug(f"Saved persistent entities for {username}: {to_save.keys()}")
    except (OSError, TypeError) as e:
        logging.error(f"Error writing persistent entity file {user_file}: {e}")


def attempt_state_recovery_from_history(history: list) -> Dict[str, Any]:
    """Attempt to recover basic state from conversation history if session was lost."""
    if not history or len(history) < 2:
        return {}

    # Look for state indicators in previous exchanges (last bot message is key)
    last_bot_message = next((msg['content'] for msg in reversed(history) if msg['role'] == 'model'), None)
    if not last_bot_message:
        return {
            'state_version': 1, # Mark state version
            'recovered': True,
            'status': 'IDLE', # Default status
            'entities': {},
            'missing_entities': [],
            'current_action': None
        }

    recovered_state = {
        'state_version': 1, # Mark state version
        'recovered': True,
        'status': 'IDLE', # Default status
        'entities': {},
        'missing_entities': [],
        'current_action': None
    }

    # Basic recovery based on common phrases in the last bot message
    if "need a few more details" in last_bot_message or \
       "What's the" in last_bot_message or \
       "What is the" in last_bot_message or \
       "Need the" in last_bot_message or \
       "ask for the" in last_bot_message or \
       "still need the" in last_bot_message:
        recovered_state['status'] = "AWAITING_CLARIFICATION"
        # Try to guess the action/missing field (very basic)
        if "ticket" in last_bot_message: recovered_state['current_action'] = "CREATE_TICKET" # Default guess
        if "summary" in last_bot_message: recovered_state['missing_entities'] = ["summary"]
        elif "details" in last_bot_message: recovered_state['missing_entities'] = ["details"]
        elif "client" in last_bot_message: recovered_state['missing_entities'] = ["client_id"]
        elif "note" in last_bot_message: recovered_state['missing_entities'] = ["note_text"]
        elif "ticket ID" in last_bot_message: recovered_state['missing_entities'] = ["ticket_id"]

    # ENHANCED CONFIRMATION RECOVERY
    elif "Confirm" in last_bot_message or \
         "proceed?" in last_bot_message or \
         "Create this?" in last_bot_message or \
         "Create this ticket?" in last_bot_message or \
         "Update ticket #" in last_bot_message or \
         "Add this" in last_bot_message and "note" in last_bot_message:
        
        recovered_state['status'] = "AWAITING_CONFIRMATION"
        
        # Improved detection of fields for recovery
        # Try to recover client details from the confirmation message
        if "Create this ticket?" in last_bot_message or "draft ticket for" in last_bot_message:
            recovered_state['current_action'] = "CREATE_TICKET"
            recovered_state['action_to_confirm'] = "CREATE_TICKET"
            
            # Try to extract client name
            client_match = re.search(r"draft ticket for \*\*(.+?)\*\*", last_bot_message)
            if client_match:
                client_name = client_match.group(1)
                recovered_state['entities']['client_name'] = client_name
                logging.info(f"Recovered client name: {client_name}")
            
            # Try to extract summary and details
            summary_match = re.search(r"\*\*Summary:\*\* (.+?)(?:\n|\.\.\.)", last_bot_message)
            if summary_match:
                summary = summary_match.group(1)
                recovered_state['entities']['summary'] = summary
                logging.info(f"Recovered summary: {summary}")
            
            details_match = re.search(r"\*\*Details:\*\* (.+?)(?=\n\n|$)", last_bot_message)
            if details_match:
                details = details_match.group(1)
                recovered_state['entities']['details'] = details
                logging.info(f"Recovered details: {details[:50]}...")
                
            # Try to rebuild confirmation data
            if "client_name" in recovered_state['entities'] and "summary" in recovered_state['entities'] and "details" in recovered_state['entities']:
                recovered_state['confirmation_data'] = {
                    "summary": recovered_state['entities']['summary'],
                    "details": recovered_state['entities']['details']
                }
                if "client_id" in recovered_state['entities']:
                    recovered_state['confirmation_data']["client_id"] = recovered_state['entities']['client_id']
        
        elif "note" in last_bot_message:
            recovered_state['current_action'] = "ADD_NOTE"
            recovered_state['action_to_confirm'] = "ADD_NOTE"
            
            # Try to extract ticket_id
            ticket_match = re.search(r"to ticket #(\d+)", last_bot_message)
            if ticket_match:
                ticket_id = int(ticket_match.group(1))
                recovered_state['entities']['ticket_id'] = ticket_id
                logging.info(f"Recovered ticket ID: {ticket_id}")
            
            # Try to extract privacy setting
            if "Private note" in last_bot_message:
                recovered_state['entities']['is_private'] = True
            elif "Public note" in last_bot_message:
                recovered_state['entities']['is_private'] = False
                
            # Try to extract time_taken
            time_match = re.search(r"\((\d+) mins\)", last_bot_message)
            if time_match:
                time_taken = int(time_match.group(1))
                recovered_state['entities']['time_taken'] = time_taken
                logging.info(f"Recovered time taken: {time_taken}")
                
            # Try to extract note text
            note_match = re.search(r"'(.+?)'(?=\n\n|$)", last_bot_message)
            if note_match:
                note_text = note_match.group(1)
                recovered_state['entities']['note_text'] = note_text
                logging.info(f"Recovered note text: {note_text[:50]}...")
                
            # Try to rebuild confirmation data
            if "ticket_id" in recovered_state['entities'] and "note_text" in recovered_state['entities']:
                recovered_state['confirmation_data'] = {
                    "ticket_id": recovered_state['entities']['ticket_id'],
                    "note_text": recovered_state['entities']['note_text']
                }
                if "time_taken" in recovered_state['entities']:
                    recovered_state['confirmation_data']["time_taken"] = recovered_state['entities']['time_taken']
                if "is_private" in recovered_state['entities']:
                    recovered_state['confirmation_data']["is_private"] = recovered_state['entities']['is_private']
                
        elif "Update" in last_bot_message:
            recovered_state['current_action'] = "UPDATE_TICKET"
            recovered_state['action_to_confirm'] = "UPDATE_TICKET"

    logging.info(f"Attempted state recovery: Status={recovered_state['status']}, Action={recovered_state['current_action']}")
    return recovered_state

# --- Get Halo API Instance ---
def get_halo_api_instance() -> Optional[HaloAPI]:
    # Check basic session validity first
    if 'logged_in' not in session or 'halo_tokens' not in session or 'username' not in session:
        logging.warning("get_halo_api_instance called without logged_in session or tokens.")
        return None

    token_info = session['halo_tokens']
    username_for_log = session['username']

    # Validate token structure
    if not isinstance(token_info, dict) or not all(k in token_info for k in ["access_token", "refresh_token", "token_expires_at"]):
        logging.error(f"Incomplete or invalid token info structure for '{username_for_log}'. Clearing session.")
        session.clear()
        flash("Session data corrupted. Please log in again.", "error")
        return None

    # Configuration check
    auth_url=os.getenv("HALO_AUTH_URL"); client_id=os.getenv("HALO_CLIENT_ID");
    api_base=os.getenv("HALO_API_BASE"); tenant=os.getenv("HALO_TENANT")
    if not all([auth_url, client_id, api_base]):
         logging.error("Server configuration error: Missing required Halo API environment variables.")
         # This is a server config issue, don't clear user session, but can't create instance
         return None

    halo_api = None
    try:
        # Initialize HaloAPI instance using token info from session
        halo_api = HaloAPI(
            auth_url=auth_url, client_id=client_id, api_base_url=api_base, tenant=tenant,
            access_token=token_info['access_token'], refresh_token=token_info['refresh_token'],
            token_expires_at=token_info['token_expires_at'], username_for_log=username_for_log
        )

        # No need to force _ensure_token_valid here.
        # The _request method handles token expiry and refresh automatically.
        # Let's verify the instance was created successfully (e.g., access token is present)
        if not halo_api._access_token:
            logging.error(f"HaloAPI instance created for {username_for_log}, but access token is missing internally.")
            # This might indicate an issue during init despite checks, treat as failure
            return None

        logging.debug(f"Successfully created Halo instance for '{username_for_log}'.")
        return halo_api

    except HaloAuthenticationError as auth_err:
        # This typically happens on initial PW auth, less likely here, but handle just in case
        logging.error(f"Halo Auth error during token initialization for '{username_for_log}': {auth_err}")
        session.clear(); flash("Authentication failed. Please log in again.", "error"); return None
    except ValueError as val_err:
        # Catch configuration or token value errors from __init__
        logging.error(f"Value error creating HaloAPI instance for '{username_for_log}': {val_err}")
        # Don't clear session for config errors, but maybe for bad token expiry?
        if "token_expires_at" in str(val_err):
             session.clear(); flash("Invalid session data. Please log in again.", "error")
        return None
    except Exception as e:
        # Catch unexpected errors during API instance creation
        logging.exception(f"Unexpected error creating Halo instance for '{username_for_log}':")
        return None


# --- Core Chat Logic ---
def process_chat_message(
    halo_api: HaloAPI, user_message: str, history: list, state: Dict[str, Any]
) -> Dict[str, Any]:
    """Processes user message, manages state, interacts with APIs."""
    if not halo_api:
        logging.error("process_chat_message called with invalid halo_api instance.")
        return {
            "response": "Error: Connection to Halo is not available. Please try logging in again.",
            "final_state": state, "token_refreshed": False, "new_token_info": None, "stream": False
        }

    # --- Prepare initial values ---
    initial_token_info = halo_api.get_current_token_info() # Get current token state BEFORE request
    logged_in_user = session.get('username', 'Unknown User')
    bot_response = "Sorry, something went wrong."
    token_refreshed_in_this_call = False
    new_token_info_after_call = None
    # Determine potential need for streaming early (can be overridden later)
    needs_streaming = len(user_message) > 150 or "explain" in user_message.lower() or "list" in user_message.lower()


    # --- State Management Setup ---
    # Ensure state is a dict, attempt recovery if empty
    if not state or not isinstance(state, dict):
        logging.warning("Chat state missing or invalid, attempting recovery.")
        state = attempt_state_recovery_from_history(history)
        session['chat_state'] = state # Store recovered state in session
        session.modified = True  # Mark session as modified to ensure it's saved
    elif 'state_version' not in state:
         state['state_version'] = 1 # Add version if missing

    # Ensure 'entities' key exists
    state.setdefault('entities', {})

    # Merge persistent entities if state entities are empty (e.g., after recovery or reset)
    if not state['entities']:
         persistent_entities = get_persistent_entities(logged_in_user)
         if persistent_entities:
              state['entities'].update(persistent_entities) # Use update to merge
              logging.debug("Loaded persistent entities into empty state.")

    # Log the initial state for debugging
    logging.info(f"Processing msg: '{user_message}' for user '{logged_in_user}'")
    logging.debug(f"INCOMING STATE BEFORE PROCESSING: {json.dumps(state, indent=2)}")

    # 1. NLU
    nlu_prompt = prompts.get_nlu_prompt(user_message, history, state)
    nlu_result = call_gemini(nlu_prompt, is_json_output=True) # Returns dict directly now

    # --- Handle NLU Failure ---
    if not isinstance(nlu_result, dict) or not nlu_result.get("intent"):
         logging.error(f"NLU failed or returned invalid format: {nlu_result}")
         # Fallback response if NLU fails critically
         action_result = {
             "success": False, "error": "NLU processing failed.",
             "clarification_needed": True,
             "clarification_question": "Sorry, I had trouble understanding that. Could you please rephrase?"
         }
         intent = "AMBIGUOUS"
         entities = {}
         # Set bot_response directly since normal flow is interrupted
         bot_response = action_result["clarification_question"]

    else: # NLU Succeeded
        intent = nlu_result.get("intent", "AMBIGUOUS")
        entities = nlu_result.get("entities", {})
        logging.info(f"NLU Result: Intent='{intent}', Entities={entities}")

    # --- State Management & Context Handling ---
    current_state = state # Use mutable state dict for modifications
    current_entities = current_state.get("entities", {}) # Ensure entities exist
    current_action = current_state.get("current_action")
    current_status = current_state.get("status")
    action_to_confirm = current_state.get("action_to_confirm")
    confirmation_data = current_state.get("confirmation_data", {})
    
    # IMPORTANT FIX: If user says "Yes" and we're awaiting confirmation, ensure we add confirmation=yes
    # even if the NLU didn't extract it
    if (current_status == "AWAITING_CONFIRMATION" and 
        intent == "CONFIRM_ACTION" and 
        ("confirmation" not in entities or not entities["confirmation"])):
        entities["confirmation"] = "yes"
        logging.info("Auto-added confirmation=yes to maintain context for confirmation flow")
    
    # If we're in AWAITING_CONFIRMATION but the intent is not CONFIRM/CANCEL, try to handle as CONFIRM
    # if the message looks like an affirmative
    if (current_status == "AWAITING_CONFIRMATION" and 
        intent not in ["CONFIRM_ACTION", "CANCEL_ACTION"] and
        action_to_confirm and confirmation_data):
        affirmative_words = ["yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "proceed", "go ahead", "do it"]
        if any(word in user_message.lower() for word in affirmative_words):
            intent = "CONFIRM_ACTION"
            entities["confirmation"] = "yes"
            logging.info(f"Changed intent to CONFIRM_ACTION based on affirmative response during confirmation")

    # Merge newly extracted entities into current state entities
    # Prioritize newly provided non-None values, but don't wipe existing IDs with None
    if entities:  # Only merge if NLU extracted something
        for key, value in entities.items():
            if key == "client_id" and value is not None:
                # Ensure client_id is always an integer
                try:
                    current_entities[key] = int(value)
                    logging.debug(f"Converted client_id from {value} to {current_entities[key]}")
                except (ValueError, TypeError):
                    logging.warning(f"Failed to convert client_id '{value}' to integer - storing as is")
                    current_entities[key] = value
            elif value is not None:
                current_entities[key] = value
                
    # Ensure current_state has the entities dict
    current_state["entities"] = current_entities

    # Log the state after entity merging
    logging.debug(f"STATE AFTER ENTITY MERGING: {json.dumps(current_state, indent=2)}")

    # Default action result (re-initialize inside flow if NLU was okay)
    action_result: Dict[str, Any] = {
        "success": False, "error": None, "data": None,
        "clarification_needed": False, "clarification_question": None, "message": None
    }

    # --- Flow Control ---
    if intent == "START_OVER":
        logging.info("Resetting state due to START_OVER intent.")
        bot_response = "Okay, starting over. What can I help you with?"
        current_state = {
            'state_version': 1,
            'entities': {},
            'status': 'IDLE'
        } # Reset with minimal structure
        action_result = {"success": True, "message": "Reset state."}

    elif current_status == "AWAITING_CONFIRMATION" and intent in ["CONFIRM_ACTION", "CANCEL_ACTION"]:
        action_to_confirm = current_state.get("action_to_confirm")
        confirmation_data = current_state.get("confirmation_data", {})
        
        # Critical debugging for confirmation data
        logging.info(f"Handling confirmation. Intent: {intent}")
        logging.info(f"Action to confirm: {action_to_confirm}")
        logging.info(f"Confirmation data: {json.dumps(confirmation_data, default=str)}")

        if intent == "CONFIRM_ACTION":
            logging.info(f"User confirmed action: {action_to_confirm}")
            logging.info(f"CONFIRMATION STATE: {json.dumps(current_state, default=str)}")
            logging.info(f"CONFIRMATION DATA CONTENTS: {json.dumps(confirmation_data, default=str)}")
            
            if not action_to_confirm or not confirmation_data:
                logging.error(f"Confirmation context missing. Current state: {json.dumps(current_state, default=str)}")
                bot_response = "I couldn't find what you're trying to confirm. Let's start over with your request."
                action_result = {"success": False, "error": "Confirmation context missing"}
                current_state = {
                    'state_version': 1,
                    'entities': current_entities,  # Keep entities but reset state
                    'status': 'IDLE'
                }
            else:
                current_state["status"] = "EXECUTING" # Update status before API call
                try:
                    # --- Execute Confirmed Action ---
                    if action_to_confirm == "CREATE_TICKET":
                        # IMPORTANT FIX: Ensure we have a valid client_id as an integer
                        if "client_id" not in confirmation_data:
                            logging.warning(f"client_id missing from confirmation_data: {json.dumps(confirmation_data, default=str)}")
                            logging.debug(f"Checking current_entities for client_id: {json.dumps(current_entities, default=str)}")

                            # Try to get it from current_entities
                            if "client_id" in current_entities:
                                try:
                                    logging.info(f"Found client_id in entities: {current_entities['client_id']} (type: {type(current_entities['client_id']).__name__})")
                                    confirmation_data["client_id"] = int(current_entities["client_id"])
                                    logging.info(f"Added client_id {confirmation_data['client_id']} from state entities")
                                except (ValueError, TypeError):
                                    error_msg = f"Invalid client_id format: {current_entities['client_id']}"
                                    logging.error(error_msg)
                                    action_result = {"success": False, "error": error_msg}
                                    bot_response = f"Sorry, there was an issue with the client ID. Please try creating the ticket again."
                                    current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                                    return {
                                        "response": bot_response,
                                        "final_state": current_state,
                                        "token_refreshed": token_refreshed_in_this_call,
                                        "new_token_info": new_token_info_after_call,
                                        "stream": False,
                                        "action_result": action_result,
                                        "user_message": user_message,
                                        "history": history
                                    }
                            else:
                                error_msg = "Missing client_id in confirmation data and state entities"
                                logging.error(error_msg)
                                logging.debug(f"Full current_entities: {json.dumps(current_entities, default=str)}")
                                logging.debug(f"Full confirmation_data: {json.dumps(confirmation_data, default=str)}")
                                action_result = {"success": False, "error": error_msg}
                                bot_response = "Sorry, I couldn't find the client information. Please try creating the ticket again."
                                current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                                return {
                                    "response": bot_response,
                                    "final_state": current_state,
                                    "token_refreshed": token_refreshed_in_this_call,
                                    "new_token_info": new_token_info_after_call,
                                    "stream": False,
                                    "action_result": action_result,
                                    "user_message": user_message,
                                    "history": history
                                }
                        
                        # Ensure client_id is an integer
                        try:
                            if not isinstance(confirmation_data["client_id"], int):
                                logging.info(f"Converting client_id from {confirmation_data['client_id']} (type: {type(confirmation_data['client_id']).__name__}) to integer")
                                confirmation_data["client_id"] = int(confirmation_data["client_id"])
                                logging.info(f"Successfully converted client_id to {confirmation_data['client_id']} (type: {type(confirmation_data['client_id']).__name__})")
                        except (ValueError, TypeError):
                            error_msg = f"Failed to convert client_id to integer: {confirmation_data['client_id']}"
                            logging.error(error_msg)
                            action_result = {"success": False, "error": error_msg}
                            bot_response = "Sorry, there was an issue with the client ID format. Please try creating the ticket again."
                            current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                            return {
                                "response": bot_response,
                                "final_state": current_state,
                                "token_refreshed": token_refreshed_in_this_call,
                                "new_token_info": new_token_info_after_call,
                                "stream": False,
                                "action_result": action_result,
                                "user_message": user_message,
                                "history": history
                            }
                        
                        # Ensure we have required fields
                        required_fields = ["client_id", "summary", "details"]
                        missing_fields = [field for field in required_fields if field not in confirmation_data]
                        
                        if missing_fields:
                            error_msg = f"Missing required fields for ticket creation: {missing_fields}"
                            logging.error(error_msg)
                            action_result = {"success": False, "error": error_msg}
                            bot_response = f"Sorry, I'm missing some information needed to create the ticket ({', '.join(missing_fields)}). Please try again."
                            current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                            return {
                                "response": bot_response,
                                "final_state": current_state,
                                "token_refreshed": token_refreshed_in_this_call,
                                "new_token_info": new_token_info_after_call,
                                "stream": False,
                                "action_result": action_result,
                                "user_message": user_message,
                                "history": history
                            }
                        
                        # Log the exact payload we're sending
                        logging.info(f"Executing CREATE_TICKET with payload: {json.dumps(confirmation_data, default=str)}")
                        
                        # Make the API call
                        success, data, error = halo_api.create_ticket(**confirmation_data)
                        
                        if success:
                            action_result = {
                                "success": True, 
                                "data": data, 
                                "message": f"Ticket created successfully!"
                            }
                            if isinstance(data, dict) and 'id' in data:
                                action_result["message"] = f"Ticket #{data['id']} created successfully!"
                        else:
                            error_msg = f"Ticket creation failed: {error}"
                            logging.error(error_msg)
                            action_result = {
                                "success": False, 
                                "error": error_msg, 
                                "message": f"Failed to create ticket: {error}"
                            }
                    
                    elif action_to_confirm == "ADD_NOTE":
                        # Similar validation for ADD_NOTE
                        if "ticket_id" not in confirmation_data or "note_text" not in confirmation_data:
                            error_msg = "Missing required fields for adding note"
                            logging.error(error_msg)
                            action_result = {"success": False, "error": error_msg}
                            bot_response = "Sorry, I'm missing some information needed to add the note. Please try again."
                            current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                            return {
                                "response": bot_response,
                                "final_state": current_state,
                                "token_refreshed": token_refreshed_in_this_call,
                                "new_token_info": new_token_info_after_call,
                                "stream": False,
                                "action_result": action_result,
                                "user_message": user_message,
                                "history": history
                            }
                        
                        # Ensure ticket_id is an integer
                        try:
                            if not isinstance(confirmation_data["ticket_id"], int):
                                confirmation_data["ticket_id"] = int(confirmation_data["ticket_id"])
                        except (ValueError, TypeError):
                            error_msg = f"Failed to convert ticket_id to integer: {confirmation_data['ticket_id']}"
                            logging.error(error_msg)
                            action_result = {"success": False, "error": error_msg}
                            bot_response = "Sorry, there was an issue with the ticket ID format. Please try adding the note again."
                            current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                            return {
                                "response": bot_response,
                                "final_state": current_state,
                                "token_refreshed": token_refreshed_in_this_call,
                                "new_token_info": new_token_info_after_call,
                                "stream": False,
                                "action_result": action_result,
                                "user_message": user_message,
                                "history": history
                            }
                        
                        # Make the API call
                        logging.info(f"Executing ADD_NOTE with payload: {json.dumps(confirmation_data, default=str)}")
                        success, data, error = halo_api.add_note(**confirmation_data)
                        
                        if success:
                            action_result = {
                                "success": True, 
                                "data": data, 
                                "message": f"Note added successfully to ticket #{confirmation_data['ticket_id']}!"
                            }
                        else:
                            error_msg = f"Note addition failed: {error}"
                            logging.error(error_msg)
                            action_result = {
                                "success": False, 
                                "error": error_msg, 
                                "message": f"Failed to add note: {error}"
                            }
                            
                    elif action_to_confirm == "UPDATE_TICKET":
                        # Validation for UPDATE_TICKET
                        if "ticket_id" not in confirmation_data or "update_payload" not in confirmation_data:
                            error_msg = "Missing required fields for updating ticket"
                            logging.error(error_msg)
                            action_result = {"success": False, "error": error_msg}
                            bot_response = "Sorry, I'm missing some information needed to update the ticket. Please try again."
                            current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                            return {
                                "response": bot_response,
                                "final_state": current_state,
                                "token_refreshed": token_refreshed_in_this_call,
                                "new_token_info": new_token_info_after_call,
                                "stream": False,
                                "action_result": action_result,
                                "user_message": user_message,
                                "history": history
                            }
                            
                        # Make the API call
                        logging.info(f"Executing UPDATE_TICKET with payload: {json.dumps(confirmation_data, default=str)}")
                        success, data, error = halo_api.update_ticket(
                            ticket_id=confirmation_data['ticket_id'],
                            update_data=confirmation_data['update_payload']
                        )
                        
                        if success:
                            action_result = {
                                "success": True, 
                                "data": data, 
                                "message": f"Ticket #{confirmation_data['ticket_id']} updated successfully!"
                            }
                        else:
                            error_msg = f"Ticket update failed: {error}"
                            logging.error(error_msg)
                            action_result = {
                                "success": False, 
                                "error": error_msg, 
                                "message": f"Failed to update ticket: {error}"
                            }
                    else:
                        error_msg = f"Unknown action '{action_to_confirm}' was pending confirmation."
                        logging.error(error_msg)
                        action_result = {"success": False, "error": error_msg}
                        bot_response = f"Sorry, I don't know how to perform the '{action_to_confirm}' action."
                        current_state = {'state_version': 1, 'entities': current_entities, 'status': 'IDLE'}
                        return {
                            "response": bot_response,
                            "final_state": current_state,
                            "token_refreshed": token_refreshed_in_this_call,
                            "new_token_info": new_token_info_after_call,
                            "stream": False,
                            "action_result": action_result,
                            "user_message": user_message,
                            "history": history
                        }

                    # --- Generate Response AFTER execution ---
                    response_prompt = prompts.get_response_generation_prompt(action_result, user_message, history)
                    bot_response = call_gemini(response_prompt)

                except Exception as e:
                    logging.exception(f"Error executing confirmed action {action_to_confirm}:")
                    error_details = str(e)
                    bot_response = f"Sorry, an error occurred while trying to {action_to_confirm.lower().replace('_', ' ')}: {error_details[:100]}"
                    action_result = {"success": False, "error": str(e)}

                # Always reset state after execution (success or failure)
                current_state = {
                    'state_version': 1,
                    'entities': current_entities,  # Keep entities but reset state
                    'status': 'IDLE'
                }

        elif intent == "CANCEL_ACTION":
            logging.info(f"User cancelled action: {action_to_confirm}")
            bot_response = f"Okay, cancelled the {action_to_confirm.lower().replace('_', ' ')} request."
            action_result = {"success": True, "message": f"Action {action_to_confirm} cancelled."}
            current_state = {
                'state_version': 1,
                'entities': current_entities,  # Keep entities but reset state
                'status': 'IDLE'
            }

    elif current_status == "AWAITING_CLARIFICATION" and intent == "PROVIDE_INFO":
        # (Rest of the code for PROVIDE_INFO handling remains the same)
        # This section is unchanged from the original and continues with the existing code.
        action_we_were_in = current_state.get("current_action")
        missing_before = current_state.get("missing_entities", [])
        provided_keys = list(entities.keys()) # Use list to handle potential multiple provisions

        logging.debug(f"Handling PROVIDE_INFO for action '{action_we_were_in}'. Needed: {missing_before}. Provided: {provided_keys}")

        if missing_before and provided_keys:
             # Update entities in state with ALL provided info FIRST
             for key in provided_keys:
                 if key in entities: # Ensure it was extracted
                      current_entities[key] = entities[key]

             # Check if any needed fields were among those provided
             provided_needed_keys = [k for k in provided_keys if k in missing_before]
             if provided_needed_keys:
                 # --- Determine required fields based on the action we were in ---
                 if action_we_were_in == "CREATE_TICKET":
                     required = ["client_id", "summary", "details"]
                 elif action_we_were_in == "ADD_NOTE":
                     required = ["ticket_id", "note_text", "time_taken", "is_private"] # Include new required
                 elif action_we_were_in == "UPDATE_TICKET":
                     required = ["ticket_id", "update_field", "update_value"]
                 else:
                     required = missing_before # Fallback

                 # Recalculate what's still missing *after* update
                 still_missing = [r for r in required if r not in current_entities or current_entities[r] is None]
                 current_state["missing_entities"] = still_missing
                 logging.info(f"Updated entities for {action_we_were_in}. Still missing: {still_missing}")

                 # If nothing is missing anymore, proceed
                 if not still_missing:
                     intent = action_we_were_in # Set intent to re-run the original action's logic
                     current_state["status"] = "GATHERING_INFO" # Signal to proceed
                     logging.info(f"All info gathered for {action_we_were_in}. Proceeding.")
                     # Fall through to main intent processing block below
                 else:
                     # Still missing some fields, ask for the next one
                     next_missing_field = still_missing[0]
                     question_map = { # Specific questions per action
                        "CREATE_TICKET": {
                             "client_id": "Got it. Which client should this ticket be for (Name or ID)?",
                             "summary": "Thanks. What is a brief summary for this ticket?",
                             "details": "Okay. Can you provide the full details or description?"
                         },
                         "ADD_NOTE": {
                             "ticket_id": "Which ticket ID should I add the note to?", # Should rarely happen here
                             "note_text": "What should the note say?",
                             "time_taken": "How many minutes did you spend?",
                             "is_private": "Should this note be Private (internal only) or Public (visible to user)? (Yes/No for Private)"
                          },
                         "UPDATE_TICKET": {
                             "ticket_id": "Which ticket ID do you want to update?", # Should rarely happen here
                             "update_field": "What field do you want to update? (e.g., status, priority, summary...)",
                             "update_value": f"What should the new value be for the '{current_entities.get('update_field', 'selected field')}' field?"
                         }
                     }
                     # Get the appropriate question
                     question = question_map.get(action_we_were_in, {}).get(next_missing_field, f"Thanks! Now I need the {next_missing_field.replace('_', ' ')}.")

                     action_result = {"success": False, "clarification_needed": True, "clarification_question": question}
                     current_state["status"] = "AWAITING_CLARIFICATION" # Stay in clarification
                     # No fall through, wait for next user input for this specific question

             else: # User provided info, but not what was specifically needed now
                 logging.warning(f"User provided {provided_keys}, but expected one of {missing_before}. Asking again for needed info.")
                 original_missing_field = missing_before[0] if missing_before else 'the required information'
                 action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Thanks, but for the {action_we_were_in or 'current task'}, I specifically need the {original_missing_field.replace('_', ' ')}."}
                 current_state["status"] = "AWAITING_CLARIFICATION" # Stay here
        else: # User sent message in AWAITING_CLARIFICATION but NLU didn't find useful entities
              logging.warning(f"PROVIDE_INFO intent for {action_we_were_in}, but no useful entities extracted for {missing_before}. Requesting again.")
              original_missing_field = missing_before[0] if missing_before else 'required information'
              action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Sorry, I didn't quite get that. For the {action_we_were_in or 'current task'}, can you please provide the {original_missing_field.replace('_', ' ')}?"}
              current_state["status"] = "AWAITING_CLARIFICATION"


    # --- Process Main Intents (or Resumed Intents after PROVIDE_INFO) ---
    # Check if intent processing is needed (not confirmation, start over, or if PROVIDE_INFO didn't finish gathering)
    if intent not in ["CONFIRM_ACTION", "CANCEL_ACTION", "START_OVER"] and current_state.get("status") != "AWAITING_CLARIFICATION":

        # --- Context Switch Check ---
        is_context_switch = (
            intent not in ["PROVIDE_INFO", "GREETING", "GOODBYE", "GENERAL_QUERY", "AMBIGUOUS"] and
            current_action and current_action != intent
        )
        if is_context_switch:
            logging.info(f"Context switch detected: From '{current_action}' to '{intent}'. Resetting action state, keeping entities.")
            current_entities_before_reset = current_entities.copy() # Keep current entities
            current_state = { # Reset state fields related to the *action*
                 "entities": current_entities_before_reset, # Restore potentially modified entities
                 "state_version": current_state.get("state_version", 1),
                 "status": 'IDLE'
            }
            # Optionally re-load persistent entities if needed, although current should be up-to-date
            # persistent = get_persistent_entities(logged_in_user)
            # if persistent: current_state['entities'].update(persistent) # Merge persistent

        # Update current action if starting a new actionable intent
        is_actionable_intent = intent not in ["GREETING", "GOODBYE", "GENERAL_QUERY", "AMBIGUOUS", "PROVIDE_INFO"]
        if is_actionable_intent and (not current_state.get("current_action") or is_context_switch):
            current_state["current_action"] = intent
            current_state["status"] = "GATHERING_INFO"
            current_state["missing_entities"] = [] # Reset missing list for new action
            # Entities are already up-to-date


        # ===================================
        # --- Business Logic Execution ---
        # ===================================
        try:
            # This section keeps most of the existing business logic except with special attention
            # to CREATE_TICKET and how we handle client_id
            if intent == "CREATE_TICKET":
                # Identify required fields and check what's missing
                required = ["client_id", "summary", "details"]
                missing = [r for r in required if r not in current_entities or current_entities[r] is None]
                logging.debug(f"CREATE_TICKET: Entities: {current_entities}, Required missing: {missing}")

                # Client Resolution (only if needed)
                if "client_id" in missing and "client_name" in current_entities and current_entities["client_name"]:
                    client_name_lookup = current_entities['client_name']
                    logging.info(f"Attempting to resolve client name: '{client_name_lookup}' for create ticket.")
                    s_lookup, clients, e_lookup = halo_api.search_clients(client_name_lookup, limit=5)
                    if s_lookup and clients:
                        exact = [c for c in clients if c.get('name','').lower() == client_name_lookup.lower()]
                        resolved_id = None
                        resolved_name = None
                        if exact:
                             resolved_id = exact[0]['id']; resolved_name = exact[0]['name']
                        elif len(clients) == 1:
                             resolved_id = clients[0]['id']; resolved_name = clients[0]['name']

                        if resolved_id:
                            # IMPORTANT FIX: Store client_id as integer
                            try:
                                current_entities['client_id'] = int(resolved_id)
                                current_entities['client_name'] = resolved_name # Store resolved name
                                missing = [r for r in required if r not in current_entities or current_entities[r] is None] # Recalculate
                                logging.info(f"Resolved client ID {resolved_id} for '{resolved_name}'. Updated missing: {missing}")
                            except (ValueError, TypeError):
                                logging.error(f"Failed to convert resolved client_id {resolved_id} to integer")
                                action_result = {"success": False, "error": f"Invalid client ID format: {resolved_id}"}
                                missing = ["client_id"]
                        else: # Multiple matches
                             client_options = ", ".join([f"'{c.get('name')}' (ID: {c.get('id')})" for c in clients])
                             action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Multiple clients found like '{client_name_lookup}': {client_options}. Please provide the exact Client ID."}
                             missing = ["client_id"] # Explicitly mark as missing
                    elif e_lookup:
                        action_result = {"success": False, "error": f"Client lookup error: {e_lookup}"}
                        missing = ["client_id"]
                    else: # No clients found
                        action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Sorry, I couldn't find any client named '{client_name_lookup}'. Can you provide the Client ID?"}
                        missing = ["client_id"]

                # Proceed if all core required fields are now present
                if not missing:
                    # Refinement Step
                    current_state["status"] = "REFINING_CONTENT"
                    logging.info("Gathering core info for ticket. Refining content...")
                    user_summary = current_entities.get("summary", "")
                    user_details = current_entities.get("details", "")
                    client_name_for_prompt = current_entities.get("client_name", "Unknown Client")
                    refine_prompt = prompts.get_refinement_prompt(user_summary, user_details, client_name_for_prompt, context="Creating new ticket")
                    refined_raw = call_gemini(refine_prompt)
                    refined_data = parse_refined_content(refined_raw)

                    # Prepare API args (only core fields)
                    api_args = {}
                    validation_ok = True
                    try: # Validate client_id is int before adding
                         # IMPORTANT FIX: Make sure client_id is an integer, not a string
                         if isinstance(current_entities["client_id"], str):
                             api_args["client_id"] = int(current_entities["client_id"])
                         else:
                             api_args["client_id"] = current_entities["client_id"]
                             
                         # Log the client_id explicitly 
                         logging.info(f"Using client_id: {api_args['client_id']} (type: {type(api_args['client_id']).__name__})")
                             
                         api_args["summary"] = refined_data.get("summary") or user_summary
                         api_args["details"] = refined_data.get("details") or user_details
                    except (ValueError, TypeError, KeyError) as e:
                        logging.error(f"Invalid client_id found before confirmation: {current_entities.get('client_id')}. Error: {str(e)}")
                        action_result = {"success": False, "clarification_needed": True, "clarification_question": "There was an issue with the Client ID. Please provide a valid numeric ID."}
                        missing = ["client_id"]
                        validation_ok = False

                    # Await Confirmation if validation passed
                    if validation_ok:
                        current_state["status"] = "AWAITING_CONFIRMATION"
                        current_state["action_to_confirm"] = "CREATE_TICKET"
                        current_state["confirmation_data"] = api_args
                        current_state.pop("missing_entities", None)
                        summary_p, details_p, client_id_p = api_args['summary'], api_args['details'], api_args['client_id']
                        preview = details_p[:150] + ('...' if len(details_p) > 150 else '')
                        client_display_name = current_entities.get('client_name', f"ID {client_id_p}") # Use resolved name if available
                        question = f"OK, draft ticket for **{client_display_name}**:\n\n**Summary:** {summary_p}\n**Details:** {preview}\n\nCreate this ticket? (Yes/No)"
                        action_result = {"success": False, "clarification_needed": True, "clarification_question": question}

                # Ask for missing fields if needed (and no other clarification question was set)
                elif not action_result.get("clarification_question"):
                    missing_field = missing[0]
                    question_map = {
                        "client_id": "Which client should this ticket be for (Name or ID)?",
                        "summary": "What is a brief summary for this ticket?",
                        "details": "Can you provide the full details or description for the ticket?"
                    }
                    question = question_map.get(missing_field, f"I need the {missing_field.replace('_', ' ')} please.")
                    action_result = {"success": False, "clarification_needed": True, "clarification_question": question}

                # Persist state if clarification is needed
                if action_result.get("clarification_needed"):
                    current_state["status"] = "AWAITING_CLARIFICATION"
                    current_state["missing_entities"] = missing # Ensure reflects current need
                    current_state["current_action"] = "CREATE_TICKET"

            # The other intent handlers remain unchanged
            elif intent == "CHECK_TICKET_STATUS":
                # Logic for CHECK_TICKET_STATUS (unchanged from original code)
                required=["ticket_id"]
                missing=[r for r in required if r not in current_entities or current_entities[r] is None]
                if not missing:
                    try:
                        ticket_id_val = int(current_entities["ticket_id"])
                        success, data, error = halo_api.get_ticket(ticket_id_val)
                        action_result = {"success": success, "data": data, "error": error}
                        if success: action_result["message"] = f"Details retrieved for ticket #{ticket_id_val}."
                        else: action_result["message"] = f"Failed to get details for ticket #{ticket_id_val}."
                        if success: current_state = {} # Reset state only on success
                    except (ValueError, TypeError):
                        action_result = {"success": False, "clarification_needed": True, "clarification_question": f"'{current_entities.get('ticket_id')}' doesn't seem like a valid ticket ID number. Please provide the correct ID."}
                        missing=["ticket_id"]
                if missing:
                    action_result = {"success": False, "clarification_needed": True, "clarification_question": "Okay, which ticket ID would you like to check?"}
                    current_state["status"]="AWAITING_CLARIFICATION"
                    current_state["missing_entities"]=missing
                    current_state["current_action"]=intent

            elif intent == "ADD_NOTE":
                # Logic for ADD_NOTE (unchanged from original code)
                required=["ticket_id", "note_text", "time_taken", "is_private"]
                optional=[]

                # Ensure missing reflects current need accurately after potential type conversions
                local_missing = []
                # Ticket ID check
                if "ticket_id" not in current_entities or current_entities["ticket_id"] is None:
                     local_missing.append("ticket_id")
                else: # Validate if present
                     try: int(current_entities["ticket_id"])
                     except (ValueError, TypeError): local_missing.append("ticket_id")
                # Note Text check
                if "note_text" not in current_entities or not current_entities["note_text"]:
                     local_missing.append("note_text")
                 # Time Taken Check & Conversion
                if "time_taken" not in current_entities or current_entities["time_taken"] is None:
                    local_missing.append("time_taken")
                elif not isinstance(current_entities['time_taken'], int):
                     try: current_entities['time_taken'] = int(current_entities['time_taken'])
                     except (ValueError, TypeError): local_missing.append("time_taken")
                 # Is Private Check & Conversion
                if "is_private" not in current_entities or current_entities["is_private"] is None:
                    local_missing.append("is_private")
                elif isinstance(current_entities['is_private'], str):
                     priv_val = current_entities['is_private'].lower()
                     if priv_val in ['true', 'yes', 'private']: current_entities['is_private'] = True
                     elif priv_val in ['false', 'no', 'public']: current_entities['is_private'] = False
                     else: local_missing.append("is_private") # Invalid string, mark as missing
                elif not isinstance(current_entities['is_private'], bool):
                     local_missing.append("is_private") # Invalid type

                missing = local_missing # Update the main missing list
                logging.debug(f"ADD_NOTE: Entities: {current_entities}, Required missing: {missing}")

                if not missing:
                    # All required fields present, proceed to Confirmation
                    try:
                        api_args = {
                            "ticket_id": int(current_entities["ticket_id"]), # Already validated if not missing
                            "note_text": current_entities["note_text"],
                            "time_taken": current_entities["time_taken"],
                            "is_private": current_entities["is_private"]
                        }
                        # Await Confirmation
                        current_state["status"] = "AWAITING_CONFIRMATION"
                        current_state["action_to_confirm"] = "ADD_NOTE"
                        current_state["confirmation_data"] = api_args
                        current_state.pop("missing_entities", None)
                        preview = api_args['note_text'][:150] + ('...' if len(api_args['note_text']) > 150 else '')
                        privacy = "Private" if api_args['is_private'] else "Public"
                        time_str = f" ({api_args['time_taken']} mins)"
                        question = f"Add this {privacy} note to ticket #{api_args['ticket_id']}{time_str}?\n\n'{preview}'\n\nProceed? (Yes/No)"
                        action_result = {"success": False, "clarification_needed": True, "clarification_question": question}
                    except Exception as e: # Catch any unexpected error during prep
                         logging.error(f"Unexpected error preparing ADD_NOTE confirmation: {e}")
                         action_result = {"success": False, "error": "Error preparing the note.", "clarification_needed": False}
                         current_state = {
                             'state_version': 1,
                             'entities': current_entities,
                             'status': 'IDLE'
                         }

                else: # Ask for the first missing required field
                   field = missing[0]
                   question_map = {
                        "ticket_id": "Which ticket ID should I add the note to?",
                        "note_text": "What should the note say?",
                        "time_taken": "How many minutes were spent on this task?",
                        "is_private": "Should this note be Private (internal only) or Public (visible to user)? (Enter 'Yes' for Private, 'No' for Public)"
                    }
                   prompt = question_map.get(field, f"I also need the {field.replace('_', ' ')}.")
                   action_result = {"success": False, "clarification_needed": True, "clarification_question": prompt}
                   current_state["status"] = "AWAITING_CLARIFICATION"
                   current_state["missing_entities"] = missing
                   current_state["current_action"] = "ADD_NOTE"

            elif intent == "LIST_CLIENT_TICKETS":
                # Logic for LIST_CLIENT_TICKETS (unchanged from original code)
                # Remaining intent handlers follow...
                required = ["client_id"]
                missing = []
                target_filter = None
                is_personal_tickets = 'user_name' in current_entities and current_entities.get('user_name') == 'logged_in_user'
                is_all_clients = 'client_name' in current_entities and current_entities.get('client_name', '').lower() == 'all'

                if is_personal_tickets:
                    logging.info("Processing 'my tickets' request.")
                    target_filter = {'type': 'agent', 'value': 'current'}
                elif is_all_clients:
                    logging.info("Processing 'all tickets' request.")
                    target_filter = {'type': 'all'}
                else: # Specific client logic
                     # Client Name Resolution Logic (same as in CREATE_TICKET)
                     if "client_id" not in current_entities and "client_name" in current_entities and current_entities["client_name"]:
                         client_name_lookup = current_entities['client_name']
                         logging.info(f"Attempting to resolve client name: '{client_name_lookup}' for list tickets.")
                         s_lookup, clients, e_lookup = halo_api.search_clients(client_name_lookup, limit=5)
                         if s_lookup and clients:
                             exact = [c for c in clients if c.get('name','').lower() == client_name_lookup.lower()]
                             resolved_id = None; resolved_name = None
                             if exact:
                                  resolved_id = exact[0]['id']; resolved_name = exact[0]['name']
                             elif len(clients) == 1:
                                  resolved_id = clients[0]['id']; resolved_name = clients[0]['name']

                             if resolved_id:
                                  current_entities['client_id'] = resolved_id
                                  current_entities['client_name'] = resolved_name # Store resolved name
                                  target_filter = {'type': 'client', 'value': resolved_id, 'name': resolved_name}
                                  logging.info(f"Resolved client ID {resolved_id} for '{resolved_name}'.")
                             else: # Multiple matches
                                  client_options = ", ".join([f"'{c.get('name')}' (ID: {c.get('id')})" for c in clients])
                                  action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Multiple clients found like '{client_name_lookup}': {client_options}. Please provide the exact Client ID."}
                                  missing = ["client_id"]
                         elif e_lookup:
                              action_result = {"success": False, "error": f"Client lookup error: {e_lookup}"}
                              missing = ["client_id"]
                         else: # No clients found
                              action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Sorry, I couldn't find client '{client_name_lookup}'. Provide ID?"}
                              missing = ["client_id"]
                     elif "client_id" in current_entities and current_entities["client_id"] is not None:
                          try:
                                cid = int(current_entities["client_id"])
                                target_filter = {'type': 'client', 'value': cid, 'name': current_entities.get("client_name")}
                          except (ValueError, TypeError):
                                action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Invalid Client ID: '{current_entities['client_id']}'. Need number."}
                                missing=["client_id"]
                     else: missing = ["client_id"] # No client info

                if target_filter and not action_result.get("clarification_needed"):
                    try:
                        api_params = { "open_only": current_entities.get("open_only", True), "limit": 20 }
                        response_message = ""
                        if target_filter['type'] == 'agent':
                            success_agent, agent_info, error_agent = halo_api.get_current_agent_info()
                            if success_agent and agent_info and 'id' in agent_info:
                                api_params["agent_id"] = agent_info['id']; response_message = "Here are your open tickets:"
                            else: action_result = {"success": False, "error": f"Could not get your agent info: {error_agent}"}
                        elif target_filter['type'] == 'client':
                            api_params["client_id"] = target_filter['value']
                            client_display = target_filter.get('name') or f"Client ID {target_filter['value']}"
                            response_message = f"Here are the open tickets for {client_display}:"
                        elif target_filter['type'] == 'all': response_message = "Here are all open tickets:"

                        if not action_result.get("error"): # Proceed if no prior errors
                             success, data, error = halo_api.get_tickets(**api_params)
                             if success and data:
                                 ticket_list = [{"id": t.get("id"), "summary": t.get("summary")} for t in data]
                                 action_result = {"success": True, "data": ticket_list, "message": response_message}
                                 if len(data) >= api_params["limit"]: action_result["message"] += f" (Showing first {api_params['limit']})"
                             elif success: action_result = {"success": True, "data": [], "message": f"No {'open ' if api_params['open_only'] else ''}tickets found."}
                             else: action_result = {"success": False, "error": error, "message": "Failed to retrieve tickets."}

                        if action_result.get("success"): current_state = {
                            'state_version': 1,
                            'entities': current_entities,
                            'status': 'IDLE'
                        }
                    except Exception as e:
                        logging.exception(f"Error retrieving tickets: {e}")
                        action_result = {"success": False, "error": "Unexpected error getting tickets."}
                        current_state = {
                            'state_version': 1,
                            'entities': current_entities,
                            'status': 'IDLE'
                        }
                elif missing:
                    if not action_result.get("clarification_question"):
                        action_result = {"success": False, "clarification_needed": True, "clarification_question": "Which tickets? 'my tickets', 'all tickets', or for a client (Name/ID)?"}
                    current_state["status"] = "AWAITING_CLARIFICATION"
                    current_state["missing_entities"] = missing
                    current_state["current_action"] = intent

            # Remaining intent handlers (SEARCH_CLIENTS, UPDATE_TICKET, GREETING, GOODBYE, etc.)
            # would follow here but are mostly unchanged from the original code
            
            elif intent == "SEARCH_CLIENTS":
                # Logic for SEARCH_CLIENTS (unchanged from original code)
                required = ["search_term"]
                missing = [r for r in required if r not in current_entities or not current_entities[r]]
                if not missing:
                      search = current_entities["search_term"]
                      success, data, error = halo_api.search_clients(search_term=search, limit=10)
                      if success and data:
                          client_list = [{"id": c.get("id"), "name": c.get("name")} for c in data]
                          action_result = {"success": True, "data": client_list, "message": f"Found clients matching '{search}':"}
                      elif success: action_result = {"success": True, "data": [], "message": f"No clients found matching '{search}'."}
                      else: action_result = {"success": False, "error": error, "message": "Failed to search clients."}
                      current_state = {
                            'state_version': 1,
                            'entities': current_entities,
                            'status': 'IDLE'
                      }
                else:
                      action_result = {"success": False, "clarification_needed": True, "clarification_question": "Which client name would you like to search for?"}
                      current_state["status"]="AWAITING_CLARIFICATION"; current_state["missing_entities"]=missing; current_state["current_action"]=intent


            elif intent == "UPDATE_TICKET":
                # Logic for UPDATE_TICKET (unchanged from original code)
                 required = ["ticket_id", "update_field", "update_value"]
                 missing = [r for r in required if r not in current_entities or current_entities[r] is None]

                 if "ticket_id" in missing:
                      action_result = {"success": False, "clarification_needed": True, "clarification_question": "Which ticket ID do you want to update?"}
                 elif "update_field" in missing:
                     try: # Verify ticket exists before asking for field
                         tid_val = int(current_entities["ticket_id"])
                         success_check, _, error_check = halo_api.get_ticket(tid_val, include_details=False)
                         if success_check: action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Okay, what field on ticket #{tid_val} do you want to update? (e.g., status, priority, summary, details, category, agent)"}
                         else: action_result = {"success": False, "error": f"Ticket #{tid_val} not found.", "message": f"Ticket #{tid_val} not found."}; current_state = {
                            'state_version': 1,
                            'entities': current_entities,
                            'status': 'IDLE'
                         }
                     except (ValueError, TypeError):
                          action_result = {"success": False, "clarification_needed": True, "clarification_question": f"'{current_entities.get('ticket_id')}' isn't a valid ticket ID. Please provide the ID number."}
                          current_state['missing_entities'] = ["ticket_id"] # Re-ask for ID
                 elif "update_value" in missing:
                     action_result = {"success": False, "clarification_needed": True, "clarification_question": f"What should the new value be for the '{current_entities.get('update_field')}' field?"}
                 else: # All required fields seem present
                     try:
                         ticket_id_val = int(current_entities["ticket_id"])
                         field = str(current_entities["update_field"])
                         value = current_entities["update_value"]

                         valid_fields = ["status_id", "priority_id", "summary", "details", "category_1", "agent_id", "team", "site_id", "tickettype_id"]
                         field_mapping = {"status": "status_id", "priority": "priority_id", "category": "category_1", "agent": "agent_id", "description": "details", "assigned agent": "agent_id", "assignee": "agent_id", "site": "site_id", "ticket type": "tickettype_id"}
                         api_field = field_mapping.get(field.lower(), field) # Map or use original

                         if api_field not in valid_fields:
                             action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Sorry, '{field}' isn't a field I can update. Try: status, priority, summary, details, category, agent, site, or ticket type."}
                             current_state['missing_entities'] = ["update_field"] # Re-ask
                         else:
                             # --- Refinement Step for Summary/Details ---
                             api_value = value # Start with user value
                             if api_field in ['summary', 'details']:
                                 logging.info(f"Refining update for '{api_field}' on T{ticket_id_val}")
                                 fetch_ok, ticket_data, fetch_err = halo_api.get_ticket(ticket_id_val)
                                 if fetch_ok and ticket_data:
                                     current_summary = ticket_data.get('summary', '')
                                     current_details = ticket_data.get('details', '')
                                     client_name_refine = ticket_data.get("client_name", "Unknown")
                                     # Decide which field is being updated to pass correctly
                                     prompt_summary = value if api_field == 'summary' else current_summary
                                     prompt_details = value if api_field == 'details' else current_details

                                     refine_prompt = prompts.get_refinement_prompt(prompt_summary, prompt_details, client_name_refine, context=f"Updating field '{api_field}'")
                                     refined_raw = call_gemini(refine_prompt)
                                     refined_data = parse_refined_content(refined_raw)
                                     refined_field_value = refined_data.get(api_field) # Get summary or details
                                     if refined_field_value:
                                         api_value = refined_field_value # Use refined value
                                         logging.info(f"Using refined value for {api_field}: {api_value[:100]}...")
                                     else: logging.warning(f"Refinement failed for {api_field}, using original.")
                                 else: logging.warning(f"Could not fetch T{ticket_id_val} for refine context: {fetch_err}. Using original value.")

                             # --- Value Type Handling (Example for IDs) ---
                             if api_field in ["status_id", "priority_id", "agent_id", "site_id", "tickettype_id"]:
                                 try: api_value = int(api_value) # Use potentially refined value
                                 except (ValueError, TypeError):
                                      if api_field == "agent_id" and isinstance(api_value, str) and api_value.lower() in ['me', 'myself']:
                                          s_agent, agent_info, _ = halo_api.get_current_agent_info()
                                          if s_agent and agent_info: api_value = agent_info['id']
                                          else: raise ValueError("Could not resolve 'me' to current agent ID")
                                      else: raise ValueError(f"Value '{api_value}' is not valid for ID field '{api_field}'")

                             # --- Prepare Payload and Confirmation ---
                             update_payload = {api_field: api_value}
                             current_state["status"] = "AWAITING_CONFIRMATION"
                             current_state["action_to_confirm"] = "UPDATE_TICKET"
                             current_state["confirmation_data"] = {"ticket_id": ticket_id_val, "update_payload": update_payload}
                             current_state.pop("missing_entities", None)
                             question = f"Confirm update for ticket #{ticket_id_val}:\nSet **{api_field}** to:\n'{str(api_value)[:100]}...'?\n\n(Yes/No)"
                             action_result = {"success": False, "clarification_needed": True, "clarification_question": question}

                     except ValueError as ve: # Catch validation/conversion errors
                          action_result = {"success": False, "clarification_needed": True, "clarification_question": f"Validation error: {ve}. Please provide a valid value."}
                          missing = ["update_value"] # Assume value error
                          if "ID" in str(ve) and "ticket" in str(ve): missing = ["ticket_id"]
                          current_state['missing_entities'] = missing; current_state['status'] = "AWAITING_CLARIFICATION"
                     except Exception as e:
                          logging.exception(f"Error preparing update for T{current_entities.get('ticket_id')}:")
                          action_result = {"success": False, "error": f"Error preparing update: {e}"}
                          current_state = {
                            'state_version': 1,
                            'entities': current_entities,
                            'status': 'IDLE'
                         }

                 if action_result.get("clarification_needed"): # Ensure state is correct if clarification needed
                      current_state["status"]="AWAITING_CLARIFICATION"
                      if 'missing_entities' not in current_state or not current_state['missing_entities']:
                          current_state['missing_entities'] = missing if missing else ['unknown'] # Ensure missing list is accurate
                      current_state["current_action"]=intent

            # --- Basic Handlers ---
            elif intent == "GREETING":
                action_result = {"success": True, "message": "Hello! How can I help you with Halo today?"}
                # Don't reset state, allow user to continue task
            elif intent == "GOODBYE":
                action_result = {"success": True, "message": "Goodbye! Have a great day."}
                current_state = {
                    'state_version': 1,
                    'entities': current_entities,
                    'status': 'IDLE'
                }
            elif intent == "GENERAL_QUERY" or intent == "AMBIGUOUS":
                 if current_state.get('status') in ["AWAITING_CLARIFICATION", "AWAITING_CONFIRMATION"]:
                      action_desc = current_state.get('current_action', 'current task').replace('_', ' ')
                      action_result = {"clarification_needed": True, "clarification_question": f"Sorry, I didn't understand that regarding the {action_desc}. Did you want to continue, or cancel and start something new?"}
                      # Keep state, prompt user for CONFIRM/CANCEL/START_OVER
                 else:
                      if not action_result.get("clarification_question"): # Check if set by PROVIDE_INFO error handling
                           action_result = {"clarification_needed": True, "clarification_question": "Sorry, I'm not sure how to help with that. Can you please rephrase, or tell me what Halo task you'd like (e.g., 'create ticket', 'add note', 'list my tickets')?"}
                      if current_state.get("status") not in ["AWAITING_CLARIFICATION", "AWAITING_CONFIRMATION"]:
                           current_state = {
                                'state_version': 1,
                                'entities': current_entities,
                                'status': 'IDLE'
                           }


        except Exception as e:
    # Enhanced general exception handler
            logging.exception(f"CRITICAL ERROR during message processing: {str(e)}")
            # Log key context variables to help diagnose the issue
            if 'current_entities' in locals():
                logging.error(f"current_entities at time of error: {json.dumps(current_entities, default=str)}")
            if 'confirmation_data' in locals():
                logging.error(f"confirmation_data at time of error: {json.dumps(confirmation_data, default=str)}")
            if 'action_to_confirm' in locals():
                logging.error(f"action_to_confirm at time of error: {action_to_confirm}")
            
            action_result = {"success": False, "error": f"An unexpected error occurred: {str(e)}"}
            bot_response = "I'm sorry, but I encountered an unexpected error while processing your request. The technical team has been notified."
            
            # Try to save a minimal valid state
            current_state = {
                'state_version': 1,
                'entities': current_entities if 'current_entities' in locals() else {},
                'status': 'IDLE'
            }

        # --- Generate Bot Response (unless already set by specific handlers like NLU fail) ---
        if not bot_response or bot_response == "Sorry, something went wrong.":
            if action_result.get("clarification_needed") and action_result.get("clarification_question"):
                 bot_response = action_result["clarification_question"]
                 logging.info(f"Using formulated clarification: {bot_response}")
            else:
                # Decide if streaming is needed (only for successful data responses)
                should_stream = (
                    needs_streaming and action_result.get("success") and
                    isinstance(action_result.get("data"), (list, dict)) and
                    action_result.get("data") # Ensure data is not empty
                )

                if not should_stream:
                    response_prompt = prompts.get_response_generation_prompt(action_result, user_message, history)
                    bot_response = call_gemini(response_prompt)
                else:
                    bot_response = "__STREAM__" # Set marker for streaming route
                    logging.info("Streaming marker set for response generation.")


    # --- Final Token Check & State Prep ---
    if halo_api.token_was_refreshed(): # Check the flag set by _request
        token_refreshed_in_this_call = True
        new_token_info_after_call = halo_api.get_current_token_info()
        logging.info("Token was refreshed during API request execution in this call.")

    final_state_to_save = current_state

    # Save persistent entities if state has potentially changed relevant keys
    if final_state_to_save.get("entities"):
         save_persistent_entities(logged_in_user, final_state_to_save["entities"])

    # Final check for streaming marker
    stream_needed = bot_response == "__STREAM__"
    if stream_needed:
        # Use a standard placeholder for the initial response displayed to user
        # action_desc = final_state_to_save.get("current_action", "details").replace("_", " ")
        bot_response = "Okay, getting that information for you..." # Generic placeholder

    # Log final state for debugging
    logging.debug(f"FINAL STATE TO SAVE: {json.dumps(final_state_to_save, default=str)}")
    logging.info(f"Bot response snippet: {bot_response[:100]}...")

    return {
        "response": bot_response,
        "final_state": final_state_to_save,
        "token_refreshed": token_refreshed_in_this_call,
        "new_token_info": new_token_info_after_call,
        "stream": stream_needed,
        # Pass back necessary data for streaming generation route if needed
        "action_result": action_result,
        "user_message": user_message,
        "history": history # Pass history back for stream generation context
    }


def generate_streaming_response(
    halo_api: HaloAPI, action_result: Dict[str, Any],
    user_message: str, history: list
) -> Iterator[str]:
    """Generate a streaming response using Gemini."""
    logging.debug("Generating streaming response...")
    # Use the same response prompt but request streaming output
    response_prompt = prompts.get_response_generation_prompt(action_result, user_message, history)
    stream = call_gemini(response_prompt, stream=True) # Get stream iterator

    # Check if the stream object is valid
    if not hasattr(stream, '__iter__') and not hasattr(stream, '__aiter__'):
        logging.error(f"Gemini call did not return a valid stream iterator. Got: {type(stream)}")
        error_msg = "Failed to get streaming response from AI."
        yield f"data: {json.dumps({'error': error_msg})}\n\n"
        yield "data: {\"done\": true}\n\n"
        return # Exit the generator

    # --- Stream Processing ---
    # Initial message to indicate start (optional, client can handle)
    # yield "data: {\"chunk\": \"\"}\n\n"
    buffer = ""
    try:
        for chunk in stream:
            chunk_text = ""
            # Robustly extract text from chunk
            try:
                if hasattr(chunk, 'text') and chunk.text:
                    chunk_text = chunk.text
                elif hasattr(chunk, 'parts') and chunk.parts:
                    chunk_text = "".join(part.text for part in chunk.parts if hasattr(part, 'text'))
            except ValueError as ve:
                 # Handle potential block during streaming
                 logging.warning(f"ValueError accessing chunk part/text during stream: {ve}")
                 if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                     logging.warning(f"Gemini stream chunk blocked: {chunk.prompt_feedback.block_reason}")
                     chunk_text = f"\n\n[My response was interrupted due to safety settings: {chunk.prompt_feedback.block_reason}]"
                 else: chunk_text = "\n\n[Error processing part of the response]"


            if chunk_text:
                 buffer += chunk_text
                 # Yield chunks frequently enough for responsiveness
                 # Optional: yield word by word or sentence by sentence if needed
                 sse_data = json.dumps({"chunk": chunk_text}) # Send only the new chunk
                 yield f"data: {sse_data}\n\n"
            else:
                 logging.debug(f"Skipping empty or non-text stream chunk: {chunk}")

        logging.debug(f"Stream finished. Full buffer length: {len(buffer)}")
        # Signal end of stream after loop finishes
        yield "data: {\"done\": true}\n\n"

    except Exception as e:
        logging.exception("Error occurred during stream iteration:")
        # Yield an error message within the stream
        error_data = json.dumps({"error": f"Error during streaming: {str(e)}"})
        yield f"data: {error_data}\n\n"
        yield "data: {\"done\": true}\n\n" # Signal done even on error


# --- Login Required Decorator ---
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for('login'))
        # Optional: Add check for valid tokens here? Could be redundant with get_halo_api_instance
        # if 'halo_tokens' not in session: ...
        return f(*args, **kwargs)
    return decorated_function


# --- Flask Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template('login.html')
        try:
            auth_url=os.getenv("HALO_AUTH_URL")
            client_id=os.getenv("HALO_CLIENT_ID")
            api_base=os.getenv("HALO_API_BASE")
            tenant=os.getenv("HALO_TENANT")
            if not all([auth_url, client_id, api_base]):
                flash("Server configuration error: Missing Halo API details.", "error")
                logging.error("Missing Halo config environment variables during login.")
                return render_template('login.html'), 500 # Indicate server error

            # Use HaloAPI for initial authentication via password grant
            temp_halo_api = HaloAPI(
                auth_url=auth_url, client_id=client_id, api_base_url=api_base,
                tenant=tenant, username=username, password=password
            )
            token_info = temp_halo_api.get_current_token_info()
            # This call itself raises HaloAuthenticationError on failure now

            # Clear potentially stale session data before setting new info
            session.clear()

            session['logged_in'] = True
            session['username'] = username
            session['halo_tokens'] = token_info
            # Initialize empty chat history and state for the new session
            session['chat_history'] = []
            session['chat_state'] = {
                'state_version': 1,  # Initialize with structured state
                'entities': {},
                'status': 'IDLE'
            }
            session.modified = True  # Explicitly mark session for saving

            flash(f"Welcome back, {username}!", "success") # Personalized welcome
            logging.info(f"User '{username}' logged in successfully.")
            return redirect(url_for('index'))

        except HaloAuthenticationError as auth_err:
            flash(f"Login Failed: Invalid username or password. Please try again.", "error") # Simplified error for user
            logging.warning(f"Login failed for '{username}': {auth_err}")
            return render_template('login.html')
        except requests.exceptions.RequestException as req_err:
             flash("Network error connecting to authentication server. Please check your connection or try again later.", "error")
             logging.error(f"Network error during login for '{username}': {req_err}")
             return render_template('login.html')
        except Exception as e:
            flash(f"An unexpected error occurred during login. Please contact support if this persists.", "error") # More user-friendly general error
            logging.exception(f"Unexpected login error for '{username}':")
            return render_template('login.html')

    # Show login page for GET requests or if POST fails validation/auth
    return render_template('login.html')


@app.route('/logout')
def logout():
    user = session.get('username', 'UNKNOWN')
    session.clear()
    flash("You have been successfully logged out.", "info")
    logging.info(f"User '{user}' logged out.")
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    user = session.get('username', 'User')
    # Ensure chat history is initialized if missing (e.g., after direct login redirect)
    history = session.setdefault('chat_history', [])
    # Also ensure state is properly initialized
    if 'chat_state' not in session or not isinstance(session['chat_state'], dict):
        session['chat_state'] = {
            'state_version': 1,
            'entities': {},
            'status': 'IDLE'
        }
    session.modified = True
    logging.info(f"Serving index page for '{user}'")
    return render_template('index.html', username=user, chat_history=history)


@app.route('/reset_chat', methods=['POST'])
@login_required
def reset_chat():
    user = session.get('username', 'UNKNOWN')
    # Get persisted entities before reset
    persisted_entities = {}
    if 'chat_state' in session and isinstance(session['chat_state'], dict):
        if 'entities' in session['chat_state']:
            # Extract only persistent entity keys
            persistent_keys = ['client_id', 'client_name', 'user_id', 'user_name', 'site_id', 'site_name']
            persisted_entities = {k: v for k, v in session['chat_state']['entities'].items() 
                                if k in persistent_keys and v is not None}
    
    # Reset chat-specific session keys safely
    session.pop('chat_history', None)
    session.pop('chat_state', None)
    session.pop('pending_stream', None) # Clear any pending stream context
    
    # Re-initialize with clean state but preserve persistent entities
    session['chat_history'] = []
    session['chat_state'] = {
        'state_version': 1,
        'entities': persisted_entities,  # Maintain persistent entities
        'status': 'IDLE'
    }
    
    session.modified = True # Ensure changes are saved
    logging.info(f"Chat reset initiated by user '{user}'. Preserved entities: {persisted_entities.keys()}")
    # Return a confirmation message that JS can display
    return jsonify({"status": "ok", "message": "Okay, I've cleared our current conversation context. How can I help you now?"})


@app.route('/chat', methods=['POST'])
@login_required
def chat():
    halo_api = None
    user = session.get('username', 'UNK')
    try:
        halo_api = get_halo_api_instance()
        if not halo_api:
            logging.warning(f"Failed to get valid API instance for '{user}' in /chat route.")
            # Redirect to login only if the core login session is gone
            if 'logged_in' not in session:
                 logging.info(f"User '{user}' redirected to login from /chat (missing session).")
                 return jsonify({"error": "Session expired.", "redirect": url_for('login')}), 401 # Signal JS to redirect
            else:
                 # API init failed, but user session exists. Suggest relogin.
                 logging.error(f"API instance failure for apparently logged-in user '{user}'.")
                 return jsonify({"error": "Connection error. Please log out and log back in."}), 503

        data = request.get_json()
        user_message = data.get('message')
        if not user_message or not isinstance(user_message, str) or not user_message.strip():
            logging.warning(f"Received empty or invalid message from {user}.")
            return jsonify({"error": "No message provided."}), 400

        # Ensure history and state exist in session
        current_history = session.setdefault('chat_history', [])
        
        # Initialize state properly if missing or invalid
        if 'chat_state' not in session or not isinstance(session['chat_state'], dict):
            session['chat_state'] = {
                'state_version': 1,
                'entities': {},
                'status': 'IDLE'
            }
            session.modified = True
        
        current_state = session['chat_state']

        # IMPORTANT: Log the state before processing to diagnose issues
        logging.debug(f"STATE BEFORE PROCESSING: {json.dumps(current_state, default=str)}")

        # --- Message Processing ---
        result = process_chat_message(halo_api, user_message.strip(), current_history, current_state)

        # Update history with the user message *after* processing
        current_history.append({"role": "user", "content": user_message.strip()}) # Store stripped message

        # Update state and tokens *immediately* after processing
        session['chat_state'] = result["final_state"]
        if result.get("token_refreshed") and result.get("new_token_info"):
            logging.info(f"Updating session tokens for '{user}' after chat processing.")
            session['halo_tokens'] = result["new_token_info"]
        
        # IMPORTANT: Always mark the session as modified to ensure state is saved
        session.modified = True

        # Check if response needs streaming
        if result.get("stream", False):
            stream_id = str(uuid.uuid4())
            # Store necessary context for the streaming endpoint
            session['pending_stream'] = {
                "id": stream_id,
                "action_result": result["action_result"],
                "user_message": user_message.strip(), # Store message for context
                "history": current_history.copy() # History *including* user msg
            }
            # Add placeholder bot response to history *before* returning stream signal
            current_history.append({"role": "model", "content": result["response"]}) # Placeholder msg
            session['chat_history'] = current_history[-30:] # Keep more history
            session.modified = True # Ensure all session updates are saved

            # Signal to JS to start streaming
            return jsonify({ "response": result["response"], "streaming": True, "stream_id": stream_id })
        else:
            # Non-streaming: Add the final bot response to history
            current_history.append({"role": "model", "content": result["response"]})
            session['chat_history'] = current_history[-30:] # Keep more history
            session.modified = True # Save history updates

            # Return the complete response
            return jsonify({"response": result["response"]})

    except Exception as e:
        logging.exception(f"Unexpected error in /chat endpoint for user '{user}':")
        # Check if it's an authentication error specifically
        if isinstance(e, HaloAuthenticationError):
             flash("Your session may have expired. Please log in again.", "error")
             return jsonify({"error": "Authentication required.", "redirect": url_for('login')}), 401
        return jsonify({"error": f"Sorry, an internal error occurred while processing your message: {str(e)}"}), 500


@app.route('/stream/<stream_id>', methods=['GET'])
@login_required
def stream_response(stream_id):
    """Handle streaming responses using Server-Sent Events (SSE)."""
    user = session.get('username', 'UNK')
    pending_stream_data = session.get('pending_stream')

    # Validate stream ID and context existence
    if not pending_stream_data or pending_stream_data.get('id') != stream_id:
        logging.warning(f"Invalid or expired stream ID '{stream_id}' request from '{user}'. Pending: {session.get('pending_stream')}")
        # Return an immediate SSE error event
        def invalid_id_stream():
            error_json = json.dumps({"error": "Invalid or expired stream request."})
            yield f"data: {error_json}\n\n"
            yield "data: {\"done\": true}\n\n"
        return Response(invalid_id_stream(), mimetype='text/event-stream')

    # Pop the data to prevent re-use and signal processing start
    stream_context = session.pop('pending_stream')
    session.modified = True
    logging.info(f"Starting stream '{stream_id}' for user '{user}'. Context retrieved.")

    # Get a fresh API instance for the streaming generation
    halo_api = get_halo_api_instance()
    if not halo_api:
        logging.error(f"Failed to get Halo API instance for stream ID {stream_id} (user: {user}).")
        def api_error_stream():
            error_json = json.dumps({"error": "Connection error preventing response generation."})
            yield f"data: {error_json}\n\n"
            yield "data: {\"done\": true}\n\n"
        return Response(api_error_stream(), mimetype='text/event-stream')

    # Create the generator for SSE
    def generate():
        # Pass the retrieved context to the streaming generation function
        return generate_streaming_response(
            halo_api,
            stream_context["action_result"],
            stream_context["user_message"],
            stream_context["history"] # Use history snapshot from before stream started
        )

    # Return the streaming response
    return Response(
        stream_with_context(generate()), # Use stream_with_context for proper handling
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no' # Disable buffering for proxies like Nginx
        }
    )


@app.route('/chat_with_image', methods=['POST'])
@login_required
def chat_with_image():
    """Handle chat messages with image uploads for analysis."""
    user = session.get('username', 'UNK')
    logging.info(f"'/chat_with_image' endpoint called by user '{user}'.")

    # Get Halo API instance (might be needed if follow-up actions occur)
    halo_api = get_halo_api_instance()
    if not halo_api:
        logging.warning(f"API instance failure during image upload for '{user}'.")
        if 'logged_in' not in session: return redirect(url_for('login'))
        else: return jsonify({"error": "Connection error. Please log out and log back in."}), 503

    image_file = request.files.get('image')
    user_message = request.form.get('message', '') # Accompanying text prompt

    if not image_file:
        logging.warning(f"Image upload request from {user} missing image file.")
        return jsonify({"error": "No image file provided."}), 400

    # Set default prompt if user doesn't provide one
    prompt_for_llm = user_message or "Analyze this image in the context of IT support. Describe any visible hardware, error messages, or diagrams."

    # Ensure history exists
    current_history = session.setdefault('chat_history', [])
    # State usually isn't modified by image analysis, but ensure it exists
    current_state = session.setdefault('chat_state', {
        'state_version': 1,
        'entities': {},
        'status': 'IDLE'
    })
    session.modified = True  # Ensure session is saved

    bot_response = "Error analyzing the image." # Default error message
    try:
        image_data = image_file.read()
        image_mime = image_file.content_type
        logging.info(f"Processing image '{image_file.filename}' ({image_mime}) from {user}.")

        # Add reference to history
        user_message_with_image_ref = f"{prompt_for_llm} [Image Uploaded: {image_file.filename}]"
        current_history.append({"role": "user", "content": user_message_with_image_ref})

        # --- Call Gemini Multimodal ---
        analysis_result = call_gemini_with_image(prompt_for_llm, image_data, image_mime)
        bot_response = analysis_result # Use the direct result

        # Add LLM response to history
        current_history.append({"role": "model", "content": bot_response})

        # Save updated history
        session['chat_history'] = current_history[-30:] # Keep history size managed
        session.modified = True

        logging.info(f"Image analysis complete for {user}.")
        return jsonify({"response": bot_response})

    except Exception as e:
        logging.exception(f"Error in /chat_with_image processing for '{user}':")
        return jsonify({"error": f"An internal error occurred while processing the image: {str(e)}"}), 500


# --- Run Application ---
if __name__ == '__main__':
    # Set debug based on environment variable for safer production defaults
    is_debug = os.getenv('FLASK_DEBUG', 'false').lower() in ['true', '1', 't']
    app.run(debug=is_debug, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
# --- END OF IMPROVED app.py ---