import os, uuid, json
from fastapi import FastAPI, HTTPException, Header, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from dotenv import load_dotenv
import redis, rq, jwt, time
from typing import List
load_dotenv()

API_KEY = os.getenv('API_KEY')
JWT_SECRET = os.getenv('JWT_SECRET')
REDIS_URL = os.getenv('REDIS_URL','redis://redis:6379/0')

r = redis.from_url(REDIS_URL, decode_responses=True)
q = rq.Queue('sentinel-queue', connection=r)

app = FastAPI(title='Operation Sentinel v3')

# simple in-memory websocket manager (for demo)
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)
    def disconnect(self, websocket: WebSocket):
        self.active.remove(websocket)
    async def broadcast(self, msg: dict):
        living = []
        for ws in list(self.active):
            try:
                await ws.send_text(json.dumps(msg))
                living.append(ws)
            except Exception:
                pass
        self.active = living

manager = ConnectionManager()

class GenerateRequest(BaseModel):
    idea: str
    name: str
    template: str = 'python-cli'
    options: dict = {}

def verify_auth(x_api_key: str | None, token: str | None):
    if API_KEY and x_api_key == API_KEY:
        return True
    if token and JWT_SECRET:
        try:
            jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            return True
        except Exception:
            raise HTTPException(status_code=401, detail='Invalid JWT')
    raise HTTPException(status_code=401, detail='Missing auth')

@app.post('/generate')
async def generate(req: GenerateRequest, x_api_key: str | None = Header(None), authorization: str | None = Header(None)):
    token = None
    if authorization and authorization.startswith('Bearer '):
        token = authorization.split(' ',1)[1]
    verify_auth(x_api_key, token)
    job_id = str(uuid.uuid4())
    payload = req.dict()
    payload['_job_id'] = job_id
    # enqueue worker; worker will push updates to Redis pubsub or we use this naive approach
    q.enqueue('worker.worker.process_job', payload, job_id=job_id, timeout=1800)
    # notify UI subscribers
    await manager.broadcast({'event':'job_queued','job_id':job_id,'name':payload['name']})
    return {'status':'queued','job_id':job_id}

@app.get('/status/{job_id}')
async def status(job_id: str, x_api_key: str | None = Header(None), authorization: str | None = Header(None)):
    token = None
    if authorization and authorization.startswith('Bearer '):
        token = authorization.split(' ',1)[1]
    verify_auth(x_api_key, token)
    try:
        job = rq.job.Job.fetch(job_id, connection=r, default=None)
    except Exception:
        job = None
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    return {'id': job.get_id(), 'status': job.get_status(), 'result': job.result}

@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # accept ping messages from client
            await websocket.send_text(json.dumps({'event':'pong'}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
