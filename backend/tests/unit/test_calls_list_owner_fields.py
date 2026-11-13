"""D4-12: the calls list says whose list it is, as /events does.

The app checks that an alert belongs to the signed-in account before showing
it. /events carries tenant_id and user_id; the calls list carried neither.
"""
from tests.unit.test_bh_calls_recordings_calls_endpoint import _Conn, _list, _user


async def test_the_calls_list_carries_the_account_and_user_it_was_read_for():
    user = _user()
    response = await _list(_Conn(), current_user=user)
    assert response.tenant_id == str(user.tenant_id)
    assert response.user_id == str(user.id)
    dumped = response.model_dump()
    assert {"tenant_id", "user_id"} <= set(dumped)
