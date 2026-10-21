"""Outbound channel abstraction — email via Instantly; chat/SMS unconfigured stubs."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class OutboundChannel(str, enum.Enum):
    EMAIL = "email"
    CHAT = "chat"
    SMS = "sms"


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    channel: OutboundChannel
    configured: bool
    provider_name: str | None
    message: str


class OutboundChannelAdapter(ABC):
    channel: OutboundChannel

    @abstractmethod
    def status(self) -> ChannelStatus:
        raise NotImplementedError


class InstantlyEmailChannel(OutboundChannelAdapter):
    channel = OutboundChannel.EMAIL

    def status(self) -> ChannelStatus:
        return ChannelStatus(
            channel=OutboundChannel.EMAIL,
            configured=True,
            provider_name="Instantly",
            message="Email outbound is implemented via Instantly API V2.",
        )


class UnconfiguredChannel(OutboundChannelAdapter):
    def __init__(self, channel: OutboundChannel) -> None:
        self.channel = channel

    def status(self) -> ChannelStatus:
        return ChannelStatus(
            channel=self.channel,
            configured=False,
            provider_name=None,
            message="Not configured",
        )


def list_channel_statuses() -> list[ChannelStatus]:
    return [
        InstantlyEmailChannel().status(),
        UnconfiguredChannel(OutboundChannel.CHAT).status(),
        UnconfiguredChannel(OutboundChannel.SMS).status(),
    ]
