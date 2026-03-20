# MasterMonitor Cloud Ingestion API

A production-ready FastAPI server that acts as the **Cloud Layer** for the MasterMonitor system.  
It receives sensor telemetry from Local Master Monitor Servers, stores data in a database, and dispatches commands back to hardware devices.

## CI/CD and Deployment
Automated deployment is configured via **GitHub Actions**.

1. The workflow uses the same **SSH Secrets** (`SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`) as the MasterMonitorServer.
2. The service runs as a **Systemd User Service** on Port **8000**.
3. Pushing to `main` will automatically deploy and restart the API.

## Database Initialization
The API is configured to use SQLite by default (`mastermonitor.db`). Tables are automatically created on the first run by the `lifespan` event in `main.py`.

## File Structure

```
CloudIngestionAPI/
├── main.py          # FastAPI app — all API endpoints
├── models.py        # Pydantic data models (request/response validation)
├── database.py      # SQLAlchemy ORM — database tables and session management
├── auth.py          # API key authentication
└── requirements.txt # Python dependencies
```

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set API Key (optional for dev, required for production)
```bash
# Windows
set MASTER_MONITOR_API_KEY=your-secret-key-here

# Linux/Mac
export MASTER_MONITOR_API_KEY=your-secret-key-here
```

### 3. Run the Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The server will start and automatically create the SQLite database file `mastermonitor.db`.

### 4. View Interactive API Docs
Open your browser at: **http://localhost:8000/docs**

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/rx` | Receive data from local monitor server |
| `POST` | `/command/{device_id}` | Queue a command for a device |
| `GET`  | `/devices` | List all devices |
| `GET`  | `/devices/{id}/readings` | Get recent readings |
| `GET`  | `/devices/{id}/latest` | Get latest reading |
| `GET`  | `/health` | Health check |

## Authentication

All endpoints (except `/health`) require the `X-API-Key` header:
```
X-API-Key: your-secret-key-here
```

## Connecting the Local Monitor Server

Update `protocol.py` in `MasterMonitorServer` to point to this server:
```python
CLOUD_ENDPOINT_URL = "http://localhost:8000/rx"
```

Also update `main.py` in `MasterMonitorServer` to include the API key header
in the `forward_to_cloud` function.

## Production Deployment

For production:
1. Replace SQLite with PostgreSQL by setting `DATABASE_URL` env variable:
   ```
   DATABASE_URL=postgresql://user:password@host/dbname
   ```
2. Run behind a reverse proxy (nginx) with HTTPS
3. Use a process manager like `gunicorn` or `systemd`
