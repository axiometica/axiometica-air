"""
PostgreSQL LISTEN/NOTIFY based event bus.
Enables async event subscriptions without additional services.
"""

import asyncio
import json
from typing import Callable, Awaitable, Optional, Dict, List
from uuid import UUID
import asyncpg
from datetime import datetime

from agentic_os.core.models import EventEnvelope, EventType, WorkflowType


class PostgresEventBus:
    """Async event bus using PostgreSQL LISTEN/NOTIFY"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.connection: Optional[asyncpg.Connection] = None
        self.subscriptions: Dict[str, List[Callable[[EventEnvelope], Awaitable[None]]]] = {}
        self.event_history: List[EventEnvelope] = []

    async def connect(self):
        """Connect to PostgreSQL and set up event listener"""
        self.connection = await asyncpg.connect(self.dsn)
        print("✓ PostgreSQL event bus connected")

    async def disconnect(self):
        """Disconnect from PostgreSQL"""
        if self.connection:
            await self.connection.close()
            print("✓ PostgreSQL event bus disconnected")

    def subscribe(self, event_type: str, handler: Callable[[EventEnvelope], Awaitable[None]]):
        """
        Subscribe to event type.
        Handler is called asynchronously when event is published.

        Args:
            event_type: Event type pattern (e.g., "incident.created", "change.*")
            handler: Async callback function
        """
        if event_type not in self.subscriptions:
            self.subscriptions[event_type] = []

        self.subscriptions[event_type].append(handler)
        print(f"✓ Subscribed to {event_type}")

    async def publish(self, event: EventEnvelope) -> None:
        """
        Publish event to PostgreSQL NOTIFY channel.
        Persists to database and notifies all subscribers.

        Args:
            event: EventEnvelope to publish
        """
        # Lazy connect if not connected
        if not self.connection:
            try:
                await self.connect()
            except Exception as e:
                logger.warning(f"Could not connect to event bus: {e}, skipping event publication")
                return

        # Persist event to database
        event_dict = event.to_dict()

        query = """
        INSERT INTO events (
            event_id, workflow_id, workflow_type, event_type,
            source_agent, payload, correlation_id, causation_id, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9
        )
        """

        try:
            await self.connection.execute(
                query,
                event.event_id,
                event.workflow_id,
                event.workflow_type.value,
                event.event_type.value,
                event.source_agent,
                json.dumps(event.payload),
                event.correlation_id,
                event.causation_id,
                event.timestamp,
            )
        except Exception as e:
            print(f"✗ Error persisting event: {e}")
            raise

        # Notify subscribers
        await self._notify_subscribers(event)

        # Keep event history for testing
        self.event_history.append(event)

    async def _notify_subscribers(self, event: EventEnvelope):
        """
        Notify all subscribers for this event type.
        Supports wildcard matching (e.g., "incident.*")
        """
        matching_handlers = []

        # Find exact match
        event_type_str = event.event_type.value
        if event_type_str in self.subscriptions:
            matching_handlers.extend(self.subscriptions[event_type_str])

        # Find wildcard matches
        for pattern, handlers in self.subscriptions.items():
            if pattern.endswith(".*"):
                prefix = pattern[:-2]  # Remove ".*"
                if event_type_str.startswith(prefix):
                    matching_handlers.extend(handlers)

        # Call all matching handlers
        if matching_handlers:
            tasks = [handler(event) for handler in matching_handlers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log any handler errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"✗ Handler error: {result}")

    async def wait_for_event(
        self,
        event_type: str,
        predicate: Optional[Callable[[EventEnvelope], bool]] = None,
        timeout_seconds: Optional[int] = None
    ) -> Optional[EventEnvelope]:
        """
        Wait for a specific event (blocking).
        Used for long-running workflows (e.g., waiting for CAB approval).

        Args:
            event_type: Event type to wait for
            predicate: Optional filter function
            timeout_seconds: Timeout in seconds

        Returns:
            EventEnvelope when event is received, None on timeout
        """
        received_event = None

        async def capture_event(event: EventEnvelope):
            nonlocal received_event
            if predicate is None or predicate(event):
                received_event = event

        # Subscribe temporarily
        self.subscribe(event_type, capture_event)

        # Wait with timeout
        try:
            if timeout_seconds:
                await asyncio.wait_for(
                    self._wait_until(lambda: received_event is not None),
                    timeout=timeout_seconds
                )
            else:
                await self._wait_until(lambda: received_event is not None)
        except asyncio.TimeoutError:
            print(f"✗ Timeout waiting for {event_type}")
            return None

        return received_event

    async def _wait_until(self, condition: Callable[[], bool], poll_interval: float = 0.1):
        """Poll until condition is true"""
        while not condition():
            await asyncio.sleep(poll_interval)

    def get_history(self, workflow_id: Optional[UUID] = None) -> List[EventEnvelope]:
        """
        Get event history (for testing).

        Args:
            workflow_id: Optional filter by workflow ID

        Returns:
            List of events
        """
        if workflow_id is None:
            return self.event_history

        return [e for e in self.event_history if e.workflow_id == workflow_id]

    async def get_events_from_db(self, workflow_id: UUID) -> List[EventEnvelope]:
        """
        Get events from database for a workflow.
        Used for replay/debugging.

        Args:
            workflow_id: Workflow ID

        Returns:
            List of events from database
        """
        if not self.connection:
            raise RuntimeError("Event bus not connected")

        query = """
        SELECT event_id, workflow_id, workflow_type, event_type, source_agent,
               payload, correlation_id, causation_id, created_at
        FROM events
        WHERE workflow_id = $1
        ORDER BY created_at
        """

        rows = await self.connection.fetch(query, workflow_id)

        events = []
        for row in rows:
            event = EventEnvelope(
                event_id=row['event_id'],
                workflow_id=row['workflow_id'],
                workflow_type=WorkflowType(row['workflow_type']),
                event_type=EventType(row['event_type']),
                source_agent=row['source_agent'],
                timestamp=row['created_at'],
                correlation_id=row['correlation_id'],
                causation_id=row['causation_id'],
                payload=row['payload'],
            )
            events.append(event)

        return events
