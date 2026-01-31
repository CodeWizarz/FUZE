import pytest
from temporalio.testing import WorkflowEnvironment

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def temporal_env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env
