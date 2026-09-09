import os

import pytest

# Tests are self-contained: offline AI, no embeddings, a known token and no analysis debounce, regardless of the
# environment of the container they run in.
os.environ.update(AI_PROVIDER='mock', EMBEDDINGS_PROVIDER='none', ENGINE_TOKEN='test-token', ANALYZE_DEBOUNCE_SECONDS='0')

needs_database = pytest.mark.skipif(not os.environ.get('ECO_DATABASE_URL'),
                                    reason='set ECO_DATABASE_URL to run database tests (scripts/test.sh does)')
