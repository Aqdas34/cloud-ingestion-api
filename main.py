import logging
import requests
import json
import os
from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from auth import verify_api_key
from database import get_db, create_tables, DeviceReading, PendingCommand, Device, AlertLog, DeviceLink
from models import DevicePayload, CloudResponse

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# --- App Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Creates database tables on startup."""
    create_tables()
    yield


# --- Push Notification Helper ---
def send_push_notification(token: str, title: str, body: str):
    """Sends a push notification via Expo's API."""
    if not token:
        return
    try:
        requests.post(
            "https://exp.host/--/api/v2/push/send",
            json={
                "to": token,
                "title": title,
                "body": body,
                "sound": "default",
                "priority": "high"
            },
            timeout=5
        )
    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")


# --- FastAPI App ---
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="MasterMonitor Cloud Ingestion API",
    description=(
        "Receives sensor telemetry from Local Master Monitor Servers, "
        "stores the data, and returns commands for hardware devices."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Allow the web dashboard to call the API from any local origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# MAIN INGESTION ENDPOINT
# ==============================================================================

@app.post(
    "/rx",
    response_model=CloudResponse,
    summary="Receive sensor data from a Local Monitor Server",
    tags=["Ingestion"],
)
@limiter.limit("5/second")
async def receive_data(
    request: Request,
    payload: DevicePayload,
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
):
    """
    Primary ingestion endpoint. The Local Master Monitor Server posts all
    buffered sensor readings here.
    """
    try:
        device_id = payload.deviceId
        readings = payload.data

        if not readings:
            raise HTTPException(status_code=400, detail="Payload 'data' array cannot be empty.")

        logger.info(f"Received {len(readings)} reading(s) from device: {device_id}")

        # --- Step 1: Store all readings ---
        db_readings = []
        for reading in readings:
            db_reading = DeviceReading(
                device_id=device_id,
                device_time=reading.Time,
                system_error_code=reading.SystemErrorCode,
                sd_space_used=reading.SdSpaceUsed,
                sd_space_left=reading.SdSpaceLeft,
                sd_detect=reading.SdDetect,
                battery_level=reading.BatteryLevel,
                alarm=reading.Alarm,
                smoke=reading.Smoke,
                carbon_monoxide=reading.CarbonMonoxide,
                gas=reading.Gas,
                aqi=reading.AQI,
                temperature=reading.Temperature,
                humidity=reading.Humidity,
                pressure=reading.Pressure,
                motion_presence=reading.MotionPresence,
                noise_presence=reading.NoisePresence,
                noise_level=reading.NoiseLevel,
                horn_hush=reading.HornHush,
                test=reading.Test,
                sensors_json=json.dumps(reading.sensors) if reading.sensors else None,
            )
            db_readings.append(db_reading)

        db.add_all(db_readings)

        # --- Step 2: Update or create Device record ---
        device = db.query(Device).filter(Device.device_id == device_id).first()
        if device:
            device.last_seen = datetime.now(timezone.utc)
            device.total_readings = (device.total_readings or 0) + len(readings)
        else:
            device = Device(
                device_id=device_id,
                total_readings=len(readings),
            )
            db.add(device)
            logger.info(f"Registered new device: {device_id}")

        db.commit()
        logger.info(f"Stored {len(readings)} reading(s) for device {device_id}.")

        # --- Step 3: Alerts & Linking ---
        latest_reading = readings[-1]
        if latest_reading.Alarm == 1:
            # Create an alert log
            log = AlertLog(
                device_id=device_id,
                event_type="Smoke/Gas Alarm",
                severity="critical",
                message=f"Alarm triggered on {device_id}!"
            )
            db.add(log)
            
            # Send Push Notification
            device = db.query(Device).filter(Device.device_id == device_id).first()
            if device and device.expo_push_token:
                logger.info(f"Sending Push Notification to {device_id}...")
                send_push_notification(
                    device.expo_push_token,
                    "⚠️ MASTER MONITOR ALARM",
                    f"Warning: Smoke or Gas detected at {device_id}!"
                )

            # Trigger linked devices
            links = db.query(DeviceLink).filter(
                (DeviceLink.device_a == device_id) | (DeviceLink.device_b == device_id)
            ).all()
            
            for link in links:
                target_id = link.device_b if link.device_a == device_id else link.device_a
                # Add pending command if not already there
                existing = db.query(PendingCommand).filter(
                    PendingCommand.device_id == target_id,
                    PendingCommand.command == "external_alarm"
                ).first()
                if not existing:
                    new_cmd = PendingCommand(device_id=target_id, command="external_alarm")
                    db.add(new_cmd)
                    logger.info(f"Linking: Queued external alarm for {target_id} due to {device_id}")

        elif latest_reading.BatteryLevel and latest_reading.BatteryLevel < 20:
             # Low battery log
             existing_log = db.query(AlertLog).filter(
                 AlertLog.device_id == device_id,
                 AlertLog.event_type == "Low Battery",
                 AlertLog.timestamp > datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
             ).first()
             if not existing_log:
                 db.add(AlertLog(
                     device_id=device_id,
                     event_type="Low Battery",
                     severity="warning",
                     message=f"Battery low on {device_id} ({latest_reading.BatteryLevel}%)"
                 ))

        db.commit()

        # --- Step 4: Check for a pending command ---
        pending = db.query(PendingCommand).filter(PendingCommand.device_id == device_id).first()
        command_to_send = "none"

        if pending:
            command_to_send = pending.command
            db.delete(pending)
            db.commit()
            logger.info(f"Dispatching command '{command_to_send}' to device {device_id}.")

        return CloudResponse(
            status="ok",
            command=command_to_send,
            received_count=len(readings),
            message=f"Successfully stored {len(readings)} reading(s).",
        )
    except Exception as e:
        import traceback
        logger.error(f"FATAL ERROR in /rx: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# COMMAND MANAGEMENT ENDPOINTS
# ==============================================================================

@app.post(
    "/command/{device_id}",
    summary="Queue a command for a specific device",
    tags=["Commands"],
)
async def send_command(
    device_id: str,
    command: str,
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
):
    """
    Queue a command to be sent to a specific device on its next check-in.
    Valid commands: none | hush | test | external_alarm
    """
    valid_commands = {"none", "hush", "test", "external_alarm"}
    if command not in valid_commands:
        raise HTTPException(status_code=400, detail=f"Invalid command. Must be one of: {valid_commands}")

    # Upsert: update existing or create new pending command
    pending = db.query(PendingCommand).filter(PendingCommand.device_id == device_id).first()
    if pending:
        pending.command = command
        logger.info(f"Updated pending command for {device_id} to '{command}'.")
    else:
        pending = PendingCommand(device_id=device_id, command=command)
        db.add(pending)
        logger.info(f"Queued command '{command}' for device {device_id}.")

    db.commit()
    return {"status": "ok", "device_id": device_id, "command_queued": command}


# ==============================================================================
# READ / QUERY ENDPOINTS
# ==============================================================================

@app.get(
    "/devices/summary",
    summary="Get all devices with their latest sensor reading",
    tags=["Devices"],
)
async def list_devices_summary(
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
):
    """
    Returns a unified summary of all registered devices including their 
    single most recent reading. This is optimized for the dashboard to 
    load the entire network state in one request.
    """
    # 1. Fetch all basic device records
    devices = db.query(Device).all()
    
    # 2. Get the IDs of the latest reading for each device
    # This is an efficient way to get "Latest per Group" in SQL
    from sqlalchemy import func
    latest_ids_subquery = (
        db.query(func.max(DeviceReading.id))
        .group_by(DeviceReading.device_id)
        .all()
    )
    latest_ids = [r[0] for r in latest_ids_subquery if r[0] is not None]
    
    # 3. Fetch the full reading objects for those IDs
    latest_readings_map = {}
    if latest_ids:
        readings = db.query(DeviceReading).filter(DeviceReading.id.in_(latest_ids)).all()
        for r in readings:
            latest_readings_map[r.device_id] = {
                "received_at": r.received_at,
                "temperature": r.temperature,
                "humidity": r.humidity,
                "aqi": r.aqi,
                "smoke": r.smoke,
                "carbon_monoxide": r.carbon_monoxide,
                "battery_level": r.battery_level,
                "alarm": r.alarm,
                "system_error_code": r.system_error_code,
                "sd_space_used": r.sd_space_used,
                "sd_space_left": r.sd_space_left,
                "sd_detect": r.sd_detect,
                "motion_presence": r.motion_presence,
                "noise_presence": r.noise_presence,
                "noise_level": r.noise_level,
                "horn_hush": r.horn_hush,
                "test": r.test,
                "sensors": json.loads(r.sensors_json) if r.sensors_json else None,
            }

    # 4. Merge
    return [
        {
            "device_id": d.device_id,
            "first_seen": d.first_seen,
            "last_seen": d.last_seen,
            "total_readings": d.total_readings,
            "latest": latest_readings_map.get(d.device_id)
        }
        for d in devices
    ]


@app.get(
    "/devices",
    summary="List all known devices",
    tags=["Devices"],
)
async def list_devices(
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
):
    """Returns a list of all registered devices and their last-seen time."""
    devices = db.query(Device).all()
    return [
        {
            "device_id": d.device_id,
            "first_seen": d.first_seen,
            "last_seen": d.last_seen,
            "total_readings": d.total_readings,
        }
        for d in devices
    ]


@app.get(
    "/devices/{device_id}/readings",
    summary="Get recent readings for a device",
    tags=["Devices"],
)
async def get_device_readings(
    device_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
):
    """Returns the most recent sensor readings for a given device."""
    readings = (
        db.query(DeviceReading)
        .filter(DeviceReading.device_id == device_id)
        .order_by(DeviceReading.received_at.desc())
        .limit(limit)
        .all()
    )
    if not readings:
        raise HTTPException(status_code=404, detail=f"No readings found for device '{device_id}'.")

    return [
        {
            "id": r.id,
            "received_at": r.received_at,
            "device_time": r.device_time,
            "temperature": r.temperature,
            "humidity": r.humidity,
            "aqi": r.aqi,
            "smoke": r.smoke,
            "carbon_monoxide": r.carbon_monoxide,
            "gas": r.gas,
            "battery_level": r.battery_level,
            "motion_presence": r.motion_presence,
            "noise_level": r.noise_level,
            "alarm": r.alarm,
            "pressure": r.pressure,
            "system_error_code": r.system_error_code,
            "sd_space_used": r.sd_space_used,
            "sd_space_left": r.sd_space_left,
            "sd_detect": r.sd_detect,
            "motion_presence": r.motion_presence,
            "noise_presence": r.noise_presence,
            "horn_hush": r.horn_hush,
            "test": r.test,
            "sensors": json.loads(r.sensors_json) if r.sensors_json else None,
        }
        for r in readings
    ]


@app.get(
    "/devices/{device_id}/latest",
    summary="Get only the latest reading for a device",
    tags=["Devices"],
)
async def get_latest_reading(
    device_id: str,
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
):
    """Returns the single most recent sensor reading for a given device."""
    reading = (
        db.query(DeviceReading)
        .filter(DeviceReading.device_id == device_id)
        .order_by(DeviceReading.received_at.desc())
        .first()
    )
    if not reading:
        raise HTTPException(status_code=404, detail=f"No readings found for device '{device_id}'.")

    return {
        "device_id": device_id,
        "received_at": reading.received_at,
        "device_time": reading.device_time,
        "temperature": reading.temperature,
        "humidity": reading.humidity,
        "aqi": reading.aqi,
        "smoke": reading.smoke,
        "carbon_monoxide": reading.carbon_monoxide,
        "gas": reading.gas,
        "battery_level": reading.battery_level,
        "motion_presence": reading.motion_presence,
        "noise_presence": reading.noise_presence,
        "noise_level": reading.noise_level,
        "alarm": reading.alarm,
        "system_error_code": reading.system_error_code,
        "sd_space_used": reading.sd_space_used,
        "sd_space_left": reading.sd_space_left,
        "sd_detect": reading.sd_detect,
        "horn_hush": reading.horn_hush,
        "test": reading.test,
        "pressure": reading.pressure,
        "sensors": json.loads(reading.sensors_json) if reading.sensors_json else None,
    }

# ==============================================================================
# CATALOGUE ENDPOINTS
# ==============================================================================

@app.get("/sensor-types", tags=["Catalogue"])
async def get_sensor_types(_api_key: str = Depends(verify_api_key)):
    """Returns the master catalogue of supported external sensor types."""
    try:
        # Load from the local docs folder
        path = os.path.join(os.path.dirname(__file__), "docs", "sensor_types_export.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load sensor types: {e}")
        raise HTTPException(status_code=500, detail="Catalogue unavailable")


# ==============================================================================
# ALERTS & LINKING ENDPOINTS
# ==============================================================================

@app.get("/alerts", tags=["Logs"])
async def get_alerts(limit: int = 50, db: Session = Depends(get_db), _api_key: str = Depends(verify_api_key)):
    """Returns the most recent critical alerts."""
    return db.query(AlertLog).order_by(AlertLog.timestamp.desc()).limit(limit).all()

@app.post("/links", tags=["Linking"])
async def create_link(device_a: str, device_b: str, db: Session = Depends(get_db), _api_key: str = Depends(verify_api_key)):
    """Links two devices together."""
    if device_a == device_b:
        raise HTTPException(status_code=400, detail="Cannot link a device to itself")
    
    # Check if link already exists
    exists = db.query(DeviceLink).filter(
        ((DeviceLink.device_a == device_a) & (DeviceLink.device_b == device_b)) |
        ((DeviceLink.device_a == device_b) & (DeviceLink.device_b == device_a))
    ).first()
    
    if exists:
        return {"status": "ok", "message": "Link already exists"}
    
    new_link = DeviceLink(device_a=device_a, device_b=device_b)
    db.add(new_link)
    db.commit()
    return {"status": "ok", "message": f"Linked {device_a} and {device_b}"}

@app.delete("/links", tags=["Linking"])
async def delete_links(device_id: str, db: Session = Depends(get_db), _api_key: str = Depends(verify_api_key)):
    """Deletes all links for a specific device."""
    db.query(DeviceLink).filter(
        (DeviceLink.device_a == device_id) | (DeviceLink.device_b == device_id)
    ).delete()
    db.commit()
    return {"status": "ok", "message": f"Cleared links for {device_id}"}

@app.get("/links/{device_id}", tags=["Linking"])
async def get_device_links(device_id: str, db: Session = Depends(get_db), _api_key: str = Depends(verify_api_key)):
    """Returns all devices linked to this one."""
    links = db.query(DeviceLink).filter(
        (DeviceLink.device_a == device_id) | (DeviceLink.device_b == device_id)
    ).all()
    
    linked_ids = []
    for l in links:
        linked_ids.append(l.device_b if l.device_a == device_id else l.device_a)
    return {"device_id": device_id, "linked_to": linked_ids}

@app.post("/register-push", tags=["Linking"])
async def register_push(device_id: str, token: str, db: Session = Depends(get_db), _api_key: str = Depends(verify_api_key)):
    """Registers an Expo Push Token for a device."""
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        device = Device(device_id=device_id, expo_push_token=token)
        db.add(device)
    else:
        device.expo_push_token = token
    db.commit()
    return {"status": "ok", "message": "Push token registered"}

# ==============================================================================
# HEALTH CHECK
# ==============================================================================

@app.get("/health", summary="Health check", tags=["System"])
async def health_check():
    """Simple health check — no auth required."""
    return {"status": "healthy", "service": "MasterMonitor Cloud Ingestion API"}
