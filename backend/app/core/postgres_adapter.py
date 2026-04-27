"""
PostgreSQL Adapter

This module preserves the existing `.table(...).select(...).execute()` calling
style while running everything against PostgreSQL via asyncpg.

Notes:
- `execute()` works in both sync and async call sites.
- Legacy `Client` name is kept for backward compatibility.
- Storage is local filesystem-backed (no external cloud storage dependency).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import date, datetime, timezone
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import asyncpg
import jwt

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://talkyai:talkyai_secret@localhost:5432/talkyai",
)
_SYNC_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_COLUMN_TYPES_CACHE: Dict[str, Dict[str, str]] = {}


class PostgrestResponse:
    """PostgREST-style response envelope."""

    def __init__(self, data=None, count=None, error=None):
        self.data = data
        self.count = count
        self.error = error


class _ExecutionResult:
    """
    A small wrapper that is both:
    - directly usable as a response object (`result.data`)
    - awaitable (`result = await query.execute()`)
    """

    def __init__(self, response: PostgrestResponse):
        self._response = response

    def __getattr__(self, name: str):
        return getattr(self._response, name)

    def __await__(self):
        async def _done() -> PostgrestResponse:
            return self._response

        return _done().__await__()


@dataclass
class _RelationSpec:
    table: str
    columns: List[str]
    inner: bool = False


class QueryBuilder:
    """Builds SQL queries from chainable table operations."""

    def __init__(self, pool, table_name: str):
        self.pool = pool
        self.table_name = table_name
        self.query_type = "select"
        self.columns = "*"
        self.filters: List[Tuple[str, str, Any]] = []
        self.updates: Optional[Dict] = None
        self.inserts: Optional[Union[Dict, List[Dict]]] = None
        self.upsert_data: Optional[Union[Dict, List[Dict]]] = None
        self.upsert_on_conflict: Optional[str] = None
        self.limit_val: Optional[int] = None
        self.offset_val: int = 0
        self.single_val = False
        self.order_cols: List[Tuple[str, str]] = []
        self.count_mode: Optional[str] = None

    def select(self, columns="*", count: Optional[str] = None):
        self.query_type = "select"
        self.columns = columns
        self.count_mode = count
        return self

    def insert(self, data: Union[Dict, List[Dict]]):
        self.query_type = "insert"
        self.inserts = data
        return self

    def update(self, data: Dict):
        self.query_type = "update"
        self.updates = data
        return self

    def upsert(self, data: Union[Dict, List[Dict]], on_conflict: Optional[str] = None):
        self.query_type = "upsert"
        self.upsert_data = data
        self.upsert_on_conflict = on_conflict
        return self

    def delete(self):
        self.query_type = "delete"
        return self

    def eq(self, column: str, value: Any):
        self.filters.append((column, "=", value))
        return self

    def neq(self, column: str, value: Any):
        self.filters.append((column, "!=", value))
        return self

    def in_(self, column: str, values: List[Any]):
        self.filters.append((column, "IN", values))
        return self

    def lt(self, column: str, value: Any):
        self.filters.append((column, "<", value))
        return self

    def lte(self, column: str, value: Any):
        self.filters.append((column, "<=", value))
        return self

    def gt(self, column: str, value: Any):
        self.filters.append((column, ">", value))
        return self

    def gte(self, column: str, value: Any):
        self.filters.append((column, ">=", value))
        return self

    def like(self, column: str, value: str):
        self.filters.append((column, "LIKE", value))
        return self

    def ilike(self, column: str, value: str):
        self.filters.append((column, "ILIKE", value))
        return self

    def is_(self, column: str, value: Any):
        if value is None or value == "null":
            self.filters.append((column, "IS", None))
        else:
            self.filters.append((column, "IS", value))
        return self

    def limit(self, count: int):
        self.limit_val = count
        return self

    def range(self, start: int, end: int):
        self.offset_val = start
        self.limit_val = end - start + 1
        return self

    def single(self):
        self.single_val = True
        return self

    def order(self, column: str, desc: bool = False):
        self.order_cols.append((column, "DESC" if desc else "ASC"))
        return self

    def execute(self) -> _ExecutionResult:
        """
        Execute immediately so legacy call sites that ignore return value still
        perform side effects. The wrapper remains awaitable.
        """
        response = self._run_sync()
        return _ExecutionResult(response)

    def _run_sync(self) -> PostgrestResponse:
        def _runner() -> PostgrestResponse:
            return asyncio.run(self._execute_async())

        try:
            asyncio.get_running_loop()
            return _SYNC_EXECUTOR.submit(_runner).result()
        except RuntimeError:
            return _runner()

    async def _execute_async(self) -> PostgrestResponse:
        conn = None
        try:
            conn = await asyncpg.connect(_DATABASE_URL)
            
            # Set RLS context for the session
            from app.core.security.tenant_isolation import get_current_tenant_id, get_bypass_rls
            tenant_id = get_current_tenant_id()
            bypass_rls = get_bypass_rls()
            
            if bypass_rls:
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                await conn.execute("SET LOCAL app.current_tenant_id = ''")
            elif tenant_id:
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                await conn.execute("SET LOCAL app.bypass_rls = 'false'")
            else:
                await conn.execute("SET LOCAL app.current_tenant_id = ''")
                await conn.execute("SET LOCAL app.bypass_rls = 'false'")

            return await self._execute_with_conn(conn)
        except Exception as e:
            logger.error("PostgresAdapter query error: %s", e, exc_info=True)
            return PostgrestResponse(error=str(e))
        finally:
            if conn:
                await conn.close()

    async def _execute_with_conn(self, conn) -> PostgrestResponse:
        if self.query_type == "select":
            return await self._execute_select(conn)
        if self.query_type == "insert":
            return await self._execute_insert(conn)
        if self.query_type == "update":
            return await self._execute_update(conn)
        if self.query_type == "upsert":
            return await self._execute_upsert(conn)
        if self.query_type == "delete":
            return await self._execute_delete(conn)
        return PostgrestResponse(error=f"Unknown query type: {self.query_type}")

    def _build_where_clause(
        self,
        start_index: int = 1,
        filters: Optional[Iterable[Tuple[str, str, Any]]] = None,
        column_types: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, List[Any]]:
        active_filters = list(self.filters if filters is None else filters)
        args: List[Any] = []
        where_parts: List[str] = []

        for col, op, val in active_filters:
            if not _IDENT_RE.fullmatch(col):
                raise ValueError(f"Invalid column identifier: {col}")
            udt_name = column_types.get(col) if column_types else None
            if op == "IN":
                if isinstance(val, (list, tuple, set)):
                    args.append([self._coerce_bind_value(v, udt_name) for v in val])
                else:
                    args.append(self._coerce_bind_value(val, udt_name))
                where_parts.append(f"{col} = ANY(${len(args) + start_index - 1})")
            elif op == "IS":
                if val is None:
                    where_parts.append(f"{col} IS NULL")
                else:
                    where_parts.append(f"{col} IS {val}")
            else:
                args.append(self._coerce_bind_value(val, udt_name))
                where_parts.append(f"{col} {op} ${len(args) + start_index - 1}")

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return where_sql, args

    @staticmethod
    def _split_csv(expr: str) -> List[str]:
        parts: List[str] = []
        current: List[str] = []
        depth = 0
        for ch in expr:
            if ch == "," and depth == 0:
                token = "".join(current).strip()
                if token:
                    parts.append(token)
                current = []
                continue
            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            current.append(ch)

        tail = "".join(current).strip()
        if tail:
            parts.append(tail)
        return parts

    def _parse_select_spec(self) -> Tuple[str, List[_RelationSpec]]:
        columns_expr = self.columns or "*"
        if not isinstance(columns_expr, str):
            columns_expr = "*"

        base_cols: List[str] = []
        relations: List[_RelationSpec] = []

        for token in self._split_csv(columns_expr):
            rel_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(!inner)?\((.*)\)", token)
            if rel_match:
                rel_table = rel_match.group(1)
                rel_inner = bool(rel_match.group(2))
                rel_cols_expr = rel_match.group(3).strip()
                rel_cols = self._split_csv(rel_cols_expr) if rel_cols_expr else ["*"]
                rel_cols = [c.strip() for c in rel_cols if c.strip()]
                relations.append(_RelationSpec(table=rel_table, columns=rel_cols or ["*"], inner=rel_inner))
                continue
            base_cols.append(token)

        if not base_cols:
            base_cols = ["*"]

        if "*" in base_cols:
            base_select = "*"
        else:
            for col in base_cols:
                if not _IDENT_RE.fullmatch(col):
                    raise ValueError(f"Invalid select column: {col}")
            base_select = ", ".join(base_cols)

        return base_select, relations

    def _split_base_and_relation_filters(
        self,
    ) -> Tuple[List[Tuple[str, str, Any]], Dict[str, List[Tuple[str, str, Any]]]]:
        base_filters: List[Tuple[str, str, Any]] = []
        relation_filters: Dict[str, List[Tuple[str, str, Any]]] = {}

        for col, op, val in self.filters:
            if "." not in col:
                base_filters.append((col, op, val))
                continue

            rel_name, rel_col = col.split(".", 1)
            if not _IDENT_RE.fullmatch(rel_name) or not _IDENT_RE.fullmatch(rel_col):
                raise ValueError(f"Invalid relational filter: {col}")
            relation_filters.setdefault(rel_name, []).append((rel_col, op, val))

        return base_filters, relation_filters

    @staticmethod
    def _to_singular(name: str) -> str:
        if name.endswith("ies") and len(name) > 3:
            return f"{name[:-3]}y"
        if name.endswith("s") and len(name) > 1:
            return name[:-1]
        return name

    def _detect_fk_column(self, row: Dict[str, Any], relation_table: str) -> Optional[str]:
        singular = self._to_singular(relation_table)
        candidates = (f"{singular}_id", f"{relation_table}_id")
        for key in candidates:
            if key in row:
                return key
        return None

    def _build_order_clause(self) -> str:
        if not self.order_cols:
            return ""
        parts: List[str] = []
        for col, direction in self.order_cols:
            if not _IDENT_RE.fullmatch(col):
                raise ValueError(f"Invalid order column: {col}")
            parts.append(f"{col} {direction}")
        return f"ORDER BY {', '.join(parts)}"

    @staticmethod
    def _coerce_bind_value(value: Any, udt_name: Optional[str] = None) -> Any:
        """
        Coerce Python values to DB bind-safe values.

        asyncpg expects JSON/JSONB values as JSON strings unless a custom codec
        is registered. We serialize dict/list payloads centrally so repository
        callers can pass native Python structures safely.
        """
        if isinstance(value, dict):
            return json.dumps(value, default=str)
        if isinstance(value, list):
            # Preserve native lists for PostgreSQL array columns (udt_name starts with "_").
            if udt_name and udt_name.startswith("_"):
                return value
            return json.dumps(value, default=str)
        if isinstance(value, str) and udt_name in {"date", "timestamp", "timestamptz"}:
            text_value = value.strip()
            if not text_value:
                return value
            try:
                if udt_name == "date":
                    if "T" in text_value or " " in text_value:
                        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).date()
                    return date.fromisoformat(text_value)

                normalized = text_value.replace("Z", "+00:00")
                if "T" not in normalized and " " not in normalized:
                    parsed_dt = datetime.fromisoformat(f"{normalized}T00:00:00")
                else:
                    parsed_dt = datetime.fromisoformat(normalized)

                if udt_name == "timestamp":
                    if parsed_dt.tzinfo is not None:
                        parsed_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    return parsed_dt

                # timestamptz
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                return parsed_dt
            except ValueError:
                return value
        return value

    @staticmethod
    def _decode_column_value(value: Any, udt_name: Optional[str] = None) -> Any:
        """
        Decode DB-native values into PostgREST-style response payloads.

        asyncpg returns json/jsonb columns as raw strings unless codecs are
        configured. The rest of the app expects Supabase/PostgREST-like native
        dict/list values for JSON columns.
        """
        if value is None:
            return None
        if udt_name in {"json", "jsonb"} and isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def _decode_row(self, row: Dict[str, Any], column_types: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        if not column_types:
            return dict(row)
        return {
            key: self._decode_column_value(value, column_types.get(key))
            for key, value in dict(row).items()
        }

    async def _get_table_column_types(self, conn) -> Dict[str, str]:
        """
        Return mapping: column_name -> udt_name for the current table.
        Cached per table to avoid repeated information_schema round trips.
        """
        return await self._get_column_types_for_table(conn, self.table_name)

    async def _get_column_types_for_table(self, conn, table_name: str) -> Dict[str, str]:
        cache_key = table_name.lower()
        cached = _TABLE_COLUMN_TYPES_CACHE.get(cache_key)
        if cached is not None:
            return cached

        sql = (
            "SELECT column_name, udt_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1"
        )
        rows = await conn.fetch(sql, table_name)
        mapping = {row["column_name"]: row["udt_name"] for row in rows}
        _TABLE_COLUMN_TYPES_CACHE[cache_key] = mapping
        return mapping

    async def _execute_select_with_relations(
        self,
        conn,
        base_select: str,
        relations: List[_RelationSpec],
        base_filters: List[Tuple[str, str, Any]],
        relation_filters: Dict[str, List[Tuple[str, str, Any]]],
        base_column_types: Optional[Dict[str, str]] = None,
    ) -> PostgrestResponse:
        if not _IDENT_RE.fullmatch(self.table_name):
            return PostgrestResponse(error=f"Invalid table: {self.table_name}")

        where_sql, args = self._build_where_clause(
            start_index=1,
            filters=base_filters,
            column_types=base_column_types,
        )
        order_sql = self._build_order_clause()

        sql = f"SELECT {base_select} FROM {self.table_name} {where_sql} {order_sql}"
        base_rows = [self._decode_row(r, base_column_types) for r in await conn.fetch(sql, *args)]

        if not base_rows:
            empty_data: Union[Dict[str, Any], List[Dict[str, Any]], None]
            if self.single_val:
                empty_data = None
            else:
                empty_data = []
            return PostgrestResponse(data=empty_data, count=0)

        relation_names = {rel.table for rel in relations}
        for rel_name in relation_filters:
            if rel_name not in relation_names:
                relations.append(_RelationSpec(table=rel_name, columns=["id"], inner=True))

        rows = base_rows
        for rel in relations:
            if not _IDENT_RE.fullmatch(rel.table):
                return PostgrestResponse(error=f"Invalid relation table: {rel.table}")

            fk_col = self._detect_fk_column(rows[0], rel.table)
            if not fk_col:
                logger.warning(
                    "Could not infer FK for relation '%s' on table '%s'",
                    rel.table,
                    self.table_name,
                )
                for row in rows:
                    row[rel.table] = None
                if rel.inner:
                    rows = []
                continue

            rel_ids = list({row.get(fk_col) for row in rows if row.get(fk_col) is not None})
            rel_map: Dict[Any, Dict[str, Any]] = {}
            if rel_ids:
                rel_cols = list(rel.columns or ["*"])
                if "*" in rel_cols:
                    rel_select = "*"
                else:
                    for rel_col in rel_cols:
                        if not _IDENT_RE.fullmatch(rel_col):
                            return PostgrestResponse(error=f"Invalid relation column: {rel_col}")
                    if "id" not in rel_cols:
                        rel_cols.insert(0, "id")
                    rel_select = ", ".join(rel_cols)

                rel_filters = relation_filters.get(rel.table, [])
                rel_column_types = (
                    await self._get_column_types_for_table(conn, rel.table)
                    if rel_filters
                    else None
                )
                rel_where_sql, rel_where_args = self._build_where_clause(
                    start_index=2,
                    filters=rel_filters,
                    column_types=rel_column_types,
                )
                rel_extra = ""
                if rel_where_sql:
                    rel_extra = f" AND {rel_where_sql[len('WHERE '):]}"

                rel_sql = f"SELECT {rel_select} FROM {rel.table} WHERE id = ANY($1){rel_extra}"
                rel_rows = await conn.fetch(rel_sql, rel_ids, *rel_where_args)
                rel_map = {
                    decoded["id"]: decoded
                    for decoded in (
                        self._decode_row(r, rel_column_types)
                        for r in rel_rows
                    )
                }

            filtered_rows: List[Dict[str, Any]] = []
            relation_has_filters = bool(relation_filters.get(rel.table))
            for row in rows:
                rel_obj = rel_map.get(row.get(fk_col))
                row[rel.table] = rel_obj
                if (rel.inner or relation_has_filters) and rel_obj is None:
                    continue
                filtered_rows.append(row)
            rows = filtered_rows

            if not rows:
                break

        total_count = len(rows)
        start = self.offset_val or 0
        end = start + self.limit_val if self.limit_val is not None else None
        paged_rows = rows[start:end]

        if self.single_val:
            data: Union[Dict[str, Any], List[Dict[str, Any]], None] = paged_rows[0] if paged_rows else None
        else:
            data = paged_rows

        count_value = total_count if self.count_mode == "exact" else len(paged_rows)
        return PostgrestResponse(data=data, count=count_value)

    async def _execute_select(self, conn) -> PostgrestResponse:
        if not _IDENT_RE.fullmatch(self.table_name):
            return PostgrestResponse(error=f"Invalid table: {self.table_name}")

        base_cols, relations = self._parse_select_spec()
        base_filters, relation_filters = self._split_base_and_relation_filters()

        column_types = await self._get_table_column_types(conn)

        if relations:
            return await self._execute_select_with_relations(
                conn,
                base_select=base_cols,
                relations=relations,
                base_filters=base_filters,
                relation_filters=relation_filters,
                base_column_types=column_types,
            )

        where_sql, args = self._build_where_clause(
            start_index=1,
            filters=base_filters,
            column_types=column_types,
        )
        order_sql = self._build_order_clause()
        limit_sql = f"LIMIT {self.limit_val}" if self.limit_val is not None else ""
        offset_sql = f"OFFSET {self.offset_val}" if self.offset_val else ""

        count_value = None
        if self.count_mode == "exact":
            count_sql = f"SELECT COUNT(*) FROM {self.table_name} {where_sql}"
            count_value = await conn.fetchval(count_sql, *args)

        sql = f"SELECT {base_cols} FROM {self.table_name} {where_sql} {order_sql} {limit_sql} {offset_sql}"
        rows = await conn.fetch(sql, *args)
        data = [self._decode_row(r, column_types) for r in rows]

        if self.single_val:
            data = data[0] if data else None

        if count_value is None:
            count_value = len(rows)

        return PostgrestResponse(data=data, count=count_value)

    async def _execute_insert(self, conn) -> PostgrestResponse:
        if not self.inserts:
            return PostgrestResponse(data=[])

        items = [self.inserts] if isinstance(self.inserts, dict) else list(self.inserts)
        if not items:
            return PostgrestResponse(data=[])

        keys = list(items[0].keys())
        cols = ", ".join(keys)
        results = []
        column_types = await self._get_table_column_types(conn)

        for item in items:
            args = [self._coerce_bind_value(item.get(k), column_types.get(k)) for k in keys]
            placeholders = ", ".join(f"${i + 1}" for i in range(len(args)))
            sql = f"INSERT INTO {self.table_name} ({cols}) VALUES ({placeholders}) RETURNING *"
            row = await conn.fetchrow(sql, *args)
            if row:
                results.append(self._decode_row(row, column_types))

        data: Any = results
        if self.single_val:
            data = results[0] if results else None
        return PostgrestResponse(data=data)

    async def _execute_update(self, conn) -> PostgrestResponse:
        if not self.updates:
            return PostgrestResponse(error="No update payload")

        set_parts: List[str] = []
        args: List[Any] = []
        column_types = await self._get_table_column_types(conn)
        for key, value in self.updates.items():
            args.append(self._coerce_bind_value(value, column_types.get(key)))
            set_parts.append(f"{key} = ${len(args)}")

        where_sql, where_args = self._build_where_clause(
            start_index=len(args) + 1,
            column_types=column_types,
        )
        args.extend(where_args)

        sql = f"UPDATE {self.table_name} SET {', '.join(set_parts)} {where_sql} RETURNING *"
        rows = await conn.fetch(sql, *args)
        data = [self._decode_row(r, column_types) for r in rows]

        if self.single_val:
            data = data[0] if data else None

        return PostgrestResponse(data=data)

    async def _execute_upsert(self, conn) -> PostgrestResponse:
        if not self.upsert_data:
            return PostgrestResponse(data=[])

        items = [self.upsert_data] if isinstance(self.upsert_data, dict) else list(self.upsert_data)
        if not items:
            return PostgrestResponse(data=[])

        results = []
        column_types = await self._get_table_column_types(conn)
        for item in items:
            keys = list(item.keys())
            args = [self._coerce_bind_value(item.get(k), column_types.get(k)) for k in keys]
            cols = ", ".join(keys)
            placeholders = ", ".join(f"${i + 1}" for i in range(len(args)))

            if self.upsert_on_conflict:
                conflict_cols = [c.strip() for c in self.upsert_on_conflict.split(",") if c.strip()]
            elif "id" in keys:
                conflict_cols = ["id"]
            else:
                conflict_cols = [keys[0]]

            update_cols = [k for k in keys if k not in conflict_cols]
            if update_cols:
                update_sql = ", ".join(f"{k} = EXCLUDED.{k}" for k in update_cols)
                conflict_action = f"DO UPDATE SET {update_sql}"
            else:
                conflict_action = "DO NOTHING"

            conflict_target = ", ".join(conflict_cols)
            sql = (
                f"INSERT INTO {self.table_name} ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_target}) {conflict_action} RETURNING *"
            )

            row = await conn.fetchrow(sql, *args)
            if row:
                results.append(self._decode_row(row, column_types))

        data: Any = results
        if self.single_val:
            data = results[0] if results else None
        return PostgrestResponse(data=data)

    async def _execute_delete(self, conn) -> PostgrestResponse:
        column_types = await self._get_table_column_types(conn)
        where_sql, args = self._build_where_clause(start_index=1, column_types=column_types)
        sql = f"DELETE FROM {self.table_name} {where_sql} RETURNING *"
        rows = await conn.fetch(sql, *args)
        data = [self._decode_row(r, column_types) for r in rows]

        if self.single_val:
            data = data[0] if data else None

        return PostgrestResponse(data=data)


class RpcBuilder:
    """RPC call compatibility wrapper."""

    def __init__(self, pool, name: str, params: Optional[Dict] = None):
        self.pool = pool
        self.name = name
        self.params = params or {}

    def execute(self) -> _ExecutionResult:
        response = self._run_sync()
        return _ExecutionResult(response)

    def _run_sync(self) -> PostgrestResponse:
        def _runner() -> PostgrestResponse:
            return asyncio.run(self._execute_async())

        try:
            asyncio.get_running_loop()
            return _SYNC_EXECUTOR.submit(_runner).result()
        except RuntimeError:
            return _runner()

    async def _execute_async(self) -> PostgrestResponse:
        conn = None
        try:
            conn = await asyncpg.connect(_DATABASE_URL)
            
            # Set RLS context for the session
            from app.core.security.tenant_isolation import get_current_tenant_id, get_bypass_rls
            tenant_id = get_current_tenant_id()
            bypass_rls = get_bypass_rls()
            
            if bypass_rls:
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                await conn.execute("SET LOCAL app.current_tenant_id = ''")
            elif tenant_id:
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")
                await conn.execute("SET LOCAL app.bypass_rls = 'false'")
            else:
                await conn.execute("SET LOCAL app.current_tenant_id = ''")
                await conn.execute("SET LOCAL app.bypass_rls = 'false'")

            return await self._execute_with_conn(conn)
        except Exception as e:
            logger.error("PostgresAdapter RPC error (%s): %s", self.name, e, exc_info=True)
            return PostgrestResponse(error=str(e))
        finally:
            if conn:
                await conn.close()

    async def _execute_with_conn(self, conn) -> PostgrestResponse:
        if self.name == "update_call_status":
            return await self._rpc_update_call_status(conn)
        if self.name == "increment_campaign_counter":
            return await self._rpc_increment_campaign_counter(conn)
        if self.name == "increment_quota_usage":
            return await self._rpc_increment_quota_usage(conn)

        logger.warning("Unsupported RPC in Postgres adapter: %s", self.name)
        return PostgrestResponse(error=f"Unsupported RPC: {self.name}")

    async def _rpc_update_call_status(self, conn) -> PostgrestResponse:
        call_id = self.params.get("p_call_uuid")
        outcome = self.params.get("p_outcome")
        duration = self.params.get("p_duration")

        row = await conn.fetchrow(
            "SELECT id, lead_id, campaign_id FROM calls WHERE id = $1",
            call_id,
        )
        if not row:
            return PostgrestResponse(data={"found": False})

        if duration is None:
            await conn.execute(
                """
                UPDATE calls
                SET status = 'completed', outcome = $2, ended_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                call_id,
                outcome,
            )
        else:
            await conn.execute(
                """
                UPDATE calls
                SET status = 'completed', outcome = $2, duration_seconds = $3,
                    ended_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                call_id,
                outcome,
                int(duration),
            )

        return PostgrestResponse(
            data={
                "found": True,
                "call_id": str(row["id"]),
                "lead_id": str(row["lead_id"]) if row.get("lead_id") else None,
                "campaign_id": str(row["campaign_id"]) if row.get("campaign_id") else None,
            }
        )

    async def _rpc_increment_campaign_counter(self, conn) -> PostgrestResponse:
        campaign_id = self.params.get("p_campaign_id")
        counter = self.params.get("p_counter")

        if counter not in {"calls_completed", "calls_failed"}:
            return PostgrestResponse(error=f"Invalid campaign counter: {counter}")

        sql = (
            f"UPDATE campaigns "
            f"SET {counter} = COALESCE({counter}, 0) + 1, updated_at = NOW() "
            f"WHERE id = $1 RETURNING id, {counter}"
        )
        row = await conn.fetchrow(sql, campaign_id)
        if not row:
            return PostgrestResponse(data=None)
        return PostgrestResponse(data={"id": str(row["id"]), counter: row[counter]})

    async def _rpc_increment_quota_usage(self, conn) -> PostgrestResponse:
        tenant_id = self.params.get("p_tenant_id")
        usage_date = self.params.get("p_usage_date")
        field = self.params.get("p_field")

        if not isinstance(field, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", field):
            return PostgrestResponse(error="Invalid usage field")

        sql = (
            f"INSERT INTO tenant_quota_usage (tenant_id, usage_date, {field}) "
            f"VALUES ($1, $2, 1) "
            f"ON CONFLICT (tenant_id, usage_date) "
            f"DO UPDATE SET {field} = COALESCE(tenant_quota_usage.{field}, 0) + 1"
        )
        await conn.execute(sql, tenant_id, usage_date)

        value = await conn.fetchval(
            f"SELECT {field} FROM tenant_quota_usage WHERE tenant_id = $1 AND usage_date = $2",
            tenant_id,
            usage_date,
        )
        return PostgrestResponse(data=value)


@dataclass
class _AuthUser:
    id: str
    email: Optional[str] = None


@dataclass
class _AuthResponse:
    user: Optional[_AuthUser]


class _AuthAdapter:
    """JWT auth helper for legacy `client.auth.get_user(token)` usage."""

    def get_user(self, token: str) -> _AuthResponse:
        settings = get_settings()
        secret = settings.effective_jwt_secret
        algorithm = settings.jwt_algorithm
        if not secret:
            return _AuthResponse(user=None)
        try:
            payload = jwt.decode(token, secret, algorithms=[algorithm])
            user_id = payload.get("sub")
            if not user_id:
                return _AuthResponse(user=None)
            return _AuthResponse(user=_AuthUser(id=str(user_id), email=payload.get("email")))
        except Exception:
            return _AuthResponse(user=None)


class _LocalStorageBucket:
    def __init__(self, base_dir: Path, bucket_name: str):
        self.base_dir = base_dir
        self.bucket_name = bucket_name

    def _abs_path(self, rel_path: str) -> Path:
        safe_rel = rel_path.replace("\\", "/").lstrip("/")
        p = (self.base_dir / self.bucket_name / safe_rel).resolve()
        bucket_root = (self.base_dir / self.bucket_name).resolve()
        if not str(p).startswith(str(bucket_root)):
            raise ValueError("Invalid storage path")
        return p

    def upload(self, path: str, file: Union[bytes, bytearray], file_options: Optional[Dict] = None):
        target = self._abs_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = bytes(file)
        target.write_bytes(data)
        return {"path": path}

    def download(self, path: str) -> bytes:
        target = self._abs_path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        return target.read_bytes()

    def create_signed_url(self, path: str, expires_in: int = 3600) -> Dict[str, str]:
        # Local backend handles auth; return stable API-style path.
        rel = path.replace("\\", "/")
        return {"signedURL": f"/api/v1/recordings/storage/{self.bucket_name}/{rel}", "signedUrl": f"/api/v1/recordings/storage/{self.bucket_name}/{rel}"}


class _LocalStorageAdapter:
    def __init__(self, base_dir: Optional[str] = None):
        root = base_dir or os.getenv("RECORDINGS_STORAGE_DIR") or "audio_files/storage"
        self.base_dir = Path(root)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def from_(self, bucket_name: str) -> _LocalStorageBucket:
        return _LocalStorageBucket(self.base_dir, bucket_name)


class PostgresClient:
    """Compatibility client exposing table/rpc/auth/storage helper surfaces."""

    def __init__(self, pool):
        self.pool = pool
        self.auth = _AuthAdapter()
        self.storage = _LocalStorageAdapter()

    def table(self, name: str) -> QueryBuilder:
        return QueryBuilder(self.pool, name)

    def rpc(self, name: str, params: Optional[Dict] = None) -> RpcBuilder:
        return RpcBuilder(self.pool, name, params)


# Backward compatibility name used across the current codebase.
Client = PostgresClient


def create_client(url: str, key: str) -> Optional[PostgresClient]:
    """Compatibility stub. The app uses container-managed DB pools."""
    logger.warning("create_client(url, key) is deprecated; use DI container DB pool")
    return None
