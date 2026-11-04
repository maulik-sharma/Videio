import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client.js';

const ADMIN = ['admin'];

// Live numbers for the System panel. Polls while the page is open.
export function useSystem() {
  return useQuery({
    queryKey: [...ADMIN, 'system'],
    queryFn: () => api('/api/admin/system'),
    refetchInterval: 5000,
    retry: false,
  });
}

// filter: 'all' | 'removed' | 'live'
export function useAdminVideos(filter) {
  return useInfiniteQuery({
    queryKey: [...ADMIN, 'videos', filter],
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({ limit: '25' });
      if (filter !== 'all') query.set('removed', filter === 'removed' ? 'true' : 'false');
      if (pageParam) query.set('cursor', pageParam);
      return api(`/api/admin/videos?${query}`);
    },
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    retry: false,
  });
}

export function useAuditLog() {
  return useQuery({
    queryKey: [...ADMIN, 'audit'],
    queryFn: () => api('/api/admin/audit?limit=10'),
    retry: false,
  });
}

function useAdminAction(path) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }) =>
      api(path(encodeURIComponent(id)), { method: 'POST', body: { reason } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN });
      queryClient.invalidateQueries({ queryKey: ['feed'] });
      queryClient.invalidateQueries({ queryKey: ['video'] });
    },
  });
}

export const useTakedown = () => useAdminAction((id) => `/api/admin/videos/${id}/takedown`);
export const useRestore = () => useAdminAction((id) => `/api/admin/videos/${id}/restore`);
export const useDisableUser = () => useAdminAction((id) => `/api/admin/users/${id}/disable`);
