"""Adaptadores de mensajería para Microsoft Teams."""

from geoassist.teams.base import InvalidPayload, TeamsAdapter, TeamsAuthError
from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter

__all__ = ["InvalidPayload", "OutgoingWebhookAdapter", "TeamsAdapter", "TeamsAuthError"]
