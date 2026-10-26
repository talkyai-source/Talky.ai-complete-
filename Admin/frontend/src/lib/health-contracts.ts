// Check the fields the monitoring views consume. A typed fetch alone cannot
// protect a deployed page from an incomplete or incompatible JSON response.
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const nullableNumber = (value: unknown) => value === null || number(value);
const string = (value: unknown) => typeof value === 'string';
const nullableString = (value: unknown) => value === null || string(value);
const fields = (value: Record<string, unknown>, names: string[], check: (item: unknown) => boolean) => names.every((name) => check(value[name]));

export function validStats(value: unknown): boolean {
    return record(value) && fields(value, ['active_calls', 'active_tenants', 'failed_calls_24h', 'total_calls_24h'], number)
        && nullableString(value.failed_call_rate_24h) && string(value.observed_at);
}

export function validSystemHealth(value: unknown): boolean {
    return record(value) && Array.isArray(value.providers) && value.providers.every((item: unknown) => record(item)
        && fields(item, ['name', 'status', 'latency_display', 'scope', 'detail'], string)
        && nullableNumber(item.latency_ms) && nullableString(item.last_check));
}

export function validWorkers(value: unknown): boolean {
    return record(value) && fields(value, ['total_workers', 'active_workers'], number) && nullableNumber(value.busy_workers)
        && fields(value, ['checked_at', 'scope'], string) && Array.isArray(value.workers)
        && value.workers.every((item: unknown) => record(item) && fields(item, ['id', 'name', 'status', 'detail'], string)
            && fields(item, ['processed_count', 'failed_count', 'success_rate', 'uptime_seconds', 'age_seconds'], nullableNumber)
            && nullableString(item.current_task) && nullableString(item.last_heartbeat));
}

export function validQueues(value: unknown): boolean {
    return record(value) && fields(value, ['total_pending', 'total_processing'], nullableNumber) && string(value.checked_at)
        && Array.isArray(value.queues) && value.queues.every((item: unknown) => record(item)
            && fields(item, ['name', 'status', 'scope', 'detail'], string)
            && fields(item, ['pending', 'processing', 'failed', 'success_rate_24h', 'avg_processing_time_ms'], nullableNumber));
}

export function validDetailedHealth(value: unknown): boolean {
    return record(value) && fields(value, ['uptime_seconds', 'memory_usage_mb', 'memory_total_mb', 'memory_percent', 'cpu_usage_percent'], number)
        && fields(value, ['uptime_display', 'os_info', 'python_version', 'version', 'checked_at', 'metrics_scope'], string);
}

export function validDatabaseHealth(value: unknown): boolean {
    return record(value) && typeof value.connected === 'boolean'
        && fields(value, ['latency_ms', 'pool_size', 'active_connections', 'available_connections', 'database_size_mb', 'table_count'], nullableNumber)
        && fields(value, ['last_check', 'scope'], string) && nullableString(value.error);
}
