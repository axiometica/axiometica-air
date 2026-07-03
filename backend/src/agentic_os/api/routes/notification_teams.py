"""
Notification Teams API.

A standalone registry of named teams, each with whichever notification
channels it has configured (PagerDuty routing key, Slack channel, email
recipients, outbound webhook — any combination, all optional). Looked up by
name via the `team` arg on the notify/alert_escalate/alert_update/send_alert
runbook actions; falls back to the existing global PagerDuty/Slack/SMTP
defaults when no team is given or the named team isn't found/enabled.

  GET    /api/notification-teams        — list (secrets redacted to *_set booleans)
  POST   /api/notification-teams        — create
  PUT    /api/notification-teams/{id}   — update (secret fields: "-"=clear, blank=keep, value=replace)
  DELETE /api/notification-teams/{id}
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agentic_os.db.database import get_session
from agentic_os.db.models import NotificationTeamModel

router = APIRouter()


class NotificationTeamCreate(BaseModel):
    name: str
    pagerduty_routing_key: Optional[str] = None
    slack_channel: Optional[str] = None
    email_recipients: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    enabled: bool = True


class NotificationTeamUpdate(BaseModel):
    name: Optional[str] = None
    pagerduty_routing_key: Optional[str] = None   # "-"=clear, blank/omitted=keep, value=replace
    slack_channel: Optional[str] = None
    email_recipients: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None          # "-"=clear, blank/omitted=keep, value=replace
    enabled: Optional[bool] = None


def _serialize(team: NotificationTeamModel) -> dict:
    return {
        "team_id":                    str(team.team_id),
        "name":                       team.name,
        "pagerduty_routing_key_set":  bool(team.pagerduty_routing_key),
        "slack_channel":              team.slack_channel,
        "email_recipients":           team.email_recipients,
        "webhook_url":                team.webhook_url,
        "webhook_secret_set":         bool(team.webhook_secret),
        "enabled":                    team.enabled,
        "created_at":                 team.created_at.isoformat(),
        "updated_at":                 team.updated_at.isoformat(),
    }


@router.get("/notification-teams")
def list_notification_teams(db: Session = Depends(get_session)):
    teams = db.query(NotificationTeamModel).order_by(NotificationTeamModel.name).all()
    return [_serialize(t) for t in teams]


@router.post("/notification-teams", status_code=201)
def create_notification_team(body: NotificationTeamCreate, db: Session = Depends(get_session)):
    existing = db.query(NotificationTeamModel).filter(
        NotificationTeamModel.name.ilike(body.name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"A team named '{body.name}' already exists")

    team = NotificationTeamModel(
        name=body.name,
        pagerduty_routing_key=body.pagerduty_routing_key or None,
        slack_channel=body.slack_channel or None,
        email_recipients=body.email_recipients or None,
        webhook_url=body.webhook_url or None,
        webhook_secret=body.webhook_secret or None,
        enabled=body.enabled,
    )
    db.add(team)
    db.commit()
    return _serialize(team)


@router.put("/notification-teams/{team_id}")
def update_notification_team(team_id: UUID, body: NotificationTeamUpdate, db: Session = Depends(get_session)):
    team = db.query(NotificationTeamModel).filter_by(team_id=team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Notification team not found")

    if body.name is not None:
        team.name = body.name
    if body.slack_channel is not None:
        team.slack_channel = body.slack_channel or None
    if body.email_recipients is not None:
        team.email_recipients = body.email_recipients or None
    if body.webhook_url is not None:
        team.webhook_url = body.webhook_url or None
    if body.enabled is not None:
        team.enabled = body.enabled

    # Secret fields: "-" clears, blank/omitted keeps, any other value replaces.
    if body.pagerduty_routing_key == "-":
        team.pagerduty_routing_key = None
    elif body.pagerduty_routing_key:
        team.pagerduty_routing_key = body.pagerduty_routing_key

    if body.webhook_secret == "-":
        team.webhook_secret = None
    elif body.webhook_secret:
        team.webhook_secret = body.webhook_secret

    team.updated_at = datetime.utcnow()
    db.commit()
    return _serialize(team)


@router.delete("/notification-teams/{team_id}", status_code=204)
def delete_notification_team(team_id: UUID, db: Session = Depends(get_session)):
    team = db.query(NotificationTeamModel).filter_by(team_id=team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Notification team not found")
    db.delete(team)
    db.commit()
    return None
