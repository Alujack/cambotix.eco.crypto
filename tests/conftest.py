import os

import pytest

os.environ.setdefault('AI_PROVIDER', 'mock')
os.environ.setdefault('EMBEDDINGS_PROVIDER', 'none')
os.environ.setdefault('ENGINE_TOKEN', 'test-token')

needs_database = pytest.mark.skipif(not os.environ.get('ECO_DATABASE_URL'),
                                    reason='set ECO_DATABASE_URL to run database tests (scripts/test.sh does)')
