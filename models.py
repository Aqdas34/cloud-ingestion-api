import time as _time
from pydantic import BaseModel, Field
from typing import List, Optional


class SensorReading(BaseModel):
    """
    Represents a single timestamped sensor reading from a hardware device.
    All fields match the JSON payload sent by the ESP32/local monitor server.
    """
    # System / Status
    SystemErrorCode: int = Field(0, description="0 = no error")
    Time: int = Field(..., description="Unix epoch timestamp from the device")

    # Storage
    SdSpaceUsed: Optional[int] = Field(None, description="SD card space used in KB")
    SdSpaceLeft: Optional[int] = Field(None, description="SD card space remaining in KB")
    SdDetect: Optional[int] = Field(None, description="1 = SD card present, 0 = absent")

    # Power
    BatteryLevel: Optional[int] = Field(None, description="Battery percentage 0-100")

    # Safety sensors
    Alarm: Optional[int] = Field(None, description="1 = alarm active, 0 = normal")
    Smoke: Optional[int] = Field(None, description="Smoke level (raw sensor value)")
    CarbonMonoxide: Optional[int] = Field(None, description="CO level (raw sensor value)")
    Gas: Optional[float] = Field(None, description="Combustible gas level (kOhms)")

    # Environmental sensors
    AQI: Optional[float] = Field(None, description="Air Quality Index")
    Temperature: Optional[float] = Field(None, description="Ambient temperature in Celsius")
    Humidity: Optional[int] = Field(None, description="Relative humidity percentage (0-100)")
    Pressure: Optional[float] = Field(None, description="Atmospheric pressure in hPa")

    # Presence / Motion
    MotionPresence: Optional[int] = Field(None, description="1 = motion detected, 0 = none")
    NoisePresence: Optional[int] = Field(None, description="1 = noise detected, 0 = none")
    NoiseLevel: Optional[float] = Field(None, description="Ambient noise level in dBA")

    # Control flags
    HornHush: Optional[int] = Field(None, description="1 = alarm silenced by user")
    Test: Optional[int] = Field(None, description="1 = test mode active")


class DevicePayload(BaseModel):
    """
    The top-level payload received from the Local Master Monitor Server.
    Wraps one or more sensor readings under a specific device ID.
    """
    deviceId: str = Field(..., description="Unique hardware device identifier, e.g. MM-1A2B3C4D5E6F")
    data: List[SensorReading] = Field(..., description="One or more timestamped sensor readings")


class CloudResponse(BaseModel):
    """
    The response returned to the Local Master Monitor Server after processing.
    Matches the exact spec from the Cloud Ingestion API PDF document.
    """
    status: str = Field("ok", description="'ok' on success, any other value treated as error by local server")
    time: int = Field(default_factory=lambda: int(_time.time()), description="Cloud's current Unix timestamp — used by device for time sync")
    command: str = Field("none", description="Command to forward to the device: none | hush | test | external_alarm")
    # Extra informational fields (not required by spec but useful for debugging)
    message: Optional[str] = Field(None, description="Optional human-readable message")
    received_count: Optional[int] = Field(None, description="Number of data points successfully stored")
