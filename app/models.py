from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(220))
    role: Mapped[str] = mapped_column(String(20), default='employee')
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Organization(Base):
    __tablename__ = 'organizations'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Service(Base):
    __tablename__ = 'services'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class WorkRecord(Base):
    __tablename__ = 'work_records'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[datetime] = mapped_column(Date)
    organization_id: Mapped[int] = mapped_column(ForeignKey('organizations.id'))
    service_id: Mapped[int] = mapped_column(ForeignKey('services.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    device_type: Mapped[str] = mapped_column(String(70))
    model: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default='')
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    organization: Mapped[Organization] = relationship()
    service: Mapped[Service] = relationship()
    user: Mapped[User] = relationship()

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    record_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30))
    details: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    user: Mapped[User] = relationship()
