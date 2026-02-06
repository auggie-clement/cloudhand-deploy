from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class UserBase(BaseModel):
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None

class UserCreate(UserBase):
    github_id: str
    access_token: Optional[str] = None

class User(UserBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True

class RepositoryBase(BaseModel):
    github_id: str
    name: str
    full_name: str
    html_url: str
    language: Optional[str] = None
    default_branch: Optional[str] = None

class Repository(RepositoryBase):
    id: UUID
    user_id: UUID

    class Config:
        from_attributes = True

class ApplicationBase(BaseModel):
    name: str
    config: Optional[Dict[str, Any]] = None

class ApplicationCreate(ApplicationBase):
    repository_id: UUID

class DeploymentBase(BaseModel):
    commit_hash: Optional[str] = None
    status: str
    logs: Optional[str] = None

class Deployment(DeploymentBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True

class OperationBase(BaseModel):
    type: str
    status: str
    trigger: Optional[str] = None
    phases: Optional[List[Dict[str, Any]]] = None
    changeset: Optional[Dict[str, Any]] = None
    sandbox_id: Optional[str] = None

class OperationCreate(OperationBase):
    application_id: UUID
    session_id: Optional[UUID] = None

class Operation(OperationBase):
    id: UUID
    application_id: UUID
    started_at: datetime
    completed_at: Optional[datetime] = None
    session_id: Optional[UUID] = None

    class Config:
        from_attributes = True

class AgentMessageBase(BaseModel):
    role: str
    content: str
    type: Optional[str] = "text"
    metadata: Optional[Dict[str, Any]] = None

class AgentMessageCreate(AgentMessageBase):
    session_id: UUID

class AgentMessage(AgentMessageBase):
    id: UUID
    session_id: UUID
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = Field(default=None, validation_alias='metadata_')

    class Config:
        from_attributes = True
        populate_by_name = True

class AgentSessionBase(BaseModel):
    title: str
    status: Optional[str] = "active"

class AgentSessionCreate(AgentSessionBase):
    application_id: Optional[UUID] = None
    primary_run_id: Optional[UUID] = None
    created_from_session_id: Optional[UUID] = None

class AgentSession(AgentSessionBase):
    id: UUID
    application_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    last_activity: datetime
    primary_run_id: Optional[UUID] = None
    created_from_session_id: Optional[UUID] = None
    messages: List[AgentMessage] = []

    class Config:
        from_attributes = True

class Application(ApplicationBase):
    id: UUID
    user_id: UUID
    repository_id: UUID
    status: str
    repository: Repository
    deployments: List[Deployment] = []
    environments: Optional[Dict[str, Any]] = None
    current_state: Optional[Dict[str, Any]] = None
    agent_memory_summary_id: Optional[UUID] = None
    operations: List[Operation] = []
    sessions: List[AgentSession] = []

    class Config:
        from_attributes = True

class ProjectBase(BaseModel):
    github_owner: str
    github_repo: str
    default_branch: str
    github_installation_id: Optional[str] = None
    infra_branch: Optional[str] = "cloudhand/infra"

class ProjectCreate(ProjectBase):
    pass

class ProjectRead(ProjectBase):
    id: UUID
    user_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True
