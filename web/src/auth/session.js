import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client.js';

const ME = ['me'];

// The logged-in user, or null when logged out.
export function useMe() {
  return useQuery({
    queryKey: ME,
    queryFn: async () => {
      try {
        return await api('/api/me');
      } catch (error) {
        if (error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }) =>
      api('/api/auth/login', { method: 'POST', body: { email, password } }),
    onSuccess: (user) => queryClient.setQueryData(ME, user),
  });
}

// Registering does not log in, so create the account and then log straight in.
export function useRegister() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ email, password, displayName }) => {
      await api('/api/auth/register', {
        method: 'POST',
        body: { email, password, display_name: displayName },
      });
      return api('/api/auth/login', { method: 'POST', body: { email, password } });
    },
    onSuccess: (user) => queryClient.setQueryData(ME, user),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api('/api/auth/logout', { method: 'POST' }),
    onSettled: () => queryClient.setQueryData(ME, null),
  });
}
