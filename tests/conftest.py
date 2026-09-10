import os

import pytest

# Tests are self-contained: offline AI, no embeddings, a known token and no analysis debounce, regardless of the
# environment of the container they run in.
os.environ.update(AI_PROVIDER='mock', EMBEDDINGS_PROVIDER='none', ENGINE_TOKEN='test-token', ANALYZE_DEBOUNCE_SECONDS='0')
# The model ids come from the same .env the running engine uses, and app.config.model_for honours them - so a host
# that runs qwen would silently change what the provider-default assertions are testing.
for name in ('EXTRACT_MODEL', 'ANALYST_MODEL', 'TRANSLATE_MODEL', 'OUTPUT_LANGUAGE'):
    os.environ.pop(name, None)

needs_database = pytest.mark.skipif(not os.environ.get('ECO_DATABASE_URL'),
                                    reason='set ECO_DATABASE_URL to run database tests (scripts/test.sh does)')
