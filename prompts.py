# --- START OF IMPROVED prompts.py ---
import json
from typing import Optional, Dict, List, Any

# --- NLU Prompt ---
def get_nlu_prompt(user_message: str, conversation_history: list, current_state: dict = None) -> str:
    """Generates the prompt for NLU (Intent/Entity Extraction)."""
    # (Keep NLU prompt function as previously refined)
    history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history[-6:]])
    state_context = "\nSystem Status:\n"
    if current_state:
        current_action = current_state.get('current_action', 'None'); status = current_state.get('status', 'IDLE'); entities_so_far = current_state.get('entities', {}); missing = current_state.get('missing_entities', [])
        state_context += f"- Current Task: {current_action}\n- Current Status: {status}\n"
        if entities_so_far: state_context += f"- Collected Info: {json.dumps(entities_so_far)}\n"
        if status == 'AWAITING_CONFIRMATION': state_context += f"- Waiting for confirmation (Yes/No) for: {current_state.get('action_to_confirm', 'unknown')}\n"
        elif status == 'AWAITING_CLARIFICATION' and missing: state_context += f"- Waiting for user to provide: {', '.join(missing)}\n"
    else: state_context += "- No active task.\n"
    prompt = f"""Analyze the user's message based on history and status for intent and entities.

Available Intents: CREATE_TICKET, CHECK_TICKET_STATUS, ADD_NOTE, LIST_CLIENT_TICKETS (my/all/client), SEARCH_CLIENTS, UPDATE_TICKET, PROVIDE_INFO, CONFIRM_ACTION, CANCEL_ACTION, START_OVER, GREETING, GOODBYE, AMBIGUOUS, GENERAL_QUERY.

Entities: ticket_id(int), client_name(str/All), client_id(int), user_name(str/logged_in_user), summary(str), details(str), note_text(str), priority(str), status_id(int), time_taken(int, mins), search_term(str), update_field(str), update_value(any), is_private(bool, default True for ADD_NOTE), open_only(bool, default True for LIST), confirmation(yes/no).

History (Last 3 turns):
{history_str}
{state_context}
User Message: "{user_message}"

Instructions:
1. Determine Intent considering History & **Status**.
2. Prioritize CONFIRM/CANCEL/START_OVER if Status indicates waiting.
3. If Status=AWAITING_CLARIFICATION, check if message provides missing info (Intent: PROVIDE_INFO).
4. For "my tickets", Intent=LIST_CLIENT_TICKETS, user_name='logged_in_user'.
5. For "all tickets", Intent=LIST_CLIENT_TICKETS, client_name='All'.
6. For "add note to TICKET", Intent=ADD_NOTE even without text. Extract note text after "saying"/"text"/etc.
7. Extract *all* relevant Entities. Time in minutes. For is_private, look for "public"/"private" (default private=True if adding note).
8. **Output ONLY valid JSON**: {{"intent": "...", "entities": {{...}}}}. Use empty {{}} if no entities.

Examples:
"Show me my tickets" → {{"intent": "LIST_CLIENT_TICKETS", "entities": {{"user_name": "logged_in_user", "open_only": true}}}}
"Show all open tickets" → {{"intent": "LIST_CLIENT_TICKETS", "entities": {{"client_name": "All", "open_only": true}}}}
"Add note to ticket 1234 saying client called, spent 10 mins" → {{"intent": "ADD_NOTE", "entities": {{"ticket_id": 1234, "note_text": "client called", "time_taken": 10}}}}
"Make that note public" (assuming state has ticket_id 1234) → {{"intent": "PROVIDE_INFO", "entities": {{"is_private": false}}}}

JSON Response:
"""
    return prompt


# --- Refinement Prompt ---
# UPDATED Signature and Prompt Text
def get_refinement_prompt(original_summary: str, original_details: str, client_name: Optional[str] = None, context: str = "Creating new ticket") -> str:
    """Generates prompt for AI to refine ticket content."""
    client_context = f" for client '{client_name}'" if client_name else ""
    # --- Include context in the prompt ---
    context_str = f" You are refining this content while **{context}**."

    prompt = f"""
    Act as a professional IT Support Technician. Review the user-provided ticket information{client_context}.
    {context_str}
    Rewrite the Summary and Details to be clear, concise, professional, and suitable for a ticketing system entry.
    - If updating, focus on incorporating the new information cleanly into the existing structure or replacing the relevant part.
    - Ensure the summary accurately reflects the core issue in 10-15 words maximum.
    - Elaborate on details if needed for clarity, maintaining factual accuracy. Correct grammar/spelling.
    - Use standard IT terminology where appropriate but avoid excessive jargon.
    - Structure the details logically (e.g., Problem, Steps Taken (if any), Next Steps/Outcome).
    - Keep important information but remove redundancies. Retain ticket ID if mentioned in context.

    Original Summary Provided for Context/Refinement:
    "{original_summary}"

    Original Details Provided for Context/Refinement:
    "{original_details}"

    Format your response *exactly* like this, with no extra text before or after:
    Refined Summary: [Your rewritten summary here]
    Refined Details: [Your rewritten details here]

    Rewritten Content:
    """
    return prompt

