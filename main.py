import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents

app = FastAPI(title="Role-based Project Management API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Schemas (request/response) -----
class AssignPartRequest(BaseModel):
    part_id: str
    user_id: str

class CreateRoleRequest(BaseModel):
    name: str
    permissions: List[str] = []
    max_capacity: Optional[int] = None

class CreateUserRequest(BaseModel):
    name: str
    email: str
    role: str
    capacity: Optional[int] = None
    locale: str = 'en'
    theme: str = 'system'

class CreateProjectRequest(BaseModel):
    title: str
    description: Optional[str] = None
    creator_id: str
    deadline: Optional[datetime] = None
    tags: List[str] = []
    priority: str = 'medium'
    metadata: Dict[str, Any] = {}

class CreatePartRequest(BaseModel):
    project_id: str
    title: str
    assigned_user_id: Optional[str] = None
    deadline: Optional[datetime] = None

class NotificationRequest(BaseModel):
    user_id: str
    type: str
    title: str
    body: str

# ----- Helpers -----

def collection(name: str):
    return db[name]


def current_active_parts_count(user_id: str) -> int:
    return collection('part').count_documents({
        'assigned_user_id': user_id,
        'status': {'$in': ['assigned', 'in_progress', 'review']}
    })


def get_user_capacity(user_id: str) -> int:
    from bson import ObjectId
    u = collection('user').find_one({'_id': ObjectId(user_id)})
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    cap = u.get('capacity')
    if cap is None:
        role = collection('role').find_one({'name': u.get('role')})
        if role and role.get('max_capacity') is not None:
            return int(role['max_capacity'])
        return 0
    return int(cap)


def recompute_project_progress(project_id: str):
    from bson import ObjectId
    parts = list(collection('part').find({'project_id': project_id}))
    if not parts:
        progress = 0.0
    else:
        done = sum(1 for p in parts if p.get('status') == 'completed')
        progress = (done / len(parts)) * 100.0
    collection('project').update_one({'_id': ObjectId(project_id)}, {'$set': {'progress': progress, 'updated_at': datetime.now(timezone.utc)}})
    return progress


# ----- Basic endpoints -----
@app.get("/")
def root():
    return {"ok": True, "service": "backend", "message": "Project Management API running"}


@app.get("/schema")
def schema_index():
    return {
        "collections": ["role", "user", "project", "part", "message", "notification", "insight"]
    }


# ----- Role & User -----
@app.post('/roles')
def create_role(payload: CreateRoleRequest):
    rid = create_document('role', payload.dict())
    return {"id": rid}


@app.get('/roles')
def list_roles():
    return get_documents('role', {}, limit=100)


@app.post('/users')
def create_user(payload: CreateUserRequest):
    data = payload.dict()
    if data.get('capacity') is None:
        role = collection('role').find_one({'name': data['role']})
        if role and role.get('max_capacity') is not None:
            data['capacity'] = int(role['max_capacity'])
        else:
            data['capacity'] = 0
    uid = create_document('user', data)
    return {"id": uid}


@app.get('/users')
def list_users():
    return get_documents('user', {}, limit=200)


# ----- Projects & Parts -----
@app.post('/projects')
def create_project(payload: CreateProjectRequest):
    pid = create_document('project', payload.dict())
    return {"id": pid}


@app.get('/projects')
def search_projects(tag: Optional[str] = None, owner: Optional[str] = None, archived: Optional[bool] = None, sort: Optional[str] = None):
    q: Dict[str, Any] = {}
    if tag:
        q['tags'] = tag
    if owner:
        q['creator_id'] = owner
    if archived is not None:
        q['archived'] = archived
    items = list(collection('project').find(q))
    if sort == 'deadline':
        items.sort(key=lambda x: x.get('deadline') or datetime.max)
    if sort == 'progress':
        items.sort(key=lambda x: x.get('progress') or 0, reverse=True)
    return items


@app.post('/parts')
def create_part(payload: CreatePartRequest):
    data = payload.dict()
    assigned = data.get('assigned_user_id')
    if assigned:
        cap = get_user_capacity(assigned)
        active = current_active_parts_count(assigned)
        if active >= cap:
            raise HTTPException(status_code=400, detail='User capacity exceeded')
    part_id = create_document('part', {
        **data,
        'status': 'assigned' if assigned else 'assigned'
    })
    # update project progress baseline
    recompute_project_progress(data['project_id'])
    return {"id": part_id}


@app.get('/parts')
def list_parts(project_id: Optional[str] = None, user_id: Optional[str] = None, status: Optional[str] = None):
    q: Dict[str, Any] = {}
    if project_id:
        q['project_id'] = project_id
    if user_id:
        q['assigned_user_id'] = user_id
    if status:
        q['status'] = status
    return get_documents('part', q, limit=300)


@app.post('/parts/assign')
def assign_part(payload: AssignPartRequest):
    from bson import ObjectId
    cap = get_user_capacity(payload.user_id)
    active = current_active_parts_count(payload.user_id)
    if active >= cap:
        raise HTTPException(status_code=400, detail='User capacity exceeded')
    res = collection('part').update_one(
        {'_id': ObjectId(payload.part_id)},
        {'$set': {'assigned_user_id': payload.user_id, 'status': 'assigned', 'updated_at': datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail='Part not found')
    part = collection('part').find_one({'_id': ObjectId(payload.part_id)})
    if part:
        recompute_project_progress(part['project_id'])
    create_document('notification', {
        'user_id': payload.user_id,
        'type': 'assignment',
        'title': 'New assignment',
        'body': f'You were assigned to part {payload.part_id}'
    })
    return {"ok": True}


@app.post('/parts/{part_id}/status')
def update_part_status(part_id: str, status: str):
    from bson import ObjectId
    allowed = ['assigned','in_progress','review','completed','blocked']
    if status not in allowed:
        raise HTTPException(status_code=400, detail='Invalid status')
    res = collection('part').update_one({'_id': ObjectId(part_id)}, {'$set': {'status': status, 'updated_at': datetime.now(timezone.utc)}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail='Part not found')
    part = collection('part').find_one({'_id': ObjectId(part_id)})
    if part:
        recompute_project_progress(part['project_id'])
    return {"ok": True}


@app.get('/users/{user_id}/workload')
def user_workload(user_id: str):
    cap = get_user_capacity(user_id)
    active = current_active_parts_count(user_id)
    return {"capacity": cap, "active": active, "available": max(cap - active, 0)}


# ----- Notifications -----
@app.post('/notifications')
def create_notification(payload: NotificationRequest):
    nid = create_document('notification', payload.dict())
    return {"id": nid}


@app.get('/notifications/{user_id}')
def list_notifications(user_id: str):
    items = get_documents('notification', {'user_id': user_id}, limit=100)
    # Sort latest first by created_at if present
    items.sort(key=lambda x: x.get('created_at') or datetime.min, reverse=True)
    return items


# ----- Insights (rule-based) -----
@app.get('/insights/system')
def system_insights():
    users = list(collection('user').find({}))
    parts = list(collection('part').find({'status': {'$in': ['assigned','in_progress','review']}}))
    by_user: Dict[str, int] = {}
    for p in parts:
        uid = p.get('assigned_user_id')
        if uid:
            by_user[uid] = by_user.get(uid, 0) + 1
    risks = []
    approaching = []
    now = datetime.now(timezone.utc)
    for p in parts:
        dl = p.get('deadline')
        if dl:
            # if within next 48 hours
            if (dl - now).total_seconds() < 172800 and p.get('status') != 'completed':
                approaching.append(str(p.get('_id')))
    overloaded = []
    from bson import ObjectId
    for u in users:
        uid = str(u['_id'])
        cap = u.get('capacity') or 0
        act = by_user.get(uid, 0)
        if act > cap:
            overloaded.append({'user_id': uid, 'active': act, 'capacity': cap})
    summary = f"Active parts: {len(parts)}. Approaching deadlines: {len(approaching)}. Overloaded users: {len(overloaded)}."
    return {"summary": summary, "overloaded": overloaded, "approaching": approaching}


@app.get('/insights/user/{user_id}')
def user_insights(user_id: str):
    cap = get_user_capacity(user_id)
    act = current_active_parts_count(user_id)
    status_breakdown = {}
    for s in ['assigned','in_progress','review','completed','blocked']:
        status_breakdown[s] = collection('part').count_documents({'assigned_user_id': user_id, 'status': s})
    trend = "balanced" if act <= cap else "overloaded"
    return {"capacity": cap, "active": act, "trend": trend, "status": status_breakdown}


# Health
@app.get('/test')
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
