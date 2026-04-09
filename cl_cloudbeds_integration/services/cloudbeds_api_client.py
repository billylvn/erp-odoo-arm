# -*- coding: utf-8 -*-
"""
Cloudbeds API v1.3 Client
=========================
Pure HTTP client — no Odoo model dependencies.
Handles OAuth2 token management, pagination, rate limiting, and error normalisation.

API Base URL:   https://hotels.cloudbeds.com/api/v1.3/{method}
OAuth Base URL: https://api.cloudbeds.com/api/v1.3/
Docs:     https://developers.cloudbeds.com/reference/about-pms-api
"""
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlencode

_logger = logging.getLogger(__name__)

BASE_URL = 'https://hotels.cloudbeds.com/api/v1.3'
# OAuth endpoints live on api.cloudbeds.com (not hotels.cloudbeds.com)
OAUTH_BASE_URL = 'https://api.cloudbeds.com/api/v1.3'
TOKEN_URL = f'{OAUTH_BASE_URL}/access_token'
AUTHORIZE_URL = f'{OAUTH_BASE_URL}/oauth'
USERINFO_URL = f'{OAUTH_BASE_URL}/userinfo'
REQUEST_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 100
# Cloudbeds rate limit: 5 req/sec — add a small sleep between retries
RATE_LIMIT_SLEEP = 0.2


class CloudbedsApiError(Exception):
    """Raised when the Cloudbeds API returns a non-success response."""

    def __init__(self, status_code, message, response=None):
        self.status_code = status_code
        self.message = message
        self.response = response
        super().__init__(f'[{status_code}] {message}')


