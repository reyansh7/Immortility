# 12 — Proactivity

## What Proactivity Means

Proactivity is the capability to notice, decide, and act (or notify) without explicit user request.

The difference from a passive assistant:
- **Passive:** User asks "did anything break?" → Immortality checks and reports
- **Proactive:** Immortality notices a test failure in the active project and surfaces it unprompted

Proactivity is a Phase 7 capability. The infrastructure for it (event bus, background execution) exists but no event watchers have been built.

---

## Design Principles

1. **Proactive actions go through the same permission layer as user-requested actions.** Immortality cannot do something proactively that it cannot do when asked.

2. **Proactive actions are bounded by the current permission mode.** In SAFE mode, Immortality notifies but does not act. In AUTONOMOUS mode, it can act and then report.

3. **Proactive actions are observable.** Every proactive action is logged and visible in the HUD task timeline.

4. **The user can disable any watcher.** No watcher should run silently without the user's knowledge.

5. **Proactive actions prefer minimal intervention.** Surface information first, act only when the action is clearly safe and reversible.

---

## Event Architecture

### Design

A simple, in-process event monitoring loop using asyncio. No Redis, no Kafka, no external message broker needed for a personal AI.

```
EventMonitor (background asyncio task)
    │
    ├── Watcher 1: GitEventWatcher
    │   → polls git fetch + status every N minutes
    │   → fires GitPushEvent, BuildFailureEvent
    │
    ├── Watcher 2: ScheduleWatcher
    │   → checks scheduled triggers
    │   → fires ScheduledTaskEvent
    │
    ├── Watcher 3: FileSystemWatcher
    │   → uses watchdog (already a dependency)
    │   → fires FileChangedEvent
    │
    └── Watcher 4: BuildWatcher (future)
        → monitors CI output files
        → fires BuildFailureEvent
    │
    ▼
Event Queue (asyncio.Queue)
    │
    ▼
EventProcessor
    → classify event
    → decide if action warranted
    → create Task (if permission mode allows)
    OR notify user via HUD + voice
```

### EventWatcher Protocol

```python
class EventWatcher(Protocol):
    name: str
    poll_interval_seconds: float
    enabled: bool

    async def check(self) -> list[Event]
        """Poll for new events. Returns events since last check."""
    
    async def on_fire(self, event: Event) -> ProactiveAction | None
        """Given an event, decide whether to act and what to do."""
```

### Event Types

```python
@dataclass
class Event:
    type: str           # git.push | git.failure | file.changed | schedule.fire
    source: str         # watcher name
    payload: dict       # event-specific data
    timestamp: datetime
    urgency: str        # low | medium | high

@dataclass
class ProactiveAction:
    description: str    # "Tests are failing in immortality1"
    action: str | None  # "Run tests and investigate" or None (notify only)
    urgency: str
    permission_required: str  # "low" | "medium" | "high"
```

---

## Concrete Watcher Implementations

### GitEventWatcher

```python
class GitEventWatcher:
    """
    Polls the active project's git status.
    Detects: new commits on remote, local uncommitted changes,
    CI failures (if a failure file exists).
    """
    poll_interval_seconds = 300  # every 5 minutes
    
    async def check(self) -> list[Event]:
        events = []
        
        # Fetch from remote (read-only, safe)
        status = git_tool.fetch(cwd=active_project.path)
        if status.get("new_commits"):
            events.append(Event(
                type="git.new_commits",
                payload={"count": status["new_commits"], "project": active_project.name}
            ))
        
        # Check for failing tests (if a test report file exists)
        report = project_path / ".pytest_last_result"
        if report.exists() and "FAILED" in report.read_text():
            events.append(Event(
                type="test.failure",
                payload={"project": active_project.name, "report": str(report)}
            ))
        
        return events
```

### ScheduleWatcher

```python
class ScheduleWatcher:
    """
    Time-based triggers. Configured via schedule entries in world model.
    Examples:
      - "daily summary at 9am"
      - "weekly project review on Monday"
      - "remind about X in 30 minutes"
    """
    poll_interval_seconds = 60  # check every minute
    
    async def check(self) -> list[Event]:
        now = datetime.now()
        due_schedules = self._get_due_schedules(now)
        return [Event(type="schedule.fire", payload=s) for s in due_schedules]
```

### FileSystemWatcher

