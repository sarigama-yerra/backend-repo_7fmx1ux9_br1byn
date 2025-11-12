"""
Database Schemas for Role-based Project Management System

Each Pydantic model corresponds to a MongoDB collection (collection name = class name lowercase).
"""
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

# Core access control
class Role(BaseModel):
    name: Literal['admin','manager','worker','guest'] | str = Field(..., description="Role name")
    permissions: List[Literal['create','edit','assign','view','delete','approve','archive','export','chat']] = Field(default_factory=list)
    max_capacity: Optional[int] = Field(None, ge=0, description="Default capacity for users of this role (parts concurrently)")

class User(BaseModel):
    name: str
    email: str
    role: str = Field(..., description="Role name reference")
    capacity: int = Field(3, ge=0, description="Concurrent active parts limit")
    locale: str = Field('en', description="Preferred language")
    theme: Literal['light','dark','system'] = 'system'
    is_active: bool = True

# Project and parts
class Project(BaseModel):
    title: str
    description: Optional[str] = None
    creator_id: str
    deadline: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    progress: float = Field(0.0, ge=0, le=100)
    priority: Literal['low','medium','high','urgent'] = 'medium'
    archived: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Part(BaseModel):
    project_id: str
    title: str
    assigned_user_id: Optional[str] = None
    deadline: Optional[datetime] = None
    status: Literal['assigned','in_progress','review','completed','blocked'] = 'assigned'
    stage: str = 'assigned'
    checklist: List[Dict[str, Any]] = Field(default_factory=list)
    files: List[Dict[str, Any]] = Field(default_factory=list)
    subtasks: List[Dict[str, Any]] = Field(default_factory=list)
    time_tracking: List[Dict[str, Any]] = Field(default_factory=list)

class Message(BaseModel):
    scope: Literal['project','part']
    scope_id: str
    author_id: str
    content: str

class Notification(BaseModel):
    user_id: str
    type: Literal['assignment','chat','deadline','role_change','progress','system']
    title: str
    body: str
    read: bool = False

# Minimal analytics cache
class Insight(BaseModel):
    scope: Literal['system','project','user']
    scope_id: Optional[str] = None
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)
