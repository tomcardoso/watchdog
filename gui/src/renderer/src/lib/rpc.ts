// Typed access to the Python backend. Views use `useRpc` for reads (cached and refetched by
// react-query) and `call` for one-off mutations; `useEvent` subscribes to backend events.

import { QueryClient, useQuery, UseQueryOptions } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import type { EventName, Events, MethodName, Params, Result } from '@shared/api'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: 1 },
    mutations: { retry: 0 }
  }
})

export interface RpcFailure extends Error {
  code: string
  data: unknown
}

export function call<M extends MethodName>(method: M, params?: Params<M>): Promise<Result<M>> {
  return window.watchdog.rpc(method, params)
}

export function useRpc<M extends MethodName>(
  method: M,
  params: Params<M> | null,
  opts?: Omit<UseQueryOptions<Result<M>, RpcFailure>, 'queryKey' | 'queryFn'>
) {
  return useQuery<Result<M>, RpcFailure>({
    queryKey: [method, params],
    queryFn: () => call(method, params ?? undefined),
    enabled: params !== null && (opts?.enabled ?? true),
    ...opts
  })
}

/** Refetch every cached read whose method starts with one of `prefixes` (after a mutation or a
 * finished job). With no prefixes, everything. */
export function invalidate(...prefixes: string[]): void {
  void queryClient.invalidateQueries({
    predicate: (q) => {
      const m = String(q.queryKey[0])
      return prefixes.length === 0 || prefixes.some((p) => m.startsWith(p))
    }
  })
}

export function useEvent<E extends EventName>(event: E, cb: (data: Events[E]) => void): void {
  const ref = useRef(cb)
  ref.current = cb
  useEffect(() => window.watchdog.on(event, (d) => ref.current(d)), [event])
}

export function errorMessage(err: unknown): string {
  if (!err) return ''
  if (err instanceof Error) return err.message.replace(/^Error invoking remote method '[^']+': /, '')
  return String(err)
}