```python
class FileSystemWatcher:
    """
    Uses watchdog (already in requirements.txt) to detect file changes.
    Currently used by project_manager.py for cache invalidation.
    Extend to generate proactive events.
    """
    
    def on_modified(self, event: FileSystemEvent):
        if event.src_path.endswith(".py"):
            self.event_queue.put(Event(
                type="file.changed",
                payload={"path": event.src_path}
            ))
```

---

## Proactive Action Decision Logic

Not every event warrants action. The decision logic:

```python
async def decide_action(event: Event) -> ProactiveAction | None:
    mode = permissions.get_mode()
    
    if event.type == "test.failure":
        if mode in (MODE_AUTONOMOUS, MODE_DEVELOPER):
            return ProactiveAction(
                description=f"Tests failing in {event.payload['project']}",
                action="Investigate and fix safe issues",
                urgency="medium",
                permission_required="medium"
            )
        else:
            # Notify only in ASSISTED mode
            return ProactiveAction(
                description=f"Tests failing in {event.payload['project']}",
                action=None,  # Notify, don't act
                urgency="medium",
                permission_required="low"
            )
    
    if event.type == "git.new_commits":
        return ProactiveAction(
            description=f"New commits available in {event.payload['project']}",
            action=None,  # Always notify-only for new commits
            urgency="low",
            permission_required="low"
        )
    
    if event.type == "schedule.fire":
        # Scheduled tasks follow their pre-approved configuration
        return ProactiveAction(
            description=event.payload["description"],
            action=event.payload["action"],
            urgency="medium",
            permission_required=event.payload.get("permission_required", "medium")
        )
    
    return None
```

---

## Notification vs Action

For each proactive event, Immortality can:

1. **Notify only:** Show in HUD + speak a brief alert. No autonomous action taken.
   - User must explicitly confirm to proceed
   - Always available regardless of permission mode

2. **Notify + propose:** Show the notification + offer to take an action.
   - "Tests are failing. Want me to investigate?"
   - User responds yes/no via voice or HUD button

3. **Notify + act:** In AUTONOMOUS mode, take the action and then report.
   - Only for pre-approved action types
   - Must be reversible or low-risk
   - Always logged to task timeline

---

## Integration with EventBus

The existing `core/event_bus.py` is an in-process pub/sub:

```python
# Existing usage:
EventBus().publish("kernel.progress", {"message": ..., "percent": ...})
EventBus().publish("coding.role", {"role": ..., "detail": ...})

# New proactive events:
EventBus().subscribe("proactive.notification", hud_notify_handler)
EventBus().subscribe("proactive.task", task_manager_handler)
```

The EventMonitor publishes events to the EventBus. The HUD subscribes for notifications. The task manager subscribes for autonomous actions.

---

## Examples of Proactive Behavior

### "I noticed your tests are failing"
```
Trigger: GitEventWatcher detects FAILED in .pytest_last_result
    → Mode: AUTONOMOUS
    → Action: Create task "Investigate failing tests in immortality1"
    → Execute: git_status + read test output + investigate top failure
    → Report: "3 tests failing due to import error in core/config.py. 
               The HERMES_API_KEY key was added to config but not to test fixtures.
               I've opened the relevant files in the HUD for your review."
```

### "Your daily summary"
```
Trigger: ScheduleWatcher fires at 9:00 AM
    → Mode: any
    → Action: None (notify-only)
    → Report: "Yesterday: 3 tasks completed in immortality1. 
               New commits from yesterday: 2 (memory improvements).
               Today: no tasks scheduled. Active project: immortality1."
```

### "New commits available"
```
Trigger: GitEventWatcher detects upstream has 3 new commits
    → Mode: any
    → Action: None (notify-only)
    → Notification: "3 new commits on origin/main in immortality1."
```

---

## Infrastructure Choice Justification

### Why not Redis/Celery/Temporal?

This is a personal AI running on one machine. The event rates are:
- Git polls: 1 event per 5 minutes
- Schedule checks: 1 per minute
- File changes: tens per hour on active projects

asyncio Queue + background task handles this trivially without any external infrastructure.

Redis/Celery/Temporal are appropriate for distributed systems handling thousands of events per second. Adding them here would:
- Require an always-running Redis server
- Add ~50MB memory overhead
- Add operational complexity (what if Redis crashes?)
- Solve a problem that doesn't exist

The `schedule` Python library (`pip install schedule`) handles cron-like triggers. The `watchdog` library (already in requirements) handles filesystem events. asyncio handles the event loop.

This is the right tool for the right scale.
