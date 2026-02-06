from sqlalchemy import Column, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from .connection import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    github_id = Column(String, unique=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String)
    avatar_url = Column(String)
    access_token = Column(String) # Encrypted in production, plain for now
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    repositories = relationship("Repository", back_populates="user")
    applications = relationship("Application", back_populates="user")

class Repository(Base):
    __tablename__ = "repositories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    github_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    html_url = Column(String, nullable=False)
    language = Column(String)
    default_branch = Column(String)
    
    user = relationship("User", back_populates="repositories")
    applications = relationship("Application", back_populates="repository")

class Application(Base):
    __tablename__ = "applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    repository_id = Column(UUID(as_uuid=True), ForeignKey("repositories.id"))
    name = Column(String, nullable=False)
    status = Column(String, default="pending") # pending, deploying, running, error
    config = Column(JSON)
    environments = Column(JSON)
    current_state = Column(JSON)
    agent_memory_summary_id = Column(UUID(as_uuid=True), nullable=True)
    
    user = relationship("User", back_populates="applications")
    repository = relationship("Repository", back_populates="applications")
    deployments = relationship("Deployment", back_populates="application")
    operations = relationship("Operation", back_populates="application")
    sessions = relationship("AgentSession", back_populates="application")

class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"))
    commit_hash = Column(String)
    status = Column(String)
    logs = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    application = relationship("Application", back_populates="deployments")

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    github_owner = Column(String, nullable=False)
    github_repo = Column(String, nullable=False)
    default_branch = Column(String, nullable=False)
    github_installation_id = Column(String)
    infra_branch = Column(String, default="cloudhand/infra")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="projects")
    tasks = relationship("Task", back_populates="project")
    pull_requests = relationship("PullRequest", back_populates="project")

class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    title = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, default="pending") # pending, running, completed, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    trigger = Column(String)

    project = relationship("Project", back_populates="tasks")
    runs = relationship("Run", back_populates="task")

class Run(Base):
    __tablename__ = "runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id"))
    sandbox_id = Column(String)
    status = Column(String)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    log_stream_key = Column(String)
    plan_id = Column(String)
    terraform_apply_status = Column(String)

    task = relationship("Task", back_populates="runs")
    artifacts = relationship("Artifact", back_populates="run")

class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id"))
    kind = Column(String) # scan, spec, plan, tf, diagram, log
    path_in_workspace = Column(String)
    storage_bucket = Column(String)
    storage_key = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    run = relationship("Run", back_populates="artifacts")

class PullRequest(Base):
    __tablename__ = "pull_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"))
    github_pr_number = Column(String)
    infra_branch = Column(String)
    base_branch = Column(String)
    status = Column(String) # open, merged, closed
    last_synced_at = Column(DateTime(timezone=True))

    project = relationship("Project", back_populates="pull_requests")

# New Domain Models

class Operation(Base):
    __tablename__ = "operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"))
    type = Column(String, nullable=False) # SETUP, UPDATE_INFRA, MAINTENANCE
    status = Column(String, nullable=False) # pending, planning, awaiting_approval, running, verifying, completed, failed, cancelled
    trigger = Column(String) # user, agent, schedule
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    phases = Column(JSON) # List of phases
    sandbox_id = Column(String)
    changeset = Column(JSON)
    session_id = Column(UUID(as_uuid=True), ForeignKey("agent_sessions.id"), nullable=True)

    application = relationship("Application", back_populates="operations")
    session = relationship("AgentSession", foreign_keys=[session_id], back_populates="operations")

class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    title = Column(String)
    status = Column(String, default="active") # active, idle, planning, executing
    last_activity = Column(DateTime(timezone=True), server_default=func.now())
    primary_run_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    created_from_session_id = Column(UUID(as_uuid=True), ForeignKey("agent_sessions.id"), nullable=True)

    user = relationship("User", back_populates="sessions")
    application = relationship("Application", back_populates="sessions")
    messages = relationship("AgentMessage", back_populates="session", cascade="all, delete-orphan")
    operations = relationship("Operation", foreign_keys=[Operation.session_id], back_populates="session")
    primary_run = relationship("Operation", foreign_keys=[primary_run_id], post_update=True)

class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("agent_sessions.id"))
    role = Column(String, nullable=False) # user, model, system
    content = Column(Text)
    type = Column(String, default="text") # text, plan, success, error, workflow, artifact
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    metadata_ = Column("metadata", JSON) # metadata is a reserved word in SQLAlchemy sometimes, safer to name it differently or quote it. But here we use "metadata" in column name.

    session = relationship("AgentSession", back_populates="messages")

# Update User relationship
User.projects = relationship("Project", back_populates="user")
User.sessions = relationship("AgentSession", back_populates="user")
