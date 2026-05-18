"""Tests for the Phase 3.2 Redis HA selection logic.

We don't spin up real Sentinel / Cluster topologies; we verify that
the right client *type* is constructed and that bad config fails loud."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.container import _parse_address_list


def test_parse_address_list_handles_mixed_input():
    assert _parse_address_list("h1:1,h2:2") == [("h1", 1), ("h2", 2)]
    # Default port when only host is given.
    assert _parse_address_list("h1") == [("h1", 6379)]
    # Empty / whitespace entries are skipped.
    assert _parse_address_list("h1:1, ,h3:3") == [("h1", 1), ("h3", 3)]
    assert _parse_address_list("") == []
    assert _parse_address_list(None) == []
    # Invalid port is skipped, not crashed.
    assert _parse_address_list("h1:abc,h2:2") == [("h2", 2)]


@pytest.mark.asyncio
async def test_sentinel_client_requires_addresses(monkeypatch):
    from app.core.container import _build_sentinel_client
    monkeypatch.delenv("REDIS_SENTINEL_ADDRESSES", raising=False)
    with pytest.raises(RuntimeError, match="REDIS_SENTINEL_ADDRESSES"):
        await _build_sentinel_client()


@pytest.mark.asyncio
async def test_cluster_client_requires_nodes(monkeypatch):
    from app.core.container import _build_cluster_client
    monkeypatch.delenv("REDIS_CLUSTER_NODES", raising=False)
    with pytest.raises(RuntimeError, match="REDIS_CLUSTER_NODES"):
        await _build_cluster_client()


@pytest.mark.asyncio
async def test_sentinel_client_constructs_with_addresses(monkeypatch):
    from app.core import container as C
    monkeypatch.setenv(
        "REDIS_SENTINEL_ADDRESSES",
        "s1:26379,s2:26379,s3:26379",
    )
    monkeypatch.setenv("REDIS_SENTINEL_SERVICE_NAME", "mymaster")
    # Sentinel ctor is sync but does no I/O — we patch master_for to
    # avoid the network call.
    with patch("redis.asyncio.sentinel.Sentinel.master_for") as mf:
        mf.return_value = "stub-master-redis"
        client = await C._build_sentinel_client()
    assert client == "stub-master-redis"
    assert mf.call_args.args[0] == "mymaster"


@pytest.mark.asyncio
async def test_cluster_client_constructs_with_nodes(monkeypatch):
    from app.core import container as C
    monkeypatch.setenv(
        "REDIS_CLUSTER_NODES",
        "n1:6379,n2:6379,n3:6379",
    )
    # RedisCluster.__init__ does no I/O — it just records startup nodes.
    client = await C._build_cluster_client()
    assert hasattr(client, "execute_command")  # the redis client surface
