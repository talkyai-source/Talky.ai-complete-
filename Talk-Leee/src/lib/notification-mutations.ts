import { useMutation, type MutationFunctionContext, type UseMutationOptions } from "@tanstack/react-query";
import { notificationsStore } from "@/lib/notifications";

type NotificationOrigin = ReturnType<typeof notificationsStore.capture>;

// TanStack keeps this context object for an execution even when an observer
// replaces its options during a rerender. Keep the origin here, not in a render
// closure or in the caller's rollback context.
const origins = new WeakMap<MutationFunctionContext, NotificationOrigin>();

export function captureMutationNotificationOrigin(context: MutationFunctionContext) {
    let origin = origins.get(context);
    if (!origin) {
        origin = notificationsStore.capture();
        origins.set(context, origin);
    }
    return origin;
}

export function mutationNotificationOrigin(context: MutationFunctionContext) {
    return origins.get(context);
}

/** Fence the existing synchronous completion callbacks to their initiating user.
 * The existing onMutate return value (including optimistic rollback data) is
 * passed through unchanged. Async callbacks must use a captured notifier after
 * their own awaits; these consumers currently publish synchronously.
 */
export function notificationMutationOptions<TData, TError = Error, TVariables = void, TContext = unknown>(
    options: UseMutationOptions<TData, TError, TVariables, TContext>,
): UseMutationOptions<TData, TError, TVariables, TContext> {
    return {
        ...options,
        onMutate: (variables, context) => {
            captureMutationNotificationOrigin(context);
            return options.onMutate?.(variables, context) as TContext | Promise<TContext>;
        },
        onSuccess: (data, variables, result, context) => {
            if (origins.get(context)?.isCurrent()) return options.onSuccess?.(data, variables, result, context);
        },
        onError: (error, variables, result, context) => {
            if (origins.get(context)?.isCurrent()) return options.onError?.(error, variables, result, context);
        },
        onSettled: (data, error, variables, result, context) => {
            if (origins.get(context)?.isCurrent()) return options.onSettled?.(data, error, variables, result, context);
        },
    };
}

export function useNotificationMutation<TData, TError = Error, TVariables = void, TContext = unknown>(
    options: UseMutationOptions<TData, TError, TVariables, TContext>,
) {
    return useMutation(notificationMutationOptions(options));
}
