import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections.abc import Iterator

import requests
from services.sandbox import SandboxService

logger = logging.getLogger(__name__)

class AgentService:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.1")
        self.history: List[Dict[str, Any]] = []
        
        # Define tools
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "start_run",
                    "description": "Start a CloudHand operation (scan, plan, or apply) in a sandbox.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_name": {"type": "string", "description": "Name of the project/workspace (e.g. repo-name-timestamp). Required for new deployments."},
                            "repo_url": {"type": "string", "description": "URL of the git repository"},
                            "operation": {"type": "string", "enum": ["scan", "plan", "apply"]},
                            "plan_description": {"type": "string", "description": "Description of changes for the plan"},
                            "branch": {"type": "string", "default": "main"}
                        },
                        "required": ["repo_url", "operation"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_run_status",
                    "description": "Get the status and artifacts of a run.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "run_id": {"type": "string"},
                            "repo_name": {"type": "string"}
                        },
                        "required": ["run_id", "repo_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_shell",
                    "description": "Run a shell command in the sandbox for debugging.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sandbox_id": {"type": "string"},
                            "command": {"type": "string"}
                        },
                        "required": ["sandbox_id", "command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "output_to_user",
                    "description": "Send a message to the user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "Markdown formatted message"},
                            "kind": {"type": "string", "enum": ["update", "final", "error"]}
                        },
                        "required": ["message"]
                    }
                }
            }
        ]

    def _run_async(self, coro):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_running():
            # We are in a running loop (e.g. uvicorn), but chat_stream is sync.
            # This is tricky. Ideally chat_stream should be async.
            # For now, we'll use a separate thread or just rely on the fact that we are in a thread pool?
            # Actually, since we are in a sync function called by FastAPI's threadpool, we can use asyncio.run() 
            # or a new loop if there isn't one in this thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return loop.run_until_complete(coro)

    def _log_debug(self, msg):
        with open("/tmp/agent_debug.log", "a") as f:
            f.write(f"{datetime.utcnow()} - {msg}\n")

    def _load_history(self, session_id: str):
        if not session_id: return
        self._log_debug(f"Loading history for session {session_id}")
        try:
            from sqlalchemy import select, asc
            from database.connection import SessionLocal
            from database.models import AgentMessage
            from uuid import UUID
            
            # Ensure session_id is UUID
            if isinstance(session_id, str):
                session_uuid = UUID(session_id)
            else:
                session_uuid = session_id

            with SessionLocal() as db:
                result = db.execute(
                    select(AgentMessage)
                    .where(AgentMessage.session_id == session_uuid)
                    .order_by(asc(AgentMessage.timestamp))
                )
                msgs = result.scalars().all()
                history = []
                for m in msgs:
                    # Skip resurrecting tool-call-only messages; they are per-request state
                    if m.role == "assistant" and (m.metadata_ or {}).get("tool_calls") and not m.content:
                        continue

                    msg = {
                        "role": m.role,
                        "content": m.content or ""  # never None for the OpenAI API
                    }
                    history.append(msg)
            
            if history:
                self._log_debug(f"Loaded {len(history)} messages")
                self.history = history
            else:
                self._log_debug("No history found")
            
        except Exception as e:
            self._log_debug(f"Failed to load history: {e}")
            logger.error(f"Failed to load history: {e}")

    def _save_message(self, session_id: str, role: str, content: str, tool_calls: Optional[List] = None):
        if not session_id: return
        content_str = content or ""
        self._log_debug(f"Saving message role={role} len={len(content_str)}")
        try:
            from database.connection import SessionLocal
            from database.models import AgentMessage
            from uuid import UUID
            
            # Ensure session_id is UUID
            if isinstance(session_id, str):
                session_uuid = UUID(session_id)
            else:
                session_uuid = session_id

            with SessionLocal() as db:
                msg = AgentMessage(
                    session_id=session_uuid, 
                    role=role, 
                    content=content_str,
                    metadata_=None  # Tool calls are ephemeral; avoid persisting malformed structures
                )
                db.add(msg)
                db.commit()
            
            self._log_debug("Message saved")
                
        except Exception as e:
            self._log_debug(f"Failed to save message: {e}")
            logger.error(f"Failed to save message: {e}")

    def _get_context_messages(self, session_id: Optional[str]) -> List[Dict[str, str]]:
        if not session_id:
            return []
            
        try:
            from sqlalchemy import select
            from database.connection import SessionLocal
            from database.models import AgentSession, Application
            from uuid import UUID
            
            # Ensure session_id is UUID
            if isinstance(session_id, str):
                session_uuid = UUID(session_id)
            else:
                session_uuid = session_id

            with SessionLocal() as db:
                result = db.execute(
                    select(AgentSession).where(AgentSession.id == session_uuid)
                )
                session = result.scalars().first()
                if session and session.application_id:
                    result = db.execute(
                        select(Application).where(Application.id == session.application_id)
                    )
                    app = result.scalars().first()
                    if app and app.repository:
                        return [{"role": "system", "content": f"Context: The user is asking about application '{app.name}' (Repo: {app.repository.get('clone_url') or app.repository.get('html_url')})."}]
            
            return []
                
        except Exception as e:
            logger.error(f"Failed to fetch session context: {e}")
            self._log_debug(f"Failed to fetch session context: {e}")
            
        return []

    def chat_stream(self, user_message: str, session_id: Optional[str] = None, github_token: Optional[str] = None):
        # Load history first
        if session_id:
            self._load_history(session_id)
            
        self.history.append({"role": "user", "content": user_message})
        if session_id:
            self._save_message(session_id, "user", user_message)
        
        # Main loop
        while True:
            try:
                # Use streaming API call
                response = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": """You are CloudHand, an automated DevOps agent. Your goal is to deploy applications to Hetzner Cloud by scanning repositories and generating Terraform plans.

