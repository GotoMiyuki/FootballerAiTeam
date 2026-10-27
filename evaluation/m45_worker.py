"""Real SDK over an offline transport, inside the production owned worker."""
import json
from pathlib import Path
import sys
import threading

from backend.execution_worker import main


def model():
    import httpx
    from unittest.mock import patch
    from config import config
    from utils.helpers import create_llm
    payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    mode = payload['history'][-1]['content'].removeprefix('m45:')
    attempts = []
    def transport(request):
        attempts.append(True)
        (Path(sys.argv[1]).parent / 'request-started').write_text('offline', encoding='utf-8')
        if mode == 'hang':
            threading.Event().wait()
        status = 500 if mode == 'retry_failure' or mode == 'retry_success' and len(attempts) == 1 else 200
        if status != 200:
            return httpx.Response(status, json={'error': {'message': 'private-test-error', 'type': 'offline'}})
        body = {'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'offline',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'offline explanation'}, 'finish_reason': 'stop'}]}
        if mode != 'missing':
            body['usage'] = {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}
        return httpx.Response(200, json=body)
    client = httpx.Client(transport=httpx.MockTransport(transport))
    with patch.object(config, 'OPENAI_API_KEY', 'offline-only'), patch.object(config, 'OPENAI_BASE_URL', 'http://offline.invalid/v1'):
        return create_llm(http_client=client)


if __name__ == '__main__':
    main(llm_factory=model)
