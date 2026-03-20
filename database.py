import os
from sqlalchemy import create_engine, Column, Integer, Float, String, BigInteger, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone

# --- Database Setup ---
# Uses SQLite for local development. Can be switched to PostgreSQL for production
# by setting the DATABASE_URL environment variable.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mastermonitor.db")

engine = create_engine(
    DATABASE_URL,
    # Required argument for SQLite — ignored for other databases
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- ORM Models (Database Tables) ---

class DeviceReading(Base):
    """
    Stores a single timestamped sensor reading from a device.
    One record = one entry in the 'data' array from the device payload.
    """
    __tablename__ = "device_readings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Device identifier
    device_id = Column(String, index=True, nullable=False)

    # Sensor timestamp (from the device itself)
    device_time = Column(BigInteger, nullable=True)

    # System/Status
    system_error_code = Column(Integer, nullable=True)

    # Storage
    sd_space_used = Column(Integer, nullable=True)
    sd_space_left = Column(Integer, nullable=True)
    sd_detect = Column(Integer, nullable=True)

    # Power
    battery_level = Column(Integer, nullable=True)

    # Safety
    alarm = Column(Integer, nullable=True)
    smoke = Column(Integer, nullable=True)
    carbon_monoxide = Column(Integer, nullable=True)
    gas = Column(Float, nullable=True)

    # Environmental
    aqi = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    humidity = Column(Integer, nullable=True)
    pressure = Column(Float, nullable=True)

    # Presence
    motion_presence = Column(Integer, nullable=True)
    noise_presence = Column(Integer, nullable=True)
    noise_level = Column(Float, nullable=True)

    # Control flags
    horn_hush = Column(Integer, nullable=True)
    test = Column(Integer, nullable=True)


class PendingCommand(Base):
    """
    Stores commands to be sent to a specific device on its next check-in.
    Commands are consumed (deleted) once delivered.
    """
    __tablename__ = "pending_commands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, unique=True, index=True, nullable=False)
    command = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Device(Base):
    """
    Tracks known devices and their last seen time.
    Automatically created/updated when data is received from a device.
    """
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, unique=True, index=True, nullable=False)
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    total_readings = Column(Integer, default=0)
    expo_push_token = Column(String, nullable=True) # For mobile notifications


class AlertLog(Base):
    """
    Stores a permanent log of critical events (Alarms, Low Battery, etc.)
    for display in the app's alert feed.
    """
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, index=True, nullable=False)
    event_type = Column(String, nullable=False) # e.g., "Smoke Detected", "CO Alarm", "Low Battery"
    severity = Column(String, default="info") # info, warning, critical
    message = Column(String, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DeviceLink(Base):
    """
    Defines relationships between devices. 
    If device A is linked to device B, an alarm on A will trigger an alarm on B.
    """
    __tablename__ = "device_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_a = Column(String, index=True, nullable=False)
    device_b = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def create_tables():
    """Creates all database tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency to provide a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