WORKFLOW RULES:
1. If the user provides a repository URL (or one exists in context), immediately call `start_run` with operation="scan".
   - Do NOT ask discovery questions about tech stack, ports, DB, etc.; the scan finds this.
   - Do NOT ask for the repo URL if it was already provided.
   - Generate a unique project_name for new deployments (e.g., repo-name-{timestamp}); reuse an existing name from history when updating.
2. If the user does NOT provide a repository URL, ask only for it.
3. After the scan finishes:
   - Call `output_to_user` with a concise summary that includes the repo name, chosen project_name, and detected stack.
   - Ask if the user wants a deployment plan.
4. If the user wants to proceed/generate a plan:
   - Call `start_run` with operation="plan", using the SAME project_name and repo_url.
   - Provide plan_description that covers region (e.g., hel1), server type (e.g., cx22), SSH key (e.g., \"aldrin\"), and networking/ports.
5. After the plan finishes:
   - Use `output_to_user` to describe the high-level resources/changes and explicitly ask for approval to apply.
6. If the user approves, call `start_run` with operation="apply" using the same project_name and repo_url.
7. Use `output_to_user` for user-facing updates; avoid dumping raw logs unless troubleshooting.
8. Always verify plans before applying."""},
                            *self._get_context_messages(session_id),
                            *self.history
                        ],
                        "tools": self.tools,
                        "tool_choice": "auto",
                        "stream": True
                    },
                    timeout=120,
                    stream=True
                )
                # logger.info(f"Sending request with {len(self.history)} history items")
                if not response.ok:
                    try:
                        err_json = response.json()
                    except Exception:
                        err_json = {"raw": response.text}
                    logger.error("OpenAI error (%s): %s", response.status_code, err_json)
                    response.raise_for_status()
                
                # Process stream
                collected_content = []
                tool_calls = []
                current_tool_call = None
                
                for line in response.iter_lines():
                    if not line: continue
                    line_text = line.decode('utf-8')
                    if line_text.startswith("data: [DONE]"): break
                    if not line_text.startswith("data: "): continue
                    
                    try:
                        chunk = json.loads(line_text[6:])
                        delta = chunk["choices"][0]["delta"]
                        
                        # Stream content directly to user
                        if "content" in delta and delta["content"]:
                            content_chunk = delta["content"]
                            collected_content.append(content_chunk)
                            yield json.dumps({"type": "token", "content": content_chunk}) + "\n"
                            
                        # Collect tool calls
                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                if tc.get("id"):
                                    if current_tool_call: tool_calls.append(current_tool_call)
                                    current_tool_call = {
                                        "id": tc["id"], 
                                        "type": "function",
                                        "function": {"name": tc["function"]["name"], "arguments": ""}
                                    }
                                elif tc.get("function") and tc["function"].get("arguments"):
                                    if current_tool_call:
                                        current_tool_call["function"]["arguments"] += tc["function"]["arguments"]
                                        
                    except json.JSONDecodeError:
                        continue
                        
                if current_tool_call: tool_calls.append(current_tool_call)
                
                # Add assistant message to history
                full_content = "".join(collected_content)
                msg = {"role": "assistant", "content": full_content or ""}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                self.history.append(msg)
                
                if session_id and full_content:
                    self._save_message(session_id, "assistant", full_content, tool_calls)
                
                if not tool_calls:
                    break
                    
                # Execute tools
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    args_str = tool_call["function"]["arguments"]
                    try:
                        args = json.loads(args_str)
                    except:
                        args = {}
                        
                    # Special handling for start_run to stream output
                    if func_name == "start_run":
                        project_msg = f" (Project: {args.get('project_name', 'default')})"
                        yield json.dumps({"type": "status", "content": f"Starting {args.get('operation')}...{project_msg}"}) + "\n"
                        
                        # Execute tool which now returns a generator
                        # We need to handle both generator (streaming) and dict (legacy/error) returns
                        result_or_gen = self._execute_tool(func_name, args, github_token=github_token)
                        
                        import sys
                        sys.stderr.write(f"DEBUG: Tool execution result type: {type(result_or_gen)}\n")
                        logger.info("start_run returned %r (iter=%s)", type(result_or_gen), isinstance(result_or_gen, Iterator))
                        
                        final_result = {}
                        # Tightened generator detection: only true iterators, not strings/dicts
                        is_stream = isinstance(result_or_gen, Iterator) and not isinstance(result_or_gen, (str, bytes, dict))
                        
                        if is_stream:
                            # It's a streaming iterator/generator – forward lines as they arrive
                            output_accumulator = []
                            sys.stderr.write("DEBUG: Starting to consume tool generator...\n")
                            yield json.dumps({"type": "status", "content": "DEBUG: Stream connected"}) + "\n"
                            
                            count = 0
                            for line in result_or_gen:
                                count += 1
                                msg = f"DEBUG: Generator yielded #{count}: {repr(line)[:100]}"
                                sys.stderr.write(msg + "\n")
                                print(msg, flush=True)
                                if isinstance(line, dict):
                                    # Final result dict yielded at end
                                    final_result = line
                                elif isinstance(line, str):
                                    output_accumulator.append(line)
                                    yield json.dumps({"type": "sandbox_log", "content": line}) + "\n"
                            
                            # Construct a result dict from accumulated output if not provided
                            if not final_result:
                                final_result = {"output": "\n".join(output_accumulator), "status": "completed"}
                            
                            result = final_result
                        else:
                            # Non-streaming result (dict/error/etc.)
                            result = result_or_gen
                            if isinstance(result, dict) and "output" in result:
                                for line in result["output"].splitlines():
                                    yield json.dumps({"type": "sandbox_log", "content": line}) + "\n"
                    else:
                        result = self._execute_tool(func_name, args, github_token=github_token)
                    
                    # Truncate output for LLM context window to avoid 400 errors
                    llm_result = result.copy() if isinstance(result, dict) else result
                    if isinstance(llm_result, dict) and "output" in llm_result and len(llm_result["output"]) > 5000:
                        # Keep the last 5000 characters as they likely contain the most relevant status/error info
                        llm_result["output"] = "... (output truncated) ...\n" + llm_result["output"][-5000:]

                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(llm_result)
                    })
                    
                    if func_name == "output_to_user":
                        msg_text = args.get("message", "")
                        if msg_text:
                            yield json.dumps({"type": "message", "content": msg_text}) + "\n"
                            assistant_msg = {"role": "assistant", "content": msg_text}
                            self.history.append(assistant_msg)
                            if session_id:
                                self._save_message(session_id, "assistant", msg_text)
                        return

            except Exception as e:
                logger.error(f"Agent loop error: {e}")
                yield json.dumps({"type": "error", "content": str(e)}) + "\n"
                return

    def _execute_tool(self, name: str, args: Dict[str, Any], github_token: Optional[str] = None) -> Any:
        logger.info(f"Executing tool: {name} with args: {args}")
        
        if name == "start_run":
            # Use provided token or fallback to env
            token = github_token or os.getenv("GITHUB_TOKEN", "")
            
            result = SandboxService.start_run(
                repo_url=args["repo_url"],
                operation=args["operation"],
                github_token=token,
                provider_config={"token": os.getenv("HCLOUD_TOKEN", "")},
                branch_name=args.get("branch", "main"),
                plan_description=args.get("plan_description", ""),
                project_id=args.get("project_name")
            )
            
            # Only log when it's the old non-streaming dict API
            # CRITICAL: Don't touch generators! The "output" in result check would drain it!
            if isinstance(result, dict) and "output" in result:
                logger.info("Sandbox output:\n%s...", result["output"][:500])
            
            # If it's a generator/iterator, leave it alone – chat_stream will consume it
            return result
        elif name == "get_run_status":
            return SandboxService.get_run_status(
                run_id=args["run_id"],
                repo_name=args["repo_name"]
            )
        elif name == "sandbox_shell":
            return SandboxService.sandbox_shell(
                sandbox_id=args["sandbox_id"],
                command=args["command"]
            )
        elif name == "output_to_user":
            return {"status": "sent"}
            
        return {"error": f"Unknown tool: {name}"}
