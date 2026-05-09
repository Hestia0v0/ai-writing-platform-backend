import os
import psycopg2
from psycopg2.extras import RealDictCursor

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://platform:platform@postgres:5432/platform")


def get_conn():
    return psycopg2.connect(POSTGRES_DSN, cursor_factory=RealDictCursor)
