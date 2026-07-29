from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

csrf = CSRFProtect()
# In-memory storage is intentional: this dashboard runs as a single
# gunicorn worker (see dashboard/Dockerfile), so per-process rate-limit
# state is correct here, not a shortcut that breaks under multiple workers.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
