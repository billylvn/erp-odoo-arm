# -*- coding: utf-8 -*-
"""
Webhook receiver for Cloudbeds real-time events.

Endpoint: POST /cloudbeds/webhook/<backend_id>

Supported events:
  - reservation/created       → create staging record
  - reservation/status_changed → update status, process if checked_out / canceled
"""
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CloudbedsWebhookController(http.Controller):

    @http.route(
        '/cloudbeds/webhook/<int:backend_id>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def webhook(self, backend_id, **kwargs):
        """Receive and process a Cloudbeds webhook POST."""
        backend = request.env['cloudbeds.backend'].sudo().browse(backend_id)
        if not backend.exists():
            _logger.warning('Cloudbeds webhook: backend %d not found.', backend_id)
            return request.make_response(
                json.dumps({'error': 'Backend not found'}),
                headers=[('Content-Type', 'application/json')],
                status=404,
            )

        # Read raw body
        raw_body = request.httprequest.get_data(as_text=True)

        # Verify HMAC-SHA256 signature
        signature = request.httprequest.headers.get('X-Cloudbeds-Signature') or ''
        if not self._verify_signature(raw_body, signature, backend.client_secret or ''):
            _logger.warning(
                'Cloudbeds webhook: invalid signature for backend %d.', backend_id
            )
            return request.make_response(
                json.dumps({'error': 'Invalid signature'}),
                headers=[('Content-Type', 'application/json')],
                status=403,
            )

        # Parse payload
        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError) as exc:
            _logger.error('Cloudbeds webhook: invalid JSON: %s', exc)
            return request.make_response(
                json.dumps({'error': 'Invalid JSON'}),
                headers=[('Content-Type', 'application/json')],
                status=400,
            )

        event_type = payload.get('type') or ''
        event_object = payload.get('object') or ''
        event_action = payload.get('action') or ''

        _logger.info(
            'Cloudbeds webhook received: object=%s action=%s backend=%d',
            event_object, event_action, backend_id,
        )

        try:
            self._dispatch(backend, event_object, event_action, payload)
        except Exception as exc:
            _logger.error(
                'Cloudbeds webhook dispatch error: %s', exc, exc_info=True
            )
            return request.make_response(
                json.dumps({'error': str(exc)}),
                headers=[('Content-Type', 'application/json')],
                status=500,
            )

        return request.make_response(
            json.dumps({'status': 'ok'}),
            headers=[('Content-Type', 'application/json')],
        )

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_signature(body, signature, secret):
        """
        Verify HMAC-SHA256 signature from Cloudbeds.
        Expected format: signature = HMAC-SHA256(secret, body)
        """
        if not secret:
            # If no secret is configured, skip verification (development mode)
            _logger.debug('Cloudbeds webhook: no client_secret — skipping signature check.')
            return True
        if not signature:
            return False
        try:
            expected = hmac.new(
                secret.encode('utf-8'),
                body.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, signature.lower())
        except Exception as exc:
            _logger.error('Cloudbeds webhook signature verification error: %s', exc)
            return False

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, backend, event_object, event_action, payload):
        """Route webhook payload to the appropriate handler."""
        env = request.env

        if event_object == 'reservation':
            reservation_id = str(
                payload.get('reservationID')
                or (payload.get('data') or {}).get('reservationID')
                or ''
            )
            if not reservation_id:
                _logger.warning('Cloudbeds webhook: no reservationID in payload.')
                return

            if event_action == 'created':
                self._handle_reservation_created(env, backend, reservation_id, payload)

            elif event_action == 'status_changed':
                new_status = (
                    payload.get('status')
                    or (payload.get('data') or {}).get('reservationStatus')
                    or ''
                )
                self._handle_reservation_status_changed(
                    env, backend, reservation_id, new_status, payload
                )

    def _handle_reservation_created(self, env, backend, reservation_id, payload):
        """Create a new staging record for a newly created reservation."""
        existing = env['cloudbeds.reservation'].sudo().search(
            [('cb_reservation_id', '=', reservation_id), ('backend_id', '=', backend.id)],
            limit=1,
        )
        if existing:
            return

        data = payload.get('data') or payload
        env['cloudbeds.reservation'].sudo().create({
            'name': reservation_id,
            'backend_id': backend.id,
            'cb_reservation_id': reservation_id,
            'cb_status': data.get('reservationStatus') or 'not_confirmed',
            'cb_check_in': data.get('startDate') or data.get('checkIn'),
            'cb_check_out': data.get('endDate') or data.get('checkOut'),
            'cb_guest_id': str(data.get('guestID') or ''),
            'cb_guest_name': data.get('guestName') or '',
            'state': 'pending',
        })
        _logger.info(
            'Cloudbeds webhook: created reservation staging record %s.', reservation_id
        )

    def _handle_reservation_status_changed(
        self, env, backend, reservation_id, new_status, payload
    ):
        """Update reservation status; process if now checked_out or cancelled."""
        reservation = env['cloudbeds.reservation'].sudo().search(
            [('cb_reservation_id', '=', reservation_id), ('backend_id', '=', backend.id)],
            limit=1,
        )

        if not reservation:
            # Create the record first
            data = payload.get('data') or payload
            reservation = env['cloudbeds.reservation'].sudo().create({
                'name': reservation_id,
                'backend_id': backend.id,
                'cb_reservation_id': reservation_id,
                'cb_status': new_status or 'confirmed',
                'cb_check_in': data.get('startDate') or data.get('checkIn'),
                'cb_check_out': data.get('endDate') or data.get('checkOut'),
                'cb_guest_id': str(data.get('guestID') or ''),
                'cb_guest_name': data.get('guestName') or '',
                'state': 'pending',
            })

        reservation.write({'cb_status': new_status})

        # Auto-process on checkout or cancellation
        if new_status in ('checked_out', 'canceled', 'no_show') and reservation.state in ('pending', 'error'):
            try:
                reservation._process()
            except Exception as exc:
                reservation.write({
                    'state': 'error',
                    'error_message': str(exc)[:250],
                })
                _logger.error(
                    'Cloudbeds webhook: failed to process reservation %s after status change: %s',
                    reservation_id, exc, exc_info=True,
                )
