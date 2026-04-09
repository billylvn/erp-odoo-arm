# -*- coding: utf-8 -*-
"""
OAuth2 callback controller for Cloudbeds.

Flow:
  1. User clicks "Connect to Cloudbeds" on the backend form.
  2. Browser is redirected to https://api.cloudbeds.com/api/v1.3/oauth?...&state=<backend_id>
  3. User logs in and approves access.
  4. Cloudbeds redirects to /cloudbeds/oauth/callback?code=...&state=<backend_id>
  5. This controller exchanges the code for tokens and saves them to the backend.
  6. User is redirected back to the Cloudbeds backend list.
"""
import logging
from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)

SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Cloudbeds Connected</title>
  <style>
    body {{ font-family: sans-serif; display: flex; align-items: center;
            justify-content: center; height: 100vh; margin: 0; background: #f0f2f5; }}
    .card {{ background: white; padding: 40px 60px; border-radius: 8px;
             box-shadow: 0 2px 12px rgba(0,0,0,.12); text-align: center; max-width: 480px; }}
    h1 {{ color: #28a745; }} p {{ color: #555; }}
    a {{ color: #007bff; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>&#10003; Connected!</h1>
    <p>Your Cloudbeds account has been successfully connected to Odoo.</p>
    <p><a href="/odoo/action-cl_cloudbeds_integration.action_cloudbeds_backend">
      &larr; Back to Cloudbeds Backends
    </a></p>
  </div>
</body>
</html>
"""

ERROR_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Cloudbeds OAuth Error</title>
  <style>
    body {{ font-family: sans-serif; display: flex; align-items: center;
            justify-content: center; height: 100vh; margin: 0; background: #f0f2f5; }}
    .card {{ background: white; padding: 40px 60px; border-radius: 8px;
             box-shadow: 0 2px 12px rgba(0,0,0,.12); text-align: center; max-width: 480px; }}
    h1 {{ color: #dc3545; }} p {{ color: #555; }}
    a {{ color: #007bff; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>&#10007; Authentication Failed</h1>
    <p>{error}</p>
    <p><a href="/odoo/action-cl_cloudbeds_integration.action_cloudbeds_backend">
      &larr; Back to Cloudbeds Backends
    </a></p>
  </div>
</body>
</html>
"""


class CloudbedsOAuthController(http.Controller):

    @http.route(
        '/cloudbeds/oauth/callback',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
        website=False,
    )
    def oauth_callback(self, code=None, state=None, error=None, **kwargs):
        """Handle the OAuth2 redirect from Cloudbeds."""
        if error:
            _logger.error('Cloudbeds OAuth error: %s', error)
            return request.make_response(
                ERROR_HTML.format(error=f'Cloudbeds OAuth error: {error}'),
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )

        if not state or not code:
            return request.make_response(
                ERROR_HTML.format(
                    error='Invalid OAuth callback — missing state (backend_id) or code.'
                ),
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )

        try:
            backend_id = int(state)
            backend = request.env['cloudbeds.backend'].sudo().browse(backend_id)
            if not backend.exists():
                raise ValueError(f'Backend {backend_id} not found.')

            backend._finalise_oauth(code)
            _logger.info(
                'Cloudbeds OAuth success for backend "%s" (id=%s).',
                backend.name, backend_id,
            )
        except Exception as exc:
            _logger.error('Cloudbeds OAuth callback failed: %s', exc, exc_info=True)
            return request.make_response(
                ERROR_HTML.format(error=f'OAuth authentication failed: {exc}'),
                headers=[('Content-Type', 'text/html; charset=utf-8')],
            )

        return request.make_response(
            SUCCESS_HTML,
            headers=[('Content-Type', 'text/html; charset=utf-8')],
        )
