"""
enhanced_halo_api.py

An enhanced and robust wrapper for the HaloPSA API that handles common
operations. Implements OAuth 2.0 Password Grant flow, storing only
access/refresh tokens after initial authentication.
"""
import requests
import logging
import time
import json
from typing import Dict, List, Any, Optional, Tuple, Union, overload # Added overload

# --- Logging Setup ---
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

# --- Constants ---
TOKEN_BUFFER_SECONDS = 60
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_RETRY_ATTEMPTS = 1
DEFAULT_PAGE_LIMIT = 100
REQUIRED_SCOPES = "all offline_access"

# --- Custom Exceptions ---
class HaloAPIError(Exception):
    """Custom exception for Halo API errors."""
    def __init__(self, message, status_code=None, response_text=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

class HaloAuthenticationError(HaloAPIError):
    """Custom exception specifically for authentication failures."""
    pass # Simple inheritance is enough for now


class HaloAPI:
    """
    A robust wrapper for the HaloPSA API using OAuth 2.0 Password Grant.
    Handles token management using access and refresh tokens. Can be
    initialized either via username/password or existing tokens.
    """

    # Overload signatures for type hinting based on initialization method
    @overload
    def __init__(
        self, *, # Force keyword arguments for clarity
        auth_url: str,
        client_id: str,
        username: str,
        password: str,
        api_base_url: str,
        tenant: Optional[str] = None
    ): ... # Signature for password auth

    @overload
    def __init__(
        self, *, # Force keyword arguments
        auth_url: str,
        client_id: str,
        api_base_url: str,
        access_token: str,
        refresh_token: str,
        token_expires_at: float,
        tenant: Optional[str] = None,
        username_for_log: Optional[str] = None # Optional username just for logging
    ): ... # Signature for token auth

    # Actual implementation
    def __init__(
        self, *,
        auth_url: str,
        client_id: str,
        api_base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        token_expires_at: Optional[float] = None,
        tenant: Optional[str] = None,
        username_for_log: Optional[str] = None # Only used if initializing with tokens
    ):
        """
        Initialize the HaloPSA API client.

        Use EITHER username/password OR access_token/refresh_token/token_expires_at.
        """
        if not all([auth_url, client_id, api_base_url]):
            raise ValueError("auth_url, client_id, and api_base_url are required.")

        use_password_auth = username is not None and password is not None
        use_token_auth = access_token is not None and refresh_token is not None and token_expires_at is not None

        if use_password_auth and use_token_auth:
            raise ValueError("Cannot initialize HaloAPI with both username/password AND existing tokens.")
        if not use_password_auth and not use_token_auth:
            raise ValueError("Must initialize HaloAPI with either username/password OR existing tokens.")

        self.auth_url = auth_url
        self.client_id = client_id
        self.api_base_url = api_base_url.rstrip('/')
        self.tenant = tenant
        self._username_for_log = username if use_password_auth else username_for_log or "token_init_user"

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._headers: Dict[str, str] = {}
        # --- NEW: Flag for token refresh during requests ---
        self._token_was_refreshed_in_request = False

        logger.info(f"Initializing HaloAPI client for user '{self._username_for_log}' at base URL: {self.api_base_url}")

        if use_password_auth:
            if username is None or password is None:
                 raise ValueError("Username and password are required for password authentication.") # Added check
            self._username = username # Store temporarily for initial auth
            self._password = password
            if not self._authenticate_with_password():
                logger.critical(f"Failed initial password grant for user '{self._username_for_log}'")
                raise HaloAuthenticationError("Initial authentication failed. Check credentials/config.")
            self._password = None # Clear password immediately
            logger.debug("Password cleared from instance after initial authentication.")
        elif use_token_auth:
            if access_token is None or refresh_token is None or token_expires_at is None:
                 raise ValueError("access_token, refresh_token, and token_expires_at are required for token authentication.") # Added check
            logger.info(f"Initializing HaloAPI with existing tokens for user '{self._username_for_log}'.")
            self._access_token = access_token
            self._refresh_token = refresh_token
            try:
                 self._token_expires_at = float(token_expires_at)
            except (ValueError, TypeError):
                 logger.error("Invalid token_expires_at value provided. Clearing tokens.")
                 self._clear_token()
                 raise ValueError("token_expires_at must be a valid float timestamp.")
            self._update_headers()


    def _get_auth_url_with_tenant(self) -> str:
        """Constructs the auth URL, adding tenant query param if needed."""
        if self.tenant:
            clean_tenant = self.tenant.strip('/?')
            separator = '&' if '?' in self.auth_url else '?'
            return f"{self.auth_url}{separator}tenant={clean_tenant}"
        else:
            return self.auth_url

    def _authenticate_with_password(self) -> bool:
        """Performs initial authentication using the Password Grant flow."""
        if not self._username or not self._password:
             logger.error("Internal error: _authenticate_with_password called without username/password.")
             return False

        payload = { "grant_type": "password", "client_id": self.client_id, "username": self._username, "password": self._password, "scope": REQUIRED_SCOPES }
        headers = { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" }
        auth_request_url = self._get_auth_url_with_tenant()
        logger.info(f"Attempting initial password grant authentication to {auth_request_url} for user {self._username}")

        try:
            response = requests.post( auth_request_url, headers=headers, data=payload, timeout=DEFAULT_REQUEST_TIMEOUT )
            response.raise_for_status()
            token_data = response.json()
            self._access_token = token_data.get("access_token")
            self._refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
            if not self._access_token:
                logger.error("Access token not found in password grant response.")
                self._clear_token()
                return False
            if not self._refresh_token:
                 logger.warning("Refresh token not found in password grant response. Scope 'offline_access' might be missing.")
            self._token_expires_at = time.time() + expires_in - TOKEN_BUFFER_SECONDS
            self._update_headers()
            expiry_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._token_expires_at + TOKEN_BUFFER_SECONDS))
            logger.info(f"Successfully authenticated user {self._username}. Token expires around: {expiry_time_str}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error during initial password authentication: {e}")
            if e.response is not None: logger.error(f"Auth Response Status: {e.response.status_code}, Body: {e.response.text}")
            self._clear_token()
            return False
        except Exception as e:
            logger.exception("Unexpected error during password authentication:")
            self._clear_token()
            return False


    def _refresh_with_token(self) -> bool:
        """Refreshes the access token using the stored refresh token."""
        if not self._refresh_token:
            logger.error("Cannot refresh token: No refresh token available.")
            return False

        payload = { "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": self._refresh_token }
        headers = { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" }
        auth_request_url = self._get_auth_url_with_tenant()
        logger.info(f"Attempting token refresh using refresh token for user {self._username_for_log}")

        try:
            response = requests.post( auth_request_url, headers=headers, data=payload, timeout=DEFAULT_REQUEST_TIMEOUT )
            response.raise_for_status()
            token_data = response.json()
            new_access_token = token_data.get("access_token")
            new_refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
            if not new_access_token:
                logger.error("Access token not found in refresh token response.")
                return False
            self._access_token = new_access_token
            if new_refresh_token:
                logger.info("Received a new refresh token during refresh.")
                self._refresh_token = new_refresh_token
            self._token_expires_at = time.time() + expires_in - TOKEN_BUFFER_SECONDS
            self._update_headers()
            expiry_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._token_expires_at + TOKEN_BUFFER_SECONDS))
            logger.info(f"Successfully refreshed access token for user {self._username_for_log}. New token expires around: {expiry_time_str}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error refreshing access token: {e}")
            if e.response is not None:
                logger.error(f"Refresh Response Status: {e.response.status_code}, Body: {e.response.text}")
                if e.response.status_code in [400, 401, 403]:
                    logger.error("Refresh token appears invalid/expired. Clearing all tokens.")
                    self._clear_token()
            else:
                logger.error("Network error during token refresh.")
            return False
        except Exception as e:
            logger.exception("Unexpected error during token refresh:")
            return False


    def _clear_token(self):
        """Clears all token information."""
        self._access_token = None
        self._refresh_token = None
        self._token_expires_at = 0
        self._headers = {}
        logger.warning("Access and refresh token information cleared.")

    def _update_headers(self):
        """Updates the headers dictionary with the current access token."""
        if self._access_token:
            self._headers = { "Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json", "Accept": "application/json" }
        else:
            self._headers = { "Content-Type": "application/json", "Accept": "application/json" }

    def _ensure_token_valid(self) -> bool:
        """Checks token validity and attempts refresh if needed."""
        if self._access_token and time.time() < self._token_expires_at:
            return True
        logger.warning("Access token is missing or expired. Attempting refresh.")
        return self._refresh_with_token()

    def _request( self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, json_data: Optional[Any] = None, attempt_number: int = 1 ) -> Tuple[bool, Any, Optional[str]]:
        """Internal method to make API requests with token handling."""
        self._token_was_refreshed_in_request = False # Reset flag at start of request

        if not self._ensure_token_valid():
            error_msg = "Auth failed: Unable to ensure valid token before request."
            logger.critical(error_msg)
            return False, None, error_msg

        if not endpoint.startswith('/'): endpoint = f'/{endpoint}'
        url = f"{self.api_base_url}{endpoint}"
        logger.debug(f"Attempt {attempt_number}: {method} request to {url}")
        if params: logger.debug(f"Params: {params}")
        if json_data: logger.debug(f"JSON Data: {json.dumps(json_data) if isinstance(json_data, (dict, list)) else str(json_data)[:100]}")

        try:
            response = requests.request( method=method, url=url, headers=self._headers, params=params, json=json_data, timeout=DEFAULT_REQUEST_TIMEOUT )

            if 200 <= response.status_code < 300:
                if response.status_code == 204: return True, None, None
                elif response.content:
                    try: return True, response.json(), None
                    except json.JSONDecodeError: return True, response.text, None
                else: return True, None, None

            elif response.status_code == 401:
                logger.warning(f"Received 401 for {url}.")
                if attempt_number == 1:
                    logger.info("Attempting token refresh due to 401...")
                    if self._refresh_with_token():
                        # --- IMPORTANT: Signal session update needed ---
                        self._token_was_refreshed_in_request = True # Set flag
                        logger.info("Token refreshed. Retrying original request...")
                        return self._request(method, endpoint, params, json_data, attempt_number=2)
                    else:
                        error_msg = "Auth error: Failed to refresh token after 401."
                        logger.error(error_msg)
                        self._clear_token()
                        return False, None, error_msg
                else:
                    error_msg = f"Auth error (401) persisted after retry for {url}: {response.text}"
                    logger.error(error_msg)
                    return False, None, error_msg
            else:
                error_msg = f"API error: {response.status_code} for {url}. Response: {response.text}"
                logger.warning(error_msg)
                # --- Fix: Don't retry on 4xx errors other than 401 ---
                if response.status_code >= 500 and attempt_number <= DEFAULT_RETRY_ATTEMPTS:
                    retry_delay = attempt_number
                    logger.warning(f"Retrying in {retry_delay}s due to server error...")
                    time.sleep(retry_delay)
                    return self._request(method, endpoint, params, json_data, attempt_number=attempt_number + 1)
                elif response.status_code >= 400 and response.status_code < 500 and response.status_code != 401:
                    # It's a client-side error (e.g., 400, 404, 405). Don't retry.
                    return False, None, error_msg
                else:
                     # This condition handles errors like 5xx after retry limit, or unexpected status codes
                    return False, None, error_msg


        except requests.exceptions.Timeout:
            error_msg = f"Request timeout for {url}"
            logger.warning(error_msg)
            if attempt_number <= DEFAULT_RETRY_ATTEMPTS:
                 retry_delay = attempt_number
                 logger.warning(f"Retrying in {retry_delay}s due to timeout...")
                 time.sleep(retry_delay)
                 return self._request(method, endpoint, params, json_data, attempt_number=attempt_number + 1)
            return False, None, error_msg
        except requests.exceptions.RequestException as e:
            error_msg = f"Network error for {url}: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
        except Exception as e:
            error_msg = f"Unexpected error during request to {url}: {str(e)}"
            logger.exception("Unexpected API request error:")
            return False, None, error_msg


    def get_current_token_info(self) -> Optional[Dict[str, Union[str, float]]]:
         """Returns the current token info needed to persist the session."""
         # Ensure the token is valid *before* returning info, attempt refresh if needed
         if not self._ensure_token_valid():
              logger.warning("Could not ensure token validity when getting current token info.")
              return None # Return None if we couldn't get/refresh a valid token

         if self._access_token and self._refresh_token and self._token_expires_at > 0:
              return {
                   "access_token": self._access_token,
                   "refresh_token": self._refresh_token,
                   "token_expires_at": self._token_expires_at
              }
         logger.warning("Missing token components when getting current token info.")
         return None

    def get_current_agent_info(self) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Get information about the currently logged-in agent."""
        logging.info(f"Getting current agent information")
        return self._request("GET", "/Agent/Me")

    def token_was_refreshed(self) -> bool:
        """Check if the token was refreshed during the last API request."""
        return self._token_was_refreshed_in_request

    # ==========================================================================
    #                       Public API Methods
    # ==========================================================================
    # --- Client Operations ---
    def search_clients( self, search_term: str, limit: int = 50, include_inactive: bool = False ) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        params = { "search": search_term, "limit": min(limit, DEFAULT_PAGE_LIMIT), "includeinactive": str(include_inactive).lower(), "includeactive": "true" }
        logger.info(f"Searching clients with term: '{search_term}'")
        success, response, error = self._request("GET", "/Client", params=params)
        if success and isinstance(response, dict):
            clients = response.get("clients", [])
            count = response.get("record_count", len(clients))
            logger.info(f"Found {len(clients)} clients matching '{search_term}' (Total possible: {count}).")
            return True, clients, None
        elif success:
             logger.warning(f"Search clients successful but response format unexpected: {response}")
             return True, [], f"Unexpected response format: {type(response)}"
        else:
            logger.error(f"Search clients failed: {error}")
            return False, None, error

    def get_client( self, client_id: Union[int, str], include_details: bool = True ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        params = {"includedetails": str(include_details).lower()}
        logger.info(f"Getting client details for ID: {client_id}")
        return self._request("GET", f"/Client/{client_id}", params=params)

    def get_clients_with_open_tickets( self, limit: int = 100 ) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        logger.info("Fetching open tickets to identify clients...")
        # Increase limit for get_tickets here too for accuracy
        success_tickets, tickets, error_tickets = self.get_tickets(open_only=True, limit=1000)
        if not success_tickets:
            msg = f"Failed to fetch open tickets needed to find clients: {error_tickets}"
            logger.error(msg)
            return False, None, msg
        if tickets is None or not tickets:
            logger.info("No open tickets found. No clients to report.")
            return True, [], None
        client_data: Dict[int, Dict[str, Any]] = {}
        for ticket in tickets:
            client_id = ticket.get("client_id")
            client_name = ticket.get("client_name", "Unknown Client")
            if client_id:
                if client_id not in client_data:
                    client_data[client_id] = {"id": client_id, "name": client_name, "open_ticket_count": 0}
                client_data[client_id]["open_ticket_count"] += 1
        clients_with_tickets = sorted( list(client_data.values()), key=lambda x: x["open_ticket_count"], reverse=True )
        limited_clients = clients_with_tickets[:limit]
        logger.info(f"Found {len(limited_clients)} clients with open tickets (out of {len(client_data)} total, limited to {limit}).")
        return True, limited_clients, None

    # --- Ticket Operations ---
    def get_tickets(
        self,
        client_id: Optional[Union[int, str]] = None,
        agent_id: Optional[Union[int, str]] = None, # Added agent_id
        search: Optional[str] = None,
        status_id: Optional[Union[int, str]] = None,
        open_only: bool = False,
        closed_only: bool = False,
        limit: int = 100, # Keep default but allow override
        page_no: int = 1,
        include_details: bool = False,
        include_last_action: bool = False,
        order_by: Optional[str] = None,
        order_desc: bool = True
    ) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        """Enhanced get_tickets with agent_id support"""
        params = {
            "pageinate": "true",
            "page_size": min(limit, DEFAULT_PAGE_LIMIT),
            "page_no": page_no,
            "includedetails": str(include_details).lower(),
            "includelastaction": str(include_last_action).lower(),
            "orderdesc": str(order_desc).lower()
        }

        # Apply filters
        if client_id:
            params["client_id"] = client_id
        if agent_id and agent_id != 'current': # Handle special 'current' case in app.py
            params["agent_id"] = agent_id
        if search:
            params["search"] = search
        if status_id:
            params["status_id"] = status_id
        if open_only and closed_only:
            logger.warning("Both open_only and closed_only are True. Preferring open_only=True.")
            params["open_only"] = "true"
        elif open_only:
            params["open_only"] = "true"
        elif closed_only:
            params["closed_only"] = "true"
        if order_by:
            params["order"] = order_by

        logger.info(f"Fetching tickets page {page_no} with params: {params}")
        success, response, error = self._request("GET", "/Tickets", params=params)

        if success and isinstance(response, dict):
            tickets = response.get("tickets", [])
            count = response.get("record_count", len(tickets))
            logger.info(f"Retrieved {len(tickets)} tickets on page {page_no} (Total possible: {count}).")
            return True, tickets, None
        elif success:
            # Handle case where API returns 200 OK but not a dict (e.g., just text)
            logger.warning(f"Get tickets successful but response format unexpected: {type(response)} - Content: {str(response)[:200]}...")
            return True, [], f"Unexpected response format: {type(response)}"
        else:
            logger.error(f"Get tickets failed: {error}")
            return False, None, error

    def get_ticket( self, ticket_id: Union[int, str], include_details: bool = True, include_last_action: bool = True ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        params = { "includedetails": str(include_details).lower(), "includelastaction": str(include_last_action).lower() }
        logger.info(f"Getting ticket details for ID: {ticket_id}")
        # --- Fix: Convert ticket_id to string for URL ---
        return self._request("GET", f"/Tickets/{str(ticket_id)}", params=params)


    def create_ticket(self, summary: str, details: str, client_id: Optional[Union[int, str]] = None, tickettype_id: Optional[int] = None, user_id: Optional[Union[int, str]] = None, site_id: Optional[Union[int, str]] = None, status_id: Optional[int] = None, priority_id: Optional[int] = None, urgency: Optional[int] = None, impact: Optional[int] = None, category_1: Optional[str] = None, agent_id: Optional[Union[int, str]] = None, team: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Creates a ticket with the provided information."""
        # Enhanced logging of all parameters
        logger.info(f"CREATE_TICKET called with: summary={summary[:50]}..., client_id={client_id} (type: {type(client_id).__name__})")
        logger.debug(f"CREATE_TICKET additional params: tickettype_id={tickettype_id}, user_id={user_id}, site_id={site_id}, status_id={status_id}, priority_id={priority_id}")
        
        # Validate required parameters
        if not summary or not details:
            logger.error("Ticket creation ABORTED: summary and details are required.")
            return False, None, "Ticket summary and details are required."
        if not client_id:
            logger.error("Ticket creation ABORTED: client_id is required.")
            return False, None, "Client ID is required to create a ticket."
        
        # Ensure client_id is an integer - Fix indentation issue
        try:
            # Convert client_id to integer if it's not already
            if not isinstance(client_id, int):
                client_id_int = int(client_id)
                logger.info(f"Converted client_id from {client_id} ({type(client_id).__name__}) to int: {client_id_int}")
            else:
                client_id_int = client_id
                logger.info(f"Client ID already an integer: {client_id_int}")
            
            # Create ticket data dictionary
            ticket_data = {
                "summary": summary,
                "details": details,
                "client_id": client_id_int
            }
            
            # Add optional parameters if provided
            if tickettype_id is not None: ticket_data["tickettype_id"] = tickettype_id
            if user_id is not None: ticket_data["user_id"] = user_id
            if site_id is not None: ticket_data["site_id"] = site_id
            if status_id is not None: ticket_data["status_id"] = status_id
            if priority_id is not None: ticket_data["priority_id"] = priority_id
            if urgency is not None: ticket_data["urgency"] = urgency
            if impact is not None: ticket_data["impact"] = impact
            if category_1 is not None: ticket_data["category_1"] = category_1
            if agent_id is not None: ticket_data["agent_id"] = agent_id
            if team is not None: ticket_data["team"] = team
            
            # Log the final ticket data before creating the API payload
            logger.info(f"Final ticket_data structure: {json.dumps(ticket_data, default=str)}")
            
            # API expects a list for POST /Tickets
            payload = [ticket_data]
            logger.info(f"Sending ticket creation request to API for client {client_id_int}: '{summary[:50]}...'")
            
            # Make the API request
            success, response, error = self._request("POST", "/Tickets", json_data=payload)
            
            # Log the raw API response
            if success:
                if response:
                    logger.info(f"API response for ticket creation: {json.dumps(response, default=str)[:1000]}")
                else:
                    logger.info("API returned success with empty response")
            else:
                logger.error(f"API error response for ticket creation: {error}")
            
            # Process and return the API response
            if success and response:
                # Halo sometimes returns the created object directly, sometimes in a list
                if isinstance(response, list) and len(response) > 0:
                    logger.info(f"Ticket created successfully. ID: {response[0].get('id', 'N/A')}")
                    return True, response[0], None
                elif isinstance(response, dict) and 'id' in response:
                    logger.info(f"Ticket created successfully. ID: {response.get('id', 'N/A')}")
                    return True, response, None
                else:
                    # Successful but format not as expected, still indicate success
                    logger.warning(f"Ticket creation request succeeded but response format was unclear: {json.dumps(response, default=str)[:500]}")
                    return True, {"message": "Ticket created, format unclear.", "raw_response": response}, None
            elif success:
                # Handle 204 No Content or similar success without body
                logger.info("Ticket created successfully (no response body returned).")
                return True, {"message": "Ticket created successfully (no ID returned)."}, None
            else:
                logger.error(f"Create ticket failed: {error}")
                return False, None, f"Create ticket failed: {error}"
        except ValueError as ve:
            # Specific handling for value errors (e.g., client_id conversion)
            error_msg = f"ValueError in create_ticket: {ve}. client_id={client_id} ({type(client_id).__name__})"
            logger.error(error_msg)
            return False, None, error_msg
        except TypeError as te:
            # Specific handling for type errors
            error_msg = f"TypeError in create_ticket: {te}. client_id={client_id} ({type(client_id).__name__})"
            logger.error(error_msg)
            return False, None, error_msg
        except Exception as e:
            # Catch-all for unexpected errors
            error_msg = f"Unexpected error in create_ticket: {e}"
            logger.exception(error_msg)
            return False, None, error_msg

    def update_ticket( self, ticket_id: Union[int, str], update_data: Dict[str, Any] ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        if not update_data: return False, None, "No update data provided"
        logger.info(f"Attempting update ticket #{ticket_id} with fields: {list(update_data.keys())}")
        # --- Fix: Use PATCH, not POST, for updating tickets ---
        success, response, error = self._request("PATCH", f"/Tickets/{str(ticket_id)}", json_data=update_data)
        if success:
            logger.info(f"Ticket #{ticket_id} updated successfully ({'body returned' if response else 'no content'}).")
            # Return the updated ticket if available, else a simple success message
            return True, response if response else {"message": f"Ticket {ticket_id} updated successfully."}, None
        else:
            logger.error(f"Update ticket #{ticket_id} failed: {error}")
            return False, None, error

    # --- Action Operations ---
    def add_note( self, ticket_id: Union[int, str], note_text: str, is_private: bool = True, time_taken: Optional[int] = None, outcome: Optional[str] = None ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        if not note_text: return False, None, "Note text cannot be empty."
        if outcome is None: outcome = "Private Note" if is_private else "Note Added"
        action_data = { "ticket_id": int(ticket_id), "outcome": outcome, "note": note_text, "hiddenfromuser": is_private }
        if time_taken is not None:
            try: action_data["timetaken"] = max(0, int(time_taken)) # Ensure non-negative
            except (ValueError, TypeError): logger.warning(f"Invalid time_taken value: {time_taken}. Ignoring.")
        payload = [action_data] # API expects a list for POST /Actions
        log_note_preview = f"{note_text[:50]}{'...' if len(note_text) > 50 else ''}"
        logger.info(f"Adding note to ticket #{ticket_id}: '{log_note_preview}' (Private: {is_private})")
        success, response, error = self._request("POST", "/Actions", json_data=payload)
        if success:
            created_action = None
            # Handle response format variation
            if isinstance(response, list) and len(response) > 0: created_action = response[0]
            elif isinstance(response, dict): created_action = response
            action_id = created_action.get("id", "N/A") if created_action else "N/A"
            logger.info(f"Note added successfully to ticket #{ticket_id}. Action ID: {action_id}")
            return True, created_action, None
        else:
            logger.error(f"Add note failed for ticket #{ticket_id}: {error}")
            return False, None, error

    def get_actions( self, ticket_id: Union[int, str], count: int = 20, exclude_system: bool = True, ) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        params = { "ticket_id": int(ticket_id), "count": min(count, DEFAULT_PAGE_LIMIT*2), "excludesys": str(exclude_system).lower() }
        logger.info(f"Getting actions for ticket #{ticket_id} with params: {params}")
        success, response, error = self._request("GET", "/Actions", params=params)
        if success and isinstance(response, dict):
            actions = response.get("actions", [])
            logger.info(f"Retrieved {len(actions)} actions for ticket #{ticket_id}.")
            return True, actions, None
        elif success:
            logger.warning(f"Get actions successful but response format unexpected: {type(response)}")
            return True, [], f"Unexpected response format: {type(response)}"
        else:
            logger.error(f"Get actions failed: {error}")
            return False, None, f"Get actions failed: {error}"

    def delete_action( self, action_id: Union[int, str], ticket_id: Union[int, str] ) -> Tuple[bool, None, Optional[str]]:
        if not action_id or not ticket_id: return False, None, "Action ID and Ticket ID required."
        params = {"ticket_id": int(ticket_id)}
        logger.info(f"Deleting action #{action_id} from ticket #{ticket_id}")
        # --- Fix: Correct endpoint ---
        success, response, error = self._request("DELETE", f"/Actions/{str(action_id)}", params=params)
        if success:
             logger.info(f"Action #{action_id} deleted successfully from ticket #{ticket_id}.")
             return True, None, None
        else:
             logger.error(f"Delete action #{action_id} failed: {error}")
             return False, None, f"Delete action #{action_id} failed: {error}"

    # --- Additional Useful Composite Methods ---
    def get_client_ticket_summary( self, client_id: Union[int, str] ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        logger.info(f"Generating ticket summary for client #{client_id}")
        try:
            client_id_int = int(client_id)
        except (ValueError, TypeError):
            return False, None, f"Invalid client_id: {client_id}"

        success_open, open_tickets, error_open = self.get_tickets( client_id=client_id_int, open_only=True, limit=1000 ) # Get more tickets for analysis
        if not success_open: return False, None, f"Error fetching open tickets: {error_open}"
        open_tickets = open_tickets or []

        # --- Fix: Use different limit for closed tickets ---
        success_closed, closed_tickets, error_closed = self.get_tickets( client_id=client_id_int, closed_only=True, limit=50, order_by='dateclosed', order_desc=True )
        if not success_closed: logger.warning(f"Error fetching closed tickets: {error_closed}")
        closed_tickets = closed_tickets or []

        status_summary, priority_summary, category_summary = {}, {}, {}
        for ticket in open_tickets + closed_tickets:
            # Use more specific field names if available
            status = ticket.get("status", {}).get("name", ticket.get("status_id"))
            priority = ticket.get("priority", {}).get("name", ticket.get("priority_id"))
            category = ticket.get("category_1")

            if status: status_summary[status] = status_summary.get(status, 0) + 1
            if priority: priority_summary[priority] = priority_summary.get(priority, 0) + 1
            if category: category_summary[category] = category_summary.get(category, 0) + 1

        summary = {
            "client_id": client_id_int,
            "open_ticket_count": len(open_tickets),
            "recently_closed_count": len(closed_tickets), # Reflects limit used
            "total_analyzed_count": len(open_tickets) + len(closed_tickets),
            "status_summary": status_summary,
            "priority_summary": priority_summary,
            "category_summary": category_summary,
            "recent_open_tickets_sample": [ # Sample remains useful
                {"id": t.get("id"), "summary": t.get("summary"), "status": t.get("status",{}).get("name")}
                for t in open_tickets[:5]
            ]
        }
        logger.info(f"Generated ticket summary for client #{client_id_int}")
        return True, summary, None

# --- Example Usage Block ---
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if not os.path.exists(dotenv_path):
         dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path=dotenv_path)

    logging.basicConfig(level=logging.DEBUG) # Enable debug for testing

    halo_auth = os.getenv("HALO_AUTH_URL")
    halo_id = os.getenv("HALO_CLIENT_ID")
    halo_user = os.getenv("HALO_USERNAME")
    halo_pass = os.getenv("HALO_PASSWORD")
    halo_base = os.getenv("HALO_API_BASE")
    halo_tenant = os.getenv("HALO_TENANT")

    if not all([halo_auth, halo_id, halo_user, halo_pass, halo_base]):
        print("Error: Missing HALO env vars for Password Grant testing.")
        exit()

    try:
        print("Initializing HaloAPI with Password Grant...")
        api = HaloAPI( auth_url=halo_auth, client_id=halo_id, username=halo_user, password=halo_pass, api_base_url=halo_base, tenant=halo_tenant )
        print("Initialization complete.")
        token_info = api.get_current_token_info()
        print(f"\nInitial Token Info: {json.dumps(token_info, indent=2)}")

        if token_info:
            print("\n--- Testing Initialization with Existing Tokens ---")
            try:
                api_from_token = HaloAPI( auth_url=halo_auth, client_id=halo_id, api_base_url=halo_base, tenant=halo_tenant, access_token=token_info['access_token'], refresh_token=token_info['refresh_token'], token_expires_at=token_info['token_expires_at'], username_for_log=f"{halo_user}_token_test" )
                print("Successfully initialized API from existing tokens.")

                print("\n--- Testing Search Clients (Token Init Instance) ---")
                success_sc_token, clients_token, error_sc_token = api_from_token.search_clients("NonExistentClientXYZ", limit=2)
                if success_sc_token: print(f"Found clients (token): {json.dumps(clients_token, indent=2)}")
                else: print(f"Search failed (token): {error_sc_token}")

                print("\n--- Testing Get My Tickets (Token Init Instance) ---")
                agent_ok, agent_info, agent_err = api_from_token.get_current_agent_info()
                if agent_ok and agent_info:
                    print(f"Current Agent ID: {agent_info.get('id')}")
                    success_tk_agent, tickets_agent, error_tk_agent = api_from_token.get_tickets(agent_id=agent_info['id'], open_only=True, limit=5)
                    if success_tk_agent: print(f"Found tickets for agent: {json.dumps(tickets_agent, indent=2)}")
                    else: print(f"Get agent tickets failed: {error_tk_agent}")
                else:
                     print(f"Could not get current agent info: {agent_err}")

            except Exception as e_token: print(f"Failed token init or test: {e_token}")

        print("\n--- Testing Search Clients (Password Init Instance) ---")
        success_sc, clients, error_sc = api.search_clients("Example Client", limit=5) # Use a client name likely to exist
        if success_sc and clients:
             print(f"Found clients: {json.dumps(clients, indent=2)}")
             test_client_id = clients[0].get('id')
             print(f"\n--- Testing Get Client Details (Client ID: {test_client_id}) ---")
             success_gc, client_details, error_gc = api.get_client(test_client_id)
             if success_gc: print(f"Client details: {json.dumps(client_details, indent=2)}")
             else: print(f"Get client failed: {error_gc}")

             print(f"\n--- Testing Get Client Tickets (Client ID: {test_client_id}) ---")
             success_ctk, client_tickets, error_ctk = api.get_tickets(client_id=test_client_id, limit=10, open_only=True)
             if success_ctk: print(f"Client tickets: {json.dumps(client_tickets, indent=2)}")
             else: print(f"Get client tickets failed: {error_ctk}")

             if client_tickets:
                 test_ticket_id = client_tickets[0].get('id')
                 print(f"\n--- Testing Get Single Ticket (Ticket ID: {test_ticket_id}) ---")
                 success_gtk, ticket_details, error_gtk = api.get_ticket(test_ticket_id)
                 if success_gtk: print(f"Ticket details: {json.dumps(ticket_details, indent=2)}")
                 else: print(f"Get ticket failed: {error_gtk}")

                 print(f"\n--- Testing Add Note to Ticket {test_ticket_id} ---")
                 success_an, note_details, error_an = api.add_note(test_ticket_id, "This is a test note from enhanced_halo_api.", is_private=True, time_taken=5)
                 if success_an: print(f"Note added: {json.dumps(note_details, indent=2)}")
                 else: print(f"Add note failed: {error_an}")

                 if success_an and note_details:
                     test_action_id = note_details.get('id')
                     print(f"\n--- Testing Delete Action {test_action_id} from Ticket {test_ticket_id} ---")
                     success_da, _, error_da = api.delete_action(test_action_id, test_ticket_id)
                     if success_da: print("Action deleted successfully.")
                     else: print(f"Delete action failed: {error_da}")


        else:
             print("Could not find a client named 'Example Client' to run further tests.")

        print("\n--- Testing Create Ticket ---")
        # Find a real client ID first for a better test
        success_sc_for_create, clients_for_create, _ = api.search_clients("Test", limit=1)
        create_client_id = None
        if success_sc_for_create and clients_for_create:
            create_client_id = clients_for_create[0].get('id')
            print(f"Using client ID {create_client_id} for ticket creation test.")
        else:
            print("WARNING: Could not find a client named 'Test' for ticket creation. Test might fail.")
            # You might need to manually insert a client ID here if 'Test' doesn't exist
            # create_client_id = 1 # Replace with a valid ID

        if create_client_id:
             success_crt, created_ticket, error_crt = api.create_ticket(
                 summary="Test ticket via API",
                 details="This is the detail body of the test ticket created via the enhanced API wrapper.",
                 client_id=create_client_id,
                 priority_id=1 # Adjust if priority ID 1 doesn't exist
             )
             if success_crt: print(f"Ticket created: {json.dumps(created_ticket, indent=2)}")
             else: print(f"Create ticket failed: {error_crt}")

             if success_crt and created_ticket:
                  new_ticket_id = created_ticket.get('id')
                  if new_ticket_id:
                     print(f"\n--- Testing Update Ticket {new_ticket_id} ---")
                     update_payload = {"summary": "Test ticket via API [UPDATED]"}
                     success_ut, updated_resp, error_ut = api.update_ticket(new_ticket_id, update_payload)
                     if success_ut: print(f"Ticket update response: {json.dumps(updated_resp, indent=2)}")
                     else: print(f"Update ticket failed: {error_ut}")
                  else:
                     print("Could not get ID from created ticket to test update.")


    except HaloAuthenticationError as e:
        print(f"CRITICAL HALO AUTH ERROR: {e}")
    except Exception as e:
        print(f"General testing error: {e}")
        logger.exception("Testing Exception:")

    print("\n--- Testing Complete ---")