import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def database():
    """One short-lived connection per unit of work. LLM calls never run inside this block."""
    kwargs = dict(connect_timeout=3, options='-c statement_timeout=30000 -c lock_timeout=5000', row_factory=dict_row)
    url = os.environ.get('ECO_DATABASE_URL')
    if url:
        conn = psycopg.connect(url, **kwargs)
    else:
        conn = psycopg.connect(host=os.getenv('DB_HOST', 'postgres'), port=int(os.getenv('DB_PORT', '5432')),
                               dbname=os.getenv('DB_NAME', 'eco'), user=os.getenv('DB_USER', 'eco'),
                               password=os.environ['POSTGRES_PASSWORD'], **kwargs)
    with conn:
        yield conn
