"""Explicit v1 request bodies for existing API tests; no server compatibility shim."""
from uuid import uuid4


def message_body(client, payload):
    body = {'request_id': f'test_{uuid4().hex}', **payload}
    if body.get('intent') == 'followup' and body.get('mission_id') and 'pause_id' not in body:
        view = client.get('/api/missions/' + body['mission_id']).json()
        body['pause_id'] = (view.get('blocked') or {}).get('pause_id')
    return body


def input_body(client, url, payload):
    view = client.get(url.removesuffix('/input')).json()
    # A terminal-task rejection still needs a syntactically valid submitted ID.
    return {'request_id': f'test_{uuid4().hex}', 'pause_id': (view.get('blocked') or {}).get('pause_id') or 'previous_pause', **payload}
