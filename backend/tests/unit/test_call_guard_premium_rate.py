"""The dialer never calls a premium-rate line (QA F2-14, QA-BE-043).

The guard checked only the E.164 shape, so a lead list holding a UK 09 or a
US 900 number would be dialled at premium per-minute rates.
"""
from __future__ import annotations

import pytest

from app.domain.services.call_guard import CallGuard


async def _check(number):
    return await CallGuard(db_pool=object(), redis_client=None)._check_number_valid(number)


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["+449012345678", "+19002345678", "+611900123456"])
async def test_a_premium_rate_number_is_never_dialled(number):
    result = await _check(number)
    assert result.passed is False and result.reason == "premium_rate_destination"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "number",
    [
        "+447400123456",  # UK mobile
        "+442071234567",  # London landline
        "+448001234567",  # UK freephone
        "+448451234567",  # UK 0845 service number (a common business line)
        "+448712345678",  # UK 0871 service number
        "+12015550123",   # US
    ],
)
async def test_ordinary_and_business_service_numbers_stay_callable(number):
    assert (await _check(number)).passed is True