class CloudbedsApiClient:
    """
    Thin, stateless HTTP wrapper around the Cloudbeds API v1.3.

    Usage::

        client = CloudbedsApiClient(
            property_id='12345',
            access_token='...',
            refresh_token='...',
            client_id='...',
            client_secret='...',
            on_token_refresh=lambda access, refresh: ...,
        )
        info = client.get_hotel_details()
    """

    def __init__(
        self,
        property_id,
        access_token,
        refresh_token=None,
        client_id=None,
        client_secret=None,
        on_token_refresh=None,
    ):
        self.property_id = property_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.on_token_refresh = on_token_refresh
        self._session = self._build_session()

    # ------------------------------------------------------------------
    # Session / transport
    # ------------------------------------------------------------------

    def _build_session(self):
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['GET', 'POST', 'PUT', 'DELETE'],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('https://', adapter)
        return session

    def _auth_headers(self):
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json',
        }

    # ------------------------------------------------------------------
    # OAuth token management
    # ------------------------------------------------------------------

    def refresh_access_token(self):
        """Exchange the refresh_token for a new access_token."""
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise CloudbedsApiError(0, 'Cannot refresh token: missing credentials.')

        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        }
        response = requests.post(
            TOKEN_URL,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=REQUEST_TIMEOUT,
        )
        self._raise_for_status(response)

        token_data = response.json()
        self.access_token = token_data.get('access_token', self.access_token)
        if token_data.get('refresh_token'):
            self.refresh_token = token_data['refresh_token']

        if callable(self.on_token_refresh):
            self.on_token_refresh(self.access_token, self.refresh_token)

        _logger.info('Cloudbeds access token refreshed successfully.')
        return self.access_token

    @staticmethod
    def build_authorize_url(client_id, redirect_uri, state=None):
        """Return the URL the user should be redirected to for OAuth consent."""
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
        }
        if state:
            params['state'] = state
        return f'{AUTHORIZE_URL}?{urlencode(params)}'

    @staticmethod
    def exchange_code_for_tokens(code, client_id, client_secret, redirect_uri):
        """Exchange an OAuth2 authorisation code for access + refresh tokens."""
        data = {
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }
        response = requests.post(
            TOKEN_URL,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Core HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_status(response):
        if response.ok:
            return
        try:
            detail = response.json()
            message = (
                detail.get('message')
                or detail.get('error')
                or detail.get('error_description')
                or response.text
            )
        except Exception:
            message = response.text
        raise CloudbedsApiError(response.status_code, message, response)

    def _request(self, method, endpoint, params=None, data=None, retry_on_401=True):
        """
        Perform an authenticated HTTP request.
        Auto-refreshes on 401. Adds property_id to all GET params.
        POST/PUT/DELETE use application/x-www-form-urlencoded.
        """
        url = f'{BASE_URL}/{endpoint.lstrip("/")}'
        headers = self._auth_headers()

        # Cloudbeds requires property_id on all requests
        _params = {'propertyID': self.property_id}
        if params:
            _params.update(params)

        if method.upper() in ('POST', 'PUT', 'DELETE') and data:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'

        response = self._session.request(
            method,
            url,
            params=_params,
            data=data,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        # Auto-refresh on 401
        if response.status_code == 401 and retry_on_401 and self.refresh_token:
            _logger.debug('Cloudbeds returned 401 — attempting token refresh.')
            self.refresh_access_token()
            headers = self._auth_headers()
            response = self._session.request(
                method,
                url,
                params=_params,
                data=data,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

        self._raise_for_status(response)

        if response.status_code == 204 or not response.content:
            return {}

        result = response.json()

        # Cloudbeds wraps errors in {"success": false, "message": "..."}
        if isinstance(result, dict) and result.get('success') is False:
            raise CloudbedsApiError(
                response.status_code,
                result.get('message', 'Cloudbeds API error'),
                response,
            )

        # Small sleep to respect the 5 req/sec rate limit
        time.sleep(RATE_LIMIT_SLEEP)
        return result

    # ------------------------------------------------------------------
    # Connection check
    # ------------------------------------------------------------------

    def check_access_token(self):
        """Verify the current access token / API key is valid via the userinfo endpoint."""
        response = self._session.get(
            USERINFO_URL,
            headers=self._auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        self._raise_for_status(response)
        return response.json()

    # ------------------------------------------------------------------
    # Property / Hotel
    # ------------------------------------------------------------------

    def get_hotel_details(self):
        """Return details for the configured property."""
        return self._request('GET', 'getHotelDetails')

    def get_hotels(self):
        """Return all properties accessible with this token."""
        return self._request('GET', 'getHotels')

    # ------------------------------------------------------------------
    # Reservations
    # ------------------------------------------------------------------

    def get_reservations(
        self,
        status=None,
        results_from=None,
        results_to=None,
        modified_from=None,
        page_number=1,
        page_size=DEFAULT_PAGE_SIZE,
    ):
        """Return one page of reservations with guest details included."""
        params = {
            'pageNumber': page_number,
            'pageSize': page_size,
            'includeGuestsDetails': 'true',
        }
        if status:
            params['status'] = status
        if results_from:
            params['resultsFrom'] = results_from
        if results_to:
            params['resultsTo'] = results_to
        if modified_from:
            params['modifiedFrom'] = modified_from
        return self._request('GET', 'getReservations', params=params)

    def get_reservation(self, reservation_id):
        """Return full details for a single reservation."""
        return self._request('GET', 'getReservation', params={'reservationID': reservation_id})

    def get_all_reservations(
        self,
        status=None,
        results_from=None,
        results_to=None,
        modified_from=None,
    ):
        """
        Iterate through all pages and return every reservation record.
        Yields dicts.
        """
        page = 1
        while True:
            result = self.get_reservations(
                status=status,
                results_from=results_from,
                results_to=results_to,
                modified_from=modified_from,
                page_number=page,
                page_size=DEFAULT_PAGE_SIZE,
            )
            data = result.get('data') or []
            if not data:
                break
            yield from data
            # Fewer records than requested → this was the last page
            if len(data) < DEFAULT_PAGE_SIZE:
                break
            page += 1

    def put_reservation(self, data):
        """Update an existing reservation in Cloudbeds."""
        return self._request('PUT', 'putReservation', data=data)

    # ------------------------------------------------------------------
    # Guests
    # ------------------------------------------------------------------

    def get_guest_list(
        self,
        results_from=None,
        results_to=None,
        page_number=1,
        page_size=DEFAULT_PAGE_SIZE,
    ):
        """Return one page of guests."""
        params = {
            'pageNumber': page_number,
            'pageSize': page_size,
        }
        if results_from:
            params['resultsFrom'] = results_from
        if results_to:
            params['resultsTo'] = results_to
        return self._request('GET', 'getGuestList', params=params)

    def get_all_guests(self, results_from=None, results_to=None):
        """Iterate through all pages and return every guest record."""
        page = 1
        while True:
            result = self.get_guest_list(
                results_from=results_from,
                results_to=results_to,
                page_number=page,
                page_size=DEFAULT_PAGE_SIZE,
            )
            data = result.get('data') or []
            if not data:
                break
            yield from data
            # Fewer records than requested → this was the last page
            if len(data) < DEFAULT_PAGE_SIZE:
                break
            page += 1

    def get_guest(self, guest_id):
        """Return details for a single guest."""
        return self._request('GET', 'getGuest', params={'guestID': guest_id})

    def post_guest(self, data):
        """Create a new guest in Cloudbeds."""
        return self._request('POST', 'postGuest', data=data)

    def put_guest(self, data):
        """Update an existing guest in Cloudbeds."""
        return self._request('PUT', 'putGuest', data=data)

    # ------------------------------------------------------------------
    # Items / Products
    # ------------------------------------------------------------------

    def get_items(self):
        """Return all catalog items/products."""
        return self._request('GET', 'getItems')

    def get_item(self, item_id):
        """Return a single catalog item."""
        return self._request('GET', 'getItem', params={'itemID': item_id})

    def get_item_categories(self):
        """Return all item categories."""
        return self._request('GET', 'getItemCategories')

    # ------------------------------------------------------------------
    # Payment Methods / Taxes
    # ------------------------------------------------------------------

    def post_payment(
        self,
        reservation_id,
        payment_type,
        amount,
        description=None,
        card_type=None,
        sub_reservation_id=None,
    ):
        """
        Register a payment against a reservation in Cloudbeds.

        POST /postPayment
        Required: reservationID, type, amount
        Optional: description, cardType (required when type='credit'), subReservationID

        Payment types: cash, credit, debit, bank_transfer, check, on_account
        Card types (when type=credit): visa, mastercard, amex, discover, diners, jcb, unionpay
        """
        data = {
            'reservationID': str(reservation_id),
            'type': payment_type,
            'amount': str(round(float(amount), 2)),
        }
        if description:
            data['description'] = description
        if card_type and payment_type == 'credit':
            data['cardType'] = card_type
        if sub_reservation_id:
            data['subReservationID'] = str(sub_reservation_id)
        return self._request('POST', 'postPayment', data=data)

    def get_payment_methods(self):
        """Return all active payment methods configured for the property."""
        return self._request('GET', 'getPaymentMethods', params={'lang': 'en'})

    def get_taxes_and_fees(self):
        """Return all taxes and fees configured for the property, including custom item taxes."""
        return self._request(
            'GET',
            'getTaxesAndFees',
            params={'includeCustomItemTaxes': 'true'},
        )
    
    def get_currencies(self):
        """Return all currencies configured for the property."""
        return self._request(
            'GET',
            'getCurrencySettings',
        )

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def get_webhooks(self):
        """Return all registered webhooks."""
        return self._request('GET', 'getWebhooks')

    def post_webhook(self, endpoint_url, object_name, action):
        """
        Register a new webhook.

        :param endpoint_url: The URL that Cloudbeds will POST events to.
        :param object_name: e.g. 'reservation'
        :param action: e.g. 'created', 'status_changed'
        """
        data = {
            'endpointUrl': endpoint_url,
            'object': object_name,
            'action': action,
        }
        return self._request('POST', 'postWebhook', data=data)

    def delete_webhook(self, subscription_id):
        """Delete a registered webhook by subscription ID."""
        return self._request(
            'DELETE', 'deleteWebhook', params={'subscriptionID': subscription_id}
        )
