"""Run: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.models import MissionView, MessageRequest, MessageAccepted, InputRequest, MessageView
from backend.service import MissionService

ROOT = Path(__file__).resolve().parent.parent

def create_app(*, data_dir=None, demo=None, demo_delay=0.5):
    @asynccontextmanager
    async def lifespan(app):
        demo_mode = demo if demo is not None else os.getenv('FAIT_DEMO', '0') == '1'
        default_dir = ROOT / 'memory' / 'web' / ('demo' if demo_mode else 'live')
        app.state.service = MissionService(Path(data_dir or os.getenv('FAIT_DATA_DIR', str(default_dir))),
            demo=demo_mode, demo_delay=demo_delay)
        yield
        app.state.service.close()
    app = FastAPI(title='FootballerAiTeam API', version='0.1.0', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'], allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Last-Event-ID'])
    def service():
        return app.state.service
    def mission(mission_id):
        try:
            return service().store.get(mission_id)
        except KeyError:
            raise HTTPException(404, '任务不存在')

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': 'demo' if service().demo else 'live', 'version': '0.1.0'}

    def read_memory(name):
        with (ROOT / 'memory' / name).open(encoding='utf-8') as file:
            return json.load(file)

    @app.get('/api/player')
    def player():
        raw = read_memory('player.json')
        return {key: raw.get(key) for key in ['name', 'age', 'height', 'weight', 'position', 'nationality', 'club', 'overall', 'attributes', 'injury', 'preferred_foot', 'last_updated', 'long_term_goals']}

    @app.get('/api/player/training-history')
    def training():
        return read_memory('training_history.json')

    @app.get('/api/player/match-history')
    def matches():
        return read_memory('match_history.json')

    @app.post('/api/messages', response_model=MessageAccepted, status_code=202)
    def message(body: MessageRequest):
        if not body.content.strip():
            raise HTTPException(422, '消息不能为空')
        try:
            message_id, mission_id = service().submit(body)
            return MessageAccepted(message_id=message_id, mission_id=mission_id)
        except KeyError:
            raise HTTPException(404, '任务不存在')
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.get('/api/conversations/{conversation_id}/messages', response_model=list[MessageView])
    def messages(conversation_id: str):
        return service().store.messages(conversation_id)

    @app.get('/api/missions', response_model=list[MissionView])
    def missions():
        return service().store.missions()

    @app.get('/api/missions/{mission_id}', response_model=MissionView)
    def get_mission(mission_id: str):
        return mission(mission_id)

    @app.post('/api/missions/{mission_id}/input', status_code=202)
    def submit_input(mission_id: str, body: InputRequest):
        mission(mission_id)
        try:
            return {'mission_id': service().input(mission_id, body.values)}
        except ValueError as error:
            raise HTTPException(422, str(error))

    @app.get('/api/missions/{mission_id}/report')
    def report(mission_id: str):
        current = mission(mission_id)
        content = service().store.report(mission_id)
        if content is None:
            raise HTTPException(404, '报告尚未生成')
        return {'title': current.title, 'markdown': content}

    @app.get('/api/missions/{mission_id}/report/download')
    def download(mission_id: str):
        document = report(mission_id)
        filename = quote(document['title'][:48], safe='') + '.md'
        return Response(document['markdown'], media_type='text/markdown; charset=utf-8',
            headers={'Content-Disposition': f"attachment; filename=report.md; filename*=UTF-8''{filename}"})

    @app.get('/api/missions/{mission_id}/events')
    async def events(mission_id: str, request: Request, after: int = 0):
        mission(mission_id)
        try:
            cursor = max(0, after, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            raise HTTPException(400, '无效事件序号')
        async def stream():
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                batch = service().store.events(mission_id, cursor)
                for event in batch:
                    cursor = event['sequence']
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                # Keep the connection for completed-mission follow-up messages.
                heartbeat += 1
                if heartbeat % 30 == 0:
                    yield ': keep-alive\n\n'
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    return app

app = create_app()