# --- Response Generation Prompt ---
def get_response_generation_prompt(action_result: dict, user_query: str, conversation_history: list) -> str:
    """Generates the prompt for crafting the user-facing response."""
    # (Keep response generation prompt as previously refined - shows all tickets)
    history_str = "\n".join([ f"{msg['role']}: {msg['content']}" for msg in conversation_history[-8:] ])
    data_str = str(action_result.get('data'))
    if isinstance(action_result.get('data'), list): # Format lists nicely
        items_to_show = action_result['data']
        list_limit = 20 # Show a reasonable number directly
        formatted_list = []
        for item in items_to_show[:list_limit]:
            if isinstance(item, dict):
                 id_val = item.get('id'); name = item.get('name'); summary = item.get('summary')
                 if id_val and name: formatted_list.append(f"- '{name}' (ID: {id_val})")
                 elif id_val and summary: formatted_list.append(f"- #{id_val}: '{summary}'")
                 elif id_val: formatted_list.append(f"- ID: {id_val}")
                 elif name: formatted_list.append(f"- Name: '{name}'")
                 else: formatted_list.append(f"- {str(item)}")
            else: formatted_list.append(f"- {str(item)}")
        if len(items_to_show) > list_limit: formatted_list.append(f"- ... (and {len(items_to_show) - list_limit} more)")
        data_str = "\n".join(formatted_list) if formatted_list else "(No results)"

    result_context = f"""
    Internal Action Result:
    - Success: {action_result.get('success')}
    - Message: {action_result.get('message')} # User-facing status/outcome message
    - Error Message: {action_result.get('error')} # Internal error details
    - Data Returned/Formatted: {data_str} # Relevant data (list or details)
    - Clarification Needed: {action_result.get('clarification_needed')} # Not used here, handled before response gen
    - Specific Question to Ask: {action_result.get('clarification_question')} # Not used here
    """
    prompt = f"""
    You are JARVIS, a helpful AI assistant for the HaloPSA system. Your user is an IT technician.
    Generate a concise, helpful, and professional response based on the outcome of their request.

    Conversation History (Last 4 turns):
    {history_str}

    User's Last Message: "{user_query}"

    {result_context}

    Instructions:
    1. **Priority:** If 'Success' is true:
        - Use the 'Message' field provided in the 'Internal Action Result' as the primary confirmation (e.g., "Okay, ticket created:", "Note added successfully.").
        - If 'Data Returned/Formatted' contains information (like a list or details), present it clearly after the 'Message'. Use markdown lists or bolding.
        - If 'Data Returned/Formatted' is empty or "(No results)", simply use the 'Message' (e.g., "No open tickets found.").
        - If the action was creating/updating and data contains an ID, highlight it (e.g., "Ticket **#12345** created successfully.").
    2. If 'Success' is false:
        - Start with a brief apology (e.g., "Sorry, I couldn't do that.").
        - Use the 'Message' field if provided and suitable for the user (e.g., "Ticket not found.").
        - Otherwise, use the 'Error Message' to give a simple reason (e.g., "Client lookup failed."). Avoid technical jargon if possible. If both Message/Error are None, use a generic failure message.
    3. Keep the tone professional but helpful. Be concise.
    4. **CRITICAL:** Respond *only* with the final message for the user. No meta-commentary.
    5. Format lists clearly (e.g., using '-' or numbered lists). Bold key info like IDs.

    JARVIS Response:
    """
    return prompt

# --- Function Calling Prompts (Not actively used in current logic) ---
def get_function_calling_prompt(functions_info: List[Dict[str, Any]], user_query: str) -> str:
    """Generates prompt for function calling based on user request."""
    functions_str = json.dumps(functions_info, indent=2)
    prompt = f"""
    Based on the user's request, determine function & parameters.
    Functions: {functions_str}
    Request: "{user_query}"
    Output JSON: {{"function_name": "...", "parameters": {{...}}}}. Null if no function.
    JSON Response: """
    return prompt
# --- END OF IMPROVED prompts.py ---