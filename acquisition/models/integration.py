"""Generic integration connection records for third-party providers."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from acquisition.db.base import Base


class IntegrationProvider(str, enum.Enum):
    INSTANTLY = "instantly"
    OPENAI = "openai"
    CALCOM = "calcom"
    STRIPE = "stripe"


class ConnectionStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    ERROR = "error"


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(
            IntegrationProvider,
            name="integration_provider",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    connection_status: Mapped[ConnectionStatus] = mapped_column(
        Enum(
            ConnectionStatus,
            name="connection_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=ConnectionStatus.UNKNOWN,
        server_default=ConnectionStatus.UNKNOWN.value,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("provider", name="uq_integration_connections_provider"),
        Index("ix_integration_connections_provider", "provider"),
        Index("ix_integration_connections_status", "connection_status"),
    )
